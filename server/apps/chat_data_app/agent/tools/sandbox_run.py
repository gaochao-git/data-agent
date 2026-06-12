"""DataAnalyze sandbox_run tool."""

from __future__ import annotations

import shlex
from pathlib import PurePosixPath

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.workspace_execution import (
    OPENSANDBOX_BACKEND,
    SandboxExecutionSessionRef,
    require_sandbox_session,
    run_command_in_sandbox,
    sandbox_outputs_dir,
    sandbox_path_to_visible,
    sandbox_runtime_skills_dir,
    sandbox_uploads_dir,
    sandbox_visible_path,
    sandbox_workspace_dir,
    truncate_output,
)
from .common import json_dumps

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 120


def _tool_result(payload: dict) -> str:
    return "[sandbox_run]\n" + json_dumps(payload)


def _normalize_cwd(cwd: str | None) -> str:
    raw = str(cwd or "").strip()
    if not raw:
        return sandbox_workspace_dir()
    return sandbox_visible_path(raw)


def _validate_command(command: str) -> str:
    value = str(command or "").strip()
    if not value:
        raise ValueError("command is required")
    if "\x00" in value:
        raise ValueError("command 不能包含空字节")
    lowered = value.lower()
    blocked_tokens = (" nohup ", " setsid ", " disown", " systemctl ", " service ", " sudo ", " su ")
    padded = f" {lowered} "
    if any(token in padded for token in blocked_tokens) or lowered.endswith("&"):
        raise ValueError("sandbox_run 不支持后台进程、提权命令或服务管理命令")
    return value


def _allowed_command_roots() -> tuple[str, ...]:
    return (sandbox_workspace_dir(), sandbox_outputs_dir(), sandbox_uploads_dir(), sandbox_runtime_skills_dir())


def _validate_command_paths(command: str) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"command 解析失败: {exc}") from exc
    disallowed = []
    allowed_roots = _allowed_command_roots()
    for token in tokens:
        normalized_token = token.lstrip("<>")
        token_parts = PurePosixPath(normalized_token).parts
        if ".." in token_parts:
            disallowed.append(token)
            continue
        if not normalized_token.startswith("/"):
            continue
        path = sandbox_visible_path(normalized_token)
        if not any(path == root or path.startswith(f"{root}/") for root in allowed_roots):
            disallowed.append(token)
    if disallowed:
        raise ValueError("sandbox_run 只允许访问当前会话目录内路径: " + ", ".join(disallowed[:3]))


async def run_sandbox_command(
    *,
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    session_ref: SandboxExecutionSessionRef | None = None,
) -> dict:
    """在 OpenSandbox 当前会话目录内执行通用命令。"""
    session = require_sandbox_session(session_ref)
    safe_command = _validate_command(command)
    _validate_command_paths(safe_command)
    sandbox_cwd = _normalize_cwd(cwd)
    timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    inner = "cd {cwd} && {command}".format(cwd=shlex.quote(sandbox_cwd), command=safe_command)
    exit_code, stdout_text, stderr_text = await run_command_in_sandbox(session=session, command=f"bash -o pipefail -lc {shlex.quote(inner)}", timeout_seconds=timeout)
    stdout, stdout_truncated = truncate_output(stdout_text)
    stderr, stderr_truncated = truncate_output(stderr_text)
    timed_out = "timeout" in stderr.lower() or "timed out" in stderr.lower()
    success = (exit_code == 0) and not timed_out
    return {
        "success": success,
        "backend": OPENSANDBOX_BACKEND,
        "sandbox_id": session.sandbox_id,
        "cwd": sandbox_path_to_visible(sandbox_cwd),
        "command": safe_command,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "timeout_seconds": timeout,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "next_required_action": (
            "如果命令生成了 workspace 中间产物，请校验后复制或写入 outputs，并使用 .chart.json / .dashboard.json 等产物后缀。"
            if success else "修复命令、脚本或输入文件后重新执行。"
        ),
    }


def create_sandbox_run_tool(workspace_root: str, execution_session_ref: SandboxExecutionSessionRef | None = None):
    @tool("sandbox_run")
    async def sandbox_run(command: str, cwd: str = "/mnt/user-data/workspace", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> str:
        """
        在当前会话 OpenSandbox 中执行非交互命令，可用于 ls/find/python3/node/bash 等。

        - cwd 必须位于 /mnt/user-data/workspace、outputs、uploads 或 runtime-skills 内，默认 workspace。
        - command 必须是短生命周期非交互命令，不支持后台进程、服务管理或提权命令。
        - 生成最终产物前，先把脚本输出写到 workspace/，校验后复制或写入 outputs/。
        """
        try:
            _ = workspace_root
            payload = await run_sandbox_command(command=command, cwd=cwd, timeout_seconds=timeout_seconds, session_ref=execution_session_ref)
            return _tool_result(payload)
        except Exception as exc:
            logger.error("sandbox_run 执行失败: %s", exc, exc_info=True)
            return f"sandbox_run 执行失败: {exc}"

    return sandbox_run
