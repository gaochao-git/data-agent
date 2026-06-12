from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from apps.complaint_app.history_storage import append_upload_stream_event


class ComplaintSSEEvent:
    """
    标准化的 SSE 事件载体，涵盖四个阶段通用字段，允许附加扩展字段。
    负责自身到 dict 的转换以及生成 SSE 字符串。
    """

    def __init__(
        self,
        *,
        base_id: Optional[str] = None,
        task: Optional[Any] = None,
        status: Optional[str] = None,
        message: Optional[str] = None,
        agent_say: Optional[str] = None,
        progress: Optional[float] = None,
        ts: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.base_id = base_id
        self.task = task
        self.status = status
        self.message = message
        self.agent_say = agent_say
        self.progress = progress
        self.ts = ts
        self.extra = extra or {}

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.task is not None:
            payload["task"] = self.task
        if self.status is not None:
            payload["status"] = self.status
        if self.message is not None:
            payload["message"] = self.message
        if self.agent_say is not None:
            payload["agent_say"] = self.agent_say
        if self.progress is not None:
            payload["progress"] = self.progress
        if self.ts is not None:
            payload["ts"] = self.ts
        payload.update(self.extra)
        if self.base_id:
            payload.setdefault("base_id", self.base_id)
        return payload

    def to_sse(self, *, user_id: str | None = None) -> str:
        payload = self.to_dict()
        base_id = payload.get("base_id")
        if base_id:
            append_upload_stream_event(user_id, base_id, payload)
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_standard_sse_payload(
    *,
    event: str,
    message_id: str,
    conversation_id: str,
    response_id: str | None = None,
    created: int | None = None,
    node_id: str | None = None,
    delta: str | None = None,
    line_id: str | None = None,
    title: str | None = None,
    type: str | None = None,
    status: str | None = None,
    index: int | None = None,
    role: str | None = None,
    model: str | None = None,
    label_hidden: bool | None = None,
) -> Dict[str, Any]:
    created_ts = created if created is not None else int(time.time() * 1000)
    items: list[tuple[str, Any]] = [("event", event)]
    if node_id is not None:
        items.append(("node_id", node_id))
    if delta is not None:
        items.append(("delta", delta))
    if line_id is not None:
        items.append(("line_id", line_id))
    if title is not None:
        items.append(("title", title))
    if type is not None:
        items.append(("type", type))
    if status is not None:
        items.append(("status", status))
    items.append(("message_id", message_id))
    items.append(("conversation_id", conversation_id))
    if response_id is not None:
        items.append(("response_id", response_id))
    items.append(("created", created_ts))
    if index is not None:
        items.append(("index", index))
    if role is not None:
        items.append(("role", role))
    if model is not None:
        items.append(("model", model))
    if label_hidden is not None:
        items.append(("label_hidden", label_hidden))
    return dict(items)
