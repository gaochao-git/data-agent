"""ChatData Bash tool for local and OpenSandbox workspaces."""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.agent.workspace.workspace_execution import truncate_output
from .common import json_dumps

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 120


def _tool_result(payload: dict) -> str:
    return "[bash_run]\n" + json_dumps(payload)


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


async def run_bash_command(
    *,
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    workspace_runtime: ConversationWorkspaceRuntime,
) -> dict:
    """在当前工作区执行受限 Bash 命令。"""
    safe_command = _validate_command(command)
    await workspace_runtime.validate_bash_paths(command=safe_command)
    try:
        requested_timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        requested_timeout = DEFAULT_TIMEOUT_SECONDS
    # 0/负数按非法输入 clamp 到下限 1，而不是被 or 当成"未提供"落到默认值。
    timeout = max(1, min(requested_timeout, MAX_TIMEOUT_SECONDS))
    execution = await workspace_runtime.run_bash(
        command=safe_command, cwd=cwd, timeout_seconds=timeout
    )
    stdout, stdout_truncated = truncate_output(str(execution["stdout"]))
    stderr, stderr_truncated = truncate_output(str(execution["stderr"]))
    success = (int(execution["exit_code"]) == 0) and not bool(execution["timed_out"])
    return {
        "success": success,
        "backend": execution["backend"],
        "sandbox_id": execution["sandbox_id"],
        "cwd": execution["cwd"],
        "command": safe_command,
        "exit_code": execution["exit_code"],
        "timed_out": execution["timed_out"],
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

        - cwd 必须是规范形式路径：/mnt/user-data/workspace、/mnt/user-data/outputs、/mnt/user-data/uploads 或已加载的只读 /mnt/skills/<技能名>/...；默认 /mnt/user-data/workspace。
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
            raise

    return bash_run
