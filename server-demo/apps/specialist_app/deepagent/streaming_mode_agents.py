"""Composable Agent abstraction built on the streaming mode framework."""

from __future__ import annotations

from typing import Any, AsyncIterator, Iterable, Callable, Union

from .streaming_mode_framework import (
    CodeBlockJSONDetector,
    PrefixModeDetector,
    StreamModeEngine,
    ModeHandler,
)


class Agent:
    """Minimal agent interface: async stream of chunks."""

    async def stream(self, chunk_stream: AsyncIterator[str] | Iterable[str] | None = None) -> AsyncIterator[str]:
        raise NotImplementedError


class _AnswerHandler(ModeHandler):
    async def on_enter(self, payload: Any, buffered: str) -> AsyncIterator[str]:
        if buffered:
            yield f"[answer] start buffered={buffered}"

    async def on_delta(self, delta: str) -> AsyncIterator[str]:
        yield f"[answer] {delta}"

    async def on_close(self) -> AsyncIterator[str]:
        yield "[answer] close"


class _SearchHandler(ModeHandler):
    def __init__(self, subagents: Iterable[Agent]) -> None:
        self.subagents = list(subagents)

    async def on_enter(self, payload: Any, buffered: str) -> AsyncIterator[str]:
        query = payload or ""
        yield f"[search] detected query={query}"
        if buffered:
            yield f"[search] buffered={buffered}"
        if not self.subagents:
            yield "[search] no subagent available"

    async def on_delta(self, delta: str) -> AsyncIterator[str]:
        yield f"[search] passthrough {delta}"

    async def on_close(self) -> AsyncIterator[str]:
        for agent in self.subagents:
            async for chunk in agent.stream():
                yield f"[search->subagent] {chunk}"
        yield "[search] close"


class _ToolHandler(ModeHandler):
    async def on_enter(self, payload: Any, buffered: str) -> AsyncIterator[str]:
        yield f"[tool] enter payload={payload}"
        if buffered:
            yield f"[tool] buffered={buffered}"

    async def on_delta(self, delta: str) -> AsyncIterator[str]:
        yield f"[tool] delta={delta}"

    async def on_close(self) -> AsyncIterator[str]:
        yield "[tool] close"


class StreamingAgent(Agent):
    """Concrete agent backed by StreamModeEngine and injected source chunks."""

    def __init__(
        self,
        *,
        name: str,
        description: str | None = None,
        system_prompt: str | None = None,
        tools: list[str] | None = None,
        chunk_source: Union[
            AsyncIterator[str],
            Iterable[str],
            Callable[[], AsyncIterator[str] | Iterable[str]],
            None,
        ] = None,
        subagents: Iterable[Agent] | None = None,
        detectors=None,
    ) -> None:
        self.name = name
        self.description = description
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.chunk_source = chunk_source
        self.subagents = list(subagents or [])
        self.engine = StreamModeEngine(
            detectors=detectors
            or [
                PrefixModeDetector("【检索】", "search"),
                CodeBlockJSONDetector("tool"),
            ],
            handlers={
                "answer": _AnswerHandler(),
                "search": _SearchHandler(self.subagents),
                "tool": _ToolHandler(),
            },
            default_mode="answer",
        )

    async def stream(self, chunk_stream: AsyncIterator[str] | Iterable[str] | None = None) -> AsyncIterator[str]:
        stream = chunk_stream
        if stream is None:
            source = self.chunk_source
            if callable(source):
                stream = source()  # type: ignore[assignment]
            else:
                stream = source

        if stream is None:
            sources = _empty_stream()
        elif hasattr(stream, "__aiter__"):
            sources = stream  # type: ignore[assignment]
        else:
            sources = _iterable_to_async(stream)  # type: ignore[arg-type]

        async for part in sources:
            async for chunk in self.engine.feed(part):
                yield f"[{self.name}] {chunk}"
        async for chunk in self.engine.finish():
            yield f"[{self.name}] {chunk}"

    def set_chunk_source(
        self,
        source: Union[
            AsyncIterator[str],
            Iterable[str],
            Callable[[], AsyncIterator[str] | Iterable[str]],
            None,
        ],
    ) -> None:
        """Assign or replace the input source for this agent."""
        self.chunk_source = source


def create_agent(
    *,
    name: str = "agent",
    description: str | None = None,
    system_prompt: str | None = None,
    tools: list[str] | None = None,
    chunk_source: Union[
        AsyncIterator[str],
        Iterable[str],
        Callable[[], AsyncIterator[str] | Iterable[str]],
        None,
    ] = None,
    subagents: Iterable[Agent] | None = None,
) -> Agent:
    """Factory to build a StreamingAgent with defaults."""
    return StreamingAgent(
        name=name,
        description=description,
        system_prompt=system_prompt,
        tools=tools,
        chunk_source=chunk_source,
        subagents=subagents or [],
    )


async def _empty_stream() -> AsyncIterator[str]:
    if False:
        yield ""  # pragma: no cover


async def _iterable_to_async(items: Iterable[str]) -> AsyncIterator[str]:
    for item in items:
        yield item
