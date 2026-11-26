"""会话模型选择口径：运行中当前 Run、历史最近使用、缺失回退默认。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.run import conversation_event_service as service_module
from apps.chat_data_app.agent.run.conversation_event_service import ConversationEventService


def _model_registry(monkeypatch):
    monkeypatch.setattr(
        service_module,
        "get_chat_data_model_config",
        lambda model: {"id": "registry-id"} if model == "request-model" else (_ for _ in ()).throw(ValueError(model)),
    )
    monkeypatch.setattr(service_module, "get_default_chat_data_model", lambda: "default-id")


def test_resolve_registered_model_id_maps_request_model(monkeypatch):
    _model_registry(monkeypatch)

    assert service_module._resolve_registered_model_id("request-model") == "registry-id"
    assert service_module._resolve_registered_model_id("") == "default-id"


def test_active_run_payload_returns_registry_model(monkeypatch):
    _model_registry(monkeypatch)
    run = SimpleNamespace(
        run_id="run-1",
        record_status="active",
        record_content=json.dumps({"runtime_context": {"model_id": "request-model"}}),
    )

    payload = ConversationEventService._active_run_payload(run)

    assert payload["model_id"] == "registry-id"


@pytest.mark.asyncio
async def test_latest_model_falls_back_to_last_assistant_reply(monkeypatch):
    _model_registry(monkeypatch)

    class Dao:
        @asynccontextmanager
        async def session_context(self):
            yield None

        async def get_latest_assistant_payload_in_session(self, _session, conversation_id, user_id):
            assert conversation_id == "conv-1"
            assert user_id == "user-1"
            return json.dumps({"generation_stats": {"model": "request-model"}})

    class InboxDao:
        async def get_active_run_snapshot(self, _session, conversation_id, user_id):
            return None

    service = ConversationEventService(Dao(), InboxDao())

    assert await service.get_latest_model_id(conversation_id="conv-1", user_id="user-1") == "registry-id"


@pytest.mark.asyncio
async def test_latest_model_falls_back_to_default_when_history_has_no_model(monkeypatch):
    _model_registry(monkeypatch)

    class Dao:
        @asynccontextmanager
        async def session_context(self):
            yield None

        async def get_latest_assistant_payload_in_session(self, _session, conversation_id, user_id):
            return json.dumps({"content": "旧会话没有 generation_stats"})

    class InboxDao:
        async def get_active_run_snapshot(self, _session, conversation_id, user_id):
            return None

    service = ConversationEventService(Dao(), InboxDao())

    assert await service.get_latest_model_id(conversation_id="conv-1", user_id="user-1") == "default-id"
