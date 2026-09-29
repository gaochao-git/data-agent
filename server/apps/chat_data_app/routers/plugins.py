"""插件后台管理路由：列表、zip 安装、启停、卸载。

入口由 routes.py 的 service token 统一保护；服务端无角色模型，
管理权限由可信代理的前端入口控制（与定时任务管理模式一致）。
启停状态经 service 层落 DB 表 chat_data_plugin，操作人 best-effort 记录。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, UploadFile
from pydantic import BaseModel

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response

from ..agent.plugins.installer import PluginInstallError, PluginNotFoundError
from ..agent.plugins.manifest import PluginManifestError
from ..services import plugin_service
from . import JSON_API_RESPONSES
from .dependencies import _get_current_user

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData 插件管理（Admin）"], responses=JSON_API_RESPONSES)

# 插件 zip 安装入口的字节上限：与 installer 的包上限一致（50MB）。
_PLUGIN_UPLOAD_MAX_BYTES = 50 * 1024 * 1024


def _resolve_operator(request: Request) -> tuple[str, str]:
    """best-effort 解析当前操作人（user_id, username）。

    管理端可能仅带 service token 无用户头，取不到时返回空并记日志，不阻断操作。
    """
    try:
        user = _get_current_user(request)
    except Exception as err:
        logger.warning("插件管理操作未解析到用户身份，审计字段留空: %s", err)
        return ("", "")
    return (
        str(getattr(user, "user_id", "") or "").strip(),
        str(getattr(user, "username", "") or "").strip(),
    )


class PluginEnabledRequest(BaseModel):
    """插件启停请求。"""

    enabled: bool


@router.get("/admin/plugins")
async def list_plugins(request: Request, limit: int = 50, offset: int = 0):
    """已安装插件分页列表（含禁用项），启停与审计字段来自 DB。"""
    _ = request
    try:
        data = await plugin_service.list_plugins(
            limit=max(1, min(limit, 200)),
            offset=max(0, offset),
        )
        return business_response(data=data, msg="获取成功")
    except Exception as e:
        logger.error("获取插件列表失败: %s", e, exc_info=True)
        return business_response(msg="获取插件列表失败，请稍后重试", code=ErrorCode.CHATDATA_PLUGIN_LIST_FAILED, status="error")


@router.post("/admin/plugins/install")
async def install_plugin(file: UploadFile, request: Request):
    """上传插件 zip 安装（manifest 校验 + 安全解压 + 写 DB；同名插件需先卸载）。"""
    try:
        data = await file.read(_PLUGIN_UPLOAD_MAX_BYTES + 1)
        if len(data) > _PLUGIN_UPLOAD_MAX_BYTES:
            return business_response(
                msg=f"插件包超过大小上限 {_PLUGIN_UPLOAD_MAX_BYTES // (1024 * 1024)}MB",
                code=ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED,
                status="error",
            )
        operator_id, operator_name = _resolve_operator(request)
        manifest = await plugin_service.install_plugin(
            data, operator_id=operator_id, operator_name=operator_name,
        )
        return business_response(
            data={"plugin": manifest.to_dict(), "effective": "新会话与下一次装配生效"},
            msg="插件安装成功",
        )
    except PluginInstallError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED, status="error")
    except PluginManifestError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_MANIFEST_INVALID, status="error")
    except Exception as e:
        logger.error("安装插件失败: %s", e, exc_info=True)
        return business_response(msg="安装插件失败，请稍后重试", code=ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED, status="error")


@router.put("/admin/plugins/{plugin_id}/enabled")
async def update_plugin_enabled(plugin_id: str, payload: PluginEnabledRequest, request: Request):
    """启用/禁用插件（写 DB，立即影响新会话快照与新装配）。"""
    try:
        operator_id, operator_name = _resolve_operator(request)
        updated = await plugin_service.set_plugin_enabled(
            plugin_id, enabled=payload.enabled, operator_id=operator_id, operator_name=operator_name,
        )
        if not updated:
            return business_response(msg=f"插件不存在: {plugin_id}", code=ErrorCode.CHATDATA_PLUGIN_NOT_FOUND, status="error")
        return business_response(
            data={"name": plugin_id, "enabled": payload.enabled},
            msg="插件已启用" if payload.enabled else "插件已禁用",
        )
    except PluginManifestError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_MANIFEST_INVALID, status="error")
    except Exception as e:
        logger.error("切换插件启停失败: %s", e, exc_info=True)
        return business_response(msg="切换插件状态失败，请稍后重试", code=ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED, status="error")


@router.delete("/admin/plugins/{plugin_id}")
async def remove_plugin(plugin_id: str, request: Request):
    """卸载插件：删除磁盘目录并软删 DB 记录。"""
    try:
        operator_id, operator_name = _resolve_operator(request)
        manifest = await plugin_service.uninstall_plugin(
            plugin_id, operator_id=operator_id, operator_name=operator_name,
        )
        return business_response(data={"name": manifest.name, "version": manifest.version}, msg="插件已卸载")
    except PluginNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_NOT_FOUND, status="error")
    except PluginInstallError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED, status="error")
    except Exception as e:
        logger.error("卸载插件失败: %s", e, exc_info=True)
        return business_response(msg="卸载插件失败，请稍后重试", code=ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED, status="error")
