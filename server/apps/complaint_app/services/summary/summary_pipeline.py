from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Tuple, AsyncGenerator
from fastapi.concurrency import run_in_threadpool

from apps.complaint_app.core.versioning import (
    load_manifest,
    next_version,
    save_manifest,
    stage_dir,
    update_manifest,
    write_meta,
)
from apps.complaint_app.services.summary.llm_summary import (
    ensure_summary_history_markdown,
    stream_summary_markdown,
)
from apps.complaint_app.history_storage import touch_user_history
from apps.complaint_app.services.summary.conversation_title import (
    generate_conversation_title_stream,
    _truncate_title,
)
from apps.complaint_app.core.event_types import ComplaintEventType
from apps.complaint_app.utils import _extract_markdown_tables, _markdown_table_to_rows, _strip_markdown_tables
from apps.complaint_app.services.pipeline_base import ComplaintSSEEvent
from utils.logger import get_logger
from utils.storage_paths import get_complaints_real_path

logger = get_logger(__name__)


class SummaryPipeline:
    """生成或保存摘要版本的管线。"""

    def __init__(
        self,
        user_id: str,
        base_id: str,
        *,
        summary_text: str | None = None,
        enable_thinking: bool = False,
    ):
        self.user_id = user_id
        self.base_id = base_id
        self.summary_text = summary_text
        self.enable_thinking = enable_thinking

    @staticmethod
    async def _write_text(path: str, content: str | None) -> None:
        def _sync_write():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content or "")

        await run_in_threadpool(_sync_write)

    @staticmethod
    async def _write_json(path: str, payload: dict[str, Any]) -> None:
        def _sync_write():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)

        await run_in_threadpool(_sync_write)

    def _pick_text_content(self) -> Tuple[str | None, int]:
        """加载最新原文文本（取 manifest 当前版本），返回文本与实际使用的版本号。"""
        manifest = load_manifest(self.user_id, self.base_id)
        resolved_version = manifest.get("text", {}).get("current") or 0
        candidate_paths = []
        if resolved_version:
            version_dir = stage_dir(self.user_id, self.base_id, "text", resolved_version)
            candidate_paths.append(os.path.join(version_dir, "final.txt"))
        try:
            text_dir = get_complaints_real_path(self.user_id, self.base_id, "texts")
            candidate_paths.append(os.path.join(text_dir, f"{self.base_id}.txt"))
        except Exception:
            pass

        for path in candidate_paths:
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        return handle.read(), resolved_version
                except Exception:
                    continue
        return None, resolved_version

    async def _generate_summary(self) -> Tuple[str, str, dict, list, int, str]:
        """模型生成摘要，返回 markdown、思考、json、表格列表、基准原文版本、schema 版本。"""
        text_content, resolved_version = self._pick_text_content()
        if text_content is None:
            raise FileNotFoundError("未找到可用全文文本")
        extracted_tables = _extract_markdown_tables(text_content)
        sanitized_text = _strip_markdown_tables(text_content)
        summary_markdown, summary_thought = await generate_summary_structured(sanitized_text, enable_thinking=self.enable_thinking)
        summary_markdown = ensure_summary_history_markdown(summary_markdown)
        summary_json = {"_schema": "raw", "_raw": summary_markdown}
        summary_schema_version = "raw"
        summary_tables_payload = []
        if extracted_tables:
            append_lines = []
            for idx, tbl in enumerate(extracted_tables):
                rows = _markdown_table_to_rows(tbl)
                table_id = f"table-{idx + 1}"
                summary_tables_payload.append(
                    {
                        "id": table_id,
                        "title": f"银行卡或流水表 {idx + 1}",
                        "category": "银行卡或流水表",
                        "markdown": tbl.strip(),
                        "rows": rows,
                        "source": "original_markdown",
                    }
                )
                append_lines.append(tbl.strip())
                append_lines.append("")
            summary_markdown = (summary_markdown or "").rstrip() + "\n\n" + "\n".join(append_lines).strip()
        return summary_markdown, summary_thought, summary_json, summary_tables_payload, resolved_version, (summary_schema_version or "raw")

    async def _save_summary(
        self,
        summary_markdown: str,
        summary_json: dict,
        summary_tables_payload: list,
        *,
        base_document_version: int,
        source: str,
        start_dt: datetime,
        start_perf: float,
        summary_schema_version: str = "v1",
        summary_result: dict | None = None,
    ) -> dict:
        """将摘要写入版本目录并更新 manifest，返回标准 payload。"""
        manifest = load_manifest(self.user_id, self.base_id)
        summary_version = next_version(manifest, "summary")

        summary_dir = stage_dir(self.user_id, self.base_id, "summary", summary_version)
        os.makedirs(summary_dir, exist_ok=True)
        summary_json_path = os.path.join(summary_dir, "final.json")
        summary_md_path = os.path.join(summary_dir, "final.md")
        await self._write_json(summary_json_path, summary_json if isinstance(summary_json, dict) else {"_raw": summary_markdown})
        await self._write_text(summary_md_path, summary_markdown)
        write_meta(
            self.user_id,
            self.base_id,
            "summary",
            summary_version,
            source=source,
            base_versions={"base_document_version": base_document_version},
            extra={"task": 2},
        )
        manifest = update_manifest(manifest, "summary", summary_version)
        save_manifest(self.user_id, self.base_id, manifest)
        end_dt = datetime.utcnow()
        duration_ms = int((time.perf_counter() - start_perf) * 1000)
        logger.info(
            "[SUMMARY_PIPELINE][end] base_id=%s version=%s source=%s duration_ms=%s",
            self.base_id,
            summary_version,
            source,
            duration_ms,
        )
        return {
            "base_id": self.base_id,
            "summary_version": summary_version,
            "base_document_version": base_document_version,
            "source": source,
            "task": 2,
            "content": summary_markdown,
            "summary_json": summary_json,
            "summary_tables": summary_tables_payload,
            # "summary_json_path": summary_json_path,
            # "summary_markdown_path": summary_md_path,
            "summary_schema_version": summary_schema_version,
            "summary": summary_result or {"content": summary_markdown, "tables": summary_tables_payload or None},
            "start_time": start_dt.isoformat() + "Z",
            "end_time": end_dt.isoformat() + "Z",
            "duration_ms": duration_ms,
        }

    async def run(self) -> dict:
        """用户提供摘要时的直接保存入口，不包含模型生成和 SSE。"""
        if not self.summary_text:
            raise ValueError("summary_text is required for run()")
        start_dt = datetime.utcnow()
        start_perf = time.perf_counter()
        manifest = load_manifest(self.user_id, self.base_id)
        base_document_version = manifest.get("text", {}).get("current") or 0
        summary_markdown = str(self.summary_text)
        summary_json = {"_schema": "v1", "_raw": summary_markdown}
        summary_schema_version = summary_json.get("_schema") or "v1"
        summary_tables_payload: list = []

        extracted_tables = _extract_markdown_tables(summary_markdown)
        if extracted_tables:
            for idx, tbl in enumerate(extracted_tables):
                rows = _markdown_table_to_rows(tbl)
                table_id = f"table-{idx + 1}"
                summary_tables_payload.append(
                    {
                        "id": table_id,
                        "title": f"表格 {idx + 1}",
                        "category": "",
                        "markdown": tbl.strip(),
                        "rows": rows,
                        "source": "user_markdown",
                    }
                )
        summary_result = {"content": summary_markdown}
        if summary_tables_payload:
            summary_result["tables"] = summary_tables_payload
        summary_json = ensure_summary_history_section(summary_json, summary_markdown)
        summary_markdown = ensure_summary_history_markdown(summary_markdown)
        return await self._save_summary(
            summary_markdown,
            summary_json,
            summary_tables_payload,
            base_document_version=base_document_version,
            source="user",
            start_dt=start_dt,
            start_perf=start_perf,
            summary_schema_version=summary_schema_version,
            summary_result=summary_result,
        )

    async def stream(self) -> AsyncGenerator[str, None]:
        """自动生成摘要的 SSE 入口。"""
        source = "auto"
        try:
            yield ComplaintSSEEvent(
                base_id=self.base_id,
                task=2,
                status="started",
                extra={"source": source},
            ).to_sse(user_id=self.user_id)

            # 自动生成摘要（流式）
            start_dt = datetime.utcnow()
            start_perf = time.perf_counter()
            text_content, resolved_version = self._pick_text_content()
            if text_content is None:
                raise FileNotFoundError("未找到可用全文文本")
            extracted_tables = _extract_markdown_tables(text_content)
            sanitized_text = _strip_markdown_tables(text_content)
            summary_markdown = ""
            summary_thought = ""
            async for delta in stream_summary_markdown(sanitized_text, enable_thinking=self.enable_thinking):
                reasoning_delta = delta.get("reasoning_delta")
                content_delta = delta.get("content_delta")
                if isinstance(reasoning_delta, str) and reasoning_delta:
                    summary_thought += reasoning_delta
                    yield ComplaintSSEEvent(
                        base_id=self.base_id,
                        task=2,
                        status="processing",
                        extra={"summary_thought_delta": reasoning_delta},
                    ).to_sse(user_id=self.user_id)
                if isinstance(content_delta, str) and content_delta:
                    summary_markdown += content_delta
                    yield ComplaintSSEEvent(
                        base_id=self.base_id,
                        task=2,
                        status="processing",
                        extra={"summary_delta": content_delta},
                    ).to_sse(user_id=self.user_id)

            summary_markdown = ensure_summary_history_markdown(summary_markdown)
            summary_json = {"_schema": "raw", "_raw": summary_markdown}
            summary_schema_version = "raw"
            summary_tables_payload = []
            if extracted_tables:
                append_lines = []
                for idx, tbl in enumerate(extracted_tables):
                    rows = _markdown_table_to_rows(tbl)
                    table_id = f"table-{idx + 1}"
                    summary_tables_payload.append(
                        {
                            "id": table_id,
                            "title": f"银行卡或流水表 {idx + 1}",
                            "category": "银行卡或流水表",
                            "markdown": tbl.strip(),
                            "rows": rows,
                            "source": "original_markdown",
                        }
                    )
                    append_lines.append(tbl.strip())
                    append_lines.append("")
                summary_markdown = (summary_markdown or "").rstrip() + "\n\n" + "\n".join(append_lines).strip()
            summary_result = {"content": summary_markdown}
            if summary_tables_payload:
                summary_result["tables"] = summary_tables_payload
            base_document_version = resolved_version
            # 会话标题（基于摘要，流式生成）
            conversation_title_preview = None
            conversation_title_final = None
            async for partial_title in generate_conversation_title_stream(summary_markdown):
                capped_title, preview = _truncate_title(partial_title, 25)
                conversation_title_final = capped_title or conversation_title_final
                conversation_title_preview = preview
                yield ComplaintSSEEvent(
                    base_id=self.base_id,
                    task=2,
                    status="processing",
                    message=f"📝 会话标题：{preview}",
                    extra={
                        "event": ComplaintEventType.CONVERSATION_TITLE.value,
                        "conversation_title": capped_title,
                        "conversation_title_preview": preview,
                    },
                ).to_sse(user_id=self.user_id)
            result = await self._save_summary(
                summary_markdown,
                summary_json,
                summary_tables_payload,
                base_document_version=base_document_version,
                source=source,
                start_dt=start_dt,
                start_perf=start_perf,
                summary_schema_version=summary_schema_version,
                summary_result=summary_result,
            )

            extra_payload = {k: v for k, v in result.items() if k not in {"task", "status", "message", "base_id"}}
            extra_payload["summary_markdown"] = summary_markdown
            if summary_thought:
                extra_payload["summary_thought"] = summary_thought
            if conversation_title_preview:
                extra_payload.setdefault("conversation_title", conversation_title_preview)
            if conversation_title_final:
                try:
                    # 将模型生成的标题写回历史，避免会话名回退为“新会话”
                    await touch_user_history(self.user_id, self.base_id, name=conversation_title_final)
                except Exception:
                    logger.exception("Failed to persist conversation title", extra={"base_id": self.base_id})
            yield ComplaintSSEEvent(
                base_id=self.base_id,
                task=2,
                status="completed",
                extra=extra_payload,
            ).to_sse(user_id=self.user_id)
        except FileNotFoundError as exc:
            logger.error(f"FileNotFoundError: {exc}")
            yield ComplaintSSEEvent(
                base_id=self.base_id,
                task=2,
                status="error",
                message=str(exc) or "未找到全文文本，请先调用 /complaint/text",
                agent_say="⚠️ 未找到全文文本，无法生成摘要。",
            ).to_sse(user_id=self.user_id)
        except Exception as exc:  # pragma: no cover - streaming safety
            logger.error(f"Exception: {exc}")
            yield ComplaintSSEEvent(
                base_id=self.base_id,
                task=2,
                status="error",
                message=f"摘要生成失败: {exc}",
                agent_say="⚠️ 摘要生成失败，请稍后重试。",
            ).to_sse(user_id=self.user_id)
