from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "tools" / "refresh_github_metrics.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


refresh = load_module("refresh_github_metrics", SCRIPT_PATH)


def api_payload(node_id: str, full_name: str, stars: int = 200) -> dict[str, object]:
    return {
        "node_id": node_id,
        "full_name": full_name,
        "stargazers_count": stars,
        "forks_count": 20,
        "open_issues_count": 3,
        "archived": False,
        "disabled": False,
        "fork": False,
        "default_branch": "main",
        "language": "Python",
        "license": {"spdx_id": "Apache-2.0"},
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": "2026-07-29T00:00:00Z",
        "pushed_at": "2026-07-29T00:00:00Z",
    }


def cached_metric(repo_id: str, node_id: str, canonical_slug: str) -> dict[str, object]:
    return {
        "repo_id": repo_id,
        "github_node_id": node_id,
        "canonical_slug": canonical_slug,
        "stars": 100,
        "status": "ok",
        "source": "github-rest-api",
        "fetched_at": "2026-07-28T00:00:00Z",
    }


class RefreshIdentityTests(unittest.TestCase):
    def test_merge_reloads_latest_snapshot_before_overlaying_this_run(self):
        repo_a = "github:owner/a"
        repo_b = "github:owner/b"
        original_a = cached_metric(repo_a, "node-a", "owner/a")
        newer_a = {**original_a, "stars": 999, "fetched_at": "2026-08-04T00:00:00Z"}
        update_b = cached_metric(repo_b, "node-b", "owner/b")

        with tempfile.TemporaryDirectory() as temp_name:
            metrics_path = Path(temp_name) / "github-metrics.jsonl"
            refresh.write_jsonl(metrics_path, [original_a])

            # Another shard finishes after this run loaded its stale in-memory snapshot.
            refresh.write_jsonl(metrics_path, [newer_a])
            refresh.merge_metric_updates(metrics_path, {repo_b: update_b})

            merged = {row["repo_id"]: row for row in refresh.read_jsonl(metrics_path)}
            self.assertEqual(merged[repo_a], newer_a)
            self.assertEqual(merged[repo_b], update_b)
            self.assertFalse(metrics_path.with_suffix(".jsonl.tmp").exists())

    def test_merge_waits_for_another_writer_and_preserves_both_updates(self):
        repo_a = "github:owner/a"
        repo_b = "github:owner/b"
        update_a = cached_metric(repo_a, "node-a", "owner/a")
        update_b = cached_metric(repo_b, "node-b", "owner/b")

        with tempfile.TemporaryDirectory() as temp_name:
            metrics_path = Path(temp_name) / "github-metrics.jsonl"
            refresh.write_jsonl(metrics_path, [])
            worker_started = threading.Event()
            worker_finished = threading.Event()

            def merge_from_worker() -> None:
                worker_started.set()
                refresh.merge_metric_updates(metrics_path, {repo_b: update_b})
                worker_finished.set()

            with refresh.exclusive_file_lock(refresh.metrics_lock_path(metrics_path)):
                thread = threading.Thread(target=merge_from_worker, daemon=True)
                thread.start()
                self.assertTrue(worker_started.wait(1.0))
                self.assertFalse(worker_finished.wait(0.15))
                # Simulate the first writer committing while it owns the lock.
                current = {row["repo_id"]: row for row in refresh.read_jsonl(metrics_path)}
                current[repo_a] = update_a
                refresh.write_jsonl(metrics_path, list(current.values()))

            thread.join(2.0)
            self.assertFalse(thread.is_alive())
            self.assertTrue(worker_finished.is_set())
            merged = {row["repo_id"]: row for row in refresh.read_jsonl(metrics_path)}
            self.assertEqual(merged, {repo_a: update_a, repo_b: update_b})

    def test_scheduler_prioritizes_pending_then_oldest_without_starvation(self):
        projects = [{"repo_id": f"github:owner/repo-{index}"} for index in range(5)]
        metrics = {
            "github:owner/repo-0": refresh.pending_metric("github:owner/repo-0"),
            "github:owner/repo-1": {"status": "ok", "fetched_at": "2025-01-01T00:00:00Z"},
            "github:owner/repo-2": {"status": "ok", "fetched_at": "2025-02-01T00:00:00Z"},
            "github:owner/repo-3": {"status": "ok", "fetched_at": "2025-03-01T00:00:00Z"},
            "github:owner/repo-4": {"status": "ok", "fetched_at": "2025-04-01T00:00:00Z"},
        }

        first = refresh.schedule_projects(projects, metrics, 2)
        self.assertEqual(
            [row["repo_id"] for row in first],
            ["github:owner/repo-0", "github:owner/repo-1"],
        )
        for row in first:
            metrics[row["repo_id"]] = {"status": "ok", "fetched_at": "2026-08-03T00:00:00Z"}
        second = refresh.schedule_projects(projects, metrics, 2)
        self.assertEqual(
            [row["repo_id"] for row in second],
            ["github:owner/repo-2", "github:owner/repo-3"],
        )

    def test_only_pending_and_stable_shards_are_supported(self):
        projects = [{"repo_id": f"github:owner/repo-{index}"} for index in range(20)]
        metrics = {
            row["repo_id"]: (
                refresh.pending_metric(row["repo_id"])
                if index % 3 == 0
                else {"status": "ok", "fetched_at": "2026-08-03T00:00:00Z"}
            )
            for index, row in enumerate(projects)
        }
        pending = refresh.schedule_projects(projects, metrics, 100, only_pending=True)
        self.assertTrue(pending)
        self.assertTrue(all(metrics[row["repo_id"]]["status"] != "ok" for row in pending))

        shards = [
            {row["repo_id"] for row in refresh.schedule_projects(projects, metrics, 100, shard=(index, 4))}
            for index in range(4)
        ]
        self.assertEqual(set().union(*shards), {row["repo_id"] for row in projects})
        self.assertTrue(all(not (left & right) for i, left in enumerate(shards) for right in shards[i + 1 :]))

    def test_refreshes_matching_identity_without_treating_case_as_a_rename(self):
        cached = cached_metric("github:Owner/Repo", "node-stable", "owner/repo")

        metric, note = refresh.reconcile_metric(
            "github:Owner/Repo",
            cached,
            api_payload("node-stable", "OWNER/REPO", stars=250),
            "2026-07-29T01:00:00Z",
        )

        self.assertEqual(metric["stars"], 250)
        self.assertIsNone(note)

    def test_rejects_different_node_before_overwriting_ok_cache(self):
        cached = cached_metric("github:owner/repo", "node-old", "owner/repo")

        with self.assertRaisesRegex(refresh.IdentityDriftError, "github_node_id changed"):
            refresh.reconcile_metric(
                "github:owner/repo",
                cached,
                api_payload("node-new", "owner/repo"),
                "2026-07-29T01:00:00Z",
            )

        self.assertEqual(cached["stars"], 100)

    def test_rejects_full_name_mismatch_without_established_same_node(self):
        pending = refresh.pending_metric("github:owner/repo")

        with self.assertRaisesRegex(refresh.IdentityDriftError, "does not match requested slug"):
            refresh.reconcile_metric(
                "github:owner/repo",
                pending,
                api_payload("node-new", "other/repo"),
                "2026-07-29T01:00:00Z",
            )

    def test_accepts_same_node_rename_after_curated_repo_id_is_updated(self):
        cached = cached_metric("github:new-owner/new-repo", "node-stable", "old-owner/old-repo")

        metric, note = refresh.reconcile_metric(
            "github:new-owner/new-repo",
            cached,
            api_payload("node-stable", "new-owner/new-repo", stars=300),
            "2026-07-29T01:00:00Z",
        )

        self.assertEqual(metric["canonical_slug"], "new-owner/new-repo")
        self.assertEqual(metric["github_node_id"], "node-stable")
        self.assertEqual(metric["stars"], 300)
        self.assertIn("accepted same-node rename", note or "")

    def test_main_reports_same_node_rename_and_preserves_cache(self):
        repo_id = "github:old-owner/old-repo"
        project = {"repo_id": repo_id, "curation": {"tier": "B"}}
        cached = cached_metric(repo_id, "node-stable", "old-owner/old-repo")
        payload = api_payload("node-stable", "new-owner/new-repo", stars=999)

        with tempfile.TemporaryDirectory() as temp_name:
            metrics_path = Path(temp_name) / "github-metrics.jsonl"
            metrics_path.write_text(json.dumps(cached) + "\n", encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch.object(refresh, "METRICS_PATH", metrics_path),
                mock.patch.object(refresh, "load_projects", return_value=[project]),
                mock.patch.object(refresh, "request_repo", return_value=(payload, {})),
                mock.patch.object(sys, "argv", ["refresh_github_metrics.py"]),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                result = refresh.main()

            self.assertEqual(result, 1)
            self.assertEqual(refresh.read_jsonl(metrics_path), [cached])
            self.assertIn("same-node rename detected", stderr.getvalue())
            summary = json.loads(stdout.getvalue().strip().splitlines()[-1])
            self.assertEqual(summary["updated"], 0)
            self.assertEqual(summary["identity_failures"], 1)


if __name__ == "__main__":
    unittest.main()
