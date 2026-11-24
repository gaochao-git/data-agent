"""子智能体报告的裁剪与外置。

报告是子图回传的结论而不是过程：过长会推高父上下文，因此沿用工具结果
同一条落盘/信封通路。独立成模块，避免主入口 ``subagents.py`` 继续膨胀。
"""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage

from apps.chat_data_app.services.runtime_config import (
    SUBAGENT_RESULT_PREVIEW_MAX_CHARS,
    TOOL_RESULT_OFFLOAD_SIZE,
)
from utils.logger import get_logger

from ..context.context_budget import estimate_text_tokens
from .subagent_async import _truncate_text
from .subagent_text import plain_text, sanitize_report_text
from .tool_result import externalize_tool_text

logger = get_logger(__name__)

# 工具结果外置时记录的逻辑来源，用于标识“来自子智能体报告”，不是模型工具名。
SUBAGENT_RESULT_SOURCE = "subagent"

# 子智能体报告进入父上下文前的封顶：报告是结论而非过程，单次派发不应把父
# 上下文推过压缩线。阈值按父 state 的硬上限占比算（32k 与 128k 窗口的合理
# 字节数不同，不写死字符数），超过后复用工具结果同一条落盘/信封通路。
SUBAGENT_REPORT_MAX_TOKEN_RATIO = 0.08
SUBAGENT_REPORT_MIN_TOKEN_LIMIT = 1000
SUBAGENT_REPORT_PREVIEW_CHARS = 2000


def _extract_final_report(messages: list) -> str:
    """取子图最后一个非空 AIMessage 文本作为回传结论。"""
    for message in reversed(messages or []):
        if not isinstance(message, AIMessage):
            continue
        text = sanitize_report_text(getattr(message, "text", "") or "")
        if text:
            return text
    return ""


def _truncate_subagent_result(content: Any) -> str:
    """把内层工具结果压成 SSE 可安全外发的文本预览。"""
    return _truncate_text(plain_text(content), SUBAGENT_RESULT_PREVIEW_MAX_CHARS)


def _subagent_report_token_limit(runtime: ToolRuntime) -> int | None:
    """报告封顶阈值 = 父上下文硬上限的固定占比，避免父上下文被单次报告推过压缩线。"""
    state = getattr(runtime, "state", None)
    usage = state.get("token_usage") if isinstance(state, dict) else None
    if not isinstance(usage, dict):
        return None
    hard_limit = int(usage.get("hard_input_limit_tokens") or 0)
    if hard_limit <= 0:
        return None
    return max(int(hard_limit * SUBAGENT_REPORT_MAX_TOKEN_RATIO), SUBAGENT_REPORT_MIN_TOKEN_LIMIT)


async def _offload_subagent_report(report: str, runtime: ToolRuntime) -> tuple[str, dict[str, Any] | None]:
    """超长报告落盘：前台用信封保护上下文，后台只取 recovery 元数据。"""
    limit = _subagent_report_token_limit(runtime)
    report_tokens = estimate_text_tokens(report)
    over_token_budget = limit is not None and report_tokens > limit
    if not over_token_budget and len(report) <= TOOL_RESULT_OFFLOAD_SIZE:
        return report, None
    state = getattr(runtime, "state", None)
    envelope, recovery = await externalize_tool_text(
        report,
        source_tool=SUBAGENT_RESULT_SOURCE,
        state=dict(state) if isinstance(state, dict) else {},
        suffix=".md",
        preview_chars=SUBAGENT_REPORT_PREVIEW_CHARS,
    )
    if recovery is None:
        # 报告是昂贵且不可复现的结论：落盘失败时保留原文，不能静默换成无内容的截断信封。
        logger.warning(
            "[tools] 子智能体报告落盘失败，保留原文: limit_tokens=%s report_tokens=%s",
            limit,
            report_tokens,
        )
        return report, None
    logger.info(
        "[tools] 子智能体报告超过父上下文预算，已落盘外置: limit_tokens=%s report_tokens=%s",
        limit,
        report_tokens,
    )
    return envelope, recovery


__all__ = [
    "SUBAGENT_REPORT_MAX_TOKEN_RATIO",
    "SUBAGENT_REPORT_MIN_TOKEN_LIMIT",
    "SUBAGENT_REPORT_PREVIEW_CHARS",
    "SUBAGENT_RESULT_SOURCE",
    "_extract_final_report",
    "_offload_subagent_report",
    "_subagent_report_token_limit",
    "_truncate_subagent_result",
]
