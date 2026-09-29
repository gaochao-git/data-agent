"""插件目录扫描与运行期视图。

磁盘布局：``CHAT_DATA_HOME/plugins/<name>/``，目录下必有 ``plugin.json``。

启停状态以 DB 表 ``chat_data_plugin`` 为唯一真值（不再用磁盘 ``.disabled`` 标记）：
本模块维护一份进程内 ``_enabled_state`` 快照，由 :func:`refresh_plugin_state` 从 DB
异步刷新；每个需要插件状态的入口（会话装配、技能快照、管理端列表）先 await 刷新，
同步读取方（``enabled_plugins`` 等）消费该快照。DB 读失败按不可用处理（快照清空、
fail-closed），不兜底旧值。磁盘缓存只存内容（manifest+目录），启停在读取时叠加。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.chat_data_app.daos.plugin_dao import PluginDao
from utils.logger import get_logger
from utils.storage_paths import CHAT_DATA_HOME

from .manifest import (
    PLUGIN_MANIFEST_FILENAME,
    PluginManifest,
    PluginManifestError,
    load_plugin_manifest,
)

logger = get_logger(__name__)

PLUGINS_DIR_NAME = "plugins"


def plugins_root(chat_data_home: str | None = None) -> Path:
    """插件物理根：``CHAT_DATA_HOME/plugins``。"""
    return Path(chat_data_home or CHAT_DATA_HOME) / PLUGINS_DIR_NAME


def plugin_dir(name: str, chat_data_home: str | None = None) -> Path:
    """单个插件目录（不做合法性校验，调用方必须先校验 name）。"""
    return plugins_root(chat_data_home) / name


@dataclass(frozen=True)
class PluginMetadata:
    """已安装插件的运行期视图。"""

    manifest: PluginManifest
    dir: Path
    enabled: bool

    def to_dict(self) -> dict[str, Any]:
        """转成管理 API 可序列化 dict。"""
        return {
            **self.manifest.to_dict(),
            "enabled": self.enabled,
            "dir": str(self.dir),
        }


# 磁盘内容缓存（manifest+目录），与启停解耦；启停来自 _enabled_state。
_disk_cache: dict[str, tuple[PluginManifest, Path]] | None = None
# DB 启停快照：None 表示本进程尚未成功刷新；frozenset() 表示已刷新且无启用项。
_enabled_state: frozenset[str] | None = None


def clear_metadata_cache() -> None:
    """清理磁盘内容缓存（安装/卸载后调用，让下一轮扫描看到新目录）。"""
    global _disk_cache
    _disk_cache = None


async def refresh_plugin_state() -> None:
    """从 DB 刷新插件启停快照。

    读失败即视为不可用：快照清空、当轮所有插件按禁用处理（fail-closed，不兜底旧值），
    并记录真实错误。调用方在会话装配、技能快照、管理端列表等入口先 await 本函数。
    """
    global _enabled_state
    try:
        _enabled_state = frozenset(await PluginDao().list_enabled_names())
    except Exception as err:
        logger.error("刷新插件启停状态失败，本轮所有插件按不可用处理: %s", err, exc_info=True)
        _enabled_state = frozenset()


def _scan_disk_plugins(*, force_refresh: bool = False) -> dict[str, tuple[PluginManifest, Path]]:
    """扫描插件目录，返回 ``{插件名: (manifest, 目录)}``（带进程级磁盘缓存）。

    单个插件 manifest 损坏、目录名与 manifest.name 不一致、或为符号链接，只告警跳过。
    """
    global _disk_cache
    if _disk_cache is not None and not force_refresh:
        return _disk_cache
    root = plugins_root()
    found: dict[str, tuple[PluginManifest, Path]] = {}
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.is_symlink() or entry.name.startswith("."):
                continue
            try:
                manifest = load_plugin_manifest(entry)
            except PluginManifestError as err:
                logger.warning("忽略非法插件 %s: %s", entry.name, err)
                continue
            if manifest.name != entry.name:
                logger.warning(
                    "忽略目录名与 manifest.name 不一致的插件: dir=%s manifest=%s",
                    entry.name,
                    manifest.name,
                )
                continue
            found[manifest.name] = (manifest, entry)
    _disk_cache = found
    return found


def load_plugins_metadata(*, force_refresh: bool = False) -> dict[str, PluginMetadata]:
    """返回 ``{插件名: 元数据}``，启停标志叠加自 DB 快照 ``_enabled_state``。

    未成功刷新（``_enabled_state is None``）时按全部禁用处理，避免读到未经 DB 确认的
    启用态；调用方应先 :func:`refresh_plugin_state`。
    """
    enabled = _enabled_state if _enabled_state is not None else frozenset()
    return {
        name: PluginMetadata(manifest=manifest, dir=directory, enabled=name in enabled)
        for name, (manifest, directory) in _scan_disk_plugins(force_refresh=force_refresh).items()
    }


def enabled_plugins() -> list[PluginMetadata]:
    """当前已启用（磁盘存在且 DB 标记启用）的插件列表。"""
    return [item for item in load_plugins_metadata().values() if item.enabled]


def enabled_plugin_skill_sources() -> list[tuple[str, Path]]:
    """已启用插件声明的技能源 ``(技能名, 技能目录)``，供会话技能快照纳入。"""
    sources: list[tuple[str, Path]] = []
    for plugin in enabled_plugins():
        for skill_name in plugin.manifest.skills:
            skill_path = plugin.dir / "skills" / skill_name
            if not skill_path.is_dir() or skill_path.is_symlink():
                logger.warning("插件 %s 声明的技能目录缺失，跳过: %s", plugin.manifest.name, skill_path)
                continue
            if not (skill_path / "SKILL.md").is_file():
                logger.warning("插件 %s 技能缺少 SKILL.md，跳过: %s", plugin.manifest.name, skill_path)
                continue
            sources.append((skill_name, skill_path))
    return sources


def enabled_plugin_mcp_servers() -> list[dict[str, Any]]:
    """已启用插件声明的 MCP server 配置（原始 dict，交给 mcp/config.py 校验解析）。"""
    entries: list[dict[str, Any]] = []
    for plugin in enabled_plugins():
        for entry in plugin.manifest.mcp_servers:
            entries.append(dict(entry))
    return entries


__all__ = [
    "PLUGINS_DIR_NAME",
    "PLUGIN_MANIFEST_FILENAME",
    "PluginMetadata",
    "clear_metadata_cache",
    "enabled_plugin_mcp_servers",
    "enabled_plugin_skill_sources",
    "enabled_plugins",
    "load_plugins_metadata",
    "plugin_dir",
    "plugins_root",
    "refresh_plugin_state",
]
