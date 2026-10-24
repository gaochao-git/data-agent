"""setting_admin 路由兜底：service 抛非业务异常时返回脱敏错误码。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat_data_app.routers import setting_admin
from utils.error_codes import ErrorCode


def _user(monkeypatch):
    monkeypatch.setattr(
        setting_admin,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id=1, username="tester"),
    )


def _body(response):
    return json.loads(response.body)


def test_http_exceptions_are_re_raised(monkeypatch):
    _user(monkeypatch)

    async def failing(**_kwargs):
        raise HTTPException(status_code=403, detail="forbidden")

    for func_name in ("list_settings", "upsert_setting", "reset_all_settings", "delete_setting"):
        monkeypatch.setattr(setting_admin, func_name, failing)

    with pytest.raises(HTTPException):
        asyncio.run(setting_admin.list_chat_data_settings(SimpleNamespace()))
    with pytest.raises(HTTPException):
        asyncio.run(
            setting_admin.upsert_chat_data_setting(
                "CHAT_DATA_MAX_UPLOAD_SIZE_MB",
                SimpleNamespace(setting_value="20"),
                SimpleNamespace(),
            )
        )
    with pytest.raises(HTTPException):
        asyncio.run(setting_admin.reset_all_chat_data_settings(SimpleNamespace()))
    with pytest.raises(HTTPException):
        asyncio.run(setting_admin.reset_chat_data_setting("CHAT_DATA_MAX_UPLOAD_SIZE_MB", SimpleNamespace()))


def test_list_settings_unexpected_error_is_masked(monkeypatch):
    async def failing(**_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(setting_admin, "list_settings", failing)
    response = asyncio.run(setting_admin.list_chat_data_settings(SimpleNamespace()))
    body = _body(response)
    assert body["status"] == "error"
    assert body["code"] == ErrorCode.CHATDATA_SETTING_LIST_FAILED
    assert "db down" not in body["msg"]


def test_upsert_setting_unexpected_error_is_masked(monkeypatch):
    _user(monkeypatch)

    async def failing(**_kwargs):
        raise RuntimeError("write failed")

    monkeypatch.setattr(setting_admin, "upsert_setting", failing)
    response = asyncio.run(
        setting_admin.upsert_chat_data_setting(
            "CHAT_DATA_MAX_UPLOAD_SIZE_MB",
            SimpleNamespace(setting_value="20"),
            SimpleNamespace(),
        )
    )
    body = _body(response)
    assert body["code"] == ErrorCode.CHATDATA_SETTING_SAVE_FAILED
    assert "write failed" not in body["msg"]


def test_reset_all_settings_unexpected_error_is_masked(monkeypatch):
    _user(monkeypatch)

    async def failing(**_kwargs):
        raise RuntimeError("reset failed")

    monkeypatch.setattr(setting_admin, "reset_all_settings", failing)
    response = asyncio.run(setting_admin.reset_all_chat_data_settings(SimpleNamespace()))
    body = _body(response)
    assert body["code"] == ErrorCode.CHATDATA_SETTING_DELETE_FAILED
    assert "reset failed" not in body["msg"]


def test_reset_single_setting_unexpected_error_is_masked(monkeypatch):
    _user(monkeypatch)

    async def failing(**_kwargs):
        raise RuntimeError("delete failed")

    monkeypatch.setattr(setting_admin, "delete_setting", failing)
    response = asyncio.run(setting_admin.reset_chat_data_setting("CHAT_DATA_MAX_UPLOAD_SIZE_MB", SimpleNamespace()))
    body = _body(response)
    assert body["code"] == ErrorCode.CHATDATA_SETTING_DELETE_FAILED
    assert "delete failed" not in body["msg"]
