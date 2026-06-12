from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import List

from .compaction import CompactionLimits, CompactionResult, compact_messages, estimate_tokens, should_compact
from .config import RuntimeConfig
from .session import Message, MessagePart, Session
from apps.complaint_app.core.llm.models_info import get_model_max_tokens


@dataclass
class ContextStats:
    """上下文统计信息。"""

    token_count: int
    max_tokens: int
    threshold: float
    usage_ratio: float


class ContextManager:
    """上下文管理器：统计 tokens、触发压缩、追加消息。"""

    def __init__(self, limits: CompactionLimits | None = None) -> None:
        cfg = RuntimeConfig.from_env()
        threshold = cfg.compaction_threshold if cfg.compaction_threshold > 0 else 0.92
        target_ratio = cfg.compaction_target_ratio if cfg.compaction_target_ratio > 0 else 0.75
        self._limits = limits or CompactionLimits(threshold=threshold, target_ratio=target_ratio)

    @property
    def limits(self) -> CompactionLimits:
        """返回当前阈值设置。"""
        return self._limits

    def _model_max_tokens(self, model: str | None) -> int:
        model_max = get_model_max_tokens(model)
        return model_max if model_max and model_max > 0 else 0

    def stats(self, session: Session, *, model: str | None = None) -> ContextStats:
        """统计当前 session 的 token 使用情况。"""
        total = 0
        for msg in session.messages:
            for part in msg.parts:
                total += estimate_tokens(part.content, model=model)
        max_tokens = self._model_max_tokens(model)
        ratio = total / max_tokens if max_tokens else 0.0
        return ContextStats(
            token_count=total,
            max_tokens=max_tokens,
            threshold=self._limits.threshold,
            usage_ratio=ratio,
        )

    def should_compact(self, session: Session, *, model: str | None = None) -> bool:
        """判断是否需要压缩。"""
        max_tokens = self._model_max_tokens(model)
        return should_compact(session.messages, self._limits, max_tokens, model=model)

    def compact(self, session: Session, *, model: str | None = None) -> CompactionResult:
        """执行压缩并更新 session 消息。"""
        max_tokens = self._model_max_tokens(model)
        result = compact_messages(session.messages, self._limits, max_tokens, model=model)
        session.messages = result.messages
        if result.metadata:
            session.metadata.setdefault("compaction", {})
            session.metadata["compaction"] = result.metadata
        return result

    def compact_if_needed(self, session: Session, *, model: str | None = None) -> CompactionResult | None:
        """达到阈值时执行压缩，否则返回 None。"""
        if not self.should_compact(session, model=model):
            return None
        return self.compact(session, model=model)

    def append_user_message(self, session: Session, content: str) -> Message:
        """追加用户消息到 session。"""
        msg = Message(
            message_id=str(uuid.uuid4()),
            role="user",
            parts=[MessagePart(kind="text", content=content)],
        )
        session.add_message(msg)
        return msg

    def append_assistant_message(self, session: Session, content: str) -> Message:
        """追加助手消息到 session。"""
        msg = Message(
            message_id=str(uuid.uuid4()),
            role="assistant",
            parts=[MessagePart(kind="text", content=content)],
        )
        session.add_message(msg)
        return msg

    def append_tool_message(self, session: Session, *, tool_name: str, output: str, tool_call_id: str | None = None) -> Message:
        """追加工具消息到 session。"""
        msg = Message(
            message_id=str(uuid.uuid4()),
            role="tool",
            parts=[
                MessagePart(
                    kind="tool",
                    content=output,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                )
            ],
            metadata={"tool": tool_name, "tool_call_id": tool_call_id},
        )
        session.add_message(msg)
        return msg


__all__ = ["ContextManager", "ContextStats"]
