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
    """写告警失败只降级告警本身：不冒泡，也不破坏后续告警的限流与上报。"""

    class BrokenLogger:
        def __init__(self):
            self.calls = 0

        def error(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("alarm io down")

    broken = BrokenLogger()
    monkeypatch.setattr(alarm_module, "get_alarm_logger", lambda: broken)
    monkeypatch.setenv("CHAT_DATA_ALARM_RATE_LIMIT_MAX_PER_WINDOW", "1")
    monkeypatch.setenv("CHAT_DATA_ALARM_RATE_LIMIT_WINDOW_SECONDS", "60")
    alarm_module._reset_alarm_rate_limits()

    alarm_module.emit_alarm("boom", alarm_id="TEST_BROKEN", error_code="E2")

    # 写入确实被尝试过、异常被吞掉；_allow_alarm 在写入前已扣配额，失败不回滚
    assert broken.calls == 1

    # 换回可用 logger：同 key 仍按限流抑制，其他 key 仍能正常上报（业务未受影响）
    fake = FakeAlarmLogger()
    monkeypatch.setattr(alarm_module, "get_alarm_logger", lambda: fake)
    alarm_module.emit_alarm("boom again", alarm_id="TEST_BROKEN", error_code="E2")
    assert fake.records == []

    alarm_module.emit_alarm("another", alarm_id="TEST_OK", error_code="E3")
    assert [record[0] for record in fake.records] == ["another"]
    assert fake.records[0][1]["alarm_id"] == "TEST_OK"
    alarm_module._reset_alarm_rate_limits()
