"""ChatData 会话工作区编排层：会话文件列表/读取/预览 API 与 DAO 校验。

路径解析、Runtime 门面、后端协议等核心已下沉 ``agent/workspace/runtime.py``；
本模块保留会话级编排（依赖 DAO 与 asyncio.to_thread），并 re-export 核心符号兼容旧导入。
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Any

from utils.database import get_session
from utils.logger import get_logger
from utils.tool_result_store import ToolResultStore

from ...daos.conversation_dao import ConversationDao
from ... import AGENT_ID
from apps.chat_data_app.agent.workspace.runtime import (  # noqa: F401
    OFFICE_PREVIEW_MAX_CHARS,
    WORKSPACE_DIR_NAMES,
    _absolute_command_path_tokens,
    _ensure_workspace_root_in_chat_data_home,
    _officecli_selector_token_indices,
    _safe_segment,
    create_workspace_backend,
    is_system_result_workspace_path,
    resolve_output_skill_directory,
    resolve_uploads_root,
    WORKSPACE_DIR_NAMES,
    ConversationWorkspaceRuntime,
    WorkspacePathMapping,
    _render_local_office_html,
    ensure_conversation_workspace,
    is_managed_semantic_model_workspace_path,
    is_system_result_workspace_path,
    resolve_uploads_root,
    resolve_workspace_path,
    to_visible_path,
    workspace_runtime_from_state,
)

logger = get_logger(__name__)

conversation_dao = ConversationDao(session_context=lambda: get_session())


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
                resolved_item = resolve_workspace_path(str(root), f"/mnt/user-data/{relative_path}")
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



def __getattr__(name: str):
    """向后兼容：Local/Sandbox 后端实现类转发到 agent.workspace.runtime。"""
    if name in {"LocalWorkspaceBackend", "SandboxWorkspaceBackend"}:
        import importlib

        return getattr(importlib.import_module(
            "apps.chat_data_app.agent.workspace.runtime"
        ), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
