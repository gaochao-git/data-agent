"""裸 tool-call XML 标记清理。"""

from utils.tool_call_markup import has_tool_call_markup, sanitize_message_content, sanitize_tool_call_markup


def test_sanitize_complete_tool_call_block():
    text = "开始\n<tool_call><function=file_read><parameter=path>/a</parameter></function></tool_call>\n结束"
    assert sanitize_tool_call_markup(text) == "开始\n\n结束"
    assert has_tool_call_markup(text)


def test_sanitize_dangling_closing_tags():
    text = "需要修正\n</parameter>\n</function>\n</tool_call>"
    assert sanitize_tool_call_markup(text).strip() == "需要修正"
    assert has_tool_call_markup(text)


def test_sanitize_multimodal_content():
    content = [{"type": "text", "text": "正文</tool_call>"}, {"type": "image_url"}]
    assert sanitize_message_content(content) == [{"type": "text", "text": "正文"}, {"type": "image_url"}]


def test_plain_text_unchanged():
    assert sanitize_tool_call_markup("普通正文") == "普通正文"
    assert not has_tool_call_markup("普通正文")
