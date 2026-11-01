"""app/alarm 日志统一脱敏与 DEBUG 不再强制打开控制台。"""

from __future__ import annotations

import logging

from utils.logger import LoggerManager


def test_app_formatter_masks_sensitive_text():
    formatter = LoggerManager._get_formatter()
    record = logging.LogRecord(
        name="unit",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="用户手机号13812345678，身份证110101199001011234",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)

    assert "13812345678" not in output
    assert "110101199001011234" not in output
    assert "138******78" in output


def test_alarm_formatter_masks_ext_fields():
    formatter = LoggerManager._get_alarm_formatter()
    record = logging.LogRecord(
        name="unit",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="alarm",
        args=(),
        exc_info=None,
    )
    record.alarm_id = "TEST"
    record.ext1 = "phone=13812345678"
    record.ext2 = "card=6222021234567890123"
    output = formatter.format(record)

    assert "13812345678" not in output
    assert "6222021234567890123" not in output
