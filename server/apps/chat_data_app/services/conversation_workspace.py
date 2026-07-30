"""ChatData 会话工作区及本地 / OpenSandbox 后端适配。"""

from __future__ import annotations

import asyncio
import datetime
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Protocol, runtime_checkable

from utils.database import get_session
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT, runtime_skill_visible_root
from utils.storage_paths import CHAT_DATA_HOME, normalize_conversation_id, normalize_user_id
from utils.tool_result_store import TOOL_RESULTS_DIR_NAME, ToolResultStore

from ..daos.conversation_dao import ConversationDao
from .artifact_registry import parse_output_artifact
from .runtime_skill_snapshot import (
    build_runtime_skill_snapshot_async,
    is_runtime_skill_snapshot_ready,
    runtime_skills_snapshot_dir_from_workspace,
)
from .semantic_common import AGENT_ID
from .skill_service import resolve_skill_name
from .workspace_execution import (
    OPENSANDBOX_BACKEND,
    SandboxExecutionSessionRef,
    get_workspace_execution_session,
    run_command_in_sandbox,
    sandbox_apply_patch_ops,
    sandbox_artifact_payload,
    sandbox_list_dir,
    sandbox_load_skill_payload,
    sandbox_outputs_dir,
    sandbox_path_to_visible,
    sandbox_read_text_file,
    sandbox_write_tool_result,
    sync_sandbox_working_trees,
    sandbox_visible_path,
    sandbox_write_text_file,
    sandbox_workspace_dir,
)
from .workspace_patch import PatchOp, apply_patch_ops

logger = get_logger(__name__)

THREADS_DIR = Path(CHAT_DATA_HOME) / "threads"
WORKSPACE_DIR_NAMES = ("workspace", "outputs", "uploads")
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

    async def write_tool_result(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        file_path: str,
        content: str,
    ) -> dict[str, Any]: ...

    async def apply_patch_ops(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        ops: list[PatchOp],
        dry_run: bool,
    ) -> list[str]: ...

    async def artifact_payload(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        file_path: str,
    ) -> dict[str, Any]: ...

    async def load_skill_payload(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        skill_name: str,
        max_chars: int,
    ) -> dict[str, Any]: ...

    async def list_dir(
        self,
        runtime: "ConversationWorkspaceRuntime",
        *,
        path: str,
        max_depth: int,
    ) -> list[dict[str, Any]]: ...

    async def get_sandbox_session_ref(
        self,
        runtime: "ConversationWorkspaceRuntime",
    ) -> SandboxExecutionSessionRef: ...

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

    async def get_sandbox_session_ref(self) -> SandboxExecutionSessionRef:
        """Return the execution session for ``bash_run`` when the backend supports it."""
        return await self.backend.get_sandbox_session_ref(self)

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

    async def write_tool_result(self, *, file_path: str, content: str) -> dict[str, Any]:
        """Persist a middleware-owned hidden tool result through the active backend."""
        return await self.backend.write_tool_result(self, file_path=file_path, content=content)

    async def apply_patch_ops(self, *, ops: list[PatchOp], dry_run: bool) -> list[str]:
        return await self.backend.apply_patch_ops(self, ops=ops, dry_run=dry_run)

    async def artifact_payload(self, *, file_path: str) -> dict[str, Any]:
        return await self.backend.artifact_payload(self, file_path=file_path)

    async def load_skill_payload(self, *, skill_name: str, max_chars: int) -> dict[str, Any]:
        return await self.backend.load_skill_payload(self, skill_name=skill_name, max_chars=max_chars)

    async def list_dir(self, *, path: str, max_depth: int) -> list[dict[str, Any]]:
        return await self.backend.list_dir(self, path=path, max_depth=max_depth)

    async def validate_bash_paths(self, *, command: str) -> None:
        await self.backend.validate_bash_paths(self, command=command)

    async def run_bash(
        self,
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        return await self.backend.run_bash(
            self, command=command, cwd=cwd, timeout_seconds=timeout_seconds
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
    (root / "outputs" / TOOL_RESULTS_DIR_NAME).mkdir(parents=True, exist_ok=True)
    return paths


def resolve_workspace_path(workspace_root: str, path: str, *, writable: bool = False) -> Path:
    """解析模型可见路径并按解析后的真实路径实施目录边界。"""
    root = Path(workspace_root).resolve()
    raw = str(path or "").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        raise ValueError("必须指定 workspace、outputs 或 uploads 下的路径")

    alias_map = {
        "/mnt/user-data/workspace": "workspace",
        "/mnt/user-data/outputs": "outputs",
        "/mnt/user-data/uploads": "uploads",
        "/workspace": "workspace",
        "/outputs": "outputs",
        "/uploads": "uploads",
    }
    for prefix, alias in alias_map.items():
        if raw == prefix or raw.startswith(f"{prefix}/"):
            raw = f"{alias}/{raw[len(prefix):].lstrip('/')}"
            break

    if os.path.isabs(raw):
        raise ValueError(f"只允许访问当前会话目录内的相对路径: {path}")

    target = (root / raw).resolve()
    if root not in target.parents:
        raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")

    visible_roots = [(root / name).resolve() for name in WORKSPACE_DIR_NAMES]
    if not any(target == item or item in target.parents for item in visible_roots):
        raise ValueError("只允许访问 workspace/、outputs/ 或 uploads/")

    if writable:
        writable_roots = [(root / "workspace").resolve(), (root / "outputs").resolve()]
        if not any(target == item or item in target.parents for item in writable_roots):
            raise ValueError("只允许写入 workspace/ 或 outputs/")
        if is_tool_result_workspace_path(str(root), target):
            raise ValueError("不允许通过文件编辑工具修改 outputs/.tool-results/")
    return target


def is_tool_result_workspace_path(workspace_root: str, path: Path) -> bool:
    """Return whether a resolved path belongs to hidden tool-result storage."""
    results_root = (Path(workspace_root).resolve() / "outputs" / TOOL_RESULTS_DIR_NAME).resolve()
    target = path.resolve()
    return target == results_root or results_root in target.parents


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
    if is_tool_result_workspace_path(workspace_root, skill_dir):
        raise ValueError("不允许导入 outputs/.tool-results/ 下的内容")
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
                if is_tool_result_workspace_path(str(root), resolved_item):
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
    if is_tool_result_workspace_path(root, target):
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


_TEXT_READ_CHUNK_CHARS = 64 * 1024
_TEXT_LINE_SEPARATOR_RE = re.compile(r"[\n\v\f\x1c-\x1e\x85\u2028\u2029]")
MAX_FILE_READ_LINE_CHARS = 500


def _iter_text_line_fragments(file_obj: Any):
    """逐块产出文本行片段，保持 ``str.splitlines()`` 的行分隔语义。

    ``newline=None`` 已将 CR / CRLF 标准化为 LF；其余 Python 识别的行分隔符
    仍需显式处理，避免大文件流式读取改变 ``offset`` 的按行语义。
    """
    while chunk := file_obj.read(_TEXT_READ_CHUNK_CHARS):
        position = 0
        for match in _TEXT_LINE_SEPARATOR_RE.finditer(chunk):
            yield chunk[position:match.start()], True
            position = match.end()
        if position < len(chunk):
            yield chunk[position:], False


def _ensure_workspace_root_in_chat_data_home(workspace_root: str) -> None:
    """确保服务端直接读取的会话根仍属于 ChatData 共享数据目录。"""
    root = Path(CHAT_DATA_HOME).expanduser().resolve()
    target = Path(workspace_root).expanduser().resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"会话工作区不在 CHAT_DATA_HOME 内: {workspace_root}")


def _allocate_line_budgets(line_lengths: list[int], max_chars: int) -> list[int]:
    """按源顺序分配正文预算，单行最多保留固定字符数。"""
    budgets: list[int] = []
    remaining = max(0, int(max_chars))
    for original_length in line_lengths:
        separator_chars = 1 if budgets else 0
        if remaining < separator_chars:
            break
        desired_chars = min(max(0, int(original_length)), MAX_FILE_READ_LINE_CHARS)
        available_chars = remaining - separator_chars
        if desired_chars > available_chars:
            if available_chars <= 0 and desired_chars > 0:
                break
            budgets.append(max(0, available_chars))
            break
        budgets.append(desired_chars)
        remaining -= desired_chars + separator_chars
    return budgets


def _scan_text_page_line_lengths(path: Path, *, offset: int, limit: int) -> dict[str, Any]:
    selected_lines: list[dict[str, int]] = []
    line_index = 0
    pending_chars = 0

    def finish_line() -> bool:
        nonlocal line_index, pending_chars
        if offset <= line_index < offset + limit:
            selected_lines.append({
                "line_number": line_index + 1,
                "original_chars": pending_chars,
            })
        elif line_index >= offset + limit:
            return True
        line_index += 1
        pending_chars = 0
        return False

    with path.open("r", encoding="utf-8", errors="replace", newline=None) as file_obj:
        for part, ends_line in _iter_text_line_fragments(file_obj):
            pending_chars += len(part)
            if ends_line and finish_line():
                return {"selected_lines": selected_lines, "has_more_lines": True}
            if len(selected_lines) == limit and pending_chars:
                return {"selected_lines": selected_lines, "has_more_lines": True}
    if pending_chars:
        if finish_line():
            return {"selected_lines": selected_lines, "has_more_lines": True}

    return {"selected_lines": selected_lines, "has_more_lines": False, "observed_lines": line_index}


def _read_text_page_prefixes(path: Path, selected_lines: list[dict[str, int]], budgets: list[int]) -> list[str]:
    selected_by_index = {
        item["line_number"] - 1: (position, item)
        for position, item in enumerate(selected_lines)
    }
    captured_parts = [[] for _ in selected_lines]
    captured_chars = [0 for _ in selected_lines]
    current_line_index = 0
    current_line_char = 0
    last_line_index = selected_lines[-1]["line_number"] - 1

    def consume(part: str) -> None:
        nonlocal current_line_char
        selected = selected_by_index.get(current_line_index)
        if selected is not None:
            position, _metadata = selected
            remaining = budgets[position] - captured_chars[position]
            if remaining > 0:
                piece = part[:remaining]
                captured_parts[position].append(piece)
                captured_chars[position] += len(piece)
        current_line_char += len(part)

    def finish_line() -> bool:
        nonlocal current_line_index, current_line_char
        current_line_index += 1
        current_line_char = 0
        return current_line_index > last_line_index

    with path.open("r", encoding="utf-8", errors="replace", newline=None) as file_obj:
        for part, ends_line in _iter_text_line_fragments(file_obj):
            consume(part)
            if ends_line and finish_line():
                return ["".join(parts) for parts in captured_parts]
    return ["".join(parts) for parts in captured_parts]


def _read_text_page_from_path(
    target: Path,
    visible_path: str,
    *,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    if not target.exists():
        return {"exists": False, "path": visible_path}
    if target.is_dir():
        return {"exists": True, "is_dir": True, "path": visible_path}
    with target.open("rb") as file_obj:
        sample = file_obj.read(4096)
    if b"\x00" in sample:
        return {"exists": True, "is_binary": True, "path": visible_path}

    page = _scan_text_page_line_lengths(target, offset=offset, limit=limit)
    selected_lines = list(page["selected_lines"])
    if not selected_lines:
        observed_lines = int(page.get("observed_lines") or 0)
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "total_lines": observed_lines,
            "empty": observed_lines == 0,
            "offset_out_of_range": observed_lines > 0,
        }
    budgets = _allocate_line_budgets(
        [item["original_chars"] for item in selected_lines],
        max_chars,
    )
    selected_lines = selected_lines[:len(budgets)]
    if not selected_lines:
        return {
            "exists": True,
            "is_dir": False,
            "is_binary": False,
            "path": visible_path,
            "empty": True,
        }
    lines = _read_text_page_prefixes(target, selected_lines, budgets)
    truncated_lines = [
        {
            "line_number": item["line_number"],
            "original_chars": item["original_chars"],
            "returned_chars": budget,
        }
        for item, budget in zip(selected_lines, budgets)
        if budget < item["original_chars"]
    ]
    has_more_lines = bool(page["has_more_lines"]) or len(budgets) < len(page["selected_lines"])
    return {
        "exists": True,
        "is_dir": False,
        "is_binary": False,
        "path": visible_path,
        "lines": lines,
        "total_lines": None,
        "start_line": selected_lines[0]["line_number"],
        "end_line": selected_lines[-1]["line_number"],
        "next_offset": offset + len(selected_lines) if has_more_lines else None,
        "has_more_lines": has_more_lines,
        "has_more": has_more_lines,
        "truncated_lines": truncated_lines,
    }


def _read_local_text_file(
    workspace_root: str,
    file_path: str,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    target = resolve_workspace_path(workspace_root, file_path)
    return _read_text_page_from_path(
        target,
        to_visible_path(workspace_root, target),
        offset=offset,
        limit=limit,
        max_chars=max_chars,
    )


def _read_local_runtime_skill_file(
    target: Path,
    visible_path: str,
    offset: int,
    limit: int,
    max_chars: int,
) -> dict[str, Any]:
    return _read_text_page_from_path(
        target,
        visible_path,
        offset=offset,
        limit=limit,
        max_chars=max_chars,
    )


def _write_local_text_file(workspace_root: str, file_path: str, content: str, mode: str) -> dict[str, Any]:
    target = resolve_workspace_path(workspace_root, file_path, writable=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w" if mode == "overwrite" else "a", encoding="utf-8") as file_obj:
        file_obj.write(content)
    return {
        "operation": "write",
        "file_path": to_visible_path(workspace_root, target),
        "mode": mode,
        "bytes": len(content.encode("utf-8")),
    }


def _write_local_tool_result(workspace_root: str, file_path: str, content: str) -> dict[str, Any]:
    """Write a private result once; this path is never exposed to file_edit."""
    target = resolve_workspace_path(workspace_root, file_path)
    if not is_tool_result_workspace_path(workspace_root, target):
        raise ValueError("内部工具结果只能写入 outputs/.tool-results/")
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    with target.open("xb") as file_obj:
        file_obj.write(data)
    return {"file_path": to_visible_path(workspace_root, target), "bytes": len(data)}


def _load_local_artifact_payload(workspace_root: str, file_path: str) -> dict[str, Any]:
    try:
        target = resolve_workspace_path(workspace_root, file_path)
    except ValueError:
        return {}
    if is_tool_result_workspace_path(workspace_root, target):
        return {}
    outputs_root = resolve_workspace_path(workspace_root, "outputs")
    if target != outputs_root and outputs_root not in target.parents:
        return {}
    if not target.is_file():
        return {}
    try:
        return parse_output_artifact(
            visible_path=to_visible_path(workspace_root, target),
            data=target.read_bytes(),
        )
    except OSError:
        return {}


def _list_skill_files(snapshot_dir: Path, directory_name: str, pattern: str = "*") -> list[str]:
    root = snapshot_dir / directory_name
    if not root.exists() or root.is_symlink():
        return []
    return sorted(
        str(item.relative_to(root).with_suffix("")) if directory_name == "references" else str(item.relative_to(root))
        for item in root.rglob(pattern)
        if item.is_file() and not item.is_symlink()
    )


def _build_local_skill_payload(runtime: ConversationWorkspaceRuntime, skill_name: str, max_chars: int) -> dict[str, Any]:
    resolved_name = resolve_skill_name(skill_name)
    snapshot_root = runtime.runtime_skills_dir.resolve()
    snapshot_entry = runtime.runtime_skills_dir / resolved_name
    snapshot_dir = snapshot_entry.resolve()
    if (
        snapshot_entry.is_symlink()
        or not snapshot_dir.is_dir()
        or snapshot_root not in snapshot_dir.parents
    ):
        raise ValueError(f"技能不存在或无权限访问: {resolved_name}")
    skill_md_path = (snapshot_dir / "SKILL.md").resolve()
    if not skill_md_path.is_file() or snapshot_dir not in skill_md_path.parents:
        raise ValueError(f"技能文件不存在: {resolved_name}")
    skill_md = _read_text_prefix(skill_md_path, max_chars + 1)
    title, description = _parse_skill_frontmatter(skill_md)
    return {
        "name": resolved_name,
        "title": title or resolved_name,
        "description": description or "",
        "visible_path": runtime_skill_visible_root(resolved_name),
        "skill_md": skill_md,
        "references": _list_skill_files(snapshot_dir, "references", "*.md"),
        "scripts": _list_skill_files(snapshot_dir, "scripts"),
        "assets": _list_skill_files(snapshot_dir, "assets"),
    }


def _read_text_prefix(path: Path, max_chars: int) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as file_obj:
        return file_obj.read(max_chars)


def _list_local_workspace_dir(
    runtime: ConversationWorkspaceRuntime,
    path: str,
    max_depth: int,
) -> list[dict[str, Any]]:
    raw = str(path or "workspace").strip().replace("\\", "/")
    if raw.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
        target, visible_root = _resolve_local_skill_path(runtime, raw)
        is_skill = True
    else:
        target = resolve_workspace_path(runtime.workspace_root, raw)
        if is_tool_result_workspace_path(runtime.workspace_root, target):
            raise ValueError("outputs/.tool-results/ 是隐藏的工具结果缓存，不能列出")
        visible_root = ""
        is_skill = False
    if not target.exists():
        raise FileNotFoundError(f"目录不存在: {raw}")
    if not target.is_dir():
        raise ValueError(f"目标不是目录: {raw}")

    entries: list[dict[str, Any]] = []
    root = Path(runtime.workspace_root).resolve()

    def visit(directory: Path, visible_dir: str, depth: int) -> None:
        if len(entries) >= 500:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name))
        except OSError:
            return
        for child in children:
            if len(entries) >= 500:
                return
            resolved = child.resolve()
            if child.is_symlink():
                continue
            if not is_skill and is_tool_result_workspace_path(runtime.workspace_root, resolved):
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            if is_skill:
                visible_path = f"{visible_dir}/{child.name}"
            else:
                try:
                    visible_path = "/mnt/user-data/" + resolved.relative_to(root).as_posix()
                except ValueError:
                    continue
            is_dir = child.is_dir()
            entries.append({
                "type": "dir" if is_dir else "file",
                "path": visible_path + ("/" if is_dir else ""),
                "size": "-" if is_dir else stat.st_size,
                "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
            if is_dir and depth + 1 < max_depth:
                visit(child, visible_path, depth + 1)

    visit(target, visible_root, 0)
    return entries


def _resolve_local_skill_path(runtime: ConversationWorkspaceRuntime, path: str) -> tuple[Path, str]:
    raw = str(path or "").strip().replace("\\", "/")
    prefix = f"{SKILLS_VIRTUAL_ROOT}/"
    if not raw.startswith(prefix):
        raise ValueError(f"只允许访问技能路径: {path}")
    parts = PurePosixPath(raw).parts
    if len(parts) < 4 or parts[1:3] != ("mnt", "skills") or ".." in parts[4:]:
        raise ValueError(f"技能路径无效: {path}")
    name = parts[3]
    snapshot_root = runtime.runtime_skills_dir.resolve()
    source_entry = runtime.runtime_skills_dir / name
    source = source_entry.resolve()
    if (
        source_entry.is_symlink()
        or not source.is_dir()
        or snapshot_root not in source.parents
    ):
        raise ValueError(f"技能不存在或无权限访问: {name}")
    target = source.joinpath(*parts[4:]).resolve()
    if target != source and source not in target.parents:
        raise ValueError(f"技能路径越界: {path}")
    return target, runtime_skill_visible_root(name) + (
        "/" + "/".join(parts[4:]) if parts[4:] else ""
    )


def _parse_skill_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", ""
    closing_index = text.find("\n---\n", len("---\n"))
    if closing_index < 0:
        return "", ""
    title = name = description = ""
    for line in text[len("---\n"):closing_index].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = value.strip().strip('"').strip("'")
        if key.strip() == "title":
            title = normalized
        elif key.strip() == "name":
            name = normalized
        elif key.strip() == "description":
            description = normalized
    return title or name, description


@dataclass
class LocalWorkspaceBackend:
    """Path-validated local filesystem backend for a conversation workspace."""

    name: str = "local"

    async def read_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        offset: int,
        limit: int,
        max_chars: int,
    ) -> dict[str, Any]:
        raw_path = str(file_path or "").strip()
        if raw_path.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            target, visible_path = await runtime.resolve_readonly_skill_path_async(file_path)
            return await asyncio.to_thread(
                _read_local_runtime_skill_file, target, visible_path, offset, limit, max_chars
            )
        _ensure_workspace_root_in_chat_data_home(runtime.workspace_root)
        return await asyncio.to_thread(
            _read_local_text_file, runtime.workspace_root, file_path, offset, limit, max_chars
        )

    async def write_text_file(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
        mode: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(_write_local_text_file, runtime.workspace_root, file_path, content, mode)

    async def write_tool_result(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
        content: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            _write_local_tool_result, runtime.workspace_root, file_path, content
        )

    async def apply_patch_ops(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        ops: list[PatchOp],
        dry_run: bool,
    ) -> list[str]:
        return await asyncio.to_thread(
            apply_patch_ops,
            ops,
            resolve_writable_path=lambda path: resolve_workspace_path(runtime.workspace_root, path, writable=True),
            dry_run=dry_run,
        )

    async def artifact_payload(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        file_path: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(_load_local_artifact_payload, runtime.workspace_root, file_path)

    async def load_skill_payload(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        skill_name: str,
        max_chars: int,
    ) -> dict[str, Any]:
        await runtime._ensure_snapshot_async()
        return await asyncio.to_thread(_build_local_skill_payload, runtime, skill_name, max_chars)

    async def list_dir(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        path: str,
        max_depth: int,
    ) -> list[dict[str, Any]]:
        if str(path or "").strip().startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            await runtime._ensure_snapshot_async()
        return await asyncio.to_thread(
            _list_local_workspace_dir, runtime, path, max_depth
        )

    async def get_sandbox_session_ref(
        self,
        runtime: ConversationWorkspaceRuntime,
    ) -> SandboxExecutionSessionRef:
        raise ValueError("当前工作区后端不支持 OpenSandbox 执行")

    async def validate_bash_paths(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
    ) -> None:
        for token in _absolute_command_path_tokens(command):
            try:
                if token.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
                    await runtime.resolve_readonly_skill_path_async(token)
                else:
                    resolve_workspace_path(runtime.workspace_root, token)
            except ValueError as exc:
                raise ValueError(
                    "bash_run 只允许访问当前会话目录或当前用户有权访问的技能路径: " + token
                ) from exc

    async def run_bash(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        command: str,
        cwd: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        raw_cwd = str(cwd or "workspace").strip()
        if raw_cwd.startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            local_cwd = (await runtime.resolve_readonly_skill_path_async(raw_cwd))[0]
        else:
            local_cwd = resolve_workspace_path(runtime.workspace_root, raw_cwd)
        path_mapping = WorkspacePathMapping(
            workspace_root=runtime.workspace_root,
            runtime_skills_dir=runtime.runtime_skills_dir,
        )
        inner = "cd {cwd} && {command}".format(
            cwd=shlex.quote(str(local_cwd)),
            command=path_mapping.translate(command),
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
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": to_visible_path(runtime.workspace_root, local_cwd),
                "exit_code": int(process.returncode or 0),
                "stdout": path_mapping.mask(stdout_bytes.decode("utf-8", errors="replace")),
                "stderr": path_mapping.mask(stderr_bytes.decode("utf-8", errors="replace")),
                "timed_out": False,
            }
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "backend": self.name,
                "sandbox_id": "",
                "cwd": to_visible_path(runtime.workspace_root, local_cwd),
                "exit_code": 124,
                "stdout": "",
                "stderr": "command timed out",
                "timed_out": True,
            }


@dataclass
class SandboxWorkspaceBackend:
    """OpenSandbox backend; all remote filesystem calls stay behind this adapter."""

    name: str = "sandbox"
    session_ref: SandboxExecutionSessionRef = field(default_factory=SandboxExecutionSessionRef)

    async def _session_ref(self, runtime: ConversationWorkspaceRuntime) -> SandboxExecutionSessionRef:
        if self.session_ref.current is None:
            await runtime._ensure_snapshot_async()
            self.session_ref.current = await get_workspace_execution_session(
                runtime.workspace_root,
                runtime.user_id,
                runtime_skills_dir=runtime.runtime_skills_dir,
            )
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

    async def apply_patch_ops(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        ops: list[PatchOp],
        dry_run: bool,
    ) -> list[str]:
        return await sandbox_apply_patch_ops(
            session_ref=await self._session_ref(runtime),
            ops=ops,
            dry_run=dry_run,
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

    async def load_skill_payload(
        self,
        runtime: ConversationWorkspaceRuntime,
        *,
        skill_name: str,
        max_chars: int,
    ) -> dict[str, Any]:
        await runtime._ensure_snapshot_async()
        return await sandbox_load_skill_payload(
            session_ref=await self._session_ref(runtime), skill_name=resolve_skill_name(skill_name), max_chars=max_chars
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

    async def get_sandbox_session_ref(
        self,
        runtime: ConversationWorkspaceRuntime,
    ) -> SandboxExecutionSessionRef:
        return await self._session_ref(runtime)

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


def create_workspace_backend(name: str) -> WorkspaceBackend:
    """Create a backend once at runtime construction; invalid input falls back to local."""
    return SandboxWorkspaceBackend() if str(name or "").strip().lower() == "sandbox" else LocalWorkspaceBackend()


def _absolute_command_path_tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"command 解析失败: {exc}") from exc
    invalid = []
    paths: list[str] = []
    for token in tokens:
        normalized = token.lstrip("<>")
        if ".." in PurePosixPath(normalized).parts:
            invalid.append(token)
        elif normalized.startswith("/"):
            paths.append(normalized)
    if invalid:
        raise ValueError("bash_run 不允许路径越界: " + ", ".join(invalid[:3]))
    return paths
