"""插件管理业务编排：安装 / 启停 / 卸载 / 列表。

流程编排与事务边界集中在此（routers 只做 HTTP 入参与统一响应）：
- 启停以 DB 表 ``chat_data_plugin`` 为唯一真值，磁盘只存内容；
- 任何变更后刷新进程内启停快照并重置 MCP 工具枚举缓存，让下一轮装配看到最新状态；
- 安装先解包落盘、再写 DB 行；DB 写失败回滚已解包目录，避免"盘上有、库里无"的半装态。
"""

from __future__ import annotations

import json
import shutil
from typing import Any, Dict

from utils.logger import get_logger
from utils.models import ChatDataPlugin
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


def _format_db_time(value: Any) -> str:
    """DB 时间字段的统一展示格式（与 conversation_service 等口径一致）。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


async def list_plugins(*, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """已安装插件分页列表（含禁用项），启停与创建人/创建时间来自 DB。

    磁盘扫描提供内容（manifest），DB 提供状态与审计字段；半装态孤儿插件
    （DB 无行）审计字段留空，首次启停时自愈补行。返回 ``{"plugins": [...], "total": n}``，
    分页口径与定时任务一致（limit 已由 router 收敛到 [1, 200]）。
    """
    await refresh_plugin_state()
    items = [item.to_dict() for item in load_plugins_metadata().values()]
    audit_by_name: Dict[str, ChatDataPlugin] = {}
    try:
        audit_by_name = {row.plugin_name: row for row in await PluginDao().list_active()}
    except Exception as err:
        logger.warning("读取插件审计字段失败，列表将不展示创建人/创建时间: %s", err)
    for item in items:
        row = audit_by_name.get(item["name"])
        item["create_by_name"] = str(row.create_by_name or "") if row is not None else ""
        item["create_time"] = _format_db_time(row.create_time) if row is not None else ""
    # 磁盘扫描本身按目录名排序（稳定），此处仅做分页切片
    ordered = sorted(items, key=lambda item: item["name"])
    return {"plugins": ordered[offset:offset + limit], "total": len(ordered)}


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


async def _reconcile_orphan(
    name: str,
    *,
    enabled: bool,
    operator_id: str,
    operator_name: str,
) -> bool:
    """半装态自愈：磁盘有插件目录但 DB 无活跃行时，按 manifest 补建 DB 行。

    触发场景：DB 换库/清数据、历史版本（磁盘 ``.disabled`` 时代）遗留的插件目录。
    命中即按 ``upsert_installed`` 复活/新建行，再落本次启停值；磁盘上没有则返回 False。
    列表展示不做写操作（读接口保持纯读），孤儿插件的启停开关自然触发补建。
    """
    metadata = load_plugins_metadata().get(name)
    if metadata is None:
        return False
    manifest = metadata.manifest
    await PluginDao().upsert_installed(
        plugin_name=manifest.name,
        version=manifest.version,
        display_name=manifest.display_name,
        description=manifest.description,
        manifest_text=_manifest_json(manifest),
        operator_id=operator_id,
        operator_name=operator_name,
    )
    await PluginDao().set_enabled(
        plugin_name=name,
        enabled=enabled,
        operator_id=operator_id,
        operator_name=operator_name,
    )
    clear_metadata_cache()
    logger.info("插件半装态自愈：已补建 DB 行 name=%s version=%s enabled=%s", manifest.name, manifest.version, enabled)
    return True


async def set_plugin_enabled(
    name: str,
    *,
    enabled: bool,
    operator_id: str,
    operator_name: str,
) -> bool:
    """启用/禁用插件（写 DB）；返回是否命中记录。变更后刷新快照与缓存。

    DB 未命中时先做半装态自愈（磁盘有、库里无则补行），再按本次启停值落库。
    """
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise PluginManifestError(f"插件名非法: {name!r}")
    updated = await PluginDao().set_enabled(
        plugin_name=name,
        enabled=enabled,
        operator_id=operator_id,
        operator_name=operator_name,
    )
    if not updated:
        updated = await _reconcile_orphan(
            name, enabled=enabled, operator_id=operator_id, operator_name=operator_name,
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