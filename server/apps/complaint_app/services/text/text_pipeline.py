from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator
from fastapi.concurrency import run_in_threadpool
from apps.complaint_app.core.versioning import (
    load_manifest,
    next_version,
    stage_dir,
    update_manifest,
    save_manifest,
    write_meta,
)

from apps.complaint_app.services.text.complaint_files_handler import process_image, process_pdf
from apps.complaint_app.core.async_openai_client import get_async_openai_client
from apps.complaint_app.history_storage import append_chat_sse_event, record_chat_assistant_message, record_chat_user_message, touch_user_history, relative_to_complaints
from apps.complaint_app.services.chats.suggestions import get_default_complaint_suggestions
from apps.complaint_app.services.pipeline_base import ComplaintSSEEvent, build_standard_sse_payload
from apps.complaint_app.services.text.tool_prompts import (
    TOOL_PLANNER_MODEL,
    TOOL_PLANNER_TEMPERATURE,
    TOOL_SUMMARY_MODEL,
    TOOL_SUMMARY_TEMPERATURE,
    build_text_tool_planner_messages,
    build_text_tool_summary_messages,
)
from apps.complaint_app.services.summary.summary_pipeline import SummaryPipeline
from apps.complaint_app.tools.image_text_extractor import recognize_image_async
from apps.complaint_app.utils import _ocr_file_max_size, _ocr_max_concurrency
from utils.logger import get_logger
from utils.storage_paths import get_complaints_real_path, get_user_dir, validate_base_id

logger = get_logger(__name__)

MAX_UPLOAD_SIZE_MB = _ocr_file_max_size()


class TextPipeline:
    """仅执行上传+OCR+全文落盘的管线（async 版本），并提供用户文本保存入口。"""

    def __init__(
        self,
        user_id: str,
        *,
        input_file_path: str | None = None,
        input_files: list[tuple[str, str]] | None = None,
        upload_display_name: str = "",
        url_prefix: str = "",
        resume_base_id: str | None = None,
        base_id_override: str | None = None,
        reuse_existing_original: bool = False,
        enable_summary: bool = True,
    ):
        self.user_id = user_id
        self.input_file_path = input_file_path
        self.input_files = input_files or []
        self.upload_display_name = upload_display_name
        self.url_prefix = url_prefix
        self.resume_base_id = resume_base_id
        self.base_id_override = base_id_override
        self.reuse_existing_original = reuse_existing_original
        # 是否在抽取后自动生成摘要（默认开启）
        self.enable_summary = bool(enable_summary)
        self.conversation_id: str | None = None
        self.response_id: str | None = None
        self.enable_thinking: bool = False
        self._openai_client = None
        self._latest_raw_text: str | None = None
        self._ingest_message_id: str | None = None
        self._ingest_plan: str = ""
        self._ingest_tool_msg: str = ""
        self._ingest_tool_done: bool = False
        self._ingest_summary: str = ""
        self.reference_blocks: list[dict] = []
        # 结构化段落缓存
        self._ingest_structured: dict | None = None
        self._status_thought: str | None = None
        self._status_tool: str | None = None
        self._status_result: str | None = None
        self._tool_parent_started: bool = False
        self._tool_parent_node_id: str | None = None
        self._tool_parent_label: str | None = None
        self._summary_label: str | None = None
        self._tool_child_started: set[str] = set()
        self._steps: list[dict] = []
        self._step_index: dict[str, dict] = {}
        self._pending_summary_record: dict[str, object] | None = None

    # ---------- 静态写入工具 ----------
    @staticmethod
    async def _write_file(path: str, content: str) -> None:
        def _sync_write():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content or "")

        await run_in_threadpool(_sync_write)

    @staticmethod
    async def _write_json(path: str, payload: dict) -> None:
        def _sync_write():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)

        await run_in_threadpool(_sync_write)

    # ---------- 对话/工具前后提示 ----------
    def _chat_sse_event(
        self,
        base_id: str,
        *,
        message_id: str | None = None,
        role: str,
        content: str | None = None,
        delta: str | None = None,
        stage: str | None = None,
        is_final: bool | None = None,
        tool_status: str | None = None,
        structured_content: dict | None = None,
        type: str | None = None,
        title: str | None = None,
        status: str | None = None,
        render_mode: str | None = None,
        line_id: str | None = None,
        order: int | None = None,
        collapse_on_end: bool | None = None,
    ) -> str:
        chat_payload: dict[str, object] = {
            "id": message_id,
            "role": role,
            "content": content or "",
        }
        if type:
            chat_payload["type"] = type
        if title:
            chat_payload["title"] = title
        if status:
            chat_payload["status"] = status
        if render_mode:
            chat_payload["render_mode"] = render_mode
        if line_id:
            chat_payload["line_id"] = line_id
        if order is not None:
            chat_payload["order"] = order
        if collapse_on_end is not None:
            chat_payload["collapse_on_end"] = collapse_on_end
        if structured_content:
            chat_payload["structured_content"] = structured_content
        if delta:
            chat_payload["content_delta"] = delta
        if stage:
            chat_payload["stage"] = stage
        if is_final is not None:
            chat_payload["is_final"] = is_final
        if tool_status:
            chat_payload["tool_status"] = tool_status
            chat_payload["variant"] = "tool"
        if self.conversation_id:
            chat_payload["conversation_id"] = self.conversation_id
        payload = {
            "event": "chat_message",
            "chat_message": chat_payload,
        }
        if self.conversation_id:
            try:
                append_chat_sse_event(self.user_id, base_id, self.conversation_id, payload)
            except Exception:
                logger.debug("append_chat_sse_event failed", exc_info=True)
        return ComplaintSSEEvent(base_id=base_id, extra=payload).to_sse(user_id=self.user_id)

    def _node_sse_event(self, base_id: str, payload: dict[str, Any]) -> str:
        if self.conversation_id:
            payload.setdefault("conversation_id", self.conversation_id)
        if self.response_id:
            payload.setdefault("response_id", self.response_id)
        if self.conversation_id:
            try:
                append_chat_sse_event(self.user_id, base_id, self.conversation_id, payload)
            except Exception:
                logger.debug("append_chat_sse_event failed", exc_info=True)
        return ComplaintSSEEvent(base_id=base_id, extra=payload).to_sse(user_id=self.user_id)

    def _system_sse_event(self, base_id: str, payload: dict[str, Any]) -> str:
        if self.conversation_id:
            payload.setdefault("conversation_id", self.conversation_id)
        if self.response_id:
            payload.setdefault("response_id", self.response_id)
        return ComplaintSSEEvent(base_id=base_id, extra=payload).to_sse(user_id=self.user_id)

    def _ensure_tool_parent_node(self, base_id: str) -> str | None:
        if self._tool_parent_started:
            return None
        if not self._ingest_message_id:
            return None
        self._tool_parent_node_id = self._tool_parent_node_id or f"{self._ingest_message_id}-tool"
        self._tool_parent_started = True
        label = self._tool_parent_label
        self._ensure_step(self._tool_parent_node_id, label, "thinking")
        return self._node_sse_event(
            base_id,
            build_standard_sse_payload(
                event="node_start",
                message_id=self._ingest_message_id,
                conversation_id=self.conversation_id or base_id,
                response_id=self.response_id,
                node_id=self._tool_parent_node_id,
                type="tool",
                title=label,
            ),
        )

    def _emit_tool_child_event(self, base_id: str, child_payload: dict[str, Any]) -> str | None:
        if not self._tool_parent_started or not self._ingest_message_id or not self._tool_parent_node_id:
            return None
        if isinstance(child_payload, dict):
            event_type = child_payload.get("event")
            child_id = child_payload.get("node_id")
            title = child_payload.get("title") or "处理中"
            if event_type == "node_start" and child_id:
                self._ensure_child_step(self._tool_parent_node_id, child_id, title, "thinking")
            elif event_type == "node_end" and child_id:
                self._ensure_child_step(self._tool_parent_node_id, child_id, title, "done")
                self._set_step_status(child_id, "done", title)
            elif event_type == "node_delta" and child_id:
                self._ensure_child_step(self._tool_parent_node_id, child_id, title, "thinking")
                delta = child_payload.get("delta")
                if isinstance(delta, str) and delta:
                    line_id = child_payload.get("line_id")
                    if isinstance(line_id, str) and line_id:
                        self._append_step_detail_line(child_id, delta, line_id)
                    else:
                        self._append_step_detail_line(child_id, delta)
        nested = json.dumps(child_payload, ensure_ascii=False)
        return self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=self._tool_parent_node_id, delta=nested))

    def _ensure_tool_child_node(self, base_id: str, child_id: str, title: str, node_type: str = "task") -> str | None:
        if child_id in self._tool_child_started:
            return None
        if not self._ingest_message_id:
            return None
        self._tool_child_started.add(child_id)
        if self._tool_parent_node_id:
            self._ensure_child_step(self._tool_parent_node_id, child_id, title, "thinking")
        return self._emit_tool_child_event(base_id, build_standard_sse_payload(event="node_start", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=child_id, type=node_type, title=title))

    def _complete_tool_parent_node(self, base_id: str) -> str | None:
        if not self._tool_parent_started or not self._ingest_message_id or not self._tool_parent_node_id:
            return None
        self._tool_parent_started = False
        label = self._tool_parent_label
        self._set_step_status(self._tool_parent_node_id, "done", label)
        return self._node_sse_event(
            base_id,
            build_standard_sse_payload(
                event="node_end",
                message_id=self._ingest_message_id,
                conversation_id=self.conversation_id or base_id,
                response_id=self.response_id,
                node_id=self._tool_parent_node_id,
                status="completed",
                title=label,
            ),
        )

    def _ensure_openai_client(self):
        if self._openai_client is None:
            self._openai_client = get_async_openai_client()
        return self._openai_client

    def _render_ingest_message(self, upload_label: str) -> str:
        """将思考/工具/总结三段组合成结构化气泡内容，使用简洁 markdown 列表。"""
        blocks: list[str] = []

        def _clean_lines(text: str) -> list[str]:
            cleaned = []
            for line in text.split("\n"):
                normalized = re.sub(r"^[\s]*[-*•·●]\s*", "", line).strip()
                if normalized:
                    cleaned.append(normalized)
            return cleaned

        # 思考 + 工具执行：合并为一个列表，避免多段分散
        thought_lines: list[str] = []
        if self._ingest_plan.strip():
            thought_lines.extend(_clean_lines(self._ingest_plan))
        if thought_lines:
            blocks.append("\n".join(thought_lines))

        # 结果：保持原始 markdown（模型总结）
        if self._ingest_summary.strip():
            blocks.append(self._ingest_summary.strip())

        if not blocks:
            return f"附件：{upload_label}"
        return "\n\n".join(blocks)

    def _format_label_with_files(self, prefix: str, file_names: list[str]) -> str:
        cleaned = [name.strip() for name in file_names if isinstance(name, str) and name.strip()]
        if not cleaned:
            return prefix
        joined = ", ".join(cleaned)
        if len(joined) > 50:
            joined = f"{joined[:50]}..."
        return f"{prefix}（{joined}）"

    def _format_read_label(self, file_names: list[str]) -> str:
        return self._format_label_with_files("阅读", file_names)

    def _format_summary_label(self, file_names: list[str]) -> str:
        return self._format_label_with_files("摘要", file_names)

    def _build_structured_content(self) -> dict:
        """输出结构化段落，方便前端直接渲染，无需拆文本。"""
        segments: list[dict] = []
        if self._ingest_plan.strip():
            cleaned_plan = re.sub(r"^[\s]*[-*•·●]\s*", "", self._ingest_plan.strip(), flags=re.MULTILINE)
            segments.append({
                "type": "thinking",
                "title": "正在制定分析思路",
                "markdown": cleaned_plan,
                "status": self._status_thought or "processing",
            })
        if self._ingest_tool_msg.strip():
            cleaned_tool = re.sub(r"^[\s]*[-*•·●]\s*", "", self._ingest_tool_msg.strip(), flags=re.MULTILINE)
            segments.append({
                "type": "tool",
                "title": "工具调用",
                "status": self._status_tool or ("end" if self._ingest_tool_done else "processing"),
                "markdown": cleaned_tool,
            })
        if self._ingest_summary.strip():
            cleaned_result = re.sub(r"^[\s]*[-*•·●]\s*", "", self._ingest_summary.strip(), flags=re.MULTILINE)
            segments.append({
                "type": "result",
                "title": "结果",
                "markdown": cleaned_result,
                "status": self._status_result or "processing",
            })
        return {"segments": segments}

    def _ensure_step(self, step_id: str, label: str, status: str = "thinking") -> dict:
        step = self._step_index.get(step_id)
        if step:
            if label:
                step["label"] = label
            if status:
                step["status"] = status
            return step
        step = {
            "id": step_id,
            "label": label or "处理中",
            "status": status or "thinking",
        }
        self._steps.append(step)
        self._step_index[step_id] = step
        return step

    def _ensure_child_step(self, parent_id: str, child_id: str, label: str, status: str = "thinking") -> dict:
        parent_label = self._tool_parent_label
        parent = self._ensure_step(parent_id, parent_label, "thinking")
        children = parent.setdefault("children", [])
        child = self._step_index.get(child_id)
        if child:
            if label:
                child["label"] = label
            if status:
                child["status"] = status
            return child
        child = {
            "id": child_id,
            "label": label or "处理中",
            "status": status or "thinking",
        }
        children.append(child)
        self._step_index[child_id] = child
        return child

    def _set_step_status(self, step_id: str, status: str, label: str | None = None) -> None:
        step = self._step_index.get(step_id)
        if not step:
            return
        if label:
            step["label"] = label
        if status:
            step["status"] = status

    def _set_step_detail(self, step_id: str, detail: str) -> None:
        step = self._step_index.get(step_id)
        if not step:
            return
        if not detail:
            return
        step["detailLines"] = [{"content": detail}]

    def _append_step_detail_line(self, step_id: str, content: str, line_id: str | None = None) -> None:
        if not content:
            return
        step = self._step_index.get(step_id)
        if not step:
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

    def _snapshot_steps(self) -> list[dict]:
        return json.loads(json.dumps(self._steps, ensure_ascii=False))

    def _flush_pending_summary_record(self, base_id: str) -> None:
        if not self._pending_summary_record:
            return
        if not self.conversation_id:
            self._pending_summary_record = None
            return
        answer = self._pending_summary_record.get("answer", "")
        metadata = self._pending_summary_record.get("metadata", {})
        summary_thought = self._pending_summary_record.get("summary_thought", "")
        snapshot_segments = []
        if isinstance(self._ingest_structured, dict) and self._ingest_structured.get("segments"):
            snapshot_segments = list(self._ingest_structured.get("segments") or [])
        elif summary_thought and summary_thought.strip():
            snapshot_segments = [
                {
                    "type": "thought",
                    "title": "思考",
                    "status": "end",
                    "markdown": summary_thought.strip(),
                },
                {
                    "type": "result",
                    "title": "结果",
                    "status": "end",
                    "markdown": answer,
                },
            ]
        else:
            snapshot_segments = [
                {
                    "type": "result",
                    "title": "结果",
                    "status": "end",
                    "markdown": answer,
                },
            ]
        steps_snapshot = self._snapshot_steps()
        snapshot_payload: dict[str, object] = {}
        if steps_snapshot:
            snapshot_payload["steps"] = steps_snapshot
        if snapshot_segments:
            snapshot_payload["segments"] = snapshot_segments
        snapshot_payload["suggestions"] = get_default_complaint_suggestions()
        if not snapshot_payload:
            snapshot_payload = {"segments": snapshot_segments}
        try:
            record_chat_assistant_message(
                self.user_id,
                base_id,
                self.conversation_id,
                answer=answer,
                metadata=metadata if isinstance(metadata, dict) else None,
                chat_message=snapshot_payload,
                snapshot=snapshot_payload,
            )
        except Exception:
            logger.debug("record_chat_assistant_message(summary_pipeline) failed", exc_info=True)
        self._pending_summary_record = None

    async def _record_user_chat(self, base_id: str, upload_label: str, source_url: str | None, attachments: list[dict] | None = None) -> None:
        if not self.conversation_id:
            return
        attachments = attachments or []
        if source_url and not attachments:
            attachments.append({"url": source_url, "original_filename": upload_label})
        try:
            record_chat_user_message(
                self.user_id,
                base_id,
                self.conversation_id,
                f"帮我分析举报件，附件是：{upload_label}",
                attachments=attachments or None,
                reference_blocks=self.reference_blocks or None,
            )
        except Exception:
            logger.debug("record_chat_user_message failed", exc_info=True)

    async def _stream_tool_planner(self, base_id: str, upload_label: str, file_size_mb: float | None) -> AsyncGenerator[str, None]:
        if not self.conversation_id:
            return
        if not self._ingest_message_id:
            self._ingest_message_id = f"{self.conversation_id}-ingest"
        # NOTE: 暂停规划模型调用，仅输出固定提示语；保留原调用逻辑以便后续恢复。
        # try:
        #     client = self._ensure_openai_client()
        # except Exception as exc:  # pragma: no cover - guard path
        #     logger.warning("skip tool planner chat: openai client init failed: %s", exc)
        #     return

        # 思考开始
        thought_detail = "我将按以下步骤处理：首先仔细审阅这些举报材料，随后为您整理出一份核心摘要，以便快速掌握关键要点。"
        self._status_thought = "start"
        self._ingest_structured = self._build_structured_content()
        node_id = f"{self._ingest_message_id}-thinking"
        step = self._ensure_step(node_id, "正在制定分析思路", "thinking")
        step["label"] = ""
        step["label_hidden"] = True
        self._set_step_detail(node_id, thought_detail)
        yield self._node_sse_event(
            base_id,
            build_standard_sse_payload(
                event="node_start",
                message_id=self._ingest_message_id,
                conversation_id=self.conversation_id or base_id,
                response_id=self.response_id,
                node_id=node_id,
                type="thinking",
                title="正在制定分析思路",
                label_hidden=True,
            ),
        )

        # messages = build_text_tool_planner_messages(upload_label, file_size_mb)
        # accumulated = ""
        # try:
        #     stream = await client.chat.completions.create(
        #         model=TOOL_PLANNER_MODEL,
        #         messages=messages,
        #         temperature=TOOL_PLANNER_TEMPERATURE,
        #         stream=True,
        #     )
        #     async for chunk in stream:
        #         delta = ""
        #         try:
        #             delta = "".join(chunk.choices[0].delta.content or [])
        #         except Exception:
        #             pass
        #         if not delta:
        #             continue
        #         accumulated += delta
        #         self._ingest_plan = accumulated
        #         self._status_thought = "processing"
        #         self._ingest_structured = self._build_structured_content()
        #         yield self._node_sse_event(
        #             base_id,
        #             build_standard_sse_payload(
        #                 event="node_delta",
        #                 message_id=self._ingest_message_id,
        #                 conversation_id=self.conversation_id or base_id,
        #                 response_id=self.response_id,
        #                 node_id=node_id,
        #                 delta=delta,
        #                 label_hidden=True,
        #             ),
        #         )
        # except Exception as exc:
        #     logger.warning("tool planner streaming failed: %s", exc)
        # plan阶段不单独落日志，等待最终总结时记录
        self._status_thought = "processing"
        self._ingest_structured = self._build_structured_content()
        self._ingest_plan = thought_detail
        yield self._node_sse_event(
            base_id,
            build_standard_sse_payload(
                event="node_delta",
                message_id=self._ingest_message_id,
                conversation_id=self.conversation_id or base_id,
                response_id=self.response_id,
                node_id=node_id,
                delta=thought_detail,
                line_id="ingest_thought",
                label_hidden=True,
            ),
        )
        self._status_thought = "end"
        self._ingest_structured = self._build_structured_content()
        self._set_step_status(node_id, "done", "")
        self._set_step_detail(node_id, thought_detail)
        yield self._node_sse_event(
            base_id,
            build_standard_sse_payload(
                event="node_end",
                message_id=self._ingest_message_id,
                conversation_id=self.conversation_id or base_id,
                response_id=self.response_id,
                node_id=node_id,
                status="completed",
                title="思考完成",
                label_hidden=True,
            ),
        )

    async def stream_tool_planner(self, base_id: str, upload_label: str, file_size_mb: float | None) -> AsyncGenerator[str, None]:
        async for chunk in self._stream_tool_planner(base_id, upload_label, file_size_mb):
            yield chunk

    async def _emit_tool_call_status(self, base_id: str, upload_label: str) -> AsyncGenerator[str, None]:
        if not self._ingest_message_id:
            self._ingest_message_id = f"{self.conversation_id or base_id}-ingest"
        self._ingest_tool_done = False
        # 工具进展：单纯描述执行中，避免和“将调用”类思考混在一起
        self._ingest_tool_msg = f"正在调用原文提取工具处理 {upload_label}，请稍候完成。"
        self._status_tool = "start"
        self._ingest_structured = self._build_structured_content()
        yield self._chat_sse_event(
            base_id,
            message_id=self._ingest_message_id,
            role="assistant",
            content=self._ingest_tool_msg,
            stage="tool_call",
            is_final=False,
            tool_status="running",
            structured_content=self._ingest_structured,
            type="tool",
            title="工具调用",
            status="start",
            render_mode="list",
            collapse_on_end=True,
        )

    async def _complete_tool_call_status(self, base_id: str, upload_label: str) -> AsyncGenerator[str, None]:
        if not self._ingest_message_id:
            self._ingest_message_id = f"{self.conversation_id or base_id}-ingest"
        self._ingest_tool_done = True
        # self._ingest_tool_msg = f"原文提取完成，附件：{upload_label}"
        self._status_tool = "end"
        self._ingest_structured = self._build_structured_content()
        yield self._chat_sse_event(
            base_id,
            message_id=self._ingest_message_id,
            role="assistant",
            content=self._ingest_tool_msg or "工具执行完成",
            stage="tool_call",
            is_final=True,
            tool_status="done",
            structured_content=self._ingest_structured,
            type="tool",
            title="工具调用",
            status="end",
            render_mode="list",
            collapse_on_end=True,
        )

    async def _stream_tool_summary(self, base_id: str, upload_label: str, extracted_text: str) -> AsyncGenerator[str, None]:
        if not self.conversation_id:
            return
        try:
            client = self._ensure_openai_client()
        except Exception as exc:  # pragma: no cover - guard path
            logger.warning("skip tool summary chat: openai client init failed: %s", exc)
            return

        # 结果开始
        self._status_result = "start"
        self._ingest_structured = self._build_structured_content()
        yield self._chat_sse_event(
            base_id,
            message_id=self._ingest_message_id,
            role="assistant",
            content="",
            stage="summary",
            is_final=False,
            structured_content=self._ingest_structured,
            type="result",
            title="结论",
            status="start",
            render_mode="streaming",
            collapse_on_end=False,
        )

        messages = build_text_tool_summary_messages(upload_label, extracted_text)
        if not self._ingest_message_id:
            self._ingest_message_id = f"{self.conversation_id}-ingest"
        accumulated = ""
        try:
            stream = await client.chat.completions.create(
                model=TOOL_SUMMARY_MODEL,
                messages=messages,
                temperature=TOOL_SUMMARY_TEMPERATURE,
                stream=True,
            )
            async for chunk in stream:
                delta = ""
                try:
                    delta = "".join(chunk.choices[0].delta.content or [])
                except Exception:
                    pass
                if not delta:
                    continue
                accumulated += delta
                # 更新总结文本
                self._ingest_summary = accumulated
                self._status_result = "processing"
                self._ingest_structured = self._build_structured_content()
                yield self._chat_sse_event(
                    base_id,
                    message_id=self._ingest_message_id,
                    role="assistant",
                    content=self._ingest_summary,
                    delta=delta,
                    stage="summary",
                    is_final=False,
                    structured_content=self._ingest_structured,
                    type="result",
                    title="结论",
                    status="processing",
                    render_mode="streaming",
                    collapse_on_end=False,
                )
        except Exception as exc:
            logger.warning("tool summary streaming failed: %s", exc)
        # 发送最终完成态并记录
        if accumulated:
            self._ingest_summary = accumulated
            self._status_result = "end"
            self._ingest_structured = self._build_structured_content()
            yield self._chat_sse_event(
                base_id,
                message_id=self._ingest_message_id,
                role="assistant",
                content=self._ingest_summary,
                stage="summary",
                is_final=True,
                structured_content=self._ingest_structured,
                type="result",
                title="结论",
                status="end",
                render_mode="streaming",
                collapse_on_end=False,
            )
            try:
                snapshot_payload = (
                    dict(self._ingest_structured)
                    if isinstance(self._ingest_structured, dict)
                    else {}
                )
                snapshot_payload["suggestions"] = get_default_complaint_suggestions()
                record_chat_assistant_message(
                    self.user_id,
                    base_id,
                    self.conversation_id,
                    answer=self._ingest_summary,
                    metadata={"stage": "summary", "tool": "text_extraction"},
                    chat_message=snapshot_payload,
                    snapshot=snapshot_payload,
                )
            except Exception:
                logger.debug("record_chat_assistant_message(summary) failed", exc_info=True)
    # ---------- 辅助 ----------
    def _locate_existing_originals(self, target_base_id: str, allowed_ext: set[str]) -> list[tuple[str, str]]:
        """在 original 目录下查找可复用的原始文件列表（按文件名排序）。"""
        try:
            base_dir = get_complaints_real_path(self.user_id, target_base_id, "original")
            manifest = load_manifest(self.user_id, target_base_id)
            uploaded_name_map = {
                str(f.get("stored_name")): f.get("uploaded_name")
                for f in manifest.get("original_files", [])
                if isinstance(f, dict)
            } if isinstance(manifest, dict) else {}
        except ValueError:
            return []
        if not os.path.isdir(base_dir):
            return []
        entries: list[tuple[str, str]] = []
        for entry in sorted(os.listdir(base_dir)):
            path = os.path.join(base_dir, entry)
            if os.path.isfile(path):
                ext = entry.rsplit(".", 1)[-1].lower() if "." in entry else ""
                if ext in allowed_ext:
                    entries.append((path, uploaded_name_map.get(entry) or entry))
        return entries

    async def _run_task1_ocr(
        self,
        base_id: str,
        files_meta: list[dict],
        *,
        upload_label: str,
        created_at_iso: str | None,
    ) -> AsyncGenerator[str, None]:
        if not self._ingest_message_id:
            self._ingest_message_id = f"{self.conversation_id or base_id}-ingest"
        parent_start = self._ensure_tool_parent_node(base_id)
        if parent_start:
            yield parent_start
        parent_id = self._tool_parent_node_id or f"{self._ingest_message_id}-tool"
        self._ensure_step(parent_id, self._tool_parent_label, "thinking")
        init_text = "现在开始阅读文件。此文件为扫描件，我将借助视觉技术对文件内容进行理解，处理进度：\n"
        self._append_step_detail_line(parent_id, init_text, "complaint_ocr-init")
        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=parent_id, delta=init_text, line_id="complaint_ocr-init"))

        # PDF/图片转换放线程池（保持上传顺序）
        image_entries: list[dict] = []
        global_index = 0
        for f in files_meta:
            ext = (f.get("ext") or "").lower()
            original_path = f.get("original_path")
            file_order = f.get("order") or 0
            prefix = f"{base_id}_{file_order:03d}"
            if ext == "pdf":
                paths: list[str] = await asyncio.to_thread(process_pdf, original_path, self.user_id, base_id, prefix)
            else:
                paths = await asyncio.to_thread(process_image, original_path, self.user_id, base_id, ext, prefix)
            for idx_in_file, path in enumerate(paths):
                image_entries.append(
                    {
                        "path": path,
                        "file_order": file_order,
                        "file_name": f.get("uploaded_name") or os.path.basename(path),
                        "page_in_file": idx_in_file + 1,
                        "index": global_index,
                    }
                )
                global_index += 1

        text_by_image: dict[str, str] = {}
        all_text: list[tuple[int, str]] = []
        image_mapping: dict[str, dict] = {}

        start_text = f"该扫描件实际为图片（共 {len(image_entries)} 张），我会按顺序逐张解读这些图片。\n"
        self._append_step_detail_line(parent_id, start_text, "complaint_ocr-start")
        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=parent_id, delta=start_text, line_id="complaint_ocr-start"))

        sem = asyncio.Semaphore(min(len(image_entries), _ocr_max_concurrency()))

        async def process_single_image(entry: dict):
            async with sem:
                ocr_start_time = time.time()
                ocr_result = await recognize_image_async(entry["path"])
                ocr_duration = time.time() - ocr_start_time
                return {
                    "index": entry.get("index", 0),
                    "file_order": entry.get("file_order"),
                    "file_name": entry.get("file_name"),
                    "page_in_file": entry.get("page_in_file"),
                    "text_content": ocr_result.get("text_content", ""),
                    "image_path": entry["path"],
                    "image_name": os.path.basename(entry["path"]),
                    "ocr_duration": ocr_duration,
                    "upload_file_id": ocr_result.get("upload_file_id"),
                    "sha256": ocr_result.get("sha256"),
                    "cache_hit": ocr_result.get("cache_hit"),
                }

        # 并发 OCR
        ocr_wall_start = time.time()
        max_duration = 0.0
        max_duration_label = ""
        tasks = [asyncio.create_task(process_single_image(entry)) for entry in image_entries]
        total_images = len(tasks)
        completed_count = 0
        for task in asyncio.as_completed(tasks):
            try:
                result = await task
            except Exception as exc:  # pragma: no cover
                error_text = f"❌ 视觉大模型图片理解失败: {exc}\n"
                self._append_step_detail_line(parent_id, error_text)
                yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=parent_id, delta=error_text, line_id="complaint_ocr-error"))
                continue

            completed_count += 1
            text_content = result.get("text_content", "")
            duration = float(result.get("ocr_duration", 0) or 0.0)
            if duration >= max_duration:
                max_duration = duration
                max_duration_label = f"{result.get('file_name') or result.get('image_name')} 第{result.get('page_in_file') or 1}页"
                logger.info("[OCR] longest image so far: %s (%.2fs)", max_duration_label, max_duration)
            # wall_elapsed = time.time() - ocr_wall_start
            # 🔍 已完成 {completed_count}/{total_images} 张，总耗时 {wall_elapsed:.2f} 秒，单张最大耗时 {max_duration:.2f} 秒。
            progress_text = f"第 {completed_count} 张图片已完成阅读理解，用时 {duration:.2f} 秒。\n"
            progress_line_id = f"complaint_ocr-progress-{completed_count}"
            self._append_step_detail_line(parent_id, progress_text, progress_line_id)
            yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=parent_id, delta=progress_text, line_id=progress_line_id))

            image_name = result["image_name"]
            text_by_image[image_name] = text_content
            all_text.append((result.get("index", 0), text_content))
            image_mapping[image_name] = {
                "upload_file_id": result.get("upload_file_id"),
                "sha256": result.get("sha256"),
                "text_content": text_content,
                "image_path": result.get("image_path"),
                "file_order": result.get("file_order"),
                "file_name": result.get("file_name"),
                "page_in_file": result.get("page_in_file"),
            }

        # 落盘文本（版本化写入 vN，并更新 manifest）
        ordered_texts = [text for _, text in sorted(all_text, key=lambda item: item[0])]
        raw_complete_text = "\n".join(ordered_texts)
        normalized_raw_text = raw_complete_text.rstrip("\n")
        self._latest_raw_text = normalized_raw_text
        manifest = load_manifest(self.user_id, base_id)
        if created_at_iso:
            manifest.setdefault("created_at", created_at_iso)
        text_version = next_version(manifest, "text")
        version_dir = stage_dir(self.user_id, base_id, "text", text_version)
        os.makedirs(version_dir, exist_ok=True)
        version_raw_path = os.path.join(version_dir, "final.txt")
        version_mapping_path = os.path.join(version_dir, "final_mapping.json")
        await self._write_file(version_raw_path, normalized_raw_text + ("\n" if normalized_raw_text else ""))
        await self._write_json(version_mapping_path, image_mapping)
        write_meta(
            self.user_id,
            base_id,
            "text",
            text_version,
            source="auto",
            base_versions={},
            extra={"task": 1},
        )
        manifest = update_manifest(manifest, "text", text_version)
        save_manifest(self.user_id, base_id, manifest)

        files_text_map: dict[tuple[int, str], list[tuple[int, str]]] = {}
        for entry in image_mapping.values():
            file_order = entry.get("file_order") or 0
            file_name = entry.get("file_name") or ""
            page_in_file = entry.get("page_in_file") or 0
            text_content = entry.get("text_content") or ""
            key = (file_order, file_name)
            files_text_map.setdefault(key, []).append((page_in_file, text_content))
        raw_text_files = []
        for (file_order, file_name), entries in sorted(files_text_map.items(), key=lambda item: item[0][0]):
            ordered = [text for _, text in sorted(entries, key=lambda item: item[0])]
            raw_text_files.append(
                {
                    "order": file_order,
                    "name": file_name,
                    "pages": [
                        {"page": page, "content": text}
                        for page, text in sorted(entries, key=lambda item: item[0])
                    ],
                }
            )
        raw_text_payload = {
            "event": "artifacts",
            "artifacts": {
                "rawText": {
                    "files": raw_text_files,
                }
            },
        }
        yield self._system_sse_event(base_id, raw_text_payload)
        done_text = f"✅ 文件阅读完毕。目前已完整掌握举报件内容，所有图片均已转换为文字信息。\n"
        self._append_step_detail_line(parent_id, done_text, "complaint_ocr-done")
        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=parent_id, delta=done_text, line_id="complaint_ocr-done"))

    async def _run_tool_summary_pipeline(self, base_id: str) -> AsyncGenerator[str, None]:
        """调用 SummaryPipeline 自动生成摘要，并在工具段新增一行进度。"""
        # 如果没有会话或全文文本，跳过生成摘要，不影响主流程。
        if not self.conversation_id or not self._latest_raw_text:
            logger.warning(
                "skip summary pipeline: missing conversation/text, base_id=%s conversation_id=%s has_text=%s",
                base_id,
                self.conversation_id,
                bool(self._latest_raw_text),
            )
            return

        summary_pipeline = SummaryPipeline(self.user_id, base_id, enable_thinking=self.enable_thinking)
        if not self._ingest_message_id:
            self._ingest_message_id = f"{self.conversation_id or base_id}-ingest"
        summary_node_id = "complaint_summary"
        summary_label = self._summary_label
        self._ensure_step(summary_node_id, summary_label, "thinking")
        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_start", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, type="task", title=summary_label))

        # 自动生成摘要（透传 task=2 进度事件）
        summary_streamed = False
        summary_thought_accum = ""
        summary_thought_closed = False
        summary_node_closed = False
        summary_content_started = False
        summary_accumulated = ""
        title_preview_prev = ""
        title_generation_started = False
        try:
            async for chunk in summary_pipeline.stream():
                for line in str(chunk).splitlines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        payload = json.loads(line.split("data:", 1)[1].strip())
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("event") == "conversation_title":
                        title = payload.get("conversation_title_preview") or payload.get("conversation_title")
                        if isinstance(title, str) and title.strip():
                            title = title.strip()
                            if title.startswith(title_preview_prev):
                                delta = title[len(title_preview_prev):]
                            else:
                                delta = title
                            title_preview_prev = title
                            title_generation_started = True
                            yield ComplaintSSEEvent(
                                base_id=base_id,
                                extra={
                                    "event": "title_generation",
                                    "title": delta,
                                    "conversation_id": self.conversation_id or base_id,
                                },
                            ).to_sse(user_id=self.user_id)
                        continue
                    message = payload.get("message")
                    if isinstance(message, str) and message.strip() and not message.strip().startswith("📝 会话标题："):
                        summary_message = message.strip()
                        self._append_step_detail_line(summary_node_id, summary_message, "summary_message")
                        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, delta=summary_message, line_id="summary_message"))
                    summary_thought = payload.get("summary_thought")
                    if isinstance(summary_thought, str):
                        summary_thought_accum = summary_thought
                        self._append_step_detail_line(summary_node_id, summary_thought_accum, "summary_thought")
                        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, delta=summary_thought_accum, line_id="summary_thought"))
                    summary_thought_delta = payload.get("summary_thought_delta")
                    if isinstance(summary_thought_delta, str):
                        summary_thought_accum += summary_thought_delta
                        self._append_step_detail_line(summary_node_id, summary_thought_accum, "summary_thought")
                        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, delta=summary_thought_delta, line_id="summary_thought"))
                    summary_delta = payload.get("summary_delta")
                    if isinstance(summary_delta, str) and summary_delta:
                        summary_streamed = True
                        if not summary_content_started:
                            summary_content_started = True
                        if not summary_thought_closed and summary_thought_accum:
                            yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_end", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, status="completed", title=summary_label))
                            summary_thought_closed = True
                        yield self._node_sse_event(base_id, build_standard_sse_payload(event="content_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, delta=summary_delta))
                        summary_accumulated += summary_delta
                    summary_markdown = payload.get("summary_markdown")
                    if isinstance(summary_markdown, str) and summary_markdown.strip() and not summary_streamed:
                        if not summary_content_started:
                            summary_content_started = True
                        if not summary_thought_closed and summary_thought_accum:
                            yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_end", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, status="completed", title=summary_label))
                            summary_thought_closed = True
                        parts = summary_markdown.splitlines()
                        for idx, part in enumerate(parts):
                            segment = part.rstrip()
                            if not segment:
                                yield self._node_sse_event(base_id, build_standard_sse_payload(event="content_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, delta="\n"))
                                summary_accumulated += "\n"
                                continue
                            prefix = "" if idx == 0 else "\n"
                            summary_accumulated += f"{prefix}{segment}"
                            yield self._node_sse_event(base_id, build_standard_sse_payload(event="content_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, delta=f"{prefix}{segment}"))
                    status = payload.get("status")
                    if status in {"completed", "error"}:
                        if not summary_thought_closed and summary_thought_accum:
                            yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_end", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, status="completed", title=summary_label))
                            summary_thought_closed = True
                        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_end", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, status="completed" if status == "completed" else "error", title=summary_label))
                        summary_node_closed = True
                        self._set_step_status(summary_node_id, "done", summary_label)
            if title_generation_started:
                yield ComplaintSSEEvent(
                    base_id=base_id,
                    extra={
                        "event": "title_generation_done",
                        "title": title_preview_prev,
                        "conversation_id": self.conversation_id or base_id,
                    },
                ).to_sse(user_id=self.user_id)
            if summary_accumulated:
                if not summary_thought_accum.strip():
                    summary_text = summary_accumulated.strip()
                    if summary_node_closed:
                        self._append_step_detail_line(summary_node_id, summary_text, "summary_content")
                        self._set_step_status(summary_node_id, "done", summary_label)
                    else:
                        self._append_step_detail_line(summary_node_id, summary_text, "summary_content")
                        yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_delta", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, title=summary_label, delta=summary_text, line_id="summary_content"))
                if not summary_node_closed:
                    yield self._node_sse_event(base_id, build_standard_sse_payload(event="node_end", message_id=self._ingest_message_id, conversation_id=self.conversation_id or base_id, response_id=self.response_id, node_id=summary_node_id, status="completed", title=summary_label))
                    summary_node_closed = True
                self._ingest_summary = summary_accumulated.strip()
                self._status_result = "end"
                self._ingest_structured = self._build_structured_content()
                self._pending_summary_record = {
                    "answer": self._ingest_summary,
                    "metadata": {"stage": "summary", "tool": "summary_pipeline"},
                    "summary_thought": summary_thought_accum,
                }
        except Exception as exc:
            logger.exception("summary pipeline stream failed, base_id=%s, error=%s", base_id, exc)
        else:
            pass

    # ---------- 主流程 ----------
    async def generate_from_upload(self) -> AsyncGenerator[str, None]:
        """主流程：校验输入 → 复制原件 → task0 → task1。"""
        allowed_ext = {"pdf", "png", "jpg", "jpeg"}
        use_existing = bool(self.resume_base_id or self.reuse_existing_original)
        filename = self.upload_display_name
        base_id = self.resume_base_id or ""
        analysis_started_at = datetime.utcnow()
        analysis_started_iso = analysis_started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        analysis_started_perf = time.perf_counter()
        upload_files: list[tuple[str, str]] = []
        if self.input_files:
            upload_files.extend(self.input_files)
        elif self.input_file_path:
            upload_files.append((self.input_file_path, self.upload_display_name))

        # Step 1: 校验输入/原件来源
        if use_existing:
            if not base_id:
                yield ComplaintSSEEvent(task=0, status="error", message="缺少 base_id 参数").to_sse(user_id=self.user_id)
                return
            try:
                base_id = validate_base_id(base_id)
            except Exception as exc:
                logger.error(f"无效的 base_id: {exc}")
                yield ComplaintSSEEvent(task=0, status="error", message=f"无效的 base_id: {exc}").to_sse(user_id=self.user_id)
                return
            existing_files = self._locate_existing_originals(base_id, allowed_ext)
            if not existing_files:
                yield ComplaintSSEEvent(base_id=base_id, task=0, status="error", message="未找到可用的原始文件").to_sse(user_id=self.user_id)
                return
            upload_files = existing_files
            filename = filename or (existing_files[0][1] if existing_files else "")
        else:
            if not upload_files:
                yield ComplaintSSEEvent(task=0, status="error", message="请求中没有文件").to_sse(user_id=self.user_id)
                return
            if len(upload_files) > 5:
                yield ComplaintSSEEvent(task=0, status="error", message="最多上传 5 个文件").to_sse(user_id=self.user_id)
                return
            for _, display_name in upload_files:
                if not display_name or "." not in display_name:
                    yield ComplaintSSEEvent(task=0, status="error", message="文件类型不支持").to_sse(user_id=self.user_id)
                    return
                ext_check = display_name.rsplit(".", 1)[1].lower()
                if ext_check not in allowed_ext:
                    yield ComplaintSSEEvent(task=0, status="error", message="文件类型不支持").to_sse(user_id=self.user_id)
                    return
            if self.base_id_override:
                try:
                    base_id = validate_base_id(self.base_id_override)
                except Exception as exc:
                    yield ComplaintSSEEvent(task=0, status="error", message=f"无效的会话ID: {exc}").to_sse(user_id=self.user_id)
                    return
            else:
                base_id = uuid.uuid4().hex

        # Step 2: 准备目录、复制原件
        os.makedirs(get_user_dir(self.user_id), exist_ok=True)
        image_folder = get_complaints_real_path(self.user_id, base_id, "images")
        original_folder = get_complaints_real_path(self.user_id, base_id, "original")
        os.makedirs(image_folder, exist_ok=True)
        os.makedirs(original_folder, exist_ok=True)

        stored_files: list[dict] = []
        if not use_existing:
            for idx, (source_path, display_name) in enumerate(upload_files, start=1):
                ext = display_name.rsplit(".", 1)[1].lower()
                stored_name = f"{idx:03d}_{base_id}.{ext}"
                original_path = os.path.join(original_folder, stored_name)
                file_size_mb = None
                file_size_bytes: int | None = None
                try:
                    shutil.copyfile(source_path, original_path)
                    try:
                        file_size_bytes = os.path.getsize(source_path)
                        file_size_mb = file_size_bytes / (1024 * 1024)
                    except Exception:
                        file_size_mb = None
                        file_size_bytes = None
                except Exception as copy_exc:
                    yield ComplaintSSEEvent(base_id=base_id, task=0, status="error", message=f"保存原始文件失败: {copy_exc}").to_sse(user_id=self.user_id)
                    return
                stored_files.append(
                    {
                        "order": idx,
                        "ext": ext,
                        "original_path": original_path,
                        "stored_name": stored_name,
                        "uploaded_name": display_name,
                        "size_bytes": file_size_bytes,
                    }
                )

            try:
                # 新建会话时写入用户上传的文件名，避免前端显示 base_id
                await touch_user_history(self.user_id, base_id, name="新会话" or filename)
            except Exception:
                pass
        else:
            for idx, (existing_path, display_name) in enumerate(upload_files, start=1):
                ext = display_name.rsplit(".", 1)[1].lower()
                stored_files.append(
                    {
                        "order": idx,
                        "ext": ext,
                        "original_path": existing_path,
                        "stored_name": os.path.basename(existing_path),
                        "uploaded_name": display_name,
                        "size_bytes": os.path.getsize(existing_path) if os.path.exists(existing_path) else None,
                    }
                )

        path_prefix_clean = (self.url_prefix or "").strip("/")
        base_source_url_prefix = f"{'/' + path_prefix_clean if path_prefix_clean else ''}/api/files/{self.user_id}/{base_id}/original/"

        # 先落 manifest，确保上传文件名被记录
        try:
            manifest = load_manifest(self.user_id, base_id)
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
            # 兼容单文件字段，沿用第一个
            if stored_files:
                manifest["original_file"] = {
                    "uploaded_name": stored_files[0].get("uploaded_name"),
                    "stored_name": stored_files[0].get("stored_name"),
                    "path": relative_to_complaints(stored_files[0].get("original_path")),
                    "url": f"{base_source_url_prefix}{stored_files[0].get('stored_name')}",
                    "size_bytes": stored_files[0].get("size_bytes"),
                }
            manifest.setdefault("created_at", manifest.get("created_at") or analysis_started_iso)
            save_manifest(self.user_id, base_id, manifest)
        except Exception:
            logger.exception("failed to persist manifest after upload for base_id=%s", base_id)

        self.conversation_id = self.conversation_id or f"{base_id}-ingest"
        self._ingest_message_id = self._ingest_message_id or f"{self.conversation_id}-ingest"
        self._ingest_plan = ""
        self._ingest_tool_msg = ""
        self._ingest_tool_done = False
        self._ingest_summary = ""
        self._ingest_structured = None
        self._status_thought = None
        self._status_tool = None
        self._status_result = None
        self._tool_parent_label = self._format_read_label(
            [f.get("uploaded_name") or "" for f in stored_files]
        )
        self._summary_label = self._format_summary_label(
            [f.get("uploaded_name") or "" for f in stored_files]
        )
        files_with_url = [
            {
                **f,
                "url": f"{base_source_url_prefix}{f.get('stored_name')}",
            }
            for f in stored_files
        ]
        server_files = [
            {
                "order": f.get("order") or 0,
                "name": f.get("uploaded_name"),
                "url": f.get("url"),
            }
            for f in files_with_url
            if f.get("url")
        ]
        preview_url = server_files[0]["url"] if server_files else None
        artifacts_payload = {
            "event": "artifacts",
            "artifacts": {
                "rawFile": {
                    "filePreviewUrl": preview_url,
                    "serverFiles": server_files,
                    "selectedFileIndex": 0,
                }
            },
        }
        yield self._system_sse_event(base_id, artifacts_payload)
        upload_label = self.upload_display_name or filename or (files_with_url[0]["uploaded_name"] if files_with_url else "")
        total_size_bytes = sum([f.get("size_bytes") or 0 for f in files_with_url]) if files_with_url else None
        total_size_mb = (total_size_bytes / (1024 * 1024)) if total_size_bytes is not None else None
        await self._record_user_chat(
            base_id,
            upload_label,
            files_with_url[0]["url"] if files_with_url else None,
            attachments=[
                {"url": f.get("url"), "original_filename": f.get("uploaded_name") or upload_label}
                for f in files_with_url
                if f.get("url")
            ] or None,
        )
        # Step 3: 触发 task1（跳过 task0 的 SSE 输出）
        async for chunk in self._run_task1_ocr(
            base_id,
            files_with_url,
            upload_label=upload_label,
            created_at_iso=analysis_started_iso,
        ):
            yield chunk

        tool_end_evt = self._complete_tool_parent_node(base_id)
        if tool_end_evt:
            yield tool_end_evt

        # Step 3.5: 自动生成摘要，工具段新增一行进度（可关闭）
        if self.enable_summary:
            async for chunk in self._run_tool_summary_pipeline(base_id):
                yield chunk

        analysis_finished_at = datetime.utcnow()
        duration_ms = int((time.perf_counter() - analysis_started_perf) * 1000)
        if self.enable_summary:
            self._flush_pending_summary_record(base_id)
        # 结论生成暂不调用
        # if self._latest_raw_text:
        #     async for chunk in self._stream_tool_summary(base_id, upload_label, self._latest_raw_text):
        #         yield chunk
        logger.info(
            "[TEXT_ONLY_SSE][%s] pipeline finished at %s (started %s) duration_ms=%s",
            base_id,
            analysis_finished_at.isoformat() + "Z",
            analysis_started_at.isoformat() + "Z",
            duration_ms,
        )

    # ---------- 类方法：用户保存原文 ----------
    @classmethod
    async def save_user_text_version(cls, user_id: str, base_id: str, document_text: str) -> dict:
        start_dt = datetime.utcnow()
        start_perf = time.perf_counter()
        manifest = load_manifest(user_id, base_id)
        text_version = next_version(manifest, "text")
        logger.info(
            "[TEXT_SAVE] start base_id=%s version=%s source=user at=%s",
            base_id,
            text_version,
            start_dt.isoformat() + "Z",
        )
        version_dir = stage_dir(user_id, base_id, "text", text_version)
        raw_path = os.path.join(version_dir, "final.txt")
        mapping_path = os.path.join(version_dir, "final_mapping.json")

        await cls._write_file(raw_path, str(document_text))
        await cls._write_json(mapping_path, {})

        write_meta(
            user_id,
            base_id,
            "text",
            text_version,
            source="user",
            base_versions={},
            extra={"task": 1},
        )
        manifest = update_manifest(manifest, "text", text_version)
        save_manifest(user_id, base_id, manifest)
        end_dt = datetime.utcnow()
        duration_ms = int((time.perf_counter() - start_perf) * 1000)

        logger.info(
            "[TEXT_SAVE] end base_id=%s version=%s duration_ms=%s",
            base_id,
            text_version,
            duration_ms,
        )
        return {
            "base_id": base_id,
            "text_version": text_version,
            "source": "user",
            "task": 1,
            "start_time": start_dt.isoformat() + "Z",
            "end_time": end_dt.isoformat() + "Z",
            "duration_ms": duration_ms,
        }


async def run_text_pipeline(
    user_id: str,
    *,
    input_file_path: str | None = None,
    input_files: list[tuple[str, str]] | None = None,
    upload_display_name: str = "",
    url_prefix: str = "",
    resume_base_id: str | None = None,
    reuse_existing_original: bool = False,
    enable_thinking: bool = False,
) -> AsyncGenerator[str, None]:
    """函数式入口，兼容调用方，内部调用类方法。"""
    #TODO mock一个sse消息

    pipeline = TextPipeline(
        user_id,
        input_file_path=input_file_path,
        input_files=input_files,
        upload_display_name=upload_display_name,
        url_prefix=url_prefix,
        resume_base_id=resume_base_id,
        reuse_existing_original=reuse_existing_original,
    )
    pipeline.enable_thinking = bool(enable_thinking)
    async for chunk in pipeline.generate_from_upload():
        yield chunk


async def save_user_text_version(user_id: str, base_id: str, document_text: str) -> dict:
    """模块级兼容入口，委托给 TextPipeline 类方法。"""
    return await TextPipeline.save_user_text_version(user_id, base_id, document_text)
