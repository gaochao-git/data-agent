"""技能包插件：manifest 声明 + plugins 目录 + zip 安装。

插件是「资源型」扩展包（技能目录 + MCP server 声明），随目录动态安装，
生效于新会话/新装配；装配型智能体扩展走 ``AgentAssemblySpec``（随发版）。
"""

from .installer import (
    PluginInstallError,
    PluginNotFoundError,
    install_plugin_from_zip_bytes,
    uninstall_plugin,
)
from .loader import (
    PluginMetadata,
    clear_metadata_cache,
    enabled_plugin_mcp_servers,
    enabled_plugin_skill_sources,
    enabled_plugins,
    load_plugins_metadata,
    plugin_dir,
    plugins_root,
    set_plugin_enabled,
)
from .manifest import (
    PLUGIN_MANIFEST_FILENAME,
    PluginManifest,
    PluginManifestError,
    load_plugin_manifest,
    parse_plugin_manifest,
)

__all__ = [
    "PLUGIN_MANIFEST_FILENAME",
    "PluginInstallError",
    "PluginManifest",
    "PluginManifestError",
    "PluginMetadata",
    "PluginNotFoundError",
    "clear_metadata_cache",
    "enabled_plugin_mcp_servers",
    "enabled_plugin_skill_sources",
    "enabled_plugins",
    "install_plugin_from_zip_bytes",
    "load_plugin_manifest",
    "load_plugins_metadata",
    "parse_plugin_manifest",
    "plugin_dir",
    "plugins_root",
    "set_plugin_enabled",
    "uninstall_plugin",
]
