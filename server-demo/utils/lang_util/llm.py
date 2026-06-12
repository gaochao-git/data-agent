"""
LLM 公共工具函数
"""

import os
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


def count_tokens(llm: BaseChatModel, messages: list[BaseMessage], tools: list | None = None) -> int:
    """
    统计消息的 token 数量（粗略估算，避免内网环境下 tiktoken 网络请求）

    Args:
        llm: LLM 实例（未使用，保留参数兼容性）
        messages: 消息列表
        tools: 工具列表（可选）

    Returns:
        token 数量（估算值，约 4 字符 = 1 token）
    """
    num_tokens = 0
    for m in messages:
        num_tokens += 4  # 消息结构开销
        num_tokens += len(str(m.content or "")) // 4
    if tools:
        num_tokens += len(tools) * 50  # 每个工具约 50 tokens
    return num_tokens


def get_llm(
    model: str | None = None,
    streaming: bool = False,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: int | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> BaseChatModel:
    """
    获取 LLM 实例

    Args:
        model: 模型名称，默认从环境变量 OPENAI_ANALYSIS_MODEL 读取
        streaming: 是否启用流式输出
        temperature: 温度参数，默认 0.7
        max_tokens: 最大输出 token 数，默认不限制
        timeout: 超时时间（秒），默认不限制
        api_key: API Key，默认读取 OPENAI_API_KEY
        base_url: API Base URL，默认读取 OPENAI_BASE_URL

    Returns:
        LLM 实例
    """
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

    return init_chat_model(**kwargs)
