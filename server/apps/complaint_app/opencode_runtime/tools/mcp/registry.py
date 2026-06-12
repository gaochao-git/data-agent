from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict

from ..base_tool import Tool
from ..base_tool import ToolContext
from utils.logger import get_logger
from .client import MCPClient, MCPClientConfig, mcp_available, mcp_import_error, run_coro_sync, stringify_tool_result

logger = get_logger(__name__)


@dataclass
class _MCPServerConfig:
    server_id: str
    url: str
    enabled: bool = True
    timeout_seconds: float = 8.0
    cache_ttl_seconds: int = 300
    prefix: str = ""
    priority: int = 0
    expose_unprefixed: bool = False


class MCPToolRegistry:
    """MCP 工具注册表：支持手工注册 + 多远端 SSE MCP 发现。"""

    _default_timeout_seconds = 8.0
    _default_cache_ttl_seconds = 300
    _manual_tools: Dict[str, Tool] = {}
    _remote_tools: Dict[str, Tool] = {}
    _remote_server_states: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        """注册一个 MCP 工具到内存表（手工注入）。"""
        if not tool or not tool.name:
            raise ValueError("Tool name is required")
        cls._manual_tools[tool.name] = tool

    @staticmethod
    def _env_bool(key: str, default: bool = False) -> bool:
        raw = os.getenv(key)
        if raw is None:
            return default
        val = str(raw).strip().lower()
        if not val:
            return default
        return val in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _to_bool(raw: Any, default: bool) -> bool:
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        value = str(raw).strip().lower()
        if not value:
            return default
        return value in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _to_int(raw: Any, default: int) -> int:
        try:
            value = int(raw)
        except Exception:
            return default
        return max(value, 0)

    @staticmethod
    def _to_float(raw: Any, default: float) -> float:
        try:
            value = float(raw)
        except Exception:
            return default
        return value

    @classmethod
    def _is_enabled(cls) -> bool:
        return cls._env_bool("OPENCODE_MCP_ENABLED", False)

    @classmethod
    def _parse_server_configs(cls) -> list[_MCPServerConfig]:
        configs: list[_MCPServerConfig] = []
        raw_json = os.getenv("OPENCODE_MCP_SERVERS", "").strip()
        if raw_json:
            try:
                payload = json.loads(raw_json)
            except Exception as exc:
                logger.warning("[mcp_registry] parse OPENCODE_MCP_SERVERS failed err=%s", exc)
                payload = []
            if isinstance(payload, list):
                for index, item in enumerate(payload):
                    if not isinstance(item, dict):
                        logger.warning("[mcp_registry] ignore invalid server config index=%s type=%s", index, type(item).__name__)
                        continue
                    server_id = str(item.get("id") or item.get("name") or f"mcp_{index + 1}").strip()
                    url = str(item.get("url") or "").strip()
                    if not server_id or not url:
                        logger.warning("[mcp_registry] ignore invalid server config index=%s reason=missing_id_or_url", index)
                        continue
                    enabled = cls._to_bool(item.get("enabled"), True)
                    if not enabled:
                        continue
                    timeout_seconds = cls._to_float(item.get("timeout_seconds"), cls._default_timeout_seconds)
                    timeout_seconds = timeout_seconds if timeout_seconds > 0 else cls._default_timeout_seconds
                    cache_ttl_seconds = cls._to_int(item.get("cache_ttl_seconds"), cls._default_cache_ttl_seconds)
                    prefix = str(item.get("prefix") or "").strip()
                    priority = cls._to_int(item.get("priority"), 0)
                    expose_unprefixed = cls._to_bool(item.get("expose_unprefixed"), False)
                    configs.append(
                        _MCPServerConfig(
                            server_id=server_id,
                            url=url,
                            enabled=True,
                            timeout_seconds=timeout_seconds,
                            cache_ttl_seconds=cache_ttl_seconds,
                            prefix=prefix,
                            priority=priority,
                            expose_unprefixed=expose_unprefixed,
                        )
                    )
            elif payload:
                logger.warning("[mcp_registry] ignore OPENCODE_MCP_SERVERS reason=not_array type=%s", type(payload).__name__)

        deduped: list[_MCPServerConfig] = []
        used_ids: set[str] = set()
        for config in configs:
            if config.server_id in used_ids:
                logger.warning("[mcp_registry] ignore duplicate server id=%s", config.server_id)
                continue
            used_ids.add(config.server_id)
            deduped.append(config)
        if len(deduped) > 1:
            for config in deduped:
                if not config.prefix:
                    config.prefix = config.server_id
        return deduped

    @classmethod
    def _server_signature(cls, config: _MCPServerConfig) -> tuple[Any, ...]:
        return (
            config.server_id,
            config.url,
            config.timeout_seconds,
            config.cache_ttl_seconds,
            config.prefix,
            config.priority,
            config.expose_unprefixed,
        )

    @classmethod
    def _build_tool_names(cls, config: _MCPServerConfig, source_name: str) -> list[str]:
        if not source_name:
            return []
        primary_name = f"{config.prefix}.{source_name}" if config.prefix else source_name
        names = [primary_name]
        if config.expose_unprefixed and source_name not in names:
            names.append(source_name)
        return names

    @classmethod
    def _build_remote_tool(cls, client: MCPClient, config: _MCPServerConfig, spec: Dict[str, Any], source_name: str, export_name: str) -> Tool | None:
        if not source_name or not export_name:
            return None
        description = str(spec.get("description") or "").strip()
        params = spec.get("input_schema") or spec.get("inputSchema") or {"type": "object", "properties": {}, "required": []}
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}, "required": []}

        async def _handler(payload: Dict[str, Any], context: ToolContext) -> Dict[str, Any]:
            args = payload if isinstance(payload, dict) else {}
            result = await client.call_tool(source_name, args)
            output = stringify_tool_result(result)
            return {
                "title": export_name,
                "output": output,
                "metadata": {
                    "source": "mcp",
                    "server_id": config.server_id,
                    "server_url": config.url,
                    "tool": source_name,
                },
            }

        return Tool(
            name=export_name,
            description=description,
            _executor=_handler,
            metadata={
                "source": "mcp",
                "server_id": config.server_id,
                "server_url": config.url,
                "origin_tool_name": source_name,
                "priority": config.priority,
                "parameters": params,
            },
        )

    @classmethod
    def _load_server_tools(cls, config: _MCPServerConfig) -> Dict[str, Tool]:
        client = MCPClient(MCPClientConfig(url=config.url, timeout_seconds=config.timeout_seconds))
        try:
            specs = run_coro_sync(client.list_tools())
        except Exception as exc:
            logger.warning("[mcp_registry] list_tools failed server=%s url=%s err=%s", config.server_id, config.url, exc)
            return {}
        loaded: Dict[str, Tool] = {}
        for spec in specs or []:
            if not isinstance(spec, dict):
                continue
            source_name = str(spec.get("name") or "").strip()
            if not source_name:
                continue
            for export_name in cls._build_tool_names(config, source_name):
                tool = cls._build_remote_tool(client, config, spec, source_name, export_name)
                if tool:
                    loaded[tool.name] = tool
        logger.info("[mcp_registry] loaded tools server=%s count=%s url=%s", config.server_id, len(loaded), config.url)
        return loaded

    @classmethod
    def _refresh_remote_tools_if_needed(cls) -> None:
        if not cls._is_enabled():
            cls._remote_tools = {}
            cls._remote_server_states = {}
            return
        server_configs = cls._parse_server_configs()
        if not server_configs:
            logger.warning("[mcp_registry] disabled reason=no_server_config env=OPENCODE_MCP_SERVERS")
            cls._remote_tools = {}
            cls._remote_server_states = {}
            return
        active_server_ids = {config.server_id for config in server_configs}
        for server_id in list(cls._remote_server_states.keys()):
            if server_id not in active_server_ids:
                cls._remote_server_states.pop(server_id, None)
        if not mcp_available():
            now = time.time()
            logger.warning("[mcp_registry] disabled reason=mcp_package_unavailable err=%s", mcp_import_error())
            for config in server_configs:
                cls._remote_server_states[config.server_id] = {
                    "signature": cls._server_signature(config),
                    "expires_at": now + config.cache_ttl_seconds,
                    "priority": config.priority,
                    "server_id": config.server_id,
                    "tools": {},
                }
            cls._remote_tools = {}
            return
        for config in server_configs:
            now = time.time()
            cached = cls._remote_server_states.get(config.server_id)
            current_signature = cls._server_signature(config)
            if cached and cached.get("signature") == current_signature and now < float(cached.get("expires_at", 0.0)):
                continue
            loaded_tools = cls._load_server_tools(config)
            cls._remote_server_states[config.server_id] = {
                "signature": current_signature,
                "expires_at": now + config.cache_ttl_seconds,
                "priority": config.priority,
                "server_id": config.server_id,
                "tools": loaded_tools,
            }
        merged: Dict[str, Tool] = {}
        owner: Dict[str, tuple[str, int]] = {}
        for config in sorted(server_configs, key=lambda item: (-item.priority, item.server_id)):
            state = cls._remote_server_states.get(config.server_id) or {}
            tools = state.get("tools") if isinstance(state, dict) else {}
            if not isinstance(tools, dict):
                continue
            for tool_name, tool in tools.items():
                if tool_name in merged:
                    keep_server, keep_priority = owner[tool_name]
                    logger.warning("[mcp_registry] tool conflict name=%s keep_server=%s keep_priority=%s skip_server=%s skip_priority=%s", tool_name, keep_server, keep_priority, config.server_id, config.priority)
                    continue
                merged[tool_name] = tool
                owner[tool_name] = (config.server_id, config.priority)
        cls._remote_tools = merged
        logger.info("[mcp_registry] merged tools total=%s servers=%s", len(merged), len(server_configs))

    @classmethod
    def registry(cls) -> Dict[str, Tool]:
        """返回当前 MCP 工具字典（手工 + 远端发现）。"""
        cls._refresh_remote_tools_if_needed()
        merged: Dict[str, Tool] = {}
        merged.update(cls._manual_tools)
        merged.update(cls._remote_tools)
        return merged

    @classmethod
    def diagnostics(cls) -> Dict[str, Any]:
        """返回 MCP 当前诊断信息（用于启动检查日志）。"""
        enabled = cls._is_enabled()
        configs = cls._parse_server_configs() if enabled else []
        servers: list[Dict[str, Any]] = []
        for config in configs:
            state = cls._remote_server_states.get(config.server_id) or {}
            state_tools = state.get("tools") if isinstance(state, dict) else {}
            tool_count = len(state_tools) if isinstance(state_tools, dict) else 0
            expires_at = float(state.get("expires_at", 0.0)) if isinstance(state, dict) else 0.0
            servers.append(
                {
                    "id": config.server_id,
                    "url": config.url,
                    "tool_count": tool_count,
                    "expires_at": expires_at,
                }
            )
        return {
            "enabled": enabled,
            "server_count": len(configs),
            "total_tools": len(cls._remote_tools),
            "servers": servers,
        }

    @classmethod
    def clear(cls) -> None:
        """清空 MCP 缓存与手工注册。"""
        cls._manual_tools = {}
        cls._remote_tools = {}
        cls._remote_server_states = {}

    @staticmethod
    def tools() -> Dict[str, Tool]:
        """兼容入口：返回 MCP 工具字典。"""
        return MCPToolRegistry.registry()
