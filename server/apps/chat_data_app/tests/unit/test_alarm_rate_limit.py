"""告警限频与失败隔离。"""

from __future__ import annotations

import utils.alarm as alarm_module


class FakeAlarmLogger:
    def __init__(self):
        self.records = []

    def error(self, message, extra=None):
        self.records.append((message, extra or {}))


def test_alarm_rate_limits_same_key(monkeypatch):
    fake = FakeAlarmLogger()
    monkeypatch.setattr(alarm_module, "get_alarm_logger", lambda: fake)
    monkeypatch.setenv("CHAT_DATA_ALARM_RATE_LIMIT_MAX_PER_WINDOW", "1")
    monkeypatch.setenv("CHAT_DATA_ALARM_RATE_LIMIT_WINDOW_SECONDS", "60")
    alarm_module._reset_alarm_rate_limits()

    alarm_module.emit_alarm("boom", alarm_id="TEST", error_code="E1")
    alarm_module.emit_alarm("boom", alarm_id="TEST", error_code="E1")
    alarm_module.emit_alarm("boom", alarm_id="TEST", error_code="E1")

    assert len(fake.records) == 1
    assert fake.records[0][0] == "boom"
    alarm_module._reset_alarm_rate_limits()


def test_alarm_write_failure_does_not_raise(monkeypatch):
    class BrokenLogger:
        def error(self, *args, **kwargs):
            raise RuntimeError("alarm io down")

    monkeypatch.setattr(alarm_module, "get_alarm_logger", lambda: BrokenLogger())
    alarm_module._reset_alarm_rate_limits()
    # 不抛异常即通过
    alarm_module.emit_alarm("boom", alarm_id="TEST_BROKEN", error_code="E2")
    alarm_module._reset_alarm_rate_limits()
