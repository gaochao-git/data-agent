"""query_history 路由分支覆盖测试——补齐鉴权异常、参数错误与兜底失败路径。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat_data_app.routers import query_history as qh
from apps.chat_data_app.services.sql_run.task_service import QueryTaskNotFoundError
from auth import AuthenticationError


def _payload(resp):
    """business_response 返回 JSONResponse；兼容直接返回 dict 的情况。"""
    if hasattr(resp, "body"):
        return json.loads(resp.body)
    return resp


@pytest.fixture(autouse=True)
def _current_user(monkeypatch):
    monkeypatch.setattr(qh, "_get_current_user", lambda request: SimpleNamespace(user_id="u1", username="tester"))


class FakeHistoryService:
    def __init__(self, exc: BaseException | None = None, data=None):
        self.exc = exc
        self.data = data if data is not None else {"items": []}
        self.calls: list[dict] = []

    async def list_user_history(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.data

    async def list_admin_history(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.data

    async def get_history_result(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.data


class FakeTaskService:
    def __init__(self, exc: BaseException | None = None, result=None):
        self.exc = exc
        self.result = result if result is not None else {"cancelled": 1}
        self.calls: list[dict] = []

    async def cancel_running_query_history(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.result

    async def cancel_query_execution_for_user(self, execution_id, **kwargs):
        self.calls.append({"execution_id": execution_id, **kwargs})
        if self.exc:
            raise self.exc
        return SimpleNamespace(data=self.result, message="已提交停止")


def _patch_history(monkeypatch, service):
    monkeypatch.setattr(qh, "get_query_history_service", lambda: service)
    return service


def _patch_task(monkeypatch, service):
    monkeypatch.setattr(qh, "get_query_task_service", lambda: service)
    return service


async def _noop_fetcher(**kwargs):
    return None


@pytest.mark.asyncio
async def test_get_query_history_success(monkeypatch):
    service = _patch_history(monkeypatch, FakeHistoryService(data={"items": [1]}))
    resp = await qh.get_query_history(request=None, datasource_id="ds1")
    assert _payload(resp)["status"] == "success"
    assert service.calls[0]["datasource_id"] == "ds1"
    assert service.calls[0]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_get_query_history_bad_request(monkeypatch):
    _patch_history(monkeypatch, FakeHistoryService(exc=ValueError("page_size 超限")))
    resp = await qh.get_query_history(request=None, datasource_id="ds1")
    payload = _payload(resp)
    assert payload["status"] == "error"
    assert "page_size 超限" in payload["msg"]


@pytest.mark.asyncio
async def test_get_query_history_rejects_injection_and_reraises_http(monkeypatch):
    """HTTPException 不吞，原样向上抛（鉴权/权限类）。"""
    _patch_history(monkeypatch, FakeHistoryService(exc=HTTPException(status_code=403, detail="forbidden")))
    with pytest.raises(HTTPException) as exc:
        await qh.get_query_history(request=None, datasource_id="ds1")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_query_history_unexpected_error(monkeypatch):
    _patch_history(monkeypatch, FakeHistoryService(exc=RuntimeError("db down")))
    resp = await qh.get_query_history(request=None, datasource_id="ds1")
    payload = _payload(resp)
    assert payload["status"] == "error"
    assert "请稍后重试" in payload["msg"]


@pytest.mark.asyncio
async def test_get_admin_query_history_success(monkeypatch):
    service = _patch_history(monkeypatch, FakeHistoryService(data={"items": []}))
    resp = await qh.get_admin_query_history(request=None, datasource_id="ds1", keyword="abc")
    assert _payload(resp)["status"] == "success"
    assert service.calls[0]["keyword"] == "abc"


@pytest.mark.asyncio
async def test_get_admin_query_history_bad_request(monkeypatch):
    _patch_history(monkeypatch, FakeHistoryService(exc=ValueError("时间范围非法")))
    resp = await qh.get_admin_query_history(request=None)
    payload = _payload(resp)
    assert payload["status"] == "error"
    assert "时间范围非法" in payload["msg"]


@pytest.mark.asyncio
async def test_get_admin_query_history_unexpected_error(monkeypatch):
    _patch_history(monkeypatch, FakeHistoryService(exc=RuntimeError("boom")))
    resp = await qh.get_admin_query_history(request=None)
    payload = _payload(resp)
    assert payload["status"] == "error"
    assert "全局查询历史" in payload["msg"]


@pytest.mark.asyncio
async def test_cancel_my_running_success(monkeypatch):
    service = _patch_task(monkeypatch, FakeTaskService())
    resp = await qh.cancel_my_running_query_history(request=None, datasource_id="ds1")
    assert _payload(resp)["status"] == "success"
    assert service.calls[0]["user_id"] == "u1"
    assert service.calls[0]["operator_name"] == "tester"


@pytest.mark.asyncio
async def test_cancel_my_running_auth_error_becomes_http(monkeypatch):
    _patch_task(monkeypatch, FakeTaskService(exc=AuthenticationError("登录失效")))
    with pytest.raises(HTTPException) as exc:
        await qh.cancel_my_running_query_history(request=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_cancel_my_running_unexpected_error(monkeypatch):
    _patch_task(monkeypatch, FakeTaskService(exc=RuntimeError("boom")))
    resp = await qh.cancel_my_running_query_history(request=None)
    assert _payload(resp)["status"] == "error"


@pytest.mark.asyncio
async def test_cancel_execution_success(monkeypatch):
    service = _patch_task(monkeypatch, FakeTaskService(result={"execution_id": "e1"}))
    resp = await qh.cancel_query_history_execution(execution_id="e1", request=None)
    assert _payload(resp)["status"] == "success"
    assert service.calls[0]["execution_id"] == "e1"


@pytest.mark.asyncio
async def test_cancel_execution_not_found(monkeypatch):
    _patch_task(monkeypatch, FakeTaskService(exc=QueryTaskNotFoundError("执行记录不存在")))
    resp = await qh.cancel_query_history_execution(execution_id="e404", request=None)
    payload = _payload(resp)
    assert payload["status"] == "error"
    assert "执行记录不存在" in payload["msg"]


@pytest.mark.asyncio
async def test_cancel_execution_auth_error_becomes_http(monkeypatch):
    _patch_task(monkeypatch, FakeTaskService(exc=AuthenticationError("登录失效", status_code=403)))
    with pytest.raises(HTTPException) as exc:
        await qh.cancel_query_history_execution(execution_id="e1", request=None)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_cancel_execution_unexpected_error(monkeypatch):
    _patch_task(monkeypatch, FakeTaskService(exc=RuntimeError("boom")))
    resp = await qh.cancel_query_history_execution(execution_id="e1", request=None)
    assert _payload(resp)["status"] == "error"


@pytest.mark.asyncio
async def test_cancel_admin_running_success(monkeypatch):
    service = _patch_task(monkeypatch, FakeTaskService())
    resp = await qh.cancel_admin_running_query_history(request=None)
    assert _payload(resp)["status"] == "success"
    assert "user_id" not in service.calls[0]


@pytest.mark.asyncio
async def test_cancel_admin_running_auth_error_becomes_http(monkeypatch):
    _patch_task(monkeypatch, FakeTaskService(exc=AuthenticationError("登录失效")))
    with pytest.raises(HTTPException) as exc:
        await qh.cancel_admin_running_query_history(request=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_cancel_admin_running_unexpected_error(monkeypatch):
    _patch_task(monkeypatch, FakeTaskService(exc=RuntimeError("boom")))
    resp = await qh.cancel_admin_running_query_history(request=None)
    assert _payload(resp)["status"] == "error"


@pytest.mark.asyncio
async def test_get_query_history_result_not_found(monkeypatch):
    _patch_history(monkeypatch, FakeHistoryService(data={}))
    monkeypatch.setattr(qh, "get_query_history_remote_result_fetcher", lambda: _noop_fetcher)
    resp = await qh.get_query_history_result(execution_id="e404", request=None)
    payload = _payload(resp)
    assert payload["status"] == "error"
    assert "不存在" in payload["msg"]


@pytest.mark.asyncio
async def test_get_query_history_result_reraises_http(monkeypatch):
    _patch_history(monkeypatch, FakeHistoryService(exc=HTTPException(status_code=404, detail="nope")))
    monkeypatch.setattr(qh, "get_query_history_remote_result_fetcher", lambda: _noop_fetcher)
    with pytest.raises(HTTPException):
        await qh.get_query_history_result(execution_id="e1", request=None)


@pytest.mark.asyncio
async def test_get_query_history_result_unexpected_error(monkeypatch):
    _patch_history(monkeypatch, FakeHistoryService(exc=RuntimeError("boom")))
    monkeypatch.setattr(qh, "get_query_history_remote_result_fetcher", lambda: _noop_fetcher)
    resp = await qh.get_query_history_result(execution_id="e1", request=None)
    assert _payload(resp)["status"] == "error"
