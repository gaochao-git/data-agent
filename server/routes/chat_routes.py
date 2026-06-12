from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from typing import Any, AsyncGenerator, Dict, Iterable, Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from starlette.datastructures import FormData, UploadFile as StarletteUploadFile

from auth import AuthenticationError, authenticate_request
from apps.complaint_app.services.text.text_pipeline import TextPipeline
from apps.complaint_app.core.versioning import load_manifest, save_manifest, stage_dir
from apps.complaint_app.services.chats.chat import stream_chat
from apps.complaint_app.history_storage import append_chat_sse_event, record_chat_assistant_message, record_chat_user_message, relative_to_complaints, touch_user_history
from apps.complaint_app.services.chats.state import create_cancel_token, is_cancelled, pop_cancel_token
from apps.complaint_app.services.chats.suggestions import get_default_complaint_suggestions
from apps.complaint_app.services.chat_service import fetch_chat_messages
from apps.complaint_app.services.analysis_agent.llm_fc import function_calling_streaming
from apps.complaint_app.services.analysis_agent.agent import run_complaint_analysis
from apps.complaint_app.services.analysis_agent.fc_tools import TOOLS as COMPLAINT_FC_TOOLS, FUNCTION_MAPPER as COMPLAINT_FC_FUNCTION_MAPPER
from apps.specialist_app.conversation_history import get_conversation_detail_for_user as get_expert_conversation_detail
from apps.specialist_app.ai_chat import stream_ai_chat_response
from utils.logger import get_logger
from utils.storage_paths import COMPLAINTS_BASE_DIR, get_complaints_real_path, get_user_dir, validate_base_id

logger = get_logger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

_ALLOWED_AGENTS = {"complaint_agent", "expert_agent"}


@router.get("/conversations/{conversation_id}/messages")
async def get_chat_conversation_messages(conversation_id: str, request: Request, agent_id: Optional[str] = None):
    current_user = _get_current_user(request)
    normalized_id = (conversation_id or "").strip()
    if not normalized_id:
        raise HTTPException(status_code=400, detail="conversation_id 不能为空")

    if agent_id == "expert_agent":
        detail = get_expert_conversation_detail(current_user, normalized_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"messages": _map_expert_messages(detail)}

    if agent_id == "complaint_agent":
        payload, status = fetch_chat_messages(
            user_id=str(current_user.user_id),
            base_id=normalized_id,
            conversation_id=normalized_id,
            first_id="",
            limit=200,
            latest_conversation_fetcher=lambda *_args, **_kwargs: normalized_id,
        )
        if status >= 400:
            raise HTTPException(status_code=status, detail=payload.get("error") or "获取历史失败")
        return {
            "messages": _map_complaint_messages(payload),
            "artifacts": _build_complaint_artifacts(str(current_user.user_id), normalized_id),
        }

    detail = get_expert_conversation_detail(current_user, normalized_id)
    if detail is not None:
        return {"messages": _map_expert_messages(detail)}

    payload, status = fetch_chat_messages(
        user_id=str(current_user.user_id),
        base_id=normalized_id,
        conversation_id=normalized_id,
        first_id="",
        limit=200,
        latest_conversation_fetcher=lambda *_args, **_kwargs: normalized_id,
    )
    if status >= 400:
        raise HTTPException(status_code=status, detail=payload.get("error") or "获取历史失败")
    return {"messages": _map_complaint_messages(payload)}


def _map_segments_to_steps(segments: list[dict]) -> list[dict]:
    steps: list[dict] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_type = str(seg.get("type") or "").strip()
        label = str(seg.get("title") or seg.get("label") or seg_type or "处理中")
        status_raw = str(seg.get("status") or "").lower()
        status = "done" if status_raw in {"end", "done", "completed"} else "thinking"
        detail = seg.get("markdown") if isinstance(seg.get("markdown"), str) else None
        steps.append(
            {
                "id": seg.get("id") or seg_type or label,
                "label": label,
                "status": status,
                "detailLines": [{"content": detail}] if detail else [],
            }
        )
    return steps


def _build_expert_steps(thinking: list[dict]) -> list[dict]:
    def ensure_step(store: dict[str, dict], order: list[dict], step_id: str, label: str) -> dict:
        step = store.get(step_id)
        if step:
            return step
        step = {"id": step_id, "label": label or "处理中", "status": "thinking"}
        store[step_id] = step
        order.append(step)
        return step

    def ensure_child(parent: dict, child_id: str, label: str) -> dict:
        children = parent.setdefault("children", [])
        index = parent.setdefault("_child_index", {})
        child = index.get(child_id)
        if child:
            return child
        child = {"id": child_id, "label": label or "处理中", "status": "thinking"}
        index[child_id] = child
        children.append(child)
        return child

    def append_detail_line(step: dict, content: str, line_id: str | None = None) -> None:
        if not content:
            return
        lines = step.get("detailLines") or []
        if line_id:
            for idx, line in enumerate(lines):
                if isinstance(line, dict) and line.get("lineId") == line_id:
                    lines[idx] = {"lineId": line_id, "content": content}
                    step["detailLines"] = lines
                    return
            lines.append({"lineId": line_id, "content": content})
        else:
            lines.append({"content": content})
        step["detailLines"] = lines

    def set_detail(step: dict, content: str, line_id: str | None = None) -> None:
        if not content:
            return
        append_detail_line(step, content, line_id)

    def is_node_event(ev: dict) -> bool:
        return str(ev.get("event") or "").startswith("node_")

    def handle_event(ev: dict, *, parent_id: str | None = None) -> None:
        event_name = str(ev.get("event") or "")
        node_id = str(ev.get("node_id") or "")
        if not node_id:
            return
        title = str(ev.get("title") or "") or "处理中"
        node_type = str(ev.get("node_type") or ev.get("type") or "")
        label_hidden = ev.get("label_hidden")
        flatten_children = ev.get("flatten_children")
        if parent_id:
            parent = steps_index.get(parent_id)
            if not parent:
                parent = ensure_step(steps_index, steps_order, parent_id, "处理中")
            step = ensure_child(parent, node_id, title)
        else:
            step = ensure_step(steps_index, steps_order, node_id, title)

        if label_hidden is not None:
            step["label_hidden"] = label_hidden
        if flatten_children is not None:
            step["flatten_children"] = flatten_children

        if event_name == "node_start":
            if title:
                step["label"] = title
            return

        if event_name == "node_delta":
            content = ev.get("content")
            if content is None:
                content = ev.get("delta")
            line_id = ev.get("line_id")
            if isinstance(content, str) and content.strip():
                if parent_id:
                    append_detail_line(step, content.strip(), line_id)
                elif node_type == "thinking":
                    set_detail(step, content.strip(), line_id)
                else:
                    append_detail_line(step, content.strip(), line_id)
            return

        if event_name == "node_end":
            status = str(ev.get("status") or "").lower()
            if status in {"completed", "done", "end"}:
                step["status"] = "done"
            if title:
                step["label"] = title
            return

    steps_order: list[dict] = []
    steps_index: dict[str, dict] = {}

    for step in thinking:
        if not isinstance(step, dict):
            continue
        if not is_node_event(step):
            continue
        if step.get("event") == "node_delta":
            raw_content = step.get("content")
            if isinstance(raw_content, str):
                trimmed = raw_content.strip()
                if trimmed.startswith("{") and trimmed.endswith("}"):
                    try:
                        nested = json.loads(trimmed)
                    except Exception:
                        nested = None
                    if isinstance(nested, dict) and is_node_event(nested):
                        handle_event(nested, parent_id=str(step.get("node_id") or ""))
                        continue
        handle_event(step)

    for step in steps_order:
        if "_child_index" in step:
            step.pop("_child_index", None)
    return steps_order


def _map_expert_messages(detail: dict) -> list[dict]:
    messages: list[dict] = []
    for msg in detail.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") if msg.get("role") in {"user", "assistant"} else "assistant"
        content = msg.get("content") if isinstance(msg.get("content"), str) else ""
        steps = []
        thinking = msg.get("thinking")
        if isinstance(thinking, list):
            if any(isinstance(item, dict) and str(item.get("event") or "").startswith("node_") for item in thinking):
                steps = _build_expert_steps(thinking)
            else:
                steps = _map_segments_to_steps(thinking)
        references = msg.get("references")
        references_payload = references if isinstance(references, list) else []
        messages.append(
            {
                "message_id": msg.get("id") or str(uuid.uuid4()),
                "role": role,
                "content": content,
                "text": content,
                "time": msg.get("timestamp") or "",
                **({"steps": steps} if steps else {}),
                **({"references": references_payload} if references_payload else {}),
            }
        )
    return messages


def _map_complaint_messages(payload: dict) -> list[dict]:
    messages: list[dict] = []
    for item in payload.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role") if item.get("role") in {"user", "assistant"} else "assistant"
        content = item.get("answer") if role == "assistant" else item.get("query")
        if not isinstance(content, str):
            content = ""
        steps = []
        snapshot = item.get("snapshot")
        if isinstance(snapshot, dict):
            steps_value = snapshot.get("steps")
            if isinstance(steps_value, list) and steps_value:
                steps = steps_value
            else:
                segments = snapshot.get("segments")
                if isinstance(segments, list):
                    has_think = any(
                        isinstance(seg, dict)
                        and str(seg.get("type") or "").strip() in {"thought", "thinking", "tool"}
                        for seg in segments
                    )
                    if has_think:
                        steps = _map_segments_to_steps(segments)
        attachments: list[dict] = []
        raw_attachments = item.get("attachments")
        if isinstance(raw_attachments, list):
            for idx, attachment in enumerate(raw_attachments):
                if not isinstance(attachment, dict):
                    continue
                name = (
                    attachment.get("original_filename")
                    or attachment.get("stored_filename")
                    or attachment.get("name")
                    or attachment.get("filename")
                    or attachment.get("url")
                )
                if not isinstance(name, str) or not name.strip():
                    continue
                attach_id = attachment.get("id") or attachment.get("stored_filename") or f"att-{idx}"
                attachments.append({"id": str(attach_id), "name": name})

        payload_msg = {
            "message_id": item.get("id") or str(uuid.uuid4()),
            "role": role,
            "content": content,
            "text": content,
            "time": item.get("created_at") or "",
            **({"steps": steps} if steps else {}),
        }
        if isinstance(snapshot, dict):
            suggestions = snapshot.get("suggestions")
            if isinstance(suggestions, list) and suggestions:
                payload_msg["suggestions"] = suggestions
        reference_blocks = item.get("reference_blocks")
        if isinstance(reference_blocks, list) and reference_blocks:
            payload_msg["reference_blocks"] = reference_blocks
        if attachments:
            payload_msg["attachments"] = attachments
            if len(attachments) == 1:
                payload_msg["attachment"] = attachments[0]
        messages.append(payload_msg)
    return messages


def _build_complaint_artifacts(user_id: str, base_id: str) -> dict:
    try:
        manifest = load_manifest(user_id, base_id)
    except Exception:
        manifest = {}

    server_files = []
    original_files = manifest.get("original_files")
    if isinstance(original_files, list):
        for idx, entry in enumerate(original_files):
            if not isinstance(entry, dict):
                continue
            name = entry.get("uploaded_name") or entry.get("stored_name")
            url = entry.get("url")
            if not name or not url:
                continue
            server_files.append(
                {
                    "order": entry.get("order") or (idx + 1),
                    "name": name,
                    "url": url,
                }
            )

    raw_text_files: list[dict] = []
    try:
        text_meta = manifest.get("text") if isinstance(manifest, dict) else {}
        text_version = int(text_meta.get("current") or 0) if isinstance(text_meta, dict) else 0
        if text_version > 0:
            text_dir = stage_dir(user_id, base_id, "text", text_version)
            mapping_path = os.path.join(text_dir, "final_mapping.json")
            if os.path.isfile(mapping_path):
                with open(mapping_path, "r", encoding="utf-8") as handle:
                    mapping = json.load(handle)
                files_text_map: dict[tuple[int, str], list[tuple[int, str]]] = {}
                if isinstance(mapping, dict):
                    for entry in mapping.values():
                        if not isinstance(entry, dict):
                            continue
                        file_order = entry.get("file_order") or 0
                        file_name = entry.get("file_name") or ""
                        page_in_file = entry.get("page_in_file") or 0
                        text_content = entry.get("text_content") or ""
                        key = (int(file_order), str(file_name))
                        files_text_map.setdefault(key, []).append((int(page_in_file), str(text_content)))
                for (file_order, file_name), entries in sorted(files_text_map.items(), key=lambda item: item[0][0]):
                    pages = [
                        {"page": page, "content": content}
                        for page, content in sorted(entries, key=lambda item: item[0])
                    ]
                    raw_text_files.append(
                        {
                            "order": file_order,
                            "name": file_name,
                            "pages": pages,
                        }
                    )
    except Exception:
        raw_text_files = []

    preview_url = server_files[0]["url"] if server_files else None
    return {
        "rawFile": {
            "filePreviewUrl": preview_url,
            "serverFiles": server_files,
            "selectedFileIndex": 0,
        },
        "rawText": {
            "files": raw_text_files,
        },
    }


def _get_current_user(request: Request):
    try:
        return authenticate_request(request)
    except AuthenticationError as exc:  # pragma: no cover - passthrough
        logger.warning(
            "authentication failed: status=%s detail=%s path=%s",
            exc.status_code,
            exc.message,
            request.url.path if request else "",
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _sse_event(event_id: int, event: str, data: Any) -> ServerSentEvent:
    payload = data if data is not None else {}
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    return ServerSentEvent(id=str(event_id), event=event, data=payload)


def _payload_message_start(response_id: str, message_id: str, conversation_id: str, role: str, model: str, created: int) -> Dict[str, Any]:
    return {"event": "message_start", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "role": role, "model": model, "created": created}


def _payload_node_start(response_id: str, message_id: str, conversation_id: str, node_id: str, node_type: str, title: str, created: int) -> Dict[str, Any]:
    return {"event": "node_start", "node_id": node_id, "title": title, "type": node_type, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": created}


def _payload_node_delta(response_id: str, message_id: str, conversation_id: str, node_id: str, delta: str, created: int) -> Dict[str, Any]:
    return {"event": "node_delta", "node_id": node_id, "delta": delta, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": created}


def _payload_node_end(response_id: str, message_id: str, conversation_id: str, node_id: str, status: str, title: str, created: int) -> Dict[str, Any]:
    return {"event": "node_end", "node_id": node_id, "status": status, "title": title, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": created}


def _payload_content_delta(response_id: str, message_id: str, conversation_id: str, delta: str, created: int, *, index: int = 0, role: str = "assistant", node_id: Optional[str] = None) -> Dict[str, Any]:
    return {"event": "content_delta", "delta": delta, "index": index, "role": role, "node_id": node_id, "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": created}


def _payload_message_end(response_id: str, message_id: str, conversation_id: str, created: int) -> Dict[str, Any]:
    return {"event": "message_end", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": created}


def _payload_done(response_id: str, message_id: str, conversation_id: str, created: int) -> Dict[str, Any]:
    return {"event": "done", "message_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": created}


def _extract_text_from_block(block: Dict[str, Any]) -> Optional[str]:
    block_type = block.get("block_type")
    content = block.get("content") or {}
    # 用户输入
    if block_type == 10001:
        text_block = content.get("text_block") or {}
        text = text_block.get("text")
        return text if isinstance(text, str) and text.strip() else None
    # 引用片段01
    if block_type == 10000:
        ref_block = content.get("reference_block") or {}
        ref_text = (ref_block.get("text") or {}).get("text")
        return ref_text if isinstance(ref_text, str) and ref_text.strip() else None
    return None


def _extract_reference_blocks(messages: Iterable[Dict[str, Any]]) -> list[dict[str, str]]:
    reference_blocks: list[dict[str, str]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        blocks = message.get("content_block") or []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("block_type") != 10000:
                continue
            content = block.get("content") or {}
            ref_block = content.get("reference_block") or {}
            ref_text = (ref_block.get("text") or {}).get("text")
            if not isinstance(ref_text, str) or not ref_text.strip():
                continue
            block_id = block.get("block_id") or str(uuid.uuid4())
            reference_blocks.append({"id": str(block_id), "text": ref_text})
    return reference_blocks


def _build_query(messages: Iterable[Dict[str, Any]]) -> str:
    for message in reversed(list(messages or [])):
        blocks = message.get("content_block") or []
        parts = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = _extract_text_from_block(block)
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts)
    return ""


def _extract_user_text(messages: Iterable[Dict[str, Any]]) -> str:
    for message in reversed(list(messages or [])):
        blocks = message.get("content_block") or []
        parts = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("block_type") != 10001:
                continue
            content = block.get("content") or {}
            text_block = content.get("text_block") or {}
            text = text_block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        if parts:
            return "\n".join(parts)
    return ""


def _build_opencode_session_from_history(conversation_id: str, history: list[dict]) -> "Session":
    """将历史消息转换为 opencode_runtime Session。"""
    from apps.complaint_app.opencode_runtime.session import Message, MessagePart, Session

    session = Session(session_id=conversation_id)
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = item.get("query") if role == "user" else item.get("answer")
        if not isinstance(content, str) or not content.strip():
            continue
        message_id = str(item.get("id") or uuid.uuid4())
        session.add_message(
            Message(
                message_id=message_id,
                role=role,
                parts=[MessagePart(kind="text", content=content)],
            )
        )
    return session


def _store_opencode_uploads(
    user_id: str,
    base_id: str,
    uploads: list[tuple[str, str]],
    *,
    url_prefix: str,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """将上传文件落盘到 complaints 目录，返回存储路径与附件元信息。"""
    if not uploads:
        return [], []

    base_id = validate_base_id(base_id)
    os.makedirs(get_user_dir(user_id), exist_ok=True)
    original_folder = get_complaints_real_path(user_id, base_id, "original")
    os.makedirs(original_folder, exist_ok=True)

    manifest = load_manifest(user_id, base_id)
    existing_files = manifest.get("original_files") if isinstance(manifest.get("original_files"), list) else []
    start_index = len(existing_files) + 1

    stored_files: list[dict[str, Any]] = []
    stored_paths: list[tuple[str, str]] = []

    for idx, (source_path, display_name) in enumerate(uploads, start=start_index):
        name = display_name or os.path.basename(source_path)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else "dat"
        stored_name = f"{idx:03d}_{base_id}.{ext}"
        original_path = os.path.join(original_folder, stored_name)
        try:
            shutil.copyfile(source_path, original_path)
        except Exception:
            continue
        size_bytes = None
        try:
            size_bytes = os.path.getsize(original_path)
        except Exception:
            size_bytes = None
        stored_files.append(
            {
                "order": idx,
                "ext": ext,
                "original_path": original_path,
                "stored_name": stored_name,
                "uploaded_name": name,
                "size_bytes": size_bytes,
            }
        )
        stored_paths.append((original_path, name))

    path_prefix_clean = (url_prefix or "").strip("/")
    base_source_url_prefix = f"{'/' + path_prefix_clean if path_prefix_clean else ''}/api/files/{user_id}/{base_id}/original/"

    manifest["original_files"] = [
        {
            "uploaded_name": f.get("uploaded_name"),
            "stored_name": f.get("stored_name"),
            "path": relative_to_complaints(f.get("original_path")),
            "url": f"{base_source_url_prefix}{f.get('stored_name')}",
            "size_bytes": f.get("size_bytes"),
            "order": f.get("order"),
        }
        for f in stored_files
    ]
    if stored_files:
        manifest["original_file"] = {
            "uploaded_name": stored_files[0].get("uploaded_name"),
            "stored_name": stored_files[0].get("stored_name"),
            "path": relative_to_complaints(stored_files[0].get("original_path")),
            "url": f"{base_source_url_prefix}{stored_files[0].get('stored_name')}",
            "size_bytes": stored_files[0].get("size_bytes"),
        }
    save_manifest(user_id, base_id, manifest)

    return stored_paths, [
        {
            "original_filename": f.get("uploaded_name"),
            "stored_name": f.get("stored_name"),
            "path": relative_to_complaints(f.get("original_path")),
            "url": f"{base_source_url_prefix}{f.get('stored_name')}",
        }
        for f in stored_files
    ]


def _plan_to_dict(plan: Any) -> dict[str, Any]:
    """将 PlanResult 转为可序列化 dict。"""
    steps = []
    for step in getattr(plan, "steps", []) or []:
        steps.append(
            {
                "step_id": getattr(step, "step_id", ""),
                "tool": getattr(step, "tool", ""),
                "kind": getattr(step, "kind", ""),
                "inputs": getattr(step, "inputs", {}) or {},
                "depends_on": getattr(step, "depends_on", []) or [],
                "description": getattr(step, "description", ""),
            }
        )
    return {
        "mode": getattr(plan, "mode", ""),
        "reason": getattr(plan, "reason", ""),
        "max_parallel": getattr(plan, "max_parallel", 0),
        "steps": steps,
    }


def _safe_jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


async def _run_opencode_chat(
    *,
    current_user: Any,
    payload: dict,
    uploads: list[UploadFile],
) -> dict[str, Any]:
    """使用 opencode_runtime 主智能体处理对话（非 SSE）。"""
    from apps.complaint_app.opencode_runtime.main_agent import MainAgent, MainAgentRequest
    from apps.complaint_app.opencode_runtime.planner import Planner

    client_meta = payload.get("client_meta") or {}
    messages = payload.get("messages") or []
    query = _build_query(messages)
    reference_blocks = _extract_reference_blocks(messages)
    display_question = _extract_user_text(messages)
    if not display_question and reference_blocks:
        display_question = "（引用原文）"

    if not query and uploads:
        query = "帮我分析举报件"
    if not query:
        raise HTTPException(status_code=400, detail="消息内容为空")

    conversation_id = str(client_meta.get("conversation_id") or payload.get("conversation_id") or uuid.uuid4())
    response_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())

    history_payload, status = fetch_chat_messages(
        user_id=str(current_user.user_id),
        base_id=conversation_id,
        conversation_id=conversation_id,
        first_id="",
        limit=200,
        latest_conversation_fetcher=lambda *_args, **_kwargs: conversation_id,
    )
    history_messages = history_payload.get("messages") if status < 400 else []
    session = _build_opencode_session_from_history(conversation_id, history_messages or [])

    temp_uploads: list[tuple[str, str]] = []
    stored_paths: list[tuple[str, str]] = []
    attachments_meta: list[dict[str, Any]] | None = None
    try:
        if uploads:
            temp_uploads = _save_uploads_to_temp(uploads)
            stored_paths, stored_meta = _store_opencode_uploads(
                str(current_user.user_id),
                conversation_id,
                temp_uploads,
                url_prefix=os.getenv("URL_PATH_PREFIX", "").strip("/"),
            )
            attachments_meta = stored_meta or [{"original_filename": name} for _path, name in temp_uploads]

        context = dict(payload.get("context") or {})
        if stored_paths and not context.get("attachment_path"):
            context["attachment_path"] = stored_paths[0][0]
        context.setdefault("user_id", str(current_user.user_id))
        context.setdefault("base_id", conversation_id)

        try:
            await touch_user_history(str(current_user.user_id), conversation_id, name="新会话")
        except Exception:
            pass

        record_chat_user_message(
            str(current_user.user_id),
            conversation_id,
            conversation_id,
            display_question or query,
            reference_blocks=reference_blocks or None,
            attachments=attachments_meta,
            context={"engine": "opencode"},
        )

        planner_model = str(client_meta.get("planner_model") or payload.get("planner_model") or "").strip() or None
        planner = Planner(model=planner_model) if planner_model else Planner()
        agent = MainAgent(planner=planner)
        result = await agent.run(
            MainAgentRequest(
                user_input=query,
                session=session,
                context=context,
            )
        )

        show_plan = bool(client_meta.get("show_plan") or payload.get("show_plan"))
        show_steps = bool(client_meta.get("show_steps") or payload.get("show_steps"))
        plan_payload = _plan_to_dict(result.plan) if show_plan else None
        step_outputs = _safe_jsonable(result.metadata.get("outputs") or {}) if show_steps else None

        record_chat_assistant_message(
            str(current_user.user_id),
            conversation_id,
            conversation_id,
            answer=result.text,
            metadata={
                "engine": "opencode",
                **({"plan": plan_payload} if show_plan else {}),
                **({"step_outputs": step_outputs} if show_steps else {}),
            },
        )

        response: dict[str, Any] = {
            "conversation_id": conversation_id,
            "response_id": response_id,
            "message_id": message_id,
            "answer": result.text,
            "metadata": result.metadata,
        }
        if show_plan:
            response["plan"] = plan_payload
        if show_steps:
            response["step_outputs"] = step_outputs
            logger.info("[opencode] step_outputs=%s", _safe_jsonable(_redact_step_outputs(step_outputs)))
        return response
    finally:
        for path, _name in temp_uploads:
            try:
                os.remove(path)
            except Exception:
                pass


def _iter_data_lines(chunk: str) -> Iterable[str]:
    for line in str(chunk).splitlines():
        if line.startswith("data:"):
            yield line.split("data:", 1)[1].strip()


def _redact_step_outputs(step_outputs: dict[str, Any] | None) -> dict[str, Any] | None:
    if not step_outputs:
        return step_outputs
    redacted: dict[str, Any] = {}
    for step_id, payload in step_outputs.items():
        if isinstance(payload, dict) and payload.get("title") == "complaint_text_extract":
            masked = dict(payload)
            if "output" in masked:
                output = masked.get("output") or ""
                if isinstance(output, str):
                    masked["output"] = f"[omitted len={len(output)}]"
                else:
                    masked["output"] = "[omitted]"
            redacted[step_id] = masked
        else:
            redacted[step_id] = payload
    return redacted


def _load_first_round_context(user_id: str, base_id: str, conversation_id: str) -> list[dict[str, str]]:
    log_path = os.path.join(COMPLAINTS_BASE_DIR, user_id, base_id, "chats", conversation_id, "conversation.jsonl")
    if not os.path.isfile(log_path):
        return []
    first_user = ""
    first_assistant = ""
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                role = payload.get("role")
                if role == "user" and not first_user:
                    first_user = str(payload.get("question") or "").strip()
                if role == "assistant" and not first_assistant:
                    first_assistant = str(payload.get("answer") or "").strip()
                if first_user and first_assistant:
                    break
    except Exception:
        return []
    messages: list[dict[str, str]] = []
    if first_user:
        messages.append({"role": "user", "content": first_user})
    if first_assistant:
        messages.append({"role": "assistant", "content": first_assistant})
    return messages


def _format_label_with_text(prefix: str, text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        return prefix
    if len(cleaned) > 50:
        cleaned = f"{cleaned[:50]}..."
    return f"{prefix}（{cleaned}）"

def _save_uploads_to_temp(files: list[UploadFile]) -> list[tuple[str, str]]:
    temp_uploads: list[tuple[str, str]] = []
    for upload in files:
        safe_name = upload.filename or ""
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            with open(tmp.name, "wb") as out:
                shutil.copyfileobj(upload.file, out)
        finally:
            try:
                tmp.close()
            except Exception:
                pass
        temp_uploads.append((tmp.name, safe_name))
    return temp_uploads


async def _parse_request_payload(request: Request) -> tuple[dict, list[UploadFile]]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form: FormData = await request.form()
        payload_raw = form.get("payload")

        def _load_json(value: Any, label: str) -> Any:
            if isinstance(value, UploadFile):
                raw_bytes = value.file.read()
                value.file.seek(0)
                text = raw_bytes.decode("utf-8", errors="ignore")
            elif isinstance(value, (bytes, bytearray)):
                text = value.decode("utf-8", errors="ignore")
            else:
                text = str(value or "")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"{label} 解析失败: {exc}") from exc

        if payload_raw:
            payload = _load_json(payload_raw, "payload")
        else:
            payload = {}
            client_meta_raw = form.get("client_meta")
            messages_raw = form.get("messages")
            if client_meta_raw:
                payload["client_meta"] = _load_json(client_meta_raw, "client_meta")
            if messages_raw:
                payload["messages"] = _load_json(messages_raw, "messages")
            if not payload:
                raise HTTPException(status_code=400, detail="缺少 payload 参数")
        logger.info(
            "[chat_completion] multipart payload keys=%s files=%s",
            list(payload.keys()),
            [
                u.filename
                for u in form.values()
                if isinstance(u, (UploadFile, StarletteUploadFile))
            ],
        )
        uploads: list[UploadFile] = []
        for _key, value in form.multi_items():
            if isinstance(value, (UploadFile, StarletteUploadFile)):
                uploads.append(value)
        return payload, uploads
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体无效")
    logger.info("[chat_completion] json payload keys=%s", list(payload.keys()))
    return payload, []


async def _stream_complaint_events(
    user_id: str,
    conversation_id: str,
    query: str,
    response_id: str,
    message_id: str,
    start_event_id: int,
    uploads: list[UploadFile],
    enable_thinking: bool,
    reference_blocks: list[dict[str, str]] | None = None,
) -> AsyncGenerator[ServerSentEvent, None]:
    event_id = start_event_id
    created_ts = int(time.time() * 1000)
    node_registry: set[str] = set()
    now_ms = lambda: int(time.time() * 1000)
    suggestions_sent = False

    yield _sse_event(event_id, "chat_message", _payload_message_start(response_id, message_id, conversation_id, "assistant", "complaint_pipeline", created_ts))
    event_id += 1

    temp_uploads: list[tuple[str, str]] = []
    try:
        if uploads:
            temp_uploads = _save_uploads_to_temp(uploads)
        pipeline = TextPipeline(
            user_id,
            input_files=temp_uploads,
            upload_display_name=temp_uploads[0][1] if temp_uploads else "",
            url_prefix=os.getenv("URL_PATH_PREFIX", "").strip("/"),
            base_id_override=conversation_id,
        )
        pipeline.conversation_id = conversation_id
        pipeline.response_id = response_id
        pipeline._ingest_message_id = message_id
        pipeline.enable_thinking = bool(enable_thinking)
        pipeline.reference_blocks = reference_blocks or []
        if temp_uploads:
            upload_label = temp_uploads[0][1]
            total_size_bytes = sum(os.path.getsize(path) for path, _name in temp_uploads)
            total_size_mb = total_size_bytes / (1024 * 1024) if total_size_bytes else None
            async for chunk in pipeline.stream_tool_planner(conversation_id, upload_label, total_size_mb):
                for payload_str in _iter_data_lines(chunk):
                    try:
                        payload = json.loads(payload_str)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict) and payload.get("error"):
                        yield _sse_event(event_id, "system", payload)
                        event_id += 1
                        continue
                    if isinstance(payload, dict) and payload.get("event") in {
                        "message_start",
                        "message_end",
                        "node_start",
                        "node_delta",
                        "node_end",
                        "content_delta",
                    }:
                        yield _sse_event(event_id, "chat_message", payload)
                        event_id += 1
                        continue
        async for chunk in pipeline.generate_from_upload():
            for payload_str in _iter_data_lines(chunk):
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("error"):
                    yield _sse_event(event_id, "system", payload)
                    event_id += 1
                    continue

                if isinstance(payload, dict) and payload.get("event") in {"title_generation", "title_generation_done", "artifacts"}:
                    yield _sse_event(event_id, "system", payload)
                    event_id += 1
                    if (
                        payload.get("event") == "title_generation_done"
                        and not suggestions_sent
                    ):
                        suggestions_payload = {
                            "event": "suggestions",
                            "message_id": payload.get("message_id") or message_id,
                            "node_id": payload.get("message_id") or message_id,
                            "conversation_id": payload.get("conversation_id") or conversation_id,
                            "response_id": payload.get("response_id") or response_id,
                            "created": now_ms(),
                            "status": "completed",
                            "type": "suggestions",
                            "delta": json.dumps(
                                {"suggestions": get_default_complaint_suggestions()},
                                ensure_ascii=False,
                            ),
                        }
                        yield _sse_event(event_id, "chat_message", suggestions_payload)
                        event_id += 1
                        suggestions_sent = True
                    continue

                if isinstance(payload, dict) and payload.get("event") in {
                    "message_start",
                    "message_end",
                    "node_start",
                    "node_delta",
                    "node_end",
                    "content_delta",
                }:
                    yield _sse_event(event_id, "chat_message", payload)
                    event_id += 1
                    continue

                if isinstance(payload, dict) and payload.get("event") == "chat_message":
                    chat_msg = payload.get("chat_message") or {}
                    node_type = chat_msg.get("type") or "process"
                    msg_id = chat_msg.get("message_id") or message_id
                    node_title = chat_msg.get("title") or "处理中"
                    node_id = f"{msg_id}-{node_type}"
                    status = chat_msg.get("status")
                    if node_id not in node_registry and status in {"start", "processing"}:
                        yield _sse_event(event_id, "chat_message", _payload_node_start(response_id, msg_id, conversation_id, node_id, node_type, node_title, now_ms()))
                        event_id += 1
                        node_registry.add(node_id)
                    delta = chat_msg.get("content_delta")
                    if isinstance(delta, str) and delta:
                        yield _sse_event(event_id, "chat_message", _payload_node_delta(response_id, msg_id, conversation_id, node_id, delta, now_ms()))
                        event_id += 1
                        if chat_msg.get("type") == "result":
                            yield _sse_event(event_id, "chat_message", _payload_content_delta(response_id, msg_id, conversation_id, delta, now_ms()))
                            event_id += 1
                    tool_done = chat_msg.get("tool_status") in {"done", "end"}
                    if status == "end" or tool_done or chat_msg.get("is_final") is True:
                        if node_id not in node_registry:
                            node_registry.add(node_id)
                        yield _sse_event(event_id, "chat_message", _payload_node_end(response_id, msg_id, conversation_id, node_id, "completed", node_title, now_ms()))
                        event_id += 1
                    continue
                #TODO 帮我在这个地方加一个临时的return进行验证功能

        yield _sse_event(event_id, "chat_message", _payload_message_end(response_id, message_id, conversation_id, now_ms()))
        event_id += 1
        yield _sse_event(event_id, "DONE", _payload_done(response_id, message_id, conversation_id, now_ms()))
        event_id += 1
    finally:
        for path, _name in temp_uploads:
            try:
                os.remove(path)
            except Exception:
                pass


async def _stream_expert_events(
    payload: Dict[str, Any],
    current_user: Any,
    response_id: str,
    fallback_message_id: str,
    start_event_id: int,
) -> AsyncGenerator[ServerSentEvent, None]:
    event_id = start_event_id
    generator, _headers = stream_ai_chat_response(payload, "", current_user)
    async for chunk in generator:
        for payload_str in _iter_data_lines(chunk):
            try:
                event_payload = json.loads(payload_str)
            except json.JSONDecodeError:
                continue
            if not isinstance(event_payload, dict):
                continue
            event_name = event_payload.get("event")
            if not event_name:
                continue
            if event_name == "done":
                message_id = event_payload.get("message_id") or fallback_message_id
                yield _sse_event(event_id, "DONE", {"event": "done", "response_id": response_id, "message_id": message_id, "created": event_payload.get("created") or int(time.time())})
                event_id += 1
                continue
            if event_name == "title_generation":
                yield _sse_event(event_id, "system", event_payload)
                event_id += 1
                continue
            event_payload.setdefault("response_id", response_id)
            event_payload.setdefault("message_id", fallback_message_id)
            yield _sse_event(event_id, "chat_message", event_payload)
            event_id += 1


@router.post("/completion")
async def chat_completion(request: Request):
    current_user = _get_current_user(request)
    payload, uploads = await _parse_request_payload(request)
    client_meta = payload.get("client_meta") or {}
    agent_id = client_meta.get("agent_id") or payload.get("agent_id")
    if not agent_id or agent_id not in _ALLOWED_AGENTS:
        raise HTTPException(status_code=400, detail="agent_id 无效")

    engine = str(client_meta.get("engine") or payload.get("engine") or "").strip().lower()
    if agent_id == "complaint_agent" and engine == "opencode":
        return await _run_opencode_chat(current_user=current_user, payload=payload, uploads=uploads)

    messages = payload.get("messages") or []
    query = _build_query(messages)
    reference_blocks = _extract_reference_blocks(messages)
    display_question = _extract_user_text(messages)
    if not display_question and reference_blocks:
        display_question = "（引用原文）"
    need_deep_think = 0
    for message in messages:
        if isinstance(message, dict):
            value = message.get("need_deep_think")
            if isinstance(value, bool):
                need_deep_think = 1 if value else 0
            elif isinstance(value, (int, float, str)):
                try:
                    need_deep_think = 1 if int(value) == 1 else 0
                except Exception:
                    pass
            if need_deep_think:
                break
    logger.info(
        "[chat_completion] agent=%s query_len=%s uploads=%s content_type=%s",
        agent_id,
        len(query or ""),
        len(uploads),
        request.headers.get("content-type", ""),
    )
    if not query and not uploads:
        raise HTTPException(status_code=400, detail="消息内容为空")
    if not query and agent_id == "complaint_agent":
        query = "帮我分析举报件"

    conversation_id = str(client_meta.get("conversation_id") or "").strip() or str(uuid.uuid4())
    response_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    force_fc_only = str(os.getenv("COMPLAINT_FC_ONLY", "false")).lower() == "true"

    async def _stream() -> AsyncGenerator[ServerSentEvent, None]:
        event_id = 1
        yield _sse_event(event_id, "ack", {})
        event_id += 1
        yield _sse_event(event_id, "heatbeat", {})
        event_id += 1
        yield _sse_event(event_id, "system", {})
        event_id += 1

        if agent_id == "complaint_agent":
            if not uploads:
                thinking_node_id = f"{message_id}-thinking"
                thinking_line = "我来分析一下"
                now_ms = int(time.time() * 1000)
                yield _sse_event(event_id, "chat_message", {**_payload_node_start(response_id, message_id, conversation_id, thinking_node_id, "thinking", "思考", now_ms), "label_hidden": True})
                event_id += 1
                yield _sse_event(event_id, "chat_message", {**_payload_node_delta(response_id, message_id, conversation_id, thinking_node_id, thinking_line, now_ms), "label_hidden": True})
                event_id += 1
                yield _sse_event(event_id, "chat_message", {**_payload_node_end(response_id, message_id, conversation_id, thinking_node_id, "completed", "思考", now_ms), "label_hidden": True})
                event_id += 1
                tool_invoked = False
                tool_name = ""
                tool_args: dict[str, Any] = {}
                fallback_content = ""
                fc_api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
                fc_model = (os.getenv("COMPLAINT_FC_MODEL") or "").strip()
                record_chat_user_message(
                    str(current_user.user_id),
                    conversation_id,
                    conversation_id,
                    display_question or query,
                    reference_blocks=reference_blocks or None,
                )
                create_cancel_token(conversation_id)
                cancel_requested = False
                context_messages = _load_first_round_context(str(current_user.user_id), conversation_id, conversation_id)
                assistant_context = ""
                for msg in context_messages:
                    if msg.get("role") == "assistant":
                        assistant_context = str(msg.get("content") or "")
                        break
                fallback_chunks: list[str] = []
                streamed_fallback = False
                try:
                    if fc_api_key:
                        fc_base_url = (os.getenv("OPENAI_BASE_URL") or "").strip()
                        async for fc_event in function_calling_streaming(
                            fc_model,
                            query,
                            tools=COMPLAINT_FC_TOOLS,
                            function_mapper=COMPLAINT_FC_FUNCTION_MAPPER,
                            api_key=fc_api_key,
                            base_url=fc_base_url,
                            context_messages=context_messages,
                            user_id=str(current_user.user_id),
                            base_id=conversation_id,
                            conversation_id=conversation_id,
                        ):
                            ev_type = str(fc_event.get("type") or "")
                            if ev_type == "content_delta":
                                delta = str(fc_event.get("delta") or "")
                                if not delta:
                                    continue
                                fallback_chunks.append(delta)
                                if force_fc_only:
                                    if not streamed_fallback:
                                        yield _sse_event(
                                            event_id,
                                            "chat_message",
                                            _payload_message_start(
                                                response_id,
                                                message_id,
                                                conversation_id,
                                                "assistant",
                                                "complaint_fc",
                                                int(time.time() * 1000),
                                            ),
                                        )
                                        event_id += 1
                                        streamed_fallback = True
                                    payload = _payload_content_delta(response_id, message_id, conversation_id, delta, int(time.time() * 1000))
                                    append_chat_sse_event(str(current_user.user_id), conversation_id, conversation_id, payload)
                                    yield _sse_event(event_id, "chat_message", payload)
                                    event_id += 1
                            elif ev_type == "final":
                                tool_invoked = bool(fc_event.get("tool_invoked"))
                                tool_name = str(fc_event.get("tool_name") or "")
                                tool_args = fc_event.get("tool_args") or {}
                                fallback_content = str(fc_event.get("fallback_content") or "")
                        if not fallback_content and fallback_chunks:
                            fallback_content = "".join(fallback_chunks).strip()
                except Exception:
                    tool_invoked = False
                    fallback_content = ""
                finally:
                    if is_cancelled(conversation_id):
                        cancel_requested = True
                        pop_cancel_token(conversation_id)
                if cancel_requested:
                    return
                if tool_invoked and tool_name == "complaint_analysis":
                    logger.info("[complaint_fc] tool_invoked=true conversation_id=%s", conversation_id)
                    analysis_label = _format_label_with_text("分析", assistant_context)
                    summary_label = _format_label_with_text("总结", assistant_context)
                    analysis_node_id = f"{message_id}-analysis"
                    summary_node_id = f"{message_id}-summary"
                    steps_snapshot: list[dict[str, Any]] = []
                    assistant_chunks: list[str] = []
                    analysis_started = False
                    analysis_ended = False
                    summary_started = False
                    summary_ended = False
                    analysis_cancelled = False
                    yield _sse_event(
                        event_id,
                        "chat_message",
                        _payload_message_start(response_id, message_id, conversation_id, "assistant", "complaint_analysis", int(time.time() * 1000), ),
                    )
                    event_id += 1
                    def _add_step_line(line_id: str, content: str) -> None:
                        if not content:
                            return
                        for step in steps_snapshot:
                            if step.get("id") == analysis_node_id:
                                lines = step.get("detailLines") or []
                                if isinstance(line_id, str) and line_id:
                                    for idx, line in enumerate(lines):
                                        if line.get("lineId") == line_id:
                                            lines[idx] = {"lineId": line_id, "content": content}
                                            break
                                    else:
                                        lines.append({"lineId": line_id, "content": content})
                                else:
                                    lines.append({"content": content})
                                step["detailLines"] = lines
                                return
                    async for ev in run_complaint_analysis(str(tool_args.get("query") or ""), str(tool_args.get("source_text") or "")):
                        if is_cancelled(conversation_id):
                            analysis_cancelled = True
                            break
                        node_id = str(ev.get("node_id") or "")
                        ev_type = str(ev.get("event") or "")
                        line_id = ev.get("line_id")
                        if node_id in {"complaint_extract_items", "complaint_match_experience", "complaint_law_analysis"}:
                            if not analysis_started and ev_type == "node_start":
                                yield _sse_event(event_id, "chat_message", _payload_node_start(response_id, message_id, conversation_id, analysis_node_id, "thinking", analysis_label, int(time.time() * 1000)))
                                event_id += 1
                                analysis_started = True
                                steps_snapshot.append({"id": analysis_node_id, "label": analysis_label, "status": "thinking", "detailLines": []})
                            if ev_type == "node_delta":
                                payload = _payload_node_delta(response_id, message_id, conversation_id, analysis_node_id, str(ev.get("delta") or ""), int(time.time() * 1000))
                                if isinstance(line_id, str) and line_id:
                                    payload["line_id"] = line_id
                                yield _sse_event(event_id, "chat_message", payload)
                                event_id += 1
                                _add_step_line(str(line_id or ""), str(ev.get("delta") or ""))
                            if ev_type == "node_end" and node_id == "complaint_law_analysis" and not analysis_ended:
                                yield _sse_event(event_id, "chat_message", _payload_node_end(response_id, message_id, conversation_id, analysis_node_id, "completed", analysis_label, int(time.time() * 1000)))
                                event_id += 1
                                analysis_ended = True
                                for step in steps_snapshot:
                                    if step.get("id") == analysis_node_id:
                                        step["status"] = "done"
                        if node_id == "complaint_final_conclusion":
                            if ev_type == "node_start" and not summary_started:
                                yield _sse_event(event_id, "chat_message", _payload_node_start(response_id, message_id, conversation_id, summary_node_id, "thinking", summary_label, int(time.time() * 1000)))
                                event_id += 1
                                summary_started = True
                                steps_snapshot.append({"id": summary_node_id, "label": summary_label, "status": "thinking", "detailLines": []})
                            if ev_type == "node_delta":
                                payload = _payload_node_delta(response_id, message_id, conversation_id, summary_node_id, str(ev.get("delta") or ""), int(time.time() * 1000))
                                if isinstance(line_id, str) and line_id:
                                    payload["line_id"] = line_id
                                yield _sse_event(event_id, "chat_message", payload)
                                event_id += 1
                                for step in steps_snapshot:
                                    if step.get("id") == summary_node_id:
                                        lines = step.get("detailLines") or []
                                        lines.append({"content": str(ev.get("delta") or "")})
                                        step["detailLines"] = lines
                            if ev_type == "node_end" and not summary_ended:
                                yield _sse_event(event_id, "chat_message", _payload_node_end(response_id, message_id, conversation_id, summary_node_id, "completed", summary_label, int(time.time() * 1000)))
                                event_id += 1
                                summary_ended = True
                                for step in steps_snapshot:
                                    if step.get("id") == summary_node_id:
                                        step["status"] = "done"
                        if ev_type == "content_delta":
                            delta = str(ev.get("delta") or "")
                            if delta:
                                payload = _payload_content_delta(response_id, message_id, conversation_id, delta, int(time.time() * 1000))
                                append_chat_sse_event(str(current_user.user_id), conversation_id, conversation_id, payload)
                                yield _sse_event(event_id, "chat_message", payload)
                                event_id += 1
                                assistant_chunks.append(delta)
                    if analysis_cancelled:
                        pop_cancel_token(conversation_id)
                        return
                    assistant_answer = "".join(assistant_chunks).strip()
                    steps_snapshot.insert(0, {"id": thinking_node_id, "label": "思考", "status": "done", "label_hidden": True, "detailLines": [{"content": thinking_line}]})
                    snapshot_payload = {"steps": steps_snapshot, "suggestions": get_default_complaint_suggestions()}
                    record_chat_assistant_message(
                        str(current_user.user_id),
                        conversation_id,
                        conversation_id,
                        answer=assistant_answer,
                        metadata={"model": "complaint_analysis", "has_thought": True},
                        chat_message=snapshot_payload,
                        snapshot=snapshot_payload,
                    )
                    yield _sse_event(event_id, "chat_message", _payload_message_end(response_id, message_id, conversation_id, int(time.time() * 1000)))
                    event_id += 1
                    yield _sse_event(event_id, "DONE", _payload_done(response_id, message_id, conversation_id, int(time.time() * 1000)))
                    event_id += 1
                    pop_cancel_token(conversation_id)
                    return
                logger.info("[complaint_fc] tool_invoked=false conversation_id=%s", conversation_id)
                if force_fc_only:
                    fallback_text = fallback_content or "⚠️ 未触发举报事项分析工具，请调整问题后重试。"
                    if not streamed_fallback:
                        yield _sse_event(
                            event_id,
                            "chat_message",
                            _payload_message_start(response_id, message_id, conversation_id, "assistant", "complaint_fc", int(time.time() * 1000)),
                        )
                        event_id += 1
                        payload = _payload_content_delta(response_id, message_id, conversation_id, fallback_text, int(time.time() * 1000))
                        append_chat_sse_event(str(current_user.user_id), conversation_id, conversation_id, payload)
                        yield _sse_event(event_id, "chat_message", payload)
                        event_id += 1
                    yield _sse_event(event_id, "chat_message", _payload_message_end(response_id, message_id, conversation_id, int(time.time() * 1000)))
                    event_id += 1
                    yield _sse_event(event_id, "DONE", _payload_done(response_id, message_id, conversation_id, int(time.time() * 1000)))
                    event_id += 1
                    snapshot_payload = {
                        "segments": [
                            {
                                "type": "result",
                                "title": "结果",
                                "status": "end",
                                "markdown": fallback_text,
                            }
                        ],
                        "suggestions": get_default_complaint_suggestions(),
                    }
                    record_chat_assistant_message(
                        str(current_user.user_id),
                        conversation_id,
                        conversation_id,
                        answer=fallback_text,
                        metadata={"model": fc_model, "has_thought": False},
                        chat_message=snapshot_payload,
                        snapshot=snapshot_payload,
                    )
                    pop_cancel_token(conversation_id)
                    return

                pop_cancel_token(conversation_id)
                chat_payload = {
                    "question": query,
                    "display_question": display_question,
                    "conversation_id": conversation_id,
                    "base_id": conversation_id,
                    "reference_blocks": reference_blocks,
                    "message_id": message_id,
                }
                async for event_name, payload in stream_chat(
                    str(current_user.user_id),
                    str(current_user.user_id),
                    content_type="application/json",
                    form_data=None,
                    files=None,
                    json_payload=chat_payload,
                ):
                    if event_name == "DONE":
                        yield _sse_event(event_id, "chat_message", {"event": "suggestions", "message_id": message_id, "node_id": message_id, "conversation_id": conversation_id, "response_id": response_id, "created": int(time.time() * 1000), "status": "completed", "type": "suggestions", "delta": json.dumps({"suggestions": get_default_complaint_suggestions()}, ensure_ascii=False)})
                        event_id += 1
                    yield _sse_event(event_id, event_name, payload)
                    event_id += 1
                return
            async for chunk in _stream_complaint_events(
                str(current_user.user_id),
                conversation_id,
                query,
                response_id,
                message_id,
                event_id,
                uploads,
                bool(need_deep_think),
                reference_blocks=reference_blocks,
            ):
                yield chunk
            return

        if agent_id == "expert_agent":
            expert_payload = {
                "question": query,
                "conversation_id": conversation_id,
            }
            async for chunk in _stream_expert_events(
                expert_payload,
                current_user,
                response_id,
                message_id,
                event_id,
            ):
                yield chunk
            return

    return EventSourceResponse(_stream())
