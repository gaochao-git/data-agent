"""测试用脚本化流式聊天模型。

内置 ``langchain_core`` fake 只能流式输出正文：``GenericFakeChatModel`` 丢掉
``tool_calls``（实测直接 ``No generation chunks were returned``），
``FakeMessagesListChatModel`` 把整条消息当一块吐出，没有 ``tool_call_chunks``。
工具参数增量（``tool_args_delta``）、半截 JSON、usage 收口这些链路因此测不到。

``ScriptedChatModel`` 按轮次脚本产出真实形态的 ``AIMessageChunk``：
正文可按片切，工具调用参数按 JSON 片段切，可注入异常与 usage，也可按请求内容
路由到不同脚本（正常回答 / 压缩摘要 / 标题生成）。子智能体与主图共用同一个
模型实例，注入一次即可覆盖两层。
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, PrivateAttr


@dataclass
class FakeTurn:
    """一轮模型调用：按顺序产出的 chunk，或一个待抛出的异常。

    ``error_after_chunks`` 仅对流式生效：先吐出指定数量的 chunk 再抛 ``error``，
    用于模拟上游在参数流到一半时断开。非流式调用无法部分交付，仍直接抛错。
    """

    chunks: list[dict[str, Any]] = field(default_factory=list)
    error: BaseException | None = None
    error_after_chunks: int | None = None

    def interrupt_after(self, count: int, error: BaseException) -> FakeTurn:
        """流式吐出 ``count`` 个 chunk 后抛 ``error``，模拟上游中途断开。"""
        self.error_after_chunks = count
        self.error = error
        return self


def _deliverable_chunks(turn: FakeTurn) -> list[dict[str, Any]]:
    """流式可交付的 chunk：上游在 ``error_after_chunks`` 处断开时只交付其之前的部分。"""
    if turn.error is None:
        return turn.chunks
    limit = turn.error_after_chunks if turn.error_after_chunks is not None else 0
    return turn.chunks[: max(0, limit)]


def _split_text(text: str, pieces: int) -> list[str]:
    if pieces <= 1 or len(text) <= 1:
        return [text]
    size = max(1, (len(text) + pieces - 1) // pieces)
    return [text[index : index + size] for index in range(0, len(text), size)]


def text_turn(text: str, *, pieces: int = 4, usage: dict[str, Any] | None = None) -> FakeTurn:
    """正文按片流式输出的一轮。"""
    chunks: list[dict[str, Any]] = [{"content": piece} for piece in _split_text(text, pieces)]
    if usage:
        chunks[-1]["usage_metadata"] = usage
    return FakeTurn(chunks=chunks)


def tool_call_turn(
    name: str,
    args: dict[str, Any],
    *,
    call_id: str = "call-1",
    arg_pieces: int = 3,
    prefix_text: str = "",
    usage: dict[str, Any] | None = None,
) -> FakeTurn:
    """一次工具调用的流式一轮：首个 chunk 带 id/name，后续 chunk 只带参数片段。"""
    arguments = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    chunks: list[dict[str, Any]] = []
    if prefix_text:
        chunks.append({"content": prefix_text})
    for index, piece in enumerate(_split_text(arguments, arg_pieces)):
        chunks.append(
            {
                "content": "",
                "tool_call_chunks": [
                    {
                        "name": name if index == 0 else None,
                        "args": piece,
                        "id": call_id if index == 0 else None,
                        "index": 0,
                    }
                ],
            }
        )
    if usage:
        chunks[-1]["usage_metadata"] = usage
    return FakeTurn(chunks=chunks)


def error_turn(error: BaseException) -> FakeTurn:
    """调用即抛异常的一轮，用于超时、限流、坏响应等失败路径。"""
    return FakeTurn(error=error)


def chunks_turn(chunks: Sequence[dict[str, Any]]) -> FakeTurn:
    """完全自定义 chunk 形态的一轮。"""
    return FakeTurn(chunks=list(chunks))


def _make_chunk(spec: dict[str, Any]) -> AIMessageChunk:
    """按 spec 造 chunk；``content`` 缺省补空串（``AIMessageChunk`` 要求必填）。"""
    return AIMessageChunk(**{"content": "", **spec})


class ScriptedChatModel(BaseChatModel):
    """按脚本产出流式 chunk 的测试模型。"""

    turns: list[Any] = Field(default_factory=list)
    router: Any = None
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_tool_names: list[str] = Field(default_factory=list)

    _cursor: int = PrivateAttr(default=0)
    _lock: Any = PrivateAttr(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted-chat-model"

    @property
    def remaining_turns(self) -> int:
        return len(self.turns) - self._cursor

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> ScriptedChatModel:
        names: list[str] = []
        for item in tools:
            name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
            if name:
                names.append(str(name))
        self.bound_tool_names = names
        return self

    def _next_turn(self, messages: Sequence[BaseMessage]) -> FakeTurn:
        routed = self.router(messages) if self.router is not None else None
        with self._lock:
            index = routed if routed is not None else self._cursor
            if routed is None:
                self._cursor += 1
            self.calls.append(list(messages))
        if not 0 <= index < len(self.turns):
            raise AssertionError(
                f"脚本化模型轮次耗尽: 第 {len(self.calls)} 次调用需要第 {index} 轮脚本，已配置 {len(self.turns)} 轮"
            )
        return self.turns[index]

    @staticmethod
    def _merge_chunks(chunks: Sequence[dict[str, Any]]) -> AIMessageChunk | None:
        merged: AIMessageChunk | None = None
        for spec in chunks:
            piece = _make_chunk(spec)
            merged = piece if merged is None else merged + piece
        return merged

    @staticmethod
    def _to_message(chunk: AIMessageChunk | None) -> AIMessage:
        if chunk is None:
            return AIMessage(content="")
        return AIMessage(
            content=chunk.content,
            tool_calls=list(chunk.tool_calls),
            additional_kwargs=dict(chunk.additional_kwargs),
            response_metadata=dict(chunk.response_metadata),
            usage_metadata=chunk.usage_metadata,
            id=chunk.id,
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        turn = self._next_turn(messages)
        if turn.error is not None:
            raise turn.error
        return ChatResult(generations=[ChatGeneration(message=self._to_message(self._merge_chunks(turn.chunks)))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        turn = self._next_turn(messages)
        for spec in _deliverable_chunks(turn):
            chunk = ChatGenerationChunk(message=_make_chunk(spec))
            if run_manager is not None:
                run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            yield chunk
        if turn.error is not None:
            raise turn.error

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        turn = self._next_turn(messages)
        for spec in _deliverable_chunks(turn):
            chunk = ChatGenerationChunk(message=_make_chunk(spec))
            if run_manager is not None:
                await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
            yield chunk
        if turn.error is not None:
            raise turn.error
