"""会话审批许可管理 API：读取/撤销/全部重置与运行态保护。"""

from __future__ import annotations

import pytest

from apps.chat_data_app.routers import conversations as conversation_router


def _patch_conversation(monkeypatch, *, existing_agent="chat_data", pending=None, active=False):
    monkeypatch.setattr(conversation_router, "_get_current_user_identity", lambda _req: ("user-1", "用户一"))

    async def get_existing(*_args):
        return existing_agent

    async def get_pending(*_args):
        return pending

    async def has_active(*_args):
        return active

    monkeypatch.setattr(conversation_router, "_get_existing_agent_for_completion", get_existing)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "get_pending_question", get_pending)
    monkeypatch.setattr(conversation_router.agent_inbox_service, "has_active_run", has_active)


def test_get_approval_grants(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch)

    async def read_grants(_conversation_id):
        return ["tool:file_delete"]

    monkeypatch.setattr(conversation_router, "read_conversation_approval_grants", read_grants)

    response = client.get("/api/chatdata/conversations/conv-1/approval-grants", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"] == {"grants": ["tool:file_delete"]}


def test_revoke_approval_grants(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch)
    revoked: list[list[str]] = []

    async def revoke(_conversation_id, tools):
        revoked.append(tools)
        return []

    monkeypatch.setattr(conversation_router, "revoke_conversation_approval_grants", revoke)

    response = client.post(
        "/api/chatdata/conversations/conv-1/approval-grants/revoke",
        json={"tools": ["file_delete"]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert revoked == [["file_delete"]]


def test_reset_approval_grants(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch)
    called: list[str] = []

    async def reset(_conversation_id):
        called.append(_conversation_id)

    monkeypatch.setattr(conversation_router, "reset_conversation_approval_grants", reset)

    response = client.post("/api/chatdata/conversations/conv-1/approval-grants/reset", headers=auth_headers)

    assert response.status_code == 200
    assert called == ["conv-1"]


@pytest.mark.parametrize("pending,active", [({"question_id": "q"}, False), (None, True)])
def test_approval_grant_mutation_rejected_when_busy(client, auth_headers, monkeypatch, pending, active):
    _patch_conversation(monkeypatch, pending=pending, active=active)
    response = client.post(
        "/api/chatdata/conversations/conv-1/approval-grants/revoke",
        json={"tools": ["file_delete"]},
        headers=auth_headers,
    )
    assert response.status_code == 409


def test_approval_grants_unknown_conversation(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch, existing_agent=None)
    response = client.get("/api/chatdata/conversations/conv-x/approval-grants", headers=auth_headers)
    assert response.status_code == 404


def _raise_auth(_request):
    from auth import AuthenticationError

    raise AuthenticationError("未认证")


def test_approval_grant_routes_require_auth(client, auth_headers, monkeypatch):
    monkeypatch.setattr(conversation_router, "_get_current_user_identity", _raise_auth)
    for method, path in [
        ("get", "/api/chatdata/conversations/conv-1/approval-grants"),
        ("post", "/api/chatdata/conversations/conv-1/approval-grants/revoke"),
        ("post", "/api/chatdata/conversations/conv-1/approval-grants/reset"),
    ]:
        response = getattr(client, method)(
            path, headers=auth_headers, **({"json": {"tools": []}} if method == "post" else {})
        )
        assert response.status_code == 401


def test_revoke_unknown_conversation(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch, existing_agent=None)
    response = client.post(
        "/api/chatdata/conversations/conv-x/approval-grants/revoke",
        json={"tools": ["file_delete"]},
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.parametrize("pending,active", [({"question_id": "q"}, False), (None, True)])
def test_reset_rejected_when_busy(client, auth_headers, monkeypatch, pending, active):
    _patch_conversation(monkeypatch, pending=pending, active=active)
    response = client.post("/api/chatdata/conversations/conv-1/approval-grants/reset", headers=auth_headers)
    assert response.status_code == 409


def test_reset_unknown_conversation(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch, existing_agent=None)
    response = client.post("/api/chatdata/conversations/conv-x/approval-grants/reset", headers=auth_headers)
    assert response.status_code == 404


def test_read_failure_returns_500(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch)

    async def fail(_conversation_id):
        raise RuntimeError("checkpoint broken")

    monkeypatch.setattr(conversation_router, "read_conversation_approval_grants", fail)
    response = client.get("/api/chatdata/conversations/conv-1/approval-grants", headers=auth_headers)
    assert response.status_code == 500


def test_revoke_failure_returns_500(client, auth_headers, monkeypatch):
    _patch_conversation(monkeypatch)

    async def fail(_conversation_id, _tools):
        raise RuntimeError("checkpoint broken")

    monkeypatch.setattr(conversation_router, "revoke_conversation_approval_grants", fail)
    response = client.post(
        "/api/chatdata/conversations/conv-1/approval-grants/revoke",
        json={"tools": ["file_delete"]},
        headers=auth_headers,
    )
    assert response.status_code == 500
