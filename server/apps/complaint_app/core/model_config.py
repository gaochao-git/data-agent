from __future__ import annotations

import os

# Centralized model/temperature configuration for complaint services.
# Update here to propagate model switches across the module.


def _env(key: str) -> str | None:
    val = os.getenv(key)
    return val.strip() if isinstance(val, str) else None


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    stripped = val.strip().lower()
    if not stripped:
        return default
    return stripped in {"1", "true", "yes", "y", "on"}


def get_analysis_model(default: str = "Qwen3-30B-A3B-Instruct-2507") -> str:
    """Shared通用分析模型（用于提示规划、标题等）。"""
    return _env("OPENAI_ANALYSIS_MODEL") or default


def get_response_model(default: str = "Qwen3-30B-A3B-Instruct-2507") -> str:
    """生成摘要/聊天回答使用的响应模型，优先专用配置。"""
    return _env("COMPLAINT_RESPONSE_LLM_MODEL") or get_analysis_model(default) or default


def get_thought_model() -> str | None:
    """聊天思考模型，可为空以回退到响应模型。"""
    return _env("COMPLAINT_THOUGHT_LLM_MODEL")


def get_chat_model(default: str = "Qwen3-30B-A3B-Instruct-2507") -> str:
    """聊天对话总模型，按思考→响应→分析→默认的优先级。"""
    return get_thought_model() or _env("COMPLAINT_RESPONSE_LLM_MODEL") or get_analysis_model() or default


def get_summary_model(default: str = "Qwen3-30B-A3B-Instruct-2507") -> str:
    """摘要生成模型（结构化摘要）。"""
    return _env("COMPLAINT_SUMMARY_MODEL") or _env("COMPLAINT_RESPONSE_LLM_MODEL") or get_analysis_model(default) or default


def get_title_model(default: str = "Qwen3-30B-A3B-Instruct-2507") -> str:
    """会话标题生成模型。"""
    return get_analysis_model(default)


def get_ocr_llm_model(default: str = "qwen3-vl-plus") -> str:
    """视觉 OCR 模型。"""
    return _env("OCR_LLM_MODEL") or default


def get_tool_planner_model() -> str:
    """原文提取前置规划提示所用模型。"""
    return get_analysis_model()


def get_tool_summary_model() -> str:
    """原文提取后简报提示所用模型。"""
    return get_analysis_model()


def get_complaint_extract_items_model(default: str = "qwen-plus") -> str:
    return _env("COMPLAINT_EXTRACT_ITEMS_MODEL") or default


def get_complaint_match_experience_model(default: str = "deepseek-v3.2") -> str:
    return _env("COMPLAINT_MATCH_EXPERIENCE_MODEL") or default


def get_complaint_law_analysis_model(default: str = "qwen-plus") -> str:
    return _env("COMPLAINT_LAW_ANALYSIS_MODEL") or default


def get_complaint_final_conclusion_model(default: str = "deepseek-v3.2") -> str:
    return _env("COMPLAINT_FINAL_CONCLUSION_MODEL") or default


def get_skill_model(default: str = "Qwen3-30B-A3B-Instruct-2507") -> str:
    """技能执行模型（需支持工具调用）。"""
    return _env("COMPLAINT_SKILL_MODEL") or get_chat_model(default)


def get_complaint_match_experience_thinking(default: bool = True) -> bool:
    return _env_bool("COMPLAINT_MATCH_EXPERIENCE_THINKING", default)


def get_complaint_final_conclusion_thinking(default: bool = True) -> bool:
    return _env_bool("COMPLAINT_FINAL_CONCLUSION_THINKING", default)


def get_complaint_match_strategy_default(default: str = "two_stage") -> str:
    value = (_env("COMPLAINT_MATCH_STRATEGY_DEFAULT") or "").strip().lower()
    if value in {"two_stage", "legacy"}:
        return value
    return default


def get_tool_planner_temperature(default: float = 0.2) -> float:
    try:
        return float(_env("TOOL_PLANNER_TEMPERATURE") or default)
    except Exception:
        return default


def get_tool_summary_temperature(default: float = 0.1) -> float:
    try:
        return float(_env("TOOL_SUMMARY_TEMPERATURE") or default)
    except Exception:
        return default


__all__ = [
    "get_analysis_model",
    "get_response_model",
    "get_thought_model",
    "get_chat_model",
    "get_summary_model",
    "get_title_model",
    "get_ocr_llm_model",
    "get_tool_planner_model",
    "get_tool_summary_model",
    "get_tool_planner_temperature",
    "get_tool_summary_temperature",
    "get_complaint_extract_items_model",
    "get_complaint_match_experience_model",
    "get_complaint_law_analysis_model",
    "get_complaint_final_conclusion_model",
    "get_skill_model",
    "get_complaint_match_experience_thinking",
    "get_complaint_final_conclusion_thinking",
    "get_complaint_match_strategy_default",
]
