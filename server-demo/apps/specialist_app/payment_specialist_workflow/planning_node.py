"""Node for emitting the planning narrative."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncIterator, Iterable, Sequence

from ..openai_client import get_openai_client
from .stream_protocol import StreamProtocol
import asyncio

logger = logging.getLogger(__name__)

PLAN_NODE_ID = "plan_single"
PLAN_NODE_TYPE = "thinking"
PLAN_NODE_TITLE = "正在制定分析思路"
PLAN_NODE_END_TITLE = "制定分析思路"
PLANNING_SYSTEM_PROMPT = (
    "你是法律检索规划助手，会根据对话历史整理后续调用法律检索工具时需要关注的关键信息。"
    "请直接输出要查的关键词、争议焦点、主体/时间/地域/金额等检索要素，必要时指出仍缺失的关键信息。"
    "保持中文要点式表述，不要客套。"
)


def _displayable_message(entry: Any) -> bool:
    """Check if a message should be shown to the planning model."""
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
    """Render recent user/assistant turns for the planning model."""
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


def _latest_user_question(messages: Sequence[dict[str, Any]]) -> str:
    """Pick the latest user-visible message content to describe the task."""
    for entry in reversed(messages):
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "user":
            continue
        content = entry.get("content") or ""
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


async def _plan_message_stream(
    messages: Sequence[dict[str, Any]],
    *,
    model_name: str | None = None,
) -> AsyncIterator[str]:
    question = _latest_user_question(messages) or "用户问题"
    fallback = f"先理解用户问题“{question}”，确定检索重点并准备调用法规检索工具。"
    yield fallback
    # history_text = _format_recent_messages(messages)
    # if not history_text:
    #     yield fallback
    #     return

    # try:
    #     client = get_openai_client()
    # except Exception as exc:  # pylint: disable=broad-except
    #     logger.warning("init planning client failed, fallback to default text: %s", exc)
    #     yield fallback
    #     return

    # target_model = (
    #     os.getenv("OPENAI_PLAN_MODEL")
    #     or model_name
    #     or os.getenv("OPENAI_SUGGEST_MODEL", "qwen3-30b-a3b-instruct-2507")
    # )
    # prefix = "结合历史对话整理检索要点：\n"
    # has_delta = False
    # try:
    #     stream = await client.chat.completions.create(
    #         model=target_model,
    #         messages=[
    #             {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
    #             {
    #                 "role": "user",
    #                 "content": (
    #                     "请根据以下历史消息，判断调用法律检索工具时应重点查询哪些关键信息，"
    #                     "用2-5条要点列出，缺失的信息也要指出：\n\n"
    #                     f"{history_text}"
    #                 ),
    #             },
    #         ],
    #         temperature=0.2,
    #         max_tokens=400,
    #         stream=True,
    #     )
    # except Exception as exc:  # pylint: disable=broad-except
    #     logger.warning("planning model failed, fallback to default text: %s", exc)
    #     yield fallback
    #     return

    # yield prefix
    # try:
    #     async for chunk in stream:
    #         if not chunk.choices:
    #             continue
    #         delta_part = chunk.choices[0].delta
    #         if not delta_part:
    #             continue
    #         delta_content = getattr(delta_part, "content", None)
    #         if not delta_content:
    #             continue
    #         if isinstance(delta_content, str):
    #             text = delta_content.strip()
    #             if text:
    #                 has_delta = True
    #                 yield text
    #         elif isinstance(delta_content, list):
    #             for item in delta_content:
    #                 text = item.get("text") if isinstance(item, dict) else None
    #                 if text:
    #                     has_delta = True
    #                     yield text
    #         else:
    #             has_delta = True
    #             yield str(delta_content)
    # except Exception as exc:  # pylint: disable=broad-except
    #     logger.warning("planning stream failed, fallback to default text: %s", exc)
    #     if not has_delta:
    #         yield fallback

    # if not has_delta:
    #     yield fallback


class PlanningNodeRunner:
    """Stream the planning narrative node."""

    NODE_ID = PLAN_NODE_ID
    NODE_TYPE = PLAN_NODE_TYPE
    NODE_TITLE = PLAN_NODE_TITLE
    NODE_END_TITLE = PLAN_NODE_END_TITLE

    def __init__(self, protocol: StreamProtocol, messages: Sequence[dict[str, Any]]) -> None:
        self.protocol = protocol
        self.messages = messages

    async def stream(self) -> Iterable[str]:
        for chunk in self.protocol.emit_node_start(
            self.NODE_ID, self.NODE_TYPE, self.NODE_TITLE
        ):
            yield chunk
        async for piece in _plan_message_stream(self.messages, model_name=self.protocol.model_name):
            if not piece:
                continue
            for chunk in self.protocol.emit_node_delta(self.NODE_ID, piece):
                yield chunk
        await asyncio.sleep(2)
        for chunk in self.protocol.emit_node_end(
            self.NODE_ID, "completed", self.NODE_END_TITLE
        ):
            yield chunk


__all__ = ["PlanningNodeRunner"]
