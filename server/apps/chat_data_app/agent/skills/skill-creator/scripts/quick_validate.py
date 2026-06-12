#!/usr/bin/env python3
"""Validate a ChatData skill directory."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


ALLOWED_ROOT_ITEMS = {"SKILL.md", "references", "scripts", "assets"}
SKILL_NAME_PATTERN = re.compile(r"^[a-z]+(?:-[a-z]+)+$")


def _load_frontmatter(skill_md: Path) -> tuple[dict, str]:
    text = skill_md.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md must start with YAML frontmatter")
    if yaml is None:
        raise ValueError("PyYAML is required to validate YAML frontmatter")
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter must be a YAML object")
    return metadata, match.group(2)


def validate_skill_dir(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    if not skill_dir.exists() or not skill_dir.is_dir():
        return [f"skill directory not found: {skill_dir}"]

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        errors.append("missing SKILL.md")
        return errors

    extra_items = {item.name for item in skill_dir.iterdir()} - ALLOWED_ROOT_ITEMS
    if extra_items:
        errors.append(f"unexpected root items: {', '.join(sorted(extra_items))}")

    try:
        metadata, body = _load_frontmatter(skill_md)
    except Exception as exc:
        errors.append(str(exc))
        return errors

    name = str(metadata.get("name") or "").strip()
    title = str(metadata.get("title") or "").strip()
    description = str(metadata.get("description") or "").strip()

    if not SKILL_NAME_PATTERN.match(name):
        errors.append("name must be xxx-xxx style: lowercase letters separated by hyphens, at least one hyphen, no digits")
    if len(name) < 3 or len(name) > 64:
        errors.append("name length must be 3-64")
    if name and name != skill_dir.name:
        errors.append(f"name must match directory name: {skill_dir.name}")
    if not title:
        errors.append("title is required")
    if not description:
        errors.append("description is required")
    if len(description) > 300:
        errors.append("description should be concise, max 300 characters")
    if not body.strip():
        errors.append("SKILL.md body is empty")

    for dir_name in ("references", "scripts", "assets"):
        target = skill_dir / dir_name
        if not target.exists():
            errors.append(f"missing required directory: {dir_name}")
        elif not target.is_dir():
            errors.append(f"{dir_name} must be a directory")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a ChatData skill directory")
    parser.add_argument("skill_dir")
    args = parser.parse_args()

    errors = validate_skill_dir(Path(args.skill_dir))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Skill is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
