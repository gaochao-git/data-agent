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
from utils.storage_paths import CHAT_DATA_HOME, normalize_user_id

from ..daos.conversation_dao import ConversationDao
from .semantic_common import AGENT_ID
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
    category: Literal["public", "custom"]
    source_dir: Path

    @property
    def visible_root(self) -> str:
        return f"/mnt/skills/{self.category}/{self.name}"


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

    @staticmethod
    def _skill_key(category: str, name: str) -> str:
        return f"{category}/{name}"

    def runtime_skill_mounts(self) -> list[RuntimeSkillMount]:
        return list(self.runtime_skills.values())

    async def register_runtime_skill(
        self,
        *,
        name: str,
        category: Literal["public", "custom"],
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

    def resolve_readonly_skill_path(self, path: str) -> tuple[Path, str]:
        """将当前运行已登记的 /mnt/skills 路径解析到真实文件系统。"""
        raw = str(path or "").strip().replace("\\", "/")
        prefix = "/mnt/skills/"
        if not raw.startswith(prefix):
            raise ValueError(f"只允许访问当前运行已加载的技能路径: {path}")
        parts = PurePosixPath(raw).parts
        if len(parts) < 5 or parts[1:3] != ("mnt", "skills"):
            raise ValueError(f"技能路径无效: {path}")
        category, name = parts[3], parts[4]
        if category not in {"public", "custom"}:
            raise ValueError(f"技能路径无效: {path}")
        mount = self.runtime_skills.get(self._skill_key(category, name))
        if mount is None:
            raise ValueError(f"技能尚未加载或无权访问: {path}")
        relative_parts = parts[5:]
        if ".." in relative_parts:
            raise ValueError(f"技能路径越界: {path}")
        target = (mount.source_dir.joinpath(*relative_parts)).resolve()
        if target != mount.source_dir and mount.source_dir not in target.parents:
            raise ValueError(f"技能路径越界: {path}")
        visible = mount.visible_root
        if relative_parts:
            visible += "/" + "/".join(relative_parts)
        return target, visible

    async def get_sandbox_session_ref(self) -> SandboxExecutionSessionRef:
        if self.backend != "sandbox":
            raise ValueError("当前工作区后端不是 OpenSandbox")
        if self.sandbox_session_ref.current is None:
            self.sandbox_session_ref.current = await get_workspace_execution_session(
                self.workspace_root,
                self.user_id,
                runtime_skill_mounts=tuple(self.runtime_skill_mounts()),
            )
        return self.sandbox_session_ref


def _safe_segment(value: str) -> str:
    """规整路径段，避免会话 ID 造成目录越界。"""
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())
    if not normalized or normalized in {".", ".."} or normalized.startswith("."):
        raise ValueError("非法会话 ID")
    return normalized


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
    runtime: ConversationWorkspaceRuntime,
    file_path: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    target, visible_path = runtime.resolve_readonly_skill_path(file_path)
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
        if str(file_path or "").strip().startswith("/mnt/skills/"):
            return await asyncio.to_thread(
                _read_local_runtime_skill_file,
                runtime,
                file_path,
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
