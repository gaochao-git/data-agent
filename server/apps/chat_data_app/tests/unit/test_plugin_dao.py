"""``chat_data_plugin`` DAO 单测：软删 + 复活 + 存在性判定（sqlite 内存库）。

覆盖 service 测试用 mock 挡掉、真正该验的 SQL 语义：
- upsert_installed 新建/复活（查不过滤 delete_flag，配合单列唯一支持无限装卸）；
- set_enabled 用存在性判定（幂等切换不误报不存在），已删除墓碑不可启用；
- mark_deleted 软删后 list_active/list_enabled_names 过滤正确。
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.daos import plugin_dao  # noqa: E402
from apps.chat_data_app.daos.plugin_dao import PluginDao  # noqa: E402
from utils.models import Base, ChatDataPlugin  # noqa: E402


@pytest.fixture()
def plugin_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[ChatDataPlugin.__table__])
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

    monkeypatch.setattr(plugin_dao, "default_get_session", fake_get_session)
    yield
    asyncio.run(engine.dispose())


async def _install(dao: PluginDao, name: str = "ops") -> None:
    await dao.upsert_installed(
        plugin_name=name,
        version="1.0.0",
        display_name=name,
        description="d",
        manifest_text="{}",
        operator_id="u",
        operator_name="张三",
    )


def test_upsert_list_and_idempotent_set_enabled(plugin_db):
    async def run():
        dao = PluginDao()
        await _install(dao)
        assert await dao.list_enabled_names() == {"ops"}

        assert await dao.set_enabled(plugin_name="ops", enabled=False, operator_id="u", operator_name="张三") is True
        assert await dao.list_enabled_names() == set()

        # 幂等：对已禁用项重复禁用仍返回 True（存在性判定，非 rowcount）
        assert await dao.set_enabled(plugin_name="ops", enabled=False, operator_id="u", operator_name="张三") is True
        assert await dao.set_enabled(plugin_name="ops", enabled=True, operator_id="u", operator_name="张三") is True
        assert await dao.list_enabled_names() == {"ops"}

        # 不存在的插件：返回 False
        assert await dao.set_enabled(plugin_name="ghost", enabled=True, operator_id="u", operator_name="张三") is False

    asyncio.run(run())


def test_uninstall_soft_delete_then_reactivate(plugin_db):
    async def run():
        dao = PluginDao()
        await _install(dao)
        assert len(await dao.list_active()) == 1

        assert await dao.mark_deleted(plugin_name="ops", operator_id="u", operator_name="张三") is True
        assert await dao.list_active() == []
        assert await dao.list_enabled_names() == set()
        # 已删除墓碑不可再启用
        assert await dao.set_enabled(plugin_name="ops", enabled=True, operator_id="u", operator_name="张三") is False

        # 重装：复活同一行，不产生第二条、不撞单列唯一索引
        await _install(dao)
        active = await dao.list_active()
        assert [r.plugin_name for r in active] == ["ops"]
        assert active[0].delete_flag == 0 and active[0].enabled_flag == 1
        assert await dao.list_enabled_names() == {"ops"}

    asyncio.run(run())
