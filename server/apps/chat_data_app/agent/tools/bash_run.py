"""ChatData Bash tool for local and OpenSandbox workspaces."""

from __future__ import annotations

import shlex
import asyncio
import os
from pathlib import PurePosixPath

from langchain_core.tools import tool

from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT
from ...services.conversation_workspace import (
    ConversationWorkspaceRuntime,
    WorkspacePathMapping,
    resolve_workspace_path,
    to_visible_path,
)
from ...services.workspace_execution import (
    OPENSANDBOX_BACKEND,
    run_command_in_sandbox,
    sandbox_outputs_dir,
    sandbox_path_to_visible,
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
    return "[bash_run]\n" + json_dumps(payload)


async def _normalize_cwd(cwd: str | None, workspace_runtime: ConversationWorkspaceRuntime) -> str:
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
        raise ValueError("bash_run 不支持后台进程、提权命令或服务管理命令")
    return value


async def _local_visible_path(workspace_runtime: ConversationWorkspaceRuntime, path: str):
    normalized = str(path or "").strip()
    if normalized.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        return (await workspace_runtime.resolve_readonly_skill_path_async(normalized))[0]
    return resolve_workspace_path(workspace_runtime.workspace_root, normalized)


async def _validate_command_paths(command: str, workspace_runtime: ConversationWorkspaceRuntime) -> None:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"command 解析失败: {exc}") from exc
    disallowed: list[str] = []
    for token in tokens:
        normalized_token = token.lstrip("<>")
        token_parts = PurePosixPath(normalized_token).parts
        if ".." in token_parts:
            disallowed.append(token)
            continue
        if not normalized_token.startswith("/"):
            continue
        try:
            if workspace_runtime.backend == "local":
                await _local_visible_path(workspace_runtime, normalized_token)
            else:
                sandbox_visible_path(normalized_token)
        except ValueError:
            disallowed.append(token)
    if disallowed:
        raise ValueError("bash_run 只允许访问当前会话目录或当前用户有权访问的技能路径: " + ", ".join(disallowed[:3]))


async def run_bash_command(
    *,
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    workspace_runtime: ConversationWorkspaceRuntime,
) -> dict:
    """在当前工作区执行受限 Bash 命令。"""
    safe_command = _validate_command(command)
    await _validate_command_paths(safe_command, workspace_runtime)
    timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    if workspace_runtime.backend == "local":
        local_cwd = await _local_visible_path(workspace_runtime, cwd or "workspace")
        path_mapping = WorkspacePathMapping(
            workspace_root=workspace_runtime.workspace_root,
            runtime_skills_dir=workspace_runtime.runtime_skills_dir,
        )
        inner = "cd {cwd} && {command}".format(
            cwd=shlex.quote(str(local_cwd)),
            command=path_mapping.translate(safe_command),
        )
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-o",
            "pipefail",
            "-lc",
            inner,
            cwd=str(local_cwd),
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(local_cwd), "LANG": "C.UTF-8"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
            exit_code = int(process.returncode or 0)
            stdout_text = path_mapping.mask(stdout_bytes.decode("utf-8", errors="replace"))
            stderr_text = path_mapping.mask(stderr_bytes.decode("utf-8", errors="replace"))
            timed_out = False
        except TimeoutError:
            process.kill()
            await process.wait()
            exit_code, stdout_text, stderr_text, timed_out = 124, "", "command timed out", True
        cwd_value = to_visible_path(workspace_runtime.workspace_root, local_cwd)
        backend = "local"
        sandbox_id = ""
    else:
        session_ref = await workspace_runtime.get_sandbox_session_ref()
        session = session_ref.current
        if session is None:
            raise ValueError("OpenSandbox 执行环境未准备")
        sandbox_cwd = await _normalize_cwd(cwd, workspace_runtime)
        inner = "cd {cwd} && {command}".format(cwd=shlex.quote(sandbox_cwd), command=safe_command)
        exit_code, stdout_text, stderr_text = await run_command_in_sandbox(
            session=session,
            command=f"bash -o pipefail -lc {shlex.quote(inner)}",
            timeout_seconds=timeout,
        )
        timed_out = "timeout" in stderr_text.lower() or "timed out" in stderr_text.lower()
        cwd_value = sandbox_path_to_visible(sandbox_cwd)
        backend = OPENSANDBOX_BACKEND
        sandbox_id = session.sandbox_id
    stdout, stdout_truncated = truncate_output(stdout_text)
    stderr, stderr_truncated = truncate_output(stderr_text)
    success = (exit_code == 0) and not timed_out
    return {
        "success": success,
        "backend": backend,
        "sandbox_id": sandbox_id,
        "cwd": cwd_value,
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


def create_bash_run_tool(workspace_runtime: ConversationWorkspaceRuntime):
    @tool("bash_run")
    async def bash_run(command: str, cwd: str = "/mnt/user-data/workspace", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> str:
        """
        在当前会话工作区执行非交互命令，可用于 ls/find/python3/node/bash 等。

        - cwd 必须位于 /mnt/user-data/workspace、outputs、uploads 或当前已加载的只读 /mnt/skills 路径，默认 workspace。
        - command 必须是短生命周期非交互命令，不支持后台进程、服务管理或提权命令。
        - 生成最终产物前，先把脚本输出写到 workspace/，校验后复制或写入 outputs/。
        """
        try:
            payload = await run_bash_command(
                command=command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                workspace_runtime=workspace_runtime,
            )
            return _tool_result(payload)
        except Exception as exc:
            logger.error("bash_run 执行失败: %s", exc, exc_info=True)
            return f"bash_run 执行失败: {exc}"

    return bash_run
