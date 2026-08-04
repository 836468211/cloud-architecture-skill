from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "choose-proven-cloud-stack" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import catalog  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = load_module("validate_catalog", SCRIPT_DIR / "validate_catalog.py")


class MetricCoverageTests(unittest.TestCase):
    def test_missing_and_orphan_metric_are_blocking_when_total_is_unchanged(self):
        projects, metrics, patterns = catalog.load_catalog()
        missing_repo_id = next(
            str(project["repo_id"])
            for project in projects
            if project.get("curation", {}).get("tier") == "C"
        )
        mismatched_metrics = dict(metrics)
        mismatched_metrics.pop(missing_repo_id)
        orphan_repo_id = "github:catalog-validator/orphan-metric"
        mismatched_metrics[orphan_repo_id] = {
            "repo_id": orphan_repo_id,
            "status": "pending-refresh",
        }
        self.assertEqual(len(mismatched_metrics), len(metrics))

        real_read_jsonl = catalog.read_jsonl

        def read_jsonl_with_mismatch(path: Path):
            if path == catalog.METRICS_FILE:
                return list(mismatched_metrics.values())
            return real_read_jsonl(path)

        stdout = io.StringIO()
        with (
            mock.patch.object(
                validator.catalog,
                "load_catalog",
                return_value=(projects, mismatched_metrics, patterns),
            ),
            mock.patch.object(
                validator.catalog,
                "read_jsonl",
                side_effect=read_jsonl_with_mismatch,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = validator.main()

        result = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1, result)
        self.assertFalse(result["valid"])
        self.assertEqual(result["metrics"], len(metrics))
        self.assertTrue(
            any(
                missing_repo_id in error and "missing GitHub metric record" in error
                for error in result["errors"]
            ),
            result,
        )
        self.assertTrue(
            any(
                orphan_repo_id in error and "metric record has no project record" in error
                for error in result["errors"]
            ),
            result,
        )


if __name__ == "__main__":
    unittest.main()
