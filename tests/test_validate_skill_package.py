from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "validate_skill_package.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = load_module("validate_skill_package", SCRIPT_PATH)


class ReleaseDocumentationTests(unittest.TestCase):
    def test_v1_target_is_consistently_fixed_at_1000(self):
        self.assertEqual(validator.EXPECTED_SKILL_VERSION, "1.0.1")
        self.assertEqual(validator.EXPECTED_CATALOG_VERSION, "1.0.0")
        self.assertEqual(
            validator.EXPECTED_NPX_INSTALL_COMMAND,
            f"npx skills add {validator.EXPECTED_INSTALL_URL}",
        )
        self.assertEqual(validator.EXPECTED_CLAUDE_PLUGIN_NAME, "proven-cloud-stack")
        self.assertEqual(
            validator.EXPECTED_CLAUDE_MARKETPLACE_NAME, "cloud-architecture-skill"
        )
        self.assertEqual(
            validator.EXPECTED_PROJECT_COUNTS,
            {
                "total": 1000,
                "tier_a": 0,
                "tier_b": 58,
                "tier_c": 942,
            },
        )
        self.assertEqual(validator.EXPECTED_STATIC_PROJECTS, 98)
        self.assertEqual(validator.EXPECTED_EXPANDED_PROJECTS, 902)
        self.assertEqual(validator.EXPECTED_FRESH_METRICS, 983)
        self.assertEqual(validator.EXPECTED_PENDING_METRICS, 17)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        schema = (
            ROOT
            / "skills"
            / "choose-proven-cloud-stack"
            / "references"
            / "catalog-schema.md"
        ).read_text(encoding="utf-8")
        interface = (
            ROOT / "skills" / "choose-proven-cloud-stack" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
        skill = (
            ROOT / "skills" / "choose-proven-cloud-stack" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("| 项目总数 | 500 |", readme)
        self.assertNotIn("| 项目总数 | 2000 |", readme)
        self.assertNotIn("exactly 500 unique project records", schema)
        self.assertNotIn("exactly 2,000 unique project records", schema)
        self.assertIn("| 项目总数 | 1000 |", readme)
        self.assertIn("exactly 1,000 unique project records", schema)
        self.assertIn("across 1,000 repositories", interface)
        self.assertIn("catalog of 1,000 repositories", skill)
        self.assertNotIn("catalog of roughly 2,000 repositories", skill)
        self.assertNotIn("Use when Codex needs", skill)
        self.assertNotIn("python scripts/", skill)
        self.assertNotIn("python3 scripts/", skill)
        self.assertIn("<skill-root>", skill)
        self.assertIn('"<skill-root>/scripts/', skill)
        self.assertIn(validator.EXPECTED_NPX_INSTALL_COMMAND, readme)


class PackageContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skills" / "choose-proven-cloud-stack"
        for relative in validator.REQUIRED_SKILL_FILES:
            path = self.skill / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

        license_text = "test license\n"
        (self.root / "LICENSE").write_text(license_text, encoding="utf-8")
        (self.skill / "LICENSE.txt").write_text(license_text, encoding="utf-8")
        (self.skill / "SKILL.md").write_text(
            "---\n"
            "name: choose-proven-cloud-stack\n"
            "description: Select evidence-backed cloud architectures and repositories.\n"
            "---\n\n"
            "# Test Skill\n\n"
            "Resolve this SKILL.md directory as `<skill-root>`. "
            "Never assume the current working directory is the Skill directory.\n\n"
            'Run `python "<skill-root>/scripts/catalog.py" stats`.\n',
            encoding="utf-8",
        )
        (self.skill / "agents" / "openai.yaml").write_text(
            'interface:\n'
            '  display_name: "Proven Cloud Stack"\n'
            '  short_description: "Compare cloud architectures across repositories"\n'
            '  default_prompt: "Use $choose-proven-cloud-stack to compare cloud projects."\n',
            encoding="utf-8",
        )
        (self.root / "README.md").write_text(
            f"{validator.EXPECTED_INSTALL_URL}\n"
            f"{validator.EXPECTED_NPX_INSTALL_COMMAND}\n"
            f"`v{validator.EXPECTED_SKILL_VERSION}` 分发版本包含目录快照 "
            f"`{validator.EXPECTED_CATALOG_VERSION}`（{validator.EXPECTED_SNAPSHOT_DATE}）\n"
            "| 项目总数 | 3 |\n"
            "| A：代码深度验证 | 0 |\n"
            "| B：人工结构化且指标已核验 | 1 |\n"
            "| C：发现目录 | 2 |\n",
            encoding="utf-8",
        )
        (self.root / "README.en.md").write_text("# Test Skill\n", encoding="utf-8")
        (self.root / ".claude-plugin").mkdir(parents=True)
        (self.root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": validator.EXPECTED_CLAUDE_PLUGIN_NAME,
                    "version": validator.EXPECTED_SKILL_VERSION,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": validator.EXPECTED_CLAUDE_MARKETPLACE_NAME,
                    "owner": {"name": "test"},
                    "plugins": [
                        {
                            "name": validator.EXPECTED_CLAUDE_PLUGIN_NAME,
                            "source": "./",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        curated = self._project("github:static/curated", "B")
        discovery = self._project("github:static/discovery", "C")
        expanded = self._project("github:generated/project", "C")
        expanded["curation"]["source"] = "github-topic-discovery"
        expanded["discovery"] = {
            "assigned_pattern_id": "pattern-a",
            "provenance": [
                {
                    "pattern_id": "pattern-a",
                    "profile_id": "profile-a",
                    "query_id": "query-a",
                    "topic": "cloud",
                }
            ],
        }
        self._write_jsonl("projects-curated.jsonl", [curated])
        self._write_jsonl("projects-discovery.jsonl", [discovery])
        self._write_jsonl("projects-expanded.jsonl", [expanded])
        self._write_jsonl(
            "github-metrics.jsonl",
            [{"repo_id": row["repo_id"]} for row in (curated, discovery, expanded)],
        )
        self._write_jsonl("patterns-core.jsonl", [{"pattern_id": "pattern-a"}])
        self._write_jsonl("reviews.jsonl", [])
        self._write_json(
            "catalog-metadata.json",
            {
                "schema_version": "1.0",
                "catalog_version": "1.0.0",
                "generated_at": "2026-08-03T00:00:00Z",
                "projects": {
                    "total": 3,
                    "tier_a": 0,
                    "tier_b": 1,
                    "tier_c": 2,
                },
                "patterns": 1,
                "github_metrics": {
                    "fresh": 2,
                    "pending_or_missing": 1,
                    "freshness_days": 30,
                },
            },
        )
        self._write_json(
            "discovery-manifest.json",
            {
                "schema_version": "1.0",
                "snapshot_date": "2026-08-03",
                "cache_complete": True,
                "queries": {"planned": 1, "cached": 1, "items": 1},
                "selection": {
                    "target_total": 3,
                    "static": 2,
                    "expanded": 1,
                    "by_pattern": {"pattern-a": 1},
                },
                "query_provenance": [
                    {
                        "query_id": "query-a",
                        "profile_id": "profile-a",
                        "pattern_id": "pattern-a",
                        "topic": "cloud",
                        "fetched_at": "2026-08-03T00:00:00Z",
                        "items": 1,
                    }
                ],
            },
        )
        self._write_json("discovery-profiles.json", {"schema_version": "1.0"})
        self._write_json("term-map.json", {"schema_version": "1.0"})

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _project(repo_id: str, tier: str):
        return {
            "schema_version": "1.0",
            "repo_id": repo_id,
            "curation": {"tier": tier, "catalogued_at": "2026-08-03"},
        }

    def _write_json(self, name: str, value):
        (self.skill / "references" / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _write_jsonl(self, name: str, rows):
        (self.skill / "references" / name).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _validate(self):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    validator,
                    "EXPECTED_PROJECT_COUNTS",
                    {
                        "total": 3,
                        "tier_a": 0,
                        "tier_b": 1,
                        "tier_c": 2,
                    },
                )
            )
            stack.enter_context(mock.patch.object(validator, "EXPECTED_STATIC_PROJECTS", 2))
            stack.enter_context(mock.patch.object(validator, "EXPECTED_EXPANDED_PROJECTS", 1))
            stack.enter_context(mock.patch.object(validator, "EXPECTED_PATTERNS", 1))
            stack.enter_context(mock.patch.object(validator, "EXPECTED_FRESH_METRICS", 2))
            stack.enter_context(mock.patch.object(validator, "EXPECTED_PENDING_METRICS", 1))
            return validator.validate_package(self.root, self.skill)

    def test_valid_fixed_snapshot_package(self):
        self.assertEqual(self._validate(), [])

    def test_rejects_wrong_catalog_version(self):
        path = self.skill / "references" / "catalog-metadata.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["catalog_version"] = "0.1.0-dev"
        self._write_json("catalog-metadata.json", metadata)
        self.assertTrue(
            any("catalog_version" in error for error in self._validate()), self._validate()
        )

    def test_rejects_cwd_relative_bundled_script_command(self):
        path = self.skill / "SKILL.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "\nRun `python scripts/catalog.py stats`.\n",
            encoding="utf-8",
        )
        errors = self._validate()
        self.assertTrue(any("resolve from <skill-root>" in error for error in errors), errors)

    def test_rejects_mismatched_claude_plugin_version(self):
        path = self.root / ".claude-plugin" / "plugin.json"
        plugin = json.loads(path.read_text(encoding="utf-8"))
        plugin["version"] = "1.0.0"
        path.write_text(json.dumps(plugin) + "\n", encoding="utf-8")
        errors = self._validate()
        self.assertTrue(any("Claude plugin version" in error for error in errors), errors)

    def test_requires_manifest_and_review_files(self):
        (self.skill / "references" / "discovery-manifest.json").unlink()
        (self.skill / "references" / "reviews.jsonl").unlink()
        errors = self._validate()
        self.assertTrue(any("discovery-manifest.json" in error for error in errors), errors)
        self.assertTrue(any("reviews.jsonl" in error for error in errors), errors)

    def test_rejects_project_provenance_missing_from_manifest(self):
        path = self.skill / "references" / "projects-expanded.jsonl"
        project = json.loads(path.read_text(encoding="utf-8"))
        project["discovery"]["provenance"][0]["query_id"] = "unknown-query"
        self._write_jsonl("projects-expanded.jsonl", [project])
        errors = self._validate()
        self.assertTrue(any("unknown discovery query IDs" in error for error in errors), errors)

    def test_rejects_missing_and_orphan_metrics_even_when_record_count_matches(self):
        metrics_path = self.skill / "references" / "github-metrics.jsonl"
        metrics = [
            json.loads(line)
            for line in metrics_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        missing_repo_id = "github:generated/project"
        metrics = [row for row in metrics if row["repo_id"] != missing_repo_id]
        metrics.append({"repo_id": "github:orphan/project"})
        self.assertEqual(len(metrics), 3)
        self._write_jsonl("github-metrics.jsonl", metrics)

        errors = self._validate()

        self.assertTrue(
            any(
                "missing project repo_ids" in error and missing_repo_id in error
                for error in errors
            ),
            errors,
        )
        self.assertTrue(
            any(
                "orphan repo_ids" in error and "github:orphan/project" in error
                for error in errors
            ),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
