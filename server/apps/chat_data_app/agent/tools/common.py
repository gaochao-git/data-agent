"""Shared helpers for DataAnalyze tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MAX_READ_LIMIT = 500
DEFAULT_READ_LIMIT = 200
MAX_GLOB_RESULTS = 500
MAX_GREP_RESULTS = 200
MAX_GREP_FILE_BYTES = 2 * 1024 * 1024
IGNORED_DIR_NAMES = {".git", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}


def validate_workspace_root(workspace_root: str) -> str:
    root = str(workspace_root or "").strip()
    if not root:
        raise ValueError("缺少 workspace_root，无法使用文件工具")
    return root


def looks_binary(path: Path) -> bool:
    try:
        return b"\x00" in path.read_bytes()[:4096]
    except OSError:
        return False


def iter_files(base_dir: Path) -> list[Path]:
    if base_dir.is_file():
        return [base_dir]
    files: list[Path] = []
    for root, dirs, names in os.walk(base_dir):
        dirs[:] = [item for item in dirs if item not in IGNORED_DIR_NAMES]
        root_path = Path(root)
        if root_path.name in IGNORED_DIR_NAMES:
            continue
        for name in names:
            files.append((root_path / name).resolve())
    return files


def format_lines(lines: list[str], start_line: int) -> str:
    return "\n".join(f"{index:>6} {line}" for index, line in enumerate(lines, start=start_line))


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def tool_result_payload(tool_name: str, payload: Any) -> str:
    """工具层统一的文本结果序列化：``"[tool_name]\\n" + json_dumps(payload)``。

    所有需要输出 ``[工具名]\\n{JSON}`` 格式的工具必须复用本函数，
    不要再各自定义 ``_json_result`` / ``_tool_result`` 样板。
    """
    return f"[{tool_name}]\n" + json_dumps(payload)


def normalize_json_like_payload(value: Any) -> Any:
    """Parse JSON strings, double-encoded JSON strings, and fenced JSON blocks."""
    current = value
    for _ in range(2):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text:
            return current
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            current = json.loads(text)
        except json.JSONDecodeError:
            return value
    return current
