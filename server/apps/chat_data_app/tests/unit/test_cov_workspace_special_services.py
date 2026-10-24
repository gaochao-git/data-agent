"""覆盖率补测：``workspace/workspace_special_services.py`` 与 ``workspace/scanned_pdf.py``。

覆盖空入参早退、Office 预览 HTML 无 ``</head>`` 时原样返回、后端已截断时不二次处理，
以及扫描版 PDF 的空文本列表判定、页数上限为 0、某批转写为空串、全部转写为空。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import fitz
import pytest

from apps.chat_data_app.agent.workspace import scanned_pdf
from apps.chat_data_app.agent.workspace.scanned_pdf import (
    is_likely_scanned_pdf,
    transcribe_scanned_pdf,
)
from apps.chat_data_app.agent.workspace.workspace_special_services import (
    SemanticModelWorkspaceService,
    WorkspaceOfficePreviewService,
    stabilize_office_preview_html,
)

WORD_HTML = '<html><head></head><body><div class="page" data-page="1"></div></body></html>'


class _SemanticBackend:
    def __init__(self) -> None:
        self.written: list[list[tuple[str, str]]] = []
        self.deleted: list[str] = []

    async def write_internal_text_files(self, runtime, *, entries):
        self.written.append(entries)
        return [{"path": path, "bytes": len(content.encode("utf-8"))} for path, content in entries]

    async def delete_internal_text_files(self, runtime, *, path):
        self.deleted.append(path)
        return {"path": path, "type": "file"}


def test_semantic_write_files_returns_empty_when_all_paths_blank():
    service = SemanticModelWorkspaceService()
    runtime = SimpleNamespace(backend=_SemanticBackend())

    assert asyncio.run(service.write_files(runtime, [("", "content"), ("   ", "content")])) == []


def test_semantic_write_and_delete_files_delegate_to_backend():
    service = SemanticModelWorkspaceService()
    backend = _SemanticBackend()
    runtime = SimpleNamespace(backend=backend)

    written = asyncio.run(service.write_files(runtime, [("  model.yaml  ", "a: 1")]))
    deleted = asyncio.run(service.delete_files(runtime, path=" model.yaml "))

    assert written == [{"path": "model.yaml", "bytes": 4}]
    assert backend.written == [[("model.yaml", "a: 1")]]
    assert deleted == {"path": "model.yaml", "type": "file"}
    assert backend.deleted == ["model.yaml"]


def test_semantic_delete_files_rejects_blank_path():
    service = SemanticModelWorkspaceService()
    runtime = SimpleNamespace(backend=_SemanticBackend())

    with pytest.raises(ValueError, match="删除路径不能为空"):
        asyncio.run(service.delete_files(runtime, path="   "))

    assert runtime.backend.deleted == []


def test_stabilize_office_preview_html_keeps_document_without_head_close():
    """含 Word 分页标记但缺 ``</head>``：无处注入 bootstrap，原样返回。"""
    html = '<html><div class="page" data-page="1"></div></html>'

    assert stabilize_office_preview_html(html) == html
    assert stabilize_office_preview_html(WORD_HTML) != WORD_HTML


def test_office_preview_returns_backend_truncated_payload_untouched():
    class _PreviewBackend:
        async def render_office_html(self, runtime, *, file_path, max_chars):
            return {"renderer": "officecli-html", "html": WORD_HTML, "truncated": True}

    service = WorkspaceOfficePreviewService()
    runtime = SimpleNamespace(backend=_PreviewBackend())

    result = asyncio.run(service.render_html(runtime, file_path="/out/report.docx", max_chars=10))

    assert result["truncated"] is True
    assert result["html"] == WORD_HTML


def test_is_likely_scanned_pdf_treats_empty_document_as_scanned():
    assert is_likely_scanned_pdf([]) is True


def _make_scanned_pdf(path, pages: int = 1) -> None:
    document = fitz.open()
    for _ in range(pages):
        page = document.new_page()
        page.draw_rect(page.rect, color=None, fill=(0.85, 0.85, 0.85))
    document.save(str(path))
    document.close()


class _SequencedVisionLLM:
    """按调用顺序返回预设文本，记录每次请求。"""

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0

    async def ainvoke(self, messages):
        content = self._contents[min(self.calls, len(self._contents) - 1)]
        self.calls += 1
        return SimpleNamespace(content=content)


@pytest.mark.asyncio
async def test_transcribe_rejects_when_page_cap_is_zero(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=1)

    async def _zero_max_pages(key):
        return 0

    monkeypatch.setattr(scanned_pdf, "get_runtime_setting", _zero_max_pages)
    llm = _SequencedVisionLLM(["内容"])

    with pytest.raises(ValueError, match="无可渲染页面"):
        await transcribe_scanned_pdf(path, llm_factory=lambda: llm)

    assert llm.calls == 0


@pytest.mark.asyncio
async def test_transcribe_skips_empty_batch_and_continues(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=5)

    async def _max_pages(key):
        return 10

    monkeypatch.setattr(scanned_pdf, "get_runtime_setting", _max_pages)
    llm = _SequencedVisionLLM(["", "第二批内容"])

    result = await transcribe_scanned_pdf(path, llm_factory=lambda: llm)

    assert llm.calls == 2
    assert "## 第 1-4 页" not in result
    assert "## 第 5 页" in result
    assert "第二批内容" in result


@pytest.mark.asyncio
async def test_transcribe_raises_when_all_batches_are_empty(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    _make_scanned_pdf(path, pages=1)

    async def _max_pages(key):
        return 10

    monkeypatch.setattr(scanned_pdf, "get_runtime_setting", _max_pages)
    llm = _SequencedVisionLLM(["   "])

    with pytest.raises(ValueError, match="未转写出任何内容"):
        await transcribe_scanned_pdf(path, llm_factory=lambda: llm)

    assert llm.calls == 1
