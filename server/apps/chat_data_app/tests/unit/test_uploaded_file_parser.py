import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.chat_data_app.services import uploaded_file_parser


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
