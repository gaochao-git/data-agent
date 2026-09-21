"""utils/response.py 全局 HTTPException 5xx 告警链路测试。

背景：未捕获异常由 APIAccessLogMiddleware 兜底告警（api_logging.py:220），
但显式 raise HTTPException(5xx) 走正常响应、不经过中间件异常分支——
这是 emit_api_call_alarm 的互补覆盖面（2026-09-16 告警链路审计发现的缺口）。
"""

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from utils import response as response_module


def _request(path: str, method: str = "GET", user_id: bytes | None = b"10001") -> Request:
    headers = [(b"x-user-id", user_id)] if user_id is not None else []
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": headers,
            "client": ("10.1.2.3", 8000),
        }
    )


@pytest.mark.asyncio
async def test_explicit_5xx_http_exception_emits_api_call_alarm(monkeypatch):
    """显式 raise 的 5xx HTTPException 必须告警，ext1 带请求方定位信息。"""
    alarms = []
    monkeypatch.setattr(response_module, "emit_api_call_alarm", lambda *a, **kw: alarms.append((a, kw)))

    request = _request("/api/chatdata/agent/runs")
    await response_module.chatdata_http_exception_handler(request, StarletteHTTPException(500, "boom"))

    assert len(alarms) == 1
    args, kwargs = alarms[0]
    assert args[0] == "/api/chatdata/agent/runs"
    assert kwargs["method"] == "GET"
    assert kwargs["ext1"]["user_id"] == "10001"
    assert kwargs["ext1"]["client"] == "10.1.2.3"


@pytest.mark.asyncio
async def test_5xx_alarm_covers_non_chatdata_paths_and_missing_user(monkeypatch):
    """告警不限于 chatdata 路径；无 X-User-Id 头时留空而不是缺键。"""
    alarms = []
    monkeypatch.setattr(response_module, "emit_api_call_alarm", lambda *a, **kw: alarms.append((a, kw)))

    request = _request("/healthz", method="POST", user_id=None)
    resp = await response_module.chatdata_http_exception_handler(request, StarletteHTTPException(503, "unavailable"))

    assert len(alarms) == 1
    assert alarms[0][0][0] == "/healthz"
    assert alarms[0][1]["method"] == "POST"
    assert alarms[0][1]["ext1"]["user_id"] == ""
    # 非 chatdata 路径仍走 FastAPI 默认响应格式，不受告警影响
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_4xx_http_exception_does_not_alarm(monkeypatch):
    """4xx 属客户端/业务错误，不触发平台接口告警（否则告警噪音爆炸）。"""
    alarms = []
    monkeypatch.setattr(response_module, "emit_api_call_alarm", lambda *a, **kw: alarms.append((a, kw)))

    request = _request("/api/chatdata/datasources/1")
    await response_module.chatdata_http_exception_handler(request, StarletteHTTPException(404, "not found"))
    await response_module.chatdata_http_exception_handler(request, StarletteHTTPException(403, "forbidden"))

    assert alarms == []


@pytest.mark.asyncio
async def test_validation_errors_do_not_alarm(monkeypatch):
    """422 参数校验失败不告警。"""
    alarms = []
    monkeypatch.setattr(response_module, "emit_api_call_alarm", lambda *a, **kw: alarms.append((a, kw)))

    request = _request("/api/chatdata/datasources")
    # 构造一个真实的 RequestValidationError
    from pydantic import BaseModel, ValidationError

    class _Model(BaseModel):
        name: str

    try:
        _Model.model_validate({"name": 123})
    except ValidationError as ve:
        exc = ve
    from fastapi.exceptions import RequestValidationError

    await response_module.chatdata_validation_exception_handler(request, RequestValidationError(exc.errors()))

    assert alarms == []
