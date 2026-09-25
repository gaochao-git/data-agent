"""Sandbox text search via rg and Files API glob."""


from __future__ import annotations


import fnmatch
import json
import shlex
from typing import Any

from utils.logger import get_logger
from .workspace_sandbox_api import (
    SandboxExecutionSessionRef,
    _resolve_sandbox_real_path,
    require_sandbox_session,
    run_command_in_sandbox,
)
from .workspace_sandbox_paths import sandbox_path_to_visible
from .workspace_sync import _recover_pending_persistence
from .workspace_text_budget import MAX_FILE_READ_LINE_CHARS

logger = get_logger(__name__)

_TEXT_GREP_MAX_FILE_BYTES = 2 * 1024 * 1024


def _matches_sandbox_glob(relative_path: str, pattern: str) -> bool:
    """Match root files for **/ patterns like pathlib's recursive glob does."""
    normalized = str(pattern or "**/*")
    return fnmatch.fnmatchcase(relative_path, normalized) or (
        normalized.startswith("**/")
        and fnmatch.fnmatchcase(relative_path, normalized[3:])
    )


def build_rg_command_str(
    target: str,
    patterns: list[str],
    *,
    glob: str = "**/*",
) -> str:
    """Build an rg command string for the sandbox backend (shell-executed).

    -F: fixed strings, --json: structured JSON Lines output for robust parsing.
    --hidden: include hidden dirs such as ``.tool-offload`` in the search.
    """
    parts = ["rg", "-F", "--json", "--hidden"]
    if glob and glob != "**/*":
        parts.extend(["--glob", shlex.quote(glob)])
    parts.append(shlex.quote(target))
    for p in patterns:
        parts.extend(["-e", shlex.quote(p)])
    return " ".join(parts)


def _parse_rg_match(
    line: str,
    normalized_target: str,
    prefix: str,
    prefix_len: int,
) -> tuple[str, Any, str] | None:
    """解析单行 ``rg --json`` 输出；非 match / 无效路径 / 缺行号时返回 None。"""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if data.get("type") != "match":
        return None
    pdata = data.get("data") or {}
    file_path = (pdata.get("path") or {}).get("text") or ""
    if file_path == normalized_target:
        file_rel = ""
    elif file_path.startswith(prefix):
        file_rel = file_path[prefix_len:]
    else:
        return None
    line_no = pdata.get("line_number")
    if line_no is None:
        return None
    content = ((pdata.get("lines") or {}).get("text") or "").rstrip("\n")[:MAX_FILE_READ_LINE_CHARS]
    return file_rel, line_no, content


def parse_rg_output(
    stdout: str,
    target: str,
    *,
    max_results: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Parse ``rg --json`` output into (matches, has_more).

    Each line is a JSON object. ``type == "match"`` entries contain
    ``data.path.text``, ``data.line_number`` and ``data.lines.text``.
    The ``target`` prefix is stripped from the file path so that ``file``
    in each match is relative to the search root.
    """
    normalized_target = target.rstrip("/")
    prefix = normalized_target + "/"
    prefix_len = len(prefix)
    matches: list[dict[str, Any]] = []
    body_chars = 0
    has_more = False
    for line in stdout.splitlines():
        if len(matches) >= max_results:
            has_more = True
            break
        parsed = _parse_rg_match(line, normalized_target, prefix, prefix_len)
        if parsed is None:
            continue
        file_rel, line_no, content = parsed
        line_cost = len(content) + (1 if matches else 0)
        if body_chars + line_cost > max_chars:
            has_more = True
            break
        matches.append({"file": file_rel, "line_number": int(line_no), "content": content})
        body_chars += line_cost
    return matches, has_more


async def sandbox_grep_text(
    *,
    session_ref: SandboxExecutionSessionRef,
    path: str,
    patterns: list[str],
    glob: str,
    max_results: int,
    max_chars: int,
) -> dict[str, Any]:
    """Search literal text in sandbox files via rg."""
    session = require_sandbox_session(session_ref)
    async with session.operation_lock:
        await _recover_pending_persistence(session)
        target = await _resolve_sandbox_real_path(session=session, file_path=path)

        command = build_rg_command_str(target, patterns, glob=glob)
        exit_code, stdout, stderr = await run_command_in_sandbox(
            session=session,
            command=f"bash -lc {shlex.quote(command)}",
            timeout_seconds=30,
        )
        if exit_code == 127 or "command not found" in (stderr or "").lower():
            raise RuntimeError(
                "file_grep 需要 ripgrep (rg)，沙箱环境未安装。"
                "请在沙箱镜像中安装 ripgrep: apt-get install ripgrep"
            )
        if exit_code > 1:
            raise RuntimeError(f"rg 执行失败 (exit={exit_code}): {stderr}")

        raw_matches, has_more = parse_rg_output(
            stdout, target, max_results=max_results, max_chars=max_chars,
        )
        stripped_target = target.rstrip("/")
        matches = [
            {
                "path": sandbox_path_to_visible(
                    f"{stripped_target}/{m['file']}" if m["file"] else stripped_target
                ),
                "line_number": m["line_number"],
                "line": m["content"],
            }
            for m in raw_matches
        ]
        payload = {"matches": matches, "has_more": has_more, "skipped_binary": 0, "skipped_large": 0}
        logger.info(
            "OpenSandbox file_grep 完成: sandbox_id=%s path=%s matches=%s has_more=%s",
            session.sandbox_id, target, len(matches), payload["has_more"],
        )
        return payload
