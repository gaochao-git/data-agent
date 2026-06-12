#!/usr/bin/env python3
"""
Count lines of code across the repository, skipping common build artifacts,
virtualenvs, and binary assets.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable

# Directory names to skip entirely when walking the tree.
DEFAULT_SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
}

# File extensions treated as binary; these are skipped.
DEFAULT_BINARY_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".lz",
    ".7z",
    ".rar",
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".bin",
    ".obj",
    ".o",
    ".a",
    ".d",
    ".class",
    ".jar",
    ".ttf",
    ".woff",
    ".woff2",
    ".map",
}


def iter_text_files(root: Path, skip_dirs: set[str], include_hidden: bool) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in skip_dirs
            and not d.startswith(".git")
            and (include_hidden or not d.startswith("."))
        ]
        for name in filenames:
            if not include_hidden and name.startswith("."):
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in DEFAULT_BINARY_EXTS:
                continue
            yield path


def count_lines(paths: Iterable[Path]) -> tuple[int, int]:
    files = 0
    lines = 0
    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                file_lines = sum(1 for _ in f)
            files += 1
            lines += file_lines
        except (OSError, UnicodeDecodeError):
            continue
    return files, lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Count lines of code in the repository.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Root directory to scan (defaults to repository root).",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include dot-directories except .git when walking.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root: Path = args.root.resolve()

    skip_dirs = set(DEFAULT_SKIP_DIRS)
    files, lines = count_lines(iter_text_files(root, skip_dirs, include_hidden=args.include_hidden))
    print(f"Scanning root: {root}")
    print(f"Files counted: {files}")
    print(f"Total lines: {lines}")


if __name__ == "__main__":
    main()
