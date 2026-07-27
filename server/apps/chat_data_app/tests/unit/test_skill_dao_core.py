import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.daos import skill_dao
from apps.chat_data_app.daos.skill_dao import SkillDao
from utils.models import Base, ChatDataSkill, ChatDataSkillFavorite, ChatDataSkillVersion


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

    monkeypatch.setattr(skill_dao, "get_session", fake_get_session)
    yield session_factory
    asyncio.run(engine.dispose())


def test_skill_dao_crud_visibility_and_versions(skill_db):
    dao = SkillDao()

    async def run_checks():
        await dao.create_skill_with_version_async({
            "skill_id": "skill_owner",
            "skill_name": "owner-skill",
            "skill_title": "Owner Skill",
            "skill_desc": "desc",
            "skill_category": "cat",
            "skill_source": "user",
            "visibility_type": "private",
            "enabled_flag": 1,
            "skill_publish_status": "draft",
            "current_skill_version_no": 0,
            "owner_user_id": "u1",
            "skill_root_dir": "/tmp/owner-skill",
            "create_by": "u1",
            "create_by_name": "用户一",
            "update_by": "u1",
            "update_by_name": "用户一",
        }, {
            "skill_id": "skill_owner",
            "skill_version_no": 1,
            "skill_version_dir": "/tmp/owner-skill/v1",
            "version_publish_status": "draft",
            "create_by": "u1",
            "create_by_name": "用户一",
            "update_by": "u1",
            "update_by_name": "用户一",
        })
        await dao.create_skill_with_version_async({
            "skill_id": "skill_global",
            "skill_name": "global-skill",
            "skill_title": "Global Skill",
            "skill_source": "user",
            "visibility_type": "global",
            "skill_publish_status": "published",
            "current_skill_version_no": 1,
            "owner_user_id": "u1",
        }, {
            "skill_id": "skill_global",
            "skill_version_no": 1,
            "skill_version_dir": "/tmp/global-skill/v1",
            "version_publish_status": "published",
        })
        async with skill_db() as session:
            session.add(ChatDataSkill(
                skill_id="legacy_system",
                skill_name="legacy-system-skill",
                skill_title="Legacy System Skill",
                skill_source="system",
                visibility_type="global",
                skill_publish_status="published",
                current_skill_version_no=1,
            ))
        await dao.create_skill_with_version_async({
            "skill_id": "global_draft",
            "skill_name": "global-draft-skill",
            "skill_title": "Global Draft Skill",
            "skill_source": "user",
            "visibility_type": "global",
            "skill_publish_status": "draft",
            "current_skill_version_no": 0,
            "owner_user_id": "u1",
        }, {
            "skill_id": "global_draft",
            "skill_version_no": 1,
            "skill_version_dir": "/tmp/global-draft-skill/v1",
            "version_publish_status": "draft",
        })

        owner_rows = await dao.list_skills_async(user_id="u1")
        assert {row["skill_id"] for row in owner_rows} == {"skill_owner", "skill_global", "global_draft"}
        owner_skill = next(row for row in owner_rows if row["skill_id"] == "skill_owner")
        assert owner_skill["draft_skill_version_no"] == 1
        assert owner_skill["favorite_flag"] == 0
        assert owner_skill["create_by"] == "u1"
        assert owner_skill["create_by_name"] == "用户一"

        other_rows = await dao.list_skills_async(user_id="u2")
        assert {row["skill_id"] for row in other_rows} == {"skill_global"}
        assert await dao.get_skill_async("global-draft-skill", user_id="u2") is None
        assert await dao.get_skill_async("owner-skill", user_id="u2") is None
        assert (await dao.get_skill_async("owner-skill", user_id="u1"))["skill_id"] == "skill_owner"
        assert (await dao.get_published_version_async("skill_global", 1))["skill_version_dir"] == "/tmp/global-skill/v1"
        assert await dao.get_published_version_async("skill_owner", 1) is None
        assert (await dao.get_latest_draft_version_async("skill_owner"))["skill_version_no"] == 1
        assert (await dao.get_latest_published_version_async("skill_global"))["skill_version_no"] == 1
        assert await dao.next_version_no_async("skill_owner") == 2
        assert await dao.get_skill_async("legacy-system-skill", user_id="u1") is None

        async with skill_db() as session:
            owner = (await session.execute(select(ChatDataSkill).where(ChatDataSkill.skill_id == "skill_owner"))).scalar_one()
            assert owner.skill_scope == "analyze"

    asyncio.run(run_checks())


def test_skill_dao_updates_publish_delete_and_favorite(skill_db):
    dao = SkillDao()

    async def run_checks():
        await dao.create_skill_with_version_async({
            "skill_id": "skill_a",
            "skill_name": "demo-skill",
            "skill_title": "Demo",
            "skill_source": "user",
            "visibility_type": "private",
            "skill_publish_status": "draft",
            "owner_user_id": "u1",
            "skill_root_dir": "/tmp/demo-skill",
        }, {
            "skill_id": "skill_a",
            "skill_version_no": 1,
            "skill_version_dir": "/tmp/demo-skill/v1",
            "version_publish_status": "draft",
        })
        assert await dao.insert_version_async({
            "skill_id": "skill_a",
            "skill_version_no": 2,
            "skill_version_dir": "/tmp/demo-skill/v2",
            "version_publish_status": "draft",
            "create_by": "u1",
            "create_by_name": "用户一",
            "update_by": "u1",
            "update_by_name": "用户一",
        }) == 1

        assert await dao.update_skill_async("skill_a", {"skill_title": "Changed"}, update_by="u2", update_by_name="用户二") == 1
        changed = await dao.get_skill_async("skill_a", user_id="u1")
        assert changed["skill_title"] == "Changed"
        assert changed["update_by"] == "u2"
        assert changed["update_by_name"] == "用户二"

        await dao.publish_version_async("skill_a", 2, update_by="u3", update_by_name="用户三")
        published = await dao.get_skill_async("skill_a", user_id="u1")
        assert published["skill_publish_status"] == "published"
        assert published["current_skill_version_no"] == 2
        assert (await dao.get_published_version_async("skill_a", 2))["update_by_name"] == "用户三"

        assert await dao.update_version_dir_async("skill_a", 2, "/tmp/demo-skill/v2-new", update_by="u4", update_by_name="用户四") == 1
        assert (await dao.get_version_async("skill_a", 2))["skill_version_dir"] == "/tmp/demo-skill/v2-new"

        await dao.set_favorite_async("u1", "skill_a", True, operator="u1", operator_name="用户一")
        assert (await dao.get_skill_async("skill_a", user_id="u1"))["favorite_flag"] == 1
        await dao.set_favorite_async("u1", "skill_a", False, operator="u1", operator_name="用户一")
        assert (await dao.get_skill_async("skill_a", user_id="u1"))["favorite_flag"] == 0
        await dao.set_favorite_async("u1", "skill_a", True, operator="u1", operator_name="用户一")
        assert (await dao.get_skill_async("skill_a", user_id="u1"))["favorite_flag"] == 1

        async with skill_db() as session:
            favorites = (await session.execute(select(ChatDataSkillFavorite))).scalars().all()
            assert len(favorites) == 1

        with pytest.raises(ValueError, match="用户技能不存在"):
            await dao.set_favorite_async("u1", "missing-skill", True)

        assert await dao.delete_skill_async("skill_a", "demo-skill--deleted-12345678", "/tmp/archive", update_by="u5", update_by_name="用户五") == 1
        assert await dao.get_skill_async("skill_a", user_id="u1") is None
        assert await dao.delete_skill_async("skill_a", "again", "/tmp/archive", update_by="u5") == 0

    asyncio.run(run_checks())
