"""ChatData 会话工作区编排层：会话文件列表/读取/预览 API 与 DAO 校验。

路径解析、Runtime 门面、后端协议等核心已下沉 ``agent/workspace/runtime.py``；
本模块保留会话级编排（依赖 DAO 与 asyncio.to_thread），并 re-export 核心符号兼容旧导入。
"""

from __future__ import annotations

import asyncio
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.database import get_session
from utils.logger import get_logger
from utils.tool_result_store import ToolResultStore

from ...daos.conversation_dao import ConversationDao
from ...daos.agent_inbox_dao import AgentInboxDao
from ... import AGENT_ID
from apps.chat_data_app.agent.workspace.runtime import (  # noqa: F401
    OFFICE_PREVIEW_MAX_CHARS,
    WORKSPACE_DIR_NAMES,
    _absolute_command_path_tokens,
    _ensure_workspace_root_in_chat_data_home,
    _officecli_selector_token_indices,
    create_workspace_backend,
    is_managed_semantic_model_workspace_path,
    is_system_result_workspace_path,
    resolve_output_skill_directory,
    resolve_uploads_root,
    resolve_workspace_path,
    to_visible_path,
    workspace_runtime_from_state,
    ensure_conversation_workspace,
    ConversationWorkspaceRuntime,
    WorkspacePathMapping,
    _render_local_office_html,
)
from apps.chat_data_app.agent.workspace.workspace_local_backend import (
    delete_local_workspace_file,
)
from apps.chat_data_app.agent.workspace.workspace_path_policy import (
    USER_DATA_VIRTUAL_ROOT,
    WORKSPACE_PATH_POLICY,
)
from apps.chat_data_app.services.runtime_config import (
    OFFICE_PREVIEW_SOURCE_MAX_MB,
    TEXT_PREVIEW_MAX_MB,
)

logger = get_logger(__name__)

conversation_dao = ConversationDao(session_context=lambda: get_session())
agent_inbox_dao = AgentInboxDao()

# 文本预览源文件上限（默认 5MB）与 Office 预览源文件上限（默认 50MB），MB 值在
# runtime_config 集中读取（CHAT_DATA_TEXT_PREVIEW_MAX_MB / CHAT_DATA_OFFICE_PREVIEW_SOURCE_MAX_MB），
# 此处只做 MB→字节派生。注意与 OFFICE_PREVIEW_MAX_CHARS（Office 转出 HTML 的字符上限，12MB）是两回事。
# 模块级求值：改 env 后必须重启进程才生效。
WORKSPACE_TEXT_PREVIEW_MAX_BYTES = TEXT_PREVIEW_MAX_MB * 1024 * 1024
WORKSPACE_OFFICE_SOURCE_MAX_BYTES = OFFICE_PREVIEW_SOURCE_MAX_MB * 1024 * 1024

# raw（原始字节）预览上限：目前无对应 env，主对话与技能管理预览共用本常量（单一来源）。
WORKSPACE_RAW_PREVIEW_MAX_BYTES = 20 * 1024 * 1024


def _format_size_limit(max_bytes: int) -> str:
    """把字节上限格式化成用户可读文案（>=1MB 显示 MB，否则显示字节）。"""
    if max_bytes >= 1024 * 1024:
        mb = max_bytes / (1024 * 1024)
        return f"{mb:g}MB"
    return f"{max_bytes} 字节"

# 这些状态下的 Run 正在写工作区，此时删除会让沙箱回写被判成「应用侧已变更」而使该 Run 落盘失败。
# 「waiting_user」是挂起等待用户输入，Run 自身不在写盘，且前端 runBusy 已为 false，故放行。
_BLOCKING_WORKSPACE_DELETE_RUN_STATUSES = frozenset({"pending", "active", "cancelling"})


class WorkspaceFileDeleteConflictError(RuntimeError):
    """删除请求与正在执行的 Run 冲突。"""


def resolve_workspace_date(create_time: datetime | None) -> str:
    """解析会话创建日期（``YYYY-MM-DD``）作为工作区日期分层；``None`` 回退当前日期。

    会话工作区按创建时间归档（``threads/<uid>/<yyyy-MM-DD>/<cid>/``），不随后续活跃时间变化。
    """
    if create_time:
        return create_time.strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


async def get_conversation_created_date(conversation_id: str, *, user_id: str) -> str | None:
    """返回当前用户会话的创建日期；会话不存在或无权访问时返回 ``None``。

    这是各入口获取工作区日期分层的权威入口：先经 ``ensure_conversation_workspace``
    落盘的路径必须与这里解析出的日期一致，避免同一会话分裂出多个日期目录。
    """
    thread = await conversation_dao.get_thread(conversation_id, user_id)
    if thread is None:
        return None
    return resolve_workspace_date(thread.create_time)


def delete_workspace_file(
    conversation_id: str,
    path: str,
    user_id: str = "system",
    *,
    created_date: str | None = None,
) -> dict[str, Any]:
    """删除会话工作区中的单个文件（同步实现，由 ``asyncio.to_thread`` 调用）。

    只删文件、不删目录：目录递归删除需要单独的确认交互，不在这里代劳。
    路径安全全部下沉到 ``delete_local_workspace_file``（符号链接拒绝、realpath 越界校验、
    系统结果目录拒绝），因此 ``outputs/query_result`` 在这里已被挡住。
    """
    logical_path = WORKSPACE_PATH_POLICY.parse(path)
    if logical_path.is_system_result:
        raise ValueError("查询结果需要留存审计，不支持删除")
    workspace = ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)
    return delete_local_workspace_file(workspace["root"], path, allow_uploads=True)


async def _has_running_agent_run(conversation_id: str, user_id: str) -> bool:
    async with get_session() as session:
        run = await agent_inbox_dao.get_active_run_snapshot(session, conversation_id, user_id)
    return bool(run) and str(getattr(run, "status", "") or "") in _BLOCKING_WORKSPACE_DELETE_RUN_STATUSES


async def _delete_uploaded_file_record(
    conversation_id: str,
    user_id: str,
    relative_path: str,
) -> bool:
    """按 uploads 里的存储文件名清理上传记录（原文件 + 解析产物 + 软删记录）。

    uploads 是上传文件的权威存储，``uploaded_files.storage_dir`` 就是该目录下的文件名；
    不清理记录，历史消息里的附件卡片会指向一个已不存在的文件。
    返回是否命中记录——命中时物理删除已由 ``UploadedFileService.delete_file`` 完成。

    延迟导入 ``uploaded_file_service``：它反向依赖本模块的 ``resolve_uploads_root``，
    模块级导入会成环。
    """
    if "/" in relative_path:
        # 记录里的 storage_dir 是文件名，子目录中的文件不由上传记录管理
        return False
    from .uploaded_file_service import get_uploaded_file_service

    service = get_uploaded_file_service()
    for row in await service.get_files_by_thread(conversation_id, user_id=user_id):
        if str(row.get("storage_dir") or "") != relative_path:
            continue
        return await service.delete_file(
            str(row.get("id") or ""),
            user_id=user_id,
            thread_id=conversation_id,
        )
    return False


async def delete_conversation_workspace_file(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
) -> dict[str, Any]:
    """删除当前用户会话工作区中的单个文件。

    三条约束：
    1. ``outputs/query_result``（SQL 查询结果）与 ``outputs/.tool-offload`` 是审计留存，永不删除；
    2. Run 正在执行时拒绝，避免把该 Run 的沙箱回写判成「应用侧已变更」而让它落盘失败；
    3. uploads 删除连带清理 ``uploaded_files`` 记录，避免历史附件卡片指向不存在的文件。
    """
    if await _has_running_agent_run(conversation_id, user_id):
        raise WorkspaceFileDeleteConflictError("智能体正在执行当前会话，请等本轮结束后再删除文件")

    logical_path = WORKSPACE_PATH_POLICY.parse(path)
    if logical_path.is_system_result:
        raise ValueError("查询结果需要留存审计，不支持删除")

    # uploads 命中上传记录时整条交给 UploadedFileService：它同时负责物理文件、解析产物与记录，
    # 不会留下「文件删了但账还在」的窗口；未命中记录（例如解析产物）才走纯物理删除。
    if logical_path.kind == "uploads" and await _delete_uploaded_file_record(
        conversation_id, user_id, logical_path.relative_path
    ):
        return {
            "path": f"{USER_DATA_VIRTUAL_ROOT}/{logical_path.normalized}",
            "type": "file",
            "record_cleaned": True,
        }

    result = await asyncio.to_thread(
        delete_workspace_file,
        conversation_id,
        path,
        user_id,
        created_date=await get_conversation_created_date(conversation_id, user_id=user_id),
    )
    return {**result, "record_cleaned": False}


def list_workspace_files(
    conversation_id: str,
    user_id: str = "system",
    *,
    created_date: str | None = None,
) -> list[dict[str, Any]]:
    """列出会话工作区中的可见文件，不跟随逃逸符号链接。"""
    paths = ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)
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
    max_bytes: int = WORKSPACE_TEXT_PREVIEW_MAX_BYTES,
    user_id: str = "system",
    *,
    created_date: str | None = None,
) -> dict[str, Any]:
    """读取会话工作区中的文本文件。"""
    paths = ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)
    root = paths["root"]
    target = resolve_workspace_path(root, path)
    if is_system_result_workspace_path(root, target):
        raise ValueError("工具结果缓存不在工作区文件预览范围内")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError(f"文件过大，暂不支持预览超过 {_format_size_limit(max_bytes)} 的文件")
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
    return await asyncio.to_thread(
        list_workspace_files,
        conversation_id,
        user_id,
        created_date=await get_conversation_created_date(conversation_id, user_id=user_id),
    )


async def read_conversation_workspace_file(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
    max_bytes: int = WORKSPACE_TEXT_PREVIEW_MAX_BYTES,
) -> dict[str, Any]:
    """读取当前用户会话持久化工作区中的文本文件。"""
    return await asyncio.to_thread(
        read_workspace_text_file,
        conversation_id,
        path,
        max_bytes,
        user_id,
        created_date=await get_conversation_created_date(conversation_id, user_id=user_id),
    )


async def read_conversation_workspace_raw_file(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
    max_bytes: int = WORKSPACE_RAW_PREVIEW_MAX_BYTES,
) -> dict[str, Any]:
    """读取会话工作区中的原始字节文件（用于 PDF/Word 等二进制预览/下载）。"""
    return await asyncio.to_thread(
        read_workspace_raw_file,
        conversation_id,
        path,
        max_bytes,
        user_id,
        created_date=await get_conversation_created_date(conversation_id, user_id=user_id),
    )


def _validate_office_preview_source(workspace_root: str, path: str) -> None:
    target = resolve_workspace_path(workspace_root, path)
    if is_system_result_workspace_path(workspace_root, target):
        raise ValueError("工具结果缓存不支持 Office 预览")
    if target.suffix.lower() not in {".docx", ".xlsx", ".pptx"}:
        raise ValueError("OfficeCLI HTML 预览仅支持 .docx、.xlsx、.pptx 文件")
    if not target.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    if target.stat().st_size > WORKSPACE_OFFICE_SOURCE_MAX_BYTES:
        raise ValueError("文件过大，暂不支持预览超过 50MB 的 Office 文件")




async def render_conversation_workspace_office_preview(
    conversation_id: str,
    *,
    user_id: str,
    path: str,
) -> dict[str, Any]:
    """Render an owned Office file from the application-side authoritative copy."""
    created_date = await get_conversation_created_date(conversation_id, user_id=user_id)
    workspace = ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)
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
    max_bytes: int = WORKSPACE_RAW_PREVIEW_MAX_BYTES,
    user_id: str = "system",
    *,
    created_date: str | None = None,
) -> dict[str, Any]:
    """读取会话工作区中的原始字节文件，返回 bytes 与 mime 类型。"""
    paths = ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)
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
    created_date = await get_conversation_created_date(conversation_id, user_id=user_id)
    return await asyncio.to_thread(
        ToolResultStore().load_query_result_for_conversation,
        result_id=result_id,
        conversation_id=conversation_id,
        user_id=user_id,
        created_date=created_date,
    )



def __getattr__(name: str):
    """向后兼容：Local/Sandbox 后端实现类转发到 agent.workspace.runtime。"""
    if name in {"LocalWorkspaceBackend", "SandboxWorkspaceBackend"}:
        import importlib

        return getattr(importlib.import_module(
            "apps.chat_data_app.agent.workspace.runtime"
        ), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
