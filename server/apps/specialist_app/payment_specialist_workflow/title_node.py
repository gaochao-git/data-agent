"""Node for generating conversation titles."""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator

from ..openai_client import get_openai_client
from .stream_protocol import StreamProtocol

logger = logging.getLogger(__name__)

TITLE_SYSTEM_PROMPT = (
    """
你是一个AI专家系统-会话标题生成助手，负责根据用户的问题生成 30 字内的简洁标题。
要求有总结性，又与用户的问题有区分度。不带引号或编号，避免通用描述。
    """
)


def build_mock_title(question: str) -> str:
    base = (question or "").strip() or "新的会话"
    if len(base) > 28:
        return base[:28].rstrip() + "..."
    return base


async def _title_stream(question: str, model_name: str | None) -> AsyncIterator[str]:
    """Yield raw delta content from the title generation stream."""
    cleaned = (question or "").strip()
    if not cleaned:
        yield build_mock_title(question)
        return

    try:
        client = get_openai_client()
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("title client init failed, fallback to mock: %s", exc)
        yield build_mock_title(cleaned)
        return

    target_model = model_name or os.getenv("OPENAI_SUGGEST_MODEL", "qwen3-30b-a3b-instruct-2507")
    try:
        stream = await client.chat.completions.create(
            model=target_model,
            messages=[
                {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"请为这个问题生成标题：{cleaned}",
                },
            ],
            temperature=0.2,
            max_tokens=300,
            stream=True,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("title generation failed, fallback to mock: %s", exc)
        yield build_mock_title(cleaned)
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
                yield delta_content
            elif isinstance(delta_content, list):
                for item in delta_content:
                    text = item.get("text") if isinstance(item, dict) else None
                    if text:
                        yield text
            else:
                yield str(delta_content)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("title stream failed, fallback to mock: %s", exc)
        yield build_mock_title(cleaned)


class TitleNodeRunner:
    """Stream title generation and collect the final title."""

    def __init__(self, protocol: StreamProtocol, question: str) -> None:
        self.protocol = protocol
        self.question = question
        self.model_name = protocol.model_name
        self.final_title: str | None = None
        self._title_emitted = False

    def _emit_event(self, title: str) -> str:
        self._title_emitted = True
        return self.protocol.build_event(
            "title_generation",
            include_message_id=False,
            title=title,
        )

    def _emit_done(self, title: str) -> str:
        return self.protocol.build_event(
            "title_generation_done",
            include_message_id=False,
            title=title,
        )

    async def stream(self) -> AsyncIterator[str]:
        cleaned = (self.question or "").strip()
        pieces: list[str] = []

        try:
            async for delta in _title_stream(cleaned, self.model_name):
                if not delta:
                    continue
                pieces.append(delta)
                yield self._emit_event(delta)
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("title runner failed, fallback to mock: %s", exc)
            fallback_title = build_mock_title(cleaned)
            self.final_title = fallback_title
            yield self._emit_event(fallback_title)
            return

        if self.final_title is None:
            final_source = "".join(pieces).strip() or cleaned
            self.final_title = build_mock_title(final_source)
            if not self._title_emitted and self.final_title:
                yield self._emit_event(self.final_title)
        if self.final_title:
            yield self._emit_done(self.final_title)


__all__ = [
    "TitleNodeRunner",
]
