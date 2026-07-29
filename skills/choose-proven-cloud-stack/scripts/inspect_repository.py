#!/usr/bin/env python3
"""Safely inspect tracked Git objects from an explicit public GitHub repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_TERMS = ["retry", "concurrency", "checkpoint", "checksum"]
SECRET_PATH_MARKERS = (".env", ".pem", ".key", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials")
SKIP_PATH_MARKERS = ("node_modules/", "vendor/", "third_party/", "dist/", "build/", ".min.js")
CODE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".java", ".js", ".jsx", ".kt", ".kts",
    ".lua", ".php", ".py", ".rb", ".rs", ".scala", ".swift", ".ts", ".tsx",
}
TEXT_EXTENSIONS = CODE_EXTENSIONS | {
    ".conf", ".css", ".graphql", ".h", ".hpp", ".html", ".ini", ".json", ".md",
    ".proto", ".rst", ".sh", ".sql", ".toml", ".txt", ".xml", ".yaml", ".yml",
}
TEXT_BASENAMES = {
    "build.gradle", "cmakelists.txt", "dockerfile", "go.mod", "go.sum", "license",
    "makefile", "pom.xml", "readme", "requirements.txt",
}
DEFAULT_MAX_TRACKED_FILES = 100_000
DEFAULT_MAX_TREE_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_CLONE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_BLOB_BYTES = 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_TERMS = 64
MAX_TERM_LENGTH = 200
RAW_HOST = "raw.githubusercontent.com"


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Do not let a pinned raw-content request leave the expected GitHub host."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        return None


RAW_OPENER = urllib.request.build_opener(RejectRedirects())


def build_git_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment that cannot inherit Git config or interactive helpers."""
    source = os.environ if base is None else base
    env = {
        key: value
        for key, value in source.items()
        if not key.upper().startswith("GIT_") and key.upper() not in {"SSH_ASKPASS", "SSH_ASKPASS_REQUIRE"}
    }
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_PROTOCOL_FROM_USER": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return env


def git_policy_args(hooks_dir: Path) -> list[str]:
    """Config overrides applied to every Git process spawned by the inspector."""
    settings = (
        ("core.hooksPath", str(hooks_dir)),
        ("credential.helper", ""),
        ("credential.interactive", "false"),
        ("fetch.recurseSubmodules", "false"),
        ("http.followRedirects", "false"),
        ("http.sslVerify", "true"),
        ("protocol.allow", "never"),
        ("protocol.ext.allow", "never"),
        ("protocol.file.allow", "never"),
        ("protocol.git.allow", "never"),
        ("protocol.http.allow", "never"),
        ("protocol.https.allow", "always"),
        ("protocol.ssh.allow", "never"),
        ("submodule.recurse", "false"),
    )
    return [part for key, value in settings for part in ("-c", f"{key}={value}")]


def _run_git_bytes(
    git_executable: str,
    args: Sequence[str],
    cwd: Path | None,
    env: dict[str, str],
    timeout: float,
    max_stdout_bytes: int | None = None,
) -> bytes:
    """Run Git without a shell, spooling output so repository data does not fill memory."""
    with tempfile.TemporaryFile() as stdout_handle, tempfile.TemporaryFile() as stderr_handle:
        completed = subprocess.run(
            [git_executable, *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            timeout=max(0.1, timeout),
            check=False,
            shell=False,
        )
        stdout_size = stdout_handle.tell()
        stderr_handle.seek(0)
        stderr = stderr_handle.read(2000).decode("utf-8", errors="replace").strip()
        if completed.returncode != 0:
            command = next((part for part in args if part in {"clone", "config", "count-objects", "ls-tree", "rev-parse", "symbolic-ref"}), args[0])
            raise RuntimeError(f"git {command} failed ({completed.returncode}): {stderr}")
        if max_stdout_bytes is not None and stdout_size > max_stdout_bytes:
            raise RuntimeError(
                f"git output exceeded safety limit ({stdout_size} > {max_stdout_bytes} bytes)"
            )
        stdout_handle.seek(0)
        return stdout_handle.read()


def run_git(
    git_executable: str,
    args: Sequence[str],
    cwd: Path | None,
    env: dict[str, str],
    timeout: float,
    max_stdout_bytes: int | None = None,
) -> str:
    return _run_git_bytes(git_executable, args, cwd, env, timeout, max_stdout_bytes).decode(
        "utf-8", errors="replace"
    )


def safe_path(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    return not any(marker in lowered for marker in SECRET_PATH_MARKERS + SKIP_PATH_MARKERS)


def is_text_candidate(path: str) -> bool:
    lowered = path.lower().replace("\\", "/")
    name = lowered.rsplit("/", 1)[-1]
    suffix = Path(name).suffix
    return suffix in TEXT_EXTENSIONS or name in TEXT_BASENAMES or name.startswith(("readme.", "license."))


def path_priority(path: str) -> tuple[int, str]:
    lowered = path.lower().replace("\\", "/")
    suffix = Path(lowered).suffix
    source_markers = ("src/", "lib/", "pkg/", "internal/", "cmd/")
    if suffix in CODE_EXTENSIONS and any(lowered.startswith(part) or f"/{part}" in lowered for part in source_markers):
        rank = 0
    elif suffix in CODE_EXTENSIONS and any(part in lowered for part in ("test", "spec")):
        rank = 1
    elif suffix in CODE_EXTENSIONS:
        rank = 2
    elif lowered.startswith("docs/") or "readme" in lowered or lowered.startswith("examples/"):
        rank = 3
    else:
        rank = 4
    return rank, lowered


def parse_tree(output: bytes, max_tracked_files: int) -> tuple[list[dict[str, str]], int]:
    """Parse `git ls-tree -r -z` without treating filename punctuation as delimiters."""
    blobs: list[dict[str, str]] = []
    tracked_count = 0
    for raw_entry in output.split(b"\0"):
        if not raw_entry:
            continue
        tracked_count += 1
        if tracked_count > max_tracked_files:
            raise RuntimeError(f"tracked file limit exceeded ({tracked_count} > {max_tracked_files})")
        metadata, separator, raw_path = raw_entry.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise RuntimeError("unexpected NUL-delimited ls-tree record")
        mode, object_type, object_id = fields
        if object_type != b"blob":
            continue
        if not re.fullmatch(rb"[0-9a-f]{40,64}", object_id):
            raise RuntimeError("ls-tree returned an invalid object ID")
        path = raw_path.decode("utf-8", errors="replace")
        blobs.append(
            {
                "mode": mode.decode("ascii", errors="replace"),
                "oid": object_id.decode("ascii"),
                "path": path,
            }
        )
    return blobs, tracked_count


def parse_clone_bytes(output: str) -> int:
    """Return loose, packed, and garbage object bytes from `count-objects -v`."""
    values: dict[str, int] = {}
    for raw_line in output.splitlines():
        key, separator, value = raw_line.partition(":")
        if separator:
            try:
                values[key.strip()] = int(value.strip())
            except ValueError:
                continue
    kibibytes = values.get("size", 0) + values.get("size-pack", 0) + values.get("size-garbage", 0)
    return max(0, kibibytes) * 1024


def normalize_terms(terms: Sequence[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_term in terms:
        term = str(raw_term).strip()
        if not term:
            continue
        if any(character in term for character in ("\0", "\r", "\n")):
            raise ValueError("search terms cannot contain NUL or newline characters")
        if len(term) > MAX_TERM_LENGTH:
            raise ValueError(f"search term exceeds {MAX_TERM_LENGTH} characters")
        folded = term.casefold()
        if folded not in seen:
            cleaned.append(term)
            seen.add(folded)
        if len(cleaned) > MAX_TERMS:
            raise ValueError(f"at most {MAX_TERMS} search terms are accepted")
    return cleaned


def quote_path(path: str) -> str:
    return urllib.parse.quote(path, safe="/")


def pinned_url(owner: str, repo: str, sha: str, path: str, line_number: int) -> str:
    return f"https://github.com/{owner}/{repo}/blob/{sha}/{quote_path(path)}#L{line_number}"


def raw_blob_url(owner: str, repo: str, sha: str, path: str) -> str:
    return f"https://{RAW_HOST}/{owner}/{repo}/{sha}/{quote_path(path)}"


def fetch_blob(
    owner: str,
    repo: str,
    sha: str,
    path: str,
    max_bytes: int,
    timeout: float,
) -> tuple[bytes | None, str, int]:
    """Fetch one pinned blob while enforcing the limit before returning searchable data."""
    url = raw_blob_url(owner, repo, sha, path)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "User-Agent": "choose-proven-cloud-stack-static-inspector",
        },
        method="GET",
    )
    try:
        with RAW_OPENER.open(request, timeout=max(0.1, timeout)) as response:
            final_url = response.geturl()
            parsed = urllib.parse.urlsplit(final_url)
            if parsed.scheme != "https" or parsed.hostname != RAW_HOST:
                return None, "unsafe-redirect", 0
            content_encoding = (response.headers.get("Content-Encoding") or "identity").lower()
            if content_encoding != "identity":
                return None, "unexpected-content-encoding", 0
            length_header = response.headers.get("Content-Length")
            if length_header:
                try:
                    content_length = int(length_header)
                except ValueError:
                    return None, "invalid-content-length", 0
                if content_length < 0 or content_length > max_bytes:
                    return None, "blob-too-large", 0
            data = response.read(max_bytes + 1)
            bytes_read = len(data)
            if bytes_read > max_bytes:
                return None, "blob-too-large", bytes_read
            return data, "ok", bytes_read
    except urllib.error.HTTPError as exc:
        return None, f"http-{exc.code}", 0
    except (OSError, ValueError, urllib.error.URLError):
        return None, "fetch-failed", 0


def find_matches(
    downloaded: Sequence[tuple[str, bytes]],
    terms: Sequence[str],
    owner: str,
    repo: str,
    sha: str,
    max_matches: int,
) -> tuple[list[dict[str, object]], bool]:
    folded_terms = [term.casefold() for term in terms]
    matches: list[dict[str, object]] = []
    truncated = False
    for path, content in downloaded:
        if b"\0" in content[:8192]:
            continue
        text = content.decode("utf-8", errors="replace")
        matches_in_file = 0
        for line_number, line in enumerate(text.splitlines(), 1):
            folded_line = line.casefold()
            if not any(term in folded_line for term in folded_terms):
                continue
            if matches_in_file >= 10:
                truncated = True
                continue
            matches.append(
                {
                    "path": path,
                    "line": line_number,
                    "excerpt": line[:500],
                    "url": pinned_url(owner, repo, sha, path, line_number),
                }
            )
            matches_in_file += 1
    matches.sort(key=lambda item: (*path_priority(str(item["path"])), int(item["line"])))
    if len(matches) > max_matches:
        truncated = True
    return matches[:max_matches], truncated


def inspect(
    url: str,
    terms: list[str],
    max_matches: int,
    max_tree_files: int,
    timeout: int,
    max_search_files: int = 40,
    max_tracked_files: int = DEFAULT_MAX_TRACKED_FILES,
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_clone_bytes: int = DEFAULT_MAX_CLONE_BYTES,
) -> dict[str, object]:
    match = URL_RE.fullmatch(url)
    if not match:
        raise ValueError("v1 accepts only explicit HTTPS GitHub repository URLs")
    owner, repo = match.groups()
    canonical_url = f"https://github.com/{owner}/{repo}"
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git is not installed or is not on PATH")
    if min(max_matches, max_tree_files, max_search_files, max_tracked_files, max_blob_bytes, max_total_bytes, max_clone_bytes) < 1:
        raise ValueError("resource limits must be positive integers")
    cleaned_terms = normalize_terms(terms)
    started = time.monotonic()

    def remaining_timeout() -> float:
        remaining = float(timeout) - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired("repository inspection", timeout)
        return remaining

    with tempfile.TemporaryDirectory(prefix="proven-cloud-review-") as temp_name:
        temp_root = Path(temp_name).resolve()
        hooks_dir = temp_root / "empty-hooks"
        hooks_dir.mkdir()
        repo_dir = temp_root / "repo"
        env = build_git_environment()
        policy = git_policy_args(hooks_dir)

        clone_tail = [
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            "--depth",
            "1",
            "--no-tags",
            "--single-branch",
            canonical_url,
            str(repo_dir),
        ]
        try:
            run_git(git, [*policy, *clone_tail], None, env, remaining_timeout())
        except RuntimeError as exc:
            message = str(exc)
            if os.name != "nt" or not ("schannel" in message.lower() or "sec_e_no_credentials" in message.lower()):
                raise
            repo_dir = temp_root / "repo-openssl"
            clone_tail[-1] = str(repo_dir)
            run_git(
                git,
                [*policy, "-c", "http.sslBackend=openssl", *clone_tail],
                None,
                env,
                remaining_timeout(),
            )

        partial_filter = run_git(
            git,
            [*policy, "config", "--local", "--get", "remote.origin.partialclonefilter"],
            repo_dir,
            env,
            remaining_timeout(),
        ).strip()
        if partial_filter != "blob:none":
            raise RuntimeError(f"remote did not retain the required blob:none filter: {partial_filter!r}")
        clone_usage = parse_clone_bytes(
            run_git(git, [*policy, "count-objects", "-v"], repo_dir, env, remaining_timeout())
        )
        if clone_usage > max_clone_bytes:
            raise RuntimeError(f"metadata clone exceeded safety limit ({clone_usage} > {max_clone_bytes} bytes)")

        sha = run_git(git, [*policy, "rev-parse", "HEAD"], repo_dir, env, remaining_timeout()).strip().lower()
        if not SHA_RE.fullmatch(sha):
            raise RuntimeError("GitHub repository did not resolve to a 40-character commit SHA")
        branch_output = run_git(
            git,
            [*policy, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            repo_dir,
            env,
            remaining_timeout(),
        ).strip()
        default_branch = branch_output.rsplit("/", 1)[-1]
        tree_output = _run_git_bytes(
            git,
            [*policy, "ls-tree", "-r", "-z", sha],
            repo_dir,
            env,
            remaining_timeout(),
            DEFAULT_MAX_TREE_BYTES,
        )
        blobs, tracked_count = parse_tree(tree_output, max_tracked_files)
        safe_blobs = [entry for entry in blobs if safe_path(entry["path"])]
        tree = [entry["path"] for entry in safe_blobs[:max_tree_files]]

        candidate_pool = sorted(
            (entry for entry in safe_blobs if is_text_candidate(entry["path"])),
            key=lambda entry: path_priority(entry["path"]),
        )
        selected = candidate_pool[:max_search_files] if cleaned_terms else []
        candidate_files = [entry["path"] for entry in selected]
        skipped: Counter[str] = Counter()
        downloaded: list[tuple[str, bytes]] = []
        transferred_bytes = 0
        searchable_bytes = 0
        total_budget_exhausted = False
        for entry in selected:
            remaining_bytes = max_total_bytes - transferred_bytes
            if remaining_bytes <= 1:
                total_budget_exhausted = True
                break
            read_limit = min(max_blob_bytes, remaining_bytes - 1)
            content, status, bytes_read = fetch_blob(
                owner,
                repo,
                sha,
                entry["path"],
                read_limit,
                remaining_timeout(),
            )
            transferred_bytes += bytes_read
            if content is None:
                skipped[status] += 1
                if bytes_read >= remaining_bytes:
                    total_budget_exhausted = True
                    break
                continue
            downloaded.append((entry["path"], content))
            searchable_bytes += len(content)

        matches, match_limit_reached = find_matches(
            downloaded, cleaned_terms, owner, repo, sha, max_matches
        ) if cleaned_terms else ([], False)
        search_scope_truncated = (
            len(candidate_pool) > len(selected)
            or total_budget_exhausted
            or bool(skipped)
        )
        return {
            "schema_version": "1.0",
            "repository": canonical_url,
            "commit_sha": sha,
            "default_branch": default_branch,
            "inspected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "security": {
                "checkout": False,
                "submodules": False,
                "git_lfs_smudge": False,
                "git_config_isolated": True,
                "git_protocols": ["https"],
                "partial_clone_filter": "blob:none",
                "repository_code_executed": False,
                "temporary_clone_removed_on_exit": True,
            },
            "resource_limits": {
                "max_blob_bytes": max_blob_bytes,
                "max_clone_bytes": max_clone_bytes,
                "max_search_files": max_search_files,
                "max_total_bytes": max_total_bytes,
                "max_tracked_files": max_tracked_files,
                "max_tree_output_bytes": DEFAULT_MAX_TREE_BYTES,
            },
            "resource_usage": {
                "metadata_clone_bytes": clone_usage,
                "content_bytes_transferred": transferred_bytes,
                "content_bytes_searched": searchable_bytes,
                "files_searched": len(downloaded),
            },
            "terms": cleaned_terms,
            "tracked_file_count": tracked_count,
            "tree_sample": tree,
            "candidate_files": candidate_files,
            "candidate_files_truncated": len(candidate_pool) > len(selected),
            "skipped_files": dict(sorted(skipped.items())),
            "search_scope_truncated": search_scope_truncated,
            "matches": matches,
            "matches_truncated": match_limit_reached,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--terms", nargs="+", default=DEFAULT_TERMS)
    parser.add_argument("--max-matches", type=int, default=80)
    parser.add_argument("--max-tree-files", type=int, default=200)
    parser.add_argument("--max-search-files", type=int, default=40)
    parser.add_argument("--max-tracked-files", type=int, default=DEFAULT_MAX_TRACKED_FILES)
    parser.add_argument("--max-blob-bytes", type=int, default=DEFAULT_MAX_BLOB_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--max-clone-bytes", type=int, default=DEFAULT_MAX_CLONE_BYTES)
    parser.add_argument("--timeout", type=int, default=60, help="total inspection deadline in seconds")
    args = parser.parse_args()
    try:
        result = inspect(
            args.url,
            args.terms,
            max(1, min(args.max_matches, 500)),
            max(1, min(args.max_tree_files, 2000)),
            max(5, min(args.timeout, 300)),
            max(1, min(args.max_search_files, 200)),
            max(1, min(args.max_tracked_files, 500_000)),
            max(1024, min(args.max_blob_bytes, 8 * 1024 * 1024)),
            max(1024, min(args.max_total_bytes, 64 * 1024 * 1024)),
            max(1024 * 1024, min(args.max_clone_bytes, 512 * 1024 * 1024)),
        )
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
