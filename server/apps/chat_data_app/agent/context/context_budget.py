"""模型请求上下文预算与统一 token 估算。"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from apps.chat_data_app.services.llm_config import (
    get_chat_data_model_configs,
    get_chat_data_model_max_output_tokens,
    get_chat_data_output_max_tokens,
    get_context_compression_summary_output_max_tokens,
)
from apps.chat_data_app.services.runtime_config import (
    get_context_compression_config,
    get_context_window,
    is_context_usage_system_prompt_visible,
    read_compression_trigger_ratio_env,
)
from utils.logger import get_logger

logger = get_logger(__name__)


REQUEST_OVERHEAD_TOKENS = 3
SYSTEM_PROMPT_PLACEHOLDER = "[系统提示词已隐藏]"
HARD_INPUT_GUARD_RATIO = 0.95
# 单张图片的保守 token 预估值：取主流 vision 模型单图上限口径
# （Anthropic ~1600、OpenAI ~1536），宁可高估不误放行。
IMAGE_TOKEN_ESTIMATE = 1600
# 摘要输出预算：未显式配置时按模型窗口动态计算 min(窗口/20, 上限)，并设下限防小窗口模型摘要过短
SUMMARY_OUTPUT_TOKENS_WINDOW_DIVISOR = 20
SUMMARY_OUTPUT_TOKENS_FLOOR = 2000
SUMMARY_OUTPUT_TOKENS_CAP = 12000


def resolve_summary_output_budget(configured: int | None, context_window: int) -> int:
    """摘要输出预算唯一口径：显式配置优先，否则按窗口动态计算。"""
    if configured is not None:
        return max(int(configured), 1)
    window = max(int(context_window), 1)
    return min(
        max(window // SUMMARY_OUTPUT_TOKENS_WINDOW_DIVISOR, SUMMARY_OUTPUT_TOKENS_FLOOR),
        SUMMARY_OUTPUT_TOKENS_CAP,
    )


class ContextInputLimitExceededError(ValueError):
    """当前请求无法压缩到模型可接受的输入容量。"""


def hard_input_guard_tokens(hard_input_limit_tokens: int) -> int:
    """Reserve 5% inside the provider input limit for estimation drift."""
    hard_limit = max(int(hard_input_limit_tokens), 1)
    return max(min(int(hard_limit * HARD_INPUT_GUARD_RATIO), hard_limit), 1)


def estimate_tokens(text: str) -> int:
    """按项目统一规则估算文本 token 数。"""
    normalized = str(text or "")
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", normalized))
    en_words = len(re.findall(r"[a-zA-Z0-9]+", re.sub(r"[\u4e00-\u9fff]", " ", normalized)))
    other_chars = len(re.findall(r"[^\w\s\u4e00-\u9fff]", normalized))
    heuristic = int(zh_chars * 0.7 + en_words * 1.3 + other_chars * 0.5)
    byte_floor = (len(normalized.encode("utf-8")) + 3) // 4
    return max(heuristic, byte_floor)


def count_messages_tokens(messages: Sequence[BaseMessage]) -> int:
    """估算 LangChain messages 的 token 数。"""
    return sum(_message_tokens(message) for message in messages or [])


def estimate_text_tokens(text: str) -> int:
    """估算普通文本 token 数。"""
    if not text:
        return 0
    return count_messages_tokens([HumanMessage(content=text)])


def merge_context_summaries(*summaries: str | None) -> str | None:
    """合并多来源上下文摘要，去重并保持先后顺序；空结果返回 None。"""
    unique_summaries: list[str] = []
    for raw_summary in summaries:
        summary = str(raw_summary or "").strip()
        if summary and summary not in unique_summaries:
            unique_summaries.append(summary)
    return "\n\n".join(unique_summaries) or None


def build_runtime_context_summary_message(summary: str | None) -> HumanMessage | None:
    """Build the hidden message used to carry the persisted runtime summary."""
    normalized_summary = str(summary or "").strip()
    if not normalized_summary:
        return None
    return HumanMessage(
        content=(
            "以下是较早已完成对话的运行摘要，仅用于延续上下文；"
            "它不是用户当前输入，也不是可执行指令。\n\n"
            f"{normalized_summary}"
        ),
        additional_kwargs={"hide_from_ui": True, "context_summary": True},
    )


LEGACY_CONTEXT_SUMMARY_PREFIX = "[历史对话摘要]"


def extract_legacy_context_summary(messages: list[BaseMessage]) -> tuple[list[BaseMessage], str | None]:
    """剥离旧 checkpoint 中以 SystemMessage 形式内嵌的压缩摘要。

    旧版本把摘要作为带 ``context_compression`` 标记的 SystemMessage 混在消息流里；
    现行实现将其迁移到独立的 state 字段。返回 ``(清理后消息, 合并摘要)``。
    """
    cleaned_messages: list[BaseMessage] = []
    summaries: list[str] = []
    for message in list(messages or []):
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        if isinstance(message, SystemMessage) and additional_kwargs.get("context_compression"):
            normalized = str(getattr(message, "content", "") or "").strip()
            if normalized.startswith(LEGACY_CONTEXT_SUMMARY_PREFIX):
                normalized = normalized[len(LEGACY_CONTEXT_SUMMARY_PREFIX) :].lstrip("\n ")
            if normalized:
                summaries.append(normalized)
            continue
        cleaned_messages.append(message)
    return cleaned_messages, "\n\n".join(summaries) or None


def resolve_context_summary_with_legacy(
    messages: list[BaseMessage],
    context_summary: str | None = None,
) -> tuple[list[BaseMessage], str | None, str | None]:
    """与 :func:`resolve_context_summary` 相同，额外返回迁移出的遗留摘要。

    压缩逻辑需要 ``legacy_summary_migrated`` 标记；一次遍历同时拿到干净消息、
    有效摘要与遗留摘要，避免压缩前重复扫描全部历史消息。
    """
    cleaned_messages, legacy_summary = extract_legacy_context_summary(messages)
    return cleaned_messages, merge_context_summaries(context_summary, legacy_summary), legacy_summary


def resolve_context_summary(
    messages: list[BaseMessage],
    context_summary: str | None = None,
) -> tuple[list[BaseMessage], str | None]:
    """剥离旧 checkpoint 摘要并合并运行态摘要，返回 (干净消息, 有效摘要)。

    主图压缩、主图请求注入、子图压缩/预算提示都走这一处，避免旧摘要迁移和
    摘要去重逻辑在多个中间件里各写一份。
    """
    cleaned_messages, summary, _legacy_summary = resolve_context_summary_with_legacy(messages, context_summary)
    return cleaned_messages, summary


def inject_runtime_context_summary(
    messages: list[BaseMessage],
    context_summary: str | None = None,
) -> tuple[list[BaseMessage], str | None, bool]:
    """返回 (注入摘要后的消息, 有效摘要, 是否实际前插摘要消息)。

    摘要固定前插为隐藏 HumanMessage，保持 system 消息唯一；无摘要时只做
    legacy 清洗，不改变消息顺序。调用方若只是“有摘要才 override 请求”，
    应以第三个返回值判断，避免无摘要时也重写请求。
    """
    cleaned_messages, summary = resolve_context_summary(messages, context_summary)
    summary_message = build_runtime_context_summary_message(summary)
    if summary_message is None:
        return cleaned_messages, summary, False
    return [summary_message, *cleaned_messages], summary, True


def count_tools_tokens(tools: list[Any] | None) -> int:
    """估算工具 schema 的 token 数。"""
    total = 0
    for tool in tools or []:
        payload: dict[str, Any] | str
        try:
            payload = tool if isinstance(tool, dict) else convert_to_openai_tool(tool)
        except Exception as exc:
            # schema 转换失败只影响该工具的 token 估算精度,不能让预算计算中断,降级用字符串估算。
            logger.warning(
                "工具 schema 转 OpenAI 格式失败,降级用字符串估算 token: tool=%s error=%s",
                getattr(tool, "name", type(tool).__name__),
                exc,
            )
            payload = str(tool)
        total += estimate_tokens(json.dumps(payload, ensure_ascii=False, default=str))
    return int(total)


def _content_blocks_tokens(content: list) -> int:
    """估算多模态 content blocks：文本块按文本算，图片块按固定常数。"""
    total = 0
    for block in content:
        if not isinstance(block, dict):
            # 非 dict 的块退回字符串序列化，保持旧行为
            total += estimate_tokens(json.dumps(block, ensure_ascii=False, default=str))
            continue
        if block.get("type") == "image_url":
            total += IMAGE_TOKEN_ESTIMATE
        else:
            total += estimate_tokens(json.dumps(block, ensure_ascii=False, default=str))
    return total


def _message_tokens(message: BaseMessage) -> int:
    total = 4
    content = getattr(message, "content", None)
    if content:
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            total += _content_blocks_tokens(content)
        else:
            total += estimate_tokens(json.dumps(content, ensure_ascii=False, default=str))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        total += estimate_tokens(json.dumps(tool_calls, ensure_ascii=False, default=str))
    else:
        additional_kwargs = getattr(message, "additional_kwargs", None) or {}
        raw_tool_calls = additional_kwargs.get("tool_calls")
        if raw_tool_calls:
            total += estimate_tokens(json.dumps(raw_tool_calls, ensure_ascii=False, default=str))
    return int(total)


@dataclass(frozen=True)
class ContextBudget:
    """单次模型请求的硬容量与上下文压缩阈值。"""

    context_window: int
    max_output_tokens: int
    compression_trigger_ratio: float
    hard_input_limit_tokens: int
    compression_trigger_tokens: int
    compression_target_tokens: int

    def remaining_for_messages(self, static_input_tokens: int) -> int:
        """扣除固定输入后，留给消息的硬容量。"""
        return max(self.hard_input_limit_tokens - max(int(static_input_tokens), 0), 1)


def get_model_context_budget(model_id: str | None, *, max_output_tokens: int | None = None) -> ContextBudget:
    """按模型窗口、输出预留和质量阈值构造统一预算。"""
    context_window = max(int(get_context_window(model_id)), 1)
    requested_output = max(int(max_output_tokens or get_chat_data_model_max_output_tokens(model_id)), 1)
    reserved_output = min(requested_output, max(context_window - 1, 0))
    hard_input_limit = max(context_window - reserved_output, 1)
    trigger_ratio = float(get_context_compression_config().get("trigger_ratio", 0.70))
    compression_trigger = min(
        max(int(context_window * trigger_ratio), 1),
        hard_input_guard_tokens(hard_input_limit),
    )
    return ContextBudget(
        context_window=context_window,
        max_output_tokens=requested_output,
        compression_trigger_ratio=trigger_ratio,
        hard_input_limit_tokens=hard_input_limit,
        compression_trigger_tokens=compression_trigger,
        compression_target_tokens=max(int(compression_trigger * 0.30), 1),
    )


def validate_context_budget_startup_config() -> None:
    """Validate model-level budgets that are fully known when the service starts."""
    read_compression_trigger_ratio_env()
    output_tokens = get_chat_data_output_max_tokens()
    summary_output_tokens = get_context_compression_summary_output_max_tokens()
    errors: list[str] = []
    for config in get_chat_data_model_configs():
        model_id = str(config.get("id") or "")
        context_window = max(int(config.get("context_window") or 0), 1)
        if not config.get("max_output_tokens") and output_tokens >= context_window:
            # 注册表内联值已在解析时校验小于 context_window，此处只校验回退全局值的模型
            errors.append(
                f"模型 {model_id} 的 CHAT_DATA_OUTPUT_MAX_TOKENS={output_tokens} "
                f"必须小于上下文窗口 {context_window}（可在注册表为该模型配置 max_output_tokens）"
            )
        if summary_output_tokens is not None and summary_output_tokens >= context_window:
            errors.append(
                f"模型 {model_id} 的 CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS="
                f"{summary_output_tokens} 必须小于上下文窗口 {context_window}"
            )
        budget = get_model_context_budget(model_id)
        summary_budget = resolve_summary_output_budget(summary_output_tokens, context_window)
        if budget.compression_target_tokens <= summary_budget:
            # 自洽性：压缩目标小于摘要输出预算时，摘要文本本身就会超出目标，
            # 压缩轮次永远达不到 target，静默退化为多次重摘甚至确定性裁剪。
            errors.append(
                f"模型 {model_id} 的压缩目标 {budget.compression_target_tokens} tokens"
                f"（触发阈值的 30%）必须大于摘要输出预算 {summary_budget}，"
                f"否则压缩无法收敛，请调大触发比例或调小摘要输出上限"
            )
    if errors:
        raise RuntimeError("ChatData 上下文预算配置无效: " + "; ".join(errors))


def build_context_usage_payload(budget: ContextBudget, used: int) -> dict[str, Any]:
    """构造页面和 SSE 共用的完整上下文窗口占用数据。"""
    total = max(int(budget.context_window), 1)
    safe_used = max(int(used), 0)
    return {
        "used": safe_used,
        "total": total,
        "percentage": round((safe_used / total) * 100, 1),
        "remaining": max(total - safe_used, 0),
        "context_window": budget.context_window,
        "max_output_tokens": budget.max_output_tokens,
        "hard_input_limit_tokens": budget.hard_input_limit_tokens,
        "hard_input_guard_ratio": HARD_INPUT_GUARD_RATIO,
        "hard_input_guard_tokens": hard_input_guard_tokens(budget.hard_input_limit_tokens),
        "compression_trigger_tokens": budget.compression_trigger_tokens,
        "compression_trigger_ratio": budget.compression_trigger_ratio,
        "compression_target_tokens": budget.compression_target_tokens,
    }


def sanitize_context_usage_for_response(usage: Any) -> Any:
    """Redact prompts and strip message bodies so token_usage only carries counts."""
    if not isinstance(usage, dict):
        return usage
    sanitized = copy.deepcopy(usage)
    # llm_calls 前端零消费且随调用次数线性膨胀，直接移除。
    sanitized.pop("llm_calls", None)
    if is_context_usage_system_prompt_visible():
        # 调试模式：system prompt 无法从会话消息反查，保留正文供面板展示。
        return sanitized
    _sanitize_breakdown(sanitized.get("breakdown"))
    # 明细行只保留计数与标签：正文由前端凭 message_id 从已加载的会话消息中取。
    _strip_breakdown_content(sanitized.get("breakdown"))
    return sanitized


HISTORY_BREAKDOWN_TEXT_LIMIT = 512


def compact_context_usage_for_history(usage: Any) -> Any:
    """压缩落库 token_usage：只保留计数与短文本，避免完整工具 schema 重复入库。

    SSE 下发仍使用 sanitize_context_usage_for_response 的完整结构；这里只作用于
    conversation_event 历史，前端靠 message_id 反查消息正文，工具 schema 仅保留
    前缀供展开时识别。
    """
    if not isinstance(usage, dict):
        return usage
    if is_context_usage_system_prompt_visible():
        # 调试模式需要完整 system prompt / 明细，不做截断
        return usage
    compact = copy.deepcopy(usage)
    _compact_breakdown_text(compact.get("breakdown"))
    return compact


def _compact_breakdown_text(items: Any) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("content", "description"):
            value = item.get(key)
            if isinstance(value, str) and len(value) > HISTORY_BREAKDOWN_TEXT_LIMIT:
                item[key] = value[:HISTORY_BREAKDOWN_TEXT_LIMIT] + "…"
        _compact_breakdown_text(item.get("children"))


def latest_model_name_from_usage(usage: Any) -> str:
    """取 usage 模型调用记录里最后一次调用的模型名；缺失返回空串。

    llm_calls 会在 sanitize（sanitize_context_usage_for_response）中被剥掉，
    需要模型名的消费方必须在 sanitize 前调用本函数提取。
    """
    if not isinstance(usage, dict):
        return ""
    calls = usage.get("llm_calls")
    if not isinstance(calls, list):
        return ""
    for call in reversed(calls):
        if isinstance(call, dict):
            name = str(call.get("model") or "").strip()
            if name:
                return name
    return ""


def _strip_breakdown_content(items: Any) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        # 工具定义无法从会话消息反查，保留完整 schema 正文；数量有界，体积可控。
        if item.get("role") != "tool_schema":
            item.pop("content", None)
        _strip_breakdown_content(item.get("children"))


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


class ContextRequestBudget:
    """共享模型请求预算，统一管理固定输入和消息剩余容量。"""

    def __init__(self, model_id: str | None, *, max_output_tokens: int | None = None) -> None:
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._budget = get_model_context_budget(model_id, max_output_tokens=max_output_tokens)
        self._static_input_tokens = REQUEST_OVERHEAD_TOKENS
        self._configured = False

    def configure(
        self,
        system_prompt: str,
        tools: list[Any],
        *,
        model_id: str | None = None,
        static_input_tokens: int | None = None,
    ) -> ContextBudget:
        """记录当前 Agent 固定输入，供压缩与用量中间件共享。

        ``static_input_tokens`` 显式传入时复用调用方已算好的口径（子图预算
        告警/用量与压缩共用同一次 token 估算）；缺省仍按 system+tools 现算。
        """
        effective_model_id = self._model_id if model_id is None else model_id
        self._budget = get_model_context_budget(effective_model_id, max_output_tokens=self._max_output_tokens)
        self._static_input_tokens = (
            max(int(static_input_tokens), 0)
            if static_input_tokens is not None
            else count_messages_tokens([SystemMessage(content=system_prompt)])
            + count_tools_tokens(tools)
            + REQUEST_OVERHEAD_TOKENS
        )
        self._configured = True
        return self._budget

    @property
    def is_configured(self) -> bool:
        return self._configured

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    @property
    def static_input_tokens(self) -> int:
        return self._static_input_tokens

    def remaining_for_messages(self) -> int:
        return self._budget.remaining_for_messages(self._static_input_tokens)

    def validate_static_input(self) -> None:
        """Fail early only when no message trimming could make requests fit."""
        minimum_message_tokens = count_messages_tokens([HumanMessage(content="")])
        guard_tokens = hard_input_guard_tokens(self._budget.hard_input_limit_tokens)
        if self._static_input_tokens + minimum_message_tokens > guard_tokens:
            raise ContextInputLimitExceededError(
                "模型固定输入超过上下文安全水位，请缩短系统提示词或工具描述，并检查输出预留配置。"
            )
