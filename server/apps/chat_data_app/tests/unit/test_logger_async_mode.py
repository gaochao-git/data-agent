"""LOG_ASYNC 开启后使用 QueueHandler，由 QueueListener 异步写文件。"""

from __future__ import annotations

import logging

from utils.logger import LoggerManager


def test_async_mode_attaches_queue_handler(monkeypatch):
    monkeypatch.setenv("LOG_ASYNC", "true")
    LoggerManager._async_listeners.clear()
    logger = logging.getLogger("unit.async.logger")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    handler = logging.NullHandler()
    LoggerManager._attach_handlers(logger, "app-test", [handler])

    handlers = logger.handlers
    assert len(handlers) == 1
    assert handlers[0].__class__.__name__ == "QueueHandler"
    # 清理缓存，避免影响其它用例
    LoggerManager._async_listeners.clear()
