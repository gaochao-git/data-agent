import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from utils.error_codes import ErrorCode
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
    return service, user, SimpleNamespace()


@pytest.mark.asyncio
async def test_skill_list_routes_normalize_scope_and_handle_errors(skills_context):
    service, _user, request = skills_context

    listed = payload(await skills_routes.list_skills(request, search="看板", category="demo", scope="invalid"))
    assert listed["data"]["total"] == 1
    assert service.calls[-1][2]["scope"] == "all"
    assert service.calls[-1][2]["user_names"] == {"u1": "用户一"}

    runtime = payload(await skills_routes.list_skills(request, scope="runtime"))
    assert runtime["data"]["total"] == 1
    assert service.calls[-1][2]["scope"] == "runtime"

    mine = payload(await skills_routes.list_skills(request, scope="mine"))
    assert mine["data"]["skills"][0]["name"] == "demo-skill"
    assert service.calls[-1][2]["scope"] == "mine"

    favorites = payload(await skills_routes.list_skills(request, scope="favorites"))
    assert favorites["data"]["total"] == 1
    assert service.calls[-1][2]["scope"] == "favorites"

    error = payload(await skills_routes.list_skills(request, search="boom"))
    assert error["code"] == ErrorCode.CHATDATA_SKILL_LIST_FAILED


@pytest.mark.asyncio
async def test_skill_metadata_mutation_routes_success_and_error_branches(skills_context):
    _service, _user, request = skills_context
    create_payload = SkillCreateRequest(skill_name="demo-skill", skill_title="Demo")
    update_payload = SkillUpdateRequest(skill_title="Updated", skill_desc="desc", skill_category="custom", visibility_type="private", content="body")

    assert payload(await skills_routes.create_user_skill(create_payload, request))["data"]["skill_id"] == "skill_new"
    assert payload(await skills_routes.create_user_skill(SkillCreateRequest(skill_name="bad", skill_title="Bad"), request))["code"] == ErrorCode.CHATDATA_SKILL_CREATE_INVALID
    assert payload(await skills_routes.create_user_skill(SkillCreateRequest(skill_name="boom", skill_title="Boom"), request))["code"] == ErrorCode.CHATDATA_SKILL_CREATE_FAILED

    assert payload(await skills_routes.update_user_skill("demo", update_payload, request))["data"]["skill_id"] == "demo"
    assert payload(await skills_routes.update_user_skill("denied", update_payload, request))["code"] == ErrorCode.CHATDATA_SKILL_UPDATE_FORBIDDEN
    assert payload(await skills_routes.update_user_skill("missing", update_payload, request))["code"] == ErrorCode.CHATDATA_SKILL_UPDATE_INVALID
    assert payload(await skills_routes.update_user_skill("boom", update_payload, request))["code"] == ErrorCode.CHATDATA_SKILL_UPDATE_FAILED

    assert payload(await skills_routes.delete_user_skill("demo", request))["data"]["deleted"] is True
    assert payload(await skills_routes.delete_user_skill("denied", request))["code"] == ErrorCode.CHATDATA_SKILL_DELETE_FORBIDDEN
    assert payload(await skills_routes.delete_user_skill("missing", request))["code"] == ErrorCode.CHATDATA_SKILL_DELETE_INVALID
    assert payload(await skills_routes.delete_user_skill("boom", request))["code"] == ErrorCode.CHATDATA_SKILL_DELETE_FAILED

    assert payload(await skills_routes.copy_user_skill("demo", request))["data"]["skill_id"] == "demo_copy"
    assert payload(await skills_routes.copy_user_skill("denied", request))["code"] == ErrorCode.CHATDATA_SKILL_COPY_FORBIDDEN
    assert payload(await skills_routes.copy_user_skill("missing", request))["code"] == ErrorCode.CHATDATA_SKILL_COPY_INVALID
    assert payload(await skills_routes.copy_user_skill("boom", request))["code"] == ErrorCode.CHATDATA_SKILL_COPY_FAILED

    assert payload(await skills_routes.publish_user_skill("demo", request))["data"]["status"] == "published"
    assert payload(await skills_routes.publish_user_skill("denied", request))["code"] == ErrorCode.CHATDATA_SKILL_PUBLISH_FORBIDDEN
    assert payload(await skills_routes.publish_user_skill("missing", request))["code"] == ErrorCode.CHATDATA_SKILL_PUBLISH_INVALID
    assert payload(await skills_routes.publish_user_skill("boom", request))["code"] == ErrorCode.CHATDATA_SKILL_PUBLISH_FAILED


@pytest.mark.asyncio
async def test_skill_version_and_file_routes_success_and_error_branches(skills_context):
    _service, _user, request = skills_context
    file_payload = SkillFileUpsertRequest(dir_name="references", file_path="a.md", content="body")

    assert payload(await skills_routes.list_skill_versions("demo", request))["data"]["total"] == 1
    assert payload(await skills_routes.list_skill_versions("denied", request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_LIST_FORBIDDEN
    assert payload(await skills_routes.list_skill_versions("missing", request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_LIST_INVALID
    assert payload(await skills_routes.list_skill_versions("boom", request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_LIST_FAILED

    assert payload(await skills_routes.get_skill_version("demo", 1, request))["data"]["version_no"] == 1
    assert payload(await skills_routes.get_skill_version("denied", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_FORBIDDEN
    assert payload(await skills_routes.get_skill_version("missing", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_INVALID
    assert payload(await skills_routes.get_skill_version("boom", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_DETAIL_FAILED

    assert payload(await skills_routes.list_skill_version_files("demo", 1, request))["data"][0]["file_path"] == "a.md"
    assert payload(await skills_routes.list_skill_version_files("denied", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_FILES_FORBIDDEN
    assert payload(await skills_routes.list_skill_version_files("missing", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_FILES_INVALID
    assert payload(await skills_routes.list_skill_version_files("boom", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_VERSION_FILES_FAILED

    assert payload(await skills_routes.read_skill_version_file("demo", 1, "references", "a.md", request))["data"]["content"] == "x"
    assert payload(await skills_routes.read_skill_version_file("denied", 1, "references", "a.md", request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_FORBIDDEN
    assert payload(await skills_routes.read_skill_version_file("missing", 1, "references", "a.md", request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_INVALID
    assert payload(await skills_routes.read_skill_version_file("boom", 1, "references", "a.md", request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_FAILED

    assert payload(await skills_routes.save_skill_version_file("demo", 1, file_payload, request))["data"]["content"] == "body"
    assert payload(await skills_routes.save_skill_version_file("denied", 1, file_payload, request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_SAVE_FORBIDDEN
    assert payload(await skills_routes.save_skill_version_file("missing", 1, file_payload, request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_SAVE_INVALID
    assert payload(await skills_routes.save_skill_version_file("boom", 1, file_payload, request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_SAVE_FAILED

    assert payload(await skills_routes.delete_skill_version_file("demo", 1, "references", "a.md", request))["data"]["deleted"] is True
    assert payload(await skills_routes.delete_skill_version_file("denied", 1, "references", "a.md", request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_DELETE_FORBIDDEN
    assert payload(await skills_routes.delete_skill_version_file("missing", 1, "references", "a.md", request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_DELETE_INVALID
    assert payload(await skills_routes.delete_skill_version_file("boom", 1, "references", "a.md", request))["code"] == ErrorCode.CHATDATA_SKILL_FILE_DELETE_FAILED

    assert payload(await skills_routes.rollback_skill_version("demo", 1, request))["data"]["skill_id"] == "demo"
    assert payload(await skills_routes.rollback_skill_version("denied", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_ROLLBACK_FORBIDDEN
    assert payload(await skills_routes.rollback_skill_version("missing", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_ROLLBACK_INVALID
    assert payload(await skills_routes.rollback_skill_version("boom", 1, request))["code"] == ErrorCode.CHATDATA_SKILL_ROLLBACK_FAILED


@pytest.mark.asyncio
async def test_skill_favorite_and_detail_routes(skills_context, monkeypatch):
    service, _user, request = skills_context

    assert payload(await skills_routes.favorite_skill("demo", request))["data"]["favorite"] is True
    assert payload(await skills_routes.favorite_skill("missing", request))["code"] == ErrorCode.CHATDATA_SKILL_FAVORITE_INVALID
    assert payload(await skills_routes.favorite_skill("boom", request))["code"] == ErrorCode.CHATDATA_SKILL_FAVORITE_FAILED

    assert payload(await skills_routes.unfavorite_skill("demo", request))["data"]["favorite"] is False
    assert payload(await skills_routes.unfavorite_skill("missing", request))["code"] == ErrorCode.CHATDATA_SKILL_UNFAVORITE_INVALID
    assert payload(await skills_routes.unfavorite_skill("boom", request))["code"] == ErrorCode.CHATDATA_SKILL_UNFAVORITE_FAILED

    detail = payload(await skills_routes.get_skill("demo", request))
    assert detail["data"]["name"] == "demo"
    assert service.calls[-1][2]["include_draft"] is True
    assert payload(await skills_routes.get_skill("missing", request))["code"] == ErrorCode.CHATDATA_NOT_FOUND
    assert payload(await skills_routes.get_skill("boom", request))["code"] == ErrorCode.CHATDATA_SKILL_DETAIL_FAILED


@pytest.mark.asyncio
async def test_skill_routes_reraise_http_exception(skills_context, monkeypatch):
    _service, _user, request = skills_context

    def raise_http(_request):
        raise HTTPException(status_code=401, detail="no auth")

    monkeypatch.setattr(skills_routes, "_get_current_user", raise_http)
    with pytest.raises(HTTPException):
        await skills_routes.list_skills(request, scope="mine")
    with pytest.raises(HTTPException):
        await skills_routes.list_skills(request, scope="favorites")
    with pytest.raises(HTTPException):
        await skills_routes.create_user_skill(SkillCreateRequest(skill_name="demo-skill", skill_title="Demo"), request)
