"""
LangChain/LangGraph 公共工具模块。

包入口保持轻量，避免导入公共小工具时触发数据库或路由初始化。
"""

import importlib
from typing import Any


_SUBMODULES = {
    "artifact_store",
    "checkpoint",
    "common_util",
    "conversation",
    "conversation_router",
    "database",
    "db_failover",
    "llm",
    "models",
    "schema",
}

_EXPORTS = {
    "get_llm": ("llm", "get_llm"),
    "DB_PATH": ("checkpoint", "DB_PATH"),
    "get_checkpointer": ("checkpoint", "get_checkpointer"),
    "setup_checkpointer": ("checkpoint", "setup_checkpointer"),
    "get_session": ("database", "get_session"),
    "init_database": ("database", "init_database"),
    "get_database_info": ("database", "get_database_info"),
    "UserThread": ("models", "UserThread"),
    "UploadedFile": ("models", "UploadedFile"),
    "get_or_create_conversation": ("conversation", "get_or_create_conversation"),
    "create": ("conversation", "create"),
    "list": ("conversation", "list"),
    "get_detail": ("conversation", "get_detail"),
    "delete": ("conversation", "delete"),
    "update_title": ("conversation", "update_title"),
    "generate_title": ("conversation", "generate_title"),
    "toggle_favorite": ("conversation", "toggle_favorite"),
    "get_agent_id": ("conversation", "get_agent_id"),
    "serialize_messages": ("conversation", "serialize_messages"),
    "router": ("conversation_router", "router"),
    "register_agent": ("conversation_router", "register_agent"),
    "register_completion_handler": ("conversation_router", "register_completion_handler"),
}


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    if name in _EXPORTS:
        module_name, attr_name = _EXPORTS[name]
        module = importlib.import_module(f"{__name__}.{module_name}")
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted([*_SUBMODULES, *_EXPORTS])
