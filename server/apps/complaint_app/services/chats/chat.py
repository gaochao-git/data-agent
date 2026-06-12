"""LLM-based chat streaming.

- 入口: 接收追问表单/JSON，整理摘要/原文/专家经验上下文，构造提示词调用异步 OpenAI。
- 流式: 使用 AsyncOpenAI 流式返回，逐片段写入 SSE，同时落盘本地聊天记录。
- 停止: 检查内存取消标记，及时退出流；不向模型发送远端 cancel。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import AsyncGenerator

from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.core.model_config import get_chat_model
from apps.complaint_app.services.chats.attachment_processor import build_structured_query, ensure_chat_attachment_dir, is_allowed_chat_attachment
from apps.complaint_app.history_storage import (
    append_chat_sse_event,
    migrate_conversation_directory,
    record_chat_assistant_message,
    record_chat_user_message,
    relative_to_complaints,
)
from apps.complaint_app.services.chats.context import context_mode, load_latest_summary, load_latest_text, strip_tables_enabled
from apps.complaint_app.services.history_service import (
    load_latest_process_items_result,
    build_expert_experience_entries,
)
from apps.complaint_app.services.chats.suggestions import get_default_complaint_suggestions
from apps.complaint_app.tools.doc_markdown import DocumentConversionError, convert_doc_to_markdown, replace_images_with_ocr
from apps.complaint_app.utils import _ocr_file_max_size, _strip_markdown_tables
from utils.storage_paths import COMPLAINTS_BASE_DIR, validate_base_id
from utils.logger import get_logger

from .state import create_cancel_token, pop_cancel_token, is_cancelled
from .prompts import build_chat_messages

logger = get_logger(__name__)

CHAT_LLM_MODEL = get_chat_model()


def _payload_message_start(
    response_id: str,
    message_id: str,
    conversation_id: str,
    role: str,
    model: str,
    created: int,
) -> dict[str, object]:
    return {
        "event": "message_start",
        "message_id": message_id,
        "conversation_id": conversation_id,
        "response_id": response_id,
        "role": role,
        "model": model,
        "created": created,
    }


def _payload_content_delta(
    response_id: str,
    message_id: str,
    conversation_id: str,
    delta: str,
    created: int,
    *,
    index: int = 0,
    role: str = "assistant",
) -> dict[str, object]:
    return {
        "event": "content_delta",
        "delta": delta,
        "index": index,
        "role": role,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "response_id": response_id,
        "created": created,
    }


def _payload_message_end(
    response_id: str,
    message_id: str,
    conversation_id: str,
    created: int,
) -> dict[str, object]:
    return {
        "event": "message_end",
        "message_id": message_id,
        "conversation_id": conversation_id,
        "response_id": response_id,
        "created": created,
    }


def _payload_done(
    response_id: str,
    message_id: str,
    conversation_id: str,
    created: int,
) -> dict[str, object]:
    return {
        "event": "done",
        "message_id": message_id,
        "conversation_id": conversation_id,
        "response_id": response_id,
        "created": created,
    }


def _emit_sse(event: str, payload: dict[str, object]) -> tuple[str, dict[str, object]]:
    return event, payload


def _split_think_answer(text: str) -> tuple[str, str, bool]:
    cleaned = (text or "").strip()
    start_idx = cleaned.find("<think>")
    end_idx = cleaned.find("</think>")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        thought = cleaned[start_idx + len("<think>"):end_idx].strip()
        answer = (cleaned[:start_idx] + cleaned[end_idx + len("</think>"):]).strip()
        return thought, answer, True
    return "", cleaned, False


def _build_structured_snapshot(thought: str, answer: str) -> dict[str, object]:
    segments: list[dict[str, object]] = []
    if thought:
        segments.append({
            "type": "thought",
            "title": "思考",
            "status": "end",
            "markdown": thought,
        })
    if answer:
        segments.append({
            "type": "result",
            "title": "结果",
            "status": "end",
            "markdown": answer,
        })
    return {"segments": segments} if segments else {}


def _build_steps_snapshot(thought: str, message_id: str, has_think: bool) -> list[dict[str, object]]:
    if not has_think or not thought:
        return []
    step_id = f"{message_id}-thinking"
    return [
        {
            "id": step_id,
            "label": "思考完成",
            "status": "done",
            "detailLines": [{"content": thought}],
        }
    ]


async def stream_chat(
    user_id: str,
    normalized_user_id: str,
    *,
    content_type: str | None,
    form_data,
    files,
    json_payload: dict | None,
) -> AsyncGenerator[tuple[str, dict[str, object]], None]:
    """Async SSE generator for chat."""
    question = ''
    content = ''
    summary = None
    conversation_id = ''
    attachment_file = None
    base_id = ''
    display_question = ''
    reference_blocks: list[dict] | None = None

    try:
        complaint_list = ''
        requests_list = ''
        second_category = ''
        incoming_message_id = ''

        if content_type and content_type.startswith('multipart/form-data'):
            form = form_data or {}
            question = str(form.get('question') or '').strip()
            content = str(form.get('content') or '')
            summary = form.get('summary')
            conversation_id = str(form.get('conversation_id') or '').strip()
            complaint_list = str(form.get('complaint_list') or '').strip()
            requests_list = str(form.get('requests_list') or form.get('requsts_list') or '').strip()
            base_id = str(form.get('base_id') or '').strip()
            attachment_file = files.get('file') if files else None
        else:
            payload = json_payload or {}
            question = str(payload.get('question') or '').strip()
            content = str(payload.get('content') or '')
            summary = payload.get('summary')
            conversation_id = str(payload.get('conversation_id') or '').strip()
            complaint_list = str(payload.get('complaint_list') or '').strip()
            requests_list = str(payload.get('requests_list') or payload.get('requsts_list') or '').strip()
            base_id = str(payload.get('base_id') or '').strip()
            reference_blocks = payload.get('reference_blocks')
            display_question = str(payload.get('display_question') or '').strip()
            incoming_message_id = str(payload.get('message_id') or '').strip()
    except Exception:
        response_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        conversation_id = str(uuid.uuid4())
        created_ts = int(time.time() * 1000)
        yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, conversation_id, "assistant", "complaint_chat", created_ts, ), )
        yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, conversation_id, "⚠️ 请求体解析失败", int(time.time() * 1000), ), )
        yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, conversation_id, int(time.time() * 1000), ), )
        yield _emit_sse( "DONE", _payload_done( response_id, message_id, conversation_id, int(time.time() * 1000), ), )
        return

    incoming_conversation_id = conversation_id
    resolved_conversation_id = incoming_conversation_id or uuid.uuid4().hex
    response_id = str(uuid.uuid4())
    message_id = incoming_message_id or str(uuid.uuid4())
    created_ts = int(time.time() * 1000)
    attachment_conversation_id = resolved_conversation_id
    attachments_info: list[dict[str, object]] = []
    chat_query = question

    if not question:
        yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
        yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, "⚠️ 问题不能为空", int(time.time() * 1000), ), )
        yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        return

    if not os.getenv("OPENAI_API_KEY"):
        yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
        yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, "⚠️ 未配置模型服务，请联系管理员", int(time.time() * 1000), ), )
        yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        return

    try:
        client = get_async_openai_client()
    except Exception as exc:
        logger.error("init openai client failed: %s", exc, exc_info=True)
        yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
        yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, "⚠️ 模型客户端初始化失败", int(time.time() * 1000), ), )
        yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        return

    if attachment_file is not None and getattr(attachment_file, "filename", ""):
        if not is_allowed_chat_attachment(attachment_file.filename):
            yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
            yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, "⚠️ 附件格式仅支持 doc/docx", int(time.time() * 1000), ), )
            yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            return

        original_filename = attachment_file.filename

        try:
            if not base_id:
                raise ValueError("缺少 base_id")
            validate_base_id(base_id)
        except Exception as exc:
            yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
            yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, f"⚠️ 无效的举报件标识: {exc}", int(time.time() * 1000), ), )
            yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            return

        max_size_mb = _ocr_file_max_size()
        if getattr(attachment_file, "seek", None) and getattr(attachment_file, "tell", None):
            try:
                attachment_file.seek(0, os.SEEK_END)
                size_bytes = attachment_file.tell()
                attachment_file.seek(0)
                if size_bytes > max_size_mb * 1024 * 1024:
                    yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
                    yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, f"⚠️ 附件大小超过限制（{max_size_mb}MB）", int(time.time() * 1000), ), )
                    yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
                    yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
                    return
            except Exception:
                attachment_file.seek(0)

        try:
            complaint_dir = os.path.join(COMPLAINTS_BASE_DIR, user_id, base_id, "chats")
            os.makedirs(complaint_dir, exist_ok=True)
            attachment_dir, safe_name = ensure_chat_attachment_dir(
                complaint_dir,
                attachment_conversation_id,
                original_filename
            )
            original_path = os.path.join(attachment_dir, safe_name)
            attachment_file.save(original_path)

            markdown_path, media_dir = convert_doc_to_markdown(original_path, attachment_dir)
            markdown_text = replace_images_with_ocr(markdown_path, media_dir)
        except DocumentConversionError as exc:
            logger.error("附件转换失败", exc_info=True)
            yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
            yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, "⚠️ 文档读取失败：暂无法解析该附件，请确认文件为未加密的 doc/docx 并重试", int(time.time() * 1000), ), )
            yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            return
        except Exception as exc:  # pylint: disable=broad-except
            error_msg = f"附件处理异常: {exc}"
            yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
            yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, f"⚠️ {error_msg}", int(time.time() * 1000), ), )
            yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
            return

        chat_query = build_structured_query(original_filename, markdown_text, question)
        attachments_info.append({
            "original_filename": original_filename,
            "stored_filename": safe_name,
            "storage_dir": relative_to_complaints(attachment_dir),
            "original_path": relative_to_complaints(original_path),
            "markdown_path": relative_to_complaints(markdown_path),
            "media_dir": relative_to_complaints(media_dir),
            "markdown_length": len(markdown_text or ""),
        })

    structured_payload: dict[str, object] = {}
    mode = context_mode()
    summary_from_file = load_latest_summary(user_id, base_id) if mode in {1, 3} else None
    summary_section = summary_from_file or structured_payload.get('summary')
    if not summary_section and summary and mode in {1, 3}:
        summary_candidate = None
        if isinstance(summary, str):
            try:
                summary_candidate = json.loads(summary)
            except (TypeError, ValueError):
                summary_candidate = {'markdown': summary}
        elif isinstance(summary, dict):
            summary_candidate = summary
        if isinstance(summary_candidate, dict):
            summary_section = summary_candidate
    if summary_section and isinstance(summary_section, dict):
        structured_payload['举报件摘要信息'] = summary_section

    raw_text_section = None
    if mode in {2, 3}:
        raw_text = load_latest_text(user_id, base_id)
        if raw_text and strip_tables_enabled():
            raw_text = _strip_markdown_tables(raw_text)
        if raw_text:
            raw_text_section = {"content": raw_text}
    if raw_text_section:
        structured_payload['举报件原文'] = raw_text_section

    latest_process_result = load_latest_process_items_result(user_id, base_id)
    expert_experience_entries = build_expert_experience_entries(latest_process_result)
    structured_payload.setdefault('举报事项与诉求及相关专家分析', expert_experience_entries)
    original_context_content = content
    content = json.dumps(structured_payload, ensure_ascii=False)

    context_payload = {
        "content": original_context_content,
        "complaint_list": complaint_list,
        "requests_list": requests_list,
        "second_category": second_category,
    }
    context_payload = {k: v for k, v in context_payload.items() if v}
    record_chat_user_message(
        user_id,
        base_id,
        resolved_conversation_id,
        display_question or question,
        structured_payload=structured_payload or None,
        context=context_payload or None,
        attachments=attachments_info or None,
        reference_blocks=reference_blocks or None,
    )

    # system 内含使用说明，user 放上下文 JSON 与用户问题，保持对模型的轻约束
    messages = build_chat_messages(content or "{}", chat_query)

    cancel_token = create_cancel_token(resolved_conversation_id)
    assistant_answer_chunks: list[str] = []

    try:
        yield _emit_sse( "chat_message", _payload_message_start( response_id, message_id, resolved_conversation_id, "assistant", "complaint_chat", created_ts, ), )
        # 直接用共享 AsyncOpenAI 客户端做流式对话
        stream = await client.chat.completions.create(
            model=CHAT_LLM_MODEL,
            messages=messages,
            stream=True,
        )
        logger.info("CHAT_LLM_MODEL: %s", CHAT_LLM_MODEL)
        async for chunk in stream:
            if is_cancelled(resolved_conversation_id):
                break
            if not chunk or not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            assistant_answer_chunks.append(delta)
            payload = _payload_content_delta(
                response_id,
                message_id,
                resolved_conversation_id,
                delta,
                int(time.time() * 1000),
            )
            append_chat_sse_event(user_id, base_id, resolved_conversation_id, payload)
            yield _emit_sse("chat_message", payload)
            yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        raw_answer = ''.join(assistant_answer_chunks).strip()
        thought, answer, has_think = _split_think_answer(raw_answer)
        assistant_answer = answer or raw_answer
        snapshot = _build_structured_snapshot(thought, assistant_answer)
        steps = _build_steps_snapshot(thought, message_id, has_think)
        if steps:
            snapshot = {
                **(snapshot or {}),
                "steps": steps,
            }
        snapshot = {
            **(snapshot or {}),
            "suggestions": get_default_complaint_suggestions(),
        }
        record_chat_assistant_message(
            user_id,
            base_id,
            resolved_conversation_id,
            answer=assistant_answer,
            metadata={"model": CHAT_LLM_MODEL, "has_thought": bool(thought)},
            chat_message=snapshot or None,
            snapshot=snapshot or None,
        )
    except Exception as exc:
        logger.error("chat streaming failed", exc_info=True)
        error_payload = f"对话失败: {exc}"
        record_chat_assistant_message(
            user_id,
            base_id,
            resolved_conversation_id,
            error=error_payload,
        )
        yield _emit_sse( "chat_message", _payload_content_delta( response_id, message_id, resolved_conversation_id, f"⚠️ {error_payload}", int(time.time() * 1000), ), )
        yield _emit_sse( "chat_message", _payload_message_end( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
        yield _emit_sse( "DONE", _payload_done( response_id, message_id, resolved_conversation_id, int(time.time() * 1000), ), )
    finally:
        pop_cancel_token(resolved_conversation_id)
        # 处理 conversation_id 变更（当前无需变更，但保留兼容逻辑）
        new_conversation_id = resolved_conversation_id
        if new_conversation_id and new_conversation_id != resolved_conversation_id:
            migrate_conversation_directory(
                user_id,
                base_id,
                resolved_conversation_id,
                new_conversation_id,
            )
