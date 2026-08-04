#!/usr/bin/env python3
"""Validate project, metric, pattern, and term records."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import catalog


REPO_ID_RE = re.compile(r"^github:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
GITHUB_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
PATTERN_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VALID_TIERS = {"A", "B", "C"}
PROJECT_LIST_FIELDS = {
    "domains", "operations", "problems", "mechanisms", "topologies",
    "runtimes", "languages", "protocols", "limitations", "pattern_links",
}
VALID_ROLES = {
    "direct-dependency",
    "official-sdk",
    "official-implementation",
    "reference-implementation",
    "mechanism-reference",
    "production-validation",
    "integration-adapter",
    "benchmark-testbed",
    "contrast-only",
}


def error(errors: list[str], record: dict[str, Any], message: str) -> None:
    source = record.get("_source_file", "?")
    line = record.get("_source_line", "?")
    errors.append(f"{source}:{line}: {message}")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        projects, metrics, patterns = catalog.load_catalog()
        aliases, _ = catalog.load_term_map()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"catalog load failed: {exc}", file=sys.stderr)
        return 2

    repo_ids: Counter[str] = Counter()
    urls: Counter[str] = Counter()
    now = datetime.now(timezone.utc)
    for project in projects:
        repo_id = str(project.get("repo_id", ""))
        url = str(project.get("url", ""))
        repo_ids[repo_id] += 1
        urls[url.lower()] += 1
        id_match = REPO_ID_RE.fullmatch(repo_id)
        url_match = GITHUB_URL_RE.fullmatch(url)
        if not id_match:
            error(errors, project, f"invalid repo_id: {repo_id!r}")
        if not url_match:
            error(errors, project, f"invalid canonical GitHub URL: {url!r}")
        if id_match and url_match and tuple(part.lower() for part in id_match.groups()) != tuple(part.lower() for part in url_match.groups()):
            error(errors, project, "repo_id and URL do not identify the same repository")
        tier = str(project.get("curation", {}).get("tier", ""))
        if project.get("schema_version") != "1.0":
            error(errors, project, "project schema_version must be '1.0'")
        if tier not in VALID_TIERS:
            error(errors, project, f"invalid curation tier: {tier!r}")
        if not project.get("primary_domain") or not project.get("domains"):
            error(errors, project, "primary_domain and domains are required")
        elif project.get("primary_domain") not in project.get("domains", []):
            error(errors, project, "primary_domain must also appear in domains")
        if not project.get("summary"):
            error(errors, project, "original summary is required")
        for field in PROJECT_LIST_FIELDS:
            value = project.get(field)
            if not isinstance(value, list):
                error(errors, project, f"{field} must be an array")
            elif field != "limitations" and not value:
                error(errors, project, f"{field} must not be empty")
            elif field != "pattern_links" and any(not isinstance(item, str) for item in value):
                error(errors, project, f"{field} must contain only strings")
            elif field != "pattern_links" and any(not item.strip() for item in value):
                error(errors, project, f"{field} must not contain empty strings")
            elif field != "pattern_links" and len(value) != len(set(value)):
                error(errors, project, f"{field} must not contain duplicates")
        links = project.get("pattern_links")
        if not isinstance(links, list) or not links:
            error(errors, project, "at least one pattern link is required")
            links = []
        for link in links:
            if not isinstance(link, dict):
                error(errors, project, "each pattern link must be an object")
                continue
            pattern_id = link.get("pattern_id")
            if pattern_id not in patterns:
                error(errors, project, f"unknown pattern_id: {pattern_id!r}")
            if not link.get("roles"):
                error(errors, project, f"pattern link {pattern_id!r} requires at least one role")
            roles = link.get("roles", [])
            if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
                error(errors, project, f"pattern link {pattern_id!r} roles must be an array of strings")
                roles = []
            invalid_roles = set(roles) - VALID_ROLES
            if invalid_roles:
                error(errors, project, f"invalid roles: {sorted(invalid_roles)}")
        metric = metrics.get(repo_id)
        if metric is None:
            errors.append(f"{repo_id}: missing GitHub metric record")
        else:
            for field in ("stars", "forks"):
                value = metric.get(field)
                if value is not None and (not isinstance(value, int) or value < 0):
                    error(errors, metric, f"{field} must be null or a nonnegative integer")
            if metric.get("status") == "ok" and not catalog.parse_iso(metric.get("fetched_at")):
                error(errors, metric, "status=ok requires an ISO fetched_at timestamp")
        if tier == "B":
            fetched = catalog.parse_iso(metric.get("fetched_at") if metric else None)
            if not metric or metric.get("status") != "ok" or fetched is None:
                error(errors, project, "Tier B requires a verified GitHub metric snapshot")
            elif (now - fetched).days > 30:
                warnings.append(f"{repo_id}: Tier B GitHub metric snapshot is older than 30 days")

    for repo_id, count in repo_ids.items():
        if count > 1:
            errors.append(f"duplicate repo_id {repo_id!r}: {count} records")
    for url, count in urls.items():
        if count > 1:
            errors.append(f"duplicate URL {url!r}: {count} records")

    metric_rows = catalog.read_jsonl(catalog.METRICS_FILE)
    metric_ids = Counter(str(row.get("repo_id", "")) for row in metric_rows)
    metric_node_ids: Counter[str] = Counter()
    for metric in metric_rows:
        repo_id = str(metric.get("repo_id", ""))
        if not REPO_ID_RE.fullmatch(repo_id):
            error(errors, metric, f"invalid metric repo_id: {repo_id!r}")
        if metric.get("status") not in {"ok", "pending-refresh"}:
            error(errors, metric, f"invalid metric status: {metric.get('status')!r}")
        if metric.get("status") == "ok":
            node_id = metric.get("github_node_id")
            canonical_slug = str(metric.get("canonical_slug") or "")
            if not isinstance(node_id, str) or not node_id:
                error(errors, metric, "status=ok requires github_node_id")
            else:
                metric_node_ids[node_id] += 1
            expected_slug = repo_id.removeprefix("github:")
            if canonical_slug.casefold() != expected_slug.casefold():
                error(errors, metric, "canonical_slug must match metric repo_id")
            for field in ("archived", "disabled", "is_fork"):
                if field in metric and not isinstance(metric.get(field), bool):
                    error(errors, metric, f"status=ok {field} must be a boolean")
    for repo_id, count in metric_ids.items():
        if count > 1:
            errors.append(f"duplicate metric repo_id {repo_id!r}: {count} records")
    for node_id, count in metric_node_ids.items():
        if count > 1:
            errors.append(f"duplicate github_node_id {node_id!r}: {count} metric records")

    pattern_rows = catalog.read_jsonl(catalog.PATTERNS_FILE)
    pattern_ids = Counter(str(row.get("pattern_id", "")) for row in pattern_rows)
    for pattern in pattern_rows:
        pattern_id = str(pattern.get("pattern_id", ""))
        if not PATTERN_ID_RE.fullmatch(pattern_id):
            error(errors, pattern, f"invalid pattern_id: {pattern_id!r}")
        for field in ("domains", "operations", "solves", "topologies", "required_mechanisms", "validated_by"):
            value = pattern.get(field)
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                error(errors, pattern, f"pattern {pattern_id!r} {field} must be an array of strings")
    for pattern_id, count in pattern_ids.items():
        if count > 1:
            errors.append(f"duplicate pattern_id {pattern_id!r}: {count} records")

    known_repos = set(repo_ids)
    for pattern_id, pattern in patterns.items():
        if not pattern.get("name") or not pattern.get("required_mechanisms"):
            error(errors, pattern, f"pattern {pattern_id!r} requires name and required_mechanisms")
        missing = set(pattern.get("validated_by", [])) - known_repos
        if missing:
            error(errors, pattern, f"validated_by references unknown repositories: {sorted(missing)}")

    metrics_without_projects = set(metrics) - known_repos
    for repo_id in sorted(metrics_without_projects):
        errors.append(f"{repo_id}: metric record has no project record")

    valid_review_repos: set[str] = set()
    review_ids: Counter[str] = Counter()
    for review in catalog.read_jsonl(catalog.REVIEWS_FILE):
        review_id = str(review.get("review_id", ""))
        review_ids[review_id] += 1
        repo_id = str(review.get("repo_id", ""))
        pattern_id = str(review.get("pattern_id", ""))
        commit_sha = str(review.get("commit_sha", ""))
        if not review_id:
            error(errors, review, "review_id is required")
        if repo_id not in known_repos:
            error(errors, review, f"review references unknown repo_id: {repo_id!r}")
        if pattern_id not in patterns:
            error(errors, review, f"review references unknown pattern_id: {pattern_id!r}")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
            error(errors, review, "review commit_sha must contain exactly 40 hexadecimal characters")
        evidence = review.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            error(errors, review, "review evidence must be a non-empty array")
        elif any(not isinstance(item, dict) or not item.get("claim") or not item.get("kind") or not item.get("path") for item in evidence):
            error(errors, review, "each review evidence item requires claim, kind, and path")
        else:
            valid_review_repos.add(repo_id)
    for review_id, count in review_ids.items():
        if count > 1:
            errors.append(f"duplicate review_id {review_id!r}: {count} records")
    for project in projects:
        if str(project.get("curation", {}).get("tier", "")).upper() == "A" and project.get("repo_id") not in valid_review_repos:
            error(errors, project, "Tier A requires at least one pinned review record")

    if not aliases:
        errors.append("term-map.json contains no aliases")

    try:
        metadata = catalog.load_catalog_metadata()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"catalog metadata load failed: {exc}")
        metadata = {}
    if metadata:
        project_counts = metadata.get("projects", {})
        expected_tiers = Counter(str(row.get("curation", {}).get("tier", "C")).upper() for row in projects)
        expected = {
            "total": len(projects),
            "tier_a": expected_tiers.get("A", 0),
            "tier_b": expected_tiers.get("B", 0),
            "tier_c": expected_tiers.get("C", 0),
        }
        if project_counts != expected:
            errors.append(f"catalog metadata project counts differ: expected {expected}, got {project_counts}")
        if metadata.get("patterns") != len(patterns):
            errors.append(
                f"catalog metadata pattern count differs: expected {len(patterns)}, got {metadata.get('patterns')!r}"
            )
        if not isinstance(metadata.get("catalog_version"), str) or not metadata.get("catalog_version"):
            errors.append("catalog metadata requires catalog_version")
        if not catalog.parse_iso(metadata.get("generated_at")):
            errors.append("catalog metadata requires an ISO generated_at timestamp")

    result = {
        "valid": not errors,
        "projects": len(projects),
        "patterns": len(patterns),
        "metrics": len(metrics),
        "errors": errors,
        "warnings": warnings,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
