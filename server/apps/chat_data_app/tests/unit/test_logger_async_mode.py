"""LOG_ASYNC 开启后使用 QueueHandler，由 QueueListener 异步写文件。"""

from __future__ import annotations

import logging
import queue

from utils.logger import LoggerManager, _BoundedQueueHandler


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
    assert isinstance(handlers[0], logging.handlers.QueueHandler)
    # 清理缓存，避免影响其它用例
    LoggerManager._async_listeners.clear()


def test_bounded_queue_handler_drops_without_blocking(capsys):
    bounded = queue.Queue(maxsize=1)
    handler = _BoundedQueueHandler(bounded)
    record = logging.LogRecord("unit", logging.INFO, __file__, 1, "msg", (), None)

    handler.enqueue(record)
    handler.enqueue(record)  # 队列满，丢弃且不能抛异常

    assert bounded.qsize() == 1
    assert "LOG_ASYNC 队列已满" in capsys.readouterr().err


def test_async_listener_can_restart_in_same_process(monkeypatch):
    monkeypatch.setenv("LOG_ASYNC", "true")
    LoggerManager._async_listeners.clear()
    LoggerManager._async_listener_handlers.clear()
    logger = logging.getLogger("unit.async.restart")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)

    handler = logging.NullHandler()
    LoggerManager._attach_handlers(logger, "app-restart-test", [handler])
    listener, queue_handler = LoggerManager._async_listeners["app-restart-test"]
    assert listener is not None

    LoggerManager.stop_async_listeners()
    assert LoggerManager._async_listeners["app-restart-test"][0] is None

    LoggerManager.ensure_async_listeners()
    assert LoggerManager._async_listeners["app-restart-test"][0] is not None
    assert LoggerManager._async_listeners["app-restart-test"][1] is queue_handler

    LoggerManager.stop_async_listeners()
    LoggerManager._async_listeners.clear()
    LoggerManager._async_listener_handlers.clear()
