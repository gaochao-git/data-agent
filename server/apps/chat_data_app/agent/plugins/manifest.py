"""插件 manifest（``plugin.json``）的解析与校验。

插件是「资源型」扩展包：声明技能目录（``skills/``）与 MCP server（``mcp_servers``），
不做进程内 Python 代码注入。schema 保持宽松前向兼容：未知字段忽略，已知字段
必须合法；单条 ``mcp_servers`` 声明复用 ``agent/mcp/config.py`` 的静态解析，
保证 manifest 与运行时同一口径。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..mcp.config import MCPServerConfigError, parse_mcp_server_config

PLUGIN_MANIFEST_FILENAME = "plugin.json"

# 插件名是磁盘目录名与管理 API 的资源 id，约束为小写安全标识。
PLUGIN_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
# 技能名与快照目录名一致，允许大小写（与现有技能命名习惯一致）。
PLUGIN_SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
PLUGIN_VERSION_MAX_LENGTH = 32
PLUGIN_DISPLAY_NAME_MAX_LENGTH = 64
PLUGIN_DESCRIPTION_MAX_LENGTH = 500


class PluginManifestError(ValueError):
    """插件 manifest 非法。"""


@dataclass(frozen=True)
class PluginManifest:
    """已解析校验的插件 manifest。"""

    name: str
    version: str
    display_name: str
    description: str
    skills: tuple[str, ...]
    mcp_servers: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        """转成 API 可序列化 dict（mcp_servers 原样透出供管理端展示）。"""
        return {
            "name": self.name,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "skills": list(self.skills),
            "mcp_servers": [dict(entry) for entry in self.mcp_servers],
        }


def _validate_mcp_server_entry(entry: object, source: str) -> None:
    """manifest 阶段的 MCP server 声明：复用 mcp/config 的单条解析，保证唯一口径。

    解析只做静态校验（不发起网络），非法声明在安装/加载阶段直接拒绝。
    """
    try:
        parse_mcp_server_config(entry, source=source)
    except MCPServerConfigError as err:
        raise PluginManifestError(str(err)) from err


def _parse_name(data: dict, source: str) -> str:
    """解析并校验 name（同时是磁盘目录名与管理 API 资源 id）。"""
    name = str(data.get("name") or "").strip()
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        raise PluginManifestError(f"{source}: name 非法（需匹配 {PLUGIN_NAME_PATTERN.pattern}）: {name!r}")
    return name


def _parse_version(data: dict, source: str) -> str:
    """解析并校验 version。"""
    version = str(data.get("version") or "").strip()
    if not version or len(version) > PLUGIN_VERSION_MAX_LENGTH:
        raise PluginManifestError(f"{source}: version 必须是非空且不超过 {PLUGIN_VERSION_MAX_LENGTH} 字符")
    return version


def _parse_display_name(data: dict, name: str, source: str) -> str:
    """解析并校验 display_name（缺省用 name）。"""
    display_name = str(data.get("display_name") or "").strip() or name
    if len(display_name) > PLUGIN_DISPLAY_NAME_MAX_LENGTH:
        raise PluginManifestError(f"{source}: display_name 不能超过 {PLUGIN_DISPLAY_NAME_MAX_LENGTH} 字符")
    return display_name


def _parse_description(data: dict, source: str) -> str:
    """解析并校验 description。"""
    description = str(data.get("description") or "").strip()
    if len(description) > PLUGIN_DESCRIPTION_MAX_LENGTH:
        raise PluginManifestError(f"{source}: description 不能超过 {PLUGIN_DESCRIPTION_MAX_LENGTH} 字符")
    return description


def _parse_skill_names(data: dict, source: str) -> tuple[str, ...]:
    """解析并校验 skills 声明（字符串数组、名字合法、不重复）。"""
    skills_raw = data.get("skills") or []
    if not isinstance(skills_raw, list) or not all(isinstance(item, str) for item in skills_raw):
        raise PluginManifestError(f"{source}: skills 必须是字符串数组")
    skills: list[str] = []
    for skill_name in skills_raw:
        normalized = skill_name.strip()
        if not PLUGIN_SKILL_NAME_PATTERN.fullmatch(normalized):
            raise PluginManifestError(f"{source}: 技能名非法: {skill_name!r}")
        if normalized in skills:
            raise PluginManifestError(f"{source}: 技能名重复声明: {normalized}")
        skills.append(normalized)
    return tuple(skills)


def _parse_mcp_entries(data: dict, source: str) -> tuple[dict[str, Any], ...]:
    """解析并校验 mcp_servers 声明（每项只做结构校验，完整校验在 mcp/config.py）。"""
    mcp_raw = data.get("mcp_servers") or []
    if not isinstance(mcp_raw, list):
        raise PluginManifestError(f"{source}: mcp_servers 必须是数组")
    for index, entry in enumerate(mcp_raw):
        _validate_mcp_server_entry(entry, source=f"{source} mcp_servers[{index}]")
    return tuple(dict(entry) for entry in mcp_raw)


def parse_plugin_manifest(data: object, *, source: str = "plugin.json") -> PluginManifest:
    """解析 manifest dict，非法抛 ``PluginManifestError``。"""
    if not isinstance(data, dict):
        raise PluginManifestError(f"{source}: manifest 必须是 JSON 对象")
    name = _parse_name(data, source)
    version = _parse_version(data, source)
    display_name = _parse_display_name(data, name, source)
    description = _parse_description(data, source)
    return PluginManifest(
        name=name,
        version=version,
        display_name=display_name,
        description=description,
        skills=_parse_skill_names(data, source),
        mcp_servers=_parse_mcp_entries(data, source),
    )


def load_plugin_manifest(plugin_dir: Path) -> PluginManifest:
    """读取插件目录下的 ``plugin.json`` 并解析。"""
    manifest_path = Path(plugin_dir) / PLUGIN_MANIFEST_FILENAME
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as err:
        raise PluginManifestError(f"读取 {PLUGIN_MANIFEST_FILENAME} 失败: {err}") from err
    try:
        data = json.loads(text)
    except ValueError as err:
        raise PluginManifestError(f"{PLUGIN_MANIFEST_FILENAME} 不是合法 JSON: {err}") from err
    return parse_plugin_manifest(data, source=str(manifest_path))


__all__ = [
    "PLUGIN_MANIFEST_FILENAME",
    "PLUGIN_NAME_PATTERN",
    "PLUGIN_SKILL_NAME_PATTERN",
    "PluginManifest",
    "PluginManifestError",
    "load_plugin_manifest",
    "parse_plugin_manifest",
]
