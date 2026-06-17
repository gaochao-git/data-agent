import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.services import file_text_extractor


def test_decode_text_bytes_fallbacks():
    assert file_text_extractor.decode_text_bytes("汉字".encode("gbk")) == "汉字"
    assert "\ufffd" in file_text_extractor.decode_text_bytes(b"\xff")


def test_extract_text_dispatches_by_suffix(monkeypatch):
    monkeypatch.setattr(file_text_extractor, "extract_docx_text", lambda data: " docx text ")
    monkeypatch.setattr(file_text_extractor, "extract_pdf_text", lambda data: " pdf text ")

    assert file_text_extractor.extract_text_from_file_bytes("rules.TXT", b" hello ") == "hello"
    assert file_text_extractor.extract_text_from_file_bytes("rules.docx", b"docx") == "docx text"
    assert file_text_extractor.extract_text_from_file_bytes("rules.pdf", b"pdf") == "pdf text"


def test_extract_docx_text_reads_paragraphs_and_tables(monkeypatch):
    def fake_document(_stream):
        return SimpleNamespace(
            paragraphs=[
                SimpleNamespace(text=" 标题 "),
                SimpleNamespace(text=""),
            ],
            tables=[
                SimpleNamespace(rows=[
                    SimpleNamespace(cells=[
                        SimpleNamespace(text="A\nB"),
                        SimpleNamespace(text=""),
                    ]),
                    SimpleNamespace(cells=[
                        SimpleNamespace(text=" C "),
                    ]),
                ])
            ],
        )

    monkeypatch.setitem(sys.modules, "docx", SimpleNamespace(Document=fake_document))

    assert file_text_extractor.extract_docx_text(b"docx") == "标题\nA B\nC"


def test_extract_pdf_text_closes_document(monkeypatch):
    closed = {"value": False}

    class FakePage:
        def __init__(self, text):
            self._text = text

        def get_text(self):
            return self._text

    class FakeDocument:
        def __iter__(self):
            return iter([FakePage(" 第一页 "), FakePage(""), FakePage("第二页")])

        def close(self):
            closed["value"] = True

    def fake_open(*, stream, filetype):
        assert stream == b"pdf"
        assert filetype == "pdf"
        return FakeDocument()

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=fake_open))

    assert file_text_extractor.extract_pdf_text(b"pdf") == "第一页\n\n第二页"
    assert closed["value"] is True
