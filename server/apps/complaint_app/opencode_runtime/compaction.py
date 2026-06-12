from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict, List

from .session import Message
from .token_estimator import TokenEstimator
from utils.logger import get_logger

_ESTIMATOR = TokenEstimator()
logger = get_logger(__name__)


def _trace_enabled() -> bool:
    return os.getenv("OPENCODE_RUNTIME_TRACE", "").strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class CompactionLimits:
    """压缩阈值计算器。

    根据模型上限计算触发阈值与压缩目标大小。
    """
    threshold: float = 0.92
    target_ratio: float = 0.75

    def trigger_tokens(self, max_tokens: int) -> int:
        if max_tokens <= 0:
            return 0
        return int(max_tokens * self.threshold)

    def target_tokens(self, max_tokens: int) -> int:
        if max_tokens <= 0:
            return 0
        return int(max_tokens * self.target_ratio)

    def should_compact(self, total_tokens: int, max_tokens: int) -> bool:
        trigger = self.trigger_tokens(max_tokens)
        if trigger <= 0:
            return False
        return total_tokens > trigger


@dataclass
class CompactionResult:
    """压缩结果。"""
    messages: List[Message]
    summary: str = ""
    metadata: Dict[str, Any] | None = None


def estimate_tokens(text: str, model: str | None = None) -> int:
    return _ESTIMATOR.count_text(text or "", model=model)


def _count_total_tokens(messages: List[Message], model: str | None = None) -> int:
    total = 0
    parts_count = 0
    for msg in messages:
        for part in msg.parts:
            total += estimate_tokens(part.content, model=model)
            parts_count += 1
    if _trace_enabled():
        logger.info("[compaction] count_total_tokens model=%s messages=%s parts=%s total_tokens=%s", model, len(messages), parts_count, total)
    return total


def should_compact(messages: List[Message], limits: CompactionLimits, max_tokens: int, model: str | None = None) -> bool:
    if max_tokens <= 0:
        if _trace_enabled():
            logger.info("[compaction] should_compact decision=false reason=max_tokens_non_positive max_tokens=%s", max_tokens)
        return False
    total = _count_total_tokens(messages, model=model)
    decision = limits.should_compact(total, max_tokens)
    if _trace_enabled():
        logger.info("[compaction] should_compact decision=%s model=%s total_tokens=%s trigger_tokens=%s max_tokens=%s", decision, model, total, limits.trigger_tokens(max_tokens), max_tokens)
    return decision


def compact_messages(messages: List[Message], limits: CompactionLimits, max_tokens: int, model: str | None = None) -> CompactionResult:
    if max_tokens <= 0:
        total = _count_total_tokens(messages, model=model)
        if _trace_enabled():
            logger.info("[compaction] compact_messages compacted=false reason=max_tokens_non_positive max_tokens=%s total_tokens=%s", max_tokens, total)
        return CompactionResult(messages=messages, summary="", metadata={"compacted": False, "token_count": total})
    total = _count_total_tokens(messages, model=model)
    if not limits.should_compact(total, max_tokens):
        if _trace_enabled():
            logger.info("[compaction] compact_messages compacted=false reason=below_threshold total_tokens=%s trigger_tokens=%s max_tokens=%s", total, limits.trigger_tokens(max_tokens), max_tokens)
        return CompactionResult(messages=messages, summary="", metadata={"compacted": False, "token_count": total})

    target_tokens = limits.target_tokens(max_tokens)
    if target_tokens <= 0:
        if _trace_enabled():
            logger.info("[compaction] compact_messages compacted=false reason=target_tokens_non_positive target_tokens=%s max_tokens=%s", target_tokens, max_tokens)
        return CompactionResult(messages=messages, summary="", metadata={"compacted": False, "token_count": total})

    kept: List[Message] = []
    kept_tokens = 0
    for msg in reversed(messages):
        msg_tokens = 0
        for part in msg.parts:
            msg_tokens += estimate_tokens(part.content, model=model)
        if kept_tokens + msg_tokens > target_tokens and kept:
            break
        kept.append(msg)
        kept_tokens += msg_tokens

    kept = list(reversed(kept))
    removed = len(messages) - len(kept)
    summary = f"Compacted {removed} messages." if removed > 0 else ""
    metadata = {
        "compacted": True,
        "token_count": total,
        "kept": len(kept),
        "removed": removed,
        "target_tokens": target_tokens,
    }
    if _trace_enabled():
        logger.info("[compaction] compact_messages compacted=true total_tokens=%s target_tokens=%s kept=%s removed=%s", total, target_tokens, len(kept), removed)
    return CompactionResult(messages=kept, summary=summary, metadata=metadata)


class Compressor:
    """上下文压缩器（基于 CompactionLimits）。"""

    def __init__(self, limits: CompactionLimits | None = None, max_tokens: int = 0) -> None:
        self._limits = limits or CompactionLimits()
        self._max_tokens = max_tokens

    @property
    def limits(self) -> CompactionLimits:
        """返回当前压缩阈值设置。"""
        return self._limits

    def compress(self, messages: List[Message], *, max_tokens: int | None = None) -> CompactionResult:
        """执行压缩并返回结果。"""
        return compact_messages(messages, self._limits, max_tokens if max_tokens is not None else self._max_tokens)
