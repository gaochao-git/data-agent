from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from utils.logger import get_logger

from ..base_tool import Tool, ToolContext
from ...trace_logger import trace_detail_logger, trace_enabled, trace_tag

logger = get_logger(__name__)


def _docs_root() -> Path:
    raw = (os.getenv("COMPLAINT_LAWS_ROOT") or "/data/aigc/fxcjg_data/complaints/laws").strip()
    return Path(raw).expanduser().resolve()


def _resolve_safe_filename(raw_filename: str) -> Path:
    filename = str(raw_filename or "").strip()
    if not filename:
        raise ValueError("filename is required")
    if not filename.endswith(".txt"):
        raise ValueError("filename must end with .txt")
    root = _docs_root()
    candidate = Path(filename).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except Exception as exc:
        raise ValueError("filename is outside allowed docs root") from exc
    if not candidate.is_file():
        raise ValueError("document file not found")
    return candidate


def _truncate(text: str, limit: int = 12000) -> tuple[str, bool]:
    value = str(text or "")
    if len(value) <= limit:
        return value, False
    return f"{value[:limit]}\n\n[output truncated: omitted {len(value) - limit} chars]", True


def _preview(text: str, limit: int = 500) -> dict[str, Any]:
    value = str(text or "")
    truncated = len(value) > limit
    return {"text": value[:limit], "chars": len(value), "truncated": truncated}


def registry() -> Tool:
    def _handler(payload: Dict[str, Any], context: ToolContext):
        filename = str((payload or {}).get("filename") or "").strip()
        trace = dict(getattr(context, "metadata", {}) or {})
        logger.info("[complaint_api] document_read_start user_id=%s base_id=%s file_id=%s stage=%s filename=%s", trace.get("user_id") or "", trace.get("base_id") or "", trace.get("file_id") or "", trace.get("stage") or "", filename)
        if trace_enabled():
            trace_detail_logger(trace).info("[document_read] %s document_read_start filename=%s", trace_tag(trace), filename)
        try:
            resolved = _resolve_safe_filename(filename)
            content = resolved.read_text(encoding="utf-8")
            output, truncated = _truncate(content)
            logger.info("[complaint_api] document_read_done user_id=%s base_id=%s file_id=%s stage=%s filename=%s resolved_file=%s truncated=%s output_chars=%s", trace.get("user_id") or "", trace.get("base_id") or "", trace.get("file_id") or "", trace.get("stage") or "", filename, str(resolved), truncated, len(output))
            if trace_enabled():
                trace_detail_logger(trace).info("[document_read] %s document_read_done filename=%s resolved_file=%s truncated=%s output_preview=%s", trace_tag(trace), filename, str(resolved), truncated, _preview(output))
            return {
                "title": "document_read",
                "output": output,
                "metadata": {
                    "ok": True,
                    "filename": filename,
                    "resolved_file": str(resolved),
                    "truncated": truncated,
                    "root": str(_docs_root()),
                },
            }
        except Exception as exc:
            logger.exception("[document_read_tool] read_failed filename=%s err=%s", filename, exc)
            if trace_enabled():
                trace_detail_logger(trace).info("[document_read] %s document_read_failed filename=%s err=%s", trace_tag(trace), filename, exc)
            return {
                "title": "document_read",
                "output": f"Error: {exc}",
                "metadata": {"ok": False, "filename": filename, "error": str(exc), "root": str(_docs_root())},
            }

    return Tool(
        name="document_read",
        description="Read a law text document by filename under the configured laws root.",
        _executor=_handler,
        metadata={
            "parallel_safe": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Exact .txt law filename from the catalog, for example 银行卡收单业务管理办法.txt. Must end with .txt. Do not include scene names, folders, slashes, or the laws root."}
                },
                "required": ["filename"],
            }
        },
    )
