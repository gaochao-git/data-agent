"""``chat_data_model_registry`` DAO 单测：软删 + 复活 + 存在性判定（sqlite 内存库）。

覆盖 service 测试用 mock 挡掉、真正该验的 SQL 语义：
- upsert 新建/复活墓碑行（查不过滤 delete_flag，配合单列唯一支持重加同注册键模型）；
- api_key_cipher 传空串保持原值不动（编辑未改 key 的场景）；
- set_enabled/set_default 用存在性判定（幂等切换不误报不存在）；
- set_default 全表唯一默认位；mark_deleted 软删后 list_rows 过滤与排序正确。
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from apps.chat_data_app.daos import model_registry_dao  # noqa: E402
from apps.chat_data_app.daos.model_registry_dao import ModelRegistryDao  # noqa: E402
from utils.models import Base, ChatDataModelRegistry  # noqa: E402


@pytest.fixture()
def registry_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(sync_conn, tables=[ChatDataModelRegistry.__table__])
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

    monkeypatch.setattr(model_registry_dao, "default_get_session", fake_get_session)
    yield
    asyncio.run(engine.dispose())


def _fields(**overrides):
    base = {
        "display_name": "测试模型",
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_cipher": "cipher-old",
        "params_json": '{"temperature": 0.2, "context_window": 48000}',
        "is_default": 0,
        "enabled_flag": 1,
        "remark_text": "",
    }
    base.update(overrides)
    return base


async def _install(dao: ModelRegistryDao, model_key: str = "qwen", **overrides) -> int:
    fields = _fields(**overrides)
    fields.setdefault("model_id", model_key)
    return await dao.upsert(model_key=model_key, fields=fields, operator_id="u", operator_name="张三")


def test_upsert_insert_and_tombstone_rewrite(registry_db):
    async def run():
        dao = ModelRegistryDao()
        row_id = await _install(dao)
        assert row_id > 0

        # 软删：请求名改写墓碑名（不占用 (provider, model_id) 组合），delete_flag 置位
        assert await dao.mark_deleted(model_key="qwen", operator_id="u", operator_name="张三") is True
        tombstone = await dao.get_by_model_key(model_key="qwen", include_deleted=True)
        assert tombstone["delete_flag"] == 1
        assert tombstone["model_id"].startswith("qwen__d")
        assert await dao.has_active_provider_model(provider="", model_id="qwen") is False

        # 重加同请求名：注册键自动避开墓碑键，插全新行（不复活）
        new_id = await dao.upsert(
            model_key="qwen#2", fields=_fields(model_id="qwen", display_name="新版"), operator_id="u2", operator_name="李四"
        )
        assert new_id != row_id
        rows = await dao.list_rows()
        assert len(rows) == 1
        assert rows[0]["display_name"] == "新版"
        assert rows[0]["update_by_name"] == "李四"
        # 时间列已转 ISO 字符串（响应体 json.dumps 需要）
        assert isinstance(rows[0]["update_time"], str)

        # 活跃组合判定：新行占用 (provider='', model_id='qwen')；exclude_key 排除自身
        assert await dao.has_active_provider_model(provider="", model_id="qwen") is True
        assert await dao.has_active_provider_model(provider="", model_id="ghost") is False
        assert await dao.has_active_provider_model(provider="", model_id="qwen", exclude_key="qwen#2") is False

    asyncio.run(run())


def test_upsert_empty_api_key_keeps_original(registry_db):
    async def run():
        dao = ModelRegistryDao()
        await _install(dao)
        # api_key_cipher 传空串：保持原密文不动（编辑未改 key 场景）
        await dao.upsert(
            model_key="qwen", fields=_fields(api_key_cipher="", display_name="改名"), operator_id="u", operator_name="张三"
        )
        row = await dao.get_by_model_key(model_key="qwen")
        assert row["api_key_cipher"] == "cipher-old"
        assert row["display_name"] == "改名"

    asyncio.run(run())


def test_set_enabled_and_default_existence_semantics(registry_db):
    async def run():
        dao = ModelRegistryDao()
        await _install(dao, "a")
        await _install(dao, "b")

        # 设默认：唯一默认位 + 自动启用
        assert await dao.set_default(model_key="b", operator_id="u", operator_name="张三") is True
        rows = {row["model_key"]: row for row in await dao.list_rows()}
        assert rows["b"]["is_default"] == 1 and rows["a"]["is_default"] == 0

        # 再设 a 为默认：b 的默认位被清掉
        assert await dao.set_default(model_key="a", operator_id="u", operator_name="张三") is True
        rows = {row["model_key"]: row for row in await dao.list_rows()}
        assert rows["a"]["is_default"] == 1 and rows["b"]["is_default"] == 0

        # 启停幂等：重复操作仍返回 True（存在性判定，非 rowcount）
        assert await dao.set_enabled(model_key="b", enabled=False, operator_id="u", operator_name="张三") is True
        assert await dao.set_enabled(model_key="b", enabled=False, operator_id="u", operator_name="张三") is True
        assert await dao.set_enabled(model_key="b", enabled=True, operator_id="u", operator_name="张三") is True

        # 不存在的模型：返回 False
        assert await dao.set_enabled(model_key="ghost", enabled=True, operator_id="u", operator_name="张三") is False
        assert await dao.set_default(model_key="ghost", operator_id="u", operator_name="张三") is False
        assert await dao.mark_deleted(model_key="ghost", operator_id="u", operator_name="张三") is False

    asyncio.run(run())


def test_list_rows_order_and_enabled_filter(registry_db):
    async def run():
        dao = ModelRegistryDao()
        id_a = await _install(dao, "a")
        id_b = await _install(dao, "b")
        id_c = await _install(dao, "c", enabled_flag=0)
        await dao.set_default(model_key="b", operator_id="u", operator_name="张三")

        # 排序：is_default 优先，再按 id 升序（c 已停用但仍未删除，出现在全量视图）
        rows = await dao.list_rows()
        assert [row["model_key"] for row in rows] == ["b", "a", "c"]
        assert rows[0]["id"] == id_b and rows[1]["id"] == id_a

        # enabled_only 过滤停用行
        assert [row["model_key"] for row in await dao.list_rows(enabled_only=True)] == ["b", "a"]

        # 软删后从两视图消失
        await dao.mark_deleted(model_key="c", operator_id="u", operator_name="张三")
        assert [row["model_key"] for row in await dao.list_rows()] == ["b", "a"]
        assert id_c > 0  # 占位断言避免未使用告警

    asyncio.run(run())
