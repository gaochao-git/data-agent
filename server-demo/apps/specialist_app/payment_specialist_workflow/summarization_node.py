"""Node for summarizing search results."""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Iterable

from ..openai_client import get_openai_client
from ..trace_utils import write_trace
from ..utils import chunk_text
from .stream_protocol import StreamProtocol

import os


SUMMARY_SYSTEM_PROMPT = (
    """
你是一个AI专家系统-法律问题分析总结助手，你将拿到用户问题以及已经检索到的法律原文片段，请以简洁中文总结主要义务/限制，并在适当位置引用已有的【引用n】标记。
注意不要编造新的条款或引用；
若检索结果为空，请如实说明。
    """
)


async def _summarize_answer_stream(
    question: str,
    search_payload: str,
    model_name: str | None,
) -> AsyncIterator[str]:
    """Yield summary deltas from the LLM for streaming back to the client."""

    target_model = model_name or os.getenv("OPENAI_SUGGEST_MODEL", "qwen3-30b-a3b-instruct-2507")
    client = get_openai_client()
    stream = await client.chat.completions.create(
        model=target_model,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"用户问题：\n{question}\n\n"
                    f"检索结果：\n{search_payload or '（检索结果为空）'}\n\n"
                    "请根据以上信息输出总结。"
                ),
            },
        ],
        temperature=0.2,
        stream=True,
    )
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


class SummarizationNodeRunner:
    """Stream the summarization node and collect the summary text."""

    def __init__(
        self,
        protocol: StreamProtocol,
        query: str,
        summary_payload: str,
    ) -> None:
        self.protocol = protocol
        self.query = query
        self.summary_payload = summary_payload
        self.model_name = protocol.model_name
        self.summary_text: str = ""

    async def stream(self) -> AsyncIterator[str]:
        protocol = self.protocol
        analysis_node_id = f"analysis_{time.time_ns()}"
        for chunk in protocol.emit_node_start(
            analysis_node_id,
            "thinking",
            "正在综合分析",
            label_hidden=True,
        ):
            yield chunk
        for chunk in protocol.emit_node_delta(
            analysis_node_id,
            "结合法规检索结果整理最终建议...",
            label_hidden=True,
        ):
            yield chunk
        time.sleep(2)
        for chunk in protocol.emit_node_end(
            analysis_node_id,
            "completed",
            "综合分析思考",
            label_hidden=True,
        ):
            yield chunk

        summary_chunks: list[str] = []
        summary_started = False
        summary_error: Exception | None = None
        write_trace(
            protocol.conversation_id,
            "summary_input",
            {
                "question": self.query,
                "search_payload": self.summary_payload,
                "model": self.model_name,
            },
        )
        try:
            async for delta_text in _summarize_answer_stream(
                self.query, self.summary_payload, self.model_name
            ):
                if not delta_text:
                    continue
                if not summary_started:
                    summary_started = True
                    async for chunk in protocol.emit_text_delta("\n", record_visible=True):
                        yield chunk
                summary_chunks.append(delta_text)
                async for chunk in protocol.emit_text_delta(delta_text, record_visible=True):
                    yield chunk
        except Exception as summary_exc:  # pylint: disable=broad-except
            summary_error = summary_exc

        if summary_started:
            async for chunk in protocol.emit_text_delta("\n", record_visible=True):
                yield chunk

        summary_text = "".join(summary_chunks).strip()
        if not summary_text:
            summary_text = "检索完成，但暂未检索到可引用的条款，请参考下方引用内容。"
            if summary_error:
                summary_text = "检索完成，但总结服务暂不可用，请参考下方引用内容。"
            for piece in chunk_text("\n" + summary_text + "\n"):
                async for chunk in protocol.emit_text_delta(piece, record_visible=True):
                    yield chunk

        self.summary_text = summary_text
        write_trace(
            protocol.conversation_id,
            "summary_output",
            {
                "summary": summary_text,
                "error": str(summary_error) if summary_error else None,
            },
        )


__all__ = ["SummarizationNodeRunner"]
