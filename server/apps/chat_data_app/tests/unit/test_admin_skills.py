"""后台技能总览（只读）链路单测：DAO 跨用户口径 / service 汇总 / router 分支。"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.chat_data_app.agent.skills import skill_service as service_module
from apps.chat_data_app.agent.skills.skill_service import SkillService
from apps.chat_data_app.daos import skill_dao as dao_module
from apps.chat_data_app.daos.skill_dao import SkillDao
from apps.chat_data_app.routers import skills as skills_routes
from utils.error_codes import ErrorCode
from utils.models import ChatDataSkill


class FakeDao:
    def __init__(self, rows=None, owner_row=None):
        self.rows = rows or []
        self.owner_row = owner_row
        self.owner_queries: list[str] = []

    async def list_all_skills_async(self):
        return self.rows

    async def get_skill_for_admin_async(self, value):
        self.owner_queries.append(value)
        return self.owner_row


def build_service(monkeypatch, rows=None, owner_row=None, system_meta=None, plugin_rows=None, plugin_names=None):
    service = SkillService()
    service._dao = FakeDao(rows, owner_row)
    monkeypatch.setattr(service_module, "get_skill_source", lambda: None)
    monkeypatch.setattr(service_module, "load_metadata", lambda: system_meta or {})
    monkeypatch.setattr(service_module, "_plugin_skill_rows", lambda existing_names: plugin_rows or [])
    monkeypatch.setattr(service_module, "_plugin_name_by_skill", lambda: plugin_names or {})
    return service


@pytest.mark.asyncio
async def test_list_all_skills_async_merges_all_sources_and_keeps_draft(monkeypatch):
    """跨用户行保留草稿/创建人/停用信息，系统+插件技能按来源合并排序。"""
    rows = [
        {
            "skill_id": "s1",
            "skill_name": "weekly-report",
            "skill_title": "周报生成",
            "skill_source": "user",
            "owner_user_id": "u2",
            "create_by_name": "李四",
            "enabled_flag": 0,
            "skill_publish_status": "draft",
            "current_skill_version_no": 0,
            "draft_skill_version_no": 2,
        }
    ]
    service = build_service(
        monkeypatch,
        rows=rows,
        system_meta={"officecli": {"name": "officecli", "title": "Office"}},
        plugin_rows=[{"name": "fault-analysis", "source": "plugin"}],
        plugin_names={"fault-analysis": "ops-readonly"},
    )

    result = await service.list_all_skills_async()

    assert [item["name"] for item in result] == ["fault-analysis", "officecli", "weekly-report"]
    assert result[0]["plugin_name"] == "ops-readonly"
    user_skill = result[-1]
    assert user_skill["owner_user_id"] == "u2"
    assert user_skill["creator_name"] == "李四"
    assert user_skill["draft_version_no"] == 2
    assert user_skill["enabled"] is False
    assert result[1]["source"] == "system"


@pytest.mark.asyncio
async def test_get_skill_owner_for_admin_async_resolves_owner(monkeypatch):
    service = build_service(monkeypatch, owner_row={"owner_user_id": "u2"})

    async def no_system(_name):
        return None

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", no_system)
    assert await service.get_skill_owner_for_admin_async("") == ""
    assert await service.get_skill_owner_for_admin_async("plugin:fault-analysis") == ""
    assert await service.get_skill_owner_for_admin_async("weekly-report") == "u2"
    assert service._dao.owner_queries == ["weekly-report"]


@pytest.mark.asyncio
async def test_get_skill_owner_for_admin_async_skips_system_and_tolerates_db_error(monkeypatch):
    service = build_service(monkeypatch, owner_row={"owner_user_id": "u2"})

    async def is_system(_name):
        return {"name": "officecli"}

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", is_system)
    assert await service.get_skill_owner_for_admin_async("officecli") == ""
    assert service._dao.owner_queries == []

    async def no_system(_name):
        return None

    async def broken(_value):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", no_system)
    monkeypatch.setattr(service._dao, "get_skill_for_admin_async", broken)
    assert await service.get_skill_owner_for_admin_async("weekly-report") == ""


def test_plugin_name_by_skill_maps_plugin_and_tolerates_failure(monkeypatch):
    """目录树按插件分组依赖技能名 → 插件名映射；插件清单不可用时降级为空映射。"""
    from apps.chat_data_app.agent.plugins import loader as plugin_loader

    plugin = SimpleNamespace(manifest=SimpleNamespace(name="ops-readonly", skills=["fault-analysis"]))
    monkeypatch.setattr(plugin_loader, "enabled_plugins", lambda: [plugin])
    assert service_module._plugin_name_by_skill() == {"fault-analysis": "ops-readonly"}

    def broken():
        raise RuntimeError("plugin db down")

    monkeypatch.setattr(plugin_loader, "enabled_plugins", broken)
    assert service_module._plugin_name_by_skill() == {}


def test_admin_skill_filters_drop_personal_visibility():
    """all_users=True 只保留软删与来源条件，个人可见性条件必须消失。"""
    dao = SkillDao()
    personal = dao._skill_base_filters("u1", include_disabled=True)
    admin = dao._skill_base_filters("u1", include_disabled=True, all_users=True)
    assert len(personal) == 3
    assert len(admin) == 2
    assert "visibility_type" not in " ".join(str(item) for item in admin)


@pytest.mark.asyncio
async def test_admin_dao_queries_return_rows_without_favorite(monkeypatch):
    row = ChatDataSkill(
        skill_id="s1",
        skill_name="weekly-report",
        skill_title="周报",
        skill_source="user",
        owner_user_id="u2",
        enabled_flag=1,
        skill_publish_status="published",
    )

    class FakeResult:
        def all(self):
            return [(row, 2, None)]

        def first(self):
            return (row, 2, None)

    class FakeSession:
        def __init__(self):
            self.statements = []

        async def execute(self, stmt):
            self.statements.append(stmt)
            return FakeResult()

    session = FakeSession()

    @asynccontextmanager
    async def fake_get_session():
        yield session

    monkeypatch.setattr(dao_module, "get_session", fake_get_session)
    dao = SkillDao()

    rows = await dao.list_all_skills_async()
    assert rows[0]["skill_name"] == "weekly-report"
    assert rows[0]["draft_skill_version_no"] == 2
    assert rows[0]["favorite_flag"] == 0

    found = await dao.get_skill_for_admin_async("s1")
    assert found and found["owner_user_id"] == "u2"
    assert await dao.get_skill_for_admin_async("  ") is None
    assert len(session.statements) == 2


class FakeAdminService:
    def __init__(self):
        self.calls: list[tuple] = []

    async def list_all_skills_async(self):
        self.calls.append(("list",))
        return [{"name": "weekly-report", "source": "user"}]

    async def get_skill_owner_for_admin_async(self, name):
        self.calls.append(("owner", name))
        return "" if name.startswith("plugin:") or name == "officecli" else "u2"

    async def get_skill_async(self, name, include_draft=False, user_id=""):
        self.calls.append(("detail", name, include_draft, user_id))
        if name == "missing":
            return None
        return {"name": name, "content": "SKILL 正文"}

    async def read_skill_version_file_async(self, *args):
        self.calls.append(("file",) + args)
        if args[0] == "denied":
            raise PermissionError("无权读取")
        if args[0] == "invalid":
            raise ValueError("文件不存在")
        return {"dir_name": args[2], "file_path": args[3], "content": "参考文档正文"}


@pytest.fixture()
def admin_context(monkeypatch):
    service = FakeAdminService()
    monkeypatch.setattr(skills_routes, "skill_service", service)
    monkeypatch.setattr(skills_routes, "_get_current_user", lambda _request: SimpleNamespace(user_id="admin"))
    return service, SimpleNamespace()


def payload(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.mark.asyncio
async def test_admin_skill_routes_success(admin_context):
    service, request = admin_context

    listed = payload(await skills_routes.list_admin_skills(request))
    assert listed["data"]["total"] == 1
    assert listed["data"]["skills"][0]["name"] == "weekly-report"

    detail = payload(await skills_routes.get_admin_skill("weekly-report", request))
    assert detail["data"]["content"] == "SKILL 正文"
    assert ("detail", "weekly-report", True, "u2") in service.calls

    file_data = payload(
        await skills_routes.read_admin_skill_file("s1", "references", "guide.md", request, version_no=3)
    )
    assert file_data["data"]["content"] == "参考文档正文"
    assert ("file", "s1", 3, "references", "guide.md", "u2") in service.calls


@pytest.mark.asyncio
async def test_admin_skill_routes_error_branches(admin_context, monkeypatch):
    _service, request = admin_context

    assert payload(await skills_routes.get_admin_skill("missing", request))["code"] == (ErrorCode.CHATDATA_NOT_FOUND)
    assert (
        payload(await skills_routes.read_admin_skill_file("denied", "references", "a.md", request))["code"]
        == ErrorCode.CHATDATA_SKILL_FILE_READ_FORBIDDEN
    )
    assert (
        payload(await skills_routes.read_admin_skill_file("invalid", "references", "a.md", request))["code"]
        == ErrorCode.CHATDATA_SKILL_FILE_READ_INVALID
    )

    class BrokenService(FakeAdminService):
        async def list_all_skills_async(self):
            raise RuntimeError("db down")

        async def get_skill_async(self, *args, **kwargs):
            raise RuntimeError("detail down")

        async def read_skill_version_file_async(self, *args):
            raise RuntimeError("file down")

    monkeypatch.setattr(skills_routes, "skill_service", BrokenService())
    assert payload(await skills_routes.list_admin_skills(request))["code"] == (ErrorCode.CHATDATA_SKILL_LIST_FAILED)
    assert payload(await skills_routes.get_admin_skill("weekly-report", request))["code"] == (
        ErrorCode.CHATDATA_SKILL_DETAIL_FAILED
    )
    assert (
        payload(await skills_routes.read_admin_skill_file("s1", "references", "a.md", request))["code"]
        == ErrorCode.CHATDATA_SKILL_FILE_READ_FAILED
    )


@pytest.mark.asyncio
async def test_admin_skill_routes_reraise_http_exception(admin_context, monkeypatch):
    _service, request = admin_context

    def raise_http(_request):
        raise HTTPException(status_code=401, detail="no auth")

    monkeypatch.setattr(skills_routes, "_get_current_user", raise_http)
    with pytest.raises(HTTPException):
        await skills_routes.list_admin_skills(request)
    with pytest.raises(HTTPException):
        await skills_routes.get_admin_skill("weekly-report", request)
    with pytest.raises(HTTPException):
        await skills_routes.read_admin_skill_file("s1", "references", "a.md", request)
