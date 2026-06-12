from __future__ import annotations

from typing import Any

_DISABLE_THINKING_MODELS = {
    "glm-4.7-w8a8",
    "glm-5",
}


def build_thinking_extra_body(model_name: str | None, enable_thinking: bool) -> dict[str, Any] | None:
    """Return model-specific extra_body for thinking mode; None when unsupported."""
    normalized = (model_name or "").strip().lower()
    # Keep explicit disable-thinking handling first to preserve original behavior.
    if normalized in _DISABLE_THINKING_MODELS:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    # Other GLM-4.7 variants also require explicit enable_thinking.
    if "glm-4.7" in normalized:
        return {"chat_template_kwargs": {"enable_thinking": bool(enable_thinking)}}
    if not enable_thinking:
        return None
    # Qwen models use enable_thinking; DeepSeek uses chat_template_kwargs.thinking.
    if normalized == "qwen-plus" or normalized == "deepseek-v3.2":
        return {"enable_thinking": True}
    if normalized == "deepseek-v3_1-terminus":
        return {"chat_template_kwargs": {"thinking": True}}
    return None
