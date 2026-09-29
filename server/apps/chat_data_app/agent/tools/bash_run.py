"""ChatData Bash tool for local and OpenSandbox workspaces."""

from __future__ import annotations

from langchain_core.tools import tool

from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.agent.workspace.workspace_sandbox_api import truncate_output

from .common import tool_result_payload

DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 120

# ============================================================================
# Bash 执行代码级总闸（优先级高于所有运行选项与 env）。
# 产品决定生产环境不开放 bash：即使 CHAT_DATA_DEFAULT_BASH_ENABLED 或用户覆盖被
# 误开启，bash_run 执行时也直接拒绝，命令不会触达运行时。工具注册链路保持原样
# （bash_enabled=true 时工具仍可见），这样模型拿到的是明确的「已禁用」结论并转告用户，
# 而不是工具凭空消失导致的静默失败。
# 这是刻意「下线而非删除」：恢复方式 = 把本常量改回 False 并重启进程，无需动 env。
# ============================================================================
BASH_RUN_DISABLED = True

BASH_RUN_DISABLED_MESSAGE = (
    "bash 执行功能已在服务端禁用，本次未执行任何命令。"
    "请直接告知用户该功能当前不可用；不要重试，也不要尝试用其他工具间接执行命令。"
)


def _disabled_payload(command: str) -> dict:
    """总闸生效时的统一返回结构：字段与正常执行结果同形，消费方无需特判。"""
    return {
        "success": False,
        "disabled": True,
        "backend": "disabled",
        "sandbox_id": None,
        "cwd": None,
        "command": str(command or ""),
        "exit_code": None,
        "timed_out": False,
        "timeout_seconds": 0,
        "stdout": "",
        "stderr": "",
        "stdout_truncated": False,
        "stderr_truncated": False,
        "message": BASH_RUN_DISABLED_MESSAGE,
        "next_required_action": "告知用户 bash 执行功能已被禁用；不要重试，也不要寻找替代执行途径。",
    }


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
    # 代码级总闸：优先于一切运行选项，命中时不做任何校验、不触达命令
    if BASH_RUN_DISABLED:
        return _disabled_payload(command)
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
    @tool("bash_run", parse_docstring=True)
    async def bash_run(command: str, cwd: str = "/mnt/user-data/workspace", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, intent: str = "") -> str:
        """在当前会话工作区执行非交互命令，可用于 ls/find/python3/node/bash 等。

        command 必须是短生命周期非交互命令：不支持后台进程、服务管理或提权命令。

        Args:
            command: 要执行的非交互命令字符串。
            cwd: 工作目录，必须是规范形式路径：/mnt/user-data/workspace、/mnt/user-data/outputs、/mnt/user-data/uploads 或已加载的只读 /mnt/skills/<技能名>/...；默认 /mnt/user-data/workspace。
            timeout_seconds: 命令超时秒数。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        payload = await run_bash_command(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            workspace_runtime=workspace_runtime,
        )
        return tool_result_payload("bash_run", payload)

    return bash_run
