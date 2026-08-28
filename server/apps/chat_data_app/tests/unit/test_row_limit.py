"""semantic_sql.row_limit 纯函数测试——LIMIT 校验的全部边界。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.services.semantic.core.semantic_sql.row_limit import normalize_row_limit


def test_normalize_row_limit_defaults_to_max_rows():
    assert normalize_row_limit(None) == 100
    assert normalize_row_limit(None, max_rows=10) == 10


def test_normalize_row_limit_accepts_in_range_values():
    assert normalize_row_limit(1) == 1
    assert normalize_row_limit(50) == 50
    assert normalize_row_limit(50, max_rows=50) == 50


@pytest.mark.parametrize("bad_limit", [0, -1, 1.5, "10", [], {}])
def test_normalize_row_limit_rejects_non_positive_or_non_integer(bad_limit):
    with pytest.raises(ValueError, match="必须为正整数"):
        normalize_row_limit(bad_limit)


def test_normalize_row_limit_rejects_values_above_max_rows():
    with pytest.raises(ValueError, match="最多返回 100 条"):
        normalize_row_limit(101)
    with pytest.raises(ValueError, match="最多返回 10 条"):
        normalize_row_limit(11, max_rows=10)
