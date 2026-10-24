"""filesystem 工具缺失分支：分页校验、全文读取上限与 file_read 边界提示。"""

import pytest

from apps.chat_data_app.agent.tools import filesystem as fs
from apps.chat_data_app.agent.tools.filesystem import (
    _fetch_full_read_page,
    _file_read_impl,
    _file_read_offset_error,
    read_text_file_full,
)


class _Runtime:
    def __init__(self, page):
        self._page = page

    async def read_text_file(self, **_kwargs):
        return self._page


@pytest.mark.asyncio
async def test_fetch_page_rejects_dir_binary_and_truncation():
    with pytest.raises(ValueError, match="目录"):
        await _fetch_full_read_page(_Runtime({"exists": True, "is_dir": True}), "a.txt", 0, 10)
    with pytest.raises(ValueError, match="二进制"):
        await _fetch_full_read_page(_Runtime({"exists": True, "is_binary": True}), "a.txt", 0, 10)
    with pytest.raises(ValueError, match="字符上限"):
        await _fetch_full_read_page(_Runtime({"exists": True, "truncated_lines": True}), "a.txt", 0, 10)


@pytest.mark.asyncio
async def test_fetch_page_returns_empty_for_out_of_range_offset():
    page, next_offset = await _fetch_full_read_page(
        _Runtime({"exists": True, "offset_out_of_range": True}), "a.txt", 10, 10
    )
    assert (page, next_offset) == ("", None)


@pytest.mark.asyncio
async def test_full_read_rejects_blank_path():
    with pytest.raises(ValueError, match="file_path 不能为空"):
        await read_text_file_full(_Runtime({}), "   ")


@pytest.mark.asyncio
async def test_full_read_rejects_collected_chars_over_limit(monkeypatch):
    async def _page(_runtime, _path, _offset, _max_chars):
        return "x" * 20, None

    monkeypatch.setattr(fs, "_fetch_full_read_page", _page)
    with pytest.raises(ValueError, match="字符上限"):
        await read_text_file_full(_Runtime({}), "a.txt", max_chars=5)


@pytest.mark.asyncio
async def test_full_read_rejects_blank_content(monkeypatch):
    async def _page(_runtime, _path, _offset, _max_chars):
        return "", None

    monkeypatch.setattr(fs, "_fetch_full_read_page", _page)
    with pytest.raises(ValueError, match="文件为空"):
        await read_text_file_full(_Runtime({}), "a.txt")


@pytest.mark.asyncio
async def test_file_read_empty_summary_with_unknown_total_lines():
    summary = await _file_read_impl(_Runtime({"exists": True, "empty": True}), file_path="a.txt", offset=0, limit=10)
    assert "status=empty" in summary
    assert "total_lines=unknown" in summary


@pytest.mark.asyncio
async def test_file_read_empty_summary_with_known_total_lines():
    summary = await _file_read_impl(
        _Runtime({"exists": True, "empty": True, "total_lines": 3}), file_path="a.txt", offset=0, limit=10
    )
    assert "total_lines=3" in summary


def test_file_read_offset_error_without_total_lines():
    assert "统计失败" in _file_read_offset_error({}, 5)
    assert "共 3 行" in _file_read_offset_error({"total_lines": 3}, 5)


@pytest.mark.asyncio
async def test_file_read_offset_out_of_range_uses_fallback_message():
    summary = await _file_read_impl(
        _Runtime({"exists": True, "offset_out_of_range": True}), file_path="a.txt", offset=9, limit=10
    )
    assert "超出文件可读范围" in summary
