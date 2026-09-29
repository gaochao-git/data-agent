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

from apps.chat_data_app.daos import dao_session
from apps.chat_data_app.daos.semantic_config_dao import SemanticConfigDao
from utils.models import Base, SemanticConfig


@pytest.fixture()
def semantic_config_db(monkeypatch):
    """用 sqlite 内存库承载语义配置表，只验证 DAO 的 SQL 行为，不依赖 MySQL。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[SemanticConfig.__table__])
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

    monkeypatch.setattr(dao_session, "get_session", fake_get_session)
    yield session_factory
    asyncio.run(engine.dispose())


def _build_row(
    config_id: str, *, group_id: str = "group_a", enabled: bool = True, delete_flag: int = 0
) -> SemanticConfig:
    return SemanticConfig(
        semantic_config_id=config_id,
        group_id=group_id,
        semantic_config_name=f"模型-{config_id}",
        enabled_flag=1 if enabled else 0,
        scope_type="public",
        delete_flag=delete_flag,
        metrics_json="[]",
        dimensions_json="[]",
        relationships_json="[]",
    )


def test_batch_update_enabled_async_only_touches_alive_rows_in_group(semantic_config_db):
    dao = SemanticConfigDao()

    async def run_checks():
        async with semantic_config_db() as session:
            session.add_all(
                [
                    _build_row("sc_1"),
                    _build_row("sc_2"),
                    _build_row("sc_3"),
                    _build_row("sc_other", group_id="group_b"),
                    _build_row("sc_deleted", delete_flag=1),
                ]
            )
            await session.commit()

        assert await dao.batch_update_enabled_async("group_a", ["sc_1", "sc_2"], False, "u1", "张三") == 2

        async with semantic_config_db() as session:
            rows = (await session.execute(select(SemanticConfig))).scalars().all()
        flags = {row.semantic_config_id: (row.enabled_flag, row.update_by, row.update_by_name) for row in rows}
        assert flags["sc_1"] == (0, "u1", "张三")
        assert flags["sc_2"] == (0, "u1", "张三")
        assert flags["sc_3"] == (1, "", "")
        assert flags["sc_other"] == (1, "", "")
        assert flags["sc_deleted"] == (1, "", "")

        # 空分组 / 空 ID 列表 / 已删除行都不产生写库
        assert await dao.batch_update_enabled_async("", ["sc_1"], True) == 0
        assert await dao.batch_update_enabled_async("group_a", [], True) == 0
        assert await dao.batch_update_enabled_async("group_a", ["sc_deleted"], True) == 0

        # 反向切换同样生效
        assert await dao.batch_update_enabled_async("group_a", ["sc_1"], True) == 1
        assert (
            next(config for config in await dao.get_by_group_id_async("group_a") if config["id"] == "sc_1")["enabled"]
            is True
        )

    asyncio.run(run_checks())
