from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
INSPECTOR_PATH = ROOT / "skills" / "choose-proven-cloud-stack" / "scripts" / "inspect_repository.py"


def load_inspector():
    spec = importlib.util.spec_from_file_location("inspect_repository_hardening", INSPECTOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inspector = load_inspector()
SHA = "a" * 40
OID = b"b" * 40


def tree_record(path: bytes, object_type: bytes = b"blob") -> bytes:
    return b"100644 " + object_type + b" " + OID + b"\t" + path + b"\0"


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, url: str | None = None):
        self.body = body
        self.headers = headers or {}
        self.url = url or f"https://{inspector.RAW_HOST}/owner/repo/{SHA}/src/a.py"
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        self.read_calls += 1
        return self.body if size < 0 else self.body[:size]


class FakeOpener:
    def __init__(self, response: FakeResponse):
        self.response = response

    def open(self, request, timeout):  # noqa: ANN001
        return self.response


class InspectorHardeningTests(unittest.TestCase):
    def test_git_environment_scrubs_config_and_prompt_injection(self):
        base = {
            "PATH": "safe-path",
            "GIT_CONFIG_PARAMETERS": "'protocol.ext.allow'='always'",
            "Git_Dir": "attacker-controlled",
            "GIT_ASKPASS": "run-me",
            "SSH_ASKPASS": "run-me-too",
        }
        env = inspector.build_git_environment(base)
        self.assertEqual(env["PATH"], "safe-path")
        self.assertNotIn("GIT_CONFIG_PARAMETERS", env)
        self.assertNotIn("Git_Dir", env)
        self.assertNotIn("GIT_ASKPASS", env)
        self.assertNotIn("SSH_ASKPASS", env)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_CONFIG_COUNT"], "0")
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_git_policy_allows_only_https_and_disables_helpers(self):
        args = inspector.git_policy_args(Path("empty-hooks"))
        settings = {value for flag, value in zip(args[::2], args[1::2]) if flag == "-c"}
        self.assertIn("protocol.allow=never", settings)
        self.assertIn("protocol.https.allow=always", settings)
        self.assertIn("protocol.ext.allow=never", settings)
        self.assertIn("protocol.file.allow=never", settings)
        self.assertIn("protocol.ssh.allow=never", settings)
        self.assertIn("credential.helper=", settings)
        self.assertIn("http.followRedirects=false", settings)

    def test_nul_tree_parser_preserves_colons_and_newlines_in_paths(self):
        output = tree_record(b"src/name:with:colons.py") + tree_record(b"docs/line\nbreak.md")
        entries, count = inspector.parse_tree(output, max_tracked_files=2)
        self.assertEqual(count, 2)
        self.assertEqual(
            [entry["path"] for entry in entries],
            ["src/name:with:colons.py", "docs/line\nbreak.md"],
        )

    def test_tree_parser_enforces_file_limit(self):
        output = tree_record(b"a.py") + tree_record(b"b.py")
        with self.assertRaisesRegex(RuntimeError, "tracked file limit exceeded"):
            inspector.parse_tree(output, max_tracked_files=1)

    def test_pinned_links_percent_encode_untrusted_filename_characters(self):
        link = inspector.pinned_url("owner", "repo", SHA, "src/a:b #c\n.py", 7)
        self.assertEqual(
            link,
            f"https://github.com/owner/repo/blob/{SHA}/src/a%3Ab%20%23c%0A.py#L7",
        )

    def test_fetch_rejects_declared_oversize_blob_without_reading_body(self):
        response = FakeResponse(b"secret", {"Content-Length": "5000"})
        with mock.patch.object(inspector, "RAW_OPENER", FakeOpener(response)):
            content, status, bytes_read = inspector.fetch_blob(
                "owner", "repo", SHA, "src/a.py", max_bytes=100, timeout=1
            )
        self.assertIsNone(content)
        self.assertEqual(status, "blob-too-large")
        self.assertEqual(bytes_read, 0)
        self.assertEqual(response.read_calls, 0)

    def test_fetch_caps_undeclared_response_and_rejects_redirected_host(self):
        oversized = FakeResponse(b"x" * 101)
        with mock.patch.object(inspector, "RAW_OPENER", FakeOpener(oversized)):
            content, status, bytes_read = inspector.fetch_blob(
                "owner", "repo", SHA, "src/a.py", max_bytes=100, timeout=1
            )
        self.assertIsNone(content)
        self.assertEqual(status, "blob-too-large")
        self.assertEqual(bytes_read, 101)

        redirected = FakeResponse(b"retry", url="https://example.com/payload")
        with mock.patch.object(inspector, "RAW_OPENER", FakeOpener(redirected)):
            content, status, bytes_read = inspector.fetch_blob(
                "owner", "repo", SHA, "src/a.py", max_bytes=100, timeout=1
            )
        self.assertIsNone(content)
        self.assertEqual(status, "unsafe-redirect")
        self.assertEqual(bytes_read, 0)
        self.assertEqual(redirected.read_calls, 0)

    def test_inspect_uses_blobless_clone_and_searches_bounded_pinned_content(self):
        git_calls: list[list[str]] = []

        def fake_run_git(git, args, cwd, env, timeout, max_stdout_bytes=None):  # noqa: ANN001
            git_calls.append(list(args))
            if "clone" in args:
                return ""
            if "config" in args:
                return "blob:none\n"
            if "count-objects" in args:
                return "size: 1\nsize-pack: 2\nsize-garbage: 0\n"
            if "rev-parse" in args:
                return SHA + "\n"
            if "symbolic-ref" in args:
                return "origin/main\n"
            raise AssertionError(args)

        raw_path = b"src/a:part #1.py"
        with (
            mock.patch.object(inspector.shutil, "which", return_value="git-safe"),
            mock.patch.object(inspector, "run_git", side_effect=fake_run_git),
            mock.patch.object(inspector, "_run_git_bytes", return_value=tree_record(raw_path)),
            mock.patch.object(inspector, "fetch_blob", return_value=(b"Retry safely\n", "ok", 13)) as fetch,
        ):
            result = inspector.inspect(
                "https://github.com/owner/repo",
                ["retry"],
                max_matches=10,
                max_tree_files=10,
                timeout=5,
                max_search_files=1,
                max_tracked_files=10,
                max_blob_bytes=100,
                max_total_bytes=1000,
                max_clone_bytes=10_000,
            )

        clone = next(call for call in git_calls if "clone" in call)
        self.assertIn("--filter=blob:none", clone)
        self.assertIn("--no-checkout", clone)
        self.assertIn("--no-tags", clone)
        self.assertEqual(result["security"]["git_protocols"], ["https"])
        self.assertEqual(result["security"]["partial_clone_filter"], "blob:none")
        self.assertEqual(result["resource_usage"]["metadata_clone_bytes"], 3072)
        self.assertEqual(result["resource_usage"]["files_searched"], 1)
        self.assertEqual(result["matches"][0]["path"], "src/a:part #1.py")
        self.assertIn("src/a%3Apart%20%231.py#L1", result["matches"][0]["url"])
        self.assertLessEqual(fetch.call_args.args[4], 100)

    def test_tracked_file_limit_aborts_before_any_blob_fetch(self):
        def fake_run_git(git, args, cwd, env, timeout, max_stdout_bytes=None):  # noqa: ANN001
            if "clone" in args:
                return ""
            if "config" in args:
                return "blob:none\n"
            if "count-objects" in args:
                return "size: 0\nsize-pack: 1\nsize-garbage: 0\n"
            if "rev-parse" in args:
                return SHA + "\n"
            if "symbolic-ref" in args:
                return "origin/main\n"
            raise AssertionError(args)

        tree = tree_record(b"src/a.py") + tree_record(b"src/b.py")
        with (
            mock.patch.object(inspector.shutil, "which", return_value="git-safe"),
            mock.patch.object(inspector, "run_git", side_effect=fake_run_git),
            mock.patch.object(inspector, "_run_git_bytes", return_value=tree),
            mock.patch.object(inspector, "fetch_blob") as fetch,
        ):
            with self.assertRaisesRegex(RuntimeError, "tracked file limit exceeded"):
                inspector.inspect(
                    "https://github.com/owner/repo",
                    ["retry"],
                    max_matches=10,
                    max_tree_files=10,
                    timeout=5,
                    max_search_files=2,
                    max_tracked_files=1,
                    max_blob_bytes=100,
                    max_total_bytes=1000,
                    max_clone_bytes=10_000,
                )
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
