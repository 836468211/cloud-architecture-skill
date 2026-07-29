#!/usr/bin/env python3
"""Refresh GitHub facts without changing curated capability labels."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "choose-proven-cloud-stack"
REFERENCE_DIR = SKILL_DIR / "references"
METRICS_PATH = REFERENCE_DIR / "github-metrics.jsonl"


class IdentityDriftError(ValueError):
    """Refuse to attach refreshed facts to an unverified repository identity."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            rows.append(value)
    return rows


def load_projects(tiers: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(REFERENCE_DIR.glob("projects-*.jsonl")):
        for row in read_jsonl(path):
            tier = str(row.get("curation", {}).get("tier", "C")).upper()
            if not tiers or tier in tiers:
                rows.append(row)
    return rows


def request_repo(owner: str, repo: str, token: str | None, timeout: int) -> tuple[dict[str, Any], dict[str, str]]:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "choose-proven-cloud-stack-catalog-maintainer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
        response_headers = {key.lower(): value for key, value in response.headers.items()}
    return payload, response_headers


def build_metric(repo_id: str, payload: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    license_value = payload.get("license") or {}
    return {
        "repo_id": repo_id,
        "github_node_id": payload.get("node_id"),
        "canonical_slug": payload.get("full_name"),
        "stars": payload.get("stargazers_count"),
        "forks": payload.get("forks_count"),
        "open_issues": payload.get("open_issues_count"),
        "archived": payload.get("archived"),
        "disabled": payload.get("disabled"),
        "is_fork": payload.get("fork"),
        "default_branch": payload.get("default_branch"),
        "primary_language": payload.get("language"),
        "license_spdx": license_value.get("spdx_id"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "pushed_at": payload.get("pushed_at"),
        "fetched_at": fetched_at,
        "source": "github-rest-api",
        "status": "ok",
    }


def normalize_slug(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().strip("/")
    parts = cleaned.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return "/".join(parts).casefold()


def requested_slug(repo_id: str) -> str:
    if not repo_id.startswith("github:"):
        raise IdentityDriftError(f"invalid GitHub repo_id {repo_id!r}")
    slug = repo_id.removeprefix("github:")
    if normalize_slug(slug) is None:
        raise IdentityDriftError(f"invalid GitHub repo_id {repo_id!r}")
    return slug


def reconcile_metric(
    repo_id: str,
    cached: dict[str, Any],
    payload: dict[str, Any],
    fetched_at: str,
) -> tuple[dict[str, Any], str | None]:
    """Build a metric only after proving the API response belongs to the curated repo."""
    requested = requested_slug(repo_id)
    requested_key = normalize_slug(requested)
    payload_slug = payload.get("full_name")
    payload_key = normalize_slug(payload_slug)
    if payload_key is None:
        raise IdentityDriftError(
            f"{repo_id}: API payload has invalid full_name {payload_slug!r}; preserving cached metric"
        )

    cached_ok = cached.get("status") == "ok"
    cached_node = cached.get("github_node_id") if cached_ok else None
    payload_node = payload.get("node_id")
    same_node = False
    if cached_ok:
        if payload_node != cached_node:
            raise IdentityDriftError(
                f"{repo_id}: github_node_id changed from {cached_node!r} to {payload_node!r}; "
                "preserving cached metric"
            )
        same_node = cached_node is not None

    if payload_key != requested_key:
        if same_node:
            cached_slug = cached.get("canonical_slug")
            raise IdentityDriftError(
                f"{repo_id}: same-node rename detected (requested {requested!r}, "
                f"cached canonical_slug {cached_slug!r}, API full_name {payload_slug!r}); "
                "update the curated project record before refreshing; preserving cached metric"
            )
        raise IdentityDriftError(
            f"{repo_id}: API full_name {payload_slug!r} does not match requested slug {requested!r}; "
            "preserving cached metric"
        )

    identity_note: str | None = None
    cached_slug = cached.get("canonical_slug") if cached_ok else None
    cached_key = normalize_slug(cached_slug)
    if same_node and cached_key is not None and cached_key != payload_key:
        identity_note = (
            f"accepted same-node rename from {cached_slug!r} to {payload_slug!r} because "
            f"the curated repo_id now requests {requested!r}"
        )

    return build_metric(repo_id, payload, fetched_at), identity_note


def pending_metric(repo_id: str) -> dict[str, Any]:
    return {
        "repo_id": repo_id,
        "stars": None,
        "forks": None,
        "archived": None,
        "pushed_at": None,
        "license_spdx": None,
        "fetched_at": None,
        "source": "github-rest-api",
        "status": "pending-refresh",
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in sorted(rows, key=lambda item: item["repo_id"].lower()):
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
    temp_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", action="append", default=None, help="curation tier to refresh; repeatable; defaults to B")
    parser.add_argument("--repo", action="append", default=[], help="exact repo_id to refresh; repeatable")
    parser.add_argument("--max", type=int, default=60, help="maximum requests in this run")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.0, help="optional delay between requests")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    tiers = {item.upper() for item in (args.tier or ["B"])}
    all_projects = load_projects(set())
    selected = load_projects(tiers)
    if args.repo:
        requested = set(args.repo)
        selected = [row for row in all_projects if row.get("repo_id") in requested]
        missing = requested - {row.get("repo_id") for row in selected}
        if missing:
            print(f"unknown repo IDs: {sorted(missing)}", file=sys.stderr)
            return 2

    existing = {row["repo_id"]: row for row in read_jsonl(METRICS_PATH) if row.get("repo_id")}
    for project in all_projects:
        repo_id = project["repo_id"]
        existing.setdefault(repo_id, pending_metric(repo_id))

    limit = max(0, args.max)
    selected = sorted(selected, key=lambda row: row["repo_id"].lower())[:limit]
    successes = 0
    failures = 0
    identity_failures = 0
    remaining: str | None = None
    for index, project in enumerate(selected, 1):
        repo_id = project["repo_id"]
        owner_repo = repo_id.removeprefix("github:")
        owner, repo = owner_repo.split("/", 1)
        fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            payload, headers = request_repo(owner, repo, token, max(5, args.timeout))
            remaining = headers.get("x-ratelimit-remaining", remaining)
            metric, identity_note = reconcile_metric(repo_id, existing[repo_id], payload, fetched_at)
            existing[repo_id] = metric
            successes += 1
            if identity_note:
                print(f"[{index}/{len(selected)}] identity {repo_id}: {identity_note}", file=sys.stderr)
            print(f"[{index}/{len(selected)}] ok {repo_id} stars={payload.get('stargazers_count')} remaining={remaining}")
        except IdentityDriftError as exc:
            failures += 1
            identity_failures += 1
            print(f"[{index}/{len(selected)}] identity rejected {repo_id}: {exc}", file=sys.stderr)
        except urllib.error.HTTPError as exc:
            failures += 1
            remaining = exc.headers.get("X-RateLimit-Remaining", remaining) if exc.headers else remaining
            print(f"[{index}/{len(selected)}] HTTP {exc.code} {repo_id} remaining={remaining}", file=sys.stderr)
            if exc.code == 403 and remaining == "0":
                print("GitHub API rate limit exhausted; preserving remaining cached records.", file=sys.stderr)
                break
        except (OSError, ValueError, urllib.error.URLError) as exc:
            failures += 1
            print(f"[{index}/{len(selected)}] failed {repo_id}: {exc}", file=sys.stderr)
        if args.delay > 0 and index < len(selected):
            time.sleep(min(args.delay, 10.0))

    write_jsonl(METRICS_PATH, list(existing.values()))
    print(
        json.dumps(
            {
                "selected": len(selected),
                "updated": successes,
                "failed": failures,
                "identity_failures": identity_failures,
                "rate_remaining": remaining,
                "output": str(METRICS_PATH),
            },
            ensure_ascii=False,
        )
    )
    if identity_failures:
        return 1
    return 0 if successes or not selected else 1


if __name__ == "__main__":
    raise SystemExit(main())
