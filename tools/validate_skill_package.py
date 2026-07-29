#!/usr/bin/env python3
"""Validate the installable Skill subtree without third-party dependencies."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "choose-proven-cloud-stack"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCAL_LINK_RE = re.compile(r"\]\((?!https?://|#)([^)]+)\)")


def main() -> int:
    errors: list[str] = []
    skill_md = SKILL_DIR / "SKILL.md"
    license_file = SKILL_DIR / "LICENSE.txt"
    interface_file = SKILL_DIR / "agents" / "openai.yaml"

    for path in (skill_md, license_file, interface_file):
        if not path.is_file():
            errors.append(f"missing required install-subtree file: {path.relative_to(ROOT)}")
    if errors:
        return finish(errors)

    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        errors.append("SKILL.md must start with YAML frontmatter")
        return finish(errors)
    try:
        closing = lines.index("---", 1)
    except ValueError:
        errors.append("SKILL.md frontmatter is not closed")
        return finish(errors)

    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip():
            continue
        match = re.fullmatch(r"([a-z_]+):\s*(.+)", line)
        if not match:
            errors.append(f"unsupported SKILL.md frontmatter line: {line!r}")
            continue
        fields[match.group(1)] = match.group(2).strip().strip('"\'')
    if set(fields) != {"name", "description"}:
        errors.append("SKILL.md frontmatter must contain only name and description")
    name = fields.get("name", "")
    if name != SKILL_DIR.name or not NAME_RE.fullmatch(name) or len(name) > 63:
        errors.append("Skill name must match its lowercase hyphenated directory name")
    if not fields.get("description"):
        errors.append("Skill description must be non-empty")

    for target in LOCAL_LINK_RE.findall(text):
        clean = target.split("#", 1)[0]
        if clean and not (SKILL_DIR / clean).is_file():
            errors.append(f"SKILL.md local link does not exist: {target}")

    interface = interface_file.read_text(encoding="utf-8")
    for key in ("display_name", "short_description", "default_prompt"):
        if not re.search(rf"^\s{{2}}{key}:\s+\"[^\"]+\"\s*$", interface, re.MULTILINE):
            errors.append(f"agents/openai.yaml lacks a quoted {key}")
    if name and f"${name}" not in interface:
        errors.append("agents/openai.yaml default_prompt must reference the Skill name")

    if (SKILL_DIR / "README.md").exists():
        errors.append("README.md belongs at repository root, not inside the installable Skill")
    if license_file.read_text(encoding="utf-8").rstrip() != (ROOT / "LICENSE").read_text(encoding="utf-8").rstrip():
        errors.append("install-subtree LICENSE.txt must match the repository license")
    return finish(errors)


def finish(errors: list[str]) -> int:
    if errors:
        for item in errors:
            print(f"error: {item}", file=sys.stderr)
        return 1
    print("Skill package is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
