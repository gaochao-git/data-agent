"""skills 链路覆盖率补充：skill_service 兜底分支、技能文件路由与 DAO 查询。

覆盖 master 上仍未触达的异常/边界路径：路径校验、运行期快照跳过、
插件技能元数据异常、文件预览/上传路由的错误码映射、DAO 空值与版本查询。
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.agent.plugins import loader as plugins_loader
from apps.chat_data_app.agent.skills import loader as skills_loader
from apps.chat_data_app.agent.skills import skill_service as ss
from apps.chat_data_app.daos import skill_dao as skill_dao_module
from apps.chat_data_app.daos.skill_dao import SkillDao
from apps.chat_data_app.routers import skills as skills_routes
from utils.error_codes import ErrorCode
from utils.models import Base, ChatDataSkill, ChatDataSkillFavorite, ChatDataSkillVersion


class _VersionDao:
    def __init__(self, version):
        self._version = version
        self.calls = []

    async def get_published_version_async(self, skill_id, version_no):
        self.calls.append((skill_id, version_no))
        return self._version


# ---------------------------------------------------------------------------
# skill_service 工具函数
# ---------------------------------------------------------------------------


def test_resolve_version_dir_rejects_blank_values():
    for value in ("", "  ", "."):
        with pytest.raises(ValueError, match="技能版本目录无效"):
            ss._resolve_user_skill_version_dir(value)


def test_ensure_skill_dirs_rejects_blank_dir():
    with pytest.raises(ValueError, match="技能版本目录无效"):
        ss._ensure_skill_dirs(Path("."))


def test_safe_copy_skill_subdir_rejects_parent_relative_path(tmp_path):
    class FakeRelative:
        parts = ("..", "escape.txt")

    class FakeItem:
        def is_symlink(self):
            return False

        def is_file(self):
            return True

        def relative_to(self, _root):
            return FakeRelative()

    class FakeSource:
        def rglob(self, _pattern):
            return [FakeItem()]

    with pytest.raises(ValueError, match="技能目录包含非法路径"):
        ss._safe_copy_skill_subdir(FakeSource(), tmp_path)


def test_append_runtime_user_skill_source_skips_missing_version():
    async def run_checks():
        out = []
        dao = _VersionDao(None)
        await ss._append_runtime_user_skill_source(out, {"skill_source": "system"}, dao)
        await ss._append_runtime_user_skill_source(out, {"skill_source": "user", "current_skill_version_no": 0}, dao)
        await ss._append_runtime_user_skill_source(
            out, {"skill_source": "user", "current_skill_version_no": 1, "skill_id": "s1"}, dao
        )
        assert out == []
        # 只有非 user 来源的行完全不会访问 DAO
        assert dao.calls == [("s1", 1)]

    asyncio.run(run_checks())


def test_append_runtime_user_skill_source_requires_existing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "USER_SKILL_ROOT", tmp_path)
    existing = tmp_path / "u1" / "demo-skill" / "versions" / "v1"
    existing.mkdir(parents=True)

    async def run_checks():
        out = []
        await ss._append_runtime_user_skill_source(
            out,
            {
                "skill_source": "user",
                "current_skill_version_no": 1,
                "skill_id": "s1",
                "owner_user_id": "u1",
                "skill_name": "demo-skill",
            },
            _VersionDao({"skill_version_no": 1}),
        )
        assert out == [("demo-skill", existing.resolve())]

        missing = []
        await ss._append_runtime_user_skill_source(
            missing,
            {
                "skill_source": "user",
                "current_skill_version_no": 2,
                "skill_id": "s1",
                "owner_user_id": "u1",
                "skill_name": "demo-skill",
            },
            _VersionDao({"skill_version_no": 2}),
        )
        assert missing == []

    asyncio.run(run_checks())


def test_reject_symlink_tree_rejects_root_and_nested(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="软链接"):
        ss._reject_symlink_tree(root_link)

    root = tmp_path / "root"
    root.mkdir()
    (root / "nested").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="软链接"):
        ss._reject_symlink_tree(root)


def test_plugin_skill_rows_skips_unreadable_and_empty_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(
        plugins_loader,
        "enabled_plugin_skill_sources",
        lambda: [("bad-skill", tmp_path / "bad"), ("empty-skill", tmp_path / "empty")],
    )

    def fake_parse(path):
        if not str(path).startswith(str(tmp_path / "bad")):
            return None
        raise RuntimeError("unreadable")

    monkeypatch.setattr(skills_loader, "parse_metadata", fake_parse)

    assert ss._plugin_skill_rows(set()) == []


def test_list_runtime_sources_skips_user_row_resolution_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "USER_SKILL_ROOT", tmp_path)

    async def failing_append(_out, _row, _dao):
        raise ValueError("历史绝对路径越界")

    monkeypatch.setattr(ss, "load_metadata", lambda: {})
    monkeypatch.setattr(ss, "get_skill_source", lambda: None)
    monkeypatch.setattr(ss, "_append_runtime_user_skill_source", failing_append)

    service = ss.SkillService()

    async def fake_db_rows(user_id):
        return [{"skill_source": "user", "skill_name": "broken-skill"}]

    monkeypatch.setattr(service, "_safe_db_skills_async", fake_db_rows)
    assert asyncio.run(service.list_runtime_skill_sources_async("u1")) == []


def test_import_user_skill_rejects_symlinked_skill_md(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "real.md"
    target.write_text("---\nname: demo\n---\n", encoding="utf-8")
    (source / "SKILL.md").symlink_to(target)

    with pytest.raises(ValueError, match="软链接"):
        asyncio.run(ss.SkillService().import_user_skill_from_directory_async(source, "u1"))


def test_resolve_copy_source_rejects_system_skill(monkeypatch):
    service = ss.SkillService()

    async def fake_system_metadata(name):
        return {"name": name}

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", fake_system_metadata)
    with pytest.raises(PermissionError, match="系统技能不支持复制"):
        asyncio.run(service._resolve_copy_source_skill_async("data-analyzing", "u1"))


def test_system_skill_version_listing_and_detail(tmp_path, monkeypatch):
    skill_dir = tmp_path / "demo"
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n# 正文", encoding="utf-8")
    (skill_dir / "references" / "guide.md").write_text("guide", encoding="utf-8")

    service = ss.SkillService()

    async def fake_system_dir(_skill_id, _version_no):
        return skill_dir

    monkeypatch.setattr(service, "_get_system_version_dir_async", fake_system_dir)

    versions = asyncio.run(service.list_skill_versions_async("demo", "u1"))
    assert versions == [
        {"skill_id": "demo", "skill_version_no": 1, "version_publish_status": "published", "current": True}
    ]

    detail = asyncio.run(service.get_skill_version_detail_async("demo", 1, "u1"))
    assert detail["content"] == "# 正文"
    assert detail["references"] == ["guide.md"]


def test_read_version_file_rejects_binary_and_unknown_mime(tmp_path, monkeypatch):
    version_dir = tmp_path / "skill" / "versions" / "v1"
    (version_dir / "assets").mkdir(parents=True)
    (version_dir / "assets" / "blob.bin").write_bytes(b"\xff\xfe\x00")
    (version_dir / "assets" / "data.unknownext").write_bytes(b"raw")

    service = ss.SkillService()

    async def fake_readable(*_args, **_kwargs):
        return version_dir

    monkeypatch.setattr(service, "_get_readable_version_dir_async", fake_readable)

    with pytest.raises(ValueError, match="不支持在线预览"):
        asyncio.run(service.read_skill_version_file_async("s1", 1, "assets", "blob.bin", "u1"))

    raw, content_type = asyncio.run(
        service.read_skill_version_file_raw_async("s1", 1, "assets", "data.unknownext", "u1")
    )
    assert raw == b"raw"
    assert content_type == "application/octet-stream"


def test_office_preview_falls_back_to_copy_when_hardlink_fails(tmp_path, monkeypatch):
    version_dir = tmp_path / "skill" / "versions" / "v1"
    (version_dir / "references").mkdir(parents=True)
    (version_dir / "references" / "report.docx").write_bytes(b"PK\x03\x04")
    service = ss.SkillService()

    async def fake_readable(*_args, **_kwargs):
        return version_dir

    monkeypatch.setattr(service, "_get_readable_version_dir_async", fake_readable)

    def broken_link(*_args, **_kwargs):
        raise OSError("cross-device")

    monkeypatch.setattr(ss.os, "link", broken_link)

    class FakePreviewService:
        async def render_html(self, runtime, *, file_path, max_chars):
            assert (Path(runtime.workspace_root) / "workspace" / "report.docx").read_bytes() == b"PK\x03\x04"
            return {"html": "<html>ok</html>", "truncated": False}

    monkeypatch.setattr(
        "apps.chat_data_app.agent.workspace.workspace_special_services.workspace_office_preview_service",
        FakePreviewService(),
    )

    async def fake_setting(_key, default=None):
        return 50

    monkeypatch.setattr(ss, "get_runtime_setting", fake_setting)

    preview = asyncio.run(service.render_skill_office_preview_async("s1", 1, "references", "report.docx", "u1"))
    assert preview["html"] == "<html>ok</html>"


def test_delete_skill_file_unlinks_existing_draft_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "USER_SKILL_ROOT", tmp_path)
    published = tmp_path / "u1" / "demo-skill" / "versions" / "v1"
    (published / "assets").mkdir(parents=True)
    (published / "assets" / "old.txt").write_text("old", encoding="utf-8")

    class FakeStore:
        async def get_skill_async(self, skill_id, user_id=""):
            return {
                "skill_id": "s1",
                "skill_name": "demo-skill",
                "skill_source": "user",
                "owner_user_id": "u1",
                "current_skill_version_no": 1,
            }

        async def get_latest_draft_version_async(self, skill_id):
            return None

        async def next_version_no_async(self, skill_id):
            return 2

        async def get_version_async(self, skill_id, version_no):
            return {"skill_id": skill_id, "skill_version_no": 1}

        async def get_latest_published_version_async(self, skill_id):
            return {"skill_id": skill_id, "skill_version_no": 1}

        async def insert_version_async(self, version):
            return 1

    service = ss.SkillService()
    monkeypatch.setattr(service, "_dao", FakeStore())

    result = asyncio.run(service.delete_skill_version_file_async("s1", 1, "assets", "old.txt", "u1"))
    assert result["deleted"] is True
    draft_copy = tmp_path / "u1" / "demo-skill" / "versions" / "v2" / "assets" / "old.txt"
    assert draft_copy.exists() is False
    assert (published / "assets" / "old.txt").exists() is True


def test_favorite_rejects_system_skill(monkeypatch):
    service = ss.SkillService()

    async def fake_system_metadata(name):
        return {"name": name}

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", fake_system_metadata)
    with pytest.raises(ValueError, match="系统技能不支持收藏"):
        asyncio.run(service.favorite_skill_async("data-analyzing", "u1", True))


def test_db_skill_detail_returns_none_when_path_missing(tmp_path, monkeypatch):
    service = ss.SkillService()

    async def fake_metadata(*_args, **_kwargs):
        return {"name": "demo", "path": str(tmp_path / "missing" / "SKILL.md")}

    monkeypatch.setattr(service, "_get_db_skill_metadata_async", fake_metadata)
    assert asyncio.run(service._get_db_skill_detail_async("demo")) is None


def test_readable_version_dir_reports_missing_skill_after_dao_error(monkeypatch):
    service = ss.SkillService()

    class BrokenStore:
        async def get_skill_async(self, skill_id, user_id=""):
            raise RuntimeError("table missing")

    async def no_system_dir(_skill_id, _version_no):
        return None

    monkeypatch.setattr(service, "_dao", BrokenStore())
    monkeypatch.setattr(service, "_get_system_version_dir_async", no_system_dir)

    with pytest.raises(ValueError, match="技能不存在"):
        asyncio.run(service._get_readable_version_dir_async("unknown", 1, "u1"))


def test_system_version_dir_is_none_when_skill_md_missing(tmp_path, monkeypatch):
    service = ss.SkillService()

    async def fake_metadata(_name):
        return {"path": str(tmp_path / "missing" / "SKILL.md")}

    monkeypatch.setattr(service, "_get_system_skill_metadata_async", fake_metadata)
    assert asyncio.run(service._get_system_version_dir_async("demo", 1)) is None


def test_resolve_file_rejects_symlink_escaping_base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "USER_SKILL_ROOT", tmp_path)
    version_dir = tmp_path / "skill" / "versions" / "v1"
    (version_dir / "references").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    (version_dir / "references" / "link.md").symlink_to(outside)

    service = ss.SkillService()
    with pytest.raises(ValueError, match="文件路径越界"):
        service._resolve_file_under_version_dir(version_dir, "references", "link.md")


# ---------------------------------------------------------------------------
# 技能文件路由
# ---------------------------------------------------------------------------


class _RouteService:
    async def read_skill_version_file_raw_async(self, skill_id, _version_no, _dir_name, _file_path, _user_id):
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("raw down")
        return b"\x89PNG", "image/png"

    async def render_skill_office_preview_async(self, skill_id, _version_no, _dir_name, _file_path, _user_id):
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("preview down")
        return {"available": True, "html": "<html/>"}

    async def upload_skill_version_file_async(
        self, skill_id, _version_no, dir_name, filename, _file_stream, _user_id, operator_name=""
    ):
        if skill_id == "denied":
            raise PermissionError("denied")
        if skill_id == "missing":
            raise ValueError("missing")
        if skill_id == "boom":
            raise RuntimeError("upload down")
        return {"uploaded": True, "dir_name": dir_name, "filename": filename, "operator": operator_name}


@pytest.fixture()
def skills_routes_context(monkeypatch):
    service = _RouteService()
    monkeypatch.setattr(skills_routes, "skill_service", service)
    monkeypatch.setattr(
        skills_routes,
        "_get_current_user",
        lambda _request: SimpleNamespace(user_id="u1", username="用户一"),
    )
    return SimpleNamespace()


def test_read_raw_route_maps_results_and_errors(skills_routes_context):
    request = skills_routes_context

    response = asyncio.run(skills_routes.read_skill_version_file_raw("demo", 1, "assets", "logo.png", request))
    assert response.body == b"\x89PNG"
    assert response.media_type == "image/png"

    denied = asyncio.run(skills_routes.read_skill_version_file_raw("denied", 1, "assets", "logo.png", request))
    assert json.loads(denied.body)["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_FORBIDDEN

    invalid = asyncio.run(skills_routes.read_skill_version_file_raw("missing", 1, "assets", "logo.png", request))
    assert json.loads(invalid.body)["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_INVALID

    failed = asyncio.run(skills_routes.read_skill_version_file_raw("boom", 1, "assets", "logo.png", request))
    assert json.loads(failed.body)["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_FAILED


def test_read_raw_route_re_raises_http_exception(skills_routes_context, monkeypatch):
    def reject(_request):
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(skills_routes, "_get_current_user", reject)
    with pytest.raises(HTTPException):
        asyncio.run(skills_routes.read_skill_version_file_raw("demo", 1, "assets", "logo.png", skills_routes_context))


def test_office_preview_route_maps_results_and_errors(skills_routes_context):
    request = skills_routes_context

    success = asyncio.run(skills_routes.render_skill_office_preview("demo", 1, "references", "report.docx", request))
    assert json.loads(success.body)["data"]["html"] == "<html/>"

    denied = asyncio.run(skills_routes.render_skill_office_preview("denied", 1, "references", "report.docx", request))
    assert json.loads(denied.body)["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_FORBIDDEN

    invalid = asyncio.run(skills_routes.render_skill_office_preview("missing", 1, "references", "report.docx", request))
    assert json.loads(invalid.body)["code"] == ErrorCode.CHATDATA_SKILL_FILE_READ_INVALID

    failed = json.loads(
        asyncio.run(skills_routes.render_skill_office_preview("boom", 1, "references", "report.docx", request)).body
    )
    assert failed["data"] == {"available": False, "reason": "preview_failed"}


def test_office_preview_route_re_raises_http_exception(skills_routes_context, monkeypatch):
    def reject(_request):
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(skills_routes, "_get_current_user", reject)
    with pytest.raises(HTTPException):
        asyncio.run(
            skills_routes.render_skill_office_preview("demo", 1, "references", "report.docx", skills_routes_context)
        )


def test_upload_route_maps_results_and_errors(skills_routes_context):
    request = skills_routes_context
    upload = SimpleNamespace(filename="notes.txt")

    success = json.loads(
        asyncio.run(skills_routes.upload_skill_version_file("demo", 1, request, upload, "references")).body
    )
    assert success["data"]["filename"] == "notes.txt"
    assert success["data"]["operator"] == "用户一"

    denied = json.loads(
        asyncio.run(skills_routes.upload_skill_version_file("denied", 1, request, upload, "references")).body
    )
    assert denied["code"] == ErrorCode.CHATDATA_SKILL_FILE_SAVE_FORBIDDEN

    invalid = json.loads(
        asyncio.run(skills_routes.upload_skill_version_file("missing", 1, request, upload, "references")).body
    )
    assert invalid["code"] == ErrorCode.CHATDATA_SKILL_FILE_SAVE_INVALID

    failed = json.loads(
        asyncio.run(skills_routes.upload_skill_version_file("boom", 1, request, upload, "references")).body
    )
    assert failed["code"] == ErrorCode.CHATDATA_SKILL_FILE_SAVE_FAILED


def test_upload_route_re_raises_http_exception(skills_routes_context, monkeypatch):
    def reject(_request):
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(skills_routes, "_get_current_user", reject)
    with pytest.raises(HTTPException):
        asyncio.run(
            skills_routes.upload_skill_version_file(
                "demo", 1, skills_routes_context, SimpleNamespace(filename="a.txt"), "assets"
            )
        )


# ---------------------------------------------------------------------------
# skill DAO
# ---------------------------------------------------------------------------


@pytest.fixture()
def skill_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn,
                    tables=[ChatDataSkill.__table__, ChatDataSkillVersion.__table__, ChatDataSkillFavorite.__table__],
                )
            )

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(skill_dao_module, "get_session", fake_get_session)
    yield session_factory
    asyncio.run(engine.dispose())


def test_skill_dao_empty_values_versions_and_activation(skill_db):
    dao = SkillDao()

    async def run_checks():
        assert await dao.get_skill_async("   ") is None
        assert await dao.skill_name_exists_async("") is False
        assert await dao.skill_name_exists_async("demo-skill") is False

        await dao.create_skill_with_version_async(
            {
                "skill_id": "s1",
                "skill_name": "demo-skill",
                "skill_title": "Demo",
                "skill_source": "user",
                "visibility_type": "private",
                "enabled_flag": 1,
                "skill_publish_status": "draft",
                "current_skill_version_no": 0,
                "owner_user_id": "u1",
                "create_by": "u1",
                "update_by": "u1",
            },
            {
                "skill_id": "s1",
                "skill_version_no": 1,
                "version_publish_status": "draft",
                "create_by": "u1",
                "update_by": "u1",
            },
        )

        assert await dao.skill_name_exists_async("demo-skill") is True
        versions = await dao.list_versions_async("s1")
        assert [item["skill_version_no"] for item in versions] == [1]
        assert (await dao.get_version_async("s1", 1))["skill_version_no"] == 1
        assert await dao.get_version_async("s1", 9) is None

        await dao.activate_version_async("s1", 1, update_by="u1", update_by_name="用户一")
        row = await dao.get_skill_async("s1", user_id="u1")
        assert row["current_skill_version_no"] == 1
        assert row["skill_publish_status"] == "published"
        assert row["update_by_name"] == "用户一"

    asyncio.run(run_checks())
