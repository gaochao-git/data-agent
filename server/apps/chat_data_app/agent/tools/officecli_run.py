"""ChatData officecli_run tool: run OfficeCLI via exec, without bash_run."""

from __future__ import annotations

from langchain_core.tools import StructuredTool, tool

from apps.chat_data_app.agent.workspace.officecli_adapter import (
    OfficeCliCommand,
    command_path_values,
    parse_officecli_args,
)
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime
from apps.chat_data_app.agent.workspace.workspace_path_policy import WORKSPACE_PATH_POLICY
from apps.chat_data_app.agent.workspace.workspace_sandbox_api import truncate_output
from utils.logger import get_logger

from .common import tool_result_payload

logger = get_logger(__name__)


DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120

_UPLOADS_PREFIXES = ("/mnt/user-data/uploads",)


def _under_uploads(path: str) -> bool:
    """判断虚拟路径是否落在 uploads 只读区内。"""
    p = str(path or "").strip()
    return any(p == prefix or p.startswith(f"{prefix}/") for prefix in _UPLOADS_PREFIXES)


def _validate_save_stdout_path(path: str) -> str:
    """复用统一路径策略校验 save_stdout_to：只允许 workspace 下可写路径，返回规范形式。

    office 转存文本是 diff 的临时产物，不进 outputs 交付区；semantic 只读快照、
    相对形式、越界等由 WORKSPACE_PATH_POLICY 统一拦截并给出可自我纠正的报错。
    """
    logical = WORKSPACE_PATH_POLICY.parse(path)
    if logical.kind != "workspace":
        raise ValueError(f"save_stdout_to 只允许写入 /mnt/user-data/workspace/: {path}")
    WORKSPACE_PATH_POLICY.assert_writable(logical)
    return WORKSPACE_PATH_POLICY.to_sandbox_path(logical)


def _validate_args(args: list[str]) -> list[str]:
    return list(parse_officecli_args(args).args)


def _guard_uploads_readonly(command: OfficeCliCommand, cwd: str | None) -> None:
    """修改/写入类子命令不允许落在 uploads 只读区，否则抛 ValueError。

    读源允许；拦截后提示用 file_copy 复制到 workspace，避免 agent 直接改用户原始上传文件。
    """
    if not (command.mutates or command.write_indices):
        return
    effective_cwd = str(cwd or "/mnt/user-data/workspace").strip()
    if command.mutates and _under_uploads(effective_cwd):
        raise ValueError(
            "uploads 目录为只读，不允许写入或修改。请先用 file_copy 将文件复制到 /mnt/user-data/workspace 后再操作。"
        )
    for index, path in command_path_values(command, effective_cwd):
        if index in command.write_indices and _under_uploads(path):
            raise ValueError(
                f"uploads 目录为只读，不允许写入或修改该路径: {path}。"
                "请先用 file_copy 将文件复制到 /mnt/user-data/workspace 后再操作。"
            )


async def run_officecli_command(
    *,
    args: list[str],
    cwd: str | None,
    timeout_seconds: int,
    workspace_runtime: ConversationWorkspaceRuntime,
    save_stdout_to: str = "",
) -> dict:
    """在会话工作区以 exec 方式运行 OfficeCLI，返回与 bash_run 对齐的结果结构。

    save_stdout_to 非空且命令成功时，把未截断的完整 stdout 原样写入 workspace 下该路径
    （整体覆盖），供 view text/annotated 等 stdout-only 输出落盘（它们的 -o 会被
    officecli 静默忽略）；命令失败时跳过转存，由 success=False 与 stderr 主路径报错。
    转存写入失败不改变命令成功结论：payload 里 saved_stdout_to 为空、saved_stdout_error
    携带原因，stdout/stderr 照常返回，不丢弃已执行命令的结果。
    """
    command = parse_officecli_args(args)
    safe_args = list(command.args)
    # uploads 为只读区：修改/写入类子命令不允许操作 uploads 路径（读源允许），见 helper。
    _guard_uploads_readonly(command, cwd)
    timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    # save_stdout_to 在执行前校验：路径非法 fail-fast，不白跑一趟命令（与测试口径一致）。
    save_target = ""
    if save_stdout_to:
        save_target = _validate_save_stdout_path(save_stdout_to)
    execution = await workspace_runtime.run_officecli(args=safe_args, cwd=cwd, timeout_seconds=timeout)
    raw_stdout = str(execution["stdout"])
    stdout, stdout_truncated = truncate_output(raw_stdout)
    stderr, stderr_truncated = truncate_output(str(execution["stderr"]))
    success = (int(execution["exit_code"]) == 0) and not bool(execution["timed_out"])
    saved_stdout_to = ""
    saved_stdout_error = ""
    if save_target and success:
        try:
            written = await workspace_runtime.write_text_file(
                file_path=save_target, content=raw_stdout, mode="overwrite"
            )
            saved_stdout_to = str(written.get("file_path") or save_target)
        except Exception as exc:
            # 命令已成功执行，转存失败不能让它整笔失败丢掉 stdout/stderr；
            # 记录真实原因进 payload，由模型决定换路径重试还是直接用 stdout。
            logger.warning("officecli 转存 stdout 失败: target=%s error=%s", save_target, exc, exc_info=True)
            saved_stdout_error = f"转存失败: {exc}"
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
        "saved_stdout_to": saved_stdout_to,
        "saved_stdout_error": saved_stdout_error,
        "next_required_action": (
            "编辑后执行 officecli validate 与 view 校验；确认无误再继续或调用 present_artifacts。"
            if success
            else "检查子命令、文件路径与参数后重试；officecli 报错见 stderr。"
        ),
    }


def create_officecli_run_tool(workspace_runtime: ConversationWorkspaceRuntime) -> StructuredTool:
    @tool("officecli_run", parse_docstring=True)
    async def officecli_run(
        command_args: list[str],
        cwd: str = "/mnt/user-data/workspace",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        save_stdout_to: str = "",
        intent: str = "",
    ) -> str:
        """在会话工作区运行 OfficeCLI 编辑/查询 Office 文件（docx/xlsx/pptx），通过 exec 直接执行，不依赖 bash_run。

        - DOM 选择器（如 /slide[1]、/Sheet1/A1、/body/p[1]）是文档内部定位，不是文件路径。
        - 修改上传文件先 file_copy 到 workspace。
        - view 的 text/annotated 模式只输出到 stdout，-o 对它们无效；要落盘成文件请用 save_stdout_to。
        - 完整用法、编辑工作流与样式示例见 officecli 技能（SKILL.md），文档交付前必读。

        Args:
            command_args: OfficeCLI 子命令与参数数组，例如 ["view", "/mnt/user-data/workspace/报告.docx", "outline"]；每个元素是独立参数，不要加引号或拼成一条命令；第一个元素不能是 plugins/watch/unwatch/open/close/mcp/install/skills。
            cwd: 工作目录，默认 /mnt/user-data/workspace。
            timeout_seconds: 命令超时秒数。
            save_stdout_to: 可选。命令成功后把完整 stdout 原样写入该路径（整体覆盖）；必须是 /mnt/user-data/workspace/ 开头的完整路径；配合 file_diff 对比 Office 文件时，先用 ["view", 文件, "text"] 加本参数生成转存文本。空表示不保存。
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        payload = await run_officecli_command(
            args=command_args,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            workspace_runtime=workspace_runtime,
            save_stdout_to=save_stdout_to,
        )
        return tool_result_payload("officecli_run", payload)

    return officecli_run
