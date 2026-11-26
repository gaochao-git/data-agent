"""插件管理业务编排：安装 / 启停 / 卸载 / 列表。

流程编排与事务边界集中在此（routers 只做 HTTP 入参与统一响应）：
- 启停以 DB 表 ``chat_data_plugin`` 为唯一真值，磁盘只存内容；
- 任何变更后刷新进程内启停快照并重置 MCP 工具枚举缓存，让下一轮装配看到最新状态；
- 安装先解包落盘、再写 DB 行；DB 写失败回滚已解包目录，避免"盘上有、库里无"的半装态。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from utils.logger import get_logger
from utils.models import ChatDataPlugin

from ..agent.mcp.manager import reset_mcp_tool_manager
from ..agent.plugins import installer
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


def _mcp_server_admission_view(entry: dict[str, Any]) -> dict[str, Any]:
    """单个 MCP server 声明的准入视图（管理页展示"未通过及原因"）。

    复用 ``agent.plugins.installer`` 的同一套解析/校验实现——services 不新增对
    agent 子模块的依赖边（架构守卫：services→agent 边数上限）。
    """
    return installer.inspect_mcp_server_admission(entry)


def _reset_runtime_caches() -> None:
    """插件变更后重置 MCP 工具枚举缓存（进程级单例，带 TTL）。"""
    try:
        reset_mcp_tool_manager()
    except Exception as err:
        logger.warning("重置 MCP 工具管理器失败: %s", err)


def _format_db_time(value: Any) -> str:
    """DB 时间字段的统一展示格式（与 conversation_service 等口径一致）。"""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


async def list_plugins(*, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """已安装插件分页列表（含禁用项），启停与创建人/创建时间来自 DB。

    磁盘扫描提供内容（manifest），DB 提供状态与审计字段；半装态孤儿插件
    （DB 无行）审计字段留空，首次启停时自愈补行。返回 ``{"plugins": [...], "total": n}``，
    分页口径与定时任务一致（limit 已由 router 收敛到 [1, 200]）。
    """
    await refresh_plugin_state()
    # 磁盘扫描读 manifest 属同步 IO；变更后缓存被清空，首次列表必然全盘扫描。
    metadata = await asyncio.to_thread(load_plugins_metadata)
    items = [item.to_dict() for item in metadata.values()]
    for item in items:
        item["mcp_servers"] = [
            {**server, "admission": _mcp_server_admission_view(server)} for server in item.get("mcp_servers") or []
        ]
    audit_by_name: dict[str, ChatDataPlugin] = {}
    permissions_by_plugin: dict[str, list[str]] = {}
    try:
        dao = PluginDao()
        audit_by_name = {row.plugin_name: row for row in await dao.list_active()}
        permissions_by_plugin = await dao.list_active_permissions()
    except Exception as err:
        logger.warning("读取插件审计或权限字段失败，列表将降级展示: %s", err)
    for item in items:
        row = audit_by_name.get(item["name"])
        item["create_by_name"] = str(row.create_by_name or "") if row is not None else ""
        item["create_time"] = _format_db_time(row.create_time) if row is not None else ""
        item["scope_type"] = str(row.scope_type or "all") if row is not None else "all"
        item["user_names"] = permissions_by_plugin.get(item["name"], []) if item["scope_type"] == "selected" else []
    # 磁盘扫描本身按目录名排序（稳定），此处仅做分页切片
    ordered = sorted(items, key=lambda item: item["name"])
    return {"plugins": ordered[offset : offset + limit], "total": len(ordered)}


async def probe_mcp_server_tools(plugin_name: str, server_id: str) -> dict[str, Any]:
    """探测插件声明的单个 MCP server（实现下沉到 agent/plugins/installer，避免 services→agent 新依赖边）。"""
    return await installer.probe_mcp_server_tools(plugin_name, server_id)


async def install_plugin(
    zip_bytes: bytes,
    *,
    operator_id: str,
    operator_name: str,
) -> PluginManifest:
    """安装插件：解包落盘 → 写 DB 行（默认启用）→ 刷新快照与缓存。

    DB 写失败时回滚已解包目录，避免磁盘与 DB 不一致。
    """
    # zip 解析、成员校验与安全解压（解压上限 200MB）全为同步 IO，放线程池避免阻塞事件循环。
    manifest = await asyncio.to_thread(installer.install_plugin_from_zip_bytes, zip_bytes)
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
            await asyncio.to_thread(shutil.rmtree, rollback_dir, ignore_errors=True)
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
    metadata = (await asyncio.to_thread(load_plugins_metadata)).get(name)
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
            name,
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
    # 卸载会递归删除整个插件目录树，同样属于同步 IO。
    manifest = await asyncio.to_thread(installer.uninstall_plugin, name)
    await PluginDao().mark_deleted(
        plugin_name=name,
        operator_id=operator_id,
        operator_name=operator_name,
    )
    clear_metadata_cache()
    await refresh_plugin_state()
    _reset_runtime_caches()
    return manifest


def _normalize_plugin_permission_scope(scope_type: str) -> str:
    normalized = str(scope_type or "").strip().lower()
    if normalized not in {"all", "selected"}:
        raise ValueError("权限范围只能是 all 或 selected")
    return normalized


def _normalize_plugin_permission_user_names(user_names: list[str]) -> list[str]:
    """按 username 精确去重并保留大小写；空白项或超长值直接拒绝。"""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in user_names:
        user_name = str(raw or "").strip()
        if not user_name:
            raise ValueError("授权用户名不能为空")
        if len(user_name) > 100:
            raise ValueError("授权用户名不能超过 100 个字符")
        if user_name not in seen:
            seen.add(user_name)
            normalized.append(user_name)
    if not normalized:
        raise ValueError("指定用户授权至少需要选择一个 username")
    return normalized


async def set_plugin_permissions(
    name: str,
    *,
    scope_type: str,
    user_names: list[str],
    operator_id: str,
    operator_name: str,
) -> dict[str, Any] | None:
    """替换插件人员授权；插件不存在时返回 ``None``，否则返回落库结果。"""
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"插件名非法: {name!r}")
    normalized_scope = _normalize_plugin_permission_scope(scope_type)
    normalized_users = _normalize_plugin_permission_user_names(user_names) if normalized_scope == "selected" else []
    updated = await PluginDao().replace_permissions(
        plugin_name=name,
        scope_type=normalized_scope,
        user_names=normalized_users,
        operator_id=operator_id,
        operator_name=operator_name,
    )
    if updated:
        await refresh_plugin_state()
    if not updated:
        return None
    return {"scope_type": normalized_scope, "user_names": normalized_users}


__all__ = [
    "install_plugin",
    "list_plugins",
    "set_plugin_permissions",
    "set_plugin_enabled",
    "uninstall_plugin",
]
