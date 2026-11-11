"""ChatData 会话工作区运行域核心：后端协议、Runtime 门面、路径解析与虚拟↔物理映射。

本模块是 agent 域的工作区唯一权威入口：
- ``WorkspaceBackend`` / ``ExecutableWorkspaceBackend``：local / sandbox 后端协议；
- ``ConversationWorkspaceRuntime``：一次会话的路径、技能快照与后端绑定门面；
- ``resolve_workspace_path`` / ``WorkspacePathMapping``：规范路径解析与虚拟↔物理翻译；
- ``create_workspace_backend``：后端白名单工厂。

会话编排（文件列表/读取/预览的会话 API、DAO 校验）留在
``services/agent_run/conversation_workspace.py``，由本模块 re-export 兼容。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, runtime_checkable

from apps.chat_data_app.agent.workspace.runtime_skill_snapshot import (
    build_runtime_skill_snapshot_async,
    is_runtime_skill_snapshot_ready,
    runtime_skills_snapshot_dir,
    runtime_skills_snapshot_dir_from_workspace,
)
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT, resolve_skill_snapshot_path, runtime_skill_visible_root
from utils.storage_paths import (
    CHAT_DATA_HOME,
    assert_workspace_root,
    conversation_semantic_dir_from_workspace,
    conversation_thread_dir,
)
from utils.tool_result_store import QUERY_RESULT_DIR_NAME, TOOL_OFFLOAD_DIR_NAME
from utils.upload_storage_crypto import materialize_plaintext, original_upload_path

from .artifact_registry import parse_output_artifact
from .office_preview_html import normalize_office_preview_html
from .officecli_adapter import OFFICECLI_NO_AUTO_RESIDENT_ENV
from .workspace_path_policy import WORKSPACE_PATH_POLICY
from .workspace_special_services import (
    semantic_model_workspace_service,
    workspace_artifact_service,
    workspace_office_preview_service,
    workspace_tool_result_service,
)

logger = get_logger(__name__)

WORKSPACE_DIR_NAMES = ("workspace", "outputs", "uploads", "memory")
OFFICE_PREVIEW_MAX_CHARS = 12 * 1024 * 1024


@runtime_checkable
class WorkspaceBackend(Protocol):
    """Backend contract for one conversation's model-visible workspace."""

    name: str

    async def read_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        offset: int,
        limit: int,
        max_chars: int,
    ) -> dict[str, Any]: ...

    async def write_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
        mode: str,
    ) -> dict[str, Any]: ...

    async def replace_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]: ...

    async def list_dir(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        max_depth: int,
    ) -> list[dict[str, Any]]: ...

    async def create_dir(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
    ) -> dict[str, Any]: ...

    async def delete_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        recursive: bool,
        expected_type: Literal["file", "dir"],
    ) -> dict[str, Any]: ...

    async def move_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]: ...

    async def copy_path(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]: ...

    async def search_files(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        pattern: str,
        max_results: int,
    ) -> list[dict[str, Any]]: ...

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
    ) -> dict[str, Any]: ...


@runtime_checkable
class ExecutableWorkspaceBackend(WorkspaceBackend, Protocol):
    """Workspace backend extension whose commands share the file environment."""

    async def validate_bash_paths(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
    ) -> None: ...

    async def run_bash(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]: ...

    async def run_officecli(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        args: list[str],
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]: ...


@dataclass
class ConversationWorkspaceRuntime:
    """当前会话的路径、技能快照和工作区后端绑定。"""

    workspace_root: str
    user_id: str
    backend: WorkspaceBackend | str = "local"
    # 当前用户有权访问的全部技能名（由请求入口按 user_id 解析得到）。
    # 仅用于说明/调试，不作为本地读取的前置条件。
    available_skill_names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Convert the configured backend name once; tools only see the backend contract."""
        if isinstance(self.backend, str):
            self.backend = create_workspace_backend(self.backend)
        if not isinstance(self.backend, WorkspaceBackend):
            raise TypeError("backend 必须是 local、sandbox 或 WorkspaceBackend 实现")
        # mypy 无法感知 __post_init__ 的归一化（字段声明为 union 以接受 backend 名称），
        # 内部方法统一读这个已收窄的引用，避免 11 处 union-attr 误报。
        self._resolved_backend: WorkspaceBackend = self.backend

    @property
    def backend_name(self) -> str:
        return self._resolved_backend.name

    async def renew_sandbox_async(self) -> None:
        """在模型调用前后续期 sandbox；local 后端无此能力时直接跳过。"""
        renew = getattr(self._resolved_backend, "renew_sandbox_async", None)
        if renew is None:
            return
        await renew()

    @property
    def runtime_skills_dir(self) -> Path:
        """本会话 runtime-skills 快照目录（运行域只读技能根）。"""
        return runtime_skills_snapshot_dir_from_workspace(self.workspace_root)

    async def _ensure_snapshot_async(self) -> None:
        """确保本会话技能快照存在且可用；为空则按当前用户可见集合惰性构建。

        ``ensure_conversation_workspace`` 会先建出空的 ``runtime-skills`` 目录，因此不能仅以
        目录是否存在判断快照就绪——空目录必须在此填充。已含技能内容或带完成标记的目录
        视为会话内稳定快照，直接复用、不重复复制。单个技能路径异常不会中断构建
        （由 ``list_runtime_skill_sources_async`` 跳过），构建失败也不影响技能本地读取。
        """
        snapshot_dir = self.runtime_skills_dir
        if is_runtime_skill_snapshot_ready(snapshot_dir):
            return
        # workspace_root 为 .../<uid>/<yyyy-MM-DD>/<cid>/user-data，cid 取其父目录名；
        # 快照目录直接由工作区根推导（self.runtime_skills_dir），不从路径反推 uid。
        cid = Path(self.workspace_root).resolve().parent.name
        try:
            await build_runtime_skill_snapshot_async(self.user_id, cid, target_dir=snapshot_dir)
        except Exception as err:
            logger.warning("惰性构建技能快照失败，技能读取可能不可用: %s", err)

    async def resolve_readonly_skill_path_async(self, path: str) -> tuple[Path, str]:
        """将 /mnt/skills 路径解析到本会话快照真实文件系统。

        运行域技能统一为扁平路径 ``/mnt/skills/<name>/...``，直接映射到
        ``threads/<uid>/<yyyy-MM-DD>/<cid>/runtime-skills/<name>/...``；不做按名远程解析，
        快照内不存在即视为无权限。快照缺失时惰性构建。
        路径安全规则统一在 utils.skill_paths.resolve_skill_snapshot_path。
        """
        raw = str(path or "").strip().replace("\\", "/")
        prefix = f"{SKILLS_VIRTUAL_ROOT}/"
        if not raw.startswith(prefix):
            raise ValueError(f"只允许访问技能路径: {path}")
        await self._ensure_snapshot_async()
        return resolve_skill_snapshot_path(self.runtime_skills_dir, path)

    def _executable_backend(self) -> ExecutableWorkspaceBackend:
        if not isinstance(self.backend, ExecutableWorkspaceBackend):
            raise ValueError("当前工作区后端不支持命令执行")
        return self.backend

    async def read_text_file(
        self,
        *,
        file_path: str,
        offset: int,
        limit: int,
        max_chars: int,
    ) -> dict[str, Any]:
        return await self._resolved_backend.read_text_file(
            self, file_path=file_path, offset=offset, limit=limit, max_chars=max_chars
        )

    async def write_text_file(self, *, file_path: str, content: str, mode: str) -> dict[str, Any]:
        return await self._resolved_backend.write_text_file(self, file_path=file_path, content=content, mode=mode)

    async def write_internal_text_files(self, entries: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Write system-owned semantic files to local cache, then sync sandbox copies."""
        if self.backend_name != "local":
            await asyncio.to_thread(self._write_semantic_cache, entries)
        return await semantic_model_workspace_service.write_files(self, entries)

    @staticmethod
    def _assert_semantic_path(path: str) -> None:
        logical_path = WORKSPACE_PATH_POLICY.parse(path)
        if not logical_path.is_semantic_model or not logical_path.relative_path.lower().endswith(".yaml"):
            raise ValueError("内部语义模型写入只能覆盖 /mnt/user-data/workspace/semantic/ 下的 YAML 文件")

    def _write_semantic_cache(self, entries: list[tuple[str, str]]) -> None:
        """语义文件先落本地派生缓存；sandbox 仍由 backend 同步远端副本。"""
        for raw_path, content in entries:
            path = str(raw_path or "").strip()
            if not path:
                continue
            self._assert_semantic_path(path)
            target = resolve_workspace_path(
                self.workspace_root,
                path,
                writable=True,
                allow_managed_semantic_model_write=True,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as file_obj:
                    temporary_path = Path(file_obj.name)
                    file_obj.write(content)
                    file_obj.flush()
                    os.fsync(file_obj.fileno())
                os.replace(temporary_path, target)
                temporary_path = None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    async def delete_internal_text_files(self, *, path: str) -> dict[str, Any]:
        """Delete one system-owned semantic file from local cache, then the sandbox copy."""
        if self.backend_name != "local":
            self._assert_semantic_path(path)
            target = resolve_workspace_path(
                self.workspace_root,
                path,
                writable=True,
                allow_managed_semantic_model_write=True,
            )
            target.unlink(missing_ok=True)
        return await semantic_model_workspace_service.delete_files(self, path=path)

    async def replace_text_file(
        self,
        *,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]:
        return await self._resolved_backend.replace_text_file(
            self,
            file_path=file_path,
            old_content=old_content,
            new_content=new_content,
        )

    async def write_tool_result(self, *, file_path: str, content: str) -> dict[str, Any]:
        """Persist a middleware-owned hidden tool result through the active backend."""
        return await workspace_tool_result_service.write(
            self,
            file_path=file_path,
            content=content,
        )

    async def artifact_payload(self, *, file_path: str) -> dict[str, Any]:
        return await workspace_artifact_service.load(self, file_path=file_path)

    async def render_office_html(self, *, file_path: str, max_chars: int) -> dict[str, Any]:
        return await workspace_office_preview_service.render_html(
            self,
            file_path=file_path,
            max_chars=max_chars,
        )

    async def list_dir(self, *, path: str, max_depth: int) -> list[dict[str, Any]]:
        return await self._resolved_backend.list_dir(self, path=path, max_depth=max_depth)

    async def create_dir(self, *, path: str) -> dict[str, Any]:
        return await self._resolved_backend.create_dir(self, path=path)

    async def delete_path(
        self,
        *,
        path: str,
        recursive: bool,
        expected_type: Literal["file", "dir"],
    ) -> dict[str, Any]:
        return await self._resolved_backend.delete_path(
            self,
            path=path,
            recursive=recursive,
            expected_type=expected_type,
        )

    async def move_path(self, *, source_path: str, destination_path: str) -> dict[str, Any]:
        return await self._resolved_backend.move_path(
            self,
            source_path=source_path,
            destination_path=destination_path,
        )

    async def copy_path(self, *, source_path: str, destination_path: str) -> dict[str, Any]:
        return await self._resolved_backend.copy_path(
            self,
            source_path=source_path,
            destination_path=destination_path,
        )

    async def search_files(
        self,
        *,
        path: str,
        pattern: str,
        max_results: int,
    ) -> list[dict[str, Any]]:
        return await self._resolved_backend.search_files(
            self,
            path=path,
            pattern=pattern,
            max_results=max_results,
        )

    async def grep_text(
        self,
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
        return await self._resolved_backend.grep_text(
            self,
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

    async def validate_bash_paths(self, *, command: str) -> None:
        await self._executable_backend().validate_bash_paths(self, command=command)

    async def run_bash(
        self,
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return await self._executable_backend().run_bash(
            self, command=command, cwd=cwd, timeout_seconds=timeout_seconds
        )

    async def run_officecli(
        self,
        *,
        args: list[str],
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return await self._executable_backend().run_officecli(self, args=args, cwd=cwd, timeout_seconds=timeout_seconds)


def workspace_runtime_from_state(state: Mapping[str, Any] | None) -> ConversationWorkspaceRuntime:
    """Rebuild a workspace runtime from serializable AgentState fields."""
    values = state if isinstance(state, Mapping) else {}
    workspace = values.get("workspace")
    context = workspace if isinstance(workspace, Mapping) else {}
    workspace_root = str(context.get("workspace_root") or "").strip()
    if not workspace_root:
        raise ValueError("当前请求缺少会话工作区")
    backend_name = str(context.get("workspace_backend") or "local").strip().lower() or "local"
    return ConversationWorkspaceRuntime(
        workspace_root=workspace_root,
        user_id=str(values.get("user_id") or "system").strip() or "system",
        backend=backend_name,
    )


def ensure_conversation_workspace(
    conversation_id: str,
    user_id: str = "system",
    *,
    created_date: str | None = None,
) -> dict[str, str]:
    """创建并返回 ChatData 会话目录结构。

    布局：共享根 ``threads/<uid>/<yyyy-MM-DD>/<cid>/user-data/``；
    技能快照落到 ``CHAT_DATA_CACHE_HOME/threads/<uid>/<yyyy-MM-DD>/<cid>/runtime-skills/``。
    ``created_date`` 为会话创建日期（``YYYY-MM-DD``，缺省当前日期）。``user-data`` 为模型可写
    运行域；``runtime-skills`` 为本地派生缓存，不进入共享数据根。
    """
    try:
        base = conversation_thread_dir(user_id, conversation_id, created_date=created_date).resolve()
    except ValueError as exc:
        raise ValueError(f"会话工作区路径参数非法: {exc}") from exc
    root = (base / "user-data").resolve()
    root.mkdir(parents=True, exist_ok=True)
    runtime_skills_snapshot_dir(user_id, conversation_id, created_date=created_date).mkdir(parents=True, exist_ok=True)
    paths = {"root": str(root)}
    for name in WORKSPACE_DIR_NAMES:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        paths[name.replace("-", "_")] = str(path)
    (root / "outputs" / TOOL_OFFLOAD_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return paths


def resolve_uploads_root(user_id: str, thread_id: str, *, created_date: str | None = None) -> Path:
    """返回当前会话工作区 uploads 目录（上传文件权威存储根）。

    不再回退到历史 ``server/uploads`` 目录；会话工作区未初始化时由
    ``ensure_conversation_workspace`` 直接抛错，由调用方处理。
    """
    paths = ensure_conversation_workspace(thread_id, user_id, created_date=created_date)
    return Path(paths["uploads"])


def _resolve_semantic_workspace_path(
    workspace_root: str,
    logical_path: Any,
    *,
    path: str,
    writable: bool,
    allow_managed_semantic_model_write: bool,
) -> Path | None:
    """语义模型逻辑路径映射到本地缓存；非语义路径返回 None。"""
    if not getattr(logical_path, "is_semantic_model", False):
        return None
    if writable and not allow_managed_semantic_model_write:
        raise ValueError("/mnt/user-data/workspace/semantic/ 是本轮语义模型只读快照，不能修改")
    semantic_root = conversation_semantic_dir_from_workspace(workspace_root).resolve()
    suffix = logical_path.relative_path[len("semantic") :].lstrip("/")
    candidate = semantic_root / suffix
    if candidate.is_symlink():
        raise ValueError(f"不允许操作符号链接: {path}")
    target = candidate.resolve()
    if target != semantic_root and semantic_root not in target.parents:
        raise ValueError(f"路径越界，禁止访问语义缓存之外的路径: {path}")
    if not writable and not semantic_root.exists() and not target.exists():
        legacy_root = (Path(workspace_root).resolve() / "workspace" / "semantic").resolve()
        legacy_target = (legacy_root / suffix).resolve()
        if (legacy_target == legacy_root or legacy_root in legacy_target.parents) and legacy_target.exists():
            return legacy_target
    return target


def _resolve_regular_workspace_path(
    root: Path,
    *,
    path: str,
    logical_path: Any,
    writable: bool,
    allow_managed_semantic_model_write: bool,
    materialize_uploads: bool,
) -> Path:
    """解析非 semantic 的工作区路径并实施目录边界。"""
    # 与 sandbox 后端保持同一口径：符号链接一律拒绝操作（sandbox 侧由
    # _sandbox_path_kind 对 entry_type=symlink 直接报错）。必须在 resolve 之前判断
    # 最终组件，否则 realpath 会先把链接跟到目标，下游的 is_symlink() 守卫永远不可达，
    # 造成「删链接实际删掉目标文件」。祖先目录已在 root=resolve() 时归一化，
    # 因此这里只会命中被操作对象本身，不会误伤 CHAT_DATA_HOME 位于符号链接下的部署。
    candidate = root / logical_path.normalized
    if candidate.is_symlink():
        raise ValueError(f"不允许操作符号链接: {path}")

    target = candidate.resolve()
    if root not in target.parents:
        raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")

    visible_roots = [(root / name).resolve() for name in WORKSPACE_DIR_NAMES]
    if not any(target == item or item in target.parents for item in visible_roots):
        raise ValueError(
            "只允许访问 /mnt/user-data/workspace、/mnt/user-data/outputs、"
            "/mnt/user-data/uploads 或 /mnt/user-data/memory"
        )

    if writable:
        if is_system_result_workspace_path(str(root), target):
            raise ValueError("不允许通过文件编辑工具修改系统结果目录")
        if is_managed_semantic_model_workspace_path(str(root), target) and not allow_managed_semantic_model_write:
            raise ValueError("/mnt/user-data/workspace/semantic/ 是本轮语义模型只读快照，不能修改")
    elif logical_path.kind == "uploads" and materialize_uploads:
        # 上传文件可能以应用层密文落盘；只有真正读取时才解密，列表/删除不触发。
        target = materialize_plaintext(target)
    return target


def resolve_workspace_path(
    workspace_root: str,
    path: str,
    *,
    writable: bool = False,
    allow_managed_semantic_model_write: bool = False,
    allow_uploads_write: bool = False,
    materialize_uploads: bool = True,
) -> Path:
    """解析模型可见路径并按解析后的真实路径实施目录边界。

    ``allow_uploads_write`` 仅在「人侧 uploads 删除接口」这一条链路上打开，见
    ``WorkspacePathPolicy.assert_writable`` 的说明；模型工具始终走默认值。
    """
    root = Path(workspace_root).resolve()
    logical_path = WORKSPACE_PATH_POLICY.parse(path)
    if logical_path.is_skill:
        raise ValueError(f"技能路径必须通过技能快照读取: {path}")
    if writable:
        WORKSPACE_PATH_POLICY.assert_writable(
            logical_path,
            allow_managed_semantic_model_write=allow_managed_semantic_model_write,
            allow_uploads_write=allow_uploads_write,
        )
    else:
        WORKSPACE_PATH_POLICY.assert_readable(logical_path)

    semantic_target = _resolve_semantic_workspace_path(
        workspace_root,
        logical_path,
        path=path,
        writable=writable,
        allow_managed_semantic_model_write=allow_managed_semantic_model_write,
    )
    if semantic_target is not None:
        return semantic_target

    return _resolve_regular_workspace_path(
        root,
        path=path,
        logical_path=logical_path,
        writable=writable,
        allow_managed_semantic_model_write=allow_managed_semantic_model_write,
        materialize_uploads=materialize_uploads,
    )


def is_system_result_workspace_path(workspace_root: str, path: Path) -> bool:
    """Return whether a resolved path belongs to system-managed result storage."""
    output_root = Path(workspace_root).resolve() / "outputs"
    target = path.resolve()
    result_roots = (
        (output_root / TOOL_OFFLOAD_DIR_NAME).resolve(),
        (output_root / QUERY_RESULT_DIR_NAME).resolve(),
    )
    return any(target == root or root in target.parents for root in result_roots)


def is_managed_semantic_model_workspace_path(workspace_root: str, path: Path) -> bool:
    """Return whether a path belongs to the system-owned semantic model directory."""
    legacy_root = (Path(workspace_root).resolve() / "workspace" / "semantic").resolve()
    cache_root = conversation_semantic_dir_from_workspace(workspace_root).resolve()
    target = path.resolve()
    return any(target == root or root in target.parents for root in (legacy_root, cache_root))


def to_visible_path(workspace_root: str, path: Path) -> str:
    """把物理路径转为模型/前端可见路径（统一规范前缀 /mnt/user-data/）。"""
    root = Path(workspace_root).resolve()
    target = path.resolve()
    semantic_root = conversation_semantic_dir_from_workspace(workspace_root).resolve()
    legacy_root = (root / "workspace" / "semantic").resolve()
    for current_root in (semantic_root, legacy_root):
        if target == current_root or current_root in target.parents:
            relative_semantic = target.relative_to(current_root).as_posix()
            return (
                f"/mnt/user-data/workspace/semantic/{relative_semantic}"
                if relative_semantic
                else ("/mnt/user-data/workspace/semantic")
            )
    original = original_upload_path(target)
    if original is not None:
        target = original
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError as exc:
        # 不返回宿主绝对路径：越界属于调用方 bug，应该 fail closed，避免泄漏部署路径。
        raise ValueError(f"路径不在当前会话工作区内: {path}") from exc
    return f"/mnt/user-data/{relative}"


def resolve_output_skill_directory(workspace_root: str, path: str) -> Path:
    """Return a generated skill directory that is eligible for user import."""
    skill_dir = resolve_workspace_path(workspace_root, path)
    if is_system_result_workspace_path(workspace_root, skill_dir):
        raise ValueError("不允许导入系统结果目录下的内容")
    outputs_root = resolve_workspace_path(workspace_root, "/mnt/user-data/outputs")
    try:
        relative = skill_dir.relative_to(outputs_root)
    except ValueError as exc:
        raise ValueError("只允许导入 /mnt/user-data/outputs/<skill-name>/ 下的技能目录") from exc
    if len(relative.parts) != 1 or relative.name.startswith("."):
        raise ValueError("只允许导入 /mnt/user-data/outputs/<skill-name>/ 下的技能目录")

    skill_md_path = skill_dir / "SKILL.md"
    if not skill_dir.is_dir() or skill_md_path.is_symlink() or not skill_md_path.is_file():
        raise ValueError("技能目录必须包含有效的 SKILL.md")
    artifact = parse_output_artifact(
        visible_path=f"/mnt/user-data/outputs/{relative.name}/SKILL.md",
        data=skill_md_path.read_bytes(),
    )
    if artifact.get("artifact_type") != "skill":
        raise ValueError("SKILL.md 元数据无效或与技能目录名不一致")
    return skill_dir


@dataclass
class WorkspacePathMapping:
    """虚拟路径 ↔ 物理路径映射，本地模式命令翻译与输出遮蔽的单一入口。

    虚拟前缀（白名单）：
      - 工作区：``/mnt/user-data/{workspace,outputs,uploads}`` 及其裸别名
        ``/workspace``、``/outputs``、``/uploads``（仅限 bash 命令文本翻译容错；
        文件工具等结构化入口只接受规范形式，非规范输入由路径策略直接拒绝）
      - 会话技能快照：``/mnt/skills/<name>/...``

    - ``translate``：正向，把文本里的虚拟路径翻成物理路径，交给本地 subprocess。
    - ``mask``：反向，把输出里的物理路径还原为虚拟路径，避免向模型泄漏宿主机路径。
      边界感知，单次替换，物理根内含 ``/uploads`` 等段也不会自引用膨胀。
    """

    workspace_root: str
    runtime_skills_dir: Path | None = None

    # 裸别名仅在路径边界处匹配：前置负向断言挡住 server/uploads 之类内部段，
    # 后置负向断言挡住 /workspaceX 误吞。与工作区虚拟前缀共用同一套边界规则。
    _ALIAS_RE = re.compile(r"(?<![\w/])/(workspace|outputs|uploads)(?![\w])")
    _VIRTUAL_RE = re.compile(r"/mnt/user-data/(workspace|outputs|uploads)(?![\w])")

    def __post_init__(self) -> None:
        root = self.workspace_root.rstrip("/")
        # 物理根绝不能包含虚拟前缀 /mnt/user-data/；若包含说明 CHAT_DATA_HOME
        # 被误配成 sandbox 挂载点路径，继续替换会导致路径指数膨胀。
        if "/mnt/user-data/" in root:
            raise ValueError(
                "workspace_root 配置异常：物理根不应包含 /mnt/user-data/，"
                "请检查 CHAT_DATA_HOME 环境变量是否误用了 sandbox 虚拟挂载点路径"
            )
        self._root = root
        self._physical_workspace = {
            "workspace": f"{root}/workspace",
            "outputs": f"{root}/outputs",
            "uploads": f"{root}/uploads",
            "memory": f"{root}/memory",
        }
        # /mnt/skills/<name> -> 本会话快照目录/<name>；从快照目录直接枚举，无需逐技能登记。
        # runtime_skills_dir 为 None 表示本映射不绑定技能快照（仅工作区路径翻译）。
        self._skill_physical: dict[str, str] = {}
        skills_dir = self.runtime_skills_dir
        if skills_dir is not None and skills_dir.is_dir():
            for item in skills_dir.iterdir():
                if item.is_dir() and not item.is_symlink() and not item.name.startswith("."):
                    self._skill_physical[runtime_skill_visible_root(item.name)] = str(item)

    def translate(self, text: str) -> str:
        """正向：命令/文本中的虚拟路径 → 物理路径。"""
        if not text:
            return text
        out = text
        # 1) 裸别名规范化成 /mnt/user-data/ 形式（边界安全，不误伤物理根内部段）。
        out = self._ALIAS_RE.sub(r"/mnt/user-data/\1", out)
        # 2) 技能虚拟 → 物理（最长优先）。
        for virtual_root, physical in sorted(self._skill_physical.items(), key=lambda kv: len(kv[0]), reverse=True):
            out = out.replace(virtual_root, physical)
        # 3) 工作区虚拟 → 物理（单次替换，root 不含 /mnt/user-data/，不自引用）。
        out = self._VIRTUAL_RE.sub(lambda m: f"{self._root}/{m.group(1)}", out)
        return out

    def mask(self, text: str) -> str:
        """反向：输出中的物理路径 → 虚拟路径，遮蔽宿主机绝对路径。"""
        if not text:
            return text
        out = text
        # 1) 技能物理 → 虚拟（最长优先）。
        for virtual_root, physical in sorted(self._skill_physical.items(), key=lambda kv: len(kv[0]), reverse=True):
            if physical:
                out = out.replace(physical, virtual_root)
        # 2) 工作区物理 → 虚拟。
        for name, physical in self._physical_workspace.items():
            out = out.replace(physical, f"/mnt/user-data/{name}")
        return out


def _render_local_office_html(
    workspace_root: str,
    file_path: str,
    max_chars: int,
) -> dict[str, Any]:
    """在本地（local）后端用宿主机 ``officecli`` 将 Office 文件渲染为受控 HTML 预览。

    逻辑与沙箱 ``sandbox_render_office_html`` 对齐：仅支持 .docx/.xlsx/.pptx，
    转出的 HTML 受 ``max_chars``（上限 ``OFFICE_PREVIEW_MAX_CHARS``）约束，超出即标记
    ``truncated``。宿主机未安装 ``officecli`` 或转换失败时抛错，由路由层统一降级为
    ``preview_unavailable`` / ``preview_failed``。
    """
    target = resolve_workspace_path(workspace_root, file_path)
    if target.suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
        raise ValueError("OfficeCLI HTML 预览仅支持 .docx、.xlsx、.pptx 文件")
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    officecli_bin = shutil.which("officecli")
    if not officecli_bin:
        raise RuntimeError("本地环境未安装 OfficeCLI，无法预览 Office 文件")
    limit = max(1, min(int(max_chars), OFFICE_PREVIEW_MAX_CHARS))
    fd, preview_path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    try:
        exit_code = subprocess.run(
            [officecli_bin, "view", str(target), "html", "--out", preview_path],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, **OFFICECLI_NO_AUTO_RESIDENT_ENV},
        )
        if exit_code.returncode != 0:
            stderr = (exit_code.stderr or "").strip()
            raise RuntimeError(stderr or "OfficeCLI HTML 预览失败")
        preview_file = Path(preview_path)
        if not preview_file.exists():
            raise RuntimeError("OfficeCLI 未生成预览文件")
        if preview_file.stat().st_size > limit:
            return {
                "renderer": "officecli-html",
                "source_path": str(target),
                "html": "",
                "truncated": True,
            }
        html = normalize_office_preview_html(preview_file.read_text(encoding="utf-8", errors="replace"))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(preview_path)
    truncated = len(html) > limit
    return {
        "renderer": "officecli-html",
        "source_path": str(target),
        "html": html[:limit],
        "truncated": truncated,
    }


def _ensure_workspace_root_in_chat_data_home(workspace_root: str) -> None:
    """确保服务端直接读取的会话根仍属于 ChatData 共享数据目录。"""
    assert_workspace_root(workspace_root, chat_data_home=CHAT_DATA_HOME)


def create_workspace_backend(name: str) -> WorkspaceBackend:
    """Create a server-registered backend; unknown names fail closed."""
    # 惰性 import：后端模块反向 import 本模块头部符号，顶层互相 import 会成环。
    from apps.chat_data_app.agent.workspace.workspace_local_backend import LocalWorkspaceBackend
    from apps.chat_data_app.agent.workspace.workspace_sandbox_backend import SandboxWorkspaceBackend

    factories: dict[str, Callable[[], WorkspaceBackend]] = {
        "local": LocalWorkspaceBackend,
        "sandbox": SandboxWorkspaceBackend,
    }
    backend_name = str(name or "").strip().lower()
    factory = factories.get(backend_name)
    if factory is None:
        supported = ", ".join(sorted(factories))
        raise ValueError(f"不支持的工作区后端: {name}；可用后端: {supported}")
    return factory()


def _absolute_command_path_tokens(command: str, *, tool_name: str = "bash_run") -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"command 解析失败: {exc}") from exc
    officecli_selector_indices = _officecli_selector_token_indices(tokens)
    invalid = []
    paths: list[str] = []
    for index, token in enumerate(tokens):
        if index in officecli_selector_indices:
            continue
        normalized = token.lstrip("<>")
        if ".." in PurePosixPath(normalized).parts:
            invalid.append(token)
        elif normalized.startswith("/"):
            paths.append(normalized)
    if invalid:
        raise ValueError(f"{tool_name} 不允许路径越界: " + ", ".join(invalid[:3]))
    return paths


_OFFICECLI_SELECTOR_COMMANDS = {"get", "query", "set", "add", "remove"}


def _officecli_selector_token_indices(tokens: list[str]) -> set[int]:
    """Exclude OfficeCLI DOM selectors from host-path validation."""
    selector_indices: set[int] = set()
    for officecli_index, token in enumerate(tokens):
        if Path(token).name not in {"officecli", "officecli.exe"}:
            continue
        verb_index = _officecli_verb_index(tokens, officecli_index)
        if verb_index is None:
            continue
        selector_indices.update(_selector_indices_after_verb(tokens, verb_index))
    return selector_indices


def _officecli_verb_index(tokens: list[str], officecli_index: int) -> int | None:
    """定位 officecli 可执行文件后的第一个动词（选择器类或 move/swap）。"""
    return next(
        (
            index
            for index in range(officecli_index + 1, len(tokens))
            if tokens[index] in _OFFICECLI_SELECTOR_COMMANDS | {"move", "swap"}
        ),
        None,
    )


def _selector_indices_after_verb(tokens: list[str], verb_index: int) -> set[int]:
    """按动词种类返回 selector token 的下标集合。"""
    verb = tokens[verb_index]
    if verb in _OFFICECLI_SELECTOR_COMMANDS and verb_index + 2 < len(tokens):
        return {verb_index + 2}
    if verb == "move":
        return _move_selector_indices(tokens, verb_index)
    if verb == "swap":
        return {index for index in (verb_index + 2, verb_index + 3) if index < len(tokens)}
    return set()


def _move_selector_indices(tokens: list[str], verb_index: int) -> set[int]:
    """move 动词：本体 selector + --to/--after/--before 选项的取值。"""
    indices: set[int] = set()
    if verb_index + 2 < len(tokens):
        indices.add(verb_index + 2)
    for option in ("--to", "--after", "--before"):
        for index in range(verb_index + 3, len(tokens) - 1):
            if tokens[index] == option:
                indices.add(index + 1)
    return indices


def __getattr__(name: str) -> Any:
    """向后兼容 re-export：后端实现已拆至独立模块，惰性解析避免 import 成环。"""
    if name == "LocalWorkspaceBackend":
        from apps.chat_data_app.agent.workspace.workspace_local_backend import LocalWorkspaceBackend

        return LocalWorkspaceBackend
    if name == "SandboxWorkspaceBackend":
        from apps.chat_data_app.agent.workspace.workspace_sandbox_backend import SandboxWorkspaceBackend

        return SandboxWorkspaceBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
