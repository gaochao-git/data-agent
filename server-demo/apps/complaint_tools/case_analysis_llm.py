from __future__ import annotations

import json
from typing import Any

from langchain.chat_models import init_chat_model

from .case_analysis_config import get_case_env


def get_default_model() -> str:
    """
    统一获取用于案例分析的模型名称。
    优先使用 OPENAI_ANALYSIS_MODEL，回退至 gpt-4o-mini。
    """
    return get_case_env("OPENAI_ANALYSIS_MODEL", "gpt-4o-mini") or "gpt-4o-mini"


def get_llm_for_case_analysis(streaming: bool = True):
    """
    初始化用于案例分析 Agent 的 LLM。
    - 使用 init_chat_model 与项目统一的环境变量
      （LLM_PROVIDER / OPENAI_API_KEY / OPENAI_BASE_URL）。
    """
    model_name = get_default_model()
    api_key = get_case_env("OPENAI_API_KEY")
    base_url = get_case_env("OPENAI_BASE_URL")
    model_provider = get_case_env("LLM_PROVIDER", "openai") or "openai"

    return init_chat_model(
        model=model_name,
        model_provider=model_provider,
        api_key=api_key,
        base_url=base_url,
    )


def safe_parse_json_from_llm_output(raw_result: Any, default: Any) -> Any:
    """
    从 LLM 输出中安全解析 JSON 结构。
    - 支持 result.content 为 list / str 等情况
    - 自动去掉 Markdown 代码块包裹
    - 失败时返回传入的 default
    """
    try:
        raw_content = getattr(raw_result, "content", raw_result)
        if isinstance(raw_content, list):
            content_str = "".join(str(part) for part in raw_content)
        else:
            content_str = str(raw_content or "")
        content_str = content_str.strip()

        if content_str.startswith("```json"):
            content_str = content_str[7:]
        elif content_str.startswith("```"):
            content_str = content_str[3:]
        if content_str.endswith("```"):
            content_str = content_str[:-3]
        content_str = content_str.strip()

        return json.loads(content_str) if content_str else default
    except Exception:
        return default


__all__ = [
    "get_default_model",
    "get_llm_for_case_analysis",
    "safe_parse_json_from_llm_output",
]

