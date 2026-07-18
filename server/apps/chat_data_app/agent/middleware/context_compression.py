"""Context compression middleware for persisted agent state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import before_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from utils.logger import get_logger
from ...services.context_budget import ToolResultBudget, count_messages_tokens
from ...services.llm_config import get_chat_data_llm
from ...services.runtime_config import get_context_compression_config

logger = get_logger(__name__)

SUMMARY_INPUT_MAX_CHARS = 60000
SUMMARY_MESSAGE_MAX_CHARS = 4000
SUMMARY_MAX_TOKENS = 2000


@dataclass(frozen=True)
class ContextCompressionConfig:
    enabled: bool
    trigger_ratio: float


async def compress_messages_for_state(
    messages: list[BaseMessage],
    *,
    context_window: int,
    message_budget_tokens: int,
    config: dict[str, Any] | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
) -> tuple[list[BaseMessage], dict[str, Any]]:
    """压缩已完成旧历史，只保留摘要和当前正在处理的一轮。"""
    cfg = _config_from_mapping(config)
    original_messages = list(messages or [])
    before_tokens = count_messages_tokens(original_messages)
    threshold_tokens = max(int(message_budget_tokens or int(max(context_window, 1) * cfg.trigger_ratio)), 1)
    report = _build_report(
        cfg,
        before_tokens=before_tokens,
        after_tokens=before_tokens,
        threshold_tokens=threshold_tokens,
    )
    if not cfg.enabled or before_tokens <= threshold_tokens:
        return original_messages, report

    compressible_end = _compressible_end_index(original_messages)
    if compressible_end <= 0:
        report["compression_skipped_reason"] = "no_completed_old_rounds"
        return original_messages, report

    summary_text = await (summarizer or _summarize_messages_with_llm)(original_messages[:compressible_end])
    if not summary_text:
        report["compression_skipped_reason"] = "empty_summary"
        return original_messages, report

    summary_message = _build_summary_message(summary_text, original_message_count=compressible_end)
    compressed_messages = [summary_message, *original_messages[compressible_end:]]
    after_tokens = count_messages_tokens(compressed_messages)
    report.update({
        "triggered": True,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
        "compressed_messages": compressible_end,
        "summary_chars": len(str(summary_message.content or "")),
    })
    logger.info(
        "[agent] 上下文压缩已执行: before_tokens=%s after_tokens=%s threshold_tokens=%s compressed_messages=%s",
        before_tokens,
        after_tokens,
        threshold_tokens,
        compressible_end,
    )
    return compressed_messages, report


async def build_context_compression_update(
    messages: list[BaseMessage],
    *,
    context_window: int,
    message_budget_tokens: int,
    config: dict[str, Any] | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
) -> tuple[list[BaseMessage] | None, dict[str, Any]]:
    """构造可写入 LangGraph state 的压缩消息更新。"""
    compressed_messages, report = await compress_messages_for_state(
        messages,
        context_window=context_window,
        message_budget_tokens=message_budget_tokens,
        config=config,
        summarizer=summarizer,
    )
    if not report.get("triggered"):
        return None, report
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *compressed_messages], report


def create_context_compression_middleware(
    *,
    system_prompt: str,
    tools: list[Any] | None = None,
    model_id: str | None = None,
    tool_result_budget: ToolResultBudget | None = None,
    state_schema: Any | None = None,
) -> Any:
    """创建模型调用前的状态级上下文压缩 middleware。"""
    compression_budget = tool_result_budget or ToolResultBudget(model_id)
    request_tools = list(tools or [])

    @before_model(name="ContextCompressionMiddleware", state_schema=state_schema)
    async def context_compression_middleware(state, _runtime):
        messages = list((state or {}).get("messages") or [])
        budget = compression_budget.configure(system_prompt, request_tools, model_id=model_id)
        message_budget_tokens = compression_budget.remaining_for_messages()
        message_updates, report = await build_context_compression_update(
            messages,
            context_window=budget.context_window,
            message_budget_tokens=message_budget_tokens,
            config=get_context_compression_config(),
            summarizer=lambda compressible_messages: _summarize_messages_with_llm(
                compressible_messages,
                model_id=model_id,
            ),
        )
        if not report.get("triggered") or not message_updates:
            return {"context_compression": report}
        return {
            "messages": message_updates,
            "context_compression": report,
        }

    return context_compression_middleware


def _config_from_mapping(config: dict[str, Any] | None) -> ContextCompressionConfig:
    data = config or {}
    return ContextCompressionConfig(
        enabled=bool(data.get("enabled", True)),
        trigger_ratio=float(data.get("trigger_ratio", 0.70)),
    )


def _build_report(
    cfg: ContextCompressionConfig,
    *,
    before_tokens: int,
    after_tokens: int,
    threshold_tokens: int,
) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "triggered": False,
        "estimated_tokens_before": before_tokens,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
        "threshold_tokens": threshold_tokens,
        "trigger_ratio": cfg.trigger_ratio,
        "compressed_messages": 0,
        "summary_chars": 0,
        "compression_skipped_reason": "",
    }


def _compressible_end_index(messages: list[BaseMessage]) -> int:
    current_round_start = _last_human_index(messages)
    search_end = current_round_start if current_round_start < len(messages) else len(messages)
    if search_end <= 0:
        return 0
    return _last_complete_boundary(messages[:search_end])


def _last_human_index(messages: list[BaseMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return index
    return len(messages)


def _last_complete_boundary(messages: list[BaseMessage]) -> int:
    boundary = 0
    pending_tool_calls: set[str] = set()
    for index, message in enumerate(messages):
        if isinstance(message, AIMessage):
            for tool_call in getattr(message, "tool_calls", None) or []:
                call_id = str((tool_call or {}).get("id") or "").strip()
                if call_id:
                    pending_tool_calls.add(call_id)
        elif isinstance(message, ToolMessage):
            tool_call_id = str(getattr(message, "tool_call_id", "") or "").strip()
            if tool_call_id:
                pending_tool_calls.discard(tool_call_id)
        if isinstance(message, AIMessage) and not pending_tool_calls:
            boundary = index + 1
    return boundary


def _build_summary_message(summary_text: str, *, original_message_count: int) -> SystemMessage:
    content = "\n".join([
        "[历史对话摘要]",
        "以下为较早已完成对话轮次的压缩摘要，不是用户本轮新问题。",
        str(summary_text or "").strip(),
    ])
    return SystemMessage(
        content=content,
        additional_kwargs={
            "context_compression": True,
            "compressed_message_count": original_message_count,
        },
    )


async def _summarize_messages_with_llm(messages: list[BaseMessage], *, model_id: str | None = None) -> str:
    prompt = _build_summary_prompt(messages)
    llm = get_chat_data_llm(
        model=model_id,
        streaming=False,
        max_tokens=SUMMARY_MAX_TOKENS,
        enable_thinking=False,
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") if isinstance(item, dict) else item) for item in content)
    return str(content or "").strip()


def _build_summary_prompt(messages: list[BaseMessage]) -> str:
    transcript = _format_messages_for_summary(messages)
    return (
        "请将以下历史对话压缩成一份中文摘要，用于后续继续对话。\n"
        "要求：保留用户真实目标、已确认的数据源/指标集/执行对象、关键筛选条件、SQL/DSL、工具结果结论、未完成事项和需要继续遵守的约束；"
        "不要编造不存在的数据、指标或结论；不需要逐字复述。\n\n"
        f"{transcript}"
    )


def _format_messages_for_summary(messages: list[BaseMessage]) -> str:
    lines: list[str] = []
    total_chars = 0
    for index, message in enumerate(messages, start=1):
        text = f"{index}. {_message_role(message)}: {_message_content_for_summary(message)}"
        remaining_chars = SUMMARY_INPUT_MAX_CHARS - total_chars
        if remaining_chars <= 0:
            break
        if len(text) > remaining_chars:
            lines.append(text[:remaining_chars])
            break
        lines.append(text)
        total_chars += len(text)
    return "\n".join(lines)


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "用户"
    if isinstance(message, AIMessage):
        return "助手"
    if isinstance(message, ToolMessage):
        return f"工具({getattr(message, 'name', None) or '-'})"
    return str(getattr(message, "type", "") or "消息")


def _message_brief(message: BaseMessage) -> str:
    return _message_content_for_summary(message)[:180]


def _message_content_for_summary(message: BaseMessage) -> str:
    content = str(getattr(message, "content", "") or "").replace("\r", " ").replace("\n", " ").strip()
    if not content and isinstance(message, AIMessage):
        tool_calls = getattr(message, "tool_calls", None) or []
        if tool_calls:
            return "调用工具 " + json.dumps(tool_calls, ensure_ascii=False, default=str)[:SUMMARY_MESSAGE_MAX_CHARS]
    return content[:SUMMARY_MESSAGE_MAX_CHARS] if content else "-"
