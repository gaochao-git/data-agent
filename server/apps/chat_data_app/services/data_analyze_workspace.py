"""DataAnalyze 会话工作目录与技能运行副本。"""

from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any

from utils.database import get_session
from utils.logger import get_logger
from utils.storage_paths import NL2SQL_BASE_DIR

from ..daos.conversation_dao import ConversationDao
from .semantic_common import DATA_ANALYZE_AGENT_ID

logger = get_logger(__name__)

THREADS_DIR = Path(NL2SQL_BASE_DIR) / "data_analyze_threads"
WORKSPACE_DIR_NAMES = ("workspace", "outputs", "uploads", "runtime-skills")
conversation_dao = ConversationDao(session_context=lambda: get_session())


def _safe_segment(value: str) -> str:
    """规整路径段，避免会话 ID 造成目录越界。"""
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "").strip())
    if not normalized or normalized in {".", ".."} or normalized.startswith("."):
        raise ValueError("非法会话 ID")
    return normalized


def ensure_data_analyze_workspace(conversation_id: str) -> dict[str, str]:
    """创建并返回 DataAnalyze 会话目录结构。"""
    thread_id = _safe_segment(conversation_id)
    root = (THREADS_DIR / thread_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    paths = {"root": str(root)}
    for name in WORKSPACE_DIR_NAMES:
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        paths[name.replace("-", "_")] = str(path)
    return paths


def write_workspace_json(workspace_root: str, relative_path: str, payload: Any) -> str:
    """向会话 workspace 写入 JSON 上下文文件。"""
    target = resolve_workspace_path(workspace_root, relative_path, writable=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return to_visible_path(workspace_root, target)


def resolve_workspace_path(workspace_root: str, path: str, *, writable: bool = False) -> Path:
    """解析模型可见路径，限制在当前会话目录内。"""
    root = Path(workspace_root).resolve()
    raw = str(path or "").strip()
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return root

    alias_map = {
        "/mnt/user-data/workspace": "workspace",
        "/mnt/user-data/outputs": "outputs",
        "/mnt/user-data/uploads": "uploads",
        "/mnt/user-data/runtime-skills": "runtime-skills",
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
    if target != root and root not in target.parents:
        raise ValueError(f"路径越界，禁止访问当前会话目录之外的路径: {path}")

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


def list_workspace_files(conversation_id: str) -> list[dict[str, Any]]:
    """列出 DataAnalyze 会话目录中的可见文件。"""
    paths = ensure_data_analyze_workspace(conversation_id)
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
                stat = item.stat()
                relative_path = item.relative_to(root).as_posix()
            except OSError:
                continue
            files.append({
                "path": f"/{relative_path}",
                "relative_path": relative_path,
                "directory": directory_name,
                "name": item.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "source": directory_name,
            })
    return files


def read_workspace_text_file(conversation_id: str, path: str, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
    """读取 DataAnalyze 会话目录中的文本文件。"""
    paths = ensure_data_analyze_workspace(conversation_id)
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


async def can_access_data_analyze_conversation(conversation_id: str, user_id: str) -> bool:
    """校验当前用户是否可访问智能分析会话。"""
    if not str(conversation_id or "").strip() or not str(user_id or "").strip():
        return False
    return await conversation_dao.exists_user_agent_thread(
        conversation_id=conversation_id,
        user_id=user_id,
        agent_id=DATA_ANALYZE_AGENT_ID,
    )
