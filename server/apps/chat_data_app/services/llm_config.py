"""智能问数 LLM 配置。"""

import os
from typing import Any

from langchain_core.language_models import BaseChatModel

from utils.crypto import decrypt_password
from utils.lang_util.llm import get_llm


DEFAULT_CHAT_DATA_MAX_TOKENS = 20000
DEFAULT_CHAT_DATA_MODEL = "gpt-4o-mini"
DEFAULT_CHAT_DATA_TEMPERATURE = 0.2


def _get_default_max_tokens() -> int:
    """读取智能问数默认最大输出 token 数。"""
    raw = str(os.getenv("CHAT_DATA_MAX_TOKENS") or "").strip()
    if not raw:
        return DEFAULT_CHAT_DATA_MAX_TOKENS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CHAT_DATA_MAX_TOKENS
    return value if value > 0 else DEFAULT_CHAT_DATA_MAX_TOKENS


def get_chat_data_temperature() -> float:
    """读取智能问数和智能分析默认模型温度。"""
    raw = str(os.getenv("CHAT_DATA_TEMPERATURE") or "").strip()
    if not raw:
        return DEFAULT_CHAT_DATA_TEMPERATURE
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CHAT_DATA_TEMPERATURE
    return value if 0 <= value <= 2 else DEFAULT_CHAT_DATA_TEMPERATURE


def _split_model_names(raw_value: str | None) -> list[str]:
    """按逗号拆分模型配置并去重。"""
    models = []
    seen = set()
    for item in str(raw_value or "").split(","):
        model = item.strip().strip("\"'")
        if not model or model in seen:
            continue
        seen.add(model)
        models.append(model)
    return models


def get_chat_data_model_names() -> list[str]:
    """读取智能问数模型列表。"""
    default_models = _split_model_names(os.getenv("CHAT_DATA_MODEL"))
    models = [*default_models]
    for model in _split_model_names(os.getenv("CHAT_DATA_MODELS")):
        if model not in models:
            models.append(model)
    return models or [DEFAULT_CHAT_DATA_MODEL]


def get_default_chat_data_model() -> str:
    """读取智能问数默认模型。"""
    return get_chat_data_model_names()[0]


def _get_chat_data_extra_body(enable_thinking: bool | None = None) -> dict[str, Any]:
    thinking_enabled = bool(enable_thinking)
    return {
        "enable_thinking": thinking_enabled,
        "chat_template_kwargs": {
            "enable_thinking": thinking_enabled,
        },
    }


def _get_chat_data_api_key() -> str | None:
    """读取智能问数 API Key，CHAT_DATA_API_KEY 存储为 SM4 密文。"""
    encrypted_api_key = str(os.getenv("CHAT_DATA_API_KEY") or "").strip()
    if not encrypted_api_key:
        return None
    return decrypt_password(encrypted_api_key)


def get_chat_data_model_options() -> dict[str, Any]:
    """读取智能问数前端可选模型列表。"""
    raw_models = get_chat_data_model_names()
    default_model = raw_models[0]
    models = []
    for model in raw_models:
        models.append({
            "id": model,
            "label": model,
            "value": model,
            "is_default": model == default_model,
        })
    return {"models": models, "default_model": default_model}


def get_chat_data_llm(
    model: str | None = None,
    streaming: bool = False,
    stream_usage: bool | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int | None = None,
    enable_thinking: bool | None = None,
) -> BaseChatModel:
    """读取 CHAT_DATA_* 环境变量创建智能问数专用 LLM。"""
    selected_model = model or get_default_chat_data_model()
    return get_llm(
        model=selected_model,
        streaming=streaming,
        stream_usage=stream_usage,
        temperature=get_chat_data_temperature() if temperature is None else temperature,
        max_tokens=max_tokens or _get_default_max_tokens(),
        timeout=timeout,
        api_key=_get_chat_data_api_key(),
        base_url=os.getenv("CHAT_DATA_BASE_URL"),
        extra_body=_get_chat_data_extra_body(enable_thinking=enable_thinking),
    )
