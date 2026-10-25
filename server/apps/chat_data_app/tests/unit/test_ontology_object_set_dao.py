"""对象集合 DAO 层单测（内存 SQLite，真实 SQL 路径）。

覆盖 upsert 三态（插入/复活软删行/拒绝覆盖活跃行）、读取、过期过滤、
软删。service 层鸭子类型测试见 test_ontology_object_set.py。
"""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.daos import ontology_object_set_dao as dao_module
from apps.chat_data_app.daos.ontology_object_set_dao import OntologyObjectSetDao
from utils.models import Base


@pytest.fixture()
def harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_test_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(dao_module, "session_scope", get_test_session)
    return OntologyObjectSetDao()


def _payload(**overrides):
    payload = {
        "display_name": "杜佑客户",
        "description": "",
        "object_type_id": 7,
        "object_type_name": "customer",
        "member_datasource_id": "ORG_CODE1",
        "object_keys": ["C1", "C2"],
        "temporary": True,
        "expires_at": datetime.now() + timedelta(hours=24),
    }
    payload.update(overrides)
    return payload


def test_upsert_and_get_round_trip(harness):
    saved = asyncio.run(harness.upsert_object_set_async("duyou_customers", _payload(), allow_overwrite=False))
    assert saved["set_name"] == "duyou_customers"
    assert saved["object_keys"] == ["C1", "C2"]
    assert saved["object_key_count"] == 2
    assert saved["temporary"] is True

    fetched = asyncio.run(harness.get_object_set_async("duyou_customers"))
    assert fetched["object_keys"] == ["C1", "C2"]
    assert fetched["object_type_name"] == "customer"
    assert fetched["expired"] is False


def test_active_name_requires_overwrite(harness):
    asyncio.run(harness.upsert_object_set_async("duyou_customers", _payload(), allow_overwrite=False))
    assert asyncio.run(harness.upsert_object_set_async("duyou_customers", _payload(), allow_overwrite=False)) is None
    overwritten = asyncio.run(
        harness.upsert_object_set_async("duyou_customers", _payload(object_keys=["C9"]), allow_overwrite=True)
    )
    assert overwritten["object_keys"] == ["C9"]


def test_soft_deleted_name_can_be_revived_without_overwrite(harness):
    asyncio.run(harness.upsert_object_set_async("duyou_customers", _payload(), allow_overwrite=False))
    assert asyncio.run(harness.soft_delete_object_set_async("duyou_customers")) is True
    assert asyncio.run(harness.get_object_set_async("duyou_customers")) is None
    revived = asyncio.run(harness.upsert_object_set_async("duyou_customers", _payload(), allow_overwrite=False))
    assert revived is not None
    assert revived["delete_flag"] == 0


def test_list_filters_expired_sets(harness):
    asyncio.run(
        harness.upsert_object_set_async(
            "active_set", _payload(expires_at=datetime.now() + timedelta(hours=1)), allow_overwrite=False
        )
    )
    asyncio.run(
        harness.upsert_object_set_async(
            "expired_set", _payload(expires_at=datetime.now() - timedelta(hours=1)), allow_overwrite=False
        )
    )
    asyncio.run(harness.upsert_object_set_async("permanent_set", _payload(expires_at=None), allow_overwrite=False))

    names = [item["set_name"] for item in asyncio.run(harness.list_object_sets_async())]
    assert set(names) == {"active_set", "permanent_set"}
    all_names = [item["set_name"] for item in asyncio.run(harness.list_object_sets_async(include_expired=True))]
    assert sorted(all_names) == ["active_set", "expired_set", "permanent_set"]


def test_list_filters_by_object_type(harness):
    asyncio.run(harness.upsert_object_set_async("customer_set", _payload(), allow_overwrite=False))
    asyncio.run(
        harness.upsert_object_set_async(
            "order_set",
            _payload(object_type_id=8, object_type_name="order", object_keys=["O1"]),
            allow_overwrite=False,
        )
    )
    names = [item["set_name"] for item in asyncio.run(harness.list_object_sets_async(8))]
    assert names == ["order_set"]


def test_get_missing_returns_none(harness):
    assert asyncio.run(harness.get_object_set_async("missing")) is None


def test_soft_delete_missing_returns_false(harness):
    assert asyncio.run(harness.soft_delete_object_set_async("missing")) is False
