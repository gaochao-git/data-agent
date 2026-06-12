from __future__ import annotations

import os
from typing import Any

from utils.llm_thinking import build_thinking_extra_body


def _env_model_patterns(key: str) -> list[str]:
    raw = str(os.getenv(key) or "").strip()
    if not raw:
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def _matches_model(model_name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern == model_name:
            return True
        if pattern.endswith("*") and model_name.startswith(pattern[:-1]):
            return True
    return False


def _thinking_extra_body_for_model(model_name: str, enable_thinking: bool) -> dict[str, Any]:
    if "glm-4.7" in model_name or model_name in {"glm-4.7-w8a8", "glm-5"}:
        return {"chat_template_kwargs": {"enable_thinking": bool(enable_thinking)}}
    if model_name == "deepseek-v3_1-terminus":
        return {"chat_template_kwargs": {"thinking": bool(enable_thinking)}}
    return {"enable_thinking": bool(enable_thinking)}


def build_analysis_v2_thinking_extra_body(model_name: str | None, enable_thinking: bool = False) -> dict[str, Any] | None:
    """按 complaint-analysis-v2 专用环境变量生成思考开关参数。"""
    normalized = str(model_name or "").strip().lower()
    disabled_models = _env_model_patterns("COMPLAINT_ANALYSIS_V2_THINKING_DISABLED_MODELS")
    enabled_models = _env_model_patterns("COMPLAINT_ANALYSIS_V2_THINKING_ENABLED_MODELS")
    if _matches_model(normalized, disabled_models):
        return _thinking_extra_body_for_model(normalized, False)
    if _matches_model(normalized, enabled_models):
        return _thinking_extra_body_for_model(normalized, True)
    return build_thinking_extra_body(model_name, enable_thinking)
