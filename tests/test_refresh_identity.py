from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
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
