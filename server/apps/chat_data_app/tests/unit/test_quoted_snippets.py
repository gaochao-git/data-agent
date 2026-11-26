"""用户消息引用链路单测：@ 引用工作区文件短句 + 划选引用片段 + 文档选中内容。

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
    assert parse_file_selections("/workspace/.system/semantic/a__1.yaml/") == ["workspace/.system/semantic/a__1.yaml"]


def test_file_selection_mention_suffix_lists_files():
    assert _file_selection_mention_suffix(
        [
            "uploads/a.pdf",
            "workspace/.system/semantic/b__1.yaml",
        ]
    ) == ("\n\n[引用文件：/mnt/user-data/uploads/a.pdf、/mnt/user-data/workspace/.system/semantic/b__1.yaml]")
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


def test_parse_document_annotations_normalizes_and_filters():
    from apps.chat_data_app.agent.run.completion_options import parse_document_annotations

    assert parse_document_annotations(None) == []
    assert parse_document_annotations("x") == []
    assert parse_document_annotations([{"file_path": "outputs/a.docx"}]) == []
    annotations = parse_document_annotations(
        [
            {
                "file_path": "/mnt/user-data/outputs/示例文档.docx",
                "doc_path": " /body/p[4] ",
                "quote": " 选中原文 ",
                "comment": " 改成季度分析 ",
            },
            {
                "file_path": "outputs/示例文档.docx",
                "doc_path": "/body/p[4]",
                "quote": "选中原文",
                "comment": "改成季度分析",
            },
            {"file_path": "../secret.docx", "doc_path": "/body/p[1]", "comment": "越权"},
            {"file_path": "outputs/a.docx", "doc_path": "/body/p 1", "comment": "非法定位"},
            {"file_path": "outputs/a.docx", "doc_path": "/body/p[1]", "comment": ""},
        ]
    )
    assert annotations == [
        {
            "file_path": "outputs/示例文档.docx",
            "doc_path": "/body/p[4]",
            "quote": "选中原文",
            "comment": "改成季度分析",
        }
    ]


def test_document_annotations_suffix_and_message_trace():
    from apps.chat_data_app.agent_stream import _document_annotations_suffix

    annotation = {
        "file_path": "outputs/示例文档.docx",
        "doc_path": "/body/p[4]",
        "quote": "这是一份示例文档",
        "comment": "改成季度经营分析",
    }
    assert _document_annotations_suffix([annotation], user_content="这段话的依据是什么？") == (
        "\n\n[文档选中内容开始]\n"
        "文件：/mnt/user-data/outputs/示例文档.docx\n"
        "定位：/body/p[4]\n"
        "选中原文：这是一份示例文档\n"
        "问题或要求：改成季度经营分析\n"
        "处理建议：先根据用户的问题或要求判断意图；只需解释、核对或回答时，不要改动文件；只有明确要求修改时，才使用 officecli 按「定位」编辑并重新展示。\n"
        "[文档选中内容结束]"
    )
    assert _document_annotations_suffix([annotation], user_content="改成季度经营分析") == (
        "\n\n[文档选中内容开始]\n"
        "文件：/mnt/user-data/outputs/示例文档.docx\n"
        "定位：/body/p[4]\n"
        "选中原文：这是一份示例文档\n"
        "处理建议：先根据用户的问题或要求判断意图；只需解释、核对或回答时，不要改动文件；只有明确要求修改时，才使用 officecli 按「定位」编辑并重新展示。\n"
        "[文档选中内容结束]"
    )
    assert _document_annotations_suffix(None) == ""
    assert _document_annotations_suffix([{"file_path": "outputs/a.docx"}]) == ""

    message = _build_user_message("改成季度经营分析", annotations=[annotation])
    assert "[文档选中内容开始]" in message.content
    assert "问题或要求：改成季度经营分析" not in message.content
    assert message.additional_kwargs["annotations"] == [annotation]


def test_document_annotation_quote_is_flattened_for_line_based_block():
    from apps.chat_data_app.agent_stream import _document_annotations_suffix

    suffix = _document_annotations_suffix(
        [
            {
                "file_path": "outputs/示例文档.docx",
                "doc_path": "/body/table[3]/tr[11]/tc[5]",
                "quote": "第一行原文\n第二行：仍属于原文",
                "comment": "这个来源是哪里",
            }
        ]
    )

    assert "选中原文：第一行原文 第二行：仍属于原文\n" in suffix
    assert "选中原文：第一行原文\n第二行" not in suffix


def test_completion_run_options_reads_document_annotations():
    from apps.chat_data_app.agent.run.completion_options import parse_completion_run_options

    options = parse_completion_run_options(
        {
            "annotations": [
                {
                    "file_path": "outputs/示例文档.docx",
                    "doc_path": "/body/p[4]",
                    "quote": "选中原文",
                    "comment": "改成季度分析",
                }
            ],
        }
    )
    assert options.annotations == [
        {
            "file_path": "outputs/示例文档.docx",
            "doc_path": "/body/p[4]",
            "quote": "选中原文",
            "comment": "改成季度分析",
        }
    ]
