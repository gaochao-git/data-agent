"""插件管理业务编排：安装 / 启停 / 卸载 / 列表。

流程编排与事务边界集中在此（routers 只做 HTTP 入参与统一响应）：
- 启停以 DB 表 ``chat_data_plugin`` 为唯一真值，磁盘只存内容；
- 任何变更后刷新进程内启停快照并重置 MCP 工具枚举缓存，让下一轮装配看到最新状态；
- 安装先解包落盘、再写 DB 行；DB 写失败回滚已解包目录，避免"盘上有、库里无"的半装态。
"""

from __future__ import annotations

import json
import shutil
from typing import Any, Dict, List

from utils.logger import get_logger
from ..agent.mcp.manager import reset_mcp_tool_manager
from ..agent.plugins.installer import (
    install_plugin_from_zip_bytes,
    uninstall_plugin as uninstall_plugin_files,
)
from ..agent.plugins.loader import (
    clear_metadata_cache,
    load_plugins_metadata,
    plugin_dir,
    refresh_plugin_state,
)
from ..agent.plugins.manifest import PLUGIN_NAME_PATTERN, PluginManifest, PluginManifestError
from ..daos.plugin_dao import PluginDao

logger = get_logger(__name__)


def _manifest_json(manifest: PluginManifest) -> str:
    """把 manifest 序列化为 JSON 文本存入 DB（不使用 MySQL JSON 列）。"""
    return json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True)


def _reset_runtime_caches() -> None:
    """插件变更后重置 MCP 工具枚举缓存（进程级单例，带 TTL）。"""
    try:
        reset_mcp_tool_manager()
    except Exception as err:
        logger.warning("重置 MCP 工具管理器失败: %s", err)


async def list_plugins() -> List[Dict[str, Any]]:
    """列出已安装插件（含禁用项），启停状态来自 DB。"""
    await refresh_plugin_state()
    return [item.to_dict() for item in load_plugins_metadata().values()]


async def install_plugin(
    zip_bytes: bytes,
    *,
    operator_id: str,
    operator_name: str,
) -> PluginManifest:
    """安装插件：解包落盘 → 写 DB 行（默认启用）→ 刷新快照与缓存。

    DB 写失败时回滚已解包目录，避免磁盘与 DB 不一致。
    """
    manifest = install_plugin_from_zip_bytes(zip_bytes)
    try:
        await PluginDao().upsert_installed(
            plugin_name=manifest.name,
            version=manifest.version,
            display_name=manifest.display_name,
            description=manifest.description,
            manifest_text=_manifest_json(manifest),
            operator_id=operator_id,
            operator_name=operator_name,
        )
    except Exception:
        rollback_dir = plugin_dir(manifest.name)
        if rollback_dir.is_dir():
            shutil.rmtree(rollback_dir, ignore_errors=True)
        clear_metadata_cache()
        logger.error("插件安装写库失败，已回滚解包目录: %s", manifest.name, exc_info=True)
        raise
    clear_metadata_cache()
    await refresh_plugin_state()
    _reset_runtime_caches()
    return manifest


async def set_plugin_enabled(
    name: str,
    *,
    enabled: bool,
    operator_id: str,
    operator_name: str,
) -> bool:
    """启用/禁用插件（写 DB）；返回是否命中记录。变更后刷新快照与缓存。"""
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise PluginManifestError(f"插件名非法: {name!r}")
    updated = await PluginDao().set_enabled(
        plugin_name=name,
        enabled=enabled,
        operator_id=operator_id,
        operator_name=operator_name,
    )
    if updated:
        await refresh_plugin_state()
        _reset_runtime_caches()
    return updated


async def uninstall_plugin(
    name: str,
    *,
    operator_id: str,
    operator_name: str,
) -> PluginManifest:
    """卸载插件：删除磁盘目录 → 软删 DB 行 → 刷新快照与缓存。

    先删盘、后软删 DB：文件删除失败（如插件不存在）时直接抛出、不写 DB。若极端情况下
    删盘成功但 DB 软删失败，会残留一条"活跃但无内容"的行——列表按磁盘扫描不会展示它，
    重装时按 plugin_name 复活同一行自愈，不影响正确性。
    """
    manifest = uninstall_plugin_files(name)
    await PluginDao().mark_deleted(
        plugin_name=name,
        operator_id=operator_id,
        operator_name=operator_name,
    )
    clear_metadata_cache()
    await refresh_plugin_state()
    _reset_runtime_caches()
    return manifest


__all__ = [
    "install_plugin",
    "list_plugins",
    "set_plugin_enabled",
    "uninstall_plugin",
]
