"""会话目录日期分层必须通过真实日历校验。"""

from __future__ import annotations

import pytest

from utils.storage_paths import normalize_created_date


def test_normalize_created_date_rejects_invalid_calendar_date():
    with pytest.raises(ValueError):
        normalize_created_date("2026-02-31")
    with pytest.raises(ValueError):
        normalize_created_date("2026-13-01")


def test_normalize_created_date_accepts_valid_date():
    assert normalize_created_date("2026-02-28") == "2026-02-28"
