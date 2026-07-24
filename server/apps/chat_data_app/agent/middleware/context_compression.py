"""Context compression middleware for persisted agent state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import before_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from ...services.context_budget import (
    ToolResultBudget,
    build_runtime_context_summary_message,
    count_messages_tokens,
)
from ...services.llm_config import get_chat_data_llm
from ...services.runtime_config import get_context_compression_config

logger = get_logger(__name__)

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
    previous_summary: str | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
) -> tuple[list[BaseMessage], str | None, dict[str, Any]]:
    """压缩已完成旧历史，仅替换运行态消息并返回独立累计摘要。"""
    cfg = _config_from_mapping(config)
    original_messages, legacy_summary = _extract_legacy_context_summary(messages)
    effective_previous_summary = _merge_context_summaries(previous_summary, legacy_summary)
    before_tokens = count_messages_tokens(original_messages) + _summary_tokens(effective_previous_summary)
    threshold_tokens = max(int(message_budget_tokens or int(max(context_window, 1) * cfg.trigger_ratio)), 1)
    report = _build_report(
        cfg,
        before_tokens=before_tokens,
        after_tokens=before_tokens,
        threshold_tokens=threshold_tokens,
    )
    report["legacy_summary_migrated"] = bool(legacy_summary)
    if not cfg.enabled or before_tokens <= threshold_tokens:
        return original_messages, effective_previous_summary, report

    compressible_end = _compressible_end_index(original_messages)
    if compressible_end <= 0:
        report["compression_skipped_reason"] = "no_completed_old_rounds"
        return original_messages, effective_previous_summary, report

    # 压缩范围内的原始内容必须完整交给摘要模型，不能先按字符截断再删除运行态消息。
    messages_to_summarize = original_messages[:compressible_end]
    if summarizer:
        summary_text = await summarizer(messages_to_summarize)
    else:
        summary_text = await _summarize_messages_with_llm(
            messages_to_summarize,
            previous_summary=effective_previous_summary,
        )
    if not summary_text:
        report["compression_skipped_reason"] = "empty_summary"
        return original_messages, effective_previous_summary, report

    compressed_messages = original_messages[compressible_end:]
    after_tokens = count_messages_tokens(compressed_messages) + _summary_tokens(summary_text)
    report.update({
        "triggered": True,
        "estimated_tokens_after": after_tokens,
        "estimated_cleared_tokens": max(before_tokens - after_tokens, 0),
        "compressed_messages": compressible_end,
        "summary_chars": len(summary_text),
    })
    logger.info(
        "[agent] 上下文压缩已执行: before_tokens=%s after_tokens=%s threshold_tokens=%s compressed_messages=%s",
        before_tokens,
        after_tokens,
        threshold_tokens,
        compressible_end,
    )
    return compressed_messages, summary_text, report


async def build_context_compression_update(
    messages: list[BaseMessage],
    *,
    context_window: int,
    message_budget_tokens: int,
    config: dict[str, Any] | None = None,
    previous_summary: str | None = None,
    summarizer: Callable[[list[BaseMessage]], Awaitable[str]] | None = None,
) -> tuple[list[BaseMessage] | None, str | None, dict[str, Any]]:
    """构造可写入 LangGraph state 的压缩消息更新。"""
    compressed_messages, summary_text, report = await compress_messages_for_state(
        messages,
        context_window=context_window,
        message_budget_tokens=message_budget_tokens,
        config=config,
        previous_summary=previous_summary,
        summarizer=summarizer,
    )
    if not report.get("triggered") and not report.get("legacy_summary_migrated"):
        return None, summary_text, report
    return [RemoveMessage(id=REMOVE_ALL_MESSAGES), *compressed_messages], summary_text, report


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
        _, legacy_summary = _extract_legacy_context_summary(messages)
        previous_summary = _merge_context_summaries(
            str((state or {}).get("context_summary") or "").strip() or None,
            legacy_summary,
        )
        budget = compression_budget.configure(system_prompt, request_tools, model_id=model_id)
        message_budget_tokens = compression_budget.remaining_for_messages()
        message_updates, summary_text, report = await build_context_compression_update(
            messages,
            context_window=budget.context_window,
            message_budget_tokens=message_budget_tokens,
            config=get_context_compression_config(),
            previous_summary=previous_summary,
            summarizer=lambda compressible_messages: _summarize_messages_with_llm(
                compressible_messages,
                model_id=model_id,
                previous_summary=previous_summary,
            ),
        )
        if not message_updates:
            return {"context_compression": report}
        return {
            "messages": message_updates,
            "context_summary": summary_text,
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
        "legacy_summary_migrated": False,
    }


def _extract_legacy_context_summary(messages: list[BaseMessage] | None) -> tuple[list[BaseMessage], str | None]:
    """将旧 checkpoint 中的摘要 SystemMessage 迁移到独立 state 字段。"""
    cleaned_messages: list[BaseMessage] = []
    summaries: list[str] = []
    for message in list(messages or []):
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        if isinstance(message, SystemMessage) and additional_kwargs.get("context_compression"):
            content = _strip_legacy_summary_prefix(str(getattr(message, "content", "") or ""))
            if content:
                summaries.append(content)
            continue
        cleaned_messages.append(message)
    return cleaned_messages, "\n\n".join(summaries) or None


def _strip_legacy_summary_prefix(summary: str) -> str:
    prefix = "[历史对话摘要]"
    normalized = str(summary or "").strip()
    if normalized.startswith(prefix):
        return normalized[len(prefix):].lstrip("\n ")
    return normalized


def _merge_context_summaries(*summaries: str | None) -> str | None:
    unique_summaries: list[str] = []
    for raw_summary in summaries:
        summary = str(raw_summary or "").strip()
        if summary and summary not in unique_summaries:
            unique_summaries.append(summary)
    return "\n\n".join(unique_summaries) or None


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
        if isinstance(message, (AIMessage, ToolMessage)) and not pending_tool_calls:
            boundary = index + 1
    return boundary


def _summary_tokens(summary_text: str | None) -> int:
    summary_message = build_runtime_context_summary_message(summary_text)
    return count_messages_tokens([summary_message]) if summary_message else 0


async def _summarize_messages_with_llm(
    messages: list[BaseMessage],
    *,
    model_id: str | None = None,
    previous_summary: str | None = None,
) -> str:
    prompt = _build_summary_prompt(messages, previous_summary=previous_summary)
    llm = get_chat_data_llm(
        model=model_id,
        streaming=False,
        max_tokens=SUMMARY_MAX_TOKENS,
        enable_thinking=False,
    )
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as exc:
        emit_llm_connection_alarm_if_needed(
            exc,
            ext1={"model_id": model_id or "", "purpose": "context_compression"},
        )
        raise
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") if isinstance(item, dict) else item) for item in content)
    return str(content or "").strip()


def _build_summary_prompt(messages: list[BaseMessage], *, previous_summary: str | None = None) -> str:
    transcript = _format_messages_for_summary(messages)
    existing_summary = str(previous_summary or "").strip()
    previous_summary_section = (
        f"\n\n已有历史摘要：\n{existing_summary}\n\n"
        if existing_summary else "\n\n"
    )
    return (
        "请将以下历史对话压缩成一份中文摘要，用于后续继续对话。\n"
        "要求：保留用户真实目标、已确认的数据源/指标集/执行对象、关键筛选条件、SQL/DSL、工具结果结论、未完成事项和需要继续遵守的约束；"
        "不要编造不存在的数据、指标或结论；将已有摘要与新增消息合并为一份完整摘要，不需要逐字复述。"
        f"{previous_summary_section}"
        f"{transcript}"
    )


def _format_messages_for_summary(messages: list[BaseMessage]) -> str:
    return "\n".join(
        f"{index}. {_message_role(message)}: {_message_content_for_summary(message)}"
        for index, message in enumerate(messages, start=1)
    )


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, HumanMessage):
        return "用户"
    if isinstance(message, AIMessage):
        return "助手"
    if isinstance(message, ToolMessage):
        return f"工具({getattr(message, 'name', None) or '-'})"
    return str(getattr(message, "type", "") or "消息")


def _message_content_for_summary(message: BaseMessage) -> str:
    content = str(getattr(message, "content", "") or "").strip()
    if not isinstance(message, AIMessage):
        return content or "-"

    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return content or "-"
    tool_calls_text = "调用工具 " + json.dumps(tool_calls, ensure_ascii=False, default=str)
    return f"{content}\n{tool_calls_text}" if content else tool_calls_text
