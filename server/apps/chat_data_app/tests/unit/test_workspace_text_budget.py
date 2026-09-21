"""workspace_text_budget 预算分配测试——覆盖分隔符与预算耗尽的中断分支。"""

from __future__ import annotations

from apps.chat_data_app.agent.workspace.workspace_text_budget import (
    MAX_FILE_READ_LINE_CHARS,
    allocate_file_read_line_budgets,
)


def test_allocate_caps_each_line_at_max_chars():
    assert allocate_file_read_line_budgets([10, MAX_FILE_READ_LINE_CHARS + 50], 10000) == [
        10,
        MAX_FILE_READ_LINE_CHARS,
    ]


def test_allocate_truncates_last_line_to_remaining_budget():
    """预算不足以容纳整行时，截断该行后停止。"""
    assert allocate_file_read_line_budgets([5], 3) == [3]


def test_allocate_stops_when_separator_does_not_fit():
    """已有内容后，剩余预算连换行分隔符都放不下时停止。"""
    assert allocate_file_read_line_budgets([1, 5], 1) == [1]


def test_allocate_stops_when_no_budget_left_for_line():
    assert allocate_file_read_line_budgets([1, 5], 2) == [1]


def test_allocate_handles_empty_and_zero_budget():
    assert allocate_file_read_line_budgets([], 100) == []
    assert allocate_file_read_line_budgets([5], 0) == []
    assert allocate_file_read_line_budgets([5], -10) == []
