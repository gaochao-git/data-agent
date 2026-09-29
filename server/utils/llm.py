"""
LLM 公共工具函数
"""

import os
from typing import Any, Mapping

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk

_THIRD_PARTY_REASONING_KEYS = ("reasoning_content", "reasoning", "reasoning_text")
_REASONING_PATCHED = False


def _patch_langchain_openai_reasoning_fields() -> None:
    """Preserve third-party OpenAI-compatible reasoning fields in streamed chunks."""
    global _REASONING_PATCHED
    if _REASONING_PATCHED:
        return
    try:
        import langchain_openai.chat_models.base as openai_chat_base
    except Exception:
        _REASONING_PATCHED = True
        return

    original = openai_chat_base._convert_delta_to_message_chunk

    def patched_convert_delta_to_message_chunk(
        delta: Mapping[str, Any],
        default_class: type,
    ):
        chunk = original(delta, default_class)
        reasoning_payload = {
            key: delta.get(key)
            for key in _THIRD_PARTY_REASONING_KEYS
            if isinstance(delta.get(key), str) and delta.get(key)
        }
        if reasoning_payload and isinstance(chunk, AIMessageChunk):
            chunk.additional_kwargs = {
                **(chunk.additional_kwargs or {}),
                **reasoning_payload,
            }
        return chunk

    openai_chat_base._convert_delta_to_message_chunk = patched_convert_delta_to_message_chunk
    _REASONING_PATCHED = True


def get_llm(
    model: str | None = None,
    streaming: bool = False,
    stream_usage: bool | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: int | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    extra_body: dict | None = None,
) -> BaseChatModel:
    """
    获取 LLM 实例

    Args:
        model: 模型名称，默认从环境变量 OPENAI_ANALYSIS_MODEL 读取
        streaming: 是否启用流式输出
        stream_usage: 流式输出时是否请求 token usage
        temperature: 温度参数，默认 0.7
        max_tokens: 最大输出 token 数，默认不限制
        timeout: 超时时间（秒），默认不限制
        api_key: API Key，默认读取 OPENAI_API_KEY
        base_url: API Base URL，默认读取 OPENAI_BASE_URL
        extra_body: OpenAI compatible 额外请求体

    Returns:
        LLM 实例
    """
    _patch_langchain_openai_reasoning_fields()

    kwargs = {
        "model": model or os.getenv("OPENAI_ANALYSIS_MODEL", "gpt-4o-mini"),
        "model_provider": os.getenv("LLM_PROVIDER", "openai"),
        "temperature": temperature,
        "api_key": api_key or os.getenv("OPENAI_API_KEY"),
        "base_url": base_url or os.getenv("OPENAI_BASE_URL"),
        "streaming": streaming,
    }

    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    if timeout:
        kwargs["timeout"] = timeout

    if stream_usage is not None:
        kwargs["stream_usage"] = stream_usage

    if extra_body:
        kwargs["extra_body"] = extra_body

    return init_chat_model(**kwargs)
