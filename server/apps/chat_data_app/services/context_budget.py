"""模型请求上下文预算与统一 token 估算。"""

from __future__ import annotations

import json
import copy
import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from .llm_config import get_chat_data_max_tokens
from .runtime_config import get_context_edit_config, get_context_window


REQUEST_OVERHEAD_TOKENS = 3
SYSTEM_PROMPT_PLACEHOLDER = "[系统提示词已隐藏]"
SYSTEM_PROMPT_REDACTION_ENABLED = True


def estimate_tokens(text: str) -> int:
    """按项目统一规则估算文本 token 数。"""
    normalized = str(text or "")
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    en_words = len(re.findall(r"[a-zA-Z0-9]+", re.sub(r"[\u4e00-\u9fff]", " ", normalized)))
    other_chars = len(re.findall(r"[^\w\s\u4e00-\u9fff]", normalized))
    return int(zh_chars * 0.7 + en_words * 1.3 + other_chars * 0.5)


def count_messages_tokens(messages: list[BaseMessage]) -> int:
    """估算 LangChain messages 的 token 数。"""
    return sum(_message_tokens(message) for message in messages or [])


def estimate_text_tokens(text: str) -> int:
    """估算普通文本 token 数。"""
    if not text:
        return 0
    return count_messages_tokens([HumanMessage(content=text)])


def count_tools_tokens(tools: list[Any] | None) -> int:
    """估算工具 schema 的 token 数。"""
    total = 0
    for tool in tools or []:
        try:
            payload = tool if isinstance(tool, dict) else convert_to_openai_tool(tool)
        except Exception:
            payload = str(tool)
        total += estimate_tokens(json.dumps(payload, ensure_ascii=False, default=str))
    return int(total)


def _message_tokens(message: BaseMessage) -> int:
    total = 4
    content = getattr(message, "content", None)
    if content:
        if isinstance(content, str):
            total += estimate_tokens(content)
        else:
            total += estimate_tokens(json.dumps(content, ensure_ascii=False, default=str))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False, default=str))
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if raw_tool_calls:
        total += estimate_tokens(json.dumps(raw_tool_calls, ensure_ascii=False, default=str))
    return int(total)


@dataclass(frozen=True)
class ContextBudget:
    """单次模型请求的统一输入预算。"""

    context_window: int
    max_output_tokens: int
    trigger_ratio: float
    input_budget_tokens: int

    def remaining_for_messages(self, static_input_tokens: int) -> int:
        """扣除 system、工具定义和协议开销后，留给消息的预算。"""
        return max(self.input_budget_tokens - max(int(static_input_tokens), 0), 1)

    def remaining_for_tool_result(self, static_input_tokens: int, message_tokens: int) -> int:
        """扣除下一次请求已知输入后，留给当前工具结果的预算。"""
        return max(
            self.input_budget_tokens - max(int(static_input_tokens), 0) - max(int(message_tokens), 0),
            0,
        )


def get_model_context_budget(model_id: str | None, *, max_output_tokens: int | None = None) -> ContextBudget:
    """按模型窗口、输出预留和全局触发比例构造唯一输入预算。"""
    context_window = max(int(get_context_window(model_id)), 1)
    requested_output = max(int(max_output_tokens or get_chat_data_max_tokens()), 1)
    reserved_output = min(requested_output, max(context_window - 1, 0))
    hard_input_limit = max(context_window - reserved_output, 1)
    trigger_ratio = float(get_context_edit_config().get("trigger_ratio", 0.70))
    soft_input_limit = max(int(context_window * trigger_ratio), 1)
    return ContextBudget(
        context_window=context_window,
        max_output_tokens=requested_output,
        trigger_ratio=trigger_ratio,
        input_budget_tokens=min(soft_input_limit, hard_input_limit),
    )


def build_context_usage_payload(budget: ContextBudget, used: int) -> dict[str, Any]:
    """构造页面和 SSE 共用的输入预算占用数据。"""
    total = max(int(budget.input_budget_tokens), 1)
    safe_used = max(int(used), 0)
    return {
        "used": safe_used,
        "total": total,
        "percentage": round((safe_used / total) * 100, 1),
        "remaining": max(total - safe_used, 0),
        "context_window": budget.context_window,
        "max_output_tokens": budget.max_output_tokens,
        "input_budget_tokens": total,
        "input_budget_trigger_ratio": budget.trigger_ratio,
    }


def sanitize_context_usage_for_response(usage: Any) -> Any:
    """返回给前端前隐藏 system prompt 明文，不改变原始 usage 对象。"""
    if not SYSTEM_PROMPT_REDACTION_ENABLED or not isinstance(usage, dict):
        return usage
    sanitized = copy.deepcopy(usage)
    _sanitize_breakdown(sanitized.get("breakdown"))
    llm_calls = sanitized.get("llm_calls")
    if isinstance(llm_calls, list):
        for call in llm_calls:
            if isinstance(call, dict):
                _sanitize_breakdown(call.get("breakdown"))
    return sanitized


def _sanitize_breakdown(items: Any) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        is_system_item = item.get("key") == "system_prompt" or item.get("role") == "system"
        if is_system_item:
            item["content"] = SYSTEM_PROMPT_PLACEHOLDER
            item["description"] = SYSTEM_PROMPT_PLACEHOLDER
            item["redacted"] = True
        _sanitize_breakdown(item.get("children"))


class ToolResultBudget:
    """同一次 Agent 执行中供工具结果使用的共享预算。"""

    def __init__(self, model_id: str | None, *, max_output_tokens: int | None = None):
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._budget = get_model_context_budget(model_id, max_output_tokens=max_output_tokens)
        self._static_input_tokens = REQUEST_OVERHEAD_TOKENS

    def configure(
        self,
        system_prompt: str,
        tools: list[Any],
        *,
        model_id: str | None = None,
    ) -> ContextBudget:
        """记录当前 Agent 固定输入部分，供工具在返回前计算剩余空间。"""
        effective_model_id = self._model_id if model_id is None else model_id
        self._budget = get_model_context_budget(effective_model_id, max_output_tokens=self._max_output_tokens)
        self._static_input_tokens = (
            count_messages_tokens([SystemMessage(content=system_prompt)])
            + count_tools_tokens(tools)
            + REQUEST_OVERHEAD_TOKENS
        )
        return self._budget

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    @property
    def static_input_tokens(self) -> int:
        return self._static_input_tokens

    def remaining_for_messages(self) -> int:
        return self._budget.remaining_for_messages(self._static_input_tokens)

    def remaining_for_tool_result(self, messages: list[BaseMessage]) -> int:
        return self._budget.remaining_for_tool_result(
            self._static_input_tokens,
            count_messages_tokens(messages),
        )
