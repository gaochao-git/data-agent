"""ChatData 会话工作区 sandbox 后端：OpenSandbox 远程文件系统适配（自 conversation_workspace 拆出）。"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass, field
from typing import Any, Literal

from utils.skill_paths import SKILLS_VIRTUAL_ROOT

from .conversation_workspace import (
    ConversationWorkspaceRuntime,
    _absolute_command_path_tokens,
)
from apps.chat_data_app.agent.workspace.officecli_adapter import command_path_values, parse_officecli_args
from apps.chat_data_app.agent.workspace.workspace_execution import (
    OPENSANDBOX_BACKEND,
    SandboxExecutionSessionRef,
    get_workspace_execution_session,
    run_command_in_sandbox,
    sandbox_artifact_payload,
    sandbox_copy_path,
    sandbox_create_dir,
    sandbox_delete_path,
    sandbox_grep_text,
    sandbox_list_dir,
    sandbox_move_path,
    sandbox_path_to_visible,
    sandbox_read_text_file,
    sandbox_render_office_html,
    sandbox_replace_text_file,
    sandbox_search_files,
    sandbox_visible_path,
    sandbox_workspace_dir,
    sandbox_write_internal_text_files,
    sandbox_write_text_file,
    sandbox_write_tool_result,
    sync_sandbox_working_trees,
)


@dataclass
class SandboxWorkspaceBackend:
    """OpenSandbox backend; all remote filesystem calls stay behind this adapter."""

    name: str = "sandbox"
    session_ref: SandboxExecutionSessionRef = field(default_factory=SandboxExecutionSessionRef)
    _session_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _session_error: str = field(default="", init=False, repr=False)

    async def _session_ref(self, runtime: ConversationWorkspaceRuntime) -> SandboxExecutionSessionRef:
        if self.session_ref.current is not None:
            return self.session_ref
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
        return await sandbox_artifact_payload(
            session_ref=await self._session_ref(runtime), file_path=file_path
        )

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
        return await sandbox_list_dir(
            session_ref=await self._session_ref(runtime), path=path, max_depth=max_depth
        )

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
    ) -> dict[str, Any]:
        return await sandbox_grep_text(
            session_ref=await self._session_ref(runtime),
            path=path,
            patterns=patterns,
            glob=glob,
            max_results=max_results,
            max_chars=max_chars,
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
        if invalid:
            raise ValueError(
                "bash_run 只允许访问当前会话目录或当前用户有权访问的技能路径: "
                + ", ".join(invalid[:3])
            )

    async def run_bash(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        sandbox_cwd = sandbox_visible_path(str(cwd or sandbox_workspace_dir()))
        session_ref = await self._session_ref(runtime)
        session = session_ref.current
        if session is None:
            raise ValueError("OpenSandbox 执行环境未准备")
        async with session.operation_lock:
            inner = "cd {cwd} && {command}".format(cwd=shlex.quote(sandbox_cwd), command=command)
            exit_code, stdout, stderr = await run_command_in_sandbox(
                session=session,
                command=f"bash -o pipefail -lc {shlex.quote(inner)}",
                timeout_seconds=timeout_seconds,
            )
            try:
                await sync_sandbox_working_trees(session)
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
                    await sync_sandbox_working_trees(session)
                except Exception as exc:
                    raise ValueError(f"officecli 执行后的工作区同步失败: {exc}") from exc
        return {
            "backend": OPENSANDBOX_BACKEND,
            "sandbox_id": session.sandbox_id,
            "cwd": sandbox_path_to_visible(sandbox_cwd),
            "args": args,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": "timeout" in stderr.lower() or "timed out" in stderr.lower(),
        }
