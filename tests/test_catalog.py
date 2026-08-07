from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "skills" / "choose-proven-cloud-stack" / "scripts"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SCRIPT_DIR))

import catalog  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


inspect_repository = load_module("inspect_repository", SCRIPT_DIR / "inspect_repository.py")


class CatalogTests(unittest.TestCase):
    def test_catalog_has_useful_seed_coverage(self):
        result = catalog.stats()
        self.assertGreaterEqual(result["projects"], 95)
        self.assertGreaterEqual(result["patterns"], 30)
        self.assertGreaterEqual(result["tiers"].get("B", 0), 50)
        projects, metrics, _ = catalog.load_catalog()
        tier_b = [row for row in projects if row.get("curation", {}).get("tier") == "B"]
        self.assertTrue(tier_b)
        for project in tier_b:
            metric = metrics[project["repo_id"]]
            self.assertEqual(metric.get("status"), "ok")
            self.assertIsNotNone(catalog.parse_iso(metric.get("fetched_at")))

    def test_forward_test_coverage_gaps_are_discoverable(self):
        minio = catalog.search("minio java presigned", 10, 0, [])
        messaging = catalog.search("strimzi schema registry rocketmq", 20, 0, [])
        observability = catalog.search("tempo mimir thanos", 20, 0, [])
        self.assertIn("github:minio/minio-java", {row["repo_id"] for row in minio["results"]})
        self.assertTrue(
            {"github:strimzi/strimzi-kafka-operator", "github:apache/rocketmq"}
            <= {row["repo_id"] for row in messaging["results"]}
        )
        self.assertTrue(
            {"github:grafana/tempo", "github:grafana/mimir", "github:thanos-io/thanos"}
            <= {row["repo_id"] for row in observability["results"]}
        )

    def test_chinese_term_aliases_are_valid_utf8_and_normalize(self):
        aliases, _ = catalog.load_term_map()
        self.assertEqual(aliases["对象存储"], "object-storage")
        self.assertEqual(aliases["断点下载"], "resume-download")

    def test_minio_download_recommendation_is_relevant_and_excludes_upload_only(self):
        requirements = json.loads((FIXTURE_DIR / "minio-browser-download.json").read_text(encoding="utf-8"))
        result = catalog.recommend(requirements, limit=20)
        ids = [row["repo_id"] for row in result["results"]]
        self.assertIn("github:aria2/aria2", ids)
        self.assertIn("github:rclone/rclone", ids)
        self.assertNotIn("github:tus/tusd", ids)
        self.assertGreaterEqual(result["hard_rejections"].get("operation-conflict", 0), 1)
        direct_ids = [row["repo_id"] for row in result["role_shortlists"]["direct"]]
        mechanism_ids = [row["repo_id"] for row in result["role_shortlists"]["mechanism"]]
        self.assertIn("github:aws/aws-sdk-js-v3", direct_ids)
        self.assertIn("github:aria2/aria2", mechanism_ids)
        rows = {row["repo_id"]: row for row in result["results"]}
        self.assertTrue(rows["github:aria2/aria2"]["default_eligible"])
        direct_rows = {row["repo_id"]: row for row in result["role_shortlists"]["direct"]}
        self.assertFalse(direct_rows["github:aws/aws-sdk-js-v3"]["default_eligible"])

    def test_minio_java_is_ranked_for_its_backend_control_plane_role(self):
        requirements = {
            "objective": "Java MinIO presigned download control plane",
            "domains": ["object-storage", "file-transfer"],
            "operations": ["download"],
            "topologies": ["service-to-object-storage"],
            "problems": ["s3-client-integration", "presigned-access"],
            "required_mechanisms": ["s3-api", "presigned-url"],
            "runtimes": ["jvm-backend"],
            "languages": ["java"],
            "roles": ["official-sdk", "direct-dependency"],
        }
        result = catalog.recommend(requirements, limit=10)
        rows = {row["repo_id"]: row for row in result["results"]}
        self.assertIn("github:minio/minio-java", rows)
        self.assertTrue(rows["github:minio/minio-java"]["relevance_eligible"])
        self.assertFalse(rows["github:minio/minio-java"]["default_eligible"])
        self.assertIn(
            "github:minio/minio-java",
            result["selection_policy"]["discovery_shortlist_ids"],
        )
        self.assertLess(rows["github:minio/minio-java"]["maturity"], 40.0)

    def test_requested_role_requires_an_exact_role_match(self):
        requirements = {
            "objective": "Java object storage server implementation",
            "domains": ["object-storage"],
            "operations": ["download"],
            "required_mechanisms": ["s3-api"],
            "roles": ["official-implementation"],
        }
        result = catalog.recommend(requirements, limit=20)
        self.assertNotIn(
            "github:minio/minio-java",
            {row["repo_id"] for row in result["results"]},
        )

    def test_ranking_is_deterministic(self):
        requirements = json.loads((FIXTURE_DIR / "minio-browser-download.json").read_text(encoding="utf-8"))
        first = catalog.recommend(requirements, limit=12)
        second = catalog.recommend(requirements, limit=12)
        self.assertEqual(first, second)

    def test_cli_accepts_requirements_from_stdin(self):
        requirements = (FIXTURE_DIR / "minio-browser-download.json").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as unrelated_cwd:
            completed = subprocess.run(
                [sys.executable, "-B", str(SCRIPT_DIR / "catalog.py"), "recommend", "--requirements", "-", "--limit", "3"],
                input=requirements,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                cwd=unrelated_cwd,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual(len(output["results"]), 3)
        self.assertEqual(output["requirements"]["objective"], json.loads(requirements)["objective"])

    def test_observability_query_covers_each_signal_role(self):
        requirements = json.loads((FIXTURE_DIR / "kubernetes-observability.json").read_text(encoding="utf-8"))
        result = catalog.recommend(requirements, limit=20)
        ids = {row["repo_id"] for row in result["results"]}
        self.assertIn("github:prometheus/prometheus", ids)
        self.assertIn("github:open-telemetry/opentelemetry-collector", ids)
        self.assertIn("github:fluent/fluent-bit", ids)

    def test_messaging_query_keeps_stream_and_queue_families(self):
        requirements = json.loads((FIXTURE_DIR / "messaging-replay-and-queue.json").read_text(encoding="utf-8"))
        result = catalog.recommend(requirements, limit=20)
        ids = {row["repo_id"] for row in result["results"]}
        self.assertIn("github:apache/kafka", ids)
        self.assertIn("github:rabbitmq/rabbitmq-server", ids)
        self.assertIn("github:nats-io/nats-server", ids)

    def test_split_messaging_fingerprints_each_find_a_default_candidate(self):
        cases = [
            (
                {
                    "objective": "durable order event replay",
                    "domains": ["messaging", "event-streaming"],
                    "operations": ["publish", "consume", "replay"],
                    "problems": ["event-streaming", "durable-replay"],
                    "required_mechanisms": ["partitioned-log", "consumer-offset", "replication"],
                    "topologies": ["broker-cluster"],
                    "runtimes": ["jvm", "kubernetes"],
                    "roles": ["official-implementation", "production-validation"],
                },
                "github:apache/kafka",
            ),
            (
                {
                    "objective": "asynchronous task work queue",
                    "domains": ["messaging"],
                    "operations": ["publish", "consume"],
                    "problems": ["message-delivery", "work-queue"],
                    "required_mechanisms": ["message-broker", "acknowledgement", "redelivery", "dead-letter-queue"],
                    "topologies": ["broker-cluster"],
                    "runtimes": ["kubernetes"],
                    "roles": ["official-implementation", "production-validation"],
                },
                "github:rabbitmq/rabbitmq-server",
            ),
            (
                {
                    "objective": "database transactional outbox CDC",
                    "domains": ["data-integration", "event-streaming", "database"],
                    "operations": ["capture", "stream", "replicate"],
                    "problems": ["database-change-stream", "event-driven-integration"],
                    "required_mechanisms": ["change-data-capture", "source-offset", "schema-history", "outbox-pattern"],
                    "topologies": ["database-log-to-event-stream"],
                    "runtimes": ["jvm", "kubernetes"],
                    "languages": ["java"],
                    "roles": ["official-implementation", "production-validation"],
                },
                "github:debezium/debezium",
            ),
        ]
        for requirements, expected in cases:
            with self.subTest(expected=expected):
                result = catalog.recommend(requirements, limit=10)
                self.assertIn(expected, result["selection_policy"]["default_shortlist_ids"])

    def test_stars_do_not_override_operation_conflict(self):
        project = {
            "operations": ["upload"],
            "pattern_links": [{"pattern_id": "resumable-upload-protocol", "roles": ["production-validation"]}],
            "limitations": ["upload-only"],
        }
        req = {
            "domains": set(),
            "operations": {"download"},
            "problems": set(),
            "mechanisms": set(),
            "optional_mechanisms": set(),
            "topologies": set(),
            "runtimes": set(),
            "languages": set(),
            "roles": set(),
            "exclude": {"upload-only"},
        }
        compatible, reasons = catalog.compatibility(project, {"stars": 10_000_000}, req, {})
        self.assertFalse(compatible)
        self.assertIn("operation-conflict", reasons)

    def test_explicit_exclusion_applies_across_controlled_dimensions(self):
        project = {
            "operations": ["download"],
            "topologies": ["server-proxy"],
            "protocols": ["s3-api"],
            "pattern_links": [{"pattern_id": "p1", "roles": ["integration-adapter"]}],
        }
        req = {
            "domains": set(), "operations": {"download"}, "problems": set(),
            "mechanisms": set(), "optional_mechanisms": set(), "topologies": set(),
            "runtimes": set(), "languages": set(), "roles": set(),
            "exclude": {"server-proxy"},
        }
        compatible, reasons = catalog.compatibility(project, {}, req, {})
        self.assertFalse(compatible)
        self.assertIn("explicit-exclusion", reasons)

    def test_repository_inspector_rejects_non_github_and_shell_like_urls(self):
        with self.assertRaises(ValueError):
            inspect_repository.inspect("https://example.com/repo.git", ["retry"], 10, 10, 5)
        with self.assertRaises(ValueError):
            inspect_repository.inspect("https://github.com/owner/repo;whoami", ["retry"], 10, 10, 5)

    def test_direct_dependency_gate_applies_to_multi_role_projects(self):
        project = {
            "operations": ["download"],
            "runtimes": ["nodejs"],
            "languages": ["typescript"],
            "pattern_links": [
                {"pattern_id": "p1", "roles": ["direct-dependency"]},
                {"pattern_id": "p2", "roles": ["mechanism-reference"]},
            ],
        }
        req = {
            "domains": set(),
            "operations": {"download"},
            "problems": set(),
            "mechanisms": set(),
            "optional_mechanisms": set(),
            "topologies": set(),
            "runtimes": {"jvm"},
            "languages": {"java"},
            "roles": {"direct-dependency"},
            "exclude": set(),
        }
        compatible, reasons = catalog.compatibility(project, {}, req, {})
        self.assertTrue(compatible, reasons)
        direct_compatible, direct_reasons = catalog.role_compatibility(project, {}, req, "direct")
        mechanism_compatible, mechanism_reasons = catalog.role_compatibility(project, {}, req, "mechanism")
        self.assertFalse(direct_compatible)
        self.assertIn("runtime-conflict-for-direct-use", direct_reasons)
        self.assertIn("language-conflict-for-direct-use", direct_reasons)
        self.assertTrue(mechanism_compatible, mechanism_reasons)

    def test_direct_dependency_gate_rejects_topology_conflict(self):
        project = {
            "operations": ["download"],
            "topologies": ["service-to-object-storage"],
            "pattern_links": [{"pattern_id": "p1", "roles": ["direct-dependency"]}],
        }
        req = {
            "domains": set(), "operations": {"download"}, "problems": set(),
            "mechanisms": set(), "optional_mechanisms": set(),
            "topologies": {"browser-to-object-storage"}, "runtimes": set(),
            "languages": set(), "roles": {"direct-dependency"}, "exclude": set(),
        }
        compatible, reasons = catalog.role_compatibility(project, {}, req, "direct")
        self.assertFalse(compatible)
        self.assertIn("topology-conflict-for-direct-use", reasons)

    def test_official_server_implementation_is_not_language_gated_like_an_sdk(self):
        project = {
            "operations": ["serve"],
            "runtimes": ["server"],
            "languages": ["go"],
            "pattern_links": [{"pattern_id": "p1", "roles": ["official-implementation"]}],
        }
        req = {
            "domains": set(), "operations": {"serve"}, "problems": set(),
            "mechanisms": set(), "optional_mechanisms": set(), "topologies": set(),
            "runtimes": {"jvm"}, "languages": {"java"},
            "roles": {"official-implementation"}, "exclude": set(),
        }
        compatible, reasons = catalog.role_compatibility(
            project,
            {},
            req,
            "direct",
            {"official-implementation"},
        )
        self.assertTrue(compatible, reasons)

    def test_required_mechanism_cap_applies_to_role_scores(self):
        project = {
            "mechanisms": ["http-range"],
            "pattern_links": [{"pattern_id": "p1", "roles": ["direct-dependency"]}],
        }
        req = {
            "domains": set(), "operations": set(), "problems": set(),
            "mechanisms": {"http-range", "checkpoint", "chunk-scheduler"},
            "optional_mechanisms": set(), "topologies": set(), "runtimes": set(),
            "languages": set(), "roles": {"direct-dependency"}, "exclude": set(),
        }
        score, _ = catalog.relevance_score(project, req, set(), "direct")
        self.assertLessEqual(score, 59.0)

    def test_requirement_types_are_validated(self):
        with self.assertRaisesRegex(ValueError, "roles"):
            catalog.recommend({"roles": "direct-dependency"}, limit=3)
        with self.assertRaisesRegex(ValueError, "constraints"):
            catalog.recommend({"constraints": None}, limit=3)

    def test_unscored_context_is_reported_instead_of_silently_ignored(self):
        result = catalog.recommend(
            {
                "domains": ["object-storage"],
                "scale": {"concurrent_users": 500},
                "weights": {"performance": 0.5},
                "constraints": {
                    "licenses_forbidden": [],
                    "licenses_preferred": ["Apache-2.0"],
                    "self_hosted": True,
                },
            },
            limit=3,
        )
        self.assertEqual(
            result["unscored_requirement_fields"],
            ["constraints.licenses_preferred", "constraints.self_hosted", "scale", "weights"],
        )

    def test_tier_a_without_a_pinned_review_is_not_high_confidence(self):
        project = {
            "repo_id": "github:owner/repo",
            "primary_domain": "storage",
            "cohort_id": "storage",
            "curation": {"tier": "A"},
            "pattern_links": [{"pattern_id": "p1", "roles": ["reference-implementation"]}],
        }
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        metric = {"stars": 100, "forks": 10, "pushed_at": fetched_at, "fetched_at": fetched_at, "status": "ok"}
        _, confidence, _, breakdown = catalog.maturity_score(
            project,
            metric,
            [project],
            {project["repo_id"]: metric},
            {"p1": {"validated_by": [project["repo_id"]]}},
            {"reference-implementation"},
            reviews=[],
        )
        self.assertEqual(confidence, "medium")
        self.assertEqual(breakdown["pinned_reviews"], 0.0)


if __name__ == "__main__":
    unittest.main()
