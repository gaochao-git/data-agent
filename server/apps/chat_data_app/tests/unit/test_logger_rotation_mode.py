"""日志轮转模式：external 用 WatchedFileHandler，内置模式保持 TimedRotating。"""

from __future__ import annotations

from logging.handlers import TimedRotatingFileHandler, WatchedFileHandler

from utils import logger as logger_module


def test_external_mode_uses_watched_handler_with_same_filename(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_ROTATION_MODE", "external")
    handler = logger_module.LoggerManager._build_file_handler(str(tmp_path), "data-agent_app.log", 30)

    assert isinstance(handler, WatchedFileHandler)
    assert handler.baseFilename == str(tmp_path / "data-agent_app.log")


def test_builtin_mode_keeps_timed_rotating_handler(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_ROTATION_MODE", "builtin")
    handler = logger_module.LoggerManager._build_file_handler(str(tmp_path), "data-agent_app.log", 30)

    assert isinstance(handler, TimedRotatingFileHandler)
    assert handler.baseFilename == str(tmp_path / "data-agent_app.log")


def test_unknown_rotation_mode_falls_back_to_builtin(monkeypatch):
    monkeypatch.setenv("LOG_ROTATION_MODE", "whatever")
    assert logger_module.get_log_rotation_mode() == "builtin"
