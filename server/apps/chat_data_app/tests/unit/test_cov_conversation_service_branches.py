"""conversation_service 缺失分支：标题内容块解析、分组键与分页游标。"""

import pytest

from apps.chat_data_app.services import conversation_service as service
from apps.chat_data_app.services.conversation_service import (
    DATASOURCE_GROUP_PREFIX,
    PINNED_GROUP_KEY,
    UNCLASSIFIED_GROUP_KEY,
    _decode_group_cursor,
    _parse_group_key,
    _title_response_text,
)


def test_title_text_returns_empty_for_non_text_content():
    assert _title_response_text(type("_R", (), {"content": 42})()) == ""


def test_title_text_collects_plain_blocks_and_skips_thinking():
    response = type(
        "_R",
        (),
        {
            "content": [
                "前置说明",
                42,
                {"type": "thinking", "text": "内部推理"},
                {"type": "text", "content": "真实标题"},
            ]
        },
    )()
    assert _title_response_text(response) == "前置说明\n真实标题"


def test_title_text_skips_dict_blocks_without_text():
    response = type("_R", (), {"content": [{"type": "text", "text": 123}, {"type": "text", "text": "ok"}]})()
    assert _title_response_text(response) == "ok"


def test_parse_group_key_supports_pinned_unclassified_and_datasource():
    assert _parse_group_key(PINNED_GROUP_KEY) == ("pinned", None)
    assert _parse_group_key(UNCLASSIFIED_GROUP_KEY) == ("unclassified", None)
    assert _parse_group_key(f"{DATASOURCE_GROUP_PREFIX} ds-1 ") == ("datasource", "ds-1")
    with pytest.raises(ValueError, match="无效的会话分组"):
        _parse_group_key("unknown")


def test_decode_group_cursor_handles_blank_and_invalid():
    assert _decode_group_cursor(None) == (None, None)
    assert _decode_group_cursor("  ") == (None, None)
    with pytest.raises(ValueError, match="无效的会话分页游标"):
        _decode_group_cursor("not-base64")
    assert service is not None
