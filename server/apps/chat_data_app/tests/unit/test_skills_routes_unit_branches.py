import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat_data_app.routers import skills as skills_routes
from apps.chat_data_app.schema import SkillCreateRequest, SkillFileUpsertRequest, SkillUpdateRequest


def payload(response):
    return json.loads(response.body.decode("utf-8"))


class FakeSkillService:
    def __init__(self):
        self.calls = []

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    async def list_skills_async(self, **kwargs):
        self._record("list_skills_async", **kwargs)
        if kwargs.get("search") == "boom":
            raise RuntimeError("list down")
        return [{"name": "demo-skill"}]

    async def create_user_skill_async(self, data, user_id, operator_name=""):
        self._record("create_user_skill_async", data, user_id, operator_name=operator_name)
        if data["skill_name"] == "bad":
            raise ValueError("bad skill")
        if data["skill_name"] == "boom":
            raise RuntimeError("create down")
        return {"skill_id": "skill_new", **data}

    async def update_user_skill_draft_async(self, skill_id, data, user_id, operator_name=""):
        self._record("update_user_skill_draft_async", skill_id, data, user_id, operator_name=operator_name)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("update down")
        return {"skill_id": skill_id, **data}

    async def delete_user_skill_async(self, skill_id, user_id, operator_name=""):
        self._record("delete_user_skill_async", skill_id, user_id, operator_name=operator_name)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("delete down")

    async def copy_user_skill_async(self, skill_id, user_id, operator_name=""):
        self._record("copy_user_skill_async", skill_id, user_id, operator_name=operator_name)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("copy down")
        return {"skill_id": f"{skill_id}_copy"}

    async def publish_user_skill_async(self, skill_id, user_id, operator_name=""):
        self._record("publish_user_skill_async", skill_id, user_id, operator_name=operator_name)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("publish down")
        return {"skill_id": skill_id, "status": "published"}

    async def list_skill_versions_async(self, skill_id, user_id):
        self._record("list_skill_versions_async", skill_id, user_id)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("versions down")
        return [{"version_no": 1}]

    async def get_skill_version_detail_async(self, skill_id, version_no, user_id):
        self._record("get_skill_version_detail_async", skill_id, version_no, user_id)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("version down")
        return {"skill_id": skill_id, "version_no": version_no}

    async def list_skill_version_files_async(self, skill_id, version_no, user_id):
        self._record("list_skill_version_files_async", skill_id, version_no, user_id)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("files down")
        return [{"file_path": "a.md"}]

    async def read_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, user_id):
        self._record("read_skill_version_file_async", skill_id, version_no, dir_name, file_path, user_id)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("read down")
        return {"dir_name": dir_name, "file_path": file_path, "content": "x"}

    async def save_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, content, user_id, operator_name=""):
        self._record("save_skill_version_file_async", skill_id, version_no, dir_name, file_path, content, user_id, operator_name=operator_name)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("save down")
        return {"dir_name": dir_name, "file_path": file_path, "content": content}

    async def delete_skill_version_file_async(self, skill_id, version_no, dir_name, file_path, user_id, operator_name=""):
        self._record("delete_skill_version_file_async", skill_id, version_no, dir_name, file_path, user_id, operator_name=operator_name)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("delete file down")
        return {"deleted": True, "file_path": file_path}

    async def rollback_user_skill_async(self, skill_id, version_no, user_id, operator_name=""):
        self._record("rollback_user_skill_async", skill_id, version_no, user_id, operator_name=operator_name)
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("rollback down")
        return {"skill_id": skill_id, "version_no": version_no}

    async def favorite_skill_async(self, skill_id, user_id, favorite, operator_name=""):
        self._record("favorite_skill_async", skill_id, user_id, favorite, operator_name=operator_name)
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("favorite down")
        return {"skill_id": skill_id, "favorite": favorite}

    async def sync_builtin_skills_async(self, operator="system", operator_name=""):
        self._record("sync_builtin_skills_async", operator=operator, operator_name=operator_name)
        if operator == "boom":
            raise RuntimeError("sync down")
        return {"synced": 2}

    async def reload_async(self):
        self._record("reload_async")
        return {"reloaded": True}

    async def get_skill_async(self, skill_name, include_draft=False, user_id="", user_names=None):
        self._record("get_skill_async", skill_name, include_draft=include_draft, user_id=user_id, user_names=user_names)
        if skill_name == "missing":
            return None
        if skill_name == "boom":
            raise RuntimeError("get down")
        return {"name": skill_name}


@pytest.fixture()
def skills_context(monkeypatch):
    service = FakeSkillService()
    user = SimpleNamespace(user_id="u1", username="用户一")
    monkeypatch.setattr(skills_routes, "skill_service", service)
    monkeypatch.setattr(skills_routes, "_get_current_user", lambda _request: user)
    monkeypatch.setattr(skills_routes, "_optional_current_user", lambda _request: user)
    return service, user, SimpleNamespace()


@pytest.mark.asyncio
async def test_skill_list_routes_normalize_scope_and_handle_errors(skills_context):
    service, _user, request = skills_context

    listed = payload(await skills_routes.list_skills(request, search="看板", category="demo", scope="invalid"))
    assert listed["data"]["total"] == 1
    assert service.calls[-1][2]["scope"] == "all"
    assert service.calls[-1][2]["user_names"] == {"u1": "用户一"}

    mine = payload(await skills_routes.list_my_skills(request))
    assert mine["data"]["skills"][0]["name"] == "demo-skill"
    assert service.calls[-1][2]["scope"] == "mine"

    favorites = payload(await skills_routes.list_favorite_skills(request))
    assert favorites["data"]["total"] == 1
    assert service.calls[-1][2]["scope"] == "favorites"

    error = payload(await skills_routes.list_skills(request, search="boom"))
    assert error["code"] == 50032


@pytest.mark.asyncio
async def test_skill_metadata_mutation_routes_success_and_error_branches(skills_context):
    _service, _user, request = skills_context
    create_payload = SkillCreateRequest(skill_name="demo-skill", skill_title="Demo")
    update_payload = SkillUpdateRequest(skill_title="Updated", skill_desc="desc", skill_category="custom", visibility_type="private", content="body")

    assert payload(await skills_routes.create_user_skill(create_payload, request))["data"]["skill_id"] == "skill_new"
    assert payload(await skills_routes.create_user_skill(SkillCreateRequest(skill_name="bad", skill_title="Bad"), request))["code"] == 40040
    assert payload(await skills_routes.create_user_skill(SkillCreateRequest(skill_name="boom", skill_title="Boom"), request))["code"] == 50039

    assert payload(await skills_routes.update_user_skill("demo", update_payload, request))["data"]["skill_id"] == "demo"
    assert payload(await skills_routes.update_user_skill("denied", update_payload, request))["code"] == 40341
    assert payload(await skills_routes.update_user_skill("missing", update_payload, request))["code"] == 40044
    assert payload(await skills_routes.update_user_skill("boom", update_payload, request))["code"] == 50044

    assert payload(await skills_routes.delete_user_skill("demo", request))["data"]["deleted"] is True
    assert payload(await skills_routes.delete_user_skill("denied", request))["code"] == 40352
    assert payload(await skills_routes.delete_user_skill("missing", request))["code"] == 40052
    assert payload(await skills_routes.delete_user_skill("boom", request))["code"] == 50052

    assert payload(await skills_routes.copy_user_skill("demo", request))["data"]["skill_id"] == "demo_copy"
    assert payload(await skills_routes.copy_user_skill("denied", request))["code"] == 40353
    assert payload(await skills_routes.copy_user_skill("missing", request))["code"] == 40053
    assert payload(await skills_routes.copy_user_skill("boom", request))["code"] == 50053

    assert payload(await skills_routes.publish_user_skill("demo", request))["data"]["status"] == "published"
    assert payload(await skills_routes.publish_user_skill("denied", request))["code"] == 40340
    assert payload(await skills_routes.publish_user_skill("missing", request))["code"] == 40041
    assert payload(await skills_routes.publish_user_skill("boom", request))["code"] == 50040


@pytest.mark.asyncio
async def test_skill_version_and_file_routes_success_and_error_branches(skills_context):
    _service, _user, request = skills_context
    file_payload = SkillFileUpsertRequest(dir_name="references", file_path="a.md", content="body")

    assert payload(await skills_routes.list_skill_versions("demo", request))["data"]["total"] == 1
    assert payload(await skills_routes.list_skill_versions("denied", request))["code"] == 40345
    assert payload(await skills_routes.list_skill_versions("missing", request))["code"] == 40045
    assert payload(await skills_routes.list_skill_versions("boom", request))["code"] == 50045

    assert payload(await skills_routes.get_skill_version("demo", 1, request))["data"]["version_no"] == 1
    assert payload(await skills_routes.get_skill_version("denied", 1, request))["code"] == 40346
    assert payload(await skills_routes.get_skill_version("missing", 1, request))["code"] == 40046
    assert payload(await skills_routes.get_skill_version("boom", 1, request))["code"] == 50046

    assert payload(await skills_routes.list_skill_version_files("demo", 1, request))["data"][0]["file_path"] == "a.md"
    assert payload(await skills_routes.list_skill_version_files("denied", 1, request))["code"] == 40348
    assert payload(await skills_routes.list_skill_version_files("missing", 1, request))["code"] == 40048
    assert payload(await skills_routes.list_skill_version_files("boom", 1, request))["code"] == 50048

    assert payload(await skills_routes.read_skill_version_file("demo", 1, "references", "a.md", request))["data"]["content"] == "x"
    assert payload(await skills_routes.read_skill_version_file("denied", 1, "references", "a.md", request))["code"] == 40349
    assert payload(await skills_routes.read_skill_version_file("missing", 1, "references", "a.md", request))["code"] == 40049
    assert payload(await skills_routes.read_skill_version_file("boom", 1, "references", "a.md", request))["code"] == 50049

    assert payload(await skills_routes.save_skill_version_file("demo", 1, file_payload, request))["data"]["content"] == "body"
    assert payload(await skills_routes.save_skill_version_file("denied", 1, file_payload, request))["code"] == 40350
    assert payload(await skills_routes.save_skill_version_file("missing", 1, file_payload, request))["code"] == 40050
    assert payload(await skills_routes.save_skill_version_file("boom", 1, file_payload, request))["code"] == 50050

    assert payload(await skills_routes.delete_skill_version_file("demo", 1, "references", "a.md", request))["data"]["deleted"] is True
    assert payload(await skills_routes.delete_skill_version_file("denied", 1, "references", "a.md", request))["code"] == 40351
    assert payload(await skills_routes.delete_skill_version_file("missing", 1, "references", "a.md", request))["code"] == 40051
    assert payload(await skills_routes.delete_skill_version_file("boom", 1, "references", "a.md", request))["code"] == 50051

    assert payload(await skills_routes.rollback_skill_version("demo", 1, request))["data"]["skill_id"] == "demo"
    assert payload(await skills_routes.rollback_skill_version("denied", 1, request))["code"] == 40347
    assert payload(await skills_routes.rollback_skill_version("missing", 1, request))["code"] == 40047
    assert payload(await skills_routes.rollback_skill_version("boom", 1, request))["code"] == 50047


@pytest.mark.asyncio
async def test_skill_favorite_sync_reload_and_detail_routes(skills_context, monkeypatch):
    service, user, request = skills_context

    assert payload(await skills_routes.favorite_skill("demo", request))["data"]["favorite"] is True
    assert payload(await skills_routes.favorite_skill("missing", request))["code"] == 40042
    assert payload(await skills_routes.favorite_skill("boom", request))["code"] == 50041

    assert payload(await skills_routes.unfavorite_skill("demo", request))["data"]["favorite"] is False
    assert payload(await skills_routes.unfavorite_skill("missing", request))["code"] == 40043
    assert payload(await skills_routes.unfavorite_skill("boom", request))["code"] == 50042

    assert payload(await skills_routes.sync_system_skills(request))["data"]["synced"] == 2
    service.calls[-1][2]["operator"] == "u1"
    monkeypatch.setattr(skills_routes, "_optional_current_user", lambda _request: SimpleNamespace(user_id="boom", username="boom"))
    assert payload(await skills_routes.sync_system_skills(request))["code"] == 50043

    monkeypatch.setattr(skills_routes, "_optional_current_user", lambda _request: user)
    assert payload(await skills_routes.reload_skills())["data"]["reloaded"] is True

    detail = payload(await skills_routes.get_skill("demo", request))
    assert detail["data"]["name"] == "demo"
    assert service.calls[-1][2]["include_draft"] is True
    assert payload(await skills_routes.get_skill("missing", request))["code"] == 40404
    assert payload(await skills_routes.get_skill("boom", request))["code"] == 50033


@pytest.mark.asyncio
async def test_skill_routes_reraise_http_exception(skills_context, monkeypatch):
    _service, _user, request = skills_context

    def raise_http(_request):
        raise HTTPException(status_code=401, detail="no auth")

    monkeypatch.setattr(skills_routes, "_get_current_user", raise_http)
    with pytest.raises(HTTPException):
        await skills_routes.list_my_skills(request)
    with pytest.raises(HTTPException):
        await skills_routes.list_favorite_skills(request)
    with pytest.raises(HTTPException):
        await skills_routes.create_user_skill(SkillCreateRequest(skill_name="demo-skill", skill_title="Demo"), request)
