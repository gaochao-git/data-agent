"""memory_parser 纯函数层测试：条目解析、规整、淘汰、转义与预算裁剪。

会话记忆条目格式：``- [类别] 结论（适用范围）``，文档内顺序即新旧（越靠后越新）。
"""

from __future__ import annotations

from apps.chat_data_app.services.memory.memory_parser import (
    MAX_ITEM_CONTENT_CHARS,
    MemoryItem,
    enforce_memory_item_limit,
    escape_memory_for_prompt,
    normalize_memory_items,
    normalize_memory_text,
    parse_memory_document,
    parse_memory_item_line,
    render_memory_document,
    select_items_within_budget,
)

# ---------------------------------------------------------------------------
# 行渲染与解析
# ---------------------------------------------------------------------------


def test_memory_item_to_line_with_and_without_scope():
    assert MemoryItem(category="口径", content="销售额含税", scope="仅订单分析").to_line() == (
        "- [口径] 销售额含税（仅订单分析）"
    )
    assert MemoryItem(category="口径", content="销售额含税").to_line() == "- [口径] 销售额含税"


def test_parse_memory_item_line_valid_with_scope():
    item = parse_memory_item_line("- [口径] 销售额含税（仅订单分析）")
    assert item == MemoryItem(category="口径", content="销售额含税", scope="仅订单分析")


def test_parse_memory_item_line_valid_without_scope():
    item = parse_memory_item_line("- [纠错] 日期字段用 dt 而非 created_at")
    assert item == MemoryItem(category="纠错", content="日期字段用 dt 而非 created_at", scope="")


def test_parse_memory_item_line_allows_zero_space_after_bracket():
    item = parse_memory_item_line("- [口径]结论（范围）")
    assert item == MemoryItem(category="口径", content="结论", scope="范围")


def test_parse_memory_item_line_rejects_malformed():
    assert parse_memory_item_line("普通文本行") is None
    assert parse_memory_item_line("- [口径]") is None
    assert parse_memory_item_line("- [] 缺类别") is None


def test_split_scope_boundary_cases_via_parse():
    # 范围括号出现在行首视为整体结论；范围括号未闭合同样视为整体结论
    assert parse_memory_item_line("- [口径] （括号开头视为结论）") == MemoryItem(
        category="口径", content="（括号开头视为结论）", scope=""
    )
    assert parse_memory_item_line("- [口径] 内容（范围未闭合") == MemoryItem(
        category="口径", content="内容（范围未闭合", scope=""
    )


def test_parse_memory_document_ignores_headers_blanks_and_dedups():
    text = (
        "# 会话记忆（conversation: c1）\n"
        "\n"
        "- [口径] A\n"
        "- [口径] A\n"
        "普通文本\n"
        "- [偏好] B（报表）\n"
    )
    items = parse_memory_document(text)
    assert items == [MemoryItem(category="口径", content="A"), MemoryItem(category="偏好", content="B", scope="报表")]


def test_parse_memory_document_empty_and_none():
    assert parse_memory_document("") == []
    assert parse_memory_document(None) == []


# ---------------------------------------------------------------------------
# 规整与淘汰
# ---------------------------------------------------------------------------


def test_normalize_memory_text_folds_whitespace_and_truncates():
    assert normalize_memory_text(" a\n b\t c ", 100) == "a b c"
    assert normalize_memory_text("x" * 300, 160) == "x" * MAX_ITEM_CONTENT_CHARS
    assert normalize_memory_text(None, 10) == ""


def test_normalize_memory_items_skips_invalid_dedups_and_limits_length():
    long_content = "长" * 300
    items = [
        MemoryItem(category="", content="缺类别"),
        MemoryItem(category="口径", content=""),
        MemoryItem(category="口径", content=long_content),
        MemoryItem(category="偏好", content="输出用  表格", scope="报表"),
        MemoryItem(category="偏好", content="输出用 表格", scope="报表"),
    ]
    normalized = normalize_memory_items(items, max_items=10)
    assert all(item.category and item.content for item in normalized)
    assert normalized[0].content == "长" * MAX_ITEM_CONTENT_CHARS
    # 空白折叠后同义的条目只保留一条
    assert [item.to_line() for item in normalized[1:]] == ["- [偏好] 输出用 表格（报表）"]


def test_normalize_memory_items_caps_at_max_items():
    items = [MemoryItem(category=f"c{i}", content=f"内容{i}") for i in range(5)]
    assert len(normalize_memory_items(items, max_items=2)) == 2


def test_enforce_memory_item_limit_keeps_latest_with_floor():
    items = [MemoryItem(category=f"c{i}", content=f"x{i}") for i in range(5)]
    kept = enforce_memory_item_limit(items, max_items=2)
    assert [item.content for item in kept] == ["x3", "x4"]
    single = [MemoryItem(category="c", content="only")]
    assert enforce_memory_item_limit(single, max_items=0) == single


# ---------------------------------------------------------------------------
# 渲染、转义与预算裁剪
# ---------------------------------------------------------------------------


def test_render_memory_document_entries_only_with_trailing_newline():
    doc = render_memory_document([MemoryItem(category="口径", content="A")])
    assert doc == "- [口径] A\n"
    assert "#" not in doc


def test_escape_memory_for_prompt_neutralizes_structure_chars():
    assert escape_memory_for_prompt("- [口径] <tag> `code`") == "- [口径] ＜tag＞ ˋcodeˋ"
    assert escape_memory_for_prompt(None) == ""


def test_select_items_within_budget_keeps_newest_in_document_order():
    items = [
        MemoryItem(category="c1", content="a" * 50),
        MemoryItem(category="c2", content="b" * 50),
        MemoryItem(category="c3", content="c" * 50),
    ]
    # 每条成本 len(to_line()) + 1 = 58，预算 120 只够最新两条
    selected = select_items_within_budget(items, max_tokens=120)
    assert [item.category for item in selected] == ["c2", "c3"]


def test_select_items_within_budget_always_keeps_latest_item():
    items = [
        MemoryItem(category="c1", content="x" * 100),
        MemoryItem(category="c2", content="y" * 100),
    ]
    selected = select_items_within_budget(items, max_tokens=10)
    assert [item.category for item in selected] == ["c2"]


def test_select_items_within_budget_zero_budget_still_returns_latest():
    items = [MemoryItem(category="c1", content="x")]
    assert [item.category for item in select_items_within_budget(items, max_tokens=0)] == ["c1"]
    assert select_items_within_budget([], max_tokens=100) == []
