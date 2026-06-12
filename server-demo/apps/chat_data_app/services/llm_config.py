"""智能问数 LLM 配置。"""

import os

from langchain_core.language_models import BaseChatModel

from utils.lang_util.llm import get_llm


def get_chat_data_llm(
    model: str | None = None,
    streaming: bool = False,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> BaseChatModel:
    """读取 CHAT_DATA_* 环境变量创建智能问数专用 LLM。"""
    return get_llm(
        model=model or os.getenv("CHAT_DATA_MODEL"),
        streaming=streaming,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        api_key=os.getenv("CHAT_DATA_API_KEY"),
        base_url=os.getenv("CHAT_DATA_BASE_URL"),
    )
