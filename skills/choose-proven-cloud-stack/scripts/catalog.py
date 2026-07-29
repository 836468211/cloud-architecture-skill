#!/usr/bin/env python3
"""Offline search and ranking for the Proven Cloud Stack catalog."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SKILL_DIR = Path(__file__).resolve().parent.parent
REFERENCE_DIR = SKILL_DIR / "references"
PROJECT_GLOB = "projects-*.jsonl"
METRICS_FILE = REFERENCE_DIR / "github-metrics.jsonl"
PATTERNS_FILE = REFERENCE_DIR / "patterns-core.jsonl"
TERM_MAP_FILE = REFERENCE_DIR / "term-map.json"
ROLE_SLOTS = {
    "direct": {"direct-dependency", "official-sdk", "official-implementation"},
    "mechanism": {"mechanism-reference", "reference-implementation"},
    "production": {"production-validation"},
    "integration": {"integration-adapter"},
    "benchmark": {"benchmark-testbed"},
    "contrast": {"contrast-only"},
}
DEFAULT_RELEVANCE_THRESHOLD = 60.0
DISCOVERY_RELEVANCE_THRESHOLD = 25.0
REQUIREMENT_LIST_FIELDS = (
    "domains",
    "operations",
    "problems",
    "required_mechanisms",
    "optional_mechanisms",
    "topologies",
    "runtimes",
    "languages",
    "roles",
    "exclude",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: record must be an object")
            value["_source_file"] = path.name
            value["_source_line"] = line_number
            records.append(value)
    return records


def load_catalog() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    projects: list[dict[str, Any]] = []
    for path in sorted(REFERENCE_DIR.glob(PROJECT_GLOB)):
        projects.extend(read_jsonl(path))
    metrics = {row["repo_id"]: row for row in read_jsonl(METRICS_FILE) if row.get("repo_id")}
    patterns = {row["pattern_id"]: row for row in read_jsonl(PATTERNS_FILE) if row.get("pattern_id")}
    return projects, metrics, patterns


def load_term_map() -> tuple[dict[str, str], dict[str, list[str]]]:
    if not TERM_MAP_FILE.exists():
        return {}, {}
    data = json.loads(TERM_MAP_FILE.read_text(encoding="utf-8"))
    aliases: dict[str, str] = {}
    search_terms: dict[str, list[str]] = {}
    for term in data.get("terms", []):
        term_id = str(term["id"]).lower()
        aliases[term_id] = term_id
        aliases[term_id.replace("-", " ")] = term_id
        for alias in term.get("aliases", []):
            aliases[str(alias).strip().lower()] = term_id
        search_terms[term_id] = [str(item) for item in term.get("code_search_terms", [])]
    return aliases, search_terms


def tokenize(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9][a-z0-9+.#_-]*|[\u3400-\u9fff]+", lowered))
    tokens.update(part for part in re.split(r"[\s,;/|]+", lowered) if part)
    return tokens


def normalize_values(values: Iterable[str], aliases: dict[str, str]) -> set[str]:
    normalized: set[str] = set()
    for raw in values:
        value = str(raw).strip().lower()
        if not value:
            continue
        normalized.add(aliases.get(value, value.replace("_", "-")))
    return normalized


def normalized_text_terms(text: str, aliases: dict[str, str]) -> set[str]:
    terms = tokenize(text)
    lowered = text.lower()
    for alias, canonical in aliases.items():
        if alias in lowered:
            terms.add(canonical)
    return normalize_values(terms, aliases)


def project_dimensions(project: dict[str, Any]) -> dict[str, set[str]]:
    pattern_links = project.get("pattern_links", [])
    return {
        "domains": set(project.get("domains", [])) | {project.get("primary_domain", "")},
        "operations": set(project.get("operations", [])),
        "problems": set(project.get("problems", [])),
        "mechanisms": set(project.get("mechanisms", [])),
        "topologies": set(project.get("topologies", [])),
        "runtimes": set(project.get("runtimes", [])),
        "languages": {str(item).lower() for item in project.get("languages", [])},
        "roles": {role for link in pattern_links for role in link.get("roles", [])},
        "patterns": {link.get("pattern_id", "") for link in pattern_links},
        "limitations": set(project.get("limitations", [])),
        "protocols": set(project.get("protocols", [])),
    }


def flatten_text(project: dict[str, Any]) -> str:
    dimensions = project_dimensions(project)
    parts = [
        project.get("repo_id", ""),
        project.get("name", ""),
        project.get("summary", ""),
        " ".join(project.get("aliases", [])),
    ]
    for values in dimensions.values():
        parts.append(" ".join(str(value) for value in values))
    return " ".join(parts).lower()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def percentile(value: float, values: list[float]) -> float:
    if not values:
        return 50.0
    below = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    return 100.0 * (below + 0.5 * equal) / len(values)


def overlap_score(requested: set[str], available: set[str]) -> float | None:
    if not requested:
        return None
    return 100.0 * len(requested & available) / len(requested)


def validate_requirements(requirements: dict[str, Any]) -> None:
    if not isinstance(requirements, dict):
        raise ValueError("requirements must be a JSON object")
    if "objective" in requirements and not isinstance(requirements["objective"], str):
        raise ValueError("requirements.objective must be a string")
    for field in REQUIREMENT_LIST_FIELDS:
        value = requirements.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"requirements.{field} must be an array of strings")
    for field in ("constraints", "scale", "weights"):
        value = requirements.get(field, {})
        if not isinstance(value, dict):
            raise ValueError(f"requirements.{field} must be an object")
    constraints = requirements.get("constraints", {})
    for field in ("licenses_forbidden", "licenses_preferred"):
        value = constraints.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"requirements.constraints.{field} must be an array of strings")


def requirements_sets(requirements: dict[str, Any], aliases: dict[str, str]) -> dict[str, set[str]]:
    mapping = {
        "domains": requirements.get("domains", []),
        "operations": requirements.get("operations", []),
        "problems": requirements.get("problems", []),
        "mechanisms": requirements.get("required_mechanisms", []),
        "optional_mechanisms": requirements.get("optional_mechanisms", []),
        "topologies": requirements.get("topologies", []),
        "runtimes": requirements.get("runtimes", []),
        "languages": requirements.get("languages", []),
        "roles": requirements.get("roles", []),
        "exclude": requirements.get("exclude", []),
    }
    return {key: normalize_values(value, aliases) for key, value in mapping.items()}


def compatibility(project: dict[str, Any], metric: dict[str, Any], req: dict[str, set[str]], raw: dict[str, Any]) -> tuple[bool, list[str]]:
    dims = project_dimensions(project)
    reasons: list[str] = []
    if req["operations"] and dims["operations"] and not (req["operations"] & dims["operations"]):
        reasons.append("operation-conflict")
    excludable = set().union(*dims.values())
    if req["exclude"] & excludable:
        reasons.append("explicit-exclusion")

    constraints = raw.get("constraints", {})
    forbidden = {str(item).lower() for item in constraints.get("licenses_forbidden", [])}
    license_id = str(metric.get("license_spdx") or project.get("license_spdx") or "").lower()
    if forbidden and license_id and license_id in forbidden:
        reasons.append("forbidden-license")
    return not reasons, reasons


def role_compatibility(project: dict[str, Any], metric: dict[str, Any], req: dict[str, set[str]], slot: str) -> tuple[bool, list[str]]:
    """Apply hard constraints that are valid only for a repository evidence role."""
    if slot != "direct":
        return True, []
    dims = project_dimensions(project)
    reasons: list[str] = []
    if req["topologies"] and dims["topologies"] and not (req["topologies"] & dims["topologies"]):
        reasons.append("topology-conflict-for-direct-use")
    if req["runtimes"] and dims["runtimes"] and not (req["runtimes"] & dims["runtimes"]):
        reasons.append("runtime-conflict-for-direct-use")
    if req["languages"] and dims["languages"] and not (req["languages"] & dims["languages"]):
        reasons.append("language-conflict-for-direct-use")
    if metric.get("archived") is True:
        reasons.append("archived-direct-dependency")
    return not reasons, reasons


def relevance_score(project: dict[str, Any], req: dict[str, set[str]], text_terms: set[str], slot: str | None = None) -> tuple[float, dict[str, float]]:
    dims = project_dimensions(project)
    weighted: list[tuple[float, float, str]] = []
    scores: dict[str, float] = {}
    slot_weights = {
        "direct": {"problems": 20.0, "mechanisms": 25.0, "topologies": 20.0, "runtimes": 25.0, "roles": 5.0, "optional": 5.0},
        "mechanism": {"problems": 25.0, "mechanisms": 45.0, "topologies": 5.0, "runtimes": 5.0, "roles": 10.0, "optional": 10.0},
        "production": {"problems": 30.0, "mechanisms": 25.0, "topologies": 20.0, "runtimes": 5.0, "roles": 15.0, "optional": 5.0},
        "integration": {"problems": 20.0, "mechanisms": 20.0, "topologies": 20.0, "runtimes": 20.0, "roles": 10.0, "optional": 10.0},
        "benchmark": {"problems": 25.0, "mechanisms": 25.0, "topologies": 20.0, "runtimes": 15.0, "roles": 10.0, "optional": 5.0},
        "contrast": {"problems": 25.0, "mechanisms": 30.0, "topologies": 15.0, "runtimes": 10.0, "roles": 15.0, "optional": 5.0},
    }
    weights_for_slot = slot_weights.get(slot or "", {})
    mapping = [
        ("problems", req["problems"], dims["problems"], weights_for_slot.get("problems", 25.0)),
        ("mechanisms", req["mechanisms"], dims["mechanisms"], weights_for_slot.get("mechanisms", 30.0)),
        ("topologies", req["topologies"], dims["topologies"], weights_for_slot.get("topologies", 15.0)),
        ("runtimes", req["runtimes"] | req["languages"], dims["runtimes"] | dims["languages"], weights_for_slot.get("runtimes", 15.0)),
        ("roles", req["roles"], dims["roles"], weights_for_slot.get("roles", 10.0)),
        ("optional", req["optional_mechanisms"], dims["mechanisms"], weights_for_slot.get("optional", 5.0)),
    ]
    for name, requested, available, weight in mapping:
        value = overlap_score(requested, available)
        if value is not None:
            scores[name] = round(value, 2)
            weighted.append((value, weight, name))

    if text_terms:
        corpus = tokenize(flatten_text(project))
        text_value = 100.0 * len(text_terms & corpus) / len(text_terms)
        scores["text"] = round(text_value, 2)
        weighted.append((text_value, 5.0, "text"))

    if req["domains"]:
        domain_value = overlap_score(req["domains"], dims["domains"]) or 0.0
        scores["domains"] = round(domain_value, 2)
        weighted.append((domain_value, 10.0, "domains"))

    if not weighted:
        return 0.0, scores
    result = sum(value * weight for value, weight, _ in weighted) / sum(weight for _, weight, _ in weighted)
    mechanism_coverage = scores.get("mechanisms")
    if mechanism_coverage is not None and mechanism_coverage < 50.0:
        result = min(result, 59.0)
    return round(result, 2), scores


def pattern_role_pairs(project: dict[str, Any], roles: set[str]) -> set[tuple[str, str]]:
    return {
        (str(link.get("pattern_id", "")), str(role))
        for link in project.get("pattern_links", [])
        for role in link.get("roles", [])
        if link.get("pattern_id") and (not roles or role in roles)
    }


def peer_stars(project: dict[str, Any], projects: list[dict[str, Any]], metrics: dict[str, dict[str, Any]], evaluated_roles: set[str]) -> tuple[list[float], str]:
    dims = project_dimensions(project)
    roles = dims["roles"] & evaluated_roles if evaluated_roles else dims["roles"]
    pairs = pattern_role_pairs(project, roles)
    peers: list[float] = []
    for candidate in projects:
        if pairs and not (pairs & pattern_role_pairs(candidate, roles)):
            continue
        stars = metrics.get(candidate.get("repo_id", ""), {}).get("stars")
        if isinstance(stars, int) and stars >= 0:
            peers.append(math.log10(stars + 1))
    if len(peers) >= 3:
        return peers, "pattern+role"

    cohort = project.get("cohort_id")
    peers = []
    for candidate in projects:
        if cohort and candidate.get("cohort_id") != cohort:
            continue
        if not cohort and candidate.get("primary_domain") != project.get("primary_domain"):
            continue
        candidate_roles = project_dimensions(candidate)["roles"]
        if roles and not (roles & candidate_roles):
            continue
        stars = metrics.get(candidate.get("repo_id", ""), {}).get("stars")
        if isinstance(stars, int) and stars >= 0:
            peers.append(math.log10(stars + 1))
    suffix = "+role" if roles else ""
    return peers, ("cohort" if cohort else "primary-domain") + suffix


def maturity_score(project: dict[str, Any], metric: dict[str, Any], projects: list[dict[str, Any]], metrics: dict[str, dict[str, Any]], patterns: dict[str, dict[str, Any]], evaluated_roles: set[str]) -> tuple[float, str, str, dict[str, float]]:
    components: dict[str, float] = {}
    weights: dict[str, float] = {}
    stars = metric.get("stars")
    peer_group = "unavailable"
    if isinstance(stars, int) and stars >= 0:
        peers, peer_group = peer_stars(project, projects, metrics, evaluated_roles)
        star_value = math.log10(stars + 1)
        components["peer_stars"] = round(percentile(star_value, peers), 2)
        weights["peer_stars"] = 35.0

    pushed = parse_iso(metric.get("pushed_at"))
    if pushed:
        age_days = max(0, (datetime.now(timezone.utc) - pushed).days)
        maintenance = max(0.0, 100.0 - min(age_days, 730) * 100.0 / 730.0)
        if metric.get("archived") is True:
            maintenance = min(maintenance, 20.0)
        components["maintenance"] = round(maintenance, 2)
        weights["maintenance"] = 20.0

    forks = metric.get("forks")
    if isinstance(forks, int) and forks >= 0:
        components["forks"] = round(min(100.0, 20.0 * math.log10(forks + 1)), 2)
        weights["forks"] = 10.0

    tier = str(project.get("curation", {}).get("tier", "C")).upper()
    tier_value = {"A": 100.0, "B": 75.0, "C": 40.0}.get(tier, 25.0)
    components["curation"] = tier_value
    weights["curation"] = 15.0

    linked_patterns = {pattern_id for pattern_id, _ in pattern_role_pairs(project, evaluated_roles)}
    if not linked_patterns:
        linked_patterns = project_dimensions(project)["patterns"]
    validation_counts = []
    candidate_owner = str(project.get("repo_id", "")).removeprefix("github:").split("/", 1)[0].lower()
    for pattern_id in linked_patterns:
        if pattern_id not in patterns:
            continue
        owners = {
            repo_id.removeprefix("github:").split("/", 1)[0].lower()
            for repo_id in patterns[pattern_id].get("validated_by", [])
            if "/" in repo_id
        }
        owners.discard(candidate_owner)
        validation_counts.append(len(owners))
    if validation_counts:
        components["independent_validation"] = min(100.0, max(validation_counts) * 20.0)
        weights["independent_validation"] = 20.0

    # Keep unknown evidence unscored. Renormalizing only the present fields would
    # make a discovery record with no GitHub facts appear deceptively mature.
    maturity = sum(components[key] * weights[key] for key in weights) / 100.0 if weights else 0.0
    fetched = parse_iso(metric.get("fetched_at"))
    stale = fetched is None or (datetime.now(timezone.utc) - fetched).days > 30
    if tier == "A" and not stale:
        confidence = "high"
    elif tier in {"A", "B"} and metric.get("status") == "ok" and not stale:
        confidence = "medium"
    else:
        confidence = "low"
    return round(maturity, 2), confidence, peer_group, components


def recommend(requirements: dict[str, Any], limit: int) -> dict[str, Any]:
    validate_requirements(requirements)
    projects, metrics, patterns = load_catalog()
    aliases, _ = load_term_map()
    req = requirements_sets(requirements, aliases)
    text_terms = normalized_text_terms(str(requirements.get("objective", "")), aliases)
    results: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    role_rejected: Counter[str] = Counter()
    for project in projects:
        metric = metrics.get(project.get("repo_id", ""), {})
        compatible, reasons = compatibility(project, metric, req, requirements)
        if not compatible:
            rejected.update(reasons)
            continue
        dims = project_dimensions(project)
        requested_roles = req["roles"]
        role_scores: dict[str, float] = {}
        role_matches: dict[str, dict[str, float]] = {}
        for slot, slot_roles in ROLE_SLOTS.items():
            if not (dims["roles"] & slot_roles):
                continue
            if requested_roles and not (requested_roles & slot_roles):
                continue
            slot_compatible, slot_reasons = role_compatibility(project, metric, req, slot)
            if not slot_compatible:
                role_rejected.update(slot_reasons)
                continue
            slot_score, slot_match = relevance_score(project, req, text_terms, slot)
            role_scores[slot] = slot_score
            role_matches[slot] = slot_match
        if not role_scores:
            continue

        role_maturity: dict[str, dict[str, Any]] = {}
        for slot in role_scores:
            evaluated_roles = dims["roles"] & ROLE_SLOTS[slot]
            slot_maturity, slot_confidence, slot_peer_group, slot_components = maturity_score(
                project, metric, projects, metrics, patterns, evaluated_roles
            )
            role_maturity[slot] = {
                "maturity": slot_maturity,
                "confidence": slot_confidence,
                "peer_group": slot_peer_group,
                "maturity_breakdown": slot_components,
            }
        if role_scores:
            recommended_slot = sorted(role_scores, key=lambda name: (-role_scores[name], name))[0]
            relevance = role_scores[recommended_slot]
            matches = role_matches[recommended_slot]
            selected_maturity = role_maturity[recommended_slot]
            maturity = selected_maturity["maturity"]
            confidence = selected_maturity["confidence"]
            peer_group = selected_maturity["peer_group"]
            maturity_components = selected_maturity["maturity_breakdown"]
        else:
            recommended_slot = None
            relevance, matches = relevance_score(project, req, text_terms)
            maturity, confidence, peer_group, maturity_components = maturity_score(
                project, metric, projects, metrics, patterns, req["roles"] or dims["roles"]
            )
        if relevance < DISCOVERY_RELEVANCE_THRESHOLD:
            continue
        confidence_factor = {"high": 1.0, "medium": 0.9, "low": 0.75}[confidence]
        priority = math.sqrt(max(relevance, 0.0) * max(maturity, 0.0)) * confidence_factor
        results.append(
            {
                "repo_id": project.get("repo_id"),
                "url": project.get("url"),
                "summary": project.get("summary"),
                "tier": project.get("curation", {}).get("tier", "C"),
                "roles": sorted(dims["roles"]),
                "patterns": sorted(dims["patterns"]),
                "relevance": relevance,
                "maturity": maturity,
                "confidence": confidence,
                "review_priority": round(priority, 2),
                "default_eligible": relevance >= DEFAULT_RELEVANCE_THRESHOLD,
                "recommended_slot": recommended_slot,
                "role_relevance": role_scores,
                "role_maturity": role_maturity,
                "match_breakdown": matches,
                "maturity_breakdown": maturity_components,
                "peer_group": peer_group,
                "github": {
                    "stars": metric.get("stars"),
                    "forks": metric.get("forks"),
                    "archived": metric.get("archived"),
                    "pushed_at": metric.get("pushed_at"),
                    "license_spdx": metric.get("license_spdx"),
                    "metrics_checked_at": metric.get("fetched_at"),
                    "status": metric.get("status", "missing"),
                },
                "limitations": project.get("limitations", []),
            }
        )
    results.sort(key=lambda row: (-row["review_priority"], -row["relevance"], row["repo_id"] or ""))
    role_shortlists: dict[str, list[dict[str, Any]]] = {}
    for slot in ROLE_SLOTS:
        slot_rows = [row for row in results if slot in row.get("role_relevance", {})]
        def slot_priority(row: dict[str, Any]) -> float:
            evidence = row["role_maturity"][slot]
            factor = {"high": 1.0, "medium": 0.9, "low": 0.75}[evidence["confidence"]]
            return math.sqrt(
                max(row["role_relevance"][slot], 0.0) * max(evidence["maturity"], 0.0)
            ) * factor

        slot_rows.sort(
            key=lambda row: (
                -slot_priority(row),
                -row["role_relevance"][slot],
                row["repo_id"] or "",
            )
        )
        if slot_rows:
            role_shortlists[slot] = [
                {
                    "repo_id": row["repo_id"],
                    "url": row["url"],
                    "role_relevance": row["role_relevance"][slot],
                    "default_eligible": row["role_relevance"][slot] >= DEFAULT_RELEVANCE_THRESHOLD,
                    "maturity": row["role_maturity"][slot]["maturity"],
                    "confidence": row["role_maturity"][slot]["confidence"],
                    "peer_group": row["role_maturity"][slot]["peer_group"],
                    "review_priority": round(slot_priority(row), 2),
                    "stars": row["github"]["stars"],
                    "tier": row["tier"],
                }
                for row in slot_rows[:3]
            ]
    return {
        "schema_version": "1.0",
        "requirements": requirements,
        "catalog_projects": len(projects),
        "compatible_candidates": len(results),
        "hard_rejections": dict(sorted(rejected.items())),
        "role_rejections": dict(sorted(role_rejected.items())),
        "selection_policy": {
            "default_min_relevance": DEFAULT_RELEVANCE_THRESHOLD,
            "discovery_min_relevance": DISCOVERY_RELEVANCE_THRESHOLD,
            "default_shortlist_ids": [
                row["repo_id"] for row in results if row["default_eligible"]
            ][:limit],
        },
        "role_shortlists": role_shortlists,
        "results": results[:limit],
    }


def search(text: str, limit: int, min_stars: int, domains: list[str]) -> dict[str, Any]:
    projects, metrics, _ = load_catalog()
    aliases, _ = load_term_map()
    terms = normalized_text_terms(text, aliases)
    requested_domains = normalize_values(domains, aliases)
    rows: list[dict[str, Any]] = []
    for project in projects:
        dims = project_dimensions(project)
        if requested_domains and not (requested_domains & dims["domains"]):
            continue
        metric = metrics.get(project.get("repo_id", ""), {})
        stars = metric.get("stars")
        if min_stars and (not isinstance(stars, int) or stars < min_stars):
            continue
        corpus = normalized_text_terms(flatten_text(project), aliases)
        matched = terms & corpus
        if terms and not matched:
            continue
        rows.append(
            {
                "repo_id": project.get("repo_id"),
                "url": project.get("url"),
                "tier": project.get("curation", {}).get("tier", "C"),
                "stars": stars,
                "matched_terms": sorted(matched),
                "summary": project.get("summary"),
            }
        )
    rows.sort(key=lambda row: (-len(row["matched_terms"]), -(row["stars"] or -1), row["repo_id"] or ""))
    return {"query": text, "count": len(rows), "results": rows[:limit]}


def stats() -> dict[str, Any]:
    projects, metrics, patterns = load_catalog()
    tiers = Counter(str(row.get("curation", {}).get("tier", "C")).upper() for row in projects)
    domains = Counter(row.get("primary_domain", "unknown") for row in projects)
    now = datetime.now(timezone.utc)
    fresh = stale = missing = 0
    for project in projects:
        metric = metrics.get(project.get("repo_id", ""))
        fetched = parse_iso(metric.get("fetched_at") if metric else None)
        if not metric or fetched is None:
            missing += 1
        elif (now - fetched).days > 30:
            stale += 1
        else:
            fresh += 1
    return {
        "schema_version": "1.0",
        "projects": len(projects),
        "patterns": len(patterns),
        "tiers": dict(sorted(tiers.items())),
        "primary_domains": dict(sorted(domains.items())),
        "metrics": {"fresh": fresh, "stale": stale, "missing": missing},
    }


def load_requirements(source: str) -> dict[str, Any]:
    """Load a requirement fingerprint from a UTF-8 file or standard input."""
    if source == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(source).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("requirements must be a JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    recommend_parser = subparsers.add_parser("recommend", help="rank repositories for a requirement fingerprint")
    recommend_parser.add_argument(
        "--requirements",
        required=True,
        help="UTF-8 JSON file, or '-' to read the requirement fingerprint from stdin",
    )
    recommend_parser.add_argument("--limit", type=int, default=12)

    search_parser = subparsers.add_parser("search", help="search catalog text and tags")
    search_parser.add_argument("--text", default="")
    search_parser.add_argument("--domain", action="append", default=[])
    search_parser.add_argument("--min-stars", type=int, default=0)
    search_parser.add_argument("--limit", type=int, default=20)

    subparsers.add_parser("stats", help="show catalog coverage")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "recommend":
            requirements = load_requirements(args.requirements)
            output = recommend(requirements, max(1, args.limit))
        elif args.command == "search":
            output = search(args.text, max(1, args.limit), max(0, args.min_stars), args.domain)
        else:
            output = stats()
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
