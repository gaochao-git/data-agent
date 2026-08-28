"""ChatData officecli_run tool: run OfficeCLI via exec, without bash_run."""

from __future__ import annotations

from langchain_core.tools import tool

from utils.logger import get_logger
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.agent.workspace.officecli_adapter import (
    command_path_values,
    parse_officecli_args,
)
from apps.chat_data_app.agent.workspace.workspace_execution import truncate_output
from .common import json_dumps

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120

_UPLOADS_PREFIXES = (
    "/mnt/user-data/uploads",
)


def _under_uploads(path: str) -> bool:
    """判断虚拟路径是否落在 uploads 只读区内。"""
    p = str(path or "").strip()
    for prefix in _UPLOADS_PREFIXES:
        if p == prefix or p.startswith(f"{prefix}/"):
            return True
    return False


def _tool_result(payload: dict) -> str:
    return "[officecli_run]\n" + json_dumps(payload)


def _validate_args(args: list[str]) -> list[str]:
    return list(parse_officecli_args(args).args)


async def run_officecli_command(
    *,
    args: list[str],
    cwd: str | None,
    timeout_seconds: int,
    workspace_runtime: ConversationWorkspaceRuntime,
) -> dict:
    """在会话工作区以 exec 方式运行 OfficeCLI，返回与 bash_run 对齐的结果结构。"""
    command = parse_officecli_args(args)
    safe_args = list(command.args)
    # uploads 为只读区：修改/写入类子命令不允许操作 uploads 路径（读源允许）。
    # 拦截后提示用 file_copy 复制到 workspace，避免 agent 直接改用户原始上传文件。
    if command.mutates or command.write_indices:
        effective_cwd = str(cwd or "/mnt/user-data/workspace").strip()
        if command.mutates and _under_uploads(effective_cwd):
            raise ValueError(
                "uploads 目录为只读，不允许写入或修改。请先用 file_copy 将文件复制到 "
                "/mnt/user-data/workspace 后再操作。"
            )
        for index, path in command_path_values(command, effective_cwd):
            if index in command.write_indices and _under_uploads(path):
                raise ValueError(
                    f"uploads 目录为只读，不允许写入或修改该路径: {path}。"
                    "请先用 file_copy 将文件复制到 /mnt/user-data/workspace 后再操作。"
                )
    timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    execution = await workspace_runtime.run_officecli(
        args=safe_args, cwd=cwd, timeout_seconds=timeout
    )
    stdout, stdout_truncated = truncate_output(str(execution["stdout"]))
    stderr, stderr_truncated = truncate_output(str(execution["stderr"]))
    success = (int(execution["exit_code"]) == 0) and not bool(execution["timed_out"])
    return {
        "success": success,
        "backend": execution["backend"],
        "sandbox_id": execution.get("sandbox_id", ""),
        "cwd": execution["cwd"],
        "args": execution["args"],
        "exit_code": execution["exit_code"],
        "timed_out": execution["timed_out"],
        "timeout_seconds": timeout,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "next_required_action": (
            "编辑后执行 officecli validate 与 view 校验；确认无误再继续或调用 present_artifacts。"
            if success else "检查子命令、文件路径与参数后重试；officecli 报错见 stderr。"
        ),
    }


def create_officecli_run_tool(workspace_runtime: ConversationWorkspaceRuntime):
    @tool("officecli_run")
    async def officecli_run(
        command_args: list[str],
        cwd: str = "/mnt/user-data/workspace",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        """在会话工作区运行 OfficeCLI 编辑/查询 Office 文件（docx/xlsx/pptx），通过 exec 直接执行，不依赖 bash_run。

        - command_args：OfficeCLI 子命令与参数数组，例如 ["view", "/mnt/user-data/workspace/报告.docx", "outline"]。
          每个元素是一个独立参数，不要加引号、不要拼成一整条命令；路径含空格或选择器含 [N] 都照原样写。
          第一个元素不能是禁用子命令：plugins / watch / unwatch / open / close / mcp / install / skills。
          OfficeCLI 的 DOM 选择器（如 /slide[1]、/Sheet1/A1、/body/p[1]）是文档内部定位，不是文件路径。
        - cwd 必须位于 /mnt/user-data/workspace、outputs 或 uploads；默认 workspace。
        - uploads 为只读区：view/get/query/validate 等只读子命令可读取 uploads 文件，
          但 add/set/remove/save/create 等修改类子命令禁止写入或修改 uploads 路径；
          如需编辑上传文件，先用 file_copy 复制到 /mnt/user-data/workspace 再操作。
        - 不支持后台或监听模式的子命令。"""
        try:
            payload = await run_officecli_command(
                args=command_args,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                workspace_runtime=workspace_runtime,
            )
            return _tool_result(payload)
        except Exception as exc:
            logger.error("officecli_run 执行失败: %s", exc, exc_info=True)
            return f"officecli_run 执行失败: {exc}"

    return officecli_run
