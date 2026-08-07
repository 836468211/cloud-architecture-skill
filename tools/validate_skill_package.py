#!/usr/bin/env python3
"""Validate the installable Skill subtree and fixed release contract."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "choose-proven-cloud-stack"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCAL_LINK_RE = re.compile(r"\]\((?!https?://|#)([^)]+)\)")

EXPECTED_SCHEMA_VERSION = "1.0"
EXPECTED_SKILL_VERSION = "1.0.1"
EXPECTED_CATALOG_VERSION = "1.0.0"
EXPECTED_SNAPSHOT_DATE = "2026-08-03"
EXPECTED_GENERATED_AT = "2026-08-03T00:00:00Z"
EXPECTED_INSTALL_URL = (
    "https://github.com/836468211/cloud-architecture-skill/"
    f"tree/v{EXPECTED_SKILL_VERSION}/skills/choose-proven-cloud-stack"
)
EXPECTED_NPX_INSTALL_COMMAND = f"npx skills add {EXPECTED_INSTALL_URL}"
EXPECTED_CLAUDE_PLUGIN_NAME = "proven-cloud-stack"
EXPECTED_CLAUDE_MARKETPLACE_NAME = "cloud-architecture-skill"
EXPECTED_PROJECT_COUNTS = {
    "total": 1000,
    "tier_a": 0,
    "tier_b": 58,
    "tier_c": 942,
}
EXPECTED_STATIC_PROJECTS = 98
EXPECTED_EXPANDED_PROJECTS = 902
EXPECTED_PATTERNS = 34
EXPECTED_FRESH_METRICS = 983
EXPECTED_PENDING_METRICS = 17

REQUIRED_SKILL_FILES = (
    "SKILL.md",
    "LICENSE.txt",
    "agents/openai.yaml",
    "scripts/catalog.py",
    "scripts/inspect_repository.py",
    "scripts/validate_catalog.py",
    "references/catalog-metadata.json",
    "references/catalog-schema.md",
    "references/discovery-manifest.json",
    "references/discovery-profiles.json",
    "references/github-metrics.jsonl",
    "references/patterns-core.jsonl",
    "references/projects-curated.jsonl",
    "references/projects-discovery.jsonl",
    "references/projects-expanded.jsonl",
    "references/requirements-schema.md",
    "references/review-playbook.md",
    "references/reviews.jsonl",
    "references/scoring-model.md",
    "references/source-policy.md",
    "references/term-map.json",
)
PROJECT_FILES = (
    "projects-curated.jsonl",
    "projects-discovery.jsonl",
    "projects-expanded.jsonl",
)


def _read_json(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"invalid {label}: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"invalid {label}: root must be an object")
        return None
    return value


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot read {path.name}: {exc}")
        return rows
    for line_number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_number}: record must be an object")
            continue
        rows.append(value)
    return rows


def _expect_mapping_values(
    errors: list[str], label: str, value: object, expected: dict[str, object]
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            errors.append(
                f"{label}.{key} must be {expected_value!r}, got {value.get(key)!r}"
            )


def _validate_readme(root: Path, errors: list[str]) -> None:
    readme_path = root / "README.md"
    try:
        readme = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        errors.append(f"cannot read README.md: {exc}")
        return
    markers = (
        EXPECTED_INSTALL_URL,
        EXPECTED_NPX_INSTALL_COMMAND,
        (
            f"`v{EXPECTED_SKILL_VERSION}` 分发版本包含目录快照 "
            f"`{EXPECTED_CATALOG_VERSION}`（{EXPECTED_SNAPSHOT_DATE}）"
        ),
        f"| 项目总数 | {EXPECTED_PROJECT_COUNTS['total']} |",
        f"| A：代码深度验证 | {EXPECTED_PROJECT_COUNTS['tier_a']} |",
        f"| B：人工结构化且指标已核验 | {EXPECTED_PROJECT_COUNTS['tier_b']} |",
        f"| C：发现目录 | {EXPECTED_PROJECT_COUNTS['tier_c']} |",
    )
    for marker in markers:
        if marker not in readme:
            errors.append(f"README.md lacks v1 release marker: {marker}")


def _validate_skill_metadata(root: Path, skill_dir: Path, errors: list[str]) -> None:
    skill_md = skill_dir / "SKILL.md"
    license_file = skill_dir / "LICENSE.txt"
    interface_file = skill_dir / "agents" / "openai.yaml"

    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        errors.append("SKILL.md must start with YAML frontmatter")
        return
    try:
        closing = lines.index("---", 1)
    except ValueError:
        errors.append("SKILL.md frontmatter is not closed")
        return

    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip():
            continue
        match = re.fullmatch(r"([a-z_]+):\s*(.+)", line)
        if not match:
            errors.append(f"unsupported SKILL.md frontmatter line: {line!r}")
            continue
        fields[match.group(1)] = match.group(2).strip().strip("\"'")
    if set(fields) != {"name", "description"}:
        errors.append("SKILL.md frontmatter must contain only name and description")
    name = fields.get("name", "")
    if name != skill_dir.name or not NAME_RE.fullmatch(name) or len(name) > 63:
        errors.append("Skill name must match its lowercase hyphenated directory name")
    if not fields.get("description"):
        errors.append("Skill description must be non-empty")
    if "Use when Codex needs" in fields.get("description", ""):
        errors.append("SKILL.md description must be platform-neutral")

    body = "\n".join(lines[closing + 1 :])
    if "<skill-root>" not in body or "Never assume the current working directory" not in body:
        errors.append("SKILL.md must explain how to resolve <skill-root>")
    if re.search(r"\bpython3?\s+scripts/", body):
        errors.append("SKILL.md bundled script commands must resolve from <skill-root>")
    if '"<skill-root>/scripts/' not in body:
        errors.append("SKILL.md must contain a quoted <skill-root>/scripts/ command")

    for target in LOCAL_LINK_RE.findall(text):
        clean = target.split("#", 1)[0]
        if clean and not (skill_dir / clean).is_file():
            errors.append(f"SKILL.md local link does not exist: {target}")

    interface = interface_file.read_text(encoding="utf-8")
    interface_values: dict[str, str] = {}
    for key in ("display_name", "short_description", "default_prompt"):
        match = re.search(rf'^\s{{2}}{key}:\s+"([^"]+)"\s*$', interface, re.MULTILINE)
        if not match:
            errors.append(f"agents/openai.yaml lacks a quoted {key}")
        else:
            interface_values[key] = match.group(1)
    description = interface_values.get("short_description", "")
    if description and not 25 <= len(description) <= 64:
        errors.append("agents/openai.yaml short_description must contain 25-64 characters")
    if name and f"${name}" not in interface_values.get("default_prompt", ""):
        errors.append("agents/openai.yaml default_prompt must reference the Skill name")

    if (skill_dir / "README.md").exists():
        errors.append("README.md belongs at repository root, not inside the installable Skill")
    if license_file.read_text(encoding="utf-8").rstrip() != (root / "LICENSE").read_text(
        encoding="utf-8"
    ).rstrip():
        errors.append("install-subtree LICENSE.txt must match the repository license")


def _validate_claude_plugin(root: Path, errors: list[str]) -> None:
    plugin = _read_json(root / ".claude-plugin" / "plugin.json", errors, "Claude plugin")
    marketplace = _read_json(
        root / ".claude-plugin" / "marketplace.json", errors, "Claude marketplace"
    )
    if plugin is not None:
        if plugin.get("name") != EXPECTED_CLAUDE_PLUGIN_NAME:
            errors.append(
                f"Claude plugin name must be {EXPECTED_CLAUDE_PLUGIN_NAME!r}"
            )
        if plugin.get("version") != EXPECTED_SKILL_VERSION:
            errors.append(
                f"Claude plugin version must be {EXPECTED_SKILL_VERSION!r}"
            )
    if marketplace is not None:
        if marketplace.get("name") != EXPECTED_CLAUDE_MARKETPLACE_NAME:
            errors.append(
                "Claude marketplace name must be "
                f"{EXPECTED_CLAUDE_MARKETPLACE_NAME!r}"
            )
        plugins = marketplace.get("plugins")
        if not isinstance(plugins, list) or len(plugins) != 1:
            errors.append("Claude marketplace must contain exactly one plugin")
        else:
            entry = plugins[0]
            if not isinstance(entry, dict):
                errors.append("Claude marketplace plugin entry must be an object")
            else:
                if entry.get("name") != EXPECTED_CLAUDE_PLUGIN_NAME:
                    errors.append("Claude marketplace plugin name does not match plugin.json")
                if entry.get("source") != "./":
                    errors.append("Claude marketplace plugin source must be './'")


def _validate_catalog_contract(skill_dir: Path, errors: list[str]) -> None:
    reference_dir = skill_dir / "references"
    metadata = _read_json(reference_dir / "catalog-metadata.json", errors, "catalog metadata")
    manifest = _read_json(reference_dir / "discovery-manifest.json", errors, "discovery manifest")

    project_rows: dict[str, list[dict[str, Any]]] = {
        name: _read_jsonl(reference_dir / name, errors) for name in PROJECT_FILES
    }
    all_projects = [row for name in PROJECT_FILES for row in project_rows[name]]
    metrics = _read_jsonl(reference_dir / "github-metrics.jsonl", errors)
    patterns = _read_jsonl(reference_dir / "patterns-core.jsonl", errors)
    _read_jsonl(reference_dir / "reviews.jsonl", errors)

    actual_tiers = Counter(
        str(row.get("curation", {}).get("tier", "")).upper()
        if isinstance(row.get("curation"), dict)
        else ""
        for row in all_projects
    )
    actual_counts = {
        "total": len(all_projects),
        "tier_a": actual_tiers.get("A", 0),
        "tier_b": actual_tiers.get("B", 0),
        "tier_c": actual_tiers.get("C", 0),
    }
    for key, expected in EXPECTED_PROJECT_COUNTS.items():
        if actual_counts[key] != expected:
            errors.append(
                f"project files contain {actual_counts[key]} {key} records; expected {expected}"
            )
    actual_static = len(project_rows["projects-curated.jsonl"]) + len(
        project_rows["projects-discovery.jsonl"]
    )
    actual_expanded = len(project_rows["projects-expanded.jsonl"])
    if actual_static != EXPECTED_STATIC_PROJECTS:
        errors.append(
            f"static project files contain {actual_static} records; expected {EXPECTED_STATIC_PROJECTS}"
        )
    if actual_expanded != EXPECTED_EXPANDED_PROJECTS:
        errors.append(
            f"projects-expanded.jsonl contains {actual_expanded} records; "
            f"expected {EXPECTED_EXPANDED_PROJECTS}"
        )
    if len(metrics) != EXPECTED_PROJECT_COUNTS["total"]:
        errors.append(
            f"github-metrics.jsonl contains {len(metrics)} records; "
            f"expected {EXPECTED_PROJECT_COUNTS['total']}"
        )
    project_repo_ids = {str(row.get("repo_id", "")) for row in all_projects}
    metric_repo_ids = {str(row.get("repo_id", "")) for row in metrics}
    missing_metric_repo_ids = project_repo_ids - metric_repo_ids
    orphan_metric_repo_ids = metric_repo_ids - project_repo_ids
    if missing_metric_repo_ids:
        errors.append(
            "github-metrics.jsonl is missing project repo_ids: "
            f"{sorted(missing_metric_repo_ids)}"
        )
    if orphan_metric_repo_ids:
        errors.append(
            "github-metrics.jsonl contains orphan repo_ids: "
            f"{sorted(orphan_metric_repo_ids)}"
        )
    if len(patterns) != EXPECTED_PATTERNS:
        errors.append(
            f"patterns-core.jsonl contains {len(patterns)} records; expected {EXPECTED_PATTERNS}"
        )

    if metadata is not None:
        if metadata.get("schema_version") != EXPECTED_SCHEMA_VERSION:
            errors.append(
                f"catalog metadata schema_version must be {EXPECTED_SCHEMA_VERSION!r}"
            )
        if metadata.get("catalog_version") != EXPECTED_CATALOG_VERSION:
            errors.append(
                f"catalog metadata catalog_version must be {EXPECTED_CATALOG_VERSION!r}"
            )
        if metadata.get("generated_at") != EXPECTED_GENERATED_AT:
            errors.append(f"catalog metadata generated_at must be {EXPECTED_GENERATED_AT!r}")
        _expect_mapping_values(
            errors, "catalog metadata projects", metadata.get("projects"), EXPECTED_PROJECT_COUNTS
        )
        if metadata.get("patterns") != EXPECTED_PATTERNS:
            errors.append(f"catalog metadata patterns must be {EXPECTED_PATTERNS}")
        _expect_mapping_values(
            errors,
            "catalog metadata github_metrics",
            metadata.get("github_metrics"),
            {
                "fresh": EXPECTED_FRESH_METRICS,
                "pending_or_missing": EXPECTED_PENDING_METRICS,
                "freshness_days": 30,
            },
        )

    manifest_query_ids: set[str] = set()
    if manifest is not None:
        if manifest.get("schema_version") != EXPECTED_SCHEMA_VERSION:
            errors.append(
                f"discovery manifest schema_version must be {EXPECTED_SCHEMA_VERSION!r}"
            )
        if manifest.get("snapshot_date") != EXPECTED_SNAPSHOT_DATE:
            errors.append(
                f"discovery manifest snapshot_date must be {EXPECTED_SNAPSHOT_DATE!r}"
            )
        if manifest.get("cache_complete") is not True:
            errors.append("discovery manifest cache_complete must be true")
        _expect_mapping_values(
            errors,
            "discovery manifest selection",
            manifest.get("selection"),
            {
                "target_total": EXPECTED_PROJECT_COUNTS["total"],
                "static": EXPECTED_STATIC_PROJECTS,
                "expanded": EXPECTED_EXPANDED_PROJECTS,
            },
        )
        queries = manifest.get("queries")
        if not isinstance(queries, dict):
            errors.append("discovery manifest queries must be an object")
        else:
            planned = queries.get("planned")
            cached = queries.get("cached")
            if not isinstance(planned, int) or planned <= 0 or cached != planned:
                errors.append("discovery manifest must contain equal positive planned/cached counts")
        provenance = manifest.get("query_provenance")
        if not isinstance(provenance, list) or not provenance:
            errors.append("discovery manifest query_provenance must be a non-empty array")
        else:
            for item in provenance:
                if isinstance(item, dict) and isinstance(item.get("query_id"), str):
                    manifest_query_ids.add(item["query_id"])
            if isinstance(queries, dict) and len(provenance) != queries.get("cached"):
                errors.append(
                    "discovery manifest query_provenance count must equal queries.cached"
                )
        selection = manifest.get("selection")
        by_pattern = selection.get("by_pattern") if isinstance(selection, dict) else None
        if not isinstance(by_pattern, dict) or any(
            not isinstance(value, int) or value < 0 for value in by_pattern.values()
        ):
            errors.append("discovery manifest selection.by_pattern must contain nonnegative counts")
        elif sum(by_pattern.values()) != EXPECTED_EXPANDED_PROJECTS:
            errors.append(
                "discovery manifest selection.by_pattern must sum to expanded project count"
            )

    for index, project in enumerate(project_rows["projects-expanded.jsonl"], 1):
        label = f"projects-expanded.jsonl:{index}"
        if project.get("schema_version") != EXPECTED_SCHEMA_VERSION:
            errors.append(f"{label}: schema_version must be {EXPECTED_SCHEMA_VERSION!r}")
        curation = project.get("curation")
        if not isinstance(curation, dict) or curation.get("tier") != "C" or curation.get(
            "source"
        ) != "github-topic-discovery":
            errors.append(f"{label}: generated project must be Tier C github-topic-discovery")
        discovery = project.get("discovery")
        provenance = discovery.get("provenance") if isinstance(discovery, dict) else None
        if (
            not isinstance(discovery, dict)
            or not isinstance(discovery.get("assigned_pattern_id"), str)
            or not isinstance(provenance, list)
            or not provenance
        ):
            errors.append(f"{label}: generated project requires discovery provenance")
            continue
        project_query_ids = {
            item.get("query_id") for item in provenance if isinstance(item, dict)
        }
        unknown = {item for item in project_query_ids if item not in manifest_query_ids}
        if unknown:
            errors.append(f"{label}: unknown discovery query IDs: {sorted(unknown, key=str)}")


def validate_package(root: Path = ROOT, skill_dir: Path | None = None) -> list[str]:
    """Return package-contract errors without printing or exiting."""
    skill_dir = skill_dir or root / "skills" / "choose-proven-cloud-stack"
    errors: list[str] = []
    required_root_files = (
        root / "README.md",
        root / "README.en.md",
        root / "LICENSE",
        root / ".claude-plugin" / "plugin.json",
        root / ".claude-plugin" / "marketplace.json",
    )
    required_skill_files = tuple(skill_dir / relative for relative in REQUIRED_SKILL_FILES)
    for path in (*required_root_files, *required_skill_files):
        if not path.is_file():
            try:
                display = path.relative_to(root)
            except ValueError:
                display = path
            errors.append(f"missing required package file: {display}")
    if errors:
        return errors

    _validate_readme(root, errors)
    _validate_skill_metadata(root, skill_dir, errors)
    _validate_claude_plugin(root, errors)
    _validate_catalog_contract(skill_dir, errors)
    return errors


def main() -> int:
    return finish(validate_package())


def finish(errors: list[str]) -> int:
    if errors:
        for item in errors:
            print(f"error: {item}", file=sys.stderr)
        return 1
    print("Skill package is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
