"""DataAnalyze workspace routes for ChatData."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..schema import SkillImportFromWorkspaceRequest
from ..services.data_analyze_workspace import can_access_data_analyze_conversation, ensure_data_analyze_workspace, resolve_workspace_path
from ..services.feature_gate import get_data_analyze_unavailable_response
from ..services.skill_service import skill_service
from ..services.workspace_execution import list_workspace_files_from_sandbox, read_workspace_file_from_sandbox
from .dependencies import _get_current_user

logger = get_logger(__name__)
router = APIRouter()

@router.get("/data-analyze/conversations/{conversation_id}/workspace-files")
async def list_data_analyze_workspace_files(conversation_id: str, request: Request):
    """获取智能分析会话工作区文件列表。"""
    try:
        unavailable_response = get_data_analyze_unavailable_response()
        if unavailable_response:
            return unavailable_response
        current_user = _get_current_user(request)
        if not await can_access_data_analyze_conversation(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        workspace_paths = ensure_data_analyze_workspace(conversation_id)
        return business_response(data={"files": await list_workspace_files_from_sandbox(workspace_root=workspace_paths["root"], user_id=str(current_user.user_id))}, msg="获取成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("获取智能分析工作区文件失败: conversation_id=%s error=%s", conversation_id, e, exc_info=True)
        return business_response(msg="获取工作区文件失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_LIST_FAILED, status="error")


@router.get("/data-analyze/conversations/{conversation_id}/workspace-files/content")
async def read_data_analyze_workspace_file(conversation_id: str, path: str, request: Request):
    """读取智能分析会话工作区文本文件。"""
    try:
        unavailable_response = get_data_analyze_unavailable_response()
        if unavailable_response:
            return unavailable_response
        current_user = _get_current_user(request)
        if not await can_access_data_analyze_conversation(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        workspace_paths = ensure_data_analyze_workspace(conversation_id)
        return business_response(data=await read_workspace_file_from_sandbox(workspace_root=workspace_paths["root"], file_path=path, user_id=str(current_user.user_id)), msg="获取成功")
    except HTTPException:
        raise
    except FileNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    except Exception as e:
        logger.error("读取智能分析工作区文件失败: conversation_id=%s path=%s error=%s", conversation_id, path, e, exc_info=True)
        return business_response(msg="读取工作区文件失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED, status="error")


@router.post("/data-analyze/conversations/{conversation_id}/workspace-skills/import")
async def import_data_analyze_workspace_skill(conversation_id: str, payload: SkillImportFromWorkspaceRequest, request: Request):
    """把智能分析工作区生成的技能目录导入为用户技能草稿。"""
    try:
        unavailable_response = get_data_analyze_unavailable_response()
        if unavailable_response:
            return unavailable_response
        current_user = _get_current_user(request)
        if not await can_access_data_analyze_conversation(conversation_id, str(current_user.user_id)):
            return business_response(msg="对话不存在或无权访问", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        workspace_paths = ensure_data_analyze_workspace(conversation_id)
        skill_dir = resolve_workspace_path(workspace_paths["root"], payload.skill_dir_path)
        allowed_roots = (
            resolve_workspace_path(workspace_paths["root"], "workspace"),
            resolve_workspace_path(workspace_paths["root"], "outputs"),
        )
        if not any(skill_dir == root or root in skill_dir.parents for root in allowed_roots):
            return business_response(msg="只允许导入 workspace 或 outputs 下的技能目录", code=ErrorCode.CHATDATA_WORKSPACE_SKILL_IMPORT_INVALID, status="error")
        skill = await skill_service.import_user_skill_from_directory_async(skill_dir, str(current_user.user_id), operator_name=str(current_user.username or ""))
        return business_response(data=skill, msg="导入成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_WORKSPACE_SKILL_IMPORT_INVALID, status="error")
    except Exception as e:
        logger.error("导入智能分析工作区技能失败: conversation_id=%s path=%s error=%s", conversation_id, payload.skill_dir_path, e, exc_info=True)
        return business_response(msg="导入技能失败，请稍后重试", code=ErrorCode.CHATDATA_WORKSPACE_SKILL_IMPORT_FAILED, status="error")
