from __future__ import annotations

import importlib
from pathlib import Path
from typing import Dict, List

from .base_tool import Tool
from .mcp.registry import MCPToolRegistry
from utils.logger import get_logger

logger = get_logger(__name__)


class ToolRegistry:
    """Registry that combines built-in and MCP tools."""

    _builtin_cache: Dict[str, Tool] | None = None
    _mcp_startup_checked = False

    @classmethod
    def builtins(cls) -> Dict[str, Tool]:
        """扫描 builtin 目录并调用各工具的 registry() 方法。"""
        if cls._builtin_cache is not None:
            return dict(cls._builtin_cache)
        tools: Dict[str, Tool] = {}
        root = Path(__file__).resolve().parent / "builtin"
        if not root.exists():
            return tools
        for path in sorted(root.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                module_name = f"{__package__}.builtin.{path.stem}"
                module = importlib.import_module(module_name)
            except Exception as e:
                logger.error("[tool_registry] import builtin module failed module=%s err=%s", f"{__package__}.builtin.{path.stem}", e)
                continue
            builder = getattr(module, "registry", None)
            if not callable(builder):
                continue
            try:
                tool = builder()
            except Exception as e:
                logger.error("[tool_registry] build builtin tool failed module=%s err=%s", path.stem, e)
                continue
            if isinstance(tool, Tool):
                tools[tool.name] = tool
        cls._builtin_cache = dict(tools)
        return dict(tools)

    @classmethod
    def registry(cls) -> List[Tool]:
        """合并内置工具与 MCP 工具，返回工具列表。"""
        merged: Dict[str, Tool] = {}
        merged.update(cls.builtins())
        mcp_tools = MCPToolRegistry.registry()
        merged.update(mcp_tools)
        cls._log_mcp_startup_check_once()
        return list(merged.values())

    @classmethod
    def _log_mcp_startup_check_once(cls) -> None:
        if cls._mcp_startup_checked:
            return
        cls._mcp_startup_checked = True
        diag = MCPToolRegistry.diagnostics()
        enabled = bool(diag.get("enabled"))
        if not enabled:
            logger.info("[tool_registry] mcp startup check skipped enabled=false")
            return
        server_count = int(diag.get("server_count") or 0)
        if server_count <= 0:
            logger.warning("[tool_registry] mcp startup check failed reason=no_server_config")
            return
        total_tools = int(diag.get("total_tools") or 0)
        servers = diag.get("servers") if isinstance(diag.get("servers"), list) else []
        summary = ",".join([f'{item.get("id")}:{item.get("tool_count")}' for item in servers if isinstance(item, dict)])
        if total_tools <= 0:
            logger.warning("[tool_registry] mcp startup check failed reason=no_tools servers=%s", summary)
            return
        logger.info("[tool_registry] mcp startup check ok servers=%s total_tools=%s detail=%s", server_count, total_tools, summary)

    @classmethod
    def ids(cls) -> List[str]:
        """返回当前可用工具的名称列表。"""
        return [tool.name for tool in cls.registry()]
