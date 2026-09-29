"""conversation_workspace 路由全分支单元测试。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from apps.chat_data_app.routers import conversation_workspace as cw_routes
from utils.error_codes import ErrorCode


def response_payload(response):
    if isinstance(response, dict):
        return response
    return json.loads(response.body.decode("utf-8"))


class FakeWorkspaceService:
    """可编排返回值/异常的服务替身。"""

    def __init__(self):
        self.access = True
        self.files = [{"relative_path": "reports/result.csv", "path": "reports/result.csv"}]
        self.file_payload = {"relative_path": "a.md", "content": "hello"}
        self.raw_payload = {"relative_path": "docs/报告.pdf", "data": b"bytes", "mime": "application/pdf"}
        self.query_result = {"result_id": "res_1"}
        self.preview = {"available": True}
        self.archive_payload = {
            "temp_path": "/tmp/chatdata-outputs-fake.zip",
            "filename": "outputs.zip",
            "file_count": 1,
            "size": 10,
        }
        self.errors = {}

    async def can_access(self, conversation_id, user_id):
        return self.access

    async def list_files(self, conversation_id, user_id=None):
        exc = self.errors.get("list")
        if exc:
            raise exc
        return self.files

    async def read_file(self, conversation_id, user_id=None, path=None):
        exc = self.errors.get("read")
        if exc:
            raise exc
        return self.file_payload

    async def read_raw(self, conversation_id, user_id=None, path=None):
        exc = self.errors.get("raw")
        if exc:
            raise exc
        return self.raw_payload

    async def render_preview(self, conversation_id, user_id=None, path=None):
        exc = self.errors.get("preview")
        if exc:
            raise exc
        return self.preview

    async def read_query_result(self, conversation_id, user_id=None, result_id=None):
        exc = self.errors.get("query_result")
        if exc:
            raise exc
        return self.query_result

    async def archive_directory(self, conversation_id, user_id=None, directory=None):
        exc = self.errors.get("archive")
        if exc:
            raise exc
        return self.archive_payload


@pytest.fixture()
def fake_service(monkeypatch):
    service = FakeWorkspaceService()
    monkeypatch.setattr(cw_routes, "can_access_conversation_workspace", service.can_access)
    monkeypatch.setattr(cw_routes, "list_workspace_files_for_conversation", service.list_files)
    monkeypatch.setattr(cw_routes, "read_workspace_file_for_conversation", service.read_file)
    monkeypatch.setattr(cw_routes, "read_conversation_workspace_raw_file", service.read_raw)
    monkeypatch.setattr(cw_routes, "render_conversation_workspace_office_preview", service.render_preview)
    monkeypatch.setattr(cw_routes, "read_conversation_query_result", service.read_query_result)
    monkeypatch.setattr(cw_routes, "archive_conversation_workspace_directory", service.archive_directory)
    return service


def fake_request():
    return SimpleNamespace()


def current_user():
    return SimpleNamespace(user_id="u1", username="张三")


def patch_user(monkeypatch):
    monkeypatch.setattr(cw_routes, "_get_current_user", lambda request: current_user())


def test_normalize_and_internal_path_helpers():
    assert (
        cw_routes._normalize_workspace_file_path("/mnt/user-data/workspace/data_context.json")
        == "workspace/data_context.json"
    )
    assert cw_routes._is_internal_workspace_file("/mnt/user-data/workspace/data_context.json") is True
    assert cw_routes._is_internal_workspace_file("workspace/reports/a.csv") is False
    files = [
        {"relative_path": "workspace/data_context.json"},
        {"path": "workspace/out.csv"},
    ]
    visible = cw_routes._filter_visible_workspace_files(files)
    assert [item["path"] for item in visible] == ["workspace/out.csv"]


def test_list_workspace_files_success_filters_internal(fake_service, monkeypatch):
    fake_service.files = [
        {"relative_path": "/mnt/user-data/workspace/data_context.json"},
        {"relative_path": "reports/out.csv"},
    ]
    patch_user(monkeypatch)
    payload = response_payload(asyncio.run(cw_routes.list_conversation_workspace_files("conv_1", fake_request())))
    assert payload["msg"] == "获取成功"
    assert [item["relative_path"] for item in payload["data"]["files"]] == ["reports/out.csv"]


def test_list_workspace_files_access_denied_and_errors(fake_service, monkeypatch):
    patch_user(monkeypatch)
    fake_service.access = False
    payload = response_payload(asyncio.run(cw_routes.list_conversation_workspace_files("conv_1", fake_request())))
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload["msg"] == "对话不存在或无权访问"

    fake_service.access = True
    fake_service.errors["list"] = ValueError("非法会话")
    payload = response_payload(asyncio.run(cw_routes.list_conversation_workspace_files("conv_1", fake_request())))
    assert payload["status"] == "error"

    fake_service.errors["list"] = RuntimeError("boom")
    payload = response_payload(asyncio.run(cw_routes.list_conversation_workspace_files("conv_1", fake_request())))
    assert payload["status"] == "error"


def test_read_workspace_file_branches(fake_service, monkeypatch):
    patch_user(monkeypatch)
    payload = response_payload(
        asyncio.run(cw_routes.read_conversation_workspace_file("conv_1", "a.md", fake_request()))
    )
    assert payload["data"]["content"] == "hello"

    payload = response_payload(
        asyncio.run(cw_routes.read_conversation_workspace_file("conv_1", "workspace/data_context.json", fake_request()))
    )
    assert payload["msg"] == "文件不存在"

    fake_service.access = False
    payload = response_payload(
        asyncio.run(cw_routes.read_conversation_workspace_file("conv_1", "a.md", fake_request()))
    )
    assert payload["status"] == "error"

    fake_service.access = True
    for exc, expect_status in [
        (FileNotFoundError("不存在"), "error"),
        (ValueError("非法路径"), "error"),
        (RuntimeError("boom"), "error"),
    ]:
        fake_service.errors["read"] = exc
        payload = response_payload(
            asyncio.run(cw_routes.read_conversation_workspace_file("conv_1", "a.md", fake_request()))
        )
        assert payload["status"] == expect_status

    fake_service.errors.clear()

    def raise_http(request):
        raise HTTPException(status_code=401)

    monkeypatch.setattr(cw_routes, "_get_current_user", raise_http)
    with pytest.raises(HTTPException):
        asyncio.run(cw_routes.read_conversation_workspace_file("conv_1", "a.md", fake_request()))


def test_read_raw_file_returns_binary_response(fake_service, monkeypatch):
    patch_user(monkeypatch)
    result = asyncio.run(cw_routes.read_conversation_workspace_raw_file_api("conv_1", "docs/报告.pdf", fake_request()))
    assert isinstance(result, Response)
    assert result.body == b"bytes"
    disposition = result.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition

    fake_service.errors["raw"] = FileNotFoundError("missing")
    payload = response_payload(
        asyncio.run(cw_routes.read_conversation_workspace_raw_file_api("conv_1", "x.pdf", fake_request()))
    )
    assert payload["status"] == "error"

    fake_service.errors["raw"] = RuntimeError("boom")
    payload = response_payload(
        asyncio.run(cw_routes.read_conversation_workspace_raw_file_api("conv_1", "x.pdf", fake_request()))
    )
    assert payload["status"] == "error"

    fake_service.errors.clear()
    payload = response_payload(
        asyncio.run(
            cw_routes.read_conversation_workspace_raw_file_api(
                "conv_1", "/mnt/user-data/workspace/data_context.json", fake_request()
            )
        )
    )
    assert payload["msg"] == "文件不存在"

    fake_service.access = False
    payload = response_payload(
        asyncio.run(cw_routes.read_conversation_workspace_raw_file_api("conv_1", "x.pdf", fake_request()))
    )
    assert payload["status"] == "error"


def test_office_preview_fallbacks(fake_service, monkeypatch):
    patch_user(monkeypatch)
    payload = response_payload(
        asyncio.run(cw_routes.render_conversation_workspace_office_preview_api("conv_1", "a.docx", fake_request()))
    )
    assert payload["data"]["available"] is True

    fake_service.errors["preview"] = FileNotFoundError("no officecli")
    payload = response_payload(
        asyncio.run(cw_routes.render_conversation_workspace_office_preview_api("conv_1", "a.docx", fake_request()))
    )
    assert payload["data"] == {"available": False, "reason": "preview_unavailable"}

    fake_service.errors["preview"] = RuntimeError("boom")
    payload = response_payload(
        asyncio.run(cw_routes.render_conversation_workspace_office_preview_api("conv_1", "a.docx", fake_request()))
    )
    assert payload["data"] == {"available": False, "reason": "preview_failed"}

    fake_service.errors.clear()
    fake_service.access = False
    payload = response_payload(
        asyncio.run(cw_routes.render_conversation_workspace_office_preview_api("conv_1", "a.docx", fake_request()))
    )
    assert payload["status"] == "error"


def test_get_query_result_branches(fake_service, monkeypatch):
    patch_user(monkeypatch)
    payload = response_payload(asyncio.run(cw_routes.get_conversation_query_result("conv_1", "res_1", fake_request())))
    assert payload["data"]["result_id"] == "res_1"

    fake_service.query_result = None
    payload = response_payload(asyncio.run(cw_routes.get_conversation_query_result("conv_1", "res_x", fake_request())))
    assert payload["msg"] == "查询结果不存在"

    fake_service.query_result = {"result_id": "res_1"}
    fake_service.errors["query_result"] = ValueError("bad id")
    payload = response_payload(asyncio.run(cw_routes.get_conversation_query_result("conv_1", "res_x", fake_request())))
    assert payload["status"] == "error"

    fake_service.errors["query_result"] = RuntimeError("boom")
    payload = response_payload(asyncio.run(cw_routes.get_conversation_query_result("conv_1", "res_x", fake_request())))
    assert payload["status"] == "error"

    fake_service.access = False
    payload = response_payload(asyncio.run(cw_routes.get_conversation_query_result("conv_1", "res_1", fake_request())))
    assert payload["status"] == "error"


def test_import_skill_from_workspace_branches(fake_service, monkeypatch):
    patch_user(monkeypatch)
    imported = []

    async def fake_import(skill_dir, user_id, operator_name=""):
        imported.append((skill_dir, user_id, operator_name))
        return {"id": "skill_1"}

    monkeypatch.setattr(
        cw_routes, "ensure_conversation_workspace", lambda conv, uid, *, created_date=None: {"root": "/tmp/ws"}
    )
    monkeypatch.setattr(cw_routes, "resolve_output_skill_directory", lambda root, path: f"{root}/{path}")
    monkeypatch.setattr(cw_routes.skill_service, "import_user_skill_from_directory_async", fake_import)

    body = SimpleNamespace(skill_dir_path="skills/report_skill")
    payload = response_payload(
        asyncio.run(cw_routes.import_conversation_workspace_skill("conv_1", body, fake_request()))
    )
    assert payload["data"]["id"] == "skill_1"
    assert imported[0][0] == "/tmp/ws/skills/report_skill"
    assert imported[0][2] == "张三"

    fake_service.access = False
    payload = response_payload(
        asyncio.run(cw_routes.import_conversation_workspace_skill("conv_1", body, fake_request()))
    )
    assert payload["status"] == "error"

    fake_service.access = True

    async def invalid_import(*_args, **_kwargs):
        raise ValueError("技能目录不合法")

    monkeypatch.setattr(cw_routes.skill_service, "import_user_skill_from_directory_async", invalid_import)
    payload = response_payload(
        asyncio.run(cw_routes.import_conversation_workspace_skill("conv_1", body, fake_request()))
    )
    assert payload["status"] == "error"

    async def boom_import(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cw_routes.skill_service, "import_user_skill_from_directory_async", boom_import)
    payload = response_payload(
        asyncio.run(cw_routes.import_conversation_workspace_skill("conv_1", body, fake_request()))
    )
    assert payload["status"] == "error"


def test_download_workspace_archive_branches(fake_service, monkeypatch):
    patch_user(monkeypatch)

    # 成功：返回 zip 二进制响应（attachment）
    result = asyncio.run(cw_routes.download_conversation_workspace_archive("conv_1", "outputs", fake_request()))
    assert isinstance(result, Response)
    disposition = result.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "outputs.zip" in disposition

    # 非法目录路径：校验已下沉到 service（normalize_archive_directory 抛 ValueError），
    # 路由只负责翻译成 BAD_REQUEST，不再重复一份目录白名单，避免两处口径漂移
    fake_service.errors["archive"] = ValueError("目录不支持打包: ../etc")
    payload = response_payload(
        asyncio.run(cw_routes.download_conversation_workspace_archive("conv_1", "../etc", fake_request()))
    )
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    del fake_service.errors["archive"]

    # 无权访问
    fake_service.access = False
    payload = response_payload(
        asyncio.run(cw_routes.download_conversation_workspace_archive("conv_1", "outputs", fake_request()))
    )
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND
    fake_service.access = True

    # 目录为空（service FileNotFoundError）→ NOT_FOUND
    fake_service.errors["archive"] = FileNotFoundError("outputs 目录下没有可打包的文件")
    payload = response_payload(
        asyncio.run(cw_routes.download_conversation_workspace_archive("conv_1", "outputs", fake_request()))
    )
    assert payload["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert "没有可打包的文件" in payload["msg"]

    # 压缩包超限 → BAD_REQUEST + 明确文案
    fake_service.errors["archive"] = cw_routes.WorkspaceArchiveTooLargeError(50 * 1024 * 1024)
    payload = response_payload(
        asyncio.run(cw_routes.download_conversation_workspace_archive("conv_1", "outputs", fake_request()))
    )
    assert payload["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert "已中止打包" in payload["msg"]

    # 未知异常 → 兜底错误
    fake_service.errors["archive"] = RuntimeError("boom")
    payload = response_payload(
        asyncio.run(cw_routes.download_conversation_workspace_archive("conv_1", "outputs", fake_request()))
    )
    assert payload["status"] == "error"
