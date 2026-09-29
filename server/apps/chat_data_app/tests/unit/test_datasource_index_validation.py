"""数据源索引校验函数的边界分支单测（纯函数，零 mock）。

覆盖 `_validate_table_index_name` / `_validate_table_index_columns` 的
各条拒绝出口——这些校验直接决定生成的 DDL，错放一条会产出非法索引，
用最小用例把每个出口钉死。
"""

import pytest

from apps.chat_data_app.services.datasource import manager


def test_primary_index_name_is_normalized():
    """主键索引名固定为 PRIMARY，用户填什么都被忽略。"""
    assert manager._validate_table_index_name("primary", "随便写", set()) == "PRIMARY"
    assert manager._validate_table_index_name("primary", None, set()) == "PRIMARY"


def test_index_name_requires_value_for_non_primary():
    """唯一/普通索引必须填名称。"""
    with pytest.raises(ValueError, match="必须填写索引名称"):
        manager._validate_table_index_name("unique", "   ", set())
    with pytest.raises(ValueError, match="必须填写索引名称"):
        manager._validate_table_index_name("secondary", None, set())


def test_index_name_rejects_illegal_chars_and_over_length():
    """名称需匹配 1-32 位字母/数字/下划线/$ 且不能以数字开头。"""
    with pytest.raises(ValueError, match="1-32 位"):
        manager._validate_table_index_name("unique", "1uniq_x", set())
    with pytest.raises(ValueError, match="1-32 位"):
        manager._validate_table_index_name("secondary", "idx_" + "x" * 40, set())


def test_index_name_enforces_prefix_by_type():
    """唯一索引必须以 uniq_ 开头，普通索引必须以 idx_ 开头。"""
    with pytest.raises(ValueError, match="uniq_"):
        manager._validate_table_index_name("unique", "idx_orders", set())
    with pytest.raises(ValueError, match="idx_"):
        manager._validate_table_index_name("secondary", "uniq_orders", set())


def test_index_name_rejects_collision_with_physical_index():
    """与库内已有物理索引重名必须拦下（调用方负责把物理名归一化为小写）。"""
    with pytest.raises(ValueError, match="物理索引重复"):
        manager._validate_table_index_name("secondary", "idx_orders", {"idx_orders"})
    assert manager._validate_table_index_name("secondary", "idx_amount", {"idx_orders"}) == "idx_amount"


def test_index_columns_require_at_least_one_non_blank_value():
    """索引字段不能为空，也不能出现空串元素。"""
    with pytest.raises(ValueError, match="至少需要一个字段"):
        manager._validate_table_index_columns([], set())
    with pytest.raises(ValueError, match="至少需要一个字段"):
        manager._validate_table_index_columns(["order_id", "  "], {"order_id"})


def test_index_columns_reject_too_many_or_duplicated():
    """组合索引字段数上限与重复字段都要拒绝。"""
    too_many = [f"col_{index}" for index in range(manager.MAX_INDEX_COLUMN_COUNT + 1)]
    with pytest.raises(ValueError, match="不能超过"):
        manager._validate_table_index_columns(too_many, set(too_many))
    with pytest.raises(ValueError, match="不能重复配置字段"):
        manager._validate_table_index_columns(["order_id", "order_id"], {"order_id"})


def test_index_columns_returns_normalized_list():
    """合法字段去空白后原样返回，供 DDL 拼装。"""
    assert manager._validate_table_index_columns([" order_id ", "created_at"], {"order_id", "created_at"}) == [
        "order_id",
        "created_at",
    ]
