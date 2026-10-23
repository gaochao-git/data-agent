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


def test_service_empty_names_and_missing_dataset(dataset_service):
    with pytest.raises(DatasetInvalidError, match="dataset_name"):
        asyncio.run(dataset_service.get_dataset_async(""))
    with pytest.raises(DatasetInvalidError, match="dataset_name"):
        asyncio.run(dataset_service.upsert_dataset_async("", _payload(), operator="u1", operator_name="测试用户"))
    with pytest.raises(DatasetInvalidError, match="dataset_name"):
        asyncio.run(
            dataset_service.create_branch_async(
                "", branch_name="a", base_version_id=None, operator="u1", operator_name="测试用户"
            )
        )
    with pytest.raises(DatasetNotFoundError):
        asyncio.run(
            dataset_service.create_branch_async(
                "missing", branch_name="a", base_version_id=None, operator="u1", operator_name="测试用户"
            )
        )
    with pytest.raises(DatasetNotFoundError):
        asyncio.run(dataset_service.delete_dataset_async("missing"))


def test_upsert_with_previous_version_records_lineage(dataset_service):
    first = asyncio.run(
        dataset_service.upsert_dataset_async("shipments", _payload(), operator="u1", operator_name="测试用户")
    )
    second = asyncio.run(
        dataset_service.upsert_dataset_async(
            "shipments",
            _payload(previous_version_id=first["versions"][0]["version_id"]),
            operator="u1",
            operator_name="测试用户",
        )
    )
    assert second["versions"][0]["version_no"] == 2


def test_invalid_bindings_type(dataset_service):
    with pytest.raises(DatasetInvalidError, match="bindings"):
        asyncio.run(
            dataset_service.upsert_dataset_async(
                "shipments", _payload(bindings="bad"), operator="u1", operator_name="测试用户"
            )
        )


def test_dataset_dao_helpers_cover_invalid_json():
    from apps.chat_data_app.daos.logical_dataset_dao import _format_time, _json_text, _parse_json

    assert _json_text(object(), {"fallback": True}) == '{"fallback": true}'
    assert _parse_json("not-json", list) == []
    assert _parse_json(["x"], list) == ["x"]
    assert _format_time(None) == ""


def test_bootstrap_from_datasource_registers_dataset_and_bindings(dataset_service, monkeypatch):
    class _FakeManager:
        async def get_all_datasources_async(self, include_tables=True):
            return [
                {
                    "group_id": "g1",
                    "member_datasource_ids": ["ORG_CODE1", "ORG_CODE2"],
                    "ds_tables": [
                        {
                            "table_name": "shipments",
                            "description": "发货单",
                            "business_desc": "发货单",
                            "primary_keys": ["shipment_id"],
                            "columns": [
                                {"name": "shipment_id", "type": "string", "primary_key": True},
                                {"name": "carrier", "type": "string"},
                            ],
                        }
                    ],
                }
            ]

    monkeypatch.setattr(service_module, "get_datasource_manager", lambda: _FakeManager())
    result = asyncio.run(
        dataset_service.bootstrap_from_datasource_async(
            "g1", table_names=["shipments"], operator="u1", operator_name="测试用户"
        )
    )
    assert len(result) == 1
    assert result[0]["dataset_name"] == "shipments"
    assert result[0]["primary_key"] == ["shipment_id"]
    assert [item["member_datasource_id"] for item in result[0]["bindings"]] == ["ORG_CODE1", "ORG_CODE2"]


def test_bootstrap_from_datasource_rejects_empty_group_and_tables(dataset_service, monkeypatch):
    class _EmptyManager:
        async def get_all_datasources_async(self, include_tables=True):
            return [{"group_id": "g1", "member_datasource_ids": [], "ds_tables": []}]

    monkeypatch.setattr(service_module, "get_datasource_manager", lambda: _EmptyManager())
    with pytest.raises(DatasetInvalidError, match="datasource_group_id"):
        asyncio.run(dataset_service.bootstrap_from_datasource_async("", operator="u1", operator_name="测试用户"))
    with pytest.raises(DatasetInvalidError, match="成员"):
        asyncio.run(dataset_service.bootstrap_from_datasource_async("g1", operator="u1", operator_name="测试用户"))
    with pytest.raises(DatasetNotFoundError, match="数据源组"):
        asyncio.run(dataset_service.bootstrap_from_datasource_async("missing", operator="u1", operator_name="测试用户"))


def test_get_dataset_by_id_missing_returns_none(dataset_service):
    assert asyncio.run(dataset_service.get_dataset_async_by_id_async(999999)) is None
