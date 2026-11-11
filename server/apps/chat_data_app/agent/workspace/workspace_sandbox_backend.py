"""ChatData 会话工作区 sandbox 后端：OpenSandbox 远程文件系统适配。"""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from apps.chat_data_app.agent.workspace.officecli_adapter import command_path_values, parse_officecli_args
from apps.chat_data_app.agent.workspace.runtime import (
    ConversationWorkspaceRuntime,
    _absolute_command_path_tokens,
)
from apps.chat_data_app.agent.workspace.workspace_ops import (
    sandbox_artifact_payload,
    sandbox_copy_path,
    sandbox_create_dir,
    sandbox_delete_internal_text_file,
    sandbox_delete_path,
    sandbox_list_dir,
    sandbox_move_path,
    sandbox_read_text_file,
    sandbox_render_office_html,
    sandbox_replace_text_file,
    sandbox_search_files,
    sandbox_write_internal_text_files,
    sandbox_write_text_file,
    sandbox_write_tool_result,
)
from apps.chat_data_app.agent.workspace.workspace_sandbox_api import (
    SandboxExecutionSessionRef,
    run_command_in_sandbox,
)
from apps.chat_data_app.agent.workspace.workspace_sandbox_paths import (
    _is_managed_semantic_model_sandbox_path,
    sandbox_path_to_visible,
    sandbox_visible_path,
    sandbox_workspace_dir,
)
from apps.chat_data_app.agent.workspace.workspace_search import sandbox_grep_text
from apps.chat_data_app.agent.workspace.workspace_session import (
    OPENSANDBOX_BACKEND,
    get_workspace_execution_session,
    invalidate_workspace_execution_session,
    read_sandbox_lifetime_seconds,
)
from apps.chat_data_app.agent.workspace.workspace_sync import (
    recover_pending_persistence,
    sync_sandbox_working_trees,
)
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT

logger = get_logger(__name__)

_RENEW_ENABLED_ENV = "OPENSANDBOX_RENEW_ENABLED"
# 续期最小间隔：模型调用/工具执行共用。刚续过就跳过，避免每个工具、每轮模型都多一次
# 控制面请求；审批中断或长间隔恢复后 _last_renew_at 较旧，会先续期兜底。
_RENEW_MIN_INTERVAL_SECONDS = 30


def _renew_enabled() -> bool:
    raw = str(os.getenv(_RENEW_ENABLED_ENV) or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class SandboxWorkspaceBackend:
    """OpenSandbox backend; all remote filesystem calls stay behind this adapter."""

    name: str = "sandbox"
    session_ref: SandboxExecutionSessionRef = field(default_factory=SandboxExecutionSessionRef)
    _session_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _renew_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _last_renew_at: float = field(default=0.0, init=False, repr=False)
    _renew_failure_logged: bool = field(default=False, init=False, repr=False)
    _session_error: str = field(default="", init=False, repr=False)

    async def _sandbox_alive(self, sandbox: Any) -> bool:
        """续期失败时探活：控制面 5xx/网络抖动不应误杀仍存活的沙箱。"""
        get_info = getattr(sandbox, "get_info", None)
        if get_info is None:
            return False
        try:
            await get_info()
            return True
        except Exception:
            return False

    async def _renew_current(self, session: Any) -> bool:
        """把当前 sandbox 过期时间推到 now + read_sandbox_lifetime_seconds()。

        按 ``_RENEW_MIN_INTERVAL_SECONDS`` 节流（模型调用与工具执行共用）；返回
        False 表示续期失败（沙箱可能已过期/被回收），调用方需要重建。
        """
        if not _renew_enabled():
            return True
        sandbox = getattr(session, "sandbox", None)
        renew = getattr(sandbox, "renew", None)
        if renew is None:
            return True
        now = time.monotonic()
        if now - self._last_renew_at < _RENEW_MIN_INTERVAL_SECONDS:
            return True
        async with self._renew_lock:
            now = time.monotonic()
            if now - self._last_renew_at < _RENEW_MIN_INTERVAL_SECONDS:
                return True
            try:
                await renew(timedelta(seconds=read_sandbox_lifetime_seconds()))
                self._last_renew_at = time.monotonic()
                self._renew_failure_logged = False
                return True
            except Exception:
                alive = await self._sandbox_alive(sandbox)
                if not self._renew_failure_logged:
                    if alive:
                        logger.warning("OpenSandbox 沙箱续期失败但实例仍存活，本次跳过续期", exc_info=True)
                    else:
                        logger.warning("OpenSandbox 沙箱续期失败且实例不可用，将在下次访问时重建", exc_info=True)
                    self._renew_failure_logged = True
                return alive

    async def renew_sandbox_async(self) -> None:
        """模型调用前后续期：只在已有 sandbox 会话时续期，不提前创建沙箱。"""
        session = self.session_ref.current
        if session is None:
            return
        await self._renew_current(session)

    async def _renew_before_tool(self) -> bool:
        """工具执行前续期；失败返回 False（调用方重建会话）。"""
        session = self.session_ref.current
        if session is None:
            return True
        return await self._renew_current(session)

    async def _reset_session(self) -> None:
        """丢弃失效 session 并清理缓存，下一次访问会重新创建沙箱。"""
        stale = self.session_ref.current
        self.session_ref.current = None
        self._last_renew_at = 0.0
        self._renew_failure_logged = False
        if stale is None:
            return
        try:
            await recover_pending_persistence(stale)
        except Exception:
            logger.warning("OpenSandbox 失效会话回写 pending 变更失败，继续重建", exc_info=True)
        try:
            await invalidate_workspace_execution_session(stale)
        except Exception:
            logger.warning("OpenSandbox 失效会话清理失败", exc_info=True)

    async def _session_ref(self, runtime: ConversationWorkspaceRuntime) -> SandboxExecutionSessionRef:
        if self.session_ref.current is not None:
            if await self._renew_before_tool():
                return self.session_ref
            # 审批等待超过 lifetime 等场景：续期失败说明沙箱已过期，重建后再执行工具。
            await self._reset_session()
        async with self._session_lock:
            if self.session_ref.current is not None:
                return self.session_ref
            if self._session_error:
                raise ValueError(self._session_error)
            await runtime._ensure_snapshot_async()
            try:
                self.session_ref.current = await get_workspace_execution_session(
                    runtime.workspace_root,
                    runtime.user_id,
                    runtime_skills_dir=runtime.runtime_skills_dir,
                )
            except Exception as exc:
                self._session_error = str(exc) or "OpenSandbox 执行环境初始化失败"
                raise
            self._last_renew_at = time.monotonic()
        return self.session_ref

    async def read_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        offset: int,
        limit: int,
        max_chars: int,
    ) -> dict[str, Any]:
        return await sandbox_read_text_file(
            session_ref=await self._session_ref(runtime),
            file_path=file_path,
            offset=offset,
            limit=limit,
            max_chars=max_chars,
        )

    async def write_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
        mode: str,
    ) -> dict[str, Any]:
        return await sandbox_write_text_file(
            session_ref=await self._session_ref(runtime), file_path=file_path, content=content, mode=mode
        )

    async def write_internal_text_files(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        entries: list[tuple[str, str]],
    ) -> list[dict[str, Any]]:
        return await sandbox_write_internal_text_files(
            session_ref=await self._session_ref(runtime),
            entries=entries,
        )

    async def delete_internal_text_files(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
    ) -> dict[str, Any]:
        return await sandbox_delete_internal_text_file(
            session_ref=await self._session_ref(runtime),
            path=path,
        )

    async def write_tool_result(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
    ) -> dict[str, Any]:
        return await sandbox_write_tool_result(
            session_ref=await self._session_ref(runtime),
            file_path=file_path,
            content=content,
        )

    async def replace_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]:
        return await sandbox_replace_text_file(
            session_ref=await self._session_ref(runtime),
            file_path=file_path,
            old_content=old_content,
            new_content=new_content,
        )

    async def artifact_payload(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
    ) -> dict[str, Any]:
        return await sandbox_artifact_payload(session_ref=await self._session_ref(runtime), file_path=file_path)

    async def render_office_html(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        max_chars: int,
    ) -> dict[str, Any]:
        return await sandbox_render_office_html(
            session_ref=await self._session_ref(runtime),
            file_path=file_path,
            max_chars=max_chars,
        )

    async def list_dir(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        max_depth: int,
    ) -> list[dict[str, Any]]:
        return await sandbox_list_dir(session_ref=await self._session_ref(runtime), path=path, max_depth=max_depth)

    async def create_dir(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
    ) -> dict[str, Any]:
        return await sandbox_create_dir(session_ref=await self._session_ref(runtime), path=path)

    async def delete_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        recursive: bool,
        expected_type: Literal["file", "dir"],
    ) -> dict[str, Any]:
        return await sandbox_delete_path(
            session_ref=await self._session_ref(runtime),
            path=path,
            recursive=recursive,
            expected_type=expected_type,
        )

    async def move_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]:
        return await sandbox_move_path(
            session_ref=await self._session_ref(runtime),
            source_path=source_path,
            destination_path=destination_path,
        )

    async def copy_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]:
        return await sandbox_copy_path(
            session_ref=await self._session_ref(runtime),
            source_path=source_path,
            destination_path=destination_path,
        )

    async def search_files(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        pattern: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        return await sandbox_search_files(
            session_ref=await self._session_ref(runtime),
            path=path,
            pattern=pattern,
            max_results=max_results,
        )

    async def grep_text(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        patterns: list[str],
        glob: str,
        max_results: int,
        max_chars: int,
        use_regex: bool = False,
        ignore_case: bool = False,
        context_lines: int = 0,
        output_mode: str = "content",
    ) -> dict[str, Any]:
        return await sandbox_grep_text(
            session_ref=await self._session_ref(runtime),
            path=path,
            patterns=patterns,
            glob=glob,
            max_results=max_results,
            max_chars=max_chars,
            use_regex=use_regex,
            ignore_case=ignore_case,
            context_lines=context_lines,
            output_mode=output_mode,
        )

    async def validate_bash_paths(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
    ) -> None:
        invalid = []
        for token in _absolute_command_path_tokens(command):
            try:
                sandbox_visible_path(token)
            except ValueError:
                invalid.append(token)
                continue
            # 与 local 后端同口径：语义模型快照只读，bash 无法区分读/写意图，
            # 一律不开放（沙箱里是只读挂载，但校验口径保持一致）。
            if _is_managed_semantic_model_sandbox_path(token):
                invalid.append(token)
        if invalid:
            raise ValueError("bash_run 只允许访问当前会话目录或当前用户有权访问的技能路径: " + ", ".join(invalid[:3]))

    async def run_bash(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        sandbox_cwd = sandbox_visible_path(str(cwd or sandbox_workspace_dir()))
        if _is_managed_semantic_model_sandbox_path(sandbox_cwd):
            raise ValueError("bash_run 不允许将 cwd 设置为系统只读语义模型快照目录；请用 file_read / file_grep 读取")
        session_ref = await self._session_ref(runtime)
        session = session_ref.current
        if session is None:
            raise ValueError("OpenSandbox 执行环境未准备")
        async with session.operation_lock:
            inner = f"cd {shlex.quote(sandbox_cwd)} && {command}"
            exit_code, stdout, stderr = await run_command_in_sandbox(
                session=session,
                command=f"bash -o pipefail -lc {shlex.quote(inner)}",
                timeout_seconds=timeout_seconds,
            )
            try:
                # 命令对文件的副作用不可预知（unknown file effects），同步并归因间接写入。
                changed_files = await sync_sandbox_working_trees(session)
            except Exception as exc:
                raise ValueError(f"Bash 执行后的工作区同步失败: {exc}") from exc
        return {
            "backend": OPENSANDBOX_BACKEND,
            "sandbox_id": session.sandbox_id,
            "cwd": sandbox_path_to_visible(sandbox_cwd),
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": "timeout" in stderr.lower() or "timed out" in stderr.lower(),
            "changed_files": changed_files,
        }

    async def run_officecli(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        args: list[str],
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """沙箱后端：OpenSandbox SDK 仅接受字符串命令，故将 argv 数组 shlex.quote 后拼成
        ``officecli ...`` 字符串，经 ``run_command_in_sandbox`` 执行；执行后同步工作区。

        沙箱内 ``/mnt/user-data/*`` 即真实挂载路径，无需翻译；仅按沙箱边界校验真实路径参数
        （排除 OfficeCLI DOM 选择器），拒绝越界访问。flag 内嵌路径（如 ``--out=/mnt/...``）
        在沙箱内本就是合法路径，不做额外转换。技能路径与本地后端一样拒绝，保证两后端行为一致。
        """
        command_spec = parse_officecli_args(args)
        visible_cwd = str(cwd or sandbox_workspace_dir())
        if visible_cwd.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            raise ValueError("officecli_run 不允许将 cwd 设置为技能目录")
        sandbox_cwd = sandbox_visible_path(visible_cwd)
        for index, path in command_path_values(command_spec, visible_cwd):
            if path.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
                raise ValueError(f"officecli_run 不支持技能路径: {path}")
            sandbox_visible_path(path, writable=index in command_spec.write_indices)
        session_ref = await self._session_ref(runtime)
        session = session_ref.current
        if session is None:
            raise ValueError("OpenSandbox 执行环境未准备")
        async with session.operation_lock:
            quoted = " ".join(shlex.quote(str(a)) for a in command_spec.args)
            command = f"cd {shlex.quote(sandbox_cwd)} && officecli {quoted}"
            exit_code, stdout, stderr = await run_command_in_sandbox(
                session=session,
                command=command,
                timeout_seconds=timeout_seconds,
            )
            if exit_code == 0 and (command_spec.mutates or command_spec.write_indices):
                try:
                    # 写入类 officecli 命令的间接产物同步并归因；只读命令不产生变更。
                    changed_files = await sync_sandbox_working_trees(session)
                except Exception as exc:
                    raise ValueError(f"officecli 执行后的工作区同步失败: {exc}") from exc
                changed_files_payload = changed_files
            else:
                changed_files_payload = None
        result = {
            "backend": OPENSANDBOX_BACKEND,
            "sandbox_id": session.sandbox_id,
            "cwd": sandbox_path_to_visible(sandbox_cwd),
            "args": args,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": "timeout" in stderr.lower() or "timed out" in stderr.lower(),
        }
        if changed_files_payload is not None:
            result["changed_files"] = changed_files_payload
        return result
