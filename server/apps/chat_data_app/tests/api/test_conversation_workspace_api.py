import asyncio
from pathlib import Path
from types import SimpleNamespace

from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import conversation_workspace as workspace_routes


def _install_accessible_workspace(monkeypatch):
    monkeypatch.setattr(
        workspace_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id="user_a", username="测试用户"),
    )

    async def can_access(conversation_id, user_id):
        assert (conversation_id, user_id) == ("conv_a", "user_a")
        return True

    monkeypatch.setattr(workspace_routes, "can_access_conversation_workspace", can_access)


def test_conversation_workspace_file_routes_read_shared_persisted_workspace(client, monkeypatch):
    _install_accessible_workspace(monkeypatch)

    async def list_files(conversation_id, *, user_id):
        assert (conversation_id, user_id) == ("conv_a", "user_a")
        return [
            {"path": "/workspace/data_context.json", "relative_path": "workspace/data_context.json"},
            {"path": "/outputs/chart.json", "relative_path": "outputs/chart.json"},
        ]

    async def read_file(conversation_id, *, user_id, path):
        assert (conversation_id, user_id, path) == ("conv_a", "user_a", "/outputs/chart.json")
        return {"path": "/outputs/chart.json", "content": "{}"}

    monkeypatch.setattr(workspace_routes, "list_workspace_files_for_conversation", list_files)
    monkeypatch.setattr(workspace_routes, "read_workspace_file_for_conversation", read_file)

    list_response = client.get("/api/chatdata/conversations/conv_a/workspace-files")
    read_response = client.get(
        "/api/chatdata/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/chart.json"},
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"]["files"] == [{"path": "/outputs/chart.json", "relative_path": "outputs/chart.json"}]
    assert read_response.status_code == 200
    assert read_response.json()["data"]["content"] == "{}"


def test_conversation_workspace_hides_internal_context_file(client, monkeypatch):
    _install_accessible_workspace(monkeypatch)

    async def read_file(**_kwargs):
        raise AssertionError("internal context file must not be read")

    monkeypatch.setattr(workspace_routes, "read_workspace_file_for_conversation", read_file)

    response = client.get(
        "/api/chatdata/conversations/conv_a/workspace-files/content",
        params={"path": "/mnt/user-data/workspace/data_context.json"},
    )

    assert response.status_code == 200
    assert response.json()["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_conversation_workspace_rejects_inaccessible_conversation(client, monkeypatch):
    monkeypatch.setattr(
        workspace_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id="user_a", username="测试用户"),
    )

    async def can_access(_conversation_id, _user_id):
        return False

    monkeypatch.setattr(workspace_routes, "can_access_conversation_workspace", can_access)

    response = client.get("/api/chatdata/conversations/missing/workspace-files")

    assert response.status_code == 200
    assert response.json()["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_conversation_query_result_route_scopes_result_to_owned_conversation(client, monkeypatch):
    _install_accessible_workspace(monkeypatch)

    async def read_result(conversation_id, *, user_id, result_id):
        assert (conversation_id, user_id, result_id) == ("conv_a", "user_a", "result_a")
        return {"columns": ["amount"], "data": [{"amount": 1}]}

    monkeypatch.setattr(workspace_routes, "read_conversation_query_result", read_result)

    response = client.get("/api/chatdata/conversations/conv_a/query-results/result_a")

    assert response.status_code == 200
    assert response.json()["data"]["data"] == [{"amount": 1}]


def test_conversation_query_result_route_handles_missing_invalid_and_inaccessible(client, monkeypatch):
    _install_accessible_workspace(monkeypatch)

    async def missing_result(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workspace_routes, "read_conversation_query_result", missing_result)
    missing = client.get("/api/chatdata/conversations/conv_a/query-results/missing")
    assert missing.json()["code"] == ErrorCode.CHATDATA_NOT_FOUND

    async def invalid_result(*_args, **_kwargs):
        raise ValueError("result_id 非法")

    monkeypatch.setattr(workspace_routes, "read_conversation_query_result", invalid_result)
    invalid = client.get("/api/chatdata/conversations/conv_a/query-results/invalid")
    assert invalid.json()["code"] == ErrorCode.CHATDATA_BAD_REQUEST

    async def cannot_access(_conversation_id, _user_id):
        return False

    monkeypatch.setattr(workspace_routes, "can_access_conversation_workspace", cannot_access)
    inaccessible = client.get("/api/chatdata/conversations/other/query-results/result_a")
    assert inaccessible.json()["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_conversation_workspace_file_error_mapping(client, monkeypatch):
    _install_accessible_workspace(monkeypatch)

    async def missing_file(*_args, **_kwargs):
        raise FileNotFoundError("文件不存在")

    async def bad_list(*_args, **_kwargs):
        raise ValueError("非法路径")

    async def bad_read(*_args, **_kwargs):
        raise ValueError("非法路径")

    async def crashed(*_args, **_kwargs):
        raise RuntimeError("storage down")

    monkeypatch.setattr(workspace_routes, "read_workspace_file_for_conversation", missing_file)
    missing_response = client.get(
        "/api/chatdata/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/missing.json"},
    )
    monkeypatch.setattr(workspace_routes, "list_workspace_files_for_conversation", bad_list)
    bad_list_response = client.get("/api/chatdata/conversations/conv_a/workspace-files")
    monkeypatch.setattr(workspace_routes, "read_workspace_file_for_conversation", bad_read)
    bad_read_response = client.get(
        "/api/chatdata/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/../bad.json"},
    )
    monkeypatch.setattr(workspace_routes, "list_workspace_files_for_conversation", crashed)
    crashed_list_response = client.get("/api/chatdata/conversations/conv_a/workspace-files")
    monkeypatch.setattr(workspace_routes, "read_workspace_file_for_conversation", crashed)
    crashed_read_response = client.get(
        "/api/chatdata/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/chart.json"},
    )

    assert missing_response.json()["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert bad_list_response.json()["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert bad_read_response.json()["code"] == ErrorCode.CHATDATA_BAD_REQUEST
    assert crashed_list_response.json()["code"] == ErrorCode.CHATDATA_WORKSPACE_FILE_LIST_FAILED
    assert crashed_read_response.json()["code"] == ErrorCode.CHATDATA_WORKSPACE_FILE_READ_FAILED


def test_conversation_workspace_imports_skill_from_shared_persisted_directory(client, monkeypatch, tmp_path):
    _install_accessible_workspace(monkeypatch)
    skill_dir = tmp_path / "workspace" / "generated-skill"
    skill_dir.mkdir(parents=True)
    (tmp_path / "outputs").mkdir()
    monkeypatch.setattr(
        workspace_routes,
        "ensure_conversation_workspace",
        lambda _conversation_id, _user_id: {"root": str(tmp_path)},
    )

    async def import_skill(path: Path, user_id: str, operator_name: str = ""):
        assert path == skill_dir
        assert user_id == "user_a"
        assert operator_name == "测试用户"
        return {"skill_id": "skill_a"}

    monkeypatch.setattr(workspace_routes.skill_service, "import_user_skill_from_directory_async", import_skill)

    success_response = client.post(
        "/api/chatdata/conversations/conv_a/workspace-skills/import",
        json={"skill_dir_path": "workspace/generated-skill"},
    )
    rejected_response = client.post(
        "/api/chatdata/conversations/conv_a/workspace-skills/import",
        json={"skill_dir_path": "/mnt/skills/system/dashboard-generation"},
    )

    assert success_response.status_code == 200
    assert success_response.json()["data"]["skill_id"] == "skill_a"
    assert rejected_response.json()["code"] == ErrorCode.CHATDATA_WORKSPACE_SKILL_IMPORT_INVALID


def test_can_access_conversation_workspace_queries_user_thread(monkeypatch):
    assert asyncio.run(workspace_routes.can_access_conversation_workspace("", "u1")) is False
    assert asyncio.run(workspace_routes.can_access_conversation_workspace("conv_a", "")) is False

    async def exists_hit(**kwargs):
        assert kwargs == {"conversation_id": "conv_a", "user_id": "u1", "agent_id": "chat_data"}
        return True

    monkeypatch.setattr(
        workspace_routes.can_access_conversation_workspace.__globals__["conversation_dao"],
        "exists_user_agent_thread",
        exists_hit,
    )

    assert asyncio.run(workspace_routes.can_access_conversation_workspace("conv_a", "u1")) is True
