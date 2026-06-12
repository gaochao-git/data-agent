from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Iterable


class StreamProtocol:
    """Utility to standardize streaming SSE events and thinking traces."""

    def __init__(
        self,
        *,
        conversation_id: str,
        response_id: str,
        message_id: str,
        model_name: str | None,
        assistant_visible_parts: list[str] | None = None,
        thinking_trace: list[dict[str, Any]] | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.response_id = response_id
        self.message_id = message_id
        self.model_name = model_name
        self.assistant_visible_parts = assistant_visible_parts or []
        self.thinking_trace = thinking_trace or []
        self.node_registry: dict[str, dict[str, Any]] = {}
        self._message_started = False
        self._node_line_counters: dict[str, int] = {}
        self.references: list[dict[str, Any]] = []

    def _allocate_line_id(self, node_id: str) -> str:
        """Allocate a monotonically increasing line id per node."""
        counter = self._node_line_counters.get(node_id, 0) + 1
        self._node_line_counters[node_id] = counter
        return f"{node_id}-line-{counter}"

    def build_event(
        self,
        event_name: str,
        *,
        include_message_id: bool = True,
        thinking_record: dict[str, Any] | None = None,
        **payload: Any,
    ) -> str:
        if event_name == "references":
            refs = payload.get("references")
            if isinstance(refs, list):
                self.references = refs
        event_payload = {
            "event": event_name,
            "created": int(time.time() * 1000),
            "response_id": self.response_id,
        }
        if include_message_id:
            event_payload["message_id"] = self.message_id
        if self.conversation_id:
            event_payload["conversation_id"] = self.conversation_id
        event_payload.update({k: v for k, v in payload.items() if v is not None})
        if thinking_record:
            record = {"event": event_name, **thinking_record}
            record.setdefault("created", event_payload["created"])
            self.thinking_trace.append(record)
        return f"data: {json.dumps(event_payload, ensure_ascii=False)}\n\n"

    def ensure_message_start(self) -> str | None:
        if self._message_started:
            return None
        self._message_started = True
        return self.build_event(
            "message_start",
            role="assistant",
            model=self.model_name,
        )

    async def emit_text_delta(
        self,
        text: str,
        *,
        record_visible: bool,
        thinking_type: str | None = None,
        event_name: str = "content_delta",
        extra_payload: dict[str, Any] | None = None,
        thinking_record_extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        if not text:
            return
        thinking_record = None
        if thinking_type or thinking_record_extra:
            thinking_record = {"type": thinking_type} if thinking_type else {}
            if text.strip():
                thinking_record.setdefault("content", text.strip())
            if thinking_record_extra:
                thinking_record.update(
                    {k: v for k, v in thinking_record_extra.items() if v is not None}
                )
        payload: dict[str, Any] = {"delta": text}
        if event_name == "content_delta":
            payload["index"] = 0
            payload["node_id"] = None
            payload["role"] = "assistant"
        if extra_payload:
            payload.update({k: v for k, v in extra_payload.items() if v is not None})
        yield self.build_event(event_name, thinking_record=thinking_record, **payload)
        if record_visible and event_name == "content_delta":
            self.assistant_visible_parts.append(text)

    def remember_node(self, node_id: str, *, node_type: str | None = None, title: str | None = None) -> None:
        meta = self.node_registry.setdefault(node_id, {})
        if node_type:
            meta["type"] = node_type
        if title:
            meta["title"] = title

    def _node_thinking_record(
        self,
        node_id: str,
        stage: str,
        *,
        content: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        meta = self.node_registry.get(node_id, {})
        record: dict[str, Any] = {"node_id": node_id, "stage": stage}
        if "type" in meta:
            record["node_type"] = meta["type"]
        if "title" in meta:
            record["title"] = meta["title"]
        if content:
            record["content"] = content
        if status:
            record["status"] = status
        return record

    def emit_node_event(
        self,
        event_name: str,
        *,
        node_id: str,
        thinking_payload: dict[str, Any],
        **payload: Any,
    ) -> Iterable[str]:
        yield self.build_event(event_name, thinking_record=thinking_payload, node_id=node_id, **payload)

    def emit_node_start(
        self,
        node_id: str,
        node_type: str,
        title: str,
        *,
        label_hidden: bool | None = None,
        flatten_children: bool | None = None,
    ) -> Iterable[str]:
        self.remember_node(node_id, node_type=node_type, title=title)
        thinking_payload = self._node_thinking_record(node_id, "start")
        thinking_payload["node_type"] = node_type
        thinking_payload["title"] = title
        if label_hidden is not None:
            thinking_payload["label_hidden"] = label_hidden
        if flatten_children is not None:
            thinking_payload["flatten_children"] = flatten_children
        yield from self.emit_node_event(
            "node_start",
            node_id=node_id,
            thinking_payload=thinking_payload,
            label_hidden=label_hidden,
            flatten_children=flatten_children,
            type=node_type,
            title=title,
        )

    def emit_node_delta(
        self,
        node_id: str,
        delta_value: Any,
        *,
        ephemeral: bool = False,
        line_id: str | None = None,
        label_hidden: bool | None = None,
        flatten_children: bool | None = None,
    ) -> Iterable[str]:
        if delta_value is None:
            return []
        line_identifier = line_id or self._allocate_line_id(node_id)
        content_text: str | None = None
        if isinstance(delta_value, str):
            content_text = delta_value.strip()
            if not content_text:
                return []
        thinking_payload = self._node_thinking_record(node_id, "delta", content=content_text)
        thinking_payload["line_id"] = line_identifier
        if label_hidden is not None:
            thinking_payload["label_hidden"] = label_hidden
        if flatten_children is not None:
            thinking_payload["flatten_children"] = flatten_children
        payload: dict[str, Any] = {"delta": delta_value, "line_id": line_identifier}
        if ephemeral:
            payload["ephemeral"] = True
            thinking_payload["ephemeral"] = True
        if label_hidden is not None:
            payload["label_hidden"] = label_hidden
        if flatten_children is not None:
            payload["flatten_children"] = flatten_children
        yield from self.emit_node_event(
            "node_delta",
            node_id=node_id,
            thinking_payload=thinking_payload,
            **payload,
        )

    def emit_node_end(
        self,
        node_id: str,
        status: str = "completed",
        title: str | None = None,
        *,
        label_hidden: bool | None = None,
        flatten_children: bool | None = None,
    ) -> Iterable[str]:
        if title:
            self.remember_node(node_id, title=title)
        meta = self.node_registry.get(node_id, {})
        resolved_title = title or meta.get("title")
        thinking_payload = self._node_thinking_record(node_id, "end", status=status)
        if label_hidden is not None:
            thinking_payload["label_hidden"] = label_hidden
        if flatten_children is not None:
            thinking_payload["flatten_children"] = flatten_children
        payload: dict[str, Any] = {"status": status}
        if resolved_title:
            payload["title"] = resolved_title
        if label_hidden is not None:
            payload["label_hidden"] = label_hidden
        if flatten_children is not None:
            payload["flatten_children"] = flatten_children
        yield from self.emit_node_event(
            "node_end",
            node_id=node_id,
            thinking_payload=thinking_payload,
            **payload,
        )

    def get_final_text(self) -> str:
        """Combine all visible assistant parts into a single response string."""
        return "".join(self.assistant_visible_parts).strip()

    def get_final_thinking(self) -> list[dict[str, Any]]:
        """
        Return the accumulated thinking trace records with node_delta chunks
        merged by node_id + line_id. Ephemeral records are dropped.
        """
        merged: list[dict[str, Any]] = []
        line_map: dict[tuple[str, str], dict[str, Any]] = {}

        for record in self.thinking_trace:
            if not isinstance(record, dict):
                continue
            event_name = record.get("event")
            if event_name != "node_delta":
                merged.append(dict(record))
                continue

            if record.get("ephemeral") is True:
                # Do not persist ephemeral records.
                continue

            node_id = record.get("node_id")
            line_identifier = record.get("line_id")
            if not node_id or not isinstance(node_id, str) or not node_id.strip():
                merged.append(dict(record))
                continue
            if not line_identifier or not isinstance(line_identifier, str) or not line_identifier.strip():
                merged.append(dict(record))
                continue

            key = (node_id, line_identifier)
            content_piece = record.get("content")
            if isinstance(content_piece, str):
                content_piece = content_piece.strip()
            else:
                content_piece = "" if content_piece is None else str(content_piece)

            existing = line_map.get(key)
            if existing:
                existing_content = existing.get("content")
                if isinstance(existing_content, str):
                    existing["content"] = f"{existing_content}{content_piece}"
                else:
                    existing["content"] = content_piece
                continue

            new_record = dict(record)
            new_record["content"] = content_piece
            merged.append(new_record)
            line_map[key] = new_record

        return merged


__all__ = ["StreamProtocol"]
