"""日志脱敏中间件工具单测：手机号/身份证/银行卡掩码、payload 递归、header 脱敏。"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from middleware.log_redaction import (
    _mask_middle,
    dump_masked_payload,
    mask_headers,
    mask_sensitive_payload,
    mask_sensitive_text,
)


def test_mask_middle_short_text_returns_as_is():
    assert _mask_middle("123", 3, 2) == "123"
    assert _mask_middle("", 3, 2) == ""


def test_mask_sensitive_text_phone():
    # 11 位手机号：保留前 3 后 2
    assert mask_sensitive_text("联系 13812345678 确认") == "联系 138******78 确认"


def test_mask_sensitive_text_id_card():
    # 18 位身份证：保留前 6 后 4；前置校验避免把长数字误判为银行卡
    text = "证件号 110101199001011234 结束"
    assert mask_sensitive_text(text) == "证件号 110101********1234 结束"


def test_mask_sensitive_text_bank_card():
    # 16 位银行卡：保留前 4 后 4
    assert mask_sensitive_text("卡号 6222021234567890 转账") == "卡号 6222********7890 转账"


def test_mask_sensitive_text_keeps_plain_text_untouched():
    assert mask_sensitive_text("普通日志内容 no digits here") == "普通日志内容 no digits here"
    assert mask_sensitive_text("") == ""


def test_mask_sensitive_payload_recurses_containers():
    payload = {
        "phone": "13812345678",
        "items": [("卡号", "6222021234567890")],
        "count": 3,
        "flag": True,
    }
    masked = mask_sensitive_payload(payload)
    assert masked["phone"] == "138******78"
    assert masked["items"] == [["卡号", "6222********7890"]]
    assert masked["count"] == 3
    assert masked["flag"] is True


def test_mask_sensitive_payload_passes_through_non_containers():
    assert mask_sensitive_payload(None) is None
    assert mask_sensitive_payload(datetime(2026, 9, 25)) == datetime(2026, 9, 25)


def test_mask_headers_redacts_sensitive_keys_case_insensitively():
    headers = {
        "Authorization": "Bearer secret-token",
        "COOKIE": "session=abc",
        "X-Service-Token": "token-value",
        "X-User-Id": "user_13812345678",
    }
    masked = mask_headers(headers)
    assert masked["Authorization"] == "[redacted]"
    assert masked["COOKIE"] == "[redacted]"
    assert masked["X-Service-Token"] == "[redacted]"
    assert masked["X-User-Id"] == "user_138******78"


def test_mask_headers_empty_returns_empty_dict():
    assert mask_headers(None) == {}
    assert mask_headers({}) == {}


def test_dump_masked_payload_serializes_json():
    text = dump_masked_payload({"phone": "13812345678", "note": "备注"})
    assert json.loads(text) == {"phone": "138******78", "note": "备注"}


def test_dump_masked_payload_string_passthrough():
    assert dump_masked_payload("13812345678") == "138******78"


def test_dump_masked_payload_falls_back_when_not_serializable():
    class Unprintable:
        def __str__(self):
            raise ValueError("no str")

    text = dump_masked_payload({"bad": Unprintable()})
    assert "bad" in text
