import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from utils.error_codes import ErrorCode
from apps.chat_data_app.routers import skills as skills_routes
from apps.chat_data_app.schema import SkillCreateRequest, SkillFileUpsertRequest, SkillUpdateRequest


class FakeSkillService:
    def __init__(self):
        self.created_payload = None
        self.updated_payload = None
        self.deleted = None
        self.favorite_state = None

    async def list_skills_async(self, **kwargs):
        return [{"skill_id": "skill_a", "skill_name": "analysis", "scope": kwargs.get("scope"), "agent": kwargs.get("agent", "")}]

    async def get_skill_async(self, skill_name, **kwargs):
        if skill_name != "analysis":
            return None
        return {"skill_id": "skill_a", "skill_name": skill_name, "skill_title": "分析技能"}

    async def create_user_skill_async(self, payload, user_id, operator_name=""):
        self.created_payload = {"payload": payload, "user_id": user_id}
        return {"skill_id": "skill_new", **payload, "user_id": user_id}

    async def update_user_skill_draft_async(self, skill_id, payload, user_id, operator_name=""):
        self.updated_payload = {"skill_id": skill_id, "payload": payload, "user_id": user_id}
        return {"skill_id": skill_id, **payload, "user_id": user_id}

    async def delete_user_skill_async(self, skill_id, user_id, operator_name=""):
        self.deleted = {"skill_id": skill_id, "user_id": user_id}

    async def copy_user_skill_async(self, skill_id, user_id, operator_name=""):
        return {"skill_id": f"{skill_id}_copy", "source_skill_id": skill_id, "user_id": user_id}

    async def favorite_skill_async(self, skill_id, user_id, favorite, operator_name=""):
        self.favorite_state = {"skill_id": skill_id, "user_id": user_id, "favorite": favorite}
        return self.favorite_state

    async def publish_user_skill_async(self, skill_id, user_id, operator_name=""):
        return {"skill_id": skill_id, "user_id": user_id, "status": "published"}

    async def list_skill_versions_async(self, skill_id, user_id):
        return [{"version_no": 1, "skill_id": skill_id, "user_id": user_id}]

    async def get_skill_version_detail_async(self, skill_id, version_no, user_id):
        return {"skill_id": skill_id, "version_no": version_no, "user_id": user_id, "content": "# Skill"}

    async def list_skill_version_files_async(self, skill_id, version_no, user_id):
        return {"skill_id": skill_id, "version_no": version_no, "files": [{"file_path": "references/a.md"}], "user_id": user_id}

    async def read_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, user_id):
        return {"skill_id": skill_id, "version_no": version_no, "dir_name": dir_name, "file_path": file_path, "content": "# Ref", "user_id": user_id}

    async def save_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, content, user_id, operator_name=""):
        return {"skill_id": skill_id, "version_no": version_no, "dir_name": dir_name, "file_path": file_path, "content": content, "user_id": user_id}

    async def delete_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, user_id, operator_name=""):
        return {"skill_id": skill_id, "version_no": version_no, "dir_name": dir_name, "file_path": file_path, "deleted": True, "user_id": user_id}

    async def rollback_user_skill_async(self, skill_id, version_no, user_id, operator_name=""):
        return {"skill_id": skill_id, "version_no": version_no, "user_id": user_id, "status": "rolled_back"}

    async def sync_builtin_skills_async(self, operator="system", operator_name=""):
        return {"operator": operator, "synced": 2}

    async def reload_async(self):
        return {"reloaded": True}


def test_list_and_get_skills_api_contract(client, auth_headers, monkeypatch):
    fake_service = FakeSkillService()
    monkeypatch.setattr(skills_routes, "skill_service", fake_service)

    list_response = client.get("/api/chatdata/skills", params={"scope": "mine", "agent": "query"}, headers=auth_headers)
    detail_response = client.get("/api/chatdata/skills/analysis", headers=auth_headers)

    assert list_response.status_code == 200
    assert list_response.json()["data"]["skills"][0]["scope"] == "mine"
    assert list_response.json()["data"]["skills"][0]["agent"] == "query"
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["skill_name"] == "analysis"


def test_create_update_delete_skill_api_contract(client, auth_headers, monkeypatch):
    fake_service = FakeSkillService()
    monkeypatch.setattr(skills_routes, "skill_service", fake_service)

    create_response = client.post(
        "/api/chatdata/skills",
        json={
            "skill_name": "my-skill",
            "skill_title": "我的技能",
            "skill_desc": "测试",
            "skill_category": "custom",
            "visibility_type": "private",
            "content": "# Skill",
        },
        headers=auth_headers,
    )
    update_response = client.put(
        "/api/chatdata/skills/skill_new",
        json={
            "skill_title": "我的技能V2",
            "skill_desc": "更新",
            "skill_category": "custom",
            "visibility_type": "private",
            "content": "# Skill V2",
        },
        headers=auth_headers,
    )
    delete_response = client.delete("/api/chatdata/skills/skill_new", headers=auth_headers)

    assert create_response.status_code == 200
    assert create_response.json()["data"]["skill_id"] == "skill_new"
    assert fake_service.created_payload["user_id"] == "user_a"
    assert update_response.status_code == 200
    assert update_response.json()["data"]["skill_title"] == "我的技能V2"
    assert fake_service.updated_payload["user_id"] == "user_a"
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["deleted"] is True
    assert fake_service.deleted == {"skill_id": "skill_new", "user_id": "user_a"}


def test_skill_publish_favorite_and_versions_api_contract(client, auth_headers, monkeypatch):
    fake_service = FakeSkillService()
    monkeypatch.setattr(skills_routes, "skill_service", fake_service)

    copy_response = client.post("/api/chatdata/skills/skill_a/copy", headers=auth_headers)
    publish_response = client.post("/api/chatdata/skills/skill_a/publish", headers=auth_headers)
    favorite_response = client.post("/api/chatdata/skills/skill_a/favorite", headers=auth_headers)
    versions_response = client.get("/api/chatdata/skills/skill_a/versions", headers=auth_headers)
    version_detail_response = client.get("/api/chatdata/skills/skill_a/versions/1", headers=auth_headers)
    rollback_response = client.post("/api/chatdata/skills/skill_a/versions/1/rollback", headers=auth_headers)
    unfavorite_response = client.delete("/api/chatdata/skills/skill_a/favorite", headers=auth_headers)

    assert copy_response.status_code == 200
    assert copy_response.json()["data"]["source_skill_id"] == "skill_a"
    assert publish_response.status_code == 200
    assert publish_response.json()["data"]["status"] == "published"
    assert favorite_response.status_code == 200
    assert favorite_response.json()["data"]["favorite"] is True
    assert versions_response.status_code == 200
    assert versions_response.json()["data"]["versions"][0]["version_no"] == 1
    assert version_detail_response.status_code == 200
    assert version_detail_response.json()["data"]["content"] == "# Skill"
    assert rollback_response.status_code == 200
    assert rollback_response.json()["data"]["status"] == "rolled_back"
    assert unfavorite_response.status_code == 200
    assert unfavorite_response.json()["data"]["favorite"] is False


def test_skill_version_file_sync_and_reload_api_contract(client, auth_headers, monkeypatch):
    fake_service = FakeSkillService()
    monkeypatch.setattr(skills_routes, "skill_service", fake_service)

    files_response = client.get("/api/chatdata/skills/skill_a/versions/1/files", headers=auth_headers)
    read_response = client.get(
        "/api/chatdata/skills/skill_a/versions/1/files/content",
        params={"dir_name": "references", "file_path": "a.md"},
        headers=auth_headers,
    )
    save_response = client.put(
        "/api/chatdata/skills/skill_a/versions/1/files",
        json={"dir_name": "references", "file_path": "a.md", "content": "# New"},
        headers=auth_headers,
    )
    delete_response = client.delete(
        "/api/chatdata/skills/skill_a/versions/1/files",
        params={"dir_name": "references", "file_path": "a.md"},
        headers=auth_headers,
    )
    sync_response = client.post("/api/chatdata/skills/sync-system", headers=auth_headers)
    reload_response = client.post("/api/chatdata/skills/reload", headers=auth_headers)

    assert files_response.status_code == 200
    assert files_response.json()["data"]["files"][0]["file_path"] == "references/a.md"
    assert read_response.status_code == 200
    assert read_response.json()["data"]["content"] == "# Ref"
    assert save_response.status_code == 200
    assert save_response.json()["data"]["content"] == "# New"
    assert delete_response.status_code == 200
    assert delete_response.json()["data"]["deleted"] is True
    assert sync_response.status_code == 200
    assert sync_response.json()["data"]["operator"] == "user_a"
    assert reload_response.status_code == 200
    assert reload_response.json()["data"]["reloaded"] is True


def test_skills_api_error_mapping(client, auth_headers, monkeypatch):
    class ErrorSkillService(FakeSkillService):
        async def create_user_skill_async(self, payload, user_id, operator_name=""):
            raise ValueError("技能名称重复")

        async def update_user_skill_draft_async(self, skill_id, payload, user_id, operator_name=""):
            raise PermissionError("无权编辑")

        async def favorite_skill_async(self, skill_id, user_id, favorite, operator_name=""):
            raise ValueError("技能不存在")

        async def get_skill_async(self, skill_name, **kwargs):
            return None

    monkeypatch.setattr(skills_routes, "skill_service", ErrorSkillService())

    create_response = client.post(
        "/api/chatdata/skills",
        json={
            "skill_name": "my-skill",
            "skill_title": "我的技能",
            "skill_desc": "测试",
            "skill_category": "custom",
            "visibility_type": "private",
            "content": "# Skill",
        },
        headers=auth_headers,
    )
    update_response = client.put(
        "/api/chatdata/skills/skill_a",
        json={
            "skill_title": "我的技能V2",
            "skill_desc": "更新",
            "skill_category": "custom",
            "visibility_type": "private",
            "content": "# Skill V2",
        },
        headers=auth_headers,
    )
    favorite_response = client.post("/api/chatdata/skills/skill_a/favorite", headers=auth_headers)
    missing_response = client.get("/api/chatdata/skills/missing", headers=auth_headers)

    assert create_response.json()["code"] == ErrorCode.CHATDATA_SKILL_CREATE_INVALID
    assert update_response.json()["code"] == ErrorCode.CHATDATA_SKILL_UPDATE_FORBIDDEN
    assert favorite_response.json()["code"] == ErrorCode.CHATDATA_SKILL_FAVORITE_INVALID
    assert missing_response.json()["code"] == ErrorCode.CHATDATA_NOT_FOUND


def _response_payload(response):
    return json.loads(response.body.decode("utf-8"))


class RaisingSkillService(FakeSkillService):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def list_skills_async(self, **kwargs):
        raise self.error

    async def get_skill_async(self, skill_name, **kwargs):
        raise self.error

    async def create_user_skill_async(self, payload, user_id, operator_name=""):
        raise self.error

    async def update_user_skill_draft_async(self, skill_id, payload, user_id, operator_name=""):
        raise self.error

    async def delete_user_skill_async(self, skill_id, user_id, operator_name=""):
        raise self.error

    async def copy_user_skill_async(self, skill_id, user_id, operator_name=""):
        raise self.error

    async def publish_user_skill_async(self, skill_id, user_id, operator_name=""):
        raise self.error

    async def list_skill_versions_async(self, skill_id, user_id):
        raise self.error

    async def get_skill_version_detail_async(self, skill_id, version_no, user_id):
        raise self.error

    async def list_skill_version_files_async(self, skill_id, version_no, user_id):
        raise self.error

    async def read_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, user_id):
        raise self.error

    async def save_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, content, user_id, operator_name=""):
        raise self.error

    async def delete_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, user_id, operator_name=""):
        raise self.error

    async def rollback_user_skill_async(self, skill_id, version_no, user_id, operator_name=""):
        raise self.error

    async def favorite_skill_async(self, skill_id, user_id, favorite, operator_name=""):
        raise self.error

    async def sync_builtin_skills_async(self, operator="system", operator_name=""):
        raise self.error

    async def reload_async(self):
        raise self.error


def test_skill_list_scopes_and_optional_user_fallback(monkeypatch):
    fake_service = FakeSkillService()
    captured = []

    async def fake_list_skills_async(**kwargs):
        captured.append(kwargs)
        return [{"skill_id": "skill_a", "scope": kwargs["scope"], "agent": kwargs.get("agent", "")}]

    fake_service.list_skills_async = fake_list_skills_async
    monkeypatch.setattr(skills_routes, "skill_service", fake_service)
    monkeypatch.setattr(skills_routes, "_optional_current_user", lambda _request: None)

    invalid_scope = _response_payload(asyncio.run(skills_routes.list_skills(None, scope="bad")))
    system_scope = _response_payload(asyncio.run(skills_routes.list_skills(None, search="s", category="c", scope="system", agent="analyze")))
    invalid_agent = _response_payload(asyncio.run(skills_routes.list_skills(None, agent="bad")))

    assert invalid_scope["data"]["skills"][0]["scope"] == "all"
    assert system_scope["data"]["skills"][0]["scope"] == "system"
    assert system_scope["data"]["skills"][0]["agent"] == "analyze"
    assert invalid_agent["data"]["skills"][0]["agent"] == ""
    assert captured[0]["user_id"] == ""
    assert captured[1]["search"] == "s"
    assert captured[1]["category"] == "c"


def test_skill_mine_and_favorites_routes(monkeypatch):
    fake_service = FakeSkillService()
    monkeypatch.setattr(skills_routes, "skill_service", fake_service)
    monkeypatch.setattr(skills_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="张三"))

    mine = _response_payload(asyncio.run(skills_routes.list_my_skills(None)))
    favorites = _response_payload(asyncio.run(skills_routes.list_favorite_skills(None)))

    assert mine["data"]["skills"][0]["scope"] == "mine"
    assert favorites["data"]["skills"][0]["scope"] == "favorites"


@pytest.mark.parametrize(
    ("route_call", "expected_code"),
    [
        (lambda: skills_routes.list_skills(None), ErrorCode.CHATDATA_SKILL_LIST_FAILED),
        (lambda: skills_routes.list_my_skills(None), ErrorCode.CHATDATA_SKILL_MINE_LIST_FAILED),
        (lambda: skills_routes.list_favorite_skills(None), ErrorCode.CHATDATA_SKILL_FAVORITE_LIST_FAILED),
        (lambda: skills_routes.create_user_skill(
            SkillCreateRequest(skill_name="my-skill", skill_title="标题", skill_desc="", skill_category="custom", visibility_type="private", content=""),
            None,
        ), ErrorCode.CHATDATA_SKILL_CREATE_FAILED),
        (lambda: skills_routes.update_user_skill(
            "skill_a",
            SkillUpdateRequest(skill_title="标题", skill_desc="", skill_category="custom", visibility_type="private", content=""),
            None,
        ), ErrorCode.CHATDATA_SKILL_UPDATE_FAILED),
        (lambda: skills_routes.delete_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_DELETE_FAILED),
        (lambda: skills_routes.copy_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_COPY_FAILED),
        (lambda: skills_routes.publish_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_PUBLISH_FAILED),
        (lambda: skills_routes.list_skill_versions("skill_a", None), ErrorCode.CHATDATA_SKILL_VERSION_LIST_FAILED),
        (lambda: skills_routes.get_skill_version("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_FAILED),
        (lambda: skills_routes.list_skill_version_files("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_VERSION_FILES_FAILED),
        (lambda: skills_routes.read_skill_version_file("skill_a", 1, "references", "a.md", None), ErrorCode.CHATDATA_SKILL_FILE_READ_FAILED),
        (lambda: skills_routes.save_skill_version_file(
            "skill_a",
            1,
            SkillFileUpsertRequest(dir_name="references", file_path="a.md", content="# A"),
            None,
        ), ErrorCode.CHATDATA_SKILL_FILE_SAVE_FAILED),
        (lambda: skills_routes.delete_skill_version_file("skill_a", 1, "references", "a.md", None), ErrorCode.CHATDATA_SKILL_FILE_DELETE_FAILED),
        (lambda: skills_routes.rollback_skill_version("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_ROLLBACK_FAILED),
        (lambda: skills_routes.favorite_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_FAVORITE_FAILED),
        (lambda: skills_routes.unfavorite_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_UNFAVORITE_FAILED),
        (lambda: skills_routes.sync_system_skills(None), ErrorCode.CHATDATA_SKILL_SYNC_FAILED),
        (lambda: skills_routes.reload_skills(), ErrorCode.CHATDATA_SKILL_RELOAD_FAILED),
        (lambda: skills_routes.get_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_DETAIL_FAILED),
    ],
)
def test_skill_routes_generic_error_mapping(route_call, expected_code, monkeypatch):
    monkeypatch.setattr(skills_routes, "skill_service", RaisingSkillService(RuntimeError("boom")))
    monkeypatch.setattr(skills_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="张三"))
    monkeypatch.setattr(skills_routes, "_optional_current_user", lambda _request: SimpleNamespace(user_id="u1", username="张三"))

    payload = _response_payload(asyncio.run(route_call()))

    assert payload["status"] == "error"
    assert payload["code"] == expected_code


@pytest.mark.parametrize(
    ("route_call", "expected_code"),
    [
        (lambda: skills_routes.update_user_skill(
            "skill_a",
            SkillUpdateRequest(skill_title="标题", skill_desc="", skill_category="custom", visibility_type="private", content=""),
            None,
        ), ErrorCode.CHATDATA_SKILL_UPDATE_INVALID),
        (lambda: skills_routes.delete_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_DELETE_INVALID),
        (lambda: skills_routes.copy_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_COPY_INVALID),
        (lambda: skills_routes.publish_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_PUBLISH_INVALID),
        (lambda: skills_routes.list_skill_versions("skill_a", None), ErrorCode.CHATDATA_SKILL_VERSION_LIST_INVALID),
        (lambda: skills_routes.get_skill_version("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_INVALID),
        (lambda: skills_routes.list_skill_version_files("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_VERSION_FILES_INVALID),
        (lambda: skills_routes.read_skill_version_file("skill_a", 1, "references", "a.md", None), ErrorCode.CHATDATA_SKILL_FILE_READ_INVALID),
        (lambda: skills_routes.save_skill_version_file(
            "skill_a",
            1,
            SkillFileUpsertRequest(dir_name="references", file_path="a.md", content="# A"),
            None,
        ), ErrorCode.CHATDATA_SKILL_FILE_SAVE_INVALID),
        (lambda: skills_routes.delete_skill_version_file("skill_a", 1, "references", "a.md", None), ErrorCode.CHATDATA_SKILL_FILE_DELETE_INVALID),
        (lambda: skills_routes.rollback_skill_version("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_ROLLBACK_INVALID),
        (lambda: skills_routes.unfavorite_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_UNFAVORITE_INVALID),
    ],
)
def test_skill_routes_value_error_mapping(route_call, expected_code, monkeypatch):
    monkeypatch.setattr(skills_routes, "skill_service", RaisingSkillService(ValueError("参数错误")))
    monkeypatch.setattr(skills_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="张三"))

    payload = _response_payload(asyncio.run(route_call()))

    assert payload["status"] == "error"
    assert payload["code"] == expected_code
    assert payload["msg"] == "参数错误"


@pytest.mark.parametrize(
    ("route_call", "expected_code"),
    [
        (lambda: skills_routes.delete_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_DELETE_FORBIDDEN),
        (lambda: skills_routes.copy_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_COPY_FORBIDDEN),
        (lambda: skills_routes.publish_user_skill("skill_a", None), ErrorCode.CHATDATA_SKILL_PUBLISH_FORBIDDEN),
        (lambda: skills_routes.list_skill_versions("skill_a", None), ErrorCode.CHATDATA_SKILL_VERSION_LIST_FORBIDDEN),
        (lambda: skills_routes.get_skill_version("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_FORBIDDEN),
        (lambda: skills_routes.list_skill_version_files("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_VERSION_FILES_FORBIDDEN),
        (lambda: skills_routes.read_skill_version_file("skill_a", 1, "references", "a.md", None), ErrorCode.CHATDATA_SKILL_FILE_READ_FORBIDDEN),
        (lambda: skills_routes.save_skill_version_file(
            "skill_a",
            1,
            SkillFileUpsertRequest(dir_name="references", file_path="a.md", content="# A"),
            None,
        ), ErrorCode.CHATDATA_SKILL_FILE_SAVE_FORBIDDEN),
        (lambda: skills_routes.delete_skill_version_file("skill_a", 1, "references", "a.md", None), ErrorCode.CHATDATA_SKILL_FILE_DELETE_FORBIDDEN),
        (lambda: skills_routes.rollback_skill_version("skill_a", 1, None), ErrorCode.CHATDATA_SKILL_ROLLBACK_FORBIDDEN),
    ],
)
def test_skill_routes_permission_error_mapping(route_call, expected_code, monkeypatch):
    monkeypatch.setattr(skills_routes, "skill_service", RaisingSkillService(PermissionError("无权操作")))
    monkeypatch.setattr(skills_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="张三"))

    payload = _response_payload(asyncio.run(route_call()))

    assert payload["status"] == "error"
    assert payload["code"] == expected_code
    assert payload["msg"] == "无权操作"


def test_skill_routes_raise_auth_http_exception(monkeypatch):
    def raise_auth(_request):
        raise HTTPException(status_code=401, detail="未登录")

    monkeypatch.setattr(skills_routes, "_get_current_user", raise_auth)

    with pytest.raises(HTTPException):
        asyncio.run(skills_routes.list_my_skills(None))
