from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class MessagePart:
    """消息分片。

    用于表达文本/工具调用/工具结果等不同类型的内容片段。
    """
    kind: str
    content: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    tool_name: str | None = None
    tool_call_id: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "kind": self.kind,
            "content": self.content,
            "data": self.data,
        }
        if self.tool_name:
            payload["tool_name"] = self.tool_name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "MessagePart":
        return cls(
            kind=str(payload.get("kind") or "text"),
            content=str(payload.get("content") or ""),
            data=dict(payload.get("data") or {}),
            tool_name=payload.get("tool_name"),
            tool_call_id=payload.get("tool_call_id"),
        )


@dataclass
class Message:
    """单条消息。

    由若干 MessagePart 组成，角色为 user/assistant/tool。
    """
    message_id: str
    role: str
    parts: List[MessagePart] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.message_id,
            "role": self.role,
            "parts": [part.to_dict() for part in self.parts],
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Message":
        parts = payload.get("parts") or []
        return cls(
            message_id=str(payload.get("id") or ""),
            role=str(payload.get("role") or ""),
            parts=[MessagePart.from_dict(item) for item in parts if isinstance(item, dict)],
            created_at=str(payload.get("created_at") or _utc_now()),
            metadata=dict(payload.get("metadata") or {}),
        )

    def append_part(self, part: MessagePart) -> None:
        self.parts.append(part)


@dataclass
class Session:
    """会话容器。

    保存本次对话的消息序列与元信息，支持序列化/反序列化和追加消息。
    - session_id: 会话唯一标识
    - parent_id: 父会话标识（子会话/子任务场景可用）
    - status: 会话状态（如 active/archived）
    - created_at/updated_at: UTC 时间字符串
    - metadata: 扩展信息（如压缩记录、上下文标签）
    - messages: Message 列表（按时间顺序）
    """
    session_id: str
    parent_id: str | None = None
    status: str = "active"
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    messages: List[Message] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "messages": [message.to_dict() for message in self.messages],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Session":
        messages = payload.get("messages") or []
        return cls(
            session_id=str(payload.get("session_id") or ""),
            parent_id=payload.get("parent_id"),
            status=str(payload.get("status") or "active"),
            created_at=str(payload.get("created_at") or _utc_now()),
            updated_at=str(payload.get("updated_at") or _utc_now()),
            metadata=dict(payload.get("metadata") or {}),
            messages=[Message.from_dict(item) for item in messages if isinstance(item, dict)],
        )

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        self.updated_at = _utc_now()

    def latest_message(self) -> Optional[Message]:
        return self.messages[-1] if self.messages else None
