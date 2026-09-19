"""插件后台管理路由：列表、zip 安装、启停、卸载。

入口由 routes.py 的 service token 统一保护；服务端无角色模型，
管理权限由可信代理的前端入口控制（与定时任务管理模式一致）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, UploadFile

from pydantic import BaseModel

from utils.error_codes import ErrorCode
from utils.logger import get_logger
from utils.response import business_response
from ..agent.plugins.installer import (
    PluginInstallError,
    PluginNotFoundError,
    install_plugin_from_zip_bytes,
    uninstall_plugin,
)
from ..agent.plugins.loader import load_plugins_metadata, set_plugin_enabled
from ..agent.plugins.manifest import PluginManifestError
from ..agent.mcp.manager import reset_mcp_tool_manager
from . import JSON_API_RESPONSES

logger = get_logger(__name__)

router = APIRouter(tags=["ChatData 插件管理（Admin）"], responses=JSON_API_RESPONSES)

# 插件 zip 安装入口的字节上限：与 installer 的包上限一致（50MB）。
_PLUGIN_UPLOAD_MAX_BYTES = 50 * 1024 * 1024


def _invalidate_mcp_tool_cache() -> None:
    """插件安装/启停/卸载后失效 MCP 工具枚举缓存。

    技能快照对新会话天然重建；MCP manager 是带 TTL 的进程级单例，
    不主动失效会让新装配在最长缓存窗口内看不到最新插件声明。
    """
    try:
        reset_mcp_tool_manager()
    except Exception as e:
        logger.warning("重置 MCP 工具管理器失败: %s", e)


class PluginEnabledRequest(BaseModel):
    """插件启停请求。"""

    enabled: bool


@router.get("/admin/plugins")
async def list_plugins(request: Request):
    """列出已安装插件（含禁用项）。"""
    _ = request
    try:
        data = [item.to_dict() for item in load_plugins_metadata().values()]
        return business_response(data={"plugins": data}, msg="获取成功")
    except Exception as e:
        logger.error("获取插件列表失败: %s", e, exc_info=True)
        return business_response(msg="获取插件列表失败，请稍后重试", code=ErrorCode.CHATDATA_PLUGIN_LIST_FAILED, status="error")


@router.post("/admin/plugins/install")
async def install_plugin(file: UploadFile):
    """上传插件 zip 安装（manifest 校验 + 安全解压；同名插件需先卸载）。"""
    try:
        data = await file.read(_PLUGIN_UPLOAD_MAX_BYTES + 1)
        if len(data) > _PLUGIN_UPLOAD_MAX_BYTES:
            return business_response(
                msg=f"插件包超过大小上限 {_PLUGIN_UPLOAD_MAX_BYTES // (1024 * 1024)}MB",
                code=ErrorCode.CHATDATA_PLUGIN_INSTALL_FAILED,
                status="error",
            )
        manifest = install_plugin_from_zip_bytes(data)
        _invalidate_mcp_tool_cache()
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
    """启用/禁用插件（`.disabled` 标记，立即影响新会话快照与新装配）。"""
    _ = request
    try:
        try:
            updated = set_plugin_enabled(plugin_id, enabled=payload.enabled)
        except PluginManifestError as e:
            return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_MANIFEST_INVALID, status="error")
        if not updated:
            return business_response(msg=f"插件不存在: {plugin_id}", code=ErrorCode.CHATDATA_PLUGIN_NOT_FOUND, status="error")
        _invalidate_mcp_tool_cache()
        return business_response(
            data={"name": plugin_id, "enabled": payload.enabled},
            msg="插件已启用" if payload.enabled else "插件已禁用",
        )
    except Exception as e:
        logger.error("切换插件启停失败: %s", e, exc_info=True)
        return business_response(msg="切换插件状态失败，请稍后重试", code=ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED, status="error")


@router.delete("/admin/plugins/{plugin_id}")
async def remove_plugin(plugin_id: str, request: Request):
    """卸载插件并删除其目录。"""
    _ = request
    try:
        manifest = uninstall_plugin(plugin_id)
        _invalidate_mcp_tool_cache()
        return business_response(data={"name": manifest.name, "version": manifest.version}, msg="插件已卸载")
    except PluginNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_NOT_FOUND, status="error")
    except PluginInstallError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED, status="error")
    except Exception as e:
        logger.error("卸载插件失败: %s", e, exc_info=True)
        return business_response(msg="卸载插件失败，请稍后重试", code=ErrorCode.CHATDATA_PLUGIN_OPERATION_FAILED, status="error")
