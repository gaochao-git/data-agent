"""Skill management routes for ChatData."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..schema import SkillCreateRequest, SkillFileUpsertRequest, SkillUpdateRequest
from ..services.skill_service import skill_service
from .dependencies import _current_user_names, _get_current_user, _optional_current_user

logger = get_logger(__name__)
router = APIRouter()

@router.get("/skills")
async def list_skills(request: Request, search: Optional[str] = None, category: Optional[str] = None, scope: Optional[str] = None, agent: Optional[str] = None):
    """获取可用专家技能列表。"""
    try:
        user = _optional_current_user(request)
        user_id = str(getattr(user, "user_id", "") or "").strip()
        normalized_scope = str(scope or "all").strip().lower()
        if normalized_scope not in {"all", "favorites", "mine", "system"}:
            normalized_scope = "all"
        normalized_agent = str(agent or "").strip().lower()
        if normalized_agent not in {"query", "analyze"}:
            normalized_agent = ""
        skills = await skill_service.list_skills_async(search=search, category=category, user_id=user_id, scope=normalized_scope, user_names=_current_user_names(user), agent=normalized_agent)
        return business_response(data={"skills": skills, "total": len(skills)}, msg="获取成功")
    except Exception as e:
        logger.error("获取技能列表失败: %s", e, exc_info=True)
        return business_response(msg="获取技能列表失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_LIST_FAILED, status="error")


@router.get("/skills/mine")
async def list_my_skills(request: Request, search: Optional[str] = None, category: Optional[str] = None):
    """获取我的技能。"""
    try:
        user = _get_current_user(request)
        skills = await skill_service.list_skills_async(search=search, category=category, user_id=str(user.user_id), scope="mine", user_names=_current_user_names(user))
        return business_response(data={"skills": skills, "total": len(skills)}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取我的技能失败: %s", e, exc_info=True)
        return business_response(msg="获取我的技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_MINE_LIST_FAILED, status="error")


@router.get("/skills/favorites")
async def list_favorite_skills(request: Request, search: Optional[str] = None, category: Optional[str] = None):
    """获取收藏技能。"""
    try:
        user = _get_current_user(request)
        skills = await skill_service.list_skills_async(search=search, category=category, user_id=str(user.user_id), scope="favorites", user_names=_current_user_names(user))
        return business_response(data={"skills": skills, "total": len(skills)}, msg="获取成功")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取收藏技能失败: %s", e, exc_info=True)
        return business_response(msg="获取收藏技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_FAVORITE_LIST_FAILED, status="error")


@router.post("/skills")
async def create_user_skill(payload: SkillCreateRequest, request: Request):
    """创建用户技能草稿。"""
    try:
        user = _get_current_user(request)
        skill = await skill_service.create_user_skill_async(payload.model_dump(), str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data=skill, msg="创建成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_CREATE_INVALID, status="error")
    except Exception as e:
        logger.error("创建用户技能失败: %s", e, exc_info=True)
        return business_response(msg="创建用户技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_CREATE_FAILED, status="error")


@router.put("/skills/{skill_id}")
async def update_user_skill(skill_id: str, payload: SkillUpdateRequest, request: Request):
    """更新用户技能元数据或正文。"""
    try:
        user = _get_current_user(request)
        skill = await skill_service.update_user_skill_draft_async(skill_id, payload.model_dump(exclude_unset=True), str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data=skill, msg="保存成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_UPDATE_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_UPDATE_INVALID, status="error")
    except Exception as e:
        logger.error("更新用户技能失败: skill_id=%s error=%s", skill_id, e, exc_info=True)
        return business_response(msg="更新用户技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_UPDATE_FAILED, status="error")


@router.delete("/skills/{skill_id}")
async def delete_user_skill(skill_id: str, request: Request):
    """删除用户自己的技能。"""
    try:
        user = _get_current_user(request)
        await skill_service.delete_user_skill_async(skill_id, str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data={"deleted": True}, msg="删除成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_DELETE_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_DELETE_INVALID, status="error")
    except Exception as e:
        logger.error("删除用户技能失败: skill_id=%s error=%s", skill_id, e, exc_info=True)
        return business_response(msg="删除用户技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_DELETE_FAILED, status="error")


@router.post("/skills/{skill_id}/copy")
async def copy_user_skill(skill_id: str, request: Request):
    """复制非系统技能为用户草稿。"""
    try:
        user = _get_current_user(request)
        skill = await skill_service.copy_user_skill_async(skill_id, str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data=skill, msg="复制成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_COPY_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_COPY_INVALID, status="error")
    except Exception as e:
        logger.error("复制用户技能失败: skill_id=%s error=%s", skill_id, e, exc_info=True)
        return business_response(msg="复制用户技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_COPY_FAILED, status="error")


@router.post("/skills/{skill_id}/publish")
async def publish_user_skill(skill_id: str, request: Request):
    """发布用户技能最新草稿。"""
    try:
        user = _get_current_user(request)
        skill = await skill_service.publish_user_skill_async(skill_id, str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data=skill, msg="发布成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_PUBLISH_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_PUBLISH_INVALID, status="error")
    except Exception as e:
        logger.error("发布用户技能失败: skill_id=%s error=%s", skill_id, e, exc_info=True)
        return business_response(msg="发布用户技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_PUBLISH_FAILED, status="error")


@router.get("/skills/{skill_id}/versions")
async def list_skill_versions(skill_id: str, request: Request):
    """获取用户技能版本列表。"""
    try:
        user = _get_current_user(request)
        versions = await skill_service.list_skill_versions_async(skill_id, str(user.user_id))
        return business_response(data={"versions": versions, "total": len(versions)}, msg="获取成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_VERSION_LIST_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_VERSION_LIST_INVALID, status="error")
    except Exception as e:
        logger.error("获取技能版本列表失败: skill_id=%s error=%s", skill_id, e, exc_info=True)
        return business_response(msg="获取技能版本列表失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_VERSION_LIST_FAILED, status="error")


@router.get("/skills/{skill_id}/versions/{version_no}")
async def get_skill_version(skill_id: str, version_no: int, request: Request):
    """获取用户技能指定版本详情。"""
    try:
        user = _get_current_user(request)
        version = await skill_service.get_skill_version_detail_async(skill_id, version_no, str(user.user_id))
        return business_response(data=version, msg="获取成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_INVALID, status="error")
    except Exception as e:
        logger.error("获取技能版本详情失败: skill_id=%s version_no=%s error=%s", skill_id, version_no, e, exc_info=True)
        return business_response(msg="获取技能版本详情失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_FAILED, status="error")


@router.get("/skills/{skill_id}/versions/{version_no}/files")
async def list_skill_version_files(skill_id: str, version_no: int, request: Request):
    """列出用户技能版本固定目录文件。"""
    try:
        user = _get_current_user(request)
        files = await skill_service.list_skill_version_files_async(skill_id, version_no, str(user.user_id))
        return business_response(data=files, msg="获取成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_VERSION_FILES_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_VERSION_FILES_INVALID, status="error")
    except Exception as e:
        logger.error("获取技能版本文件列表失败: skill_id=%s version_no=%s error=%s", skill_id, version_no, e, exc_info=True)
        return business_response(msg="获取技能版本文件列表失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_VERSION_FILES_FAILED, status="error")


@router.get("/skills/{skill_id}/versions/{version_no}/files/content")
async def read_skill_version_file(skill_id: str, version_no: int, dir_name: str, file_path: str, request: Request):
    """读取用户技能版本固定目录文件。"""
    try:
        user = _get_current_user(request)
        file_data = await skill_service.read_skill_version_file_async(skill_id, version_no, dir_name, file_path, str(user.user_id))
        return business_response(data=file_data, msg="获取成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_FILE_READ_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_FILE_READ_INVALID, status="error")
    except Exception as e:
        logger.error("读取技能版本文件失败: skill_id=%s version_no=%s error=%s", skill_id, version_no, e, exc_info=True)
        return business_response(msg="读取技能版本文件失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_FILE_READ_FAILED, status="error")


@router.put("/skills/{skill_id}/versions/{version_no}/files")
async def save_skill_version_file(skill_id: str, version_no: int, payload: SkillFileUpsertRequest, request: Request):
    """保存用户技能版本固定目录文件。"""
    try:
        user = _get_current_user(request)
        file_data = await skill_service.save_skill_version_file_async(skill_id, version_no, payload.dir_name, payload.file_path, payload.content, str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data=file_data, msg="保存成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_FILE_SAVE_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_FILE_SAVE_INVALID, status="error")
    except Exception as e:
        logger.error("保存技能版本文件失败: skill_id=%s version_no=%s error=%s", skill_id, version_no, e, exc_info=True)
        return business_response(msg="保存技能版本文件失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_FILE_SAVE_FAILED, status="error")


@router.delete("/skills/{skill_id}/versions/{version_no}/files")
async def delete_skill_version_file(skill_id: str, version_no: int, dir_name: str, file_path: str, request: Request):
    """删除用户技能版本固定目录文件。"""
    try:
        user = _get_current_user(request)
        file_data = await skill_service.delete_skill_version_file_async(skill_id, version_no, dir_name, file_path, str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data=file_data, msg="删除成功")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_FILE_DELETE_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_FILE_DELETE_INVALID, status="error")
    except Exception as e:
        logger.error("删除技能版本文件失败: skill_id=%s version_no=%s error=%s", skill_id, version_no, e, exc_info=True)
        return business_response(msg="删除技能版本文件失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_FILE_DELETE_FAILED, status="error")


@router.post("/skills/{skill_id}/versions/{version_no}/rollback")
async def rollback_skill_version(skill_id: str, version_no: int, request: Request):
    """将用户技能切回指定版本。"""
    try:
        user = _get_current_user(request)
        skill = await skill_service.rollback_user_skill_async(skill_id, version_no, str(user.user_id), operator_name=str(user.username or ""))
        return business_response(data=skill, msg="已切换版本")
    except HTTPException:
        raise
    except PermissionError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_ROLLBACK_FORBIDDEN, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_ROLLBACK_INVALID, status="error")
    except Exception as e:
        logger.error("切换技能版本失败: skill_id=%s version_no=%s error=%s", skill_id, version_no, e, exc_info=True)
        return business_response(msg="切换技能版本失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_ROLLBACK_FAILED, status="error")


@router.post("/skills/{skill_id}/favorite")
async def favorite_skill(skill_id: str, request: Request):
    """收藏技能。"""
    try:
        user = _get_current_user(request)
        return business_response(data=await skill_service.favorite_skill_async(skill_id, str(user.user_id), True, operator_name=str(user.username or "")), msg="收藏成功")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_FAVORITE_INVALID, status="error")
    except Exception as e:
        logger.error("收藏技能失败: skill_id=%s error=%s", skill_id, e, exc_info=True)
        return business_response(msg="收藏技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_FAVORITE_FAILED, status="error")


@router.delete("/skills/{skill_id}/favorite")
async def unfavorite_skill(skill_id: str, request: Request):
    """取消收藏技能。"""
    try:
        user = _get_current_user(request)
        return business_response(data=await skill_service.favorite_skill_async(skill_id, str(user.user_id), False, operator_name=str(user.username or "")), msg="已取消收藏")
    except HTTPException:
        raise
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_SKILL_UNFAVORITE_INVALID, status="error")
    except Exception as e:
        logger.error("取消收藏技能失败: skill_id=%s error=%s", skill_id, e, exc_info=True)
        return business_response(msg="取消收藏技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_UNFAVORITE_FAILED, status="error")


@router.post("/skills/sync-system")
async def sync_system_skills(request: Request):
    """同步系统内置技能到数据库。"""
    try:
        user = _optional_current_user(request)
        operator = str(getattr(user, "user_id", "") or "system")
        operator_name = str(getattr(user, "username", "") or "")
        return business_response(data=await skill_service.sync_builtin_skills_async(operator=operator, operator_name=operator_name), msg="同步成功")
    except Exception as e:
        logger.error("同步系统技能失败: %s", e, exc_info=True)
        return business_response(msg="同步系统技能失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_SYNC_FAILED, status="error")


@router.post("/skills/reload")
async def reload_skills():
    """刷新专家技能元数据缓存。"""
    try:
        return business_response(data=await skill_service.reload_async(), msg="刷新成功")
    except Exception as e:
        logger.error("刷新技能缓存失败: %s", e, exc_info=True)
        return business_response(msg="刷新技能缓存失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_RELOAD_FAILED, status="error")


@router.get("/skills/{skill_name}")
async def get_skill(skill_name: str, request: Request):
    """获取专家技能详情。"""
    try:
        user = _optional_current_user(request)
        user_id = str(getattr(user, "user_id", "") or "").strip()
        skill = await skill_service.get_skill_async(skill_name, include_draft=True, user_id=user_id, user_names=_current_user_names(user))
        if not skill:
            return business_response(msg=f"技能不存在: {skill_name}", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        return business_response(data=skill, msg="获取成功")
    except Exception as e:
        logger.error("获取技能详情失败: skill_name=%s error=%s", skill_name, e, exc_info=True)
        return business_response(msg="获取技能详情失败，请稍后重试", code=ErrorCode.CHATDATA_SKILL_DETAIL_FAILED, status="error")
