"""服务自身 5xx/未捕获异常不写 alarm 的回归测试。

alarm 只留给外部依赖失败等需要值班介入的场景；5xx 由 access/app 日志与监控告警覆盖。
"""

from types import SimpleNamespace

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

import utils.alarm as alarm_module
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


def _capture_alarms(monkeypatch):
    calls = []
    monkeypatch.setattr(
        alarm_module,
        "get_alarm_logger",
        lambda: SimpleNamespace(error=lambda *args, **kwargs: calls.append((args, kwargs))),
    )
    return calls


@pytest.mark.asyncio
async def test_explicit_5xx_does_not_emit_alarm(monkeypatch):
    calls = _capture_alarms(monkeypatch)
    request = _request("/api/chatdata/agent/runs")
    resp = await response_module.chatdata_http_exception_handler(request, StarletteHTTPException(500, "boom"))

    assert resp.status_code == 500
    assert calls == []


@pytest.mark.asyncio
async def test_5xx_non_chatdata_path_does_not_emit_alarm(monkeypatch):
    calls = _capture_alarms(monkeypatch)
    request = _request("/healthz", method="POST", user_id=None)
    resp = await response_module.chatdata_http_exception_handler(request, StarletteHTTPException(503, "unavailable"))

    assert resp.status_code == 503
    assert calls == []


@pytest.mark.asyncio
async def test_4xx_and_validation_do_not_emit_alarm(monkeypatch):
    calls = _capture_alarms(monkeypatch)
    request = _request("/api/chatdata/datasources/1")
    await response_module.chatdata_http_exception_handler(request, StarletteHTTPException(404, "not found"))

    from fastapi.exceptions import RequestValidationError
    from pydantic import BaseModel, ValidationError

    class _Model(BaseModel):
        name: str

    try:
        _Model.model_validate({"name": 123})
        validation_exc = None
    except ValidationError as ve:
        validation_exc = ve
    assert validation_exc is not None
    await response_module.chatdata_validation_exception_handler(
        request, RequestValidationError(validation_exc.errors())
    )

    assert calls == []
