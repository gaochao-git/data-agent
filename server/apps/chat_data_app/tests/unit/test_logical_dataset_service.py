"""Logical Dataset 服务：身份、版本、事务、分支和 member 绑定。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos import dao_session
from apps.chat_data_app.daos.logical_dataset_dao import LogicalDatasetDao
from apps.chat_data_app.services.dataset import dataset_service as service_module
from apps.chat_data_app.services.dataset.dataset_service import (
    DatasetInvalidError,
    DatasetNotFoundError,
    LogicalDatasetService,
)
from utils.models import Base


def _payload(**overrides):
    payload = {
        "display_name": "发货单数据集",
        "description": "多机构发货单",
        "primary_key": ["shipment_id"],
        "columns": [
            {
                "name": "shipment_id",
                "display_name": "发货单ID",
                "data_type": "string",
                "physical_column": "shipment_id",
            },
            {"name": "carrier", "display_name": "承运商", "data_type": "string", "physical_column": "carrier"},
        ],
        "bindings": [
            {"datasource_group_id": "g1", "member_datasource_id": "ORG_CODE1", "physical_table": "shipments"},
            {"datasource_group_id": "g1", "member_datasource_id": "ORG_CODE2", "physical_table": "shipments"},
        ],
        "status": "draft",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def dataset_service(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def create_tables():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def test_transaction_scope():
        active_session = dao_session._ORM_SESSION.get()
        if active_session is not None:
            yield active_session
            return
        async with session_factory() as session:
            token = dao_session._ORM_SESSION.set(session)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                dao_session._ORM_SESSION.reset(token)

    monkeypatch.setattr(service_module, "transaction_scope", test_transaction_scope)
    return LogicalDatasetService(LogicalDatasetDao())


def test_upsert_creates_version_transaction_and_bindings(dataset_service):
    result = asyncio.run(
        dataset_service.upsert_dataset_async("shipments", _payload(), operator="u1", operator_name="测试用户")
    )
    assert result["dataset_name"] == "shipments"
    assert result["primary_key"] == ["shipment_id"]
    assert result["current_version_id"] is not None
    assert [item["member_datasource_id"] for item in result["bindings"]] == ["ORG_CODE1", "ORG_CODE2"]
    assert result["versions"][0]["version_no"] == 1
    assert result["versions"][0]["status"] == "committed"
    assert result["branches"][0]["branch_name"] == "main"
    assert result["branches"][0]["is_default"] is True


def test_upsert_creates_new_version_and_lineage(dataset_service):
    first = asyncio.run(
        dataset_service.upsert_dataset_async("shipments", _payload(), operator="u1", operator_name="测试用户")
    )
    second = asyncio.run(
        dataset_service.upsert_dataset_async(
            "shipments",
            _payload(
                display_name="发货单数据集 V2",
                bindings=[
                    {"datasource_group_id": "g1", "member_datasource_id": "ORG_CODE1", "physical_table": "shipments"},
                ],
            ),
            operator="u1",
            operator_name="测试用户",
        )
    )
    assert second["display_name"] == "发货单数据集 V2"
    assert second["versions"][0]["version_no"] == 2
    assert [item["member_datasource_id"] for item in second["bindings"]] == ["ORG_CODE1"]
    assert first["versions"][0]["version_id"] != second["versions"][0]["version_id"]


def test_list_get_branch_and_delete(dataset_service):
    asyncio.run(dataset_service.upsert_dataset_async("shipments", _payload(), operator="u1", operator_name="测试用户"))
    listed = asyncio.run(dataset_service.list_datasets_async())
    assert listed[0]["dataset_name"] == "shipments"
    assert listed[0]["binding_count"] == 2
    branch = asyncio.run(
        dataset_service.create_branch_async(
            "shipments", branch_name="feature/test", base_version_id=None, operator="u1", operator_name="测试用户"
        )
    )
    assert branch["branch_name"] == "feature/test"
    detail = asyncio.run(dataset_service.get_dataset_async("shipments"))
    assert {item["branch_name"] for item in detail["branches"]} == {"main", "feature/test"}
    asyncio.run(dataset_service.delete_dataset_async("shipments"))
    with pytest.raises(DatasetNotFoundError):
        asyncio.run(dataset_service.get_dataset_async("shipments"))


def test_validation_errors(dataset_service):
    with pytest.raises(DatasetInvalidError, match="primary_key"):
        asyncio.run(
            dataset_service.upsert_dataset_async(
                "shipments", _payload(primary_key=[]), operator="u1", operator_name="测试用户"
            )
        )
    with pytest.raises(DatasetInvalidError, match="引用"):
        asyncio.run(
            dataset_service.upsert_dataset_async(
                "shipments", _payload(primary_key=["missing"]), operator="u1", operator_name="测试用户"
            )
        )
    with pytest.raises(DatasetInvalidError, match="不能重复"):
        asyncio.run(
            dataset_service.upsert_dataset_async(
                "shipments",
                _payload(
                    bindings=[
                        {
                            "datasource_group_id": "g1",
                            "member_datasource_id": "ORG_CODE1",
                            "physical_table": "shipments",
                        },
                        {
                            "datasource_group_id": "g1",
                            "member_datasource_id": "ORG_CODE1",
                            "physical_table": "shipments",
                        },
                    ]
                ),
                operator="u1",
                operator_name="测试用户",
            )
        )
    with pytest.raises(DatasetInvalidError, match="columns"):
        asyncio.run(
            dataset_service.upsert_dataset_async(
                "shipments", _payload(columns="bad"), operator="u1", operator_name="测试用户"
            )
        )
