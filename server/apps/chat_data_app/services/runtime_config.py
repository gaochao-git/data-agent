"""ChatData runtime configuration helpers."""

from __future__ import annotations

import os

from utils.logger import get_logger

logger = get_logger(__name__)


def read_positive_int_env(name: str, default: int) -> int:
    """读取正整数环境变量，非法时回退默认值。"""
    raw_value = str(os.getenv(name) or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("环境变量 %s=%r 不是合法整数，回退默认值 %s", name, raw_value, default)
        return default
    if value < 1:
        logger.warning("环境变量 %s=%s 小于 1，回退默认值 %s", name, value, default)
        return default
    return value


def read_bool_env(name: str, default: bool = False) -> bool:
    """读取布尔环境变量，非法或未配置时回退默认值。"""
    raw_value = str(os.getenv(name) or "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "y", "on"}


DEFAULT_ROW_LIMIT = read_positive_int_env("CHATDATA_ROW_LIMIT", 100)
TOOL_RESULT_OFFLOAD_SIZE = read_positive_int_env("CHATDATA_TOOL_RESULT_OFFLOAD_SIZE", 32000)
QUERY_CACHE_ENABLED = read_bool_env("CHAT_DATA_QUERY_CACHE_ENABLED", False)
QUERY_CACHE_TTL_SECONDS = read_positive_int_env("CHAT_DATA_QUERY_CACHE_TTL_SECONDS", 3600)
DEFAULT_CONTEXT_WINDOW = 128000
CONTEXT_COMPRESSION_ENABLED = read_bool_env("CHATDATA_CONTEXT_COMPRESSION_ENABLED", True)
CONTEXT_COMPRESSION_TRIGGER_RATIO = 0.70
CONTEXT_USAGE_SHOW_SYSTEM_PROMPT = read_bool_env("CHATDATA_CONTEXT_USAGE_SHOW_SYSTEM_PROMPT", False)
LLM_FIRST_TOKEN_TIMEOUT_SECONDS = read_positive_int_env("CHATDATA_LLM_FIRST_TOKEN_TIMEOUT_SECONDS", 60)


def get_context_window(model_id: str | None) -> int:
    """按模型名返回上下文窗口大小。"""
    from .llm_config import get_chat_data_model_config

    return int(get_chat_data_model_config(model_id).get("context_window") or DEFAULT_CONTEXT_WINDOW)


def get_context_compression_config() -> dict:
    """读取智能问数上下文压缩配置。"""
    return {
        "enabled": CONTEXT_COMPRESSION_ENABLED,
        "trigger_ratio": CONTEXT_COMPRESSION_TRIGGER_RATIO,
    }


def is_context_usage_system_prompt_visible() -> bool:
    """是否在上下文预算明细中展示 system prompt 原文。"""
    return CONTEXT_USAGE_SHOW_SYSTEM_PROMPT


def get_chat_data_runtime_config() -> dict:
    """读取智能问数前端运行时配置。"""
    from .llm_config import get_chat_data_model_options
    from .feature_gate import is_data_analyze_enabled

    model_options = get_chat_data_model_options()
    default_model = model_options.get("default_model")
    return {
        "models": model_options["models"],
        "data_analyze_enabled": is_data_analyze_enabled(),
        "default_row_limit": DEFAULT_ROW_LIMIT,
        "query_cache_enabled": QUERY_CACHE_ENABLED,
        "context_window": get_context_window(default_model),
    }


def normalize_row_limit(limit: int | None) -> int:
    """读取请求行数限制，未传时使用全局配置。"""
    if limit is None:
        return DEFAULT_ROW_LIMIT
    if not isinstance(limit, int) or limit < 1:
        raise ValueError(f"查询结果行数限制必须为正整数，当前值：{limit}")
    if limit > DEFAULT_ROW_LIMIT:
        raise ValueError(f"查询结果最多返回 {DEFAULT_ROW_LIMIT} 条，请调整查询条件")
    return limit
