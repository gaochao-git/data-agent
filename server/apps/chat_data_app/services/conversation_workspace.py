"""ChatData 会话工作区及本地 / OpenSandbox 后端适配。"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Literal

from utils.database import get_session
from utils.logger import get_logger
from utils.skill_paths import SKILLS_VIRTUAL_ROOT, is_valid_skill_category, skill_virtual_root
from utils.storage_paths import CHAT_DATA_HOME, normalize_conversation_id, normalize_user_id

from ..daos.conversation_dao import ConversationDao
from .artifact_registry import parse_output_artifact
from .semantic_common import AGENT_ID
from .skill_service import skill_service
from .workspace_execution import (
    SandboxExecutionSessionRef,
    get_workspace_execution_session,
    sandbox_apply_patch_ops,
    sandbox_artifact_payload,
    invalidate_workspace_execution_session,
    sandbox_read_text_file,
    sandbox_write_text_file,
)

logger = get_logger(__name__)

THREADS_DIR = Path(CHAT_DATA_HOME) / "users"
WORKSPACE_DIR_NAMES = ("workspace", "outputs", "uploads")
conversation_dao = ConversationDao(session_context=lambda: get_session())


@dataclass(frozen=True)
class RuntimeSkillMount:
    """一个经权限校验后可在当前运行中只读访问的技能目录。"""

    name: str
    category: Literal["system", "user"]
    source_dir: Path

    @property
    def visible_root(self) -> str:
        return skill_virtual_root(self.category, self.name)


@dataclass
class ConversationWorkspaceRuntime:
    """当前会话工作区的后端绑定。

    本地后端始终直接访问 ``workspace_root``。OpenSandbox session 延迟到实际文件或
    Bash 调用时创建，避免只做问数时要求 OpenSandbox 配置。
    """

    workspace_root: str
    user_id: str
    backend: Literal["local", "sandbox"]
    sandbox_session_ref: SandboxExecutionSessionRef = field(default_factory=SandboxExecutionSessionRef)
    runtime_skills: dict[str, RuntimeSkillMount] = field(default_factory=dict)
    # 当前用户有权访问的全部技能名（由请求入口按 user_id 解析得到）。
    # 仅用于沙箱模式预挂载，不作为本地读取的前置条件。
    available_skill_names: list[str] = field(default_factory=list)

    @staticmethod
    def _skill_key(category: str, name: str) -> str:
        return f"{category}/{name}"

    def runtime_skill_mounts(self) -> list[RuntimeSkillMount]:
        return list(self.runtime_skills.values())

    async def register_runtime_skill(
        self,
        *,
        name: str,
        category: Literal["system", "user"],
        source_dir: Path,
    ) -> str:
        """登记本轮可读技能；已创建沙箱时在下次访问前重建只读挂载。"""
        normalized_name = _safe_segment(name)
        resolved_source = source_dir.expanduser().resolve()
        if not resolved_source.is_dir():
            raise ValueError(f"技能目录不存在: {name}")
        key = self._skill_key(category, normalized_name)
        mount = RuntimeSkillMount(
            name=normalized_name,
            category=category,
            source_dir=resolved_source,
        )
        if self.runtime_skills.get(key) == mount:
            return mount.visible_root
        self.runtime_skills[key] = mount
        if self.backend == "sandbox" and self.sandbox_session_ref.current is not None:
            await invalidate_workspace_execution_session(self.sandbox_session_ref.current, self.user_id)
            self.sandbox_session_ref.current = None
        return mount.visible_root

    async def resolve_readonly_skill_path_async(self, path: str) -> tuple[Path, str]:
        """将 /mnt/skills 路径解析到真实文件系统。

        不再依赖本运行显式「加载」登记（无注册门控，对齐 deer-flow：技能发现基于
        磁盘/数据库，读取等价于普通文件读取）。解析时按 ``user_id`` 做权限校验，只允许
        访问当前用户有权读到的技能（system=系统内置，user=该用户自己的技能）。命中后
        结果缓存在 ``runtime_skills`` 以便同请求内复用，但缓存不作为访问前提。
        """
        raw = str(path or "").strip().replace("\\", "/")
        prefix = f"{SKILLS_VIRTUAL_ROOT}/"
        if not raw.startswith(prefix):
            raise ValueError(f"只允许访问技能路径: {path}")
        parts = PurePosixPath(raw).parts
        if len(parts) < 5 or parts[1:3] != ("mnt", "skills"):
            raise ValueError(f"技能路径无效: {path}")
        category, name = parts[3], parts[4]
        if not is_valid_skill_category(category):
            raise ValueError(f"技能路径无效: {path}")
        mount = await self._resolve_skill_mount_async(category, name)
        relative_parts = parts[5:]
        if ".." in relative_parts:
            raise ValueError(f"技能路径越界: {path}")
        target = (mount.source_dir.joinpath(*relative_parts)).resolve()
        if target != mount.source_dir and mount.source_dir not in target.parents:
            raise ValueError(f"技能路径越界: {path}")
        visible = skill_virtual_root(category, name)
        if relative_parts:
            visible += "/" + "/".join(relative_parts)
        return target, visible

    async def _resolve_skill_mount_async(self, category: str, name: str) -> RuntimeSkillMount:
        """按权限解析技能真实目录，命中则缓存。

        不要求本运行显式 ``skill_load``。先查缓存；未命中则经 ``skill_service`` 做
        磁盘/数据库解析与权限校验。解析出的真实类别必须与路径中的类别一致，否则拒绝。
        """
        key = self._skill_key(category, name)
        cached = self.runtime_skills.get(key)
        if cached is not None:
            return cached
        metadata = await skill_service.resolve_skill_path_async(name, user_id=self.user_id)
        if not metadata:
            raise ValueError(f"技能不存在或无权限访问: {name}")
        actual_category = _runtime_skill_category(metadata)
        if actual_category != category:
            raise ValueError(f"技能路径类别不匹配: {category}/{name}")
        source_md = Path(str(metadata.get("path") or "")).resolve()
        source_dir = source_md.parent
        if not source_dir.is_dir():
            raise ValueError(f"技能目录不存在: {name}")
        mount = RuntimeSkillMount(name=name, category=category, source_dir=source_dir)
        self.runtime_skills[key] = mount
        return mount

    async def _resolve_skill_mount_async_by_name(self, name: str) -> RuntimeSkillMount | None:
        """按名解析技能挂载（类别未知时由元数据决定），失败返回 None。"""
        metadata = await skill_service.resolve_skill_path_async(name, user_id=self.user_id)
        if not metadata:
            return None
        category = _runtime_skill_category(metadata)
        source_md = Path(str(metadata.get("path") or "")).resolve()
        source_dir = source_md.parent
        if not source_dir.is_dir():
            return None
        return RuntimeSkillMount(name=name, category=category, source_dir=source_dir)

    async def get_sandbox_session_ref(self) -> SandboxExecutionSessionRef:
        if self.backend != "sandbox":
            raise ValueError("当前工作区后端不是 OpenSandbox")
        if self.sandbox_session_ref.current is None:
            # 挂载当前用户全部有权访问的技能（system + 自己的 user），而非仅本轮回显加载的，
            # 对齐 deer-flow：技能发现基于磁盘/数据库，沙箱内任意已授权技能路径均可读。
            await self._ensure_available_skill_mounts_async()
            self.sandbox_session_ref.current = await get_workspace_execution_session(
                self.workspace_root,
                self.user_id,
                runtime_skill_mounts=tuple(self.runtime_skill_mounts()),
            )
        return self.sandbox_session_ref

    async def _ensure_available_skill_mounts_async(self) -> None:
        """把 ``available_skill_names`` 中尚未缓存的技能解析并缓存，供沙箱挂载。"""
        for name in self.available_skill_names:
            if any(m.name == name for m in self.runtime_skills.values()):
                continue
            try:
                mount = await self._resolve_skill_mount_async_by_name(name)
                if mount is not None:
                    self.runtime_skills[self._skill_key(mount.category, mount.name)] = mount
            except Exception as exc:  # 单个技能解析失败不应阻断整个会话
                logger.debug("预解析技能挂载失败 name=%s: %s", name, exc)

    async def resolve_available_skill_mounts_async(self) -> tuple[RuntimeSkillMount, ...]:
        """解析并缓存当前用户全部可用技能，返回其挂载列表（供本地 bash 路径翻译）。"""
        await self._ensure_available_skill_mounts_async()
        return tuple(self.runtime_skill_mounts())


def _safe_segment(value: str) -> str:
    """规整路径段，避免会话 ID 造成目录越界。"""
    try:
        return normalize_conversation_id(value)
    except ValueError as exc:
        raise ValueError("非法会话 ID") from exc


def _runtime_skill_category(metadata: dict[str, Any]) -> Literal["system", "user"]:
    """校验技能来源并将其直接作为运行时可见目录类别。"""
    source = str(metadata.get("source") or "").strip()
    if source == "system":
        return "system"
    if source == "user":
        return "user"
    raise ValueError("技能来源无效")


def ensure_conversation_workspace(conversation_id: str, user_id: str = "system") -> dict[str, str]:
    """创建并返回 ChatData 会话目录结构。"""
    thread_id = _safe_segment(conversation_id)
    safe_user_id = normalize_user_id(user_id or "system")
    root = (THREADS_DIR / safe_user_id / "threads" / thread_id / "user-data").resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {"root": str(root)}
    for name in WORKSPACE_DIR_NAMES:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        paths[name.replace("-", "_")] = str(path)
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
    return target


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
      - 已登记技能：``/mnt/skills/{system,user}/<name>/...``

    - ``translate``：正向，把文本里的虚拟路径翻成物理路径，交给本地 subprocess。
    - ``mask``：反向，把输出里的物理路径还原为虚拟路径，避免向模型泄漏宿主机路径。
      边界感知，单次替换，物理根内含 ``/uploads`` 等段也不会自引用膨胀。
    """

    workspace_root: str
    skill_mounts: tuple[RuntimeSkillMount, ...] = ()

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
        # virtual_root -> 物理 source_dir；按虚拟根长度降序，避免子串误伤。
        self._skill_physical = {
            mount.visible_root: str(mount.source_dir) for mount in self.skill_mounts
        }

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
    from utils.query_result_store import QueryResultStore

    return await asyncio.to_thread(
        QueryResultStore().load_for_conversation,
        result_id=result_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )


def _read_local_text_file(workspace_root: str, file_path: str, offset: int, limit: int) -> dict[str, Any]:
    target = resolve_workspace_path(workspace_root, file_path)
    visible_path = to_visible_path(workspace_root, target)
    if not target.exists():
        return {"exists": False, "path": visible_path}
    if target.is_dir():
        return {"exists": True, "is_dir": True, "path": visible_path}
    data = target.read_bytes()
    if b"\x00" in data[:4096]:
        return {"exists": True, "is_binary": True, "path": visible_path}
    lines = data.decode("utf-8", errors="replace").splitlines()
    start = max(0, int(offset or 0))
    count = max(1, int(limit or 1))
    selected = lines[start:start + count]
    return {
        "exists": True,
        "is_dir": False,
        "is_binary": False,
        "path": visible_path,
        "lines": selected,
        "total_lines": len(lines),
        "start_line": start + 1,
        "end_line": start + len(selected),
        "has_more": start + len(selected) < len(lines),
    }


def _read_local_runtime_skill_file(
    target: Path,
    visible_path: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    if not target.exists():
        return {"exists": False, "path": visible_path}
    if target.is_dir():
        return {"exists": True, "is_dir": True, "path": visible_path}
    data = target.read_bytes()
    if b"\x00" in data[:4096]:
        return {"exists": True, "is_binary": True, "path": visible_path}
    lines = data.decode("utf-8", errors="replace").splitlines()
    start = max(0, int(offset or 0))
    count = max(1, int(limit or 1))
    selected = lines[start:start + count]
    return {
        "exists": True,
        "is_dir": False,
        "is_binary": False,
        "path": visible_path,
        "lines": selected,
        "total_lines": len(lines),
        "start_line": start + 1,
        "end_line": start + len(selected),
        "has_more": start + len(selected) < len(lines),
    }


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


async def workspace_read_text_file(
    runtime: ConversationWorkspaceRuntime,
    *,
    file_path: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    if runtime.backend == "local":
        if str(file_path or "").strip().startswith(f"{SKILLS_VIRTUAL_ROOT}/"):
            target, visible_path = await runtime.resolve_readonly_skill_path_async(file_path)
            return await asyncio.to_thread(
                _read_local_runtime_skill_file,
                target,
                visible_path,
                offset,
                limit,
            )
        return await asyncio.to_thread(
            _read_local_text_file,
            runtime.workspace_root,
            file_path,
            offset,
            limit,
        )
    return await sandbox_read_text_file(
        session_ref=await runtime.get_sandbox_session_ref(),
        file_path=file_path,
        offset=offset,
        limit=limit,
    )


async def workspace_write_text_file(
    runtime: ConversationWorkspaceRuntime,
    *,
    file_path: str,
    content: str,
    mode: str,
) -> dict[str, Any]:
    if runtime.backend == "local":
        return await asyncio.to_thread(
            _write_local_text_file,
            runtime.workspace_root,
            file_path,
            content,
            mode,
        )
    return await sandbox_write_text_file(
        session_ref=await runtime.get_sandbox_session_ref(),
        file_path=file_path,
        content=content,
        mode=mode,
    )


async def workspace_apply_patch_ops(
    runtime: ConversationWorkspaceRuntime,
    *,
    ops: list[dict[str, Any]],
    dry_run: bool,
    local_apply: Any,
) -> list[str]:
    if runtime.backend == "local":
        return await asyncio.to_thread(
            local_apply,
            ops,
            root=runtime.workspace_root,
            dry_run=dry_run,
        )
    return await sandbox_apply_patch_ops(
        session_ref=await runtime.get_sandbox_session_ref(),
        ops=ops,
        dry_run=dry_run,
    )


async def workspace_artifact_payload(
    runtime: ConversationWorkspaceRuntime,
    *,
    file_path: str,
    local_loader: Any,
) -> dict[str, Any]:
    if runtime.backend == "local":
        return await asyncio.to_thread(local_loader, runtime.workspace_root, [file_path])
    return await sandbox_artifact_payload(
        session_ref=await runtime.get_sandbox_session_ref(),
        file_path=file_path,
    )
