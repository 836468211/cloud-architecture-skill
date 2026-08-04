from __future__ import annotations

import copy
import gc
import json
import sys
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "choose-proven-cloud-stack"
SCRIPT_DIR = SKILL_DIR / "scripts"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
RELEASE_PROJECTS = 1_000
# Capacity headroom only. This synthetic scale is not a promise about v1 catalog size.
SCALE_BUDGET_PROJECTS = 2_000
MAX_RECOMMEND_SECONDS = 5.0
MAX_SEARCH_SECONDS = 1.0
MAX_SKILL_BYTES = 10 * 1024 * 1024
sys.path.insert(0, str(SCRIPT_DIR))

import catalog  # noqa: E402


class CatalogReleaseGateTests(unittest.TestCase):
    def test_v1_catalog_count_and_metadata_match_the_payload(self):
        projects, _, patterns = catalog.load_catalog()
        metadata = catalog.load_catalog_metadata()
        tiers = Counter(
            str(row.get("curation", {}).get("tier", "C")).upper() for row in projects
        )
        computed_counts = {
            "total": len(projects),
            "tier_a": tiers.get("A", 0),
            "tier_b": tiers.get("B", 0),
            "tier_c": tiers.get("C", 0),
        }

        self.assertEqual(
            len(projects),
            RELEASE_PROJECTS,
            "v1.0.0 must not be released until the installable catalog contains exactly 1,000 repositories",
        )
        self.assertEqual(metadata.get("schema_version"), "1.0")
        self.assertEqual(metadata.get("catalog_version"), "1.0.0")
        self.assertIsNotNone(catalog.parse_iso(metadata.get("generated_at")))
        self.assertEqual(metadata.get("projects"), computed_counts)
        self.assertEqual(metadata.get("patterns"), len(patterns))

    def test_metadata_metric_summary_matches_the_snapshot(self):
        projects, metrics, _ = catalog.load_catalog()
        metadata = catalog.load_catalog_metadata()
        generated_at = catalog.parse_iso(metadata.get("generated_at"))
        self.assertIsNotNone(generated_at)
        assert generated_at is not None

        summary = metadata.get("github_metrics", {})
        freshness_days = summary.get("freshness_days")
        self.assertIsInstance(freshness_days, int)
        self.assertGreaterEqual(freshness_days, 0)

        fresh = 0
        for project in projects:
            metric = metrics.get(str(project.get("repo_id")), {})
            fetched_at = catalog.parse_iso(metric.get("fetched_at"))
            if metric.get("status") != "ok" or fetched_at is None:
                continue
            age_days = (generated_at.date() - fetched_at.date()).days
            if 0 <= age_days <= freshness_days:
                fresh += 1

        self.assertEqual(
            summary,
            {
                "fresh": fresh,
                "pending_or_missing": len(projects) - fresh,
                "freshness_days": freshness_days,
            },
        )

    def test_tier_c_is_never_marked_as_a_default_recommendation(self):
        for fixture_name in (
            "minio-browser-download.json",
            "messaging-replay-and-queue.json",
            "kubernetes-observability.json",
        ):
            requirements = json.loads(
                (FIXTURE_DIR / fixture_name).read_text(encoding="utf-8")
            )
            result = catalog.recommend(requirements, limit=50)
            rows = {row["repo_id"]: row for row in result["results"]}
            with self.subTest(fixture=fixture_name, surface="results"):
                for row in rows.values():
                    if row["tier"] == "C":
                        self.assertFalse(row["default_eligible"], row["repo_id"])
                for repo_id in result["selection_policy"]["default_shortlist_ids"]:
                    self.assertIn(repo_id, rows)
                    self.assertIn(rows[repo_id]["tier"], {"A", "B"})

            for slot, shortlist in result["role_shortlists"].items():
                with self.subTest(fixture=fixture_name, surface=f"role:{slot}"):
                    for row in shortlist:
                        if row["tier"] == "C":
                            self.assertFalse(row["default_eligible"], row["repo_id"])

    def test_installable_skill_subtree_stays_below_ten_mib(self):
        payload_files = [
            path
            for path in SKILL_DIR.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix.lower() not in {".pyc", ".pyo"}
        ]
        payload_bytes = sum(path.stat().st_size for path in payload_files)
        self.assertLessEqual(
            payload_bytes,
            MAX_SKILL_BYTES,
            f"installable Skill is {payload_bytes / 1024 / 1024:.2f} MiB; budget is 10 MiB",
        )


class CatalogScaleBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base_projects, base_metrics, patterns = catalog.load_catalog()
        if not base_projects:
            raise AssertionError("catalog must contain seed projects for the scale fixture")

        projects: list[dict[str, object]] = []
        metrics: dict[str, dict[str, object]] = {}
        for index in range(SCALE_BUDGET_PROJECTS):
            source = base_projects[index % len(base_projects)]
            project = copy.deepcopy(source)
            slug = f"scale-owner-{index}/scale-repo-{index}"
            repo_id = f"github:{slug}"
            project["repo_id"] = repo_id
            project["url"] = f"https://github.com/{slug}"
            projects.append(project)

            metric = copy.deepcopy(base_metrics.get(str(source.get("repo_id")), {}))
            metric["repo_id"] = repo_id
            metrics[repo_id] = metric

        cls.projects = projects
        cls.metrics = metrics
        cls.patterns = patterns
        cls.requirements = json.loads(
            (FIXTURE_DIR / "minio-browser-download.json").read_text(encoding="utf-8")
        )

    def test_recommend_two_thousand_project_capacity_within_five_seconds(self):
        gc.collect()
        with (
            mock.patch.object(
                catalog,
                "load_catalog",
                return_value=(self.projects, self.metrics, self.patterns),
            ),
            mock.patch.object(catalog, "load_reviews", return_value={}),
            mock.patch.object(
                catalog,
                "load_catalog_metadata",
                return_value={"catalog_version": "1.0.0"},
            ),
        ):
            started = time.perf_counter()
            result = catalog.recommend(self.requirements, limit=12)
            elapsed = time.perf_counter() - started

        self.assertEqual(result["catalog_projects"], SCALE_BUDGET_PROJECTS)
        self.assertLessEqual(
            elapsed,
            MAX_RECOMMEND_SECONDS,
            f"recommend took {elapsed:.3f}s for the 2,000-project synthetic capacity fixture; budget is 5s",
        )

    def test_search_two_thousand_project_capacity_within_one_second(self):
        gc.collect()
        with mock.patch.object(
            catalog,
            "load_catalog",
            return_value=(self.projects, self.metrics, self.patterns),
        ):
            started = time.perf_counter()
            result = catalog.search("kubernetes observability", 20, 0, [])
            elapsed = time.perf_counter() - started

        self.assertGreater(result["count"], 0)
        self.assertLessEqual(
            elapsed,
            MAX_SEARCH_SECONDS,
            f"search took {elapsed:.3f}s for the 2,000-project synthetic capacity fixture; budget is 1s",
        )


if __name__ == "__main__":
    unittest.main()
