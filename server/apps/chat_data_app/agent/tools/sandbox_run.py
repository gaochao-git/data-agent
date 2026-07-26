"""ChatData Bash tool for local and OpenSandbox workspaces."""

from __future__ import annotations

import re
import shlex
import asyncio
import os
from pathlib import PurePosixPath

from langchain_core.tools import tool

from utils.logger import get_logger
from ...services.conversation_workspace import (
    ConversationWorkspaceRuntime,
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
    return "[sandbox_run]\n" + json_dumps(payload)


def _normalize_cwd(cwd: str | None, workspace_runtime: ConversationWorkspaceRuntime) -> str:
    raw = str(cwd or "").strip()
    if not raw:
        return sandbox_workspace_dir()
    return sandbox_visible_path(raw, runtime_skill_mounts=tuple(workspace_runtime.runtime_skill_mounts()))


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


def _local_visible_path(workspace_runtime: ConversationWorkspaceRuntime, path: str):
    normalized = str(path or "").strip()
    if normalized.startswith("/mnt/skills/"):
        return workspace_runtime.resolve_readonly_skill_path(normalized)[0]
    return resolve_workspace_path(workspace_runtime.workspace_root, normalized)


def _validate_command_paths(command: str, workspace_runtime: ConversationWorkspaceRuntime) -> None:
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
                _local_visible_path(workspace_runtime, normalized_token)
            else:
                sandbox_visible_path(
                    normalized_token,
                    runtime_skill_mounts=tuple(workspace_runtime.runtime_skill_mounts()),
                )
        except ValueError:
            disallowed.append(token)
    if disallowed:
        raise ValueError("sandbox_run 只允许访问当前会话目录或当前已加载技能路径: " + ", ".join(disallowed[:3]))


def _replace_local_skill_paths(command: str, workspace_runtime: ConversationWorkspaceRuntime) -> str:
    """将 local 开发模式使用的虚拟技能路径映射到已授权的物理目录。"""
    rewritten = command
    for mount in workspace_runtime.runtime_skill_mounts():
        visible_root = mount.visible_root
        source_root = str(mount.source_dir)
        rewritten = rewritten.replace(f'"{visible_root}', f'"{source_root}')
        rewritten = rewritten.replace(f"'{visible_root}", f"'{source_root}")
        rewritten = rewritten.replace(visible_root, shlex.quote(source_root))
    return rewritten


_WORKSPACE_VIRTUAL_RE = re.compile(r"/mnt/user-data/(workspace|outputs|uploads)(?![\w])")
# 裸别名 /workspace、/outputs、/uploads 仅在路径边界处匹配：
# 前置负向断言要求 / 之前不是单词字符也不是斜杠（空格、引号、括号、= 等之前可以，字符串开头也可以），
# 这样物理根内部的 server/uploads 之类路径段不会被误伤；后置负向断言要求之后不是单词字符
# （允许紧跟 / 或结尾或标点），避免把一个更长的段误截断。
_WORKSPACE_ALIAS_RE = re.compile(r"(?<![\w/])/(workspace|outputs|uploads)(?![\w])")


def _replace_local_workspace_paths(command: str, workspace_root: str) -> str:
    """将虚拟工作区路径（/mnt/user-data/* 及别名 /workspace 等）替换为物理绝对路径，供本地 subprocess 执行。"""
    root = workspace_root.rstrip("/")
    # 防御：workspace_root 是物理根，绝不应包含虚拟前缀 /mnt/user-data/。
    # 若包含，说明 DATA_ROOT/CHAT_DATA_HOME 被误配成含 sandbox 虚拟挂载点的路径，
    # 此时继续替换会把物理根内部的虚拟前缀反复展开，导致路径指数膨胀。
    if "/mnt/user-data/" in root:
        raise ValueError(
            "workspace_root 配置异常：物理根不应包含 /mnt/user-data/，"
            "请检查 DATA_ROOT/CHAT_DATA_HOME 环境变量是否误用了 sandbox 虚拟挂载点路径"
        )
    # 1) 先把裸别名（/workspace 等）规范成 /mnt/user-data/ 形式。
    #    使用边界断言，避免误匹配物理根内部的 server/uploads 之类路径段。
    command = _WORKSPACE_ALIAS_RE.sub(r"/mnt/user-data/\1", command)
    # 2) 再统一把 /mnt/user-data/(workspace|outputs|uploads) 翻译成物理路径。
    #    干净配置下 root 不含 /mnt/user-data/，单次替换即可，不会自引用膨胀。
    command = _WORKSPACE_VIRTUAL_RE.sub(lambda m: f"{root}/{m.group(1)}", command)
    return command


async def run_sandbox_command(
    *,
    command: str,
    cwd: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    workspace_runtime: ConversationWorkspaceRuntime,
) -> dict:
    """在当前工作区执行受限 Bash 命令。"""
    safe_command = _validate_command(command)
    _validate_command_paths(safe_command, workspace_runtime)
    timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    if workspace_runtime.backend == "local":
        local_cwd = _local_visible_path(workspace_runtime, cwd or "workspace")
        inner = "cd {cwd} && {command}".format(
            cwd=shlex.quote(str(local_cwd)),
            command=_replace_local_workspace_paths(
                _replace_local_skill_paths(safe_command, workspace_runtime),
                workspace_runtime.workspace_root,
            ),
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
            stdout_text = stdout_bytes.decode("utf-8", errors="replace")
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
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
        sandbox_cwd = _normalize_cwd(cwd, workspace_runtime)
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


def create_sandbox_run_tool(workspace_runtime: ConversationWorkspaceRuntime):
    @tool("sandbox_run")
    async def sandbox_run(command: str, cwd: str = "/mnt/user-data/workspace", timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> str:
        """
        在当前会话工作区执行非交互命令，可用于 ls/find/python3/node/bash 等。

        - cwd 必须位于 /mnt/user-data/workspace、outputs、uploads 或当前已加载的只读 /mnt/skills 路径，默认 workspace。
        - command 必须是短生命周期非交互命令，不支持后台进程、服务管理或提权命令。
        - 生成最终产物前，先把脚本输出写到 workspace/，校验后复制或写入 outputs/。
        """
        try:
            payload = await run_sandbox_command(
                command=command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                workspace_runtime=workspace_runtime,
            )
            return _tool_result(payload)
        except Exception as exc:
            logger.error("sandbox_run 执行失败: %s", exc, exc_info=True)
            return f"sandbox_run 执行失败: {exc}"

    return sandbox_run
