"""Shared streaming mode engine for buffered token inspection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Protocol

# Sentinel to tell the engine a detector needs more tokens before deciding.
WAIT = object()


@dataclass
class DetectionResult:
    mode: str
    payload: Any = None
    flush_buffer: bool = True
    consume_buffer: bool = True


class ModeDetector(Protocol):
    def inspect(self, buffered: str) -> DetectionResult | object | None:
        ...


class ModeHandler(Protocol):
    async def on_enter(self, payload: Any, buffered: str) -> AsyncIterator[Any]:
        ...

    async def on_delta(self, delta: str) -> AsyncIterator[Any]:
        ...

    async def on_close(self) -> AsyncIterator[Any]:
        ...


class StreamModeEngine:
    """Minimal state machine that buffers tokens until a mode is confirmed."""

    def __init__(
        self,
        detectors: Iterable[ModeDetector],
        handlers: dict[str, ModeHandler],
        *,
        default_mode: str = "answer",
        undecided_mode: str = "undecided",
    ) -> None:
        self.detectors = list(detectors)
        self.handlers = handlers
        self.default_mode = default_mode
        self.undecided_mode = undecided_mode
        self.mode = undecided_mode
        self.buffer: list[str] = []

    async def feed(self, delta: str) -> AsyncIterator[Any]:
        """Consume one token/text chunk and emit handler outputs."""
        if self.mode != self.undecided_mode:
            async for item in self._dispatch_delta(delta):
                yield item
            return

        self.buffer.append(delta)
        buffered_text = "".join(self.buffer)
        decision = None
        waiting = False
        for detector in self.detectors:
            verdict = detector.inspect(buffered_text)
            if verdict is WAIT:
                waiting = True
                continue
            if isinstance(verdict, DetectionResult):
                decision = verdict
                break

        if decision is None and not waiting and self.default_mode:
            decision = DetectionResult(self.default_mode, payload=None, flush_buffer=True, consume_buffer=True)

        if decision:
            async for item in self._activate_mode(decision, buffered_text):
                yield item

    async def finish(self) -> AsyncIterator[Any]:
        """Force flush remaining buffer and close current handler."""
        if self.mode == self.undecided_mode and self.default_mode:
            buffered_text = "".join(self.buffer)
            self.buffer.clear()
            decision = DetectionResult(self.default_mode, payload=None, flush_buffer=True, consume_buffer=True)
            async for item in self._activate_mode(decision, buffered_text):
                yield item

        handler = self.handlers.get(self.mode)
        if handler:
            async for item in handler.on_close():
                yield item

    async def _dispatch_delta(self, delta: str) -> AsyncIterator[Any]:
        handler = self.handlers.get(self.mode)
        if handler:
            async for item in handler.on_delta(delta):
                yield item

    async def _activate_mode(self, decision: DetectionResult, buffered_text: str) -> AsyncIterator[Any]:
        self.mode = decision.mode
        handler = self.handlers.get(self.mode)
        if decision.consume_buffer:
            self.buffer.clear()
        flush_text = buffered_text if decision.flush_buffer else ""

        if handler:
            async for item in handler.on_enter(decision.payload, flush_text):
                yield item
        if flush_text and handler:
            async for item in handler.on_delta(flush_text):
                yield item


class PrefixModeDetector:
    """Detects replies that start with a fixed prefix after trimming leading spaces."""

    def __init__(self, prefix: str, mode: str) -> None:
        self.prefix = prefix
        self.mode = mode

    def inspect(self, buffered: str) -> DetectionResult | object | None:
        trimmed = buffered.lstrip()
        if not trimmed:
            return WAIT
        if trimmed.startswith(self.prefix):
            payload = trimmed[len(self.prefix) :].strip()
            return DetectionResult(self.mode, payload=payload)
        if self.prefix.startswith(trimmed):
            return WAIT
        return None


class CodeBlockJSONDetector:
    """Detects fenced or bare JSON blocks that contain a required key."""

    def __init__(self, mode: str, required_key: str = "tool_call") -> None:
        self.mode = mode
        self.required_key = required_key

    def inspect(self, buffered: str) -> DetectionResult | object | None:
        trimmed = buffered.lstrip()
        if not trimmed:
            return WAIT

        candidate = None
        if "```" in trimmed:
            if trimmed.count("```") < 2:
                return WAIT
            try:
                fenced = trimmed.split("```", 2)[1]
            except IndexError:
                fenced = ""
            if fenced.startswith("json"):
                fenced = fenced.split("json", 1)[1]
            candidate = fenced.strip()
        elif trimmed.startswith("{"):
            if trimmed.count("}") == 0:
                return WAIT
            candidate = trimmed

        if not candidate or self.required_key not in candidate:
            return None

        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None

        return DetectionResult(self.mode, payload=payload)
