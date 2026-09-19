"""插件目录扫描与运行期视图。

磁盘布局：``CHAT_DATA_HOME/plugins/<name>/``，目录下必有 ``plugin.json``；
禁用通过目录内标记文件 ``.disabled`` 表达（管理员手动放置同样生效）。
元数据带进程级缓存；安装/启停/卸载接口负责在变更后清缓存，让下一轮
装配与新会话快照拿到最新插件视图。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
PLUGIN_DISABLED_MARKER = ".disabled"


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


_metadata_cache: dict[str, PluginMetadata] | None = None


def clear_metadata_cache() -> None:
    """清理插件元数据缓存（安装/启停/卸载后必须调用）。"""
    global _metadata_cache
    _metadata_cache = None


def load_plugins_metadata(*, force_refresh: bool = False) -> dict[str, PluginMetadata]:
    """扫描插件目录，返回 ``{插件名: 元数据}``（带进程级缓存）。

    单个插件 manifest 损坏只告警跳过，不影响其他插件。
    """
    global _metadata_cache
    if _metadata_cache is not None and not force_refresh:
        return _metadata_cache
    root = plugins_root()
    metadata: dict[str, PluginMetadata] = {}
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
            metadata[manifest.name] = PluginMetadata(
                manifest=manifest,
                dir=entry,
                enabled=not (entry / PLUGIN_DISABLED_MARKER).exists(),
            )
    _metadata_cache = metadata
    return metadata


def enabled_plugins() -> list[PluginMetadata]:
    """当前已启用的插件列表。"""
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


def set_plugin_enabled(name: str, *, enabled: bool, chat_data_home: str | None = None) -> bool:
    """启用/禁用插件（写/删 ``.disabled`` 标记）；插件不存在返回 False。"""
    from .manifest import PLUGIN_NAME_PATTERN

    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise PluginManifestError(f"插件名非法: {name!r}")
    target = plugin_dir(name, chat_data_home)
    if not target.is_dir() or target.is_symlink():
        return False
    marker = target / PLUGIN_DISABLED_MARKER
    if enabled:
        if marker.exists():
            marker.unlink()
    else:
        marker.write_text("", encoding="utf-8")
    clear_metadata_cache()
    return True


__all__ = [
    "PLUGINS_DIR_NAME",
    "PLUGIN_DISABLED_MARKER",
    "PluginMetadata",
    "clear_metadata_cache",
    "enabled_plugin_mcp_servers",
    "enabled_plugin_skill_sources",
    "enabled_plugins",
    "load_plugins_metadata",
    "plugin_dir",
    "plugins_root",
    "set_plugin_enabled",
    PLUGIN_MANIFEST_FILENAME,
]
