import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from apps.chat_data_app.route_modules import data_analyze_workspace as workspace_routes


def test_data_analyze_workspace_file_routes(client, auth_headers, monkeypatch, tmp_path):
    async def can_access(_conversation_id, _user_id):
        return True

    monkeypatch.setattr(workspace_routes, "_can_access_data_analyze_conversation", can_access)
    monkeypatch.setattr(workspace_routes, "ensure_data_analyze_workspace", lambda conversation_id: {"root": str(tmp_path)})

    async def fake_list_files(workspace_root, user_id):
        assert workspace_root == str(tmp_path)
        assert user_id == "user_a"
        return [{"path": "/outputs/chart.json"}]

    async def fake_read_file(workspace_root, file_path, user_id):
        assert workspace_root == str(tmp_path)
        assert file_path == "/outputs/chart.json"
        assert user_id == "user_a"
        return {"path": "/outputs/chart.json", "content": "{}"}

    monkeypatch.setattr(workspace_routes, "list_workspace_files_from_sandbox", fake_list_files)
    monkeypatch.setattr(workspace_routes, "read_workspace_file_from_sandbox", fake_read_file)

    list_response = client.get("/api/chatdata/data-analyze/conversations/conv_a/workspace-files", headers=auth_headers)
    read_response = client.get(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/chart.json"},
        headers=auth_headers,
    )

    assert list_response.status_code == 200
    assert list_response.json()["data"]["files"][0]["path"] == "/outputs/chart.json"
    assert read_response.status_code == 200
    assert read_response.json()["data"]["content"] == "{}"


def test_data_analyze_workspace_rejects_inaccessible_conversation(client, auth_headers, monkeypatch):
    async def can_access(_conversation_id, _user_id):
        return False

    monkeypatch.setattr(workspace_routes, "_can_access_data_analyze_conversation", can_access)

    response = client.get("/api/chatdata/data-analyze/conversations/missing/workspace-files", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["code"] == 40404


def test_data_analyze_workspace_file_error_mapping(client, auth_headers, monkeypatch, tmp_path):
    async def can_access(_conversation_id, _user_id):
        return True

    monkeypatch.setattr(workspace_routes, "_can_access_data_analyze_conversation", can_access)
    monkeypatch.setattr(workspace_routes, "ensure_data_analyze_workspace", lambda conversation_id: {"root": str(tmp_path)})

    async def fake_missing_file(**_kwargs):
        raise FileNotFoundError("文件不存在")

    monkeypatch.setattr(workspace_routes, "read_workspace_file_from_sandbox", fake_missing_file)
    missing_response = client.get(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/missing.json"},
        headers=auth_headers,
    )

    async def fake_bad_list(**_kwargs):
        raise ValueError("非法路径")

    monkeypatch.setattr(workspace_routes, "list_workspace_files_from_sandbox", fake_bad_list)
    bad_list_response = client.get("/api/chatdata/data-analyze/conversations/conv_a/workspace-files", headers=auth_headers)

    async def fake_bad_read(**_kwargs):
        raise ValueError("非法路径")

    monkeypatch.setattr(workspace_routes, "read_workspace_file_from_sandbox", fake_bad_read)
    bad_read_response = client.get(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/../bad.json"},
        headers=auth_headers,
    )

    async def fake_crashed_list(**_kwargs):
        raise RuntimeError("sandbox down")

    monkeypatch.setattr(workspace_routes, "list_workspace_files_from_sandbox", fake_crashed_list)
    crashed_list_response = client.get("/api/chatdata/data-analyze/conversations/conv_a/workspace-files", headers=auth_headers)

    async def fake_crashed_read(**_kwargs):
        raise RuntimeError("sandbox down")

    monkeypatch.setattr(workspace_routes, "read_workspace_file_from_sandbox", fake_crashed_read)
    crashed_read_response = client.get(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-files/content",
        params={"path": "/outputs/chart.json"},
        headers=auth_headers,
    )

    assert missing_response.json()["code"] == 40404
    assert bad_list_response.json()["code"] == 40001
    assert bad_read_response.json()["code"] == 40001
    assert crashed_list_response.json()["code"] == 50053
    assert crashed_read_response.json()["code"] == 50054


def test_data_analyze_workspace_import_skill_route(client, auth_headers, monkeypatch, tmp_path):
    workspace_dir = tmp_path / "workspace"
    outputs_dir = tmp_path / "outputs"
    workspace_dir.mkdir()
    outputs_dir.mkdir()
    skill_dir = workspace_dir / "generated-skill"
    skill_dir.mkdir()
    async def can_access(_conversation_id, _user_id):
        return True

    monkeypatch.setattr(workspace_routes, "_can_access_data_analyze_conversation", can_access)
    monkeypatch.setattr(workspace_routes, "ensure_data_analyze_workspace", lambda conversation_id: {"root": str(tmp_path)})

    async def fake_import_user_skill_from_directory_async(path: Path, user_id: str, operator_name: str = ""):
        assert path == skill_dir
        assert user_id == "user_a"
        assert operator_name == "测试用户"
        return {"skill_id": "skill_a", "path": str(path)}

    monkeypatch.setattr(workspace_routes.skill_service, "import_user_skill_from_directory_async", fake_import_user_skill_from_directory_async)

    success_response = client.post(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-skills/import",
        json={"skill_dir_path": "workspace/generated-skill"},
        headers=auth_headers,
    )
    rejected_response = client.post(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-skills/import",
        json={"skill_dir_path": "runtime-skills/generated-skill"},
        headers=auth_headers,
    )

    assert success_response.status_code == 200
    assert success_response.json()["data"]["skill_id"] == "skill_a"
    assert rejected_response.json()["code"] == 40055


def test_data_analyze_workspace_import_error_mapping(client, auth_headers, monkeypatch, tmp_path):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async def can_access(_conversation_id, _user_id):
        return True

    monkeypatch.setattr(workspace_routes, "_can_access_data_analyze_conversation", can_access)
    monkeypatch.setattr(workspace_routes, "ensure_data_analyze_workspace", lambda conversation_id: {"root": str(tmp_path)})

    async def fake_import_value_error(_path: Path, _user_id: str, operator_name: str = ""):
        assert operator_name == "测试用户"
        raise ValueError("技能格式错误")

    monkeypatch.setattr(workspace_routes.skill_service, "import_user_skill_from_directory_async", fake_import_value_error)
    value_error_response = client.post(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-skills/import",
        json={"skill_dir_path": "workspace/generated-skill"},
        headers=auth_headers,
    )

    async def fake_import_runtime_error(_path: Path, _user_id: str, operator_name: str = ""):
        assert operator_name == "测试用户"
        raise RuntimeError("db down")

    monkeypatch.setattr(workspace_routes.skill_service, "import_user_skill_from_directory_async", fake_import_runtime_error)
    runtime_error_response = client.post(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-skills/import",
        json={"skill_dir_path": "workspace/generated-skill"},
        headers=auth_headers,
    )

    async def cannot_access(_conversation_id, _user_id):
        return False

    monkeypatch.setattr(workspace_routes, "_can_access_data_analyze_conversation", cannot_access)
    inaccessible_response = client.post(
        "/api/chatdata/data-analyze/conversations/conv_a/workspace-skills/import",
        json={"skill_dir_path": "workspace/generated-skill"},
        headers=auth_headers,
    )

    assert value_error_response.json()["code"] == 40055
    assert runtime_error_response.json()["code"] == 50055
    assert inaccessible_response.json()["code"] == 40404


def test_can_access_data_analyze_conversation_queries_user_thread(monkeypatch):
    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSession:
        def __init__(self, value):
            self.value = value
            self.executed = False

        async def execute(self, _stmt):
            self.executed = True
            return FakeResult(self.value)

    @asynccontextmanager
    async def fake_get_session_hit():
        yield FakeSession(1)

    @asynccontextmanager
    async def fake_get_session_miss():
        yield FakeSession(None)

    assert asyncio.run(workspace_routes._can_access_data_analyze_conversation("", "u1")) is False
    assert asyncio.run(workspace_routes._can_access_data_analyze_conversation("conv_a", "")) is False

    monkeypatch.setattr(workspace_routes, "get_session", fake_get_session_hit)
    assert asyncio.run(workspace_routes._can_access_data_analyze_conversation("conv_a", "u1")) is True

    monkeypatch.setattr(workspace_routes, "get_session", fake_get_session_miss)
    assert asyncio.run(workspace_routes._can_access_data_analyze_conversation("conv_a", "u1")) is False
