import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document as build_docx

from apps.chat_data_app.agent.workspace import uploaded_file_parser


def test_parser_uses_local_file_conversion_without_plugins(monkeypatch, tmp_path):
    source = tmp_path / "report.pptx"
    source.write_bytes(b"pptx")
    captured = {}

    class FakeMarkItDown:
        def __init__(self, *, enable_plugins):
            captured["enable_plugins"] = enable_plugins

        def convert_local(self, path):
            captured["path"] = path
            return SimpleNamespace(text_content="# 演示文稿")

    monkeypatch.setattr(uploaded_file_parser, "MarkItDown", FakeMarkItDown)

    result = asyncio.run(uploaded_file_parser.parse_uploaded_file_to_markdown(source))

    assert result == "# 演示文稿"
    assert captured == {"enable_plugins": False, "path": source}


def test_parser_rejects_unsupported_or_empty_files(monkeypatch, tmp_path):
    unsupported = tmp_path / "archive.zip"
    unsupported.write_bytes(b"zip")

    with pytest.raises(ValueError, match="不支持解析"):
        uploaded_file_parser._convert_local_upload(unsupported)

    source = tmp_path / "empty.docx"
    source.write_bytes(b"docx")

    class EmptyMarkItDown:
        def __init__(self, **_kwargs):
            pass

        def convert_local(self, _path):
            return SimpleNamespace(text_content="  \n")

    monkeypatch.setattr(uploaded_file_parser, "MarkItDown", EmptyMarkItDown)

    with pytest.raises(ValueError, match="未提取到"):
        uploaded_file_parser._convert_local_upload(source)


def _build_merged_docx(path: Path) -> None:
    """构造含横向(gridSpan)与纵向(vMerge)合并单元格的 docx。"""
    document = build_docx()
    table = document.add_table(rows=4, cols=4)
    table.style = "Table Grid"
    for col, header in enumerate(["机构", "一季度", "二季度", "合计"]):
        table.cell(0, col).text = header

    merged_row = table.rows[1]
    merged_row.cells[1].text = "半年数据"
    merged_row.cells[1].merge(merged_row.cells[2])
    merged_row.cells[3].text = "100"

    table.cell(2, 0).text = "B 机构"
    table.cell(2, 0).merge(table.cell(3, 0))
    for col, value in ((1, "60"), (2, "70"), (3, "130")):
        table.cell(2, col).text = value
    for col, value in ((1, "80"), (2, "90"), (3, "170")):
        table.cell(3, col).text = value
    document.save(str(path))


def test_docx_merged_cells_expanded_before_markitdown(tmp_path):
    source = tmp_path / "merged.docx"
    _build_merged_docx(source)

    content = uploaded_file_parser._convert_local_upload(source)

    table_rows = [line for line in content.splitlines() if line.startswith("|")]
    assert table_rows, "应解析出 markdown 表格"
    cell_pipes = {row.count("|") for row in table_rows}
    assert cell_pipes == {5}, f"所有表格行应为等宽 4 列, 实际: {table_rows}"
    assert sum(row.count("半年数据") for row in table_rows) == 2, "横向合并内容应重复填充"
    assert sum(row.count("B 机构") for row in table_rows) == 2, "纵向合并内容应重复填充"
    last_row = table_rows[-1]
    assert all(token in last_row for token in ("B 机构", "80", "90", "170")), "延续行不得串列"


def test_docx_without_merge_keeps_original_file(tmp_path):
    document = build_docx()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "A"
    table.cell(1, 1).text = "B"
    source = tmp_path / "plain.docx"
    document.save(str(source))

    assert uploaded_file_parser._resolve_markitdown_source(source, ".docx", tmp_path) == source

    non_docx = tmp_path / "note.txt"
    non_docx.write_text("hello", encoding="utf-8")
    assert uploaded_file_parser._resolve_markitdown_source(non_docx, ".txt", tmp_path) == non_docx


def test_docx_preprocess_failure_falls_back_to_original(tmp_path):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not-a-real-docx")

    assert uploaded_file_parser._resolve_markitdown_source(broken, ".docx", tmp_path) == broken


def test_csv_json_xml_are_parsed_as_markdown(tmp_path):
    samples = {
        "data.csv": ("name,amount\nA,10\nB,20\n", "| name | amount |"),
        "data.json": ('{"name":"A","items":[1,2]}', '"name":"A"'),
        "data.xml": ('<root><item id="1">A</item></root>', "<root>"),
    }
    for filename, (body, expected) in samples.items():
        source = tmp_path / filename
        source.write_text(body, encoding="utf-8")
        content = uploaded_file_parser._convert_local_upload(source)
        assert expected in content


def test_gbk_csv_is_decoded_before_markitdown(tmp_path):
    source = tmp_path / "gbk.csv"
    source.write_bytes("姓名,金额\n张三,10\n".encode("gbk"))
    content = uploaded_file_parser._convert_local_upload(source)
    assert "张三" in content
    assert "姓名" in content


def test_csv_rows_are_truncated_with_note(monkeypatch, tmp_path):
    source = tmp_path / "large.csv"
    source.write_text("name,amount\n" + "\n".join(f"A{i},{i}" for i in range(5)) + "\n", encoding="utf-8")
    monkeypatch.setattr(uploaded_file_parser, "CSV_PARSE_MAX_ROWS", 2)
    content = uploaded_file_parser._convert_local_upload(source)
    assert "A0" in content and "A1" in content
    assert "A4" not in content
    assert "CSV 已截断" in content and "原始共 5 数据行" in content


def test_utf16_and_latin1_csv_are_readable(tmp_path):
    utf16 = tmp_path / "utf16.csv"
    utf16.write_bytes("姓名,金额\n张三,10\n".encode("utf-16"))
    assert "张三" in uploaded_file_parser._convert_local_upload(utf16)

    latin1 = tmp_path / "latin1.csv"
    latin1.write_bytes("name,city\nAndré,Zürich\n".encode("latin-1"))
    content = uploaded_file_parser._convert_local_upload(latin1)
    assert "André" in content and "Zürich" in content
