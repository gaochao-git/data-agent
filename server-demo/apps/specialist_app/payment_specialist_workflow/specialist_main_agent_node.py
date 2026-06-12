"""Main specialist agent node that stitches sub-agent outputs together."""

from __future__ import annotations

import logging
import time
import os
from typing import Any, AsyncIterator, Iterable, Sequence

from ..openai_client import get_openai_client
from .legal_research_subagent_node import LegalResearchSubAgentNodeRunner
from .stream_protocol import StreamProtocol

logger = logging.getLogger(__name__)

DECISION_NODE_ID = "payment_specialist_decision"
DECISION_NODE_TYPE = "thinking"
DECISION_NODE_TITLE = "制定分析思路"
DECISION_NODE_END_TITLE = "规划完成"
DECISION_SYSTEM_PROMPT = (
    """
你是一个AI专家系统中的【检索意图识别模块】。

你的任务是判断：用户问题是否需要进行法规条文检索。

【输出规则】
1. 如果需要检索：
   - 只输出两行
   - 第一行以“【检索】”开头，后面给出用于检索的“最小语义关键词”
   - 第二行以“【提示】”开头，给出一段供前端展示的话术
   - “最小语义关键词”仅保留“行为 + 对象 / 事项”
   - 删除疑问词、语气词、以及“法律、法规、违反、责任、依据”等冗余词
   - 两行之间必须换行，不允许输出在同一行
   - “【提示】”话术需结合用户问题生成，风格简洁中性，10-20字
   - 避免使用“已收到问题/请求/正在...”等固定模板
   - 不要添加解释，不要造句，不要输出多余内容

2. 如果不需要检索：
   - 直接输出最终答复
   - 不要包含“【检索】”

【示例】
输入：违规发行预付卡违反了什么法律？
输出：
【检索】违规发行预付卡
【提示】已收到问题，我将检索相关法律条款

输入：商家不退预付卡余额合法吗？
输出：
【检索】不退预付卡余额
【提示】我理解你在询问退款合法性，准备检索相关法律条款

输入：你是谁？
输出：我是法律检索辅助系统。

现在处理用户输入：
{用户问题}

    """
)


def _displayable_message(entry: Any) -> bool:
    """Check if a message should be shown to the model."""
    if not isinstance(entry, dict):
        return False
    role = entry.get("role")
    if role not in {"user", "assistant"}:
        return False
    display_meta = entry.get("display")
    if isinstance(display_meta, dict) and display_meta.get("visible") is False:
        return False
    return True


def _extract_display_content(entry: dict[str, Any]) -> str:
    display_meta = entry.get("display")
    if isinstance(display_meta, dict):
        display_content = display_meta.get("content")
        if isinstance(display_content, str):
            return display_content.strip()
    content = entry.get("content")
    return "" if content is None else str(content).strip()


def _format_recent_messages(messages: Sequence[dict[str, Any]], limit: int = 12) -> str:
    """Render recent user/assistant turns for the decision model."""
    rendered: list[str] = []
    for entry in messages:
        if not _displayable_message(entry):
            continue
        text = _extract_display_content(entry)
        if not text:
            continue
        prefix = "用户" if entry.get("role") == "user" else "助手"
        rendered.append(f"{prefix}：{text}")
    if limit > 0:
        rendered = rendered[-limit:]
    return "\n".join(rendered).strip()


def _extract_prefixed_segment(text: str, prefix: str, other_prefixes: Sequence[str]) -> str | None:
    """Return the segment that starts with prefix, stopping before any other prefix."""
    start = text.find(prefix)
    if start < 0:
        return None
    end = len(text)
    for other in other_prefixes:
        if other == prefix:
            continue
        idx = text.find(other, start + len(prefix))
        if idx >= 0:
            end = min(end, idx)
    segment = text[start:end].strip()
    return segment or None


class SpecialistMainAgentNodeRunner:
    """Main agent node that decides whether to检索或直接回复.

    The node先调用模型决定是否需要法规检索（输出规范前缀“【检索】”）。若需要检索，则
    触发子agent检索/总结/引用；否则直接将模型输出作为最终可见回复。
    """

    def __init__(
        self,
        protocol: StreamProtocol,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        sub_agents: Sequence[Any] | None = None,
        main: bool = True,
    ) -> None:
        self.protocol = protocol
        self.messages = list(messages or [])
        self.sub_agents = list(sub_agents or [])
        self.is_main = main

    async def _decision_stream(self) -> AsyncIterator[str]:
        """Ask the LLM whether to search or answer directly."""
        history_text = _format_recent_messages(self.messages)
        if not history_text:
            return

        try:
            client = get_openai_client()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("init main-agent client failed, fallback to default text: %s", exc)
            return
        target_model = (
            os.getenv("OPENAI_PLAN_MODEL")
            or self.protocol.model_name
            or os.getenv("OPENAI_SUGGEST_MODEL", "qwen3-30b-a3b-instruct-2507")
        )
        try:
            stream = await client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"请根据以下历史对话，判断是否需要法规检索：\n{history_text}"
                        ),
                    },
                ],
                temperature=0.2,
                max_tokens=500,
                stream=True,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("main-agent model failed, fallback to default text: %s", exc)
            return

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta_part = chunk.choices[0].delta
                if not delta_part:
                    continue
                delta_content = getattr(delta_part, "content", None)
                if not delta_content:
                    continue
                if isinstance(delta_content, str):
                    text = delta_content.strip()
                    if text:
                        yield text
                elif isinstance(delta_content, list):
                    for item in delta_content:
                        text = item.get("text") if isinstance(item, dict) else None
                        if text:
                            yield text
                else:
                    yield str(delta_content)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("main-agent stream failed, fallback to default text: %s", exc)

    async def stream(self) -> Iterable[str]:
        protocol = self.protocol

        decision_node_id = DECISION_NODE_ID
        decision_node_type = DECISION_NODE_TYPE
        decision_node_title = DECISION_NODE_TITLE
        decision_node_end_title = DECISION_NODE_END_TITLE

        decision_chunks: list[str] = []

        async for piece in self._decision_stream():
            if piece:
                decision_chunks.append(piece)

        decision_text = "".join(decision_chunks).strip() or self._fallback_decision()
        search_line = _extract_prefixed_segment(
            decision_text, "【检索】", ("【检索】", "【提示】")
        )
        tip_line = _extract_prefixed_segment(
            decision_text, "【提示】", ("【检索】", "【提示】")
        )
        needs_search = search_line is not None

        if needs_search:
            print(
                "[decision-parse] search_line=%r tip_line=%r raw=%r"
                % (search_line, tip_line, decision_text)
            )
            tip_line_id = f"{decision_node_id}-tip-{int(time.time() * 1000)}"
            for chunk in protocol.emit_node_start(
                decision_node_id,
                decision_node_type,
                decision_node_title,
                label_hidden=True,
            ):
                yield chunk
            if tip_line:
                tip_text = tip_line[len("【提示】") :].strip() or tip_line
                for chunk in protocol.emit_node_delta(
                    decision_node_id,
                    tip_text,
                    line_id=tip_line_id,
                    label_hidden=True,
                ):
                    yield chunk
            else:
                for chunk in protocol.emit_node_delta(
                    decision_node_id,
                    "已收到问题，准备检索相关法律条款",
                    line_id=tip_line_id,
                    label_hidden=True,
                ):
                    yield chunk
            final_query = (
                (search_line or decision_text)[len("【检索】") :].strip()
                or self._latest_user_query()
            )
            for chunk in protocol.emit_node_end(
                decision_node_id,
                "completed",
                decision_node_end_title,
                label_hidden=True,
            ):
                yield chunk
            legal_runner = LegalResearchSubAgentNodeRunner(protocol, final_query)
            self.sub_agents.append(legal_runner)
            async for chunk in legal_runner.stream():
                yield chunk
        else:
            visible_reply = decision_text or self._latest_user_query() or "感谢您的提问。"
            async for chunk in protocol.emit_text_delta(visible_reply, record_visible=True):
                yield chunk


__all__ = [
    "SpecialistMainAgentNodeRunner",
    "DECISION_NODE_ID",
    "DECISION_NODE_TYPE",
    "DECISION_NODE_TITLE",
    "DECISION_NODE_END_TITLE",
]
