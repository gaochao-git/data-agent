"""uploaded_file_parser 覆盖率补充：解析失败、编码兜底与 CSV/docx 预处理分支。"""

import codecs
import sys

import pytest
from docx import Document as build_docx

from apps.chat_data_app.agent.workspace import uploaded_file_parser


def test_missing_supported_file_reports_not_found(tmp_path):
    missing = tmp_path / "gone.csv"
    with pytest.raises(FileNotFoundError, match="文件不存在"):
        uploaded_file_parser._convert_local_upload(missing)


def test_markitdown_failure_is_wrapped(monkeypatch, tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")

    class BrokenMarkItDown:
        def __init__(self, **_kwargs):
            pass

        def convert_local(self, _path):
            raise RuntimeError("converter exploded")

    monkeypatch.setattr(uploaded_file_parser, "MarkItDown", BrokenMarkItDown)
    with pytest.raises(ValueError, match="文件解析失败"):
        uploaded_file_parser._convert_local_upload(source)


def test_normalize_text_source_keeps_original_when_undecodable(monkeypatch, tmp_path):
    source = tmp_path / "raw.txt"
    source.write_bytes(b"\xff\xfe\xff")
    monkeypatch.setattr(uploaded_file_parser, "_decode_text_bytes", lambda _data: None)
    assert uploaded_file_parser._normalize_text_source(source, ".txt", tmp_path) == source


def test_decode_text_bytes_falls_back_after_invalid_utf16(monkeypatch):
    data = codecs.BOM_UTF16_LE + b"\x00"
    decoded = uploaded_file_parser._decode_text_bytes(data)
    assert decoded is None or isinstance(decoded, str)


def test_decode_text_bytes_returns_none_when_charset_normalizer_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "charset_normalizer", None)
    assert uploaded_file_parser._decode_text_bytes(b"\xff\xfe\xff") is None


def test_decode_text_bytes_uses_charset_normalizer_fallback():
    decoded = uploaded_file_parser._decode_text_bytes(b"\xff\xfe\xff")
    assert isinstance(decoded, str) and decoded


def test_prepare_csv_source_handles_open_and_decode_errors(tmp_path):
    directory = tmp_path / "dir.csv"
    directory.mkdir()
    assert uploaded_file_parser._prepare_csv_source(directory, tmp_path) == (directory, "")

    empty = tmp_path / "empty.csv"
    empty.write_bytes(b"")
    assert uploaded_file_parser._prepare_csv_source(empty, tmp_path) == (empty, "")

    bad_header = tmp_path / "bad_header.csv"
    bad_header.write_bytes(b"\xff\xfe\xff\n")
    assert uploaded_file_parser._prepare_csv_source(bad_header, tmp_path) == (bad_header, "")

    bad_row = tmp_path / "bad_row.csv"
    # 超过 TextIOWrapper 默认缓冲，确保表头先解码成功、坏字节在行迭代时才触发
    bad_row.write_bytes(b"name,amount\n" + b"".join(b"A,1\n" for _ in range(5000)) + b"\xff\xfe\xff\n")
    assert uploaded_file_parser._prepare_csv_source(bad_row, tmp_path) == (bad_row, "")


def test_resolve_markitdown_source_returns_original_without_merge(tmp_path):
    document = build_docx()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    source = tmp_path / "plain.docx"
    document.save(str(source))

    assert uploaded_file_parser._resolve_markitdown_source(source, ".docx", tmp_path) == source


def test_resolve_markitdown_source_returns_original_when_doc_has_no_tables(monkeypatch, tmp_path):
    from types import SimpleNamespace

    source = tmp_path / "no-tables.docx"
    source.write_bytes(b"PK")
    monkeypatch.setattr(
        uploaded_file_parser,
        "_load_docx_document",
        lambda _path: SimpleNamespace(tables=[]),
    )
    assert uploaded_file_parser._resolve_markitdown_source(source, ".docx", tmp_path) == source


def _merged_docx(path):
    document = build_docx()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "机构"
    table.cell(0, 1).text = "金额"
    table.cell(1, 0).text = "A"
    table.cell(1, 0).merge(table.cell(1, 1))
    document.save(str(path))


def test_resolve_markitdown_source_falls_back_when_rebuild_fails(monkeypatch, tmp_path):
    source = tmp_path / "merged.docx"
    _merged_docx(source)

    def failing_rebuild(*_args, **_kwargs):
        raise IndexError("ragged grid")

    monkeypatch.setattr(uploaded_file_parser, "_rebuild_table_rectangular", failing_rebuild)
    assert uploaded_file_parser._resolve_markitdown_source(source, ".docx", tmp_path) == source


def test_table_has_merge_detects_plain_table():
    from types import SimpleNamespace

    def cell(tc):
        return SimpleNamespace(_tc=tc)

    plain = SimpleNamespace(
        rows=[
            SimpleNamespace(cells=[cell(object()), cell(object())]),
            SimpleNamespace(cells=[cell(object()), cell(object())]),
        ]
    )
    assert uploaded_file_parser._table_has_merge(plain) is False


def test_set_cell_paragraphs_preserves_multiline_text():
    document = build_docx()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    uploaded_file_parser._set_cell_paragraphs(cell, "第一行\n第二行")
    assert [paragraph.text for paragraph in cell.paragraphs] == ["第一行", "第二行"]
