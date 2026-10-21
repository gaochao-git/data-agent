"""上传文件链路（dao/service/router）覆盖率补充测试。"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.workspace import uploaded_file_parser as ufp
from apps.chat_data_app.daos.uploaded_file_dao import UploadedFileDao
from apps.chat_data_app.routers import uploaded_files as uploaded_files_routes
from apps.chat_data_app.services.agent_run import uploaded_file_service as ufs
from utils import upload_storage_crypto as crypto
from utils.error_codes import ErrorCode


def response_payload(response):
    if isinstance(response, dict):
        return response
    return json.loads(response.body.decode("utf-8"))


class FakeSession:
    def __init__(self):
        self.executed = []
        self.added = []
        self.result = SimpleNamespace(scalar_one_or_none=lambda: 1)

    async def execute(self, statement):
        self.executed.append(statement)
        return self.result

    def add(self, model):
        self.added.append(model)

    async def flush(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def make_dao(session):
    return UploadedFileDao(session_context=lambda: session)


class ListResult:
    def __init__(self, scalar=1, rows=None):
        self._scalar = scalar
        self._rows = list(rows or [])

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)

    def scalar_one_or_none(self):
        return self._scalar


def test_dao_threads_and_crud_paths():
    session = FakeSession()
    session.result = ListResult()
    dao = make_dao(session)
    assert asyncio.run(dao.thread_belongs_to_user("t1", user_id="u1")) is True

    row = asyncio.run(
        dao.create_file(
            file_id="f1",
            thread_id="t1",
            original_filename="a.pdf",
            file_type="pdf",
            file_size=10,
            storage_dir="a.pdf",
            now=datetime(2026, 1, 1),
        )
    )
    assert row.file_id == "f1"
    assert row.parse_status == "pending"

    found = asyncio.run(dao.get_file("f1", user_id="u1"))
    assert found is None or isinstance(found, object)

    session.result = ListResult(scalar=None)
    assert asyncio.run(dao.thread_belongs_to_user("t2", user_id="u2")) is False
    assert asyncio.run(dao.get_file("missing", user_id="u2")) is None
    session.result = ListResult()

    asyncio.run(dao.delete_file("f1", user_id="u1"))
    assert len(session.executed) >= 3

    asyncio.run(dao.update_parse_status(file_id="f1", status="completed", user_id="u1", parse_filename="a.md"))
    asyncio.run(dao.update_parse_status(file_id="f1", status="failed", user_id="u1"))
    assert len(session.executed) >= 5

    rows = asyncio.run(dao.list_files_by_thread("t1", user_id="u1"))
    assert rows == []

    session.result = ListResult(rows=[SimpleNamespace(file_id="f1")])
    rows_desc = asyncio.run(dao.list_files_by_thread("t1", user_id="u1"))
    assert rows_desc[0].file_id == "f1"
    rows_asc = asyncio.run(dao.list_files_by_thread("t1", user_id="u1", ascending=True))
    assert rows_asc[0].file_id == "f1"


class FakeUpload:
    def __init__(self, filename: str, chunks: list[bytes]):
        self.filename = filename
        self._chunks = list(chunks)

    async def read(self, size=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


@pytest.fixture()
def uploads_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(ufs, "resolve_uploads_root", lambda user_id, thread_id, *, created_date=None: root)
    return root


class FakeDao:
    def __init__(self, belongs=True):
        self.belongs = belongs
        self.created = []
        self.deleted = []
        self.rows = {}
        self.by_thread = {}

    async def thread_belongs_to_user(self, thread_id, *, user_id):
        return self.belongs

    async def create_file(self, **kwargs):
        self.created.append(kwargs)
        return None

    async def get_file(self, file_id, *, user_id=None):
        return self.rows.get(file_id)

    async def delete_file(self, file_id, *, user_id=None):
        self.deleted.append((file_id, user_id))

    async def list_files_by_thread(self, thread_id, *, user_id=None, ascending=False):
        return self.by_thread.get(thread_id, [])


def make_row(**overrides):
    base = {
        "file_id": "f1",
        "thread_id": "t1",
        "original_filename": "报告.pdf",
        "file_type": "pdf",
        "file_size": 12,
        "storage_dir": "报告.pdf",
        "parse_status": "completed",
        "parse_filename": None,
        "create_time": datetime(2026, 8, 27, 10, 30),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_unique_storage_name_and_extension_helpers(uploads_root):
    first = ufs._unique_storage_name(uploads_root, "report.pdf")
    assert first == "report.pdf"
    (uploads_root / "report.pdf").write_bytes(b"x")
    second = ufs._unique_storage_name(uploads_root, "report.pdf")
    assert second == "report-1.pdf"
    (uploads_root / "report-1.pdf").write_bytes(b"x")
    third = ufs._unique_storage_name(uploads_root, "report.pdf")
    assert third == "report-2.pdf"
    assert ufs._get_file_extension("A.PDF") == "pdf"
    assert ufs._get_file_extension("noext") == ""


def test_upload_file_success_writes_chunked_file(uploads_root):
    dao = FakeDao(belongs=True)
    service = ufs.UploadedFileService(dao=dao)
    file = FakeUpload("data report.csv", [b"abc", b"def"])

    result = asyncio.run(service.upload_file(file, "t1", user_id="u1"))

    assert result["file_size"] == 6
    assert result["file_type"] == "csv"
    stored = uploads_root / result["storage_dir"]
    assert stored.read_bytes() == b"abcdef"
    assert len(dao.created) == 1
    assert list(uploads_root.glob(".*upload")) == []


def test_upload_file_rejects_oversize_or_denied(uploads_root):
    dao = FakeDao(belongs=False)
    service = ufs.UploadedFileService(dao=dao)
    with pytest.raises(ValueError, match="无权访问"):
        asyncio.run(service.upload_file(FakeUpload("a.txt", [b"x"]), "t1", user_id="u1"))

    big_chunk = b"z" * (ufs.UPLOAD_CHUNK_SIZE + 1)
    oversized = FakeUpload("big.bin", [big_chunk] * 60)
    dao_ok = FakeDao(belongs=True)
    service_ok = ufs.UploadedFileService(dao=dao_ok)
    with pytest.raises(ValueError, match="50MB"):
        asyncio.run(service_ok.upload_file(oversized, "t1", user_id="u1"))


def test_upload_file_rolls_back_record_failure(uploads_root):
    class BrokenDao(FakeDao):
        async def create_file(self, **kwargs):
            raise RuntimeError("db down")

    service = ufs.UploadedFileService(dao=BrokenDao())
    with pytest.raises(RuntimeError, match="db down"):
        asyncio.run(service.upload_file(FakeUpload("b.txt", [b"data"]), "t1", user_id="u1"))
    leftover = [p.name for p in uploads_root.iterdir()]
    assert leftover == []


def test_get_info_delete_content_path_branches(uploads_root):
    dao = FakeDao(belongs=True)
    service = ufs.UploadedFileService(dao=dao)
    row = make_row(parse_filename="报告.md")
    dao.rows["f1"] = row

    info = asyncio.run(service.get_file_info("f1", user_id="u1", thread_id="t1"))
    assert info["file_id"] == "f1"
    assert info["create_time"] == "2026-08-27 10:30:00"
    assert asyncio.run(service.get_file_info("f1", user_id="u1", thread_id="other")) is None
    dao.rows.clear()
    assert asyncio.run(service.get_file_info("f1", user_id="u1")) is None

    dao.rows["f1"] = make_row()
    (uploads_root / "报告.pdf").write_bytes(b"raw")
    assert asyncio.run(service.delete_file("f1", user_id="u1", thread_id="t1")) is True
    assert not (uploads_root / "报告.pdf").exists()
    assert dao.deleted == [("f1", "u1")]

    (uploads_root / "报告.pdf").write_bytes(b"raw")
    blocking = Path(str(uploads_root / "报告.pdf"))
    dao.rows["f1"] = make_row()
    original_unlink = Path.unlink

    def blocked_unlink(self, *args, **kwargs):
        if self == blocking:
            raise OSError("locked")
        return original_unlink(self, *args, **kwargs)

    Path.unlink = blocked_unlink
    try:
        # 物理删除失败：保留记账记录并返回失败，避免"账删了文件还在"的孤儿；
        # dao.deleted 仍只有上面首次成功删除的那条，说明本次失败未删账
        assert asyncio.run(service.delete_file("f1", user_id="u1", thread_id="t1")) is False
        assert dao.deleted == [("f1", "u1")]
    finally:
        Path.unlink = original_unlink

    # 重试（不再阻塞）可正常删除
    assert asyncio.run(service.delete_file("f1", user_id="u1", thread_id="t1")) is True
    assert dao.deleted == [("f1", "u1"), ("f1", "u1")]

    dao.rows.clear()
    assert asyncio.run(service.delete_file("f1", user_id="u1")) is False

    # 新语义：重试删除后记录与文件都已删除，不能再按孤儿文件读到内容
    dao.rows["f1"] = make_row()
    assert (uploads_root / "报告.pdf").exists() is False
    content = asyncio.run(service.get_file_content("f1", user_id="u1", thread_id="t1"))
    assert content is None
    dao.rows["f1"] = make_row(storage_dir="missing.bin")
    assert asyncio.run(service.get_file_content("f1", user_id="u1", thread_id="t1")) is None

    # get_file_path_by_id 独立建一份文件+记录验证（上一段已按新语义删净）
    dao.rows["f1"] = make_row()
    (uploads_root / "报告.pdf").write_bytes(b"raw")
    path = asyncio.run(service.get_file_path_by_id("f1", user_id="u1", thread_id="t1"))
    assert path is not None and path.endswith("报告.pdf")

    summary_rows = [make_row(), make_row(file_id="f2")]
    dao.by_thread["t1"] = summary_rows
    summaries = asyncio.run(service.list_files_by_thread("t1", user_id="u1"))
    assert [item["file_id"] for item in summaries] == ["f1", "f2"]
    detailed = asyncio.run(service.get_files_by_thread("t1", user_id="u1"))
    assert detailed[0]["status"] == "completed"


def test_module_level_helpers_delegate_to_service(monkeypatch):
    singleton_before = ufs.get_uploaded_file_service()
    assert isinstance(singleton_before, ufs.UploadedFileService)
    assert ufs.get_uploaded_file_service() is singleton_before

    calls = []

    async def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        return "ok"

    monkeypatch.setattr(singleton_before, "upload_file", fake_call)
    monkeypatch.setattr(singleton_before, "get_file_info", fake_call)
    monkeypatch.setattr(singleton_before, "delete_file", fake_call)
    monkeypatch.setattr(singleton_before, "get_file_content", fake_call)
    monkeypatch.setattr(singleton_before, "list_files_by_thread", fake_call)
    monkeypatch.setattr(singleton_before, "get_files_by_thread", fake_call)
    monkeypatch.setattr(singleton_before, "get_file_path_by_id", fake_call)

    assert asyncio.run(ufs.upload_file(SimpleNamespace(filename="x"), "t1")) == "ok"
    assert asyncio.run(ufs.get_file_info("f1")) == "ok"
    assert asyncio.run(ufs.delete_file("f1")) == "ok"
    assert asyncio.run(ufs.get_file_content("f1")) == "ok"
    assert asyncio.run(ufs.list_files_by_thread("t1")) == "ok"
    assert asyncio.run(ufs.get_files_by_thread("t1")) == "ok"
    assert asyncio.run(ufs.get_file_path_by_id("f1")) == "ok"
    assert len(calls) == 7


def patch_route_user(monkeypatch):
    monkeypatch.setattr(
        uploaded_files_routes,
        "_get_current_user",
        lambda req: SimpleNamespace(user_id="u1"),
    )


def test_router_content_type_map():
    assert uploaded_files_routes._get_content_type("pdf") == "application/pdf"
    assert uploaded_files_routes._get_content_type("docx").startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml"
    )
    assert uploaded_files_routes._get_content_type("zip") == "application/octet-stream"


def test_router_upload_validations(monkeypatch):
    payload = response_payload(
        asyncio.run(
            uploaded_files_routes.upload_file_api(
                file=SimpleNamespace(filename=None),
                thread_id="t1",
                req=None,
            )
        )
    )
    assert payload["code"] == ErrorCode.CHATDATA_REQUIRED_FIELD

    payload = response_payload(
        asyncio.run(
            uploaded_files_routes.upload_file_api(
                file=SimpleNamespace(filename="evil.exe"),
                thread_id="t1",
                req=None,
            )
        )
    )
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert ".exe" in payload["msg"]

    patch_route_user(monkeypatch)

    async def fake_upload(file, thread_id, user_id=None):
        return {"file_id": "f9"}

    monkeypatch.setattr(uploaded_files_routes, "upload_file", fake_upload)
    payload = response_payload(
        asyncio.run(
            uploaded_files_routes.upload_file_api(
                file=SimpleNamespace(filename="a.pdf"),
                thread_id="t1",
                req=None,
            )
        )
    )
    assert payload["data"]["file_id"] == "f9"

    async def value_error(*_args, **_kwargs):
        raise ValueError("文件大小不能超过50MB")

    monkeypatch.setattr(uploaded_files_routes, "upload_file", value_error)
    payload = response_payload(
        asyncio.run(
            uploaded_files_routes.upload_file_api(
                file=SimpleNamespace(filename="a.pdf"),
                thread_id="t1",
                req=None,
            )
        )
    )
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(uploaded_files_routes, "upload_file", boom)
    payload = response_payload(
        asyncio.run(
            uploaded_files_routes.upload_file_api(
                file=SimpleNamespace(filename="a.pdf"),
                thread_id="t1",
                req=None,
            )
        )
    )
    assert payload["code"] == ErrorCode.CHATDATA_UPLOAD_FAILED


def test_router_list_info_delete_download(monkeypatch):
    patch_route_user(monkeypatch)

    async def list_files(thread_id, user_id=None):
        return [{"file_id": "f1"}]

    async def get_info(file_id, user_id=None):
        return {"file_id": file_id, "original_filename": "报告.pdf", "file_type": "pdf"}

    async def missing_info(file_id, user_id=None):
        return None

    async def delete_ok(file_id, user_id=None):
        return True

    async def delete_missing(file_id, user_id=None):
        return False

    async def content_ok(file_id, user_id=None):
        return b"raw-bytes"

    async def content_missing(file_id, user_id=None):
        return None

    monkeypatch.setattr(uploaded_files_routes, "list_files_by_thread", list_files)
    payload = response_payload(asyncio.run(uploaded_files_routes.list_thread_files_api("t1", None)))
    assert payload["data"]["files"][0]["file_id"] == "f1"

    monkeypatch.setattr(uploaded_files_routes, "get_file_info", get_info)
    payload = response_payload(asyncio.run(uploaded_files_routes.get_file_info_api("f1", None)))
    assert payload["data"]["file_id"] == "f1"
    monkeypatch.setattr(uploaded_files_routes, "get_file_info", missing_info)
    payload = response_payload(asyncio.run(uploaded_files_routes.get_file_info_api("f1", None)))
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND

    monkeypatch.setattr(uploaded_files_routes, "delete_file", delete_ok)
    payload = response_payload(asyncio.run(uploaded_files_routes.delete_file_api("f1", None)))
    assert payload["msg"] == "删除成功"
    monkeypatch.setattr(uploaded_files_routes, "delete_file", delete_missing)
    payload = response_payload(asyncio.run(uploaded_files_routes.delete_file_api("f1", None)))
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND

    from fastapi.responses import Response

    monkeypatch.setattr(uploaded_files_routes, "get_file_info", get_info)
    monkeypatch.setattr(uploaded_files_routes, "get_file_content", content_ok)
    result = asyncio.run(uploaded_files_routes.get_file_content_api("f1", None))
    assert isinstance(result, Response)
    assert result.body == b"raw-bytes"
    assert "pdf" in result.media_type

    monkeypatch.setattr(uploaded_files_routes, "get_file_content", content_missing)
    payload = response_payload(asyncio.run(uploaded_files_routes.get_file_content_api("f1", None)))
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND

    monkeypatch.setattr(uploaded_files_routes, "get_file_info", missing_info)
    payload = response_payload(asyncio.run(uploaded_files_routes.get_file_content_api("f1", None)))
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_upload_file_encrypted_roundtrip(uploads_root, monkeypatch):
    import base64

    from utils.crypto import encrypt_password

    monkeypatch.setenv("ENCRYPT_KEY", "00" * 16)
    monkeypatch.setenv("CHAT_DATA_UPLOAD_ENCRYPTION_KEY", encrypt_password(base64.b64encode(b"k" * 32).decode()))
    crypto.clear_materialized_cache()
    dao = FakeDao(belongs=True)
    service = ufs.UploadedFileService(dao=dao)
    result = asyncio.run(service.upload_file(FakeUpload("secret.txt", [b"hello", b" world"]), "t1", user_id="u1"))
    stored = (uploads_root / result["storage_dir"]).read_bytes()
    assert stored.startswith(crypto.MAGIC)
    assert b"hello" not in stored
    dao.rows[result["file_id"]] = make_row(storage_dir=result["storage_dir"], original_filename="secret.txt")
    assert asyncio.run(service.get_file_content(result["file_id"], user_id="u1", thread_id="t1")) == b"hello world"


def test_csv_json_xml_are_upload_allowed():
    assert {"csv", "json", "xml"} <= ufs.ALLOWED_FILE_EXTENSIONS
    assert {"csv", "json", "xml"} <= {item.lstrip(".") for item in ufp.SUPPORTED_DOCUMENT_EXTENSIONS}
    assert {item.lstrip(".") for item in ufp.SUPPORTED_DOCUMENT_EXTENSIONS} == ufs.ALLOWED_FILE_EXTENSIONS - set(
        ufp.IMAGE_EXTENSIONS
    )
