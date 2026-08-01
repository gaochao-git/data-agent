"""ChatData conversation workspace routes."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..schema import SkillImportFromWorkspaceRequest
from ..services.conversation_workspace import (
    can_access_conversation_workspace,
    ensure_conversation_workspace,
    list_conversation_workspace_files as list_workspace_files_for_conversation,
    read_conversation_query_result,
    read_conversation_workspace_file as read_workspace_file_for_conversation,
    resolve_output_skill_directory,
)
from ..services.skill_service import skill_service
from ..services.uploaded_file_service import get_file_info
from ..services.conversation_workspace import resolve_uploads_root
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter()


INTERNAL_WORKSPACE_FILE_PATHS = {"workspace/data_context.json"}


def _normalize_workspace_file_path(path: str) -> str:
    normalized = str(path or "").strip()
    return normalized.replace("/mnt/user-data/", "").lstrip("/")


def _is_internal_workspace_file(path: str) -> bool:
    normalized = _normalize_workspace_file_path(path)
    return normalized in INTERNAL_WORKSPACE_FILE_PATHS


def _filter_visible_workspace_files(files: list[dict]) -> list[dict]:
    return [
        item for item in files
        if not _is_internal_workspace_file(str(item.get("relative_path") or item.get("path") or ""))
    ]


@router.get("/conversations/{conversation_id}/workspace-files")
async def list_conversation_workspace_files(conversation_id: str, request: Request):
    """获取 ChatData 会话工作区文件列表。"""
    try:
        current_user = _get_current_user(request)
        if not await can_access_conversation_workspace(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        files = await list_workspace_files_for_conversation(
            conversation_id,
            user_id=str(current_user.user_id),
        )
        return business_response(data={"files": _filter_visible_workspace_files(files)}, msg="获取成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("获取会话工作区文件失败: conversation_id=%s error=%s", conversation_id, e, exc_info=True)
        return business_response(msg="获取工作区文件失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_LIST_FAILED, status="error")


@router.get("/conversations/{conversation_id}/workspace-files/content")
async def read_conversation_workspace_file(conversation_id: str, path: str, request: Request):
    """读取 ChatData 会话工作区文本文件。"""
    try:
        current_user = _get_current_user(request)
        if not await can_access_conversation_workspace(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if _is_internal_workspace_file(path):
            return business_response(msg="文件不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        file_payload = await read_workspace_file_for_conversation(
            conversation_id,
            user_id=str(current_user.user_id),
            path=path,
        )
        return business_response(data=file_payload, msg="获取成功")
    except HTTPException:
        raise
    except FileNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("读取会话工作区文件失败: conversation_id=%s path=%s error=%s", conversation_id, path, e, exc_info=True)
        return business_response(msg="读取工作区文件失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED, status="error")


@router.get("/conversations/{conversation_id}/query-results/{result_id}")
async def get_conversation_query_result(conversation_id: str, result_id: str, request: Request):
    """读取当前用户会话内卸载的大查询结果。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        if not await can_access_conversation_workspace(conversation_id, user_id):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        result = await read_conversation_query_result(
            conversation_id,
            user_id=user_id,
            result_id=result_id,
        )
        if result is None:
            return business_response(msg="查询结果不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data=result, msg="获取成功")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error(
            "读取会话查询结果失败: conversation_id=%s result_id=%s error=%s",
            conversation_id,
            result_id,
            e,
            exc_info=True,
        )
        return business_response(msg="读取查询结果失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED, status="error")


@router.post("/conversations/{conversation_id}/workspace-skills/import")
async def import_conversation_workspace_skill(conversation_id: str, payload: SkillImportFromWorkspaceRequest, request: Request):
    """把会话工作区生成的技能目录导入为用户技能草稿。"""
    try:
        current_user = _get_current_user(request)
        if not await can_access_conversation_workspace(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        workspace_paths = ensure_conversation_workspace(conversation_id, str(current_user.user_id))
        skill_dir = resolve_output_skill_directory(workspace_paths["root"], payload.skill_dir_path)
        skill = await skill_service.import_user_skill_from_directory_async(
            skill_dir,
            str(current_user.user_id),
            operator_name=str(current_user.username or ""),
        )
        return business_response(data=skill, msg="导入成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_WORKSPACE_SKILL_IMPORT_INVALID, status="error")
    except Exception as e:
        logger.error("导入会话工作区技能失败: conversation_id=%s path=%s error=%s", conversation_id, payload.skill_dir_path, e, exc_info=True)
        return business_response(msg="导入技能失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_SKILL_IMPORT_FAILED, status="error")


class LinkFileRequest(BaseModel):
    """将已上传文件链接到会话工作区的请求。"""
    file_id: str


@router.post("/conversations/{conversation_id}/workspace-files/link")
async def link_uploaded_file_to_workspace(conversation_id: str, payload: LinkFileRequest, request: Request):
    """将已上传且解析完成的文件链接到会话工作区 uploads 目录。

    前端上传文件后轮询解析状态，解析完成后调用此接口，
    将原始文件和解析后的 Markdown 复制到会话工作区的 uploads/ 目录，
    使 Agent 可以通过 file_read 读取。
    """
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        if not await can_access_conversation_workspace(conversation_id, user_id):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        file_info = await get_file_info(payload.file_id)
        if not file_info:
            return business_response(msg="文件不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if file_info["status"] != "completed":
            return business_response(msg="文件尚未解析完成", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
        if not file_info.get("parse_filename"):
            return business_response(msg="文件解析失败，无法链接", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        workspace_paths = ensure_conversation_workspace(conversation_id, user_id)
        uploads_dir = Path(workspace_paths["uploads"])

        file_type = file_info["file_type"]
        original_filename = file_info["original_filename"]
        uploads_root = resolve_uploads_root(user_id, conversation_id)

        original_src = uploads_root / file_info["storage_dir"]
        parsed_src = uploads_root / file_info["parse_filename"]

        # 使用原始文件名（不含扩展名）作为前缀，避免冲突
        stem = Path(original_filename).stem
        original_dest = uploads_dir / f"upload-{stem}.{file_type}"
        parsed_dest = uploads_dir / f"upload-{stem}.md"

        # 处理同名文件
        counter = 1
        while original_dest.exists() or parsed_dest.exists():
            original_dest = uploads_dir / f"upload-{stem}-{counter}.{file_type}"
            parsed_dest = uploads_dir / f"upload-{stem}-{counter}.md"
            counter += 1

        if original_src.exists():
            shutil.copy2(original_src, original_dest)
        if parsed_src.exists():
            shutil.copy2(parsed_src, parsed_dest)

        return business_response(data={
            "file_id": payload.file_id,
            "original_filename": original_filename,
            "file_type": file_type,
            "file_size": file_info["file_size"],
            "original_path": f"/mnt/user-data/uploads/{original_dest.name}",
            "parsed_path": f"/mnt/user-data/uploads/{parsed_dest.name}",
        }, msg="文件链接成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("链接上传文件到工作区失败: conversation_id=%s file_id=%s error=%s", conversation_id, payload.file_id, e, exc_info=True)
        return business_response(msg="文件链接失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_LINK_FAILED, status="error")
