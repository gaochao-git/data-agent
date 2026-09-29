"""扫描版 PDF 检测/渲染/视觉转写测试（parse_document 的 PDF 无文本层兜底链路）。"""

from __future__ import annotations

import fitz
import pytest
from types import SimpleNamespace

from apps.chat_data_app.agent.workspace import scanned_pdf
from apps.chat_data_app.agent.workspace.scanned_pdf import (
    is_likely_scanned_pdf,
    load_pdf_page_texts,
    render_pdf_pages,
    select_page_numbers,
    transcribe_scanned_pdf,
)
from apps.chat_data_app.agent.workspace.uploaded_file_parser import (
    EmptyExtractionError,
    parse_uploaded_file_to_markdown,
)


def _make_text_pdf(path) -> None:
    """有文本层的 PDF（markitdown 主链路可正常提取；文本量需超过扫描判定阈值）。"""
    document = fitz.open()
    page = document.new_page()
    paragraph = "Sample order statistics report with revenue numbers 12345."
    for index in range(6):
        page.insert_text((72, 72 + index * 18), paragraph)
    document.save(str(path))
    document.close()


def _make_scanned_pdf(path, pages: int = 2) -> None:
    """纯图形页（无文字层）模拟扫描件。"""
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        page.draw_rect(page.rect, color=None, fill=(0.85, 0.85, 0.85))
    document.save(str(path))
    document.close()


class _FakeVisionLLM:
    """记录请求并返回固定转写文本的假视觉模型。"""

    def __init__(self, content: str, recorder: list | None = None):
        self._content = content
        self._recorder = recorder

    async def ainvoke(self, messages):
        if self._recorder is not None:
            self._recorder.append(messages)
        return SimpleNamespace(content=self._content)


# --------------------------------------------------------------------------- #
# 扫描版判定
# --------------------------------------------------------------------------- #

def test_text_pdf_is_not_scanned(tmp_path):
    path = tmp_path / "text.pdf"
    _make_text_pdf(path)
    assert is_likely_scanned_pdf(load_pdf_page_texts(path)) is False


def test_image_only_pdf_is_scanned(tmp_path):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=2)
    assert is_likely_scanned_pdf(load_pdf_page_texts(path)) is True


# --------------------------------------------------------------------------- #
# 页码选择与渲染
# --------------------------------------------------------------------------- #

def test_select_page_numbers_caps_and_floors():
    assert select_page_numbers(5, 10) == [1, 2, 3, 4, 5]
    assert select_page_numbers(30, 10) == list(range(1, 11))
    assert select_page_numbers(3, 0) == []


def test_render_pdf_pages_creates_numbered_pngs(tmp_path):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=2)
    rendered = render_pdf_pages(path, [1, 2], tmp_path / "out")
    assert [item.name for item in rendered] == ["page-001.png", "page-002.png"]
    assert all(item.is_file() and item.stat().st_size > 0 for item in rendered)


def test_render_downgrades_dpi_when_over_size_limit(tmp_path):
    """超大画幅超过视觉链路单张上限时逐次减半 DPI,最低降到 72 且始终产出有效文件。"""
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=1)
    rendered = render_pdf_pages(path, [1], tmp_path / "out", max_bytes=1)
    assert rendered[0].is_file() and rendered[0].stat().st_size > 1


# --------------------------------------------------------------------------- #
# 视觉转写
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_transcribe_batches_pages_and_headers(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=2)
    recorder: list = []

    async def _fake_setting(key):
        return 10

    monkeypatch.setattr(scanned_pdf, "get_runtime_setting", _fake_setting)
    result = await transcribe_scanned_pdf(
        path,
        llm_factory=lambda: _FakeVisionLLM("转写内容", recorder),
    )
    assert "## 第 1-2 页" in result
    assert "转写内容" in result
    # 2 页 < 批大小 4 → 单次调用;消息含 1 段指令 + 2 张图片
    assert len(recorder) == 1
    blocks = recorder[0][0].content
    assert blocks[0]["type"] == "text"
    assert [block["type"] for block in blocks[1:]] == ["image_url", "image_url"]


@pytest.mark.asyncio
async def test_transcribe_caps_pages_and_notes_truncation(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=3)

    async def _fake_setting(key):
        return 1

    monkeypatch.setattr(scanned_pdf, "get_runtime_setting", _fake_setting)
    result = await transcribe_scanned_pdf(path, llm_factory=lambda: _FakeVisionLLM("转写内容"))
    assert "## 第 1 页" in result
    assert "仅转写前 1 页" in result


@pytest.mark.asyncio
async def test_transcribe_wraps_model_errors(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=1)

    async def _fake_setting(key):
        return 10

    monkeypatch.setattr(scanned_pdf, "get_runtime_setting", _fake_setting)

    class _BrokenLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("model does not support images")

    with pytest.raises(ValueError, match="视觉转写失败"):
        await transcribe_scanned_pdf(path, llm_factory=lambda: _BrokenLLM())


# --------------------------------------------------------------------------- #
# 与解析主链路的衔接
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_parser_raises_empty_extraction_for_scanned_pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=1)
    with pytest.raises(EmptyExtractionError, match="未提取到可分析内容"):
        await parse_uploaded_file_to_markdown(path)


@pytest.mark.asyncio
async def test_parser_keeps_markitdown_path_for_text_pdf(tmp_path):
    path = tmp_path / "text.pdf"
    _make_text_pdf(path)
    content = await parse_uploaded_file_to_markdown(path)
    assert "Sample order statistics report with revenue numbers 12345." in content
