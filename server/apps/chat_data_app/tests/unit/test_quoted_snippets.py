"""用户消息引用链路单测：@ 引用工作区文件短句 + 划选引用片段（quoted_snippets）。

覆盖：入参解析归一（parse_file_selections / parse_quoted_snippets）、模型可见
短句/标记块后缀（_file_selection_mention_suffix / _quote_snippets_suffix）、
_build_user_message 的正文注入与 additional_kwargs 留痕——历史回放据此渲染 chip。
"""

from apps.chat_data_app.agent.run.completion_options import parse_file_selections, parse_quoted_snippets
from apps.chat_data_app.agent_stream import (
    _build_user_message,
    _file_selection_mention_suffix,
    _quote_snippets_suffix,
)


def test_parse_file_selections_branches():
    # 非法入参一律空列表
    assert parse_file_selections(None) == []
    assert parse_file_selections(123) == []
    assert parse_file_selections(["", "   "]) == []
    # 单字符串与列表都兼容；strip 后按序去重
    assert parse_file_selections(" uploads/a.pdf ") == ["uploads/a.pdf"]
    assert parse_file_selections(
        [
            "uploads/a.pdf",
            " outputs/b.md ",
            "uploads/a.pdf",
            "",
        ]
    ) == ["uploads/a.pdf", "outputs/b.md"]
    # 首尾斜杠归一（前端容错）
    assert parse_file_selections("/workspace/semantic/a__1.yaml/") == ["workspace/semantic/a__1.yaml"]


def test_file_selection_mention_suffix_lists_files():
    assert _file_selection_mention_suffix(
        [
            "uploads/a.pdf",
            "workspace/semantic/b__1.yaml",
        ]
    ) == ("\n\n[引用文件：/mnt/user-data/uploads/a.pdf、/mnt/user-data/workspace/semantic/b__1.yaml]")
    assert _file_selection_mention_suffix([]) == ""


def test_parse_quoted_snippets_branches():
    # 非法入参一律空列表
    assert parse_quoted_snippets(None) == []
    assert parse_quoted_snippets("片段") == []
    assert parse_quoted_snippets([{"text": "  "}, {}, {"other": 1}]) == []
    # dict 条目取 text，纯字符串条目兼容；strip 后按序去重
    assert parse_quoted_snippets([{"text": " 片段A "}, "片段B", {"text": "片段A"}, ""]) == [
        {"text": "片段A"},
        {"text": "片段B"},
    ]
    # 单条超长截断、总条数上限截断
    long_text = "字" * 3000
    assert len(parse_quoted_snippets([{"text": long_text}])[0]["text"]) == 2000
    overflow = [{"text": f"片段{i}"} for i in range(8)]
    assert len(parse_quoted_snippets(overflow)) == 5


def test_quote_snippets_suffix_wraps_blocks():
    suffix = _quote_snippets_suffix([{"text": " 户表与订单表通过用户ID关联 "}, {"text": ""}])
    assert suffix == ("\n\n[引用助手回复片段开始]\n户表与订单表通过用户ID关联\n[引用助手回复片段结束]")
    assert _quote_snippets_suffix(None) == ""
    assert _quote_snippets_suffix([]) == ""


def test_build_user_message_quoted_snippets_trace():
    message = _build_user_message(
        "这是什么",
        quoted_snippets=[{"text": "户表与订单表通过用户ID关联（一对多）"}],
    )
    # 正文末尾追加模型可见标记块；结构化片段留痕 additional_kwargs 供事件表持久化
    assert message.content == (
        "这是什么\n\n[引用助手回复片段开始]\n户表与订单表通过用户ID关联（一对多）\n[引用助手回复片段结束]"
    )
    assert message.additional_kwargs["quoted_snippets"] == [{"text": "户表与订单表通过用户ID关联（一对多）"}]

    # 无引用时不追加、不留痕
    plain = _build_user_message("这是什么")
    assert plain.content == "这是什么"
    assert "quoted_snippets" not in plain.additional_kwargs
