"""Dataset ↔ 语义模型关联服务单测：校验、自动列对照、软删。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos import dao_session
from apps.chat_data_app.services.dataset import dataset_semantic_link_service as link_module
from apps.chat_data_app.services.dataset.dataset_semantic_link_service import (
    DatasetSemanticLinkInvalidError,
    DatasetSemanticLinkNotFoundError,
    DatasetSemanticLinkService,
)
from utils.models import Base

DATASET = {
    "id": 1,
    "dataset_name": "users",
    "columns": [
        {"name": "user_id", "data_type": "string", "physical_column": "user_id"},
        {"name": "username", "data_type": "string", "physical_column": "username"},
        {"name": "created_at", "data_type": "datetime", "physical_column": "created_at"},
    ],
    "bindings": [
        {
            "datasource_group_id": "g1",
            "member_datasource_id": "ORG_CODE1",
            "physical_table": "users",
        }
    ],
}

CONFIG = {
    "id": "sc_users",
    "name": "用户模型",
    "metrics": [
        {"name": "用户数", "expression": "COUNT(*)", "table": "users"},
        {
            "name": "近7日新增用户",
            "metric_kind": "derived",
            "base_metric": "用户数",
            "period": {"kind": "last_n_days", "n": 7, "time_dimension": "created_at"},
            "table": "users",
        },
    ],
    "dimensions": [
        {"name": "user_id", "table": "users", "column": "user_id"},
        {"name": "username", "table": "users", "column": "username"},
        {"name": "created_at", "table": "users", "column": "created_at"},
    ],
}


class _FakeConfigService:
    async def get_config_async(self, datasource_id: str, config_id: str, user_id: str = ""):
        if datasource_id != "g1" or config_id != "sc_users":
            return None
        return dict(CONFIG)


@pytest.fixture()
def harness(monkeypatch):
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

    monkeypatch.setattr(link_module, "transaction_scope", test_transaction_scope)

    async def fake_dataset(_name: str):
        return dict(DATASET)

    async def fake_dataset_by_id(dataset_id: int):
        return dict(DATASET) if int(dataset_id) == 1 else None

    monkeypatch.setattr(link_module.logical_dataset_service, "get_dataset_async", fake_dataset)
    monkeypatch.setattr(link_module.logical_dataset_service, "get_dataset_async_by_id_async", fake_dataset_by_id)
    monkeypatch.setattr(link_module, "get_semantic_config_service", lambda: _FakeConfigService())
    return DatasetSemanticLinkService()


def test_upsert_link_auto_maps_columns_and_scope(harness):
    async def run():
        view = await harness.upsert_link_async(
            "users",
            "sc_users",
            {
                "datasource_group_id": "g1",
                "table_name": "users",
                "column_mappings": {},
                "metric_scope": [],
                "lifecycle_status": "active",
            },
            operator="u1",
            operator_name="测试用户",
        )
        assert view["dataset_name"] == "users"
        assert view["semantic_config_name"] == "用户模型"
        assert view["column_mappings"] == {
            "user_id": "user_id",
            "username": "username",
            "created_at": "created_at",
        }
        assert view["metric_scope"] == ["用户数", "近7日新增用户"]
        assert view["lifecycle_status"] == "active"

    asyncio.run(run())


def test_upsert_rejects_unknown_metric_in_scope(harness):
    async def run():
        with pytest.raises(DatasetSemanticLinkInvalidError):
            await harness.upsert_link_async(
                "users",
                "sc_users",
                {
                    "datasource_group_id": "g1",
                    "table_name": "users",
                    "metric_scope": ["不存在的指标"],
                },
            )

    asyncio.run(run())


def test_upsert_rejects_metric_table_mismatch(harness):
    async def run():
        with pytest.raises(DatasetSemanticLinkInvalidError):
            await harness.upsert_link_async(
                "users",
                "sc_users",
                {
                    "datasource_group_id": "g1",
                    "table_name": "other_table",
                },
            )

    asyncio.run(run())


def test_upsert_rejects_unknown_column_mapping(harness):
    async def run():
        with pytest.raises(DatasetSemanticLinkInvalidError):
            await harness.upsert_link_async(
                "users",
                "sc_users",
                {
                    "datasource_group_id": "g1",
                    "table_name": "users",
                    "column_mappings": {"missing_column": "user_id"},
                },
            )

    asyncio.run(run())


def test_upsert_rejects_group_without_binding(harness):
    async def run():
        with pytest.raises(DatasetSemanticLinkInvalidError):
            await harness.upsert_link_async(
                "users",
                "sc_users",
                {"datasource_group_id": "g_missing", "table_name": "users"},
            )

    asyncio.run(run())


def test_delete_link_then_not_found(harness):
    async def run():
        await harness.upsert_link_async(
            "users",
            "sc_users",
            {"datasource_group_id": "g1", "table_name": "users", "lifecycle_status": "active"},
        )
        await harness.delete_link_async(
            "users", "sc_users", datasource_group_id="g1", operator="u1", operator_name="测试用户"
        )
        links = await harness.list_links_async("users", datasource_group_id="g1")
        assert links == []
        with pytest.raises(DatasetSemanticLinkNotFoundError):
            await harness.delete_link_async("users", "sc_users")

    asyncio.run(run())


def test_link_helpers_validate_table_and_scope():
    service = DatasetSemanticLinkService()
    bindings = DATASET["bindings"]
    with pytest.raises(DatasetSemanticLinkInvalidError, match="table_name"):
        service._resolve_table_name(DATASET, bindings, "other", CONFIG)
    config = {**CONFIG, "metrics": [{**CONFIG["metrics"][0], "table": "orders"}]}
    config["dimensions"] = []
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不存在于语义模型"):
        service._resolve_table_name(DATASET, bindings, "users", config)
    assert service._resolve_table_name(DATASET, bindings, "", CONFIG) == "users"

    metrics = CONFIG["metrics"]
    with pytest.raises(DatasetSemanticLinkInvalidError, match="字符串数组"):
        service._normalize_metric_scope("bad", metrics, "users")
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不存在"):
        service._normalize_metric_scope(["ghost"], metrics, "users")
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不属于该表"):
        service._normalize_metric_scope(["用户数"], [{**metrics[0], "table": "orders"}], "users")
    assert service._normalize_metric_scope(None, metrics, "users") == ["用户数", "近7日新增用户"]


def test_link_helpers_validate_column_mappings():
    service = DatasetSemanticLinkService()
    columns = DATASET["columns"]
    dimensions = CONFIG["dimensions"]
    with pytest.raises(DatasetSemanticLinkInvalidError, match="没有任何可对照"):
        service._normalize_column_mappings([], [], None, "users")
    with pytest.raises(DatasetSemanticLinkInvalidError, match="结构"):
        service._normalize_column_mappings(columns, dimensions, ["bad"], "users")
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不存在的 Dataset"):
        service._normalize_column_mappings(columns, dimensions, {"ghost": "user_id"}, "users")
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不存在的语义维度"):
        service._normalize_column_mappings(columns, dimensions, {"user_id": "ghost"}, "users")
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不属于该表"):
        service._normalize_column_mappings(
            columns,
            [{**dimensions[0], "table": "orders"}],
            {"user_id": "user_id"},
            "users",
        )
    assert service._normalize_column_mappings(columns, dimensions, {"user_id": "user_id"}, "users") == {
        "user_id": "user_id"
    }
    assert service._normalize_column_mappings(columns, dimensions, None, "users") == {
        "user_id": "user_id",
        "username": "username",
        "created_at": "created_at",
    }


def test_link_helpers_resolve_dimension_and_metric_kind():
    from apps.chat_data_app.services.dataset.dataset_semantic_link_service import (
        _metric_kind,
        _resolve_dimension_name,
    )

    dims = {item["name"]: item for item in CONFIG["dimensions"]}
    by_column = {item["column"]: item for item in CONFIG["dimensions"]}
    assert _resolve_dimension_name("user_id", dims, by_column, "users") == "user_id"
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不存在的语义维度"):
        _resolve_dimension_name("ghost", dims, by_column, "users")
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不属于该表"):
        _resolve_dimension_name(
            "user_id", {**dims, "user_id": {**dims["user_id"], "table": "orders"}}, by_column, "users"
        )
    assert _metric_kind({"metric_kind": "derived"}) == "derived"
    assert _metric_kind({"metric_kind": "composite"}) == "composite"
    assert _metric_kind({}) == "atomic"


def test_link_service_empty_and_missing_paths(monkeypatch):
    service = DatasetSemanticLinkService()
    with pytest.raises(DatasetSemanticLinkInvalidError, match="dataset_name"):
        asyncio.run(service._dataset_detail_async(""))

    async def missing_dataset(_name):
        raise DatasetSemanticLinkNotFoundError("missing")

    monkeypatch.setattr(link_module.logical_dataset_service, "get_dataset_async", missing_dataset)
    with pytest.raises(DatasetSemanticLinkNotFoundError):
        asyncio.run(service._dataset_detail_async("missing"))


def test_upsert_link_validation_and_read_paths(harness):
    service = harness
    payload = {
        "datasource_group_id": "g1",
        "table_name": "users",
        "metric_scope": ["用户数"],
        "column_mappings": {"user_id": "user_id"},
        "lifecycle_status": "active",
    }
    with pytest.raises(DatasetSemanticLinkInvalidError, match="semantic_config_id"):
        asyncio.run(service.upsert_link_async("users", "", payload))
    with pytest.raises(DatasetSemanticLinkInvalidError, match="datasource_group_id"):
        asyncio.run(service.upsert_link_async("users", "sc_users", {**payload, "datasource_group_id": ""}))
    with pytest.raises(DatasetSemanticLinkInvalidError, match="lifecycle_status"):
        asyncio.run(service.upsert_link_async("users", "sc_users", {**payload, "lifecycle_status": "bad"}))
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不存在语义模型"):
        asyncio.run(service.upsert_link_async("users", "missing", payload))

    saved = asyncio.run(service.upsert_link_async("users", "sc_users", payload))
    links = asyncio.run(service.list_links_async("users"))
    assert len(links) == 1
    by_id = asyncio.run(service.list_links_for_dataset_id_async(1))
    assert by_id[0]["semantic_config_name"] == "用户模型"
    active = asyncio.run(service.get_active_link_async(1, "g1"))
    assert active is not None
    assert asyncio.run(service.get_active_link_async(999, "g1")) is None
    assert asyncio.run(service.get_active_link_async(1, "g1", semantic_config_id="missing")) is None

    with pytest.raises(DatasetSemanticLinkInvalidError, match="不属于指定数据源组"):
        asyncio.run(service.delete_link_async("users", saved["semantic_config_id"], datasource_group_id="g2"))
    asyncio.run(service.delete_link_async("users", saved["semantic_config_id"], datasource_group_id="g1"))
    with pytest.raises(DatasetSemanticLinkNotFoundError):
        asyncio.run(service.delete_link_async("users", "sc_users"))


def test_link_config_read_helpers(monkeypatch):
    service = DatasetSemanticLinkService()
    assert asyncio.run(service._load_config_async("", "sc")) is None
    assert asyncio.run(service._load_config_async("g1", "")) is None

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    class FakeService:
        get_config_async = staticmethod(boom)

    monkeypatch.setattr(link_module, "get_semantic_config_service", lambda: FakeService())
    assert asyncio.run(service._load_config_async("g1", "sc")) is None
    with pytest.raises(DatasetSemanticLinkInvalidError, match="不存在语义模型"):
        asyncio.run(service._require_config_async("g1", "sc"))

    class BadStructure:
        async def get_config_async(self, *_args, **_kwargs):
            return {"metrics": [], "dimensions": None}

    monkeypatch.setattr(link_module, "get_semantic_config_service", lambda: BadStructure())
    with pytest.raises(DatasetSemanticLinkInvalidError, match="结构不完整"):
        asyncio.run(service._require_config_async("g1", "sc", user_id=""))


def test_link_view_with_missing_dataset_and_empty_link(harness):
    service = harness
    assert asyncio.run(service.list_links_for_dataset_id_async(999)) == []
    assert asyncio.run(service.load_config_for_link_async({})) is None
    columns = [{"name": "user_id"}, {"name": "other"}]
    dimensions = [
        {"name": "user_id", "table": "users", "column": "user_id"},
        {"name": "other", "table": "orders", "column": "other"},
    ]
    assert service._normalize_column_mappings(columns, dimensions, None, "users") == {"user_id": "user_id"}
