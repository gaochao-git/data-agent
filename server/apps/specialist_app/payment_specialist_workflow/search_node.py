"""Node for streaming law search results."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Iterable

from ..gpts_regulation import gpts_id as REGULATION_TOOL_GROUP, search_laws
from ..tool_register import ToolStreamChunk, _TOOL_DESC_DICT
from ..trace_utils import write_trace
from .references import (
    ReferenceSnippet,
    extract_reference_snippets,
    structured_search_payload,
)
from .stream_protocol import StreamProtocol

logger = logging.getLogger(__name__)


def _unwrap_stream_content(value: Any, *, _depth: int = 0) -> str | None:
    """Peel the common {\"event\": ..., \"delta\": ...} wrapper to keep payload only."""

    if value is None:
        return None
    if _depth > 10:
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        return _unwrap_stream_content(parsed, _depth=_depth + 1)
    if isinstance(value, dict):
        for key in ("delta", "text", "content"):
            if key in value:
                return _unwrap_stream_content(value[key], _depth=_depth + 1)
        if not value:
            return None
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            normalized = _unwrap_stream_content(item, _depth=_depth + 1)
            if normalized:
                pieces.append(normalized)
        if not pieces:
            return None
        return "".join(pieces)
    return str(value)


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _format_tool_desc(template: Any, **kwargs: Any) -> str | None:
    if template is None:
        return None
    try:
        return str(template).format(**kwargs)
    except Exception:  # pylint: disable=broad-except
        return str(template)


class SearchNodeRunner:
    """Stream search results and expose collected payloads after completion."""

    def __init__(self, protocol: StreamProtocol, query: str) -> None:
        self.protocol = protocol
        self.query = query
        self.search_payload: str = ""
        self.reference_snippets: list[ReferenceSnippet] = []
        self.summary_payload: str = ""

    async def stream(self) -> Iterable[str]:
        protocol = self.protocol
        query = self.query

        tool_call_id = f"tc_{uuid.uuid4().hex}"
        tool_name = "search_laws"
        tool_args = {"query": query}
        tool_desc_bucket = _TOOL_DESC_DICT.get(REGULATION_TOOL_GROUP, {})
        tool_meta = tool_desc_bucket.get(tool_name, {})
        tool_start_desc = tool_meta.get("desc")
        tool_ing_desc = _format_tool_desc(tool_meta.get("ing_desc"), **tool_args)
        tool_end_desc = _format_tool_desc(tool_meta.get("end_desc"), **tool_args)
        tool_started_at = time.time()
        tool_node_id = f"tool_{tool_call_id}"
        tool_node_title = tool_ing_desc or tool_start_desc or "法规检索"
        write_trace(
            protocol.conversation_id,
            "tool_invocation",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": tool_args,
            },
        )
        for chunk in protocol.emit_node_start(
            tool_node_id,
            "tool",
            tool_node_title,
            flatten_children=True,
        ):
            yield chunk
        args_delta = json.dumps(tool_args, ensure_ascii=False)
        # for chunk in protocol.emit_node_delta(tool_node_id, f"检索参数：{args_delta}"):
        #     yield chunk

        tool_status = "ok"
        tool_error_payload: dict[str, Any] | None = None
        try:
            search_stream = await search_laws(query=query)
        except Exception as tool_exc:  # pylint: disable=broad-except
            logger.exception("search_laws invocation failed: %s", tool_exc)
            error_text = f"\n检索服务调用失败：{tool_exc}\n"
            async for chunk in protocol.emit_text_delta(error_text, record_visible=False):
                yield chunk
            tool_status = "error"
            tool_error_payload = {"error": str(tool_exc)}
            for chunk in protocol.emit_node_delta(
                tool_node_id,
                f"检索失败：{tool_exc}",
            ):
                yield chunk
            for chunk in protocol.emit_node_end(tool_node_id, "error", tool_end_desc or tool_node_title):
                yield chunk
            raise

        collected_search_text: list[str] = []
        suppressed_tool_events = {"message_start", "message_end", "done"}

        def _collect_structured_child_events(delta_value: Any) -> list[Any]:
            collected: list[Any] = []

            def _try_parse(value: Any) -> Any:
                if isinstance(value, str):
                    stripped = value.strip()
                    if not stripped:
                        return None
                    try:
                        return json.loads(stripped)
                    except json.JSONDecodeError:
                        return None
                return value

            def _walk(value: Any) -> bool:
                normalized_value = _try_parse(value)
                if normalized_value is None:
                    return False
                if isinstance(normalized_value, dict):
                    event_name = normalized_value.get("event")
                    if (
                        isinstance(event_name, str)
                        and event_name.strip().lower().startswith("node_")
                    ):
                        collected.append(normalized_value)
                        return True
                    inner_value = normalized_value.get("delta")
                    return _walk(inner_value)
                if isinstance(normalized_value, list):
                    handled = False
                    for item in normalized_value:
                        handled = _walk(item) or handled
                    return handled
                return False

            if _walk(delta_value):
                return collected
            return []

        async for stream_chunk in search_stream:
            delta_text: str | None = None
            payload_value: Any = None
            desc_text: str | None = None
            inner_event: str | None = None
            normalized_inner_event: str | None = None

            if isinstance(stream_chunk, ToolStreamChunk):
                delta_text = stream_chunk.delta
                payload_value = stream_chunk.payload
                desc_text = stream_chunk.desc
            elif isinstance(stream_chunk, dict):
                delta_text = stream_chunk.get("delta")
                payload_value = stream_chunk.get("payload")
                desc_text = stream_chunk.get("desc")
            else:
                delta_text = str(stream_chunk)

            if isinstance(payload_value, str):
                try:
                    parsed_payload = json.loads(payload_value)
                except json.JSONDecodeError:
                    parsed_payload = None
                else:
                    payload_value = parsed_payload

            if isinstance(payload_value, dict):
                inner_event = payload_value.get("event")
                if isinstance(inner_event, str):
                    normalized_value = inner_event.strip().lower()
                    normalized_inner_event = normalized_value or None
                else:
                    normalized_inner_event = None
                if desc_text is None:
                    desc_candidate = payload_value.get("desc") or payload_value.get("stage")
                    if isinstance(desc_candidate, str) and desc_candidate.strip():
                        desc_text = desc_candidate.strip()
                if not delta_text:
                    token_value = (
                        payload_value.get("delta")
                        or payload_value.get("text")
                        or payload_value.get("content")
                    )
                    if isinstance(token_value, str):
                        delta_text = token_value or None
                    elif token_value is not None:
                        delta_text = json.dumps(token_value, ensure_ascii=False)
            else:
                normalized_inner_event = None

            if normalized_inner_event in suppressed_tool_events:
                continue

            structured_child_events: list[Any] = []
            if isinstance(payload_value, dict):
                nested_delta_value = payload_value.get("delta")
                structured_child_events = _collect_structured_child_events(nested_delta_value)
                for nested_event in structured_child_events:
                    serialized_event = json.dumps(nested_event, ensure_ascii=False)
                    for chunk in protocol.emit_node_delta(tool_node_id, serialized_event):
                        yield chunk

            display_text = desc_text or (delta_text if not structured_child_events else None)
            if not display_text and isinstance(payload_value, (dict, list)):
                display_text = json.dumps(payload_value, ensure_ascii=False)
            elif display_text is None and payload_value is not None:
                display_text = str(payload_value)
            emit_delta_for_display = normalized_inner_event != "content_delta"
            if emit_delta_for_display and display_text:
                is_ephemeral = bool(
                    normalized_inner_event in {"status", "progress", "debug"}
                )
                for chunk in protocol.emit_node_delta(
                    tool_node_id,
                    str(display_text),
                    ephemeral=is_ephemeral,
                ):
                    yield chunk

            if structured_child_events:
                delta_text = None

            if not delta_text:
                continue

            # Only record actual content tokens; skip envelope events such as message_start.
            should_record = normalized_inner_event is None
            if normalized_inner_event == "content_delta":
                should_record = True
            elif normalized_inner_event in {"status", "progress", "debug"}:
                should_record = False
            elif normalized_inner_event:
                should_record = False

            if should_record:
                normalized_delta = _unwrap_stream_content(delta_text)
                if normalized_delta:
                    collected_search_text.append(normalized_delta)

        search_payload = "".join(collected_search_text).strip()
        reference_snippets = extract_reference_snippets(search_payload)
        summary_payload = (
            structured_search_payload(search_payload, snippets=reference_snippets or None)
            or search_payload
        )
        latency_ms = int((time.time() - tool_started_at) * 1000)
        end_desc = tool_end_desc
        if tool_status == "ok" and not end_desc:
            end_desc = "法规检索完成"
        if not end_desc:
            end_desc = tool_node_title
        if tool_status != "ok":
            for chunk in protocol.emit_node_delta(
                tool_node_id,
                f"检索失败：{_json_safe(tool_error_payload)}",
            ):
                yield chunk
        # else:
        #     for chunk in protocol.emit_node_delta(
        #         tool_node_id,
        #         f"检索耗时 {latency_ms} ms，找到 {len(reference_snippets) if reference_snippets else 0} 条引用",
        #     ):
        #         yield chunk
        for chunk in protocol.emit_node_end(
            tool_node_id,
            "completed" if tool_status == "ok" else "error",
            end_desc,
        ):
            yield chunk

        self.search_payload = search_payload
        self.reference_snippets = reference_snippets or []
        self.summary_payload = summary_payload
        write_trace(
            protocol.conversation_id,
            "tool_result",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "status": tool_status,
                "latency_ms": latency_ms,
                "output": search_payload,
                "reference_count": len(self.reference_snippets),
            },
        )

        print(f"summary_payload:{summary_payload}")


__all__ = ["SearchNodeRunner"]
