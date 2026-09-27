"""routers/config 运行时配置路由测试——成功与兜底失败两条路径。"""

from __future__ import annotations

import json

import pytest

from apps.chat_data_app.routers import config as config_router


def _payload(resp):
    if hasattr(resp, "body"):
        return json.loads(resp.body)
    return resp


@pytest.mark.asyncio
async def test_get_chat_data_config_success(monkeypatch):
    async def fake_runtime_config():
        return {"model": "qwen"}

    monkeypatch.setattr(config_router, "get_chat_data_runtime_config", fake_runtime_config)
    payload = _payload(await config_router.get_chat_data_config())
    assert payload["status"] == "success"
    assert payload["data"] == {"model": "qwen"}


@pytest.mark.asyncio
async def test_get_chat_data_config_failure_is_contained(monkeypatch):
    def _boom():
        raise RuntimeError("config source down")

    monkeypatch.setattr(config_router, "get_chat_data_runtime_config", _boom)
    payload = _payload(await config_router.get_chat_data_config())
    assert payload["status"] == "error"
    assert "运行时配置" in payload["msg"]
