from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "build_discovery_catalog.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load_module("build_discovery_catalog", SCRIPT_PATH)


def repository(
    full_name: str,
    node_id: str,
    *,
    stars: int = 100,
    size: int = 200,
    language: str | None = "Go",
    topics: list[str] | None = None,
    **overrides,
):
    owner, name = full_name.split("/", 1)
    row = {
        "id": abs(hash(node_id)),
        "node_id": node_id,
        "full_name": full_name,
        "name": name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{full_name}",
        "description": "Production implementation",
        "fork": False,
        "mirror_url": None,
        "is_template": False,
        "archived": False,
        "disabled": False,
        "private": False,
        "language": language,
        "size": size,
        "stargazers_count": stars,
        "forks_count": 5,
        "open_issues_count": 2,
        "topics": topics or ["cloud"],
        "default_branch": "main",
        "license": {"spdx_id": "Apache-2.0"},
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def cache_row(query_id: str, pattern_id: str, topic: str, items: list[dict]):
    return {
        "schema_version": "1.0",
        "query_id": query_id,
        "query": f"topic:{topic} stars:>=20 fork:false",
        "profile_id": f"profile-{pattern_id}",
        "pattern_id": pattern_id,
        "topic": topic,
        "min_stars": 20,
        "per_page": 75,
        "fetched_at": "2026-08-01T00:00:00Z",
        "complete": True,
        "incomplete_results": False,
        "total_count": len(items),
        "items": items,
    }


class FilteringTests(unittest.TestCase):
    def test_rejects_unsafe_and_low_signal_repositories(self):
        cases = {
            "fork": repository("o/fork", "N1", fork=True),
            "mirror": repository("o/mirror", "N2", mirror_url="https://example.test/upstream"),
            "template": repository("o/template", "N3", is_template=True),
            "archived": repository("o/archived", "N4", archived=True),
            "disabled": repository("o/disabled", "N5", disabled=True),
            "missing-language": repository("o/no-language", "N6", language=None),
            "too-small": repository("o/tiny", "N7", size=2),
            "below-min-stars": repository("o/unknown", "N8", stars=19),
            "list-or-tutorial": repository("o/awesome-cloud", "N9"),
        }
        for reason, row in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(builder.exclusion_reason(row, 20, 32), reason)
        self.assertIsNone(builder.exclusion_reason(repository("o/service", "N10"), 20, 32))


class MergeTests(unittest.TestCase):
    def test_merges_multi_query_provenance(self):
        hit = repository("acme/service", "NODE-1")
        merged = builder.merge_search_results(
            [
                cache_row("q1", "pattern-a", "cloud", [hit]),
                cache_row("q2", "pattern-b", "database", [dict(hit)]),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["_pattern_ids"], ["pattern-a", "pattern-b"])
        self.assertEqual(
            {item["query_id"] for item in merged[0]["_provenance"]}, {"q1", "q2"}
        )

    def test_same_slug_with_different_node_is_rejected(self):
        with self.assertRaises(builder.IdentityConflictError):
            builder.merge_search_results(
                [
                    cache_row("q1", "pattern-a", "cloud", [repository("acme/service", "N1")]),
                    cache_row("q2", "pattern-b", "db", [repository("ACME/SERVICE", "N2")]),
                ]
            )


class SelectionTests(unittest.TestCase):
    @staticmethod
    def candidate(slug: str, node: str, stars: int, patterns: list[str]):
        row = repository(slug, node, stars=stars)
        row["_pattern_ids"] = patterns
        row["_provenance"] = []
        return row

    def test_round_robin_balance_and_owner_cap(self):
        candidates = [
            self.candidate("mega/a1", "A1", 500, ["pattern-a"]),
            self.candidate("mega/a2", "A2", 490, ["pattern-a"]),
            self.candidate("independent/a3", "A3", 300, ["pattern-a"]),
            self.candidate("owner-b/b1", "B1", 400, ["pattern-b"]),
            self.candidate("owner-b2/b2", "B2", 200, ["pattern-b"]),
        ]
        selected = builder.balanced_select(
            candidates, ["pattern-a", "pattern-b"], limit=4, owner_cap=1
        )
        self.assertEqual(
            [row["_assigned_pattern_id"] for row in selected],
            ["pattern-a", "pattern-b", "pattern-a", "pattern-b"],
        )
        owners = [row["full_name"].split("/", 1)[0].casefold() for row in selected]
        self.assertEqual(len(owners), len(set(owners)))
        self.assertNotIn("mega/a2", [row["full_name"] for row in selected])


class QueryPlanTests(unittest.TestCase):
    def test_default_catalog_target_is_exactly_1000(self):
        self.assertEqual(builder.DEFAULT_TARGET_TOTAL, 1_000)

    def test_query_uses_documented_public_repository_qualifiers(self):
        defaults = {"min_stars": 20, "per_page": 75}
        profiles = [
            {
                "profile_id": "profile-a",
                "pattern_id": "pattern-a",
                "topics": ["cloud-native"],
            }
        ]
        query = builder.make_query_plan(defaults, profiles)[0]
        self.assertEqual(
            query["query"],
            "topic:cloud-native stars:>=20 is:public archived:false mirror:false template:false",
        )
        self.assertNotIn("fork:false", query["query"])
        self.assertEqual(query["per_page"], 75)

    def test_per_page_is_part_of_cache_identity(self):
        profile = {
            "profile_id": "profile-a",
            "pattern_id": "pattern-a",
            "topics": ["cloud-native"],
        }
        first = builder.make_query_plan({"min_stars": 20, "per_page": 50}, [profile])[0]
        second = builder.make_query_plan({"min_stars": 20, "per_page": 75}, [profile])[0]
        self.assertNotEqual(first["query_id"], second["query_id"])


class IdentityTests(unittest.TestCase):
    def test_existing_node_id_conflict_is_rejected(self):
        existing = [
            {
                "repo_id": "github:old/service",
                "github_node_id": "NODE-1",
                "canonical_slug": "old/service",
                "status": "ok",
            }
        ]
        with self.assertRaises(builder.IdentityConflictError):
            builder.protect_identities([repository("new/service", "NODE-1")], existing)

    def test_existing_slug_changed_node_is_rejected(self):
        existing = [
            {
                "repo_id": "github:acme/service",
                "github_node_id": "NODE-OLD",
                "canonical_slug": "acme/service",
                "status": "ok",
            }
        ]
        with self.assertRaises(builder.IdentityConflictError):
            builder.protect_identities([repository("acme/service", "NODE-NEW")], existing)

    def test_verified_metric_is_preserved_instead_of_downgraded_to_search_snapshot(self):
        candidate = repository("acme/service", "NODE-1", stars=999)
        candidate["_provenance"] = [
            {"fetched_at": "2026-08-01T00:00:00Z"}
        ]
        verified = {
            "repo_id": "github:acme/service",
            "github_node_id": "NODE-1",
            "canonical_slug": "acme/service",
            "stars": 123,
            "forks": 5,
            "archived": False,
            "disabled": False,
            "is_fork": False,
            "fetched_at": "2026-08-02T00:00:00Z",
            "source": "github-rest-api",
            "status": "ok",
        }
        metrics = {verified["repo_id"]: dict(verified)}
        keys = {verified["repo_id"].casefold(): verified["repo_id"]}
        updated = builder._upsert_search_metric(
            metrics, keys, candidate, "github:acme/service"
        )
        self.assertFalse(updated)
        self.assertEqual(metrics["github:acme/service"], verified)


class DeterministicBuildTests(unittest.TestCase):
    def write_json(self, path: Path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_jsonl(self, path: Path, rows):
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_complete_cache_build_is_byte_deterministic_and_preserves_static_data(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            references = root / "references"
            cache = root / "cache"
            references.mkdir()
            cache.mkdir()
            patterns = [
                {
                    "schema_version": "1.0",
                    "pattern_id": "pattern-a",
                    "name": "Pattern A",
                    "domains": ["domain-a"],
                    "operations": ["operate"],
                    "solves": ["problem-a"],
                    "topologies": ["service"],
                    "required_mechanisms": ["mechanism-a"],
                    "validated_by": [],
                    "status": "established",
                },
                {
                    "schema_version": "1.0",
                    "pattern_id": "pattern-b",
                    "name": "Pattern B",
                    "domains": ["domain-b"],
                    "operations": ["operate"],
                    "solves": ["problem-b"],
                    "topologies": ["service"],
                    "required_mechanisms": ["mechanism-b"],
                    "validated_by": [],
                    "status": "established",
                },
            ]
            profiles = {
                "schema_version": "1.0",
                "defaults": {"min_stars": 20, "per_page": 75, "roles": ["mechanism-reference"]},
                "profiles": [
                    {
                        "profile_id": "profile-pattern-a",
                        "pattern_id": "pattern-a",
                        "topics": ["cloud"],
                        "cohort_id": "cohort-a",
                        "runtimes": ["server"],
                        "protocols": ["http"],
                    },
                    {
                        "profile_id": "profile-pattern-b",
                        "pattern_id": "pattern-b",
                        "topics": ["database"],
                        "cohort_id": "cohort-b",
                        "runtimes": ["server"],
                        "protocols": ["http"],
                    },
                ],
            }
            static_project = {
                "schema_version": "1.0",
                "repo_id": "github:static/project",
                "url": "https://github.com/static/project",
                "name": "Static",
                "primary_domain": "domain-a",
                "domains": ["domain-a"],
                "cohort_id": "static",
                "operations": ["operate"],
                "problems": ["problem-a"],
                "mechanisms": ["mechanism-a"],
                "topologies": ["service"],
                "runtimes": ["server"],
                "languages": ["go"],
                "protocols": ["http"],
                "limitations": [],
                "pattern_links": [{"pattern_id": "pattern-a", "roles": ["mechanism-reference"]}],
                "summary": "Static project.",
                "curation": {"tier": "B", "catalogued_at": "2026-01-01"},
            }
            static_metric = {
                "repo_id": "github:static/project",
                "stars": None,
                "forks": None,
                "archived": None,
                "fetched_at": None,
                "status": "pending-refresh",
                "source": "github-rest-api",
                "custom_preserved": True,
            }
            self.write_json(references / "discovery-profiles.json", profiles)
            self.write_jsonl(references / "patterns-core.jsonl", patterns)
            self.write_jsonl(references / "projects-curated.jsonl", [static_project])
            self.write_jsonl(references / "projects-discovery.jsonl", [])
            self.write_jsonl(references / "github-metrics.jsonl", [static_metric])

            defaults, normalized_profiles, _, _ = builder.load_configuration(
                references / "discovery-profiles.json", references / "patterns-core.jsonl"
            )
            plan = builder.make_query_plan(defaults, normalized_profiles)
            hits = {
                "pattern-a": [
                    repository("static/project", "STATIC", stars=150),
                    repository("owner-a/service", "A", stars=90),
                ],
                "pattern-b": [repository("owner-b/service", "B", stars=80)],
            }
            for query in plan:
                row = cache_row(
                    query["query_id"], query["pattern_id"], query["topic"], hits[query["pattern_id"]]
                )
                row.update(
                    {
                        "query": query["query"],
                        "profile_id": query["profile_id"],
                    }
                )
                builder.atomic_write_json(builder.cache_path(cache, query), row)

            result = builder.build_from_cache(
                references, cache, target_total=3, snapshot_date="2026-08-03", owner_cap=2
            )
            self.assertEqual(result["expanded"], 2)
            output_names = [
                "projects-expanded.jsonl",
                "github-metrics.jsonl",
                "discovery-manifest.json",
                "catalog-metadata.json",
            ]
            first = {name: (references / name).read_bytes() for name in output_names}
            builder.build_from_cache(
                references, cache, target_total=3, snapshot_date="2026-08-03", owner_cap=2
            )
            second = {name: (references / name).read_bytes() for name in output_names}
            self.assertEqual(first, second)

            self.assertEqual(
                (references / "projects-curated.jsonl").read_text(encoding="utf-8"),
                json.dumps(static_project, ensure_ascii=False) + "\n",
            )
            metrics = {row["repo_id"]: row for row in builder.read_jsonl(references / "github-metrics.jsonl")}
            self.assertTrue(metrics["github:static/project"]["custom_preserved"])
            self.assertEqual(metrics["github:static/project"]["status"], "ok")
            self.assertEqual(metrics["github:static/project"]["github_node_id"], "STATIC")
            self.assertEqual(metrics["github:static/project"]["source"], "github-rest-api")
            self.assertEqual(
                metrics["github:static/project"]["source_endpoint"], "search/repositories"
            )
            self.assertEqual(len(metrics), 3)
            metadata = builder.read_json(references / "catalog-metadata.json")
            self.assertEqual(
                metadata["projects"],
                {"total": 3, "tier_a": 0, "tier_b": 1, "tier_c": 2},
            )
            self.assertEqual(metadata["catalog_version"], "1.0.0")
            expanded = builder.read_jsonl(references / "projects-expanded.jsonl")
            for project in expanded:
                self.assertEqual(project["curation"]["tier"], "C")
                for field in (
                    "domains",
                    "operations",
                    "problems",
                    "mechanisms",
                    "topologies",
                    "runtimes",
                    "languages",
                    "protocols",
                    "pattern_links",
                ):
                    self.assertTrue(project[field], field)
            for metric in metrics.values():
                builder.validate_metric_record(metric)
            manifest = builder.read_json(references / "discovery-manifest.json")
            self.assertEqual(
                manifest["selection"]["static_metrics_completed_from_search"], 1
            )

    def test_incomplete_cache_refuses_build_before_outputs_exist(self):
        plan = [
            {
                "ordinal": 1,
                "query_id": "q1",
                "query": "topic:cloud stars:>=20",
                "profile_id": "p1",
                "pattern_id": "pattern-a",
                "topic": "cloud",
            }
        ]
        with tempfile.TemporaryDirectory() as temp_name:
            with self.assertRaises(builder.IncompleteCacheError):
                builder.load_complete_cache(plan, Path(temp_name))


if __name__ == "__main__":
    unittest.main()
