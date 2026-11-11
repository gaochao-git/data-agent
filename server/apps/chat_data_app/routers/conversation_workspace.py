"""ChatData conversation workspace routes."""

from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi import status as http_status
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..agent.skills.skill_service import skill_service
from ..schema import SkillImportFromWorkspaceRequest
from ..services.agent_run.conversation_workspace import (
    WorkspaceArchiveTooLargeError,
    WorkspaceFileDeleteConflictError,
    archive_conversation_workspace_directory,
    can_access_conversation_workspace,
    ensure_conversation_workspace,
    get_conversation_created_date,
    read_conversation_query_result,
    read_conversation_workspace_raw_file,
    render_conversation_workspace_office_preview,
    resolve_output_skill_directory,
)
from ..services.agent_run.conversation_workspace import (
    delete_conversation_workspace_file as delete_workspace_file_for_conversation,
)
from ..services.agent_run.conversation_workspace import (
    list_conversation_workspace_directories as list_workspace_directories_for_conversation,
)
from ..services.agent_run.conversation_workspace import (
    list_conversation_workspace_files as list_workspace_files_for_conversation,
)
from ..services.agent_run.conversation_workspace import (
    read_conversation_workspace_file as read_workspace_file_for_conversation,
)
from ..services.setting_service import get_workspace_size_limit
from . import BINARY_RESPONSES, JSON_API_RESPONSES
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter(tags=["ChatData 会话工作区文件"], responses=JSON_API_RESPONSES)


INTERNAL_WORKSPACE_FILE_PATHS = {"workspace/data_context.json"}


def _normalize_workspace_file_path(path: str) -> str:
    normalized = str(path or "").strip()
    return normalized.replace("/mnt/user-data/", "").lstrip("/")


def _is_internal_workspace_file(path: str) -> bool:
    normalized = _normalize_workspace_file_path(path)
    return normalized in INTERNAL_WORKSPACE_FILE_PATHS


def _filter_visible_workspace_directories(directories: list[dict]) -> list[dict]:
    return [
        item
        for item in directories
        if not _is_internal_workspace_file(str(item.get("relative_path") or item.get("path") or ""))
    ]


def _filter_visible_workspace_files(files: list[dict]) -> list[dict]:
    return [
        item
        for item in files
        if not _is_internal_workspace_file(str(item.get("relative_path") or item.get("path") or ""))
    ]


@router.get("/conversations/{conversation_id}/workspace-files")
async def list_conversation_workspace_files(conversation_id: str, request: Request) -> JSONResponse:
    """获取 ChatData 会话工作区文件列表。"""
    try:
        current_user = _get_current_user(request)
        if not await can_access_conversation_workspace(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        files = await list_workspace_files_for_conversation(
            conversation_id,
            user_id=str(current_user.user_id),
        )
        directories = await list_workspace_directories_for_conversation(
            conversation_id,
            user_id=str(current_user.user_id),
        )
        limit_mb, limit_bytes = await get_workspace_size_limit()
        used_bytes = sum(int(item.get("size") or 0) for item in files)
        return business_response(
            data={
                "files": _filter_visible_workspace_files(files),
                "directories": _filter_visible_workspace_directories(directories),
                "usage": {
                    "used_bytes": used_bytes,
                    "limit_bytes": limit_bytes,
                    "limit_mb": limit_mb,
                    "percentage": round(used_bytes / limit_bytes * 100, 1) if limit_bytes else 0,
                },
            },
            msg="获取成功",
        )
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("获取会话工作区文件失败: conversation_id=%s error=%s", conversation_id, e, exc_info=True)
        return business_response(
            msg="获取工作区文件失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_LIST_FAILED, status="error"
        )


@router.delete("/conversations/{conversation_id}/workspace-files")
async def delete_conversation_workspace_file(conversation_id: str, path: str, request: Request) -> JSONResponse:
    """删除会话工作区中的单个文件（SQL 查询结果需留存审计，不可删除）。"""
    try:
        current_user = _get_current_user(request)
        if not await can_access_conversation_workspace(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if _is_internal_workspace_file(path):
            return business_response(msg="文件不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        payload = await delete_workspace_file_for_conversation(
            conversation_id,
            user_id=str(current_user.user_id),
            path=path,
        )
        return business_response(data=payload, msg="删除成功")
    except HTTPException:
        raise
    except WorkspaceFileDeleteConflictError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_AGENT_RUN_ACTIVE,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except FileNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error(
            "删除会话工作区文件失败: conversation_id=%s path=%s error=%s", conversation_id, path, e, exc_info=True
        )
        return business_response(
            msg="删除工作区文件失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_DELETE_FAILED, status="error"
        )


@router.get("/conversations/{conversation_id}/workspace-files/content")
async def read_conversation_workspace_file(conversation_id: str, path: str, request: Request) -> JSONResponse:
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
        logger.error(
            "读取会话工作区文件失败: conversation_id=%s path=%s error=%s", conversation_id, path, e, exc_info=True
        )
        return business_response(
            msg="读取工作区文件失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED, status="error"
        )


@router.get(
    "/conversations/{conversation_id}/workspace-files/raw",
    operation_id="read_conversation_workspace_raw_file",
    responses=BINARY_RESPONSES,
)
async def read_conversation_workspace_raw_file_api(conversation_id: str, path: str, request: Request) -> Response:
    """读取会话工作区原始字节文件（PDF/Word 等二进制预览/下载）。"""
    try:
        current_user = _get_current_user(request)
        if not await can_access_conversation_workspace(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if _is_internal_workspace_file(path):
            return business_response(
                msg="文件不存在",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error",
            )
        payload = await read_conversation_workspace_raw_file(
            conversation_id, user_id=str(current_user.user_id), path=path
        )
        filename = str(payload["relative_path"]).rsplit("/", 1)[-1]
        ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "file"
        disposition = f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
        return Response(
            content=payload["data"],
            media_type=payload["mime"],
            headers={"Content-Disposition": disposition},
        )
    except HTTPException:
        raise
    except FileNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error(
            "读取工作区原始文件失败: conversation_id=%s path=%s error=%s", conversation_id, path, e, exc_info=True
        )
        return business_response(
            msg="读取文件失败，请稍后重试",
            code=ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED,
            status="error",
        )


@router.get(
    "/conversations/{conversation_id}/workspace-files/archive",
    operation_id="download_conversation_workspace_archive",
    responses=BINARY_RESPONSES,
)
async def download_conversation_workspace_archive(conversation_id: str, directory: str, request: Request) -> Response:
    """把会话工作区的目录（顶层目录或其下子目录，UI 当前只放开 outputs）打包为 zip 下载。

    目录合法性由 service 层 ``normalize_archive_directory`` 单点校验（首段必须在工作区
    目录白名单内、拒绝 ``..`` 逃逸），路由不再重复一份白名单，非法目录走 ValueError
    分支返回业务错误。内容与权限口径复用文件列表的白名单（见 service 层注释）；大小
    限制针对 zip 产物（CHAT_DATA_WORKSPACE_ARCHIVE_MAX_MB，默认 50MB），超限返回业务错误。
    临时 zip 通过 BackgroundTask 在响应发送后删除。
    """
    normalized_directory = str(directory or "").strip()
    try:
        current_user = _get_current_user(request)
        if not await can_access_conversation_workspace(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        payload = await archive_conversation_workspace_directory(
            conversation_id, user_id=str(current_user.user_id), directory=normalized_directory
        )
        filename = str(payload["filename"])
        ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "archive.zip"
        disposition = f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"
        return FileResponse(
            path=payload["temp_path"],
            media_type="application/zip",
            headers={"Content-Disposition": disposition},
            background=BackgroundTask(os.unlink, payload["temp_path"]),
        )
    except HTTPException:
        raise
    except WorkspaceArchiveTooLargeError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except FileNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error(
            "打包下载工作区目录失败: conversation_id=%s directory=%s error=%s",
            conversation_id,
            directory,
            e,
            exc_info=True,
        )
        return business_response(
            msg="打包下载失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED, status="error"
        )


@router.get(
    "/conversations/{conversation_id}/workspace-files/office-html",
    operation_id="render_conversation_workspace_office_preview",
)
async def render_conversation_workspace_office_preview_api(
    conversation_id: str, path: str, request: Request
) -> JSONResponse:
    """渲染当前会话 Office 文件的受控 OfficeCLI HTML 预览。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        if not await can_access_conversation_workspace(conversation_id, user_id):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        if _is_internal_workspace_file(path):
            return business_response(msg="文件不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        preview = await render_conversation_workspace_office_preview(
            conversation_id,
            user_id=user_id,
            path=path,
        )
        return business_response(data=preview, msg="获取成功")
    except (FileNotFoundError, ValueError) as exc:
        logger.info(
            "OfficeCLI Word 预览不可用，回退前端渲染: conversation_id=%s path=%s error=%s",
            conversation_id,
            path,
            exc,
        )
        return business_response(data={"available": False, "reason": "preview_unavailable"}, msg="Office 预览不可用")
    except Exception as exc:
        logger.warning(
            "OfficeCLI Word 预览失败，回退前端渲染: conversation_id=%s path=%s error=%s",
            conversation_id,
            path,
            exc,
            exc_info=True,
        )
        return business_response(data={"available": False, "reason": "preview_failed"}, msg="Office 预览暂不可用")


@router.get("/conversations/{conversation_id}/query-results/{result_id}")
async def get_conversation_query_result(conversation_id: str, result_id: str, request: Request) -> JSONResponse:
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
        return business_response(
            msg="读取查询结果失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED, status="error"
        )


@router.post("/conversations/{conversation_id}/workspace-skills/import")
async def import_conversation_workspace_skill(
    conversation_id: str, payload: SkillImportFromWorkspaceRequest, request: Request
) -> JSONResponse:
    """把会话工作区生成的技能目录导入为用户技能草稿。"""
    try:
        current_user = _get_current_user(request)
        user_id = str(current_user.user_id)
        if not await can_access_conversation_workspace(conversation_id, user_id):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        created_date = await get_conversation_created_date(conversation_id, user_id=user_id)
        workspace_paths = ensure_conversation_workspace(conversation_id, user_id, created_date=created_date)
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
        logger.error(
            "导入会话工作区技能失败: conversation_id=%s path=%s error=%s",
            conversation_id,
            payload.skill_dir_path,
            e,
            exc_info=True,
        )
        return business_response(
            msg="导入技能失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_SKILL_IMPORT_FAILED, status="error"
        )
