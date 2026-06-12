from __future__ import annotations

import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler
from typing import Any

from utils.logger import get_current_log_dir, resolve_log_path

_TRACE_LOGGERS: dict[str, logging.Logger] = {}


def trace_enabled() -> bool:
    return os.getenv("OPENCODE_RUNTIME_TRACE", "").strip().lower() in {"1", "true", "yes", "y", "on"}


def trace_full_messages_enabled() -> bool:
    return os.getenv("OPENCODE_RUNTIME_TRACE_FULL_MESSAGES", "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_filename(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z._-]+", "_", text)
    return text.strip("._-") or "unknown"


def trace_index_log_path() -> str:
    raw = os.getenv("OPENCODE_SESSION_PROMPT_TRACE_LOG", "").strip()
    if raw:
        return resolve_log_path(raw)
    return resolve_log_path(os.path.join(get_current_log_dir(), "session_prompt_trace.log"))


def trace_detail_log_path(trace_context: dict[str, Any] | None = None) -> str:
    context = trace_context if isinstance(trace_context, dict) else {}
    analysis_id = str(context.get("analysis_id") or "").strip()
    trace_dir = os.getenv("OPENCODE_SESSION_PROMPT_TRACE_DIR", "").strip()
    if analysis_id and trace_dir:
        return os.path.join(resolve_log_path(trace_dir), f"{_safe_filename(analysis_id)}.log")
    return trace_index_log_path()


def _logger_for_path(path: str, logger_name: str) -> logging.Logger:
    resolved = resolve_log_path(path)
    cached = _TRACE_LOGGERS.get(resolved)
    if cached is not None:
        return cached
    trace_logger = logging.getLogger(f"{logger_name}.{_safe_filename(resolved)}")
    trace_logger.setLevel(logging.INFO)
    trace_logger.propagate = False
    for handler in list(trace_logger.handlers):
        trace_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    handler = TimedRotatingFileHandler(resolved, when="midnight", interval=1, backupCount=30, encoding="utf-8", utc=False)
    handler.suffix = "%Y-%m-%d"
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    trace_logger.addHandler(handler)
    _TRACE_LOGGERS[resolved] = trace_logger
    return trace_logger


def trace_index_logger() -> logging.Logger:
    return _logger_for_path(trace_index_log_path(), "opencode_runtime_trace_index")


def trace_detail_logger(trace_context: dict[str, Any] | None = None) -> logging.Logger:
    return _logger_for_path(trace_detail_log_path(trace_context), "opencode_runtime_trace_detail")


def trace_tag(trace_context: dict[str, Any] | None = None) -> str:
    context = trace_context if isinstance(trace_context, dict) else {}
    keys = ("request_id", "analysis_id", "base_id", "user_id", "file_id", "stage", "skill_name", "session_id", "laws_root")
    parts = []
    for key in keys:
        value = str(context.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
        elif key in {"request_id", "base_id", "user_id", "file_id"}:
            parts.append(f"{key}=-")
    return " ".join(parts)


__all__ = ["trace_detail_log_path", "trace_detail_logger", "trace_enabled", "trace_full_messages_enabled", "trace_index_logger", "trace_tag"]
