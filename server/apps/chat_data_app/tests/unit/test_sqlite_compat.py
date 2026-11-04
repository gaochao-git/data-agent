"""SQLite 兼容 shim 的平台化告警。"""

from __future__ import annotations

import logging
import sys

from utils import sqlite_compat


def _reset(monkeypatch):
    monkeypatch.setattr(sqlite_compat, "_applied", False)
    monkeypatch.setattr(sqlite_compat, "_attempted", False)
    monkeypatch.setitem(sys.modules, "pysqlite3", None)


def test_missing_pysqlite3_warns_on_linux(monkeypatch, caplog):
    _reset(monkeypatch)
    monkeypatch.setattr(sqlite_compat.sys, "platform", "linux")

    with caplog.at_level(logging.DEBUG, logger="utils.sqlite_compat"):
        assert sqlite_compat.apply_sqlite_compat() is False

    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_missing_pysqlite3_on_mac_new_sqlite_is_debug(monkeypatch, caplog):
    _reset(monkeypatch)
    monkeypatch.setattr(sqlite_compat.sys, "platform", "darwin")
    monkeypatch.setattr(sqlite_compat.sqlite3, "sqlite_version_info", (3, 51, 0))

    with caplog.at_level(logging.DEBUG, logger="utils.sqlite_compat"):
        assert sqlite_compat.apply_sqlite_compat() is False

    assert not any(record.levelno == logging.WARNING for record in caplog.records)


def test_missing_pysqlite3_with_old_sqlite_warns_any_platform(monkeypatch, caplog):
    _reset(monkeypatch)
    monkeypatch.setattr(sqlite_compat.sys, "platform", "darwin")
    monkeypatch.setattr(sqlite_compat.sqlite3, "sqlite_version_info", (3, 8, 2))

    with caplog.at_level(logging.DEBUG, logger="utils.sqlite_compat"):
        assert sqlite_compat.apply_sqlite_compat() is False

    assert any(record.levelno == logging.WARNING for record in caplog.records)
