#!/usr/bin/env python3
"""Fetch cached GitHub discovery searches and build a deterministic Tier C catalog.

Fetching and building are deliberately separate operations.  ``--fetch`` writes one
cache file per configured query, while ``--build`` consumes only a complete cache.
This makes the catalog build reproducible and fully testable without network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = REPO_ROOT / "skills" / "choose-proven-cloud-stack" / "references"
PROFILES_PATH = REFERENCE_DIR / "discovery-profiles.json"
PATTERNS_PATH = REFERENCE_DIR / "patterns-core.jsonl"
METRICS_PATH = REFERENCE_DIR / "github-metrics.jsonl"
EXPANDED_PATH = REFERENCE_DIR / "projects-expanded.jsonl"
MANIFEST_PATH = REFERENCE_DIR / "discovery-manifest.json"
METADATA_PATH = REFERENCE_DIR / "catalog-metadata.json"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "github-discovery"

DEFAULT_TARGET_TOTAL = 1_000
DEFAULT_OWNER_CAP = 20
DEFAULT_MIN_SIZE_KB = 32
DEFAULT_MAX_QUERIES = 10
SCHEMA_VERSION = "1.0"
CATALOG_VERSION = "1.0.0"
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
TOPIC_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$")
SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

LIST_OR_TUTORIAL_TOPICS = {
    "awesome",
    "awesome-list",
    "course",
    "courses",
    "curated-list",
    "examples",
    "interview",
    "learning-resources",
    "roadmap",
    "tutorial",
    "tutorials",
}
LIST_OR_TUTORIAL_NAME_RE = re.compile(
    r"(?:^awesome(?:[-_.]|$)|(?:^|[-_.])tutorials?(?:[-_.]|$)|"
    r"(?:^|[-_.])curated[-_.]?list(?:[-_.]|$)|^roadmap(?:[-_.]|$))",
    re.IGNORECASE,
)
LIST_OR_TUTORIAL_DESCRIPTION_RE = re.compile(
    r"\b(?:a|the|an)?\s*(?:curated|comprehensive)?\s*list of\b|"
    r"\bstep[- ]by[- ]step tutorial\b|\blearning resources\b",
    re.IGNORECASE,
)


class IncompleteCacheError(ValueError):
    """Raised when any planned query lacks a complete cached response."""


class IdentityConflictError(ValueError):
    """Raised when a slug or GitHub node ID is attached to another identity."""


class InsufficientCandidatesError(ValueError):
    """Raised rather than silently publishing below the requested catalog size."""


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def _json_bytes(value: Any, *, pretty: bool) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n"
    return text.encode("utf-8")


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Replace *path* only after the complete new file is safely closed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, _json_bytes(value, pretty=True))


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    content = b"".join(_json_bytes(row, pretty=False) for row in rows)
    atomic_write_bytes(path, content)


def _clean_string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a non-empty array of strings")
    cleaned = sorted({item.strip() for item in value if item.strip()})
    if not cleaned:
        raise ValueError(f"{field} must not be empty")
    return cleaned


def _clean_ordered_string_list(value: object, field: str) -> list[str]:
    _clean_string_list(value, field)
    ordered: list[str] = []
    seen: set[str] = set()
    for item in value:  # type: ignore[union-attr]
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            ordered.append(cleaned)
            seen.add(cleaned)
    return ordered


def load_configuration(
    profiles_path: Path = PROFILES_PATH,
    patterns_path: Path = PATTERNS_PATH,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    config = read_json(profiles_path)
    defaults = config.get("defaults", {})
    profiles = config.get("profiles")
    if not isinstance(defaults, dict) or not isinstance(profiles, list) or not profiles:
        raise ValueError(f"{profiles_path}: defaults must be an object and profiles a non-empty array")

    pattern_rows = read_jsonl(patterns_path)
    patterns: dict[str, dict[str, Any]] = {}
    pattern_order: list[str] = []
    for pattern in pattern_rows:
        pattern_id = pattern.get("pattern_id")
        if not isinstance(pattern_id, str) or not pattern_id or pattern_id in patterns:
            raise ValueError(f"{patterns_path}: invalid or duplicate pattern_id {pattern_id!r}")
        for field in ("domains", "operations", "solves", "topologies", "required_mechanisms"):
            pattern[field] = _clean_ordered_string_list(
                pattern.get(field), f"pattern {pattern_id}.{field}"
            )
        patterns[pattern_id] = pattern
        pattern_order.append(pattern_id)

    normalized_profiles: list[dict[str, Any]] = []
    profile_ids: set[str] = set()
    configured_patterns: set[str] = set()
    for raw in profiles:
        if not isinstance(raw, dict):
            raise ValueError(f"{profiles_path}: every profile must be an object")
        profile = dict(raw)
        profile_id = profile.get("profile_id")
        pattern_id = profile.get("pattern_id")
        if not isinstance(profile_id, str) or not profile_id or profile_id in profile_ids:
            raise ValueError(f"{profiles_path}: invalid or duplicate profile_id {profile_id!r}")
        if pattern_id not in patterns:
            raise ValueError(f"{profiles_path}: profile {profile_id!r} has unknown pattern {pattern_id!r}")
        profile["topics"] = _clean_string_list(profile.get("topics"), f"{profile_id}.topics")
        invalid_topics = [topic for topic in profile["topics"] if not TOPIC_RE.fullmatch(topic)]
        if invalid_topics:
            raise ValueError(
                f"{profiles_path}: profile {profile_id!r} has invalid GitHub topics {invalid_topics}"
            )
        profile["runtimes"] = _clean_string_list(profile.get("runtimes"), f"{profile_id}.runtimes")
        profile["protocols"] = _clean_string_list(profile.get("protocols"), f"{profile_id}.protocols")
        roles = profile.get("roles", defaults.get("roles", ["mechanism-reference"]))
        profile["roles"] = _clean_string_list(roles, f"{profile_id}.roles")
        invalid_roles = set(profile["roles"]) - VALID_ROLES
        if invalid_roles:
            raise ValueError(
                f"{profiles_path}: profile {profile_id!r} has invalid roles {sorted(invalid_roles)}"
            )
        cohort_id = profile.get("cohort_id")
        if not isinstance(cohort_id, str) or not cohort_id.strip():
            raise ValueError(f"{profiles_path}: profile {profile_id!r} requires cohort_id")
        profile_ids.add(profile_id)
        configured_patterns.add(str(pattern_id))
        normalized_profiles.append(profile)

    missing_patterns = set(patterns) - configured_patterns
    if missing_patterns:
        raise ValueError(
            f"{profiles_path}: discovery profiles do not cover patterns {sorted(missing_patterns)}"
        )
    return defaults, normalized_profiles, patterns, pattern_order


def make_query_plan(
    defaults: dict[str, Any], profiles: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    min_stars = int(defaults.get("min_stars", 20))
    per_page = min(100, max(1, int(defaults.get("per_page", 75))))
    plan: list[dict[str, Any]] = []
    for profile in profiles:
        profile_min_stars = max(0, int(profile.get("min_stars", min_stars)))
        for topic in profile["topics"]:
            query = (
                f"topic:{topic} stars:>={profile_min_stars} "
                "is:public archived:false mirror:false template:false"
            )
            identity = "\0".join(
                [
                    str(profile["profile_id"]),
                    str(profile["pattern_id"]),
                    topic,
                    query,
                    str(per_page),
                ]
            )
            query_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            plan.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "profile_id": profile["profile_id"],
                    "pattern_id": profile["pattern_id"],
                    "topic": topic,
                    "min_stars": profile_min_stars,
                    "per_page": per_page,
                }
            )
    plan.sort(key=lambda row: (row["pattern_id"], row["profile_id"], row["topic"], row["query_id"]))
    for index, query in enumerate(plan, 1):
        query["ordinal"] = index
    return plan


def cache_path(cache_dir: Path, query: dict[str, Any]) -> Path:
    return cache_dir / f"{int(query['ordinal']):03d}-{query['query_id']}.json"


def request_search(
    query: str,
    per_page: int,
    token: str | None,
    timeout: int = 30,
) -> dict[str, Any]:
    parameters = urllib.parse.urlencode(
        {"q": query, "sort": "stars", "order": "desc", "per_page": per_page, "page": 1}
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "choose-proven-cloud-stack-discovery-builder",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com/search/repositories?{parameters}", headers=headers, method="GET"
    )
    with urllib.request.urlopen(request, timeout=max(5, timeout)) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("GitHub Search API returned a non-object response")
    return payload


def _validate_search_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("GitHub Search API response has invalid items")
    return items


def fetch_queries(
    plan: Sequence[dict[str, Any]],
    cache_dir: Path,
    max_queries: int,
    *,
    token: str | None = None,
    timeout: int = 30,
    requester: Callable[[str, int, str | None, int], dict[str, Any]] = request_search,
) -> dict[str, Any]:
    """Fetch at most *max_queries* absent/incomplete entries and cache each atomically."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    limit = max(0, max_queries)
    attempted = fetched = complete = 0
    for query in plan:
        path = cache_path(cache_dir, query)
        if path.exists():
            try:
                cached = read_json(path)
                _validate_search_payload(cached)
                _parse_utc_timestamp(cached.get("fetched_at"), "cached fetched_at")
                if (
                    cached.get("query_id") == query["query_id"]
                    and cached.get("query") == query["query"]
                    and cached.get("profile_id") == query["profile_id"]
                    and cached.get("pattern_id") == query["pattern_id"]
                    and cached.get("topic") == query["topic"]
                    and cached.get("min_stars") == query["min_stars"]
                    and cached.get("per_page") == query["per_page"]
                    and cached.get("complete") is True
                    and cached.get("incomplete_results") is False
                ):
                    complete += 1
                    continue
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        if attempted >= limit:
            continue
        attempted += 1
        payload = requester(query["query"], int(query["per_page"]), token, timeout)
        items = _validate_search_payload(payload)
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        incomplete = payload.get("incomplete_results") is not False
        record = {
            "schema_version": SCHEMA_VERSION,
            "query_id": query["query_id"],
            "query": query["query"],
            "profile_id": query["profile_id"],
            "pattern_id": query["pattern_id"],
            "topic": query["topic"],
            "min_stars": query["min_stars"],
            "per_page": query["per_page"],
            "fetched_at": fetched_at,
            "complete": not incomplete,
            "incomplete_results": incomplete,
            "total_count": payload.get("total_count"),
            "items": items,
        }
        atomic_write_json(path, record)
        fetched += 1
        if not incomplete:
            complete += 1
    return {
        "planned": len(plan),
        "attempted": attempted,
        "fetched": fetched,
        "complete": complete,
        "remaining": len(plan) - complete,
        "cache_dir": str(cache_dir),
    }


def load_complete_cache(
    plan: Sequence[dict[str, Any]], cache_dir: Path
) -> list[dict[str, Any]]:
    cached_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    incomplete: list[str] = []
    invalid: list[str] = []
    for query in plan:
        path = cache_path(cache_dir, query)
        if not path.exists():
            missing.append(query["query_id"])
            continue
        try:
            cached = read_json(path)
            _validate_search_payload(cached)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            invalid.append(f"{query['query_id']} ({exc})")
            continue
        expected = {
            "query_id": query["query_id"],
            "query": query["query"],
            "profile_id": query["profile_id"],
            "pattern_id": query["pattern_id"],
            "topic": query["topic"],
            "min_stars": query["min_stars"],
            "per_page": query["per_page"],
        }
        if any(cached.get(field) != value for field, value in expected.items()):
            invalid.append(f"{query['query_id']} (query metadata mismatch)")
            continue
        if cached.get("complete") is not True or cached.get("incomplete_results") is not False:
            incomplete.append(query["query_id"])
            continue
        try:
            _parse_utc_timestamp(cached.get("fetched_at"), "cached fetched_at")
        except ValueError as exc:
            invalid.append(f"{query['query_id']} ({exc})")
            continue
        cached_rows.append(cached)
    if missing or incomplete or invalid:
        details = []
        if missing:
            details.append(f"missing={len(missing)}")
        if incomplete:
            details.append(f"incomplete={len(incomplete)}")
        if invalid:
            details.append(f"invalid={len(invalid)}")
        raise IncompleteCacheError(
            "discovery cache is not buildable (" + ", ".join(details) + "); run --fetch again"
        )
    return cached_rows


def normalize_slug(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().strip("/")
    if not SLUG_RE.fullmatch(cleaned):
        return None
    return cleaned.casefold()


def _parse_utc_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def exclusion_reason(
    repository: dict[str, Any],
    min_stars: int = 20,
    min_size_kb: int = DEFAULT_MIN_SIZE_KB,
) -> str | None:
    """Return a stable machine-readable reason, or ``None`` when eligible."""
    if repository.get("fork") is True:
        return "fork"
    if repository.get("mirror_url"):
        return "mirror"
    if repository.get("is_template") is True:
        return "template"
    if repository.get("archived") is True:
        return "archived"
    if repository.get("disabled") is True:
        return "disabled"
    if repository.get("private") is True:
        return "private"
    for field in ("fork", "is_template", "archived", "disabled", "private"):
        if not isinstance(repository.get(field), bool):
            return "invalid-repository-flags"
    mirror_url = repository.get("mirror_url")
    if mirror_url is not None and not isinstance(mirror_url, str):
        return "invalid-repository-flags"
    if not isinstance(repository.get("node_id"), str) or not repository["node_id"].strip():
        return "missing-node-id"
    if normalize_slug(repository.get("full_name")) is None:
        return "invalid-slug"
    language = repository.get("language")
    if not isinstance(language, str) or not language.strip():
        return "missing-language"
    size = repository.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < min_size_kb:
        return "too-small"
    stars = repository.get("stargazers_count")
    if not isinstance(stars, int) or isinstance(stars, bool) or stars < min_stars:
        return "below-min-stars"
    topics = {
        str(topic).strip().casefold()
        for topic in repository.get("topics", [])
        if isinstance(topic, str) and topic.strip()
    }
    name = str(repository.get("name") or "")
    description = str(repository.get("description") or "")
    if (
        topics & LIST_OR_TUTORIAL_TOPICS
        or LIST_OR_TUTORIAL_NAME_RE.search(name)
        or LIST_OR_TUTORIAL_DESCRIPTION_RE.search(description)
    ):
        return "list-or-tutorial"
    return None


def is_eligible_repository(
    repository: dict[str, Any],
    min_stars: int = 20,
    min_size_kb: int = DEFAULT_MIN_SIZE_KB,
) -> bool:
    return exclusion_reason(repository, min_stars, min_size_kb) is None


def _provenance(cache: dict[str, Any]) -> dict[str, Any]:
    return {
        "query_id": cache["query_id"],
        "query": cache["query"],
        "profile_id": cache["profile_id"],
        "pattern_id": cache["pattern_id"],
        "topic": cache["topic"],
        "min_stars": cache.get("min_stars"),
        "fetched_at": cache.get("fetched_at"),
    }


def _provenance_key(value: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(value.get(field) or "") for field in (
        "pattern_id", "profile_id", "topic", "query_id", "query", "fetched_at"
    ))


def merge_search_results(cached_rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate search hits and retain every distinct query provenance."""
    by_node: dict[str, dict[str, Any]] = {}
    slug_to_node: dict[str, str] = {}
    for cache in sorted(cached_rows, key=lambda row: str(row.get("query_id", ""))):
        items = _validate_search_payload(cache)
        provenance = _provenance(cache)
        for item in items:
            node_id = item.get("node_id")
            slug = normalize_slug(item.get("full_name"))
            if not isinstance(node_id, str) or not node_id.strip() or slug is None:
                # Eligibility filtering accounts for these malformed hits.
                identity = f"invalid:{cache.get('query_id')}:{len(by_node)}:{len(slug_to_node)}"
                merged = dict(item)
                merged["_provenance"] = [provenance]
                by_node[identity] = merged
                continue
            prior_node = slug_to_node.get(slug)
            if prior_node is not None and prior_node != node_id:
                raise IdentityConflictError(
                    f"GitHub slug {item.get('full_name')!r} appeared with node IDs "
                    f"{prior_node!r} and {node_id!r}"
                )
            slug_to_node[slug] = node_id
            current = by_node.get(node_id)
            if current is None:
                current = dict(item)
                current["_provenance"] = []
                by_node[node_id] = current
            elif normalize_slug(current.get("full_name")) != slug:
                raise IdentityConflictError(
                    f"GitHub node ID {node_id!r} appeared as both "
                    f"{current.get('full_name')!r} and {item.get('full_name')!r}"
                )
            current["_provenance"].append(provenance)
            # Prefer the payload from the lexicographically latest cached timestamp.
            current_stamp = str(current.get("_payload_fetched_at") or "")
            incoming_stamp = str(cache.get("fetched_at") or "")
            if incoming_stamp > current_stamp:
                saved_provenance = current["_provenance"]
                current.clear()
                current.update(item)
                current["_provenance"] = saved_provenance
                current["_payload_fetched_at"] = incoming_stamp

    merged_rows: list[dict[str, Any]] = []
    for candidate in by_node.values():
        unique = {_provenance_key(row): row for row in candidate.get("_provenance", [])}
        candidate["_provenance"] = [unique[key] for key in sorted(unique)]
        candidate["_pattern_ids"] = sorted(
            {str(row["pattern_id"]) for row in candidate["_provenance"]}
        )
        merged_rows.append(candidate)
    merged_rows.sort(key=lambda row: (normalize_slug(row.get("full_name")) or "", str(row.get("node_id") or "")))
    return merged_rows


def filter_candidates(
    candidates: Sequence[dict[str, Any]],
    *,
    default_min_stars: int = 20,
    min_size_kb: int = DEFAULT_MIN_SIZE_KB,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    accepted: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for candidate in candidates:
        thresholds: list[int] = []
        for provenance in candidate.get("_provenance", []):
            configured = provenance.get("min_stars")
            if isinstance(configured, int) and not isinstance(configured, bool) and configured >= 0:
                thresholds.append(configured)
                continue
            match = re.search(r"(?:^|\s)stars:>=(\d+)(?:\s|$)", str(provenance.get("query", "")))
            if match:
                thresholds.append(int(match.group(1)))
        min_stars = min(thresholds, default=default_min_stars)
        reason = exclusion_reason(candidate, min_stars=min_stars, min_size_kb=min_size_kb)
        if reason:
            rejected[reason] += 1
        else:
            accepted.append(candidate)
    accepted.sort(
        key=lambda row: (
            -int(row.get("stargazers_count", 0)),
            normalize_slug(row.get("full_name")) or "",
            str(row.get("node_id") or ""),
        )
    )
    return accepted, rejected


def balanced_select(
    candidates: Sequence[dict[str, Any]],
    pattern_ids: Sequence[str],
    limit: int,
    owner_cap: int = DEFAULT_OWNER_CAP,
) -> list[dict[str, Any]]:
    """Select in pattern rounds while limiting repositories from any one owner."""
    if limit <= 0:
        return []
    if owner_cap <= 0:
        raise ValueError("owner_cap must be positive")
    queues: dict[str, list[dict[str, Any]]] = {}
    for pattern_id in pattern_ids:
        queue = [row for row in candidates if pattern_id in row.get("_pattern_ids", [])]
        queue.sort(
            key=lambda row: (
                -int(row.get("stargazers_count", 0)),
                normalize_slug(row.get("full_name")) or "",
                str(row.get("node_id") or ""),
            )
        )
        queues[pattern_id] = queue

    offsets = defaultdict(int)
    owner_counts: Counter[str] = Counter()
    selected_nodes: set[str] = set()
    selected_slugs: set[str] = set()
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        progress = False
        for pattern_id in pattern_ids:
            queue = queues[pattern_id]
            while offsets[pattern_id] < len(queue):
                candidate = queue[offsets[pattern_id]]
                offsets[pattern_id] += 1
                node_id = str(candidate.get("node_id") or "")
                slug = normalize_slug(candidate.get("full_name")) or ""
                owner = slug.split("/", 1)[0]
                if node_id in selected_nodes or slug in selected_slugs or owner_counts[owner] >= owner_cap:
                    continue
                chosen = dict(candidate)
                chosen["_assigned_pattern_id"] = pattern_id
                selected.append(chosen)
                selected_nodes.add(node_id)
                selected_slugs.add(slug)
                owner_counts[owner] += 1
                progress = True
                break
            if len(selected) >= limit:
                break
        if not progress:
            break
    return selected


def _repo_id_from_slug(slug: str) -> str:
    owner, repository = slug.strip("/").split("/", 1)
    return f"github:{owner}/{repository}"


def _repo_slug_from_id(repo_id: object) -> str | None:
    if not isinstance(repo_id, str) or not repo_id.startswith("github:"):
        return None
    return normalize_slug(repo_id.removeprefix("github:"))


def protect_identities(
    candidates: Sequence[dict[str, Any]],
    existing_metrics: Sequence[dict[str, Any]],
) -> None:
    """Reject slug/node reuse before any output file is changed."""
    slug_nodes: dict[str, str] = {}
    node_slugs: dict[str, str] = {}
    for metric in existing_metrics:
        slug = _repo_slug_from_id(metric.get("repo_id"))
        if slug is None:
            continue
        canonical = normalize_slug(metric.get("canonical_slug"))
        if canonical is not None and canonical != slug:
            raise IdentityConflictError(
                f"existing metric {metric.get('repo_id')!r} has conflicting canonical_slug "
                f"{metric.get('canonical_slug')!r}"
            )
        node_id = metric.get("github_node_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        if slug in slug_nodes and slug_nodes[slug] != node_id:
            raise IdentityConflictError(f"existing slug {slug!r} maps to multiple GitHub node IDs")
        if node_id in node_slugs and node_slugs[node_id] != slug:
            raise IdentityConflictError(f"existing GitHub node ID {node_id!r} maps to multiple slugs")
        slug_nodes[slug] = node_id
        node_slugs[node_id] = slug

    for candidate in candidates:
        slug = normalize_slug(candidate.get("full_name"))
        node_id = candidate.get("node_id")
        if slug is None or not isinstance(node_id, str) or not node_id:
            continue
        if slug in slug_nodes and slug_nodes[slug] != node_id:
            raise IdentityConflictError(
                f"discovered slug {candidate.get('full_name')!r} changed node ID from "
                f"{slug_nodes[slug]!r} to {node_id!r}"
            )
        if node_id in node_slugs and node_slugs[node_id] != slug:
            raise IdentityConflictError(
                f"discovered node ID {node_id!r} is already attached to {node_slugs[node_id]!r}, "
                f"not {slug!r}"
            )


def _ordered_union(values: Iterable[Iterable[str]]) -> list[str]:
    return sorted({str(item) for group in values for item in group if str(item)})


def project_from_candidate(
    candidate: dict[str, Any],
    snapshot_date: str,
    profiles: dict[str, dict[str, Any]],
    patterns: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    provenance = candidate["_provenance"]
    pattern_ids = sorted({str(item["pattern_id"]) for item in provenance})
    assigned_pattern = str(candidate["_assigned_pattern_id"])
    if assigned_pattern not in pattern_ids:
        raise ValueError(f"assigned pattern {assigned_pattern!r} is absent from provenance")
    linked_profiles = [profiles[str(item["profile_id"])] for item in provenance]
    assigned_profiles = sorted(
        (profile for profile in linked_profiles if profile["pattern_id"] == assigned_pattern),
        key=lambda profile: profile["profile_id"],
    )
    pattern_links = []
    for pattern_id in pattern_ids:
        roles = _ordered_union(
            profile["roles"] for profile in linked_profiles if profile["pattern_id"] == pattern_id
        )
        pattern_links.append({"pattern_id": pattern_id, "roles": roles})
    linked_patterns = [patterns[pattern_id] for pattern_id in pattern_ids]
    assigned = patterns[assigned_pattern]
    full_name = str(candidate["full_name"])
    language = str(candidate["language"]).strip().casefold()
    compact_provenance = [
        {
            "pattern_id": item["pattern_id"],
            "profile_id": item["profile_id"],
            "query_id": item["query_id"],
            "topic": item["topic"],
        }
        for item in provenance
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "repo_id": _repo_id_from_slug(full_name),
        "url": f"https://github.com/{full_name}",
        "name": str(candidate.get("name") or full_name.split("/", 1)[1]),
        "primary_domain": assigned["domains"][0],
        "domains": _ordered_union(pattern["domains"] for pattern in linked_patterns),
        "cohort_id": assigned_profiles[0]["cohort_id"],
        "operations": _ordered_union(pattern["operations"] for pattern in linked_patterns),
        "problems": _ordered_union(pattern["solves"] for pattern in linked_patterns),
        "mechanisms": _ordered_union(pattern["required_mechanisms"] for pattern in linked_patterns),
        "topologies": _ordered_union(pattern["topologies"] for pattern in linked_patterns),
        "runtimes": _ordered_union(profile["runtimes"] for profile in linked_profiles),
        "languages": [language],
        "protocols": _ordered_union(profile["protocols"] for profile in linked_profiles),
        "limitations": ["discovery-only", "requires-code-review"],
        "pattern_links": pattern_links,
        "summary": (
            f"{full_name} was found through GitHub topic searches linked to "
            f"{len(pattern_ids)} catalog pattern(s); treat it as discovery evidence until its code "
            "and tests are reviewed."
        ),
        "curation": {
            "tier": "C",
            "catalogued_at": snapshot_date,
            "source": "github-topic-discovery",
        },
        "discovery": {
            "assigned_pattern_id": assigned_pattern,
            "provenance": compact_provenance,
        },
    }


def metric_from_candidate(candidate: dict[str, Any], repo_id: str) -> dict[str, Any]:
    candidate_slug = normalize_slug(candidate.get("full_name"))
    repo_slug = _repo_slug_from_id(repo_id)
    node_id = candidate.get("node_id")
    if candidate_slug is None or repo_slug != candidate_slug:
        raise IdentityConflictError(
            f"cannot attach search result {candidate.get('full_name')!r} to {repo_id!r}"
        )
    if not isinstance(node_id, str) or not node_id:
        raise ValueError(f"{repo_id}: search result requires github_node_id")
    for field in ("archived", "disabled", "fork", "is_template"):
        if not isinstance(candidate.get(field), bool):
            raise ValueError(f"{repo_id}: search result {field} must be a boolean")
    for field in ("stargazers_count", "forks_count", "open_issues_count"):
        value = candidate.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{repo_id}: search result {field} must be a nonnegative integer")
    license_value = candidate.get("license")
    spdx = license_value.get("spdx_id") if isinstance(license_value, dict) else None
    fetched_values = sorted(
        str(item.get("fetched_at"))
        for item in candidate.get("_provenance", [])
        if item.get("fetched_at")
    )
    if not fetched_values:
        raise ValueError(f"{repo_id}: search result requires fetched_at provenance")
    for fetched_at in fetched_values:
        _parse_utc_timestamp(fetched_at, f"{repo_id} fetched_at")
    return {
        "repo_id": repo_id,
        "github_node_id": node_id,
        "canonical_slug": candidate.get("full_name"),
        "stars": candidate.get("stargazers_count"),
        "forks": candidate.get("forks_count"),
        "open_issues": candidate.get("open_issues_count"),
        "archived": candidate.get("archived"),
        "disabled": candidate.get("disabled"),
        "is_fork": candidate.get("fork"),
        "is_template": candidate.get("is_template"),
        "default_branch": candidate.get("default_branch"),
        "primary_language": candidate.get("language"),
        "license_spdx": spdx,
        "created_at": candidate.get("created_at"),
        "updated_at": candidate.get("updated_at"),
        "pushed_at": candidate.get("pushed_at"),
        "fetched_at": fetched_values[-1] if fetched_values else None,
        "source": "github-rest-api",
        "source_endpoint": "search/repositories",
        "status": "ok",
    }


def _parse_snapshot_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid --snapshot-date {value!r}; expected YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"invalid --snapshot-date {value!r}; expected YYYY-MM-DD")
    return parsed


def _static_project_paths(reference_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(reference_dir.glob("projects-*.jsonl"))
        if path.name != "projects-expanded.jsonl"
    ]


def _upsert_search_metric(
    metric_by_id: dict[str, dict[str, Any]],
    metric_id_keys: dict[str, str],
    candidate: dict[str, Any],
    repo_id: str,
) -> bool:
    """Fill a missing/pending metric; retain a verified existing REST snapshot."""
    key = repo_id.casefold()
    prior_id = metric_id_keys.get(key)
    existing = metric_by_id.get(prior_id, {}) if prior_id is not None else {}
    if existing.get("status") == "ok":
        if prior_id != repo_id:
            preserved = metric_by_id.pop(str(prior_id))
            preserved["repo_id"] = repo_id
            metric_by_id[repo_id] = preserved
            metric_id_keys[key] = repo_id
        return False

    new_metric = metric_from_candidate(candidate, repo_id)
    if prior_id is not None:
        existing = metric_by_id.pop(prior_id)
        new_metric = {**existing, **new_metric}
    metric_id_keys[key] = repo_id
    metric_by_id[repo_id] = new_metric
    return True


def validate_metric_record(metric: dict[str, Any]) -> None:
    """Validate the metric contract before preserving a pre-existing row."""
    repo_id = metric.get("repo_id")
    repo_slug = _repo_slug_from_id(repo_id)
    if repo_slug is None:
        raise ValueError(f"existing metric has invalid repo_id {repo_id!r}")
    status = metric.get("status")
    if status not in {"ok", "pending-refresh"}:
        raise ValueError(f"{repo_id}: invalid metric status {status!r}")
    for field in ("stars", "forks"):
        value = metric.get(field)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f"{repo_id}: metric {field} must be null or nonnegative integer")
    if status != "ok":
        return
    node_id = metric.get("github_node_id")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError(f"{repo_id}: status=ok requires github_node_id")
    if normalize_slug(metric.get("canonical_slug")) != repo_slug:
        raise IdentityConflictError(f"{repo_id}: canonical_slug does not match repo_id")
    _parse_utc_timestamp(metric.get("fetched_at"), f"{repo_id} fetched_at")
    for field in ("archived", "disabled", "is_fork"):
        if not isinstance(metric.get(field), bool):
            raise ValueError(f"{repo_id}: status=ok requires boolean {field}")


def _metadata(
    projects: Sequence[dict[str, Any]],
    metrics: Sequence[dict[str, Any]],
    pattern_count: int,
    snapshot: date,
) -> dict[str, Any]:
    tier_counts = Counter(
        str(project.get("curation", {}).get("tier", "C")).upper() for project in projects
    )
    metrics_by_repo = {str(metric.get("repo_id")): metric for metric in metrics}
    fresh = 0
    for project in projects:
        metric = metrics_by_repo.get(str(project.get("repo_id")), {})
        fetched = metric.get("fetched_at")
        try:
            fetched_date = _parse_utc_timestamp(fetched, "metric fetched_at").date()
        except ValueError:
            continue
        if metric.get("status") == "ok" and 0 <= (snapshot - fetched_date).days <= 30:
            fresh += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
        "generated_at": f"{snapshot.isoformat()}T00:00:00Z",
        "projects": {
            "total": len(projects),
            "tier_a": tier_counts.get("A", 0),
            "tier_b": tier_counts.get("B", 0),
            "tier_c": tier_counts.get("C", 0),
        },
        "patterns": pattern_count,
        "github_metrics": {
            "fresh": fresh,
            "pending_or_missing": len(projects) - fresh,
            "freshness_days": 30,
        },
        "notes": [
            "Static Tier B and Tier C records are preserved independently of generated discovery records.",
            "Expanded Tier C records come from a complete cached GitHub Search snapshot and require verification.",
            "No repository is represented as Tier A until code and test evidence is pinned to a commit.",
        ],
    }


def build_from_cache(
    reference_dir: Path,
    cache_dir: Path,
    *,
    target_total: int = DEFAULT_TARGET_TOTAL,
    snapshot_date: str,
    owner_cap: int = DEFAULT_OWNER_CAP,
) -> dict[str, Any]:
    """Build all four outputs from a complete cache, then atomically replace each file."""
    snapshot = _parse_snapshot_date(snapshot_date)
    profiles_path = reference_dir / "discovery-profiles.json"
    patterns_path = reference_dir / "patterns-core.jsonl"
    defaults, profiles, patterns, pattern_order = load_configuration(profiles_path, patterns_path)
    plan = make_query_plan(defaults, profiles)
    cached_rows = load_complete_cache(plan, cache_dir)
    merged = merge_search_results(cached_rows)
    eligible, rejected = filter_candidates(
        merged,
        default_min_stars=max(0, int(defaults.get("min_stars", 20))),
        min_size_kb=max(0, int(defaults.get("min_size_kb", DEFAULT_MIN_SIZE_KB))),
    )

    static_projects = [
        row for path in _static_project_paths(reference_dir) for row in read_jsonl(path)
    ]
    static_ids: dict[str, str] = {}
    for project in static_projects:
        repo_id = project.get("repo_id")
        slug = _repo_slug_from_id(repo_id)
        if slug is None or slug in static_ids:
            raise ValueError(f"duplicate or invalid static repo_id {repo_id!r}")
        static_ids[slug] = str(repo_id)

    existing_metrics = read_jsonl(reference_dir / "github-metrics.jsonl")
    for metric in existing_metrics:
        validate_metric_record(metric)
    protect_identities(merged, existing_metrics)
    new_candidates = [
        candidate
        for candidate in eligible
        if normalize_slug(candidate.get("full_name")) not in static_ids
    ]
    if target_total < len(static_projects):
        raise ValueError(
            f"target_total={target_total} is below the {len(static_projects)} static projects"
        )
    required = target_total - len(static_projects)
    selected = balanced_select(new_candidates, pattern_order, required, owner_cap)
    if len(selected) < required:
        raise InsufficientCandidatesError(
            f"target_total={target_total} requires {required} new repositories, but only "
            f"{len(selected)} satisfy filtering, balance, and owner_cap={owner_cap}"
        )

    profile_by_id = {str(profile["profile_id"]): profile for profile in profiles}
    project_candidate_pairs = [
        (
            project_from_candidate(candidate, snapshot_date, profile_by_id, patterns),
            candidate,
        )
        for candidate in selected
    ]
    expanded_projects = [project for project, _ in project_candidate_pairs]
    expanded_projects.sort(key=lambda row: str(row["repo_id"]).casefold())

    metric_by_id: dict[str, dict[str, Any]] = {}
    metric_id_keys: dict[str, str] = {}
    for metric in existing_metrics:
        repo_id = metric.get("repo_id")
        if not isinstance(repo_id, str):
            raise ValueError(f"existing metric has invalid repo_id {repo_id!r}")
        key = repo_id.casefold()
        if key in metric_id_keys:
            raise IdentityConflictError(f"duplicate existing metric repo_id {repo_id!r}")
        metric_id_keys[key] = repo_id
        metric_by_id[repo_id] = metric

    candidates_by_slug = {
        slug: candidate
        for candidate in merged
        if (slug := normalize_slug(candidate.get("full_name"))) is not None
    }
    static_metric_updates = 0
    for slug, repo_id in sorted(static_ids.items()):
        candidate = candidates_by_slug.get(slug)
        if candidate is not None:
            _upsert_search_metric(
                metric_by_id, metric_id_keys, candidate, repo_id
            )
            stored_id = metric_id_keys[repo_id.casefold()]
            if metric_by_id[stored_id].get("source_endpoint") == "search/repositories":
                static_metric_updates += 1

    for project, candidate in project_candidate_pairs:
        _upsert_search_metric(
            metric_by_id, metric_id_keys, candidate, str(project["repo_id"])
        )
    merged_metrics = sorted(metric_by_id.values(), key=lambda row: str(row["repo_id"]).casefold())

    all_projects = static_projects + expanded_projects
    assigned_counts = Counter(str(item["_assigned_pattern_id"]) for item in selected)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": snapshot_date,
        "cache_complete": True,
        "queries": {
            "planned": len(plan),
            "cached": len(cached_rows),
            "items": sum(len(row["items"]) for row in cached_rows),
        },
        "candidates": {
            "merged": len(merged),
            "eligible": len(eligible),
            "excluded": sum(rejected.values()),
            "exclusion_reasons": dict(sorted(rejected.items())),
        },
        "selection": {
            "strategy": "pattern-round-robin",
            "owner_cap": owner_cap,
            "target_total": target_total,
            "static": len(static_projects),
            "expanded": len(expanded_projects),
            "static_metrics_completed_from_search": static_metric_updates,
            "by_pattern": {pattern_id: assigned_counts.get(pattern_id, 0) for pattern_id in pattern_order},
        },
        "query_provenance": [
            {
                "query_id": row["query_id"],
                "profile_id": row["profile_id"],
                "pattern_id": row["pattern_id"],
                "topic": row["topic"],
                "fetched_at": row.get("fetched_at"),
                "items": len(row["items"]),
            }
            for row in sorted(cached_rows, key=lambda item: str(item["query_id"]))
        ],
    }
    metadata = _metadata(
        all_projects,
        merged_metrics,
        len(patterns),
        snapshot,
    )

    # All validation and identity checks above happen before the first replacement.
    atomic_write_jsonl(reference_dir / "projects-expanded.jsonl", expanded_projects)
    atomic_write_jsonl(reference_dir / "github-metrics.jsonl", merged_metrics)
    atomic_write_json(reference_dir / "discovery-manifest.json", manifest)
    atomic_write_json(reference_dir / "catalog-metadata.json", metadata)
    return {
        "target_total": target_total,
        "static": len(static_projects),
        "expanded": len(expanded_projects),
        "patterns": len(patterns),
        "metrics": len(merged_metrics),
        "outputs": {
            "projects": str(reference_dir / "projects-expanded.jsonl"),
            "metrics": str(reference_dir / "github-metrics.jsonl"),
            "manifest": str(reference_dir / "discovery-manifest.json"),
            "metadata": str(reference_dir / "catalog-metadata.json"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="fetch the next uncached query batch")
    parser.add_argument("--build", action="store_true", help="build outputs from a complete cache")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--target-total", type=int, default=DEFAULT_TARGET_TOTAL)
    parser.add_argument("--snapshot-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--timeout", type=int, default=30, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.fetch and not args.build:
        print("error: choose --fetch, --build, or both", file=sys.stderr)
        return 2
    if args.max_queries < 0 or args.target_total < 0:
        print("error: --max-queries and --target-total must be nonnegative", file=sys.stderr)
        return 2
    try:
        defaults, profiles, _, _ = load_configuration()
        plan = make_query_plan(defaults, profiles)
        result: dict[str, Any] = {}
        if args.fetch:
            token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            result["fetch"] = fetch_queries(
                plan,
                args.cache_dir,
                args.max_queries,
                token=token,
                timeout=max(5, args.timeout),
            )
        if args.build:
            result["build"] = build_from_cache(
                REFERENCE_DIR,
                args.cache_dir,
                target_total=args.target_total,
                snapshot_date=args.snapshot_date,
            )
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (
        IncompleteCacheError,
        IdentityConflictError,
        InsufficientCandidatesError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
