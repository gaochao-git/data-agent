"""ChatData 会话工作区：后端协议、Runtime 门面与路径策略（local / sandbox 后端实现见同目录 workspace_*_backend.py）。"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable, Literal, Mapping, Protocol, runtime_checkable

from utils.database import get_session
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT, runtime_skill_visible_root
from utils.storage_paths import CHAT_DATA_HOME, normalize_conversation_id, normalize_user_id
from utils.tool_result_store import QUERY_RESULT_DIR_NAME, TOOL_OFFLOAD_DIR_NAME, ToolResultStore

from ...daos.conversation_dao import ConversationDao
from .artifact_registry import parse_output_artifact
from .runtime_skill_snapshot import (
    build_runtime_skill_snapshot_async,
    is_runtime_skill_snapshot_ready,
    runtime_skills_snapshot_dir_from_workspace,
)
from ... import AGENT_ID
from apps.chat_data_app.agent.workspace.workspace_path_policy import WORKSPACE_PATH_POLICY
from apps.chat_data_app.agent.workspace.workspace_special_services import (
    semantic_model_workspace_service,
    workspace_artifact_service,
    workspace_office_preview_service,
    workspace_tool_result_service,
)

logger = get_logger(__name__)

THREADS_DIR = Path(CHAT_DATA_HOME) / "threads"
WORKSPACE_DIR_NAMES = ("workspace", "outputs", "uploads")
OFFICE_PREVIEW_MAX_CHARS = 12 * 1024 * 1024
conversation_dao = ConversationDao(session_context=lambda: get_session())


@runtime_checkable
class WorkspaceBackend(Protocol):
    """Backend contract for one conversation's model-visible workspace."""

    name: str

    async def read_text_file(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        file_path: str,
        offset: int,
        limit: int,
        max_chars: int,
    ) -> dict[str, Any]: ...

    async def write_text_file(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        file_path: str,
        content: str,
        mode: str,
    ) -> dict[str, Any]: ...

    async def replace_text_file(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]: ...

    async def list_dir(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        path: str,
        max_depth: int,
    ) -> list[dict[str, Any]]: ...

    async def create_dir(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        path: str,
    ) -> dict[str, Any]: ...

    async def delete_path(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        path: str,
        recursive: bool,
        expected_type: Literal["file", "dir"],
    ) -> dict[str, Any]: ...

    async def move_path(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]: ...

    async def copy_path(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        source_path: str,
        destination_path: str,
    ) -> dict[str, Any]: ...

    async def search_files(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        path: str,
        pattern: str,
        max_results: int,
    ) -> list[dict[str, Any]]: ...

    async def grep_text(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        path: str,
        patterns: list[str],
        glob: str,
        max_results: int,
        max_chars: int,
    ) -> dict[str, Any]: ...



@runtime_checkable
class ExecutableWorkspaceBackend(WorkspaceBackend, Protocol):
    """Workspace backend extension whose commands share the file environment."""

    async def validate_bash_paths(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        command: str,
    ) -> None: ...

    async def run_bash(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]: ...

    async def run_officecli(
        self,
        runtime: "ConversationWorkspaceRuntime",
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
    backend: "WorkspaceBackend | str" = "local"
    # 当前用户有权访问的全部技能名（由请求入口按 user_id 解析得到）。
    # 仅用于说明/调试，不作为本地读取的前置条件。
    available_skill_names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Convert the configured backend name once; tools only see the backend contract."""
        if isinstance(self.backend, str):
            self.backend = create_workspace_backend(self.backend)
        if not isinstance(self.backend, WorkspaceBackend):
            raise TypeError("backend 必须是 local、sandbox 或 WorkspaceBackend 实现")

    @property
    def backend_name(self) -> str:
        return self.backend.name

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
        base = Path(self.workspace_root).resolve().parent  # .../<cid>
        uid, cid = base.parent.name, base.name
        try:
            await build_runtime_skill_snapshot_async(uid, cid)
        except Exception as err:
            logger.warning("惰性构建技能快照失败，技能读取可能不可用: %s", err)

    async def resolve_readonly_skill_path_async(self, path: str) -> tuple[Path, str]:
        """将 /mnt/skills 路径解析到本会话快照真实文件系统。

        运行域技能统一为扁平路径 ``/mnt/skills/<name>/...``，直接映射到
        ``threads/<uid>/<cid>/runtime-skills/<name>/...``；不做按名远程解析，
        快照内不存在即视为无权限。快照缺失时惰性构建。
        """
        raw = str(path or "").strip().replace("\\", "/")
        prefix = f"{SKILLS_VIRTUAL_ROOT}/"
        if not raw.startswith(prefix):
            raise ValueError(f"只允许访问技能路径: {path}")
        parts = PurePosixPath(raw).parts
        if len(parts) < 4 or parts[1:3] != ("mnt", "skills"):
            raise ValueError(f"技能路径无效: {path}")
        name = parts[3]
        if not name or name.startswith("."):
            raise ValueError(f"技能路径无效: {path}")
        await self._ensure_snapshot_async()
        snapshot_root = self.runtime_skills_dir.resolve()
        source_entry = self.runtime_skills_dir / name
        source_dir = source_entry.resolve()
        if (
            source_entry.is_symlink()
            or not source_dir.is_dir()
            or snapshot_root not in source_dir.parents
        ):
            raise ValueError(f"技能不存在或无权限访问: {name}")
        relative_parts = parts[4:]
        if ".." in relative_parts:
            raise ValueError(f"技能路径越界: {path}")
        target = (source_dir.joinpath(*relative_parts)).resolve()
        if target != source_dir and source_dir not in target.parents:
            raise ValueError(f"技能路径越界: {path}")
        visible = runtime_skill_visible_root(name)
        if relative_parts:
            visible += "/" + "/".join(relative_parts)
        return target, visible

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
        return await self.backend.read_text_file(
            self, file_path=file_path, offset=offset, limit=limit, max_chars=max_chars
        )

    async def write_text_file(self, *, file_path: str, content: str, mode: str) -> dict[str, Any]:
        return await self.backend.write_text_file(self, file_path=file_path, content=content, mode=mode)

    async def write_internal_text_files(self, entries: list[tuple[str, str]]) -> list[dict[str, Any]]:
        """Write system-owned semantic model files through the active backend."""
        return await semantic_model_workspace_service.write_files(self, entries)

    async def replace_text_file(
        self,
        *,
        file_path: str,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]:
        return await self.backend.replace_text_file(
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
        return await self.backend.list_dir(self, path=path, max_depth=max_depth)

    async def create_dir(self, *, path: str) -> dict[str, Any]:
        return await self.backend.create_dir(self, path=path)

    async def delete_path(
        self,
        *,
        path: str,
        recursive: bool,
        expected_type: Literal["file", "dir"],
    ) -> dict[str, Any]:
        return await self.backend.delete_path(
            self,
            path=path,
            recursive=recursive,
            expected_type=expected_type,
        )

    async def move_path(self, *, source_path: str, destination_path: str) -> dict[str, Any]:
        return await self.backend.move_path(
            self,
            source_path=source_path,
            destination_path=destination_path,
        )

    async def copy_path(self, *, source_path: str, destination_path: str) -> dict[str, Any]:
        return await self.backend.copy_path(
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
        return await self.backend.search_files(
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
    ) -> dict[str, Any]:
        return await self.backend.grep_text(
            self,
            path=path,
            patterns=patterns,
            glob=glob,
            max_results=max_results,
            max_chars=max_chars,
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
        return await self._executable_backend().run_officecli(
            self, args=args, cwd=cwd, timeout_seconds=timeout_seconds
        )


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


def _safe_segment(value: str) -> str:
    """规整路径段，避免会话 ID 造成目录越界。"""
    try:
        return normalize_conversation_id(value)
    except ValueError as exc:
        raise ValueError("非法会话 ID") from exc


def ensure_conversation_workspace(conversation_id: str, user_id: str = "system") -> dict[str, str]:
    """创建并返回 ChatData 会话目录结构。

    布局：``threads/<uid>/<cid>/{user-data/{workspace,outputs,uploads}, runtime-skills/}``。
    ``user-data`` 为模型可写运行域；``runtime-skills`` 为会话级技能快照（由
    ``runtime_skill_snapshot.build_runtime_skill_snapshot_async`` 惰性填充）。
    """
    thread_id = _safe_segment(conversation_id)
    safe_user_id = normalize_user_id(user_id or "system")
    base = (THREADS_DIR / safe_user_id / thread_id).resolve()
    root = (base / "user-data").resolve()
    root.mkdir(parents=True, exist_ok=True)
    (base / "runtime-skills").mkdir(parents=True, exist_ok=True)
    paths = {"root": str(root)}
    for name in WORKSPACE_DIR_NAMES:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        paths[name.replace("-", "_")] = str(path)
    (root / "outputs" / TOOL_OFFLOAD_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return paths


def resolve_uploads_root(user_id: str, thread_id: str) -> Path:
    """返回当前会话工作区 uploads 目录（上传文件权威存储根）。

    不再回退到历史 ``server/uploads`` 目录；会话工作区未初始化时由
    ``ensure_conversation_workspace`` 直接抛错，由调用方处理。
    """
    paths = ensure_conversation_workspace(thread_id, user_id)
    return Path(paths["uploads"])


def resolve_workspace_path(
    workspace_root: str,
    path: str,
    *,
    writable: bool = False,
    allow_managed_semantic_model_write: bool = False,
) -> Path:
    """解析模型可见路径并按解析后的真实路径实施目录边界。"""
    root = Path(workspace_root).resolve()
    logical_path = WORKSPACE_PATH_POLICY.parse(path)
    if logical_path.is_skill:
        raise ValueError(f"技能路径必须通过技能快照读取: {path}")
    if writable:
        WORKSPACE_PATH_POLICY.assert_writable(
            logical_path,
            allow_managed_semantic_model_write=allow_managed_semantic_model_write,
        )
    else:
        WORKSPACE_PATH_POLICY.assert_readable(logical_path)

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
        raise ValueError("只允许访问 workspace/、outputs/ 或 uploads/")

    if writable:
        if is_system_result_workspace_path(str(root), target):
            raise ValueError("不允许通过文件编辑工具修改系统结果目录")
        if (
            is_managed_semantic_model_workspace_path(str(root), target)
            and not allow_managed_semantic_model_write
        ):
            raise ValueError("workspace/semantic/ 是本轮语义模型只读快照，不能修改")

    return target


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
    semantic_root = (Path(workspace_root).resolve() / "workspace" / "semantic").resolve()
    target = path.resolve()
    return target == semantic_root or semantic_root in target.parents


def to_visible_path(workspace_root: str, path: Path) -> str:
    """把物理路径转为模型/前端可见路径。"""
    root = Path(workspace_root).resolve()
    target = path.resolve()
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError:
        return str(target)
    return f"/{relative}"


def resolve_output_skill_directory(workspace_root: str, path: str) -> Path:
    """Return a generated skill directory that is eligible for user import."""
    skill_dir = resolve_workspace_path(workspace_root, path)
    if is_system_result_workspace_path(workspace_root, skill_dir):
        raise ValueError("不允许导入系统结果目录下的内容")
    outputs_root = resolve_workspace_path(workspace_root, "outputs")
    try:
        relative = skill_dir.relative_to(outputs_root)
    except ValueError as exc:
        raise ValueError("只允许导入 outputs/<skill-name>/ 下的技能目录") from exc
    if len(relative.parts) != 1 or relative.name.startswith("."):
        raise ValueError("只允许导入 outputs/<skill-name>/ 下的技能目录")

    skill_md_path = skill_dir / "SKILL.md"
    if not skill_dir.is_dir() or skill_md_path.is_symlink() or not skill_md_path.is_file():
        raise ValueError("技能目录必须包含有效的 SKILL.md")
    artifact = parse_output_artifact(
        visible_path=f"/outputs/{relative.name}/SKILL.md",
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
        ``/workspace``、``/outputs``、``/uploads``
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
        for virtual_root, physical in sorted(
            self._skill_physical.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
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
        for virtual_root, physical in sorted(
            self._skill_physical.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if physical:
                out = out.replace(physical, virtual_root)
        # 2) 工作区物理 → 虚拟。
        for name in ("workspace", "outputs", "uploads"):
            out = out.replace(self._physical_workspace[name], f"/mnt/user-data/{name}")
        return out


def list_workspace_files(conversation_id: str, user_id: str = "system") -> list[dict[str, Any]]:
    """列出会话工作区中的可见文件，不跟随逃逸符号链接。"""
    paths = ensure_conversation_workspace(conversation_id, user_id)
    root = Path(paths["root"]).resolve()
    files: list[dict[str, Any]] = []
    for directory_name in WORKSPACE_DIR_NAMES:
        directory = root / directory_name
        if not directory.exists():
            continue
        for item in sorted(directory.rglob("*")):
            if not item.is_file():
                continue
            try:
                relative_path = item.relative_to(root).as_posix()
                resolved_item = resolve_workspace_path(str(root), relative_path)
                if is_system_result_workspace_path(str(root), resolved_item):
                    continue
                stat = item.stat()
            except (OSError, ValueError):
                continue
            files.append({
                "path": to_visible_path(str(root), resolved_item),
                "relative_path": relative_path,
                "directory": directory_name,
                "name": item.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "source": directory_name,
            })
    return files


def read_workspace_text_file(
    conversation_id: str,
    path: str,
    max_bytes: int = 1024 * 1024,
    user_id: str = "system",
) -> dict[str, Any]:
    """读取会话工作区中的文本文件。"""
    paths = ensure_conversation_workspace(conversation_id, user_id)
    root = paths["root"]
    target = resolve_workspace_path(root, path)
    if is_system_result_workspace_path(root, target):
        raise ValueError("工具结果缓存不在工作区文件预览范围内")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError(f"文件过大，暂不支持预览超过 {max_bytes} 字节的文件")
    content = target.read_text(encoding="utf-8", errors="replace")
    return {
        "path": to_visible_path(root, target),
        "relative_path": target.relative_to(Path(root).resolve()).as_posix(),
        "content": content,
        "size": size,
    }


async def can_access_conversation_workspace(conversation_id: str, user_id: str) -> bool:
    """校验当前用户是否可访问 ChatData 会话工作区。"""
    if not str(conversation_id or "").strip() or not str(user_id or "").strip():
        return False
    return await conversation_dao.exists_user_agent_thread(
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=AGENT_ID,
    )


async def list_conversation_workspace_files(
    conversation_id: str,
    *,
    user_id: str,
) -> list[dict[str, Any]]:
    """读取当前用户会话的持久化工作区文件。"""
    return await asyncio.to_thread(list_workspace_files, conversation_id, user_id)


async def read_conversation_workspace_file(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
    max_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """读取当前用户会话持久化工作区中的文本文件。"""
    return await asyncio.to_thread(read_workspace_text_file, conversation_id, path, max_bytes, user_id)


async def read_conversation_workspace_raw_file(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
    max_bytes: int = 20 * 1024 * 1024,
) -> dict[str, Any]:
    """读取会话工作区中的原始字节文件（用于 PDF/Word 等二进制预览/下载）。"""
    return await asyncio.to_thread(read_workspace_raw_file, conversation_id, path, max_bytes, user_id)


def _validate_office_preview_source(workspace_root: str, path: str) -> None:
    target = resolve_workspace_path(workspace_root, path)
    if is_system_result_workspace_path(workspace_root, target):
        raise ValueError("工具结果缓存不支持 Office 预览")
    if target.suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
        raise ValueError("OfficeCLI HTML 预览仅支持 .docx、.xlsx、.pptx 文件")
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    if target.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("文件过大，暂不支持预览超过 50MB 的 Office 文件")


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
        html = preview_file.read_text(encoding="utf-8", errors="replace")
    finally:
        try:
            os.unlink(preview_path)
        except OSError:
            pass
    truncated = len(html) > limit
    return {
        "renderer": "officecli-html",
        "source_path": str(target),
        "html": html[:limit],
        "truncated": truncated,
    }


async def render_conversation_workspace_office_preview(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
) -> dict[str, Any]:
    """Render an owned Office file from the application-side authoritative copy."""
    workspace = ensure_conversation_workspace(conversation_id, user_id)
    workspace_root = workspace["root"]
    await asyncio.to_thread(
        _validate_office_preview_source,
        workspace_root,
        path,
    )
    runtime = ConversationWorkspaceRuntime(
        workspace_root=workspace_root,
        user_id=user_id,
        backend="local",
    )
    preview = await runtime.render_office_html(
        file_path=path,
        max_chars=OFFICE_PREVIEW_MAX_CHARS,
    )
    html = str(preview.get("html") or "")
    if bool(preview.get("truncated")):
        return {"available": False, "reason": "preview_too_large"}
    if not html:
        raise RuntimeError("OfficeCLI 未返回 HTML 预览内容")
    return {
        "available": True,
        "renderer": "officecli-html",
        "html": html,
        "truncated": False,
    }


def read_workspace_raw_file(
    conversation_id: str,
    path: str,
    max_bytes: int = 20 * 1024 * 1024,
    user_id: str = "system",
) -> dict[str, Any]:
    """读取会话工作区中的原始字节文件，返回 bytes 与 mime 类型。"""
    paths = ensure_conversation_workspace(conversation_id, user_id)
    root = paths["root"]
    target = resolve_workspace_path(root, path)
    if is_system_result_workspace_path(root, target):
        raise ValueError("工具结果缓存不在工作区文件预览范围内")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError(f"文件过大，暂不支持预览超过 {max_bytes} 字节的文件")
    mime, _ = mimetypes.guess_type(str(target))
    return {
        "path": to_visible_path(root, target),
        "relative_path": target.relative_to(Path(root).resolve()).as_posix(),
        "data": target.read_bytes(),
        "size": size,
        "mime": mime or "application/octet-stream",
    }


async def read_conversation_query_result(
    conversation_id: str,
    *,
    user_id: str,
    result_id: str,
) -> dict[str, Any] | None:
    """读取当前用户当前会话内被卸载的大查询结果。"""
    return await asyncio.to_thread(
        ToolResultStore().load_query_result_for_conversation,
        result_id=result_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )


def _ensure_workspace_root_in_chat_data_home(workspace_root: str) -> None:
    """确保服务端直接读取的会话根仍属于 ChatData 共享数据目录。"""
    root = Path(CHAT_DATA_HOME).expanduser().resolve()
    target = Path(workspace_root).expanduser().resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")



def create_workspace_backend(name: str) -> WorkspaceBackend:
    """Create a server-registered backend; unknown names fail closed."""
    # 惰性 import：后端模块反向 import 本模块头部符号，顶层互相 import 会成环。
    from .workspace_local_backend import LocalWorkspaceBackend
    from .workspace_sandbox_backend import SandboxWorkspaceBackend

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


def _officecli_selector_token_indices(tokens: list[str]) -> set[int]:
    """Exclude OfficeCLI DOM selectors from host-path validation."""
    selector_indices: set[int] = set()
    selector_commands = {"get", "query", "set", "add", "remove"}
    for officecli_index, token in enumerate(tokens):
        if Path(token).name not in {"officecli", "officecli.exe"}:
            continue
        verb_index = next(
            (
                index
                for index in range(officecli_index + 1, len(tokens))
                if tokens[index] in selector_commands | {"move", "swap"}
            ),
            None,
        )
        if verb_index is None:
            continue
        verb = tokens[verb_index]
        if verb in selector_commands and verb_index + 2 < len(tokens):
            selector_indices.add(verb_index + 2)
        elif verb == "move":
            if verb_index + 2 < len(tokens):
                selector_indices.add(verb_index + 2)
            for option in ("--to", "--after", "--before"):
                for index in range(verb_index + 3, len(tokens) - 1):
                    if tokens[index] == option:
                        selector_indices.add(index + 1)
        elif verb == "swap":
            selector_indices.update(
                index for index in (verb_index + 2, verb_index + 3) if index < len(tokens)
            )
    return selector_indices


def __getattr__(name: str):
    """向后兼容 re-export：后端实现已拆至独立模块，惰性解析避免 import 成环。"""
    if name == "LocalWorkspaceBackend":
        from .workspace_local_backend import LocalWorkspaceBackend

        return LocalWorkspaceBackend
    if name == "SandboxWorkspaceBackend":
        from .workspace_sandbox_backend import SandboxWorkspaceBackend

        return SandboxWorkspaceBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
