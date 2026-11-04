"""异步日志/任何 handler 下都要保留请求 trace 与 user。"""

from __future__ import annotations

import logging

from utils import logger as logger_module


def test_context_filter_snapshots_trace_and_user(monkeypatch):
    logger_module.set_log_context(request_id="trace-1", user="u1-用户1")
    try:
        record = logging.LogRecord(
            name="unit",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        assert logger_module.LogContextFilter().filter(record) is True
        assert record.request_id == "trace-1"
        assert record.user == "u1-用户1"
    finally:
        logger_module.clear_log_context()


def test_existing_extra_fields_are_not_overwritten():
    record = logging.LogRecord(
        name="unit",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.request_id = "explicit"
    logger_module.set_log_context(request_id="context", user="u")
    try:
        logger_module.LogContextFilter().filter(record)
        assert record.request_id == "explicit"
    finally:
        logger_module.clear_log_context()
