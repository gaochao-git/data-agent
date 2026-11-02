"""第三方 logger 接管：进统一 handler、级别可控、不重复、幂等。"""

from __future__ import annotations

import logging

from utils import logger as logger_module


def test_third_party_loggers_use_unified_handler(monkeypatch):
    logger_module._third_party_configured = False
    seen = []

    class MemoryHandler(logging.Handler):
        def emit(self, record):
            seen.append(record.name)

    memory = MemoryHandler()
    monkeypatch.setattr(logger_module.LoggerManager, "_get_file_handler", classmethod(lambda cls, *args: memory))
    monkeypatch.setattr(logger_module, "get_current_console_output", lambda: False)

    logger_module.configure_third_party_loggers()

    for name in ("uvicorn", "uvicorn.error", "httpx", "aiomysql"):
        lib = logging.getLogger(name)
        assert memory in lib.handlers
        assert lib.propagate is False
        assert any(isinstance(item, logger_module.LogContextFilter) for item in lib.filters)

    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("uvicorn.access").handlers == []

    # 幂等：重复调用不重复添加 handler
    logger_module.configure_third_party_loggers()
    assert logging.getLogger("httpx").handlers.count(memory) == 1
    logger_module._third_party_configured = False
