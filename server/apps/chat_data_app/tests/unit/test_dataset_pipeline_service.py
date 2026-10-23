"""Dataset Pipeline：定义版本、build、输出 Dataset 版本/事务/血缘。"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos import dao_session
from apps.chat_data_app.daos.logical_dataset_dao import DatasetPipelineDao, LogicalDatasetDao
from apps.chat_data_app.services.dataset import dataset_service as dataset_service_module
from apps.chat_data_app.services.dataset import pipeline_runner as runner_module
from apps.chat_data_app.services.dataset import pipeline_service as pipeline_service_module
from apps.chat_data_app.services.dataset.dataset_service import DatasetInvalidError, LogicalDatasetService
from apps.chat_data_app.services.dataset.pipeline_errors import (
    PipelineBuildError,
    PipelineInvalidError,
    PipelineNotFoundError,
)
from apps.chat_data_app.services.dataset.pipeline_service import DatasetPipelineService
from apps.chat_data_app.services.dataset.pipeline_transforms import (
    TransformError,
    apply_transforms,
    normalize_transform_config,
)
from utils.models import (
    Base,
    ChatDataDatasetLineageEdge,
    ChatDataDatasetTransaction,
    ChatDataDatasetVersion,
    ChatDataPipelineDefinition,
)


def _source_payload(**overrides):
    payload = {
        "display_name": "发货单数据集",
        "description": "多机构发货单",
        "primary_key": ["shipment_id"],
        "columns": [
            {"name": "shipment_id", "data_type": "string", "physical_column": "shipment_id"},
            {"name": "carrier", "data_type": "string", "physical_column": "carrier"},
            {"name": "amount", "data_type": "number", "physical_column": "amount"},
            {"name": "status", "data_type": "string", "physical_column": "status"},
        ],
        "bindings": [
            {"datasource_group_id": "g1", "member_datasource_id": "ORG_CODE1", "physical_table": "shipments"},
        ],
        "status": "draft",
    }
    payload.update(overrides)
    return payload


def _pipeline_payload(**overrides):
    payload = {
        "display_name": "发货单清洗",
        "description": "过滤已发货",
        "target_layer": "cleaned",
        "status": "experimental",
        "nodes": [
            {"node_id": "src", "node_type": "source", "config": {"dataset_name": "shipments"}},
            {
                "node_id": "clean",
                "node_type": "transform",
                "config": {
                    "transform_type": "filter",
                    "column": "status",
                    "operator": "eq",
                    "value": "shipped",
                },
            },
            {"node_id": "out", "node_type": "output", "config": {"dataset_name": "shipments_cleaned"}},
        ],
        "edges": [
            {"edge_id": "e1", "from_node_id": "src", "to_node_id": "clean"},
            {"edge_id": "e2", "from_node_id": "clean", "to_node_id": "out"},
        ],
    }
    payload.update(overrides)
    return payload


class _Harness:
    def __init__(self, dataset_service, pipeline_service, session_factory):
        self.dataset_service = dataset_service
        self.pipeline_service = pipeline_service
        self.session_factory = session_factory


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

    monkeypatch.setattr(dataset_service_module, "transaction_scope", test_transaction_scope)
    monkeypatch.setattr(pipeline_service_module, "transaction_scope", test_transaction_scope)
    dataset_service = LogicalDatasetService(LogicalDatasetDao())
    pipeline_service = DatasetPipelineService(
        dao=DatasetPipelineDao(),
        dataset_service=dataset_service,
    )
    return _Harness(dataset_service, pipeline_service, session_factory)


async def _fake_rows(_dataset, **_kwargs):
    return [
        {"shipment_id": "S1", "carrier": "顺丰", "amount": "10", "status": "shipped"},
        {"shipment_id": "S2", "carrier": "顺丰", "amount": "20", "status": "pending"},
        {"shipment_id": "S3", "carrier": "德邦", "amount": "30", "status": "shipped"},
    ]


def _count_rows(session_factory, model):
    async def run():
        async with session_factory() as session:
            return list((await session.execute(select(model))).scalars().all())

    return asyncio.run(run())


def test_pipeline_definition_versions_are_append_only(harness):
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    first = asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    assert first["pipeline_name"] == "shipments_clean"
    assert first["current_version_no"] == 1
    assert first["target_layer"] == "cleaned"
    assert first["output_dataset_name"] == "shipments_cleaned"
    assert [node["node_id"] for node in first["nodes"]] == ["src", "clean", "out"]
    assert [edge["edge_id"] for edge in first["edges"]] == ["e1", "e2"]

    second = asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean",
            _pipeline_payload(target_layer="enriched"),
            operator="u1",
            operator_name="测试",
        )
    )
    assert second["current_version_no"] == 2
    assert second["pipeline_id"] == first["pipeline_id"]
    # 历史版本节点不会被覆盖

    async def load_v1():
        async with harness.session_factory() as session:
            return await DatasetPipelineDao().list_nodes(session, first["pipeline_id"], 1)

    assert len(asyncio.run(load_v1())) == 3


def test_build_creates_output_dataset_version_transaction_and_lineage(harness, monkeypatch):
    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_rows)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    result = asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    assert result["run_status"] == "succeeded"
    assert result["pipeline_version"] == 1
    assert result["output_summary"]["row_count"] == 2
    assert [item["name"] for item in result["output_dataset"]["columns"]] == [
        "shipment_id",
        "carrier",
        "amount",
        "status",
    ]
    output = result["output_dataset"]
    assert output["dataset_origin"] == "pipeline"
    assert output["dataset_layer"] == "cleaned"
    assert output["versions"][0]["version_no"] == 1
    assert output["versions"][0]["row_count"] == 2
    assert output["primary_key"] == ["shipment_id"]
    assert output["bindings"] == []

    versions = _count_rows(harness.session_factory, ChatDataDatasetVersion)
    transactions = _count_rows(harness.session_factory, ChatDataDatasetTransaction)
    lineage = _count_rows(harness.session_factory, ChatDataDatasetLineageEdge)
    output_lineage = [item for item in lineage if item.operation == "pipeline_build"]
    output_transaction = [item for item in transactions if item.run_id == result["run_id"]]
    assert len(output_transaction) == 1
    assert output_transaction[0].transaction_status == "committed"
    assert len(output_lineage) == 1
    assert output_lineage[0].run_id == result["run_id"]
    # 输入 source Dataset + 输出 pipeline Dataset 各 1 个版本
    assert len(versions) == 2
    assert output_lineage[0].to_version_id == output["versions"][0]["version_id"]

    listed = asyncio.run(
        harness.dataset_service.list_datasets_async(dataset_layer="cleaned", dataset_origin="pipeline")
    )
    assert [item["dataset_name"] for item in listed] == ["shipments_cleaned"]
    sources = asyncio.run(harness.dataset_service.list_datasets_async(dataset_layer="source"))
    assert [item["dataset_name"] for item in sources] == ["shipments"]

    # 再次 build 追加不可变版本，旧版本仍在
    result2 = asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    assert result2["output_dataset"]["versions"][0]["version_no"] == 2
    assert len(result2["output_dataset"]["versions"]) == 2
    runs = asyncio.run(harness.pipeline_service.list_runs_async("shipments_clean"))
    assert [item["run_status"] for item in runs] == ["succeeded", "succeeded"]


def test_build_aggregate_transform(harness, monkeypatch):
    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_rows)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    payload = _pipeline_payload(
        target_layer="enriched",
        nodes=[
            {"node_id": "src", "node_type": "source", "config": {"dataset_name": "shipments"}},
            {
                "node_id": "agg",
                "node_type": "transform",
                "config": {
                    "transform_type": "aggregate",
                    "group_by": ["carrier"],
                    "metrics": [{"column": "amount", "function": "sum", "alias": "total_amount"}],
                },
            },
            {
                "node_id": "out",
                "node_type": "output",
                "config": {"dataset_name": "shipments_by_carrier", "layer": "enriched"},
            },
        ],
        edges=[
            {"edge_id": "e1", "from_node_id": "src", "to_node_id": "agg"},
            {"edge_id": "e2", "from_node_id": "agg", "to_node_id": "out"},
        ],
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async("shipments_agg", payload, operator="u1", operator_name="测试")
    )
    result = asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "shipments_agg", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    assert result["run_status"] == "succeeded"
    assert result["output_summary"]["row_count"] == 2
    assert [item["name"] for item in result["output_dataset"]["columns"]] == ["carrier", "total_amount"]
    assert result["output_dataset"]["dataset_layer"] == "enriched"
    assert result["output_dataset"]["dataset_origin"] == "pipeline"


def test_build_failure_marks_run_failed_without_output_version(harness, monkeypatch):
    async def _boom(_dataset, **_kwargs):
        raise PipelineBuildError("数据源不可用")

    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _boom)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    with pytest.raises(PipelineBuildError, match="数据源不可用"):
        asyncio.run(
            harness.pipeline_service.build_pipeline_async(
                "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
            )
        )
    runs = asyncio.run(harness.pipeline_service.list_runs_async("shipments_clean"))
    assert runs[0]["run_status"] == "failed"
    assert "数据源不可用" in runs[0]["error_message"]
    versions = _count_rows(harness.session_factory, ChatDataDatasetVersion)
    assert len(versions) == 1  # 只有 source Dataset 版本，没有输出版本
    assert asyncio.run(harness.dataset_service.get_dataset_async_by_id_async(1)) is not None

    detail = asyncio.run(harness.pipeline_service.get_run_async(runs[0]["run_id"]))
    assert detail["run_status"] == "failed"
    assert detail["output_dataset"] is None


def test_build_failure_unknown_error_is_sanitized(harness, monkeypatch):
    async def _boom(_dataset, **_kwargs):
        raise RuntimeError("internal secret stack")

    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _boom)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    with pytest.raises(PipelineBuildError, match="服务端日志"):
        asyncio.run(
            harness.pipeline_service.build_pipeline_async(
                "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
            )
        )


def test_definition_validation_errors(harness):
    with pytest.raises(PipelineInvalidError, match="pipeline_name"):
        asyncio.run(harness.pipeline_service.get_pipeline_async(""))
    with pytest.raises(PipelineInvalidError, match="transform"):
        asyncio.run(
            harness.pipeline_service.upsert_pipeline_async(
                "bad",
                _pipeline_payload(
                    nodes=[
                        {"node_id": "src", "node_type": "source", "config": {"dataset_name": "shipments"}},
                        {"node_id": "out", "node_type": "output", "config": {"dataset_name": "x"}},
                    ],
                    edges=[{"from_node_id": "src", "to_node_id": "out"}],
                ),
                operator="u1",
                operator_name="测试",
            )
        )
    with pytest.raises(PipelineInvalidError, match="环"):
        asyncio.run(
            harness.pipeline_service.upsert_pipeline_async(
                "bad",
                _pipeline_payload(
                    edges=[
                        {"from_node_id": "src", "to_node_id": "clean"},
                        {"from_node_id": "clean", "to_node_id": "src"},
                    ]
                ),
                operator="u1",
                operator_name="测试",
            )
        )
    with pytest.raises(PipelineInvalidError, match="target_layer"):
        asyncio.run(
            harness.pipeline_service.upsert_pipeline_async(
                "bad", _pipeline_payload(target_layer="raw"), operator="u1", operator_name="测试"
            )
        )
    with pytest.raises(PipelineInvalidError, match="transform"):
        asyncio.run(
            harness.pipeline_service.upsert_pipeline_async(
                "bad",
                _pipeline_payload(
                    nodes=[
                        {"node_id": "src", "node_type": "source", "config": {"dataset_name": "shipments"}},
                        {
                            "node_id": "clean",
                            "node_type": "transform",
                            "config": {"transform_type": "unknown"},
                        },
                        {"node_id": "out", "node_type": "output", "config": {"dataset_name": "x"}},
                    ]
                ),
                operator="u1",
                operator_name="测试",
            )
        )
    with pytest.raises(PipelineInvalidError, match="source 节点缺少 dataset_name"):
        asyncio.run(
            harness.pipeline_service.upsert_pipeline_async(
                "bad",
                _pipeline_payload(
                    nodes=[
                        {"node_id": "src", "node_type": "source", "config": {}},
                        {
                            "node_id": "clean",
                            "node_type": "transform",
                            "config": {"transform_type": "filter", "column": "a", "value": 1},
                        },
                        {"node_id": "out", "node_type": "output", "config": {"dataset_name": "x"}},
                    ]
                ),
                operator="u1",
                operator_name="测试",
            )
        )


def test_pipeline_lifecycle_and_missing(harness):
    with pytest.raises(PipelineNotFoundError):
        asyncio.run(harness.pipeline_service.get_pipeline_async("missing"))
    with pytest.raises(PipelineNotFoundError):
        asyncio.run(harness.pipeline_service.delete_pipeline_async("missing"))
    with pytest.raises(PipelineNotFoundError):
        asyncio.run(harness.pipeline_service.list_runs_async("missing"))
    with pytest.raises(PipelineNotFoundError):
        asyncio.run(harness.pipeline_service.get_run_async("missing"))
    with pytest.raises(PipelineInvalidError):
        asyncio.run(
            harness.pipeline_service.build_pipeline_async(
                "", operator="u1", operator_name="测试", user_id="u1", username="测试"
            )
        )
    # 无定义版本时 build 应报定义不合法
    pipeline_service = harness.pipeline_service
    asyncio.run(pipeline_service.upsert_pipeline_async("p1", _pipeline_payload(), operator="u1", operator_name="测试"))
    assert [item["pipeline_name"] for item in asyncio.run(pipeline_service.list_pipelines_async())] == ["p1"]
    asyncio.run(pipeline_service.delete_pipeline_async("p1"))
    with pytest.raises(PipelineNotFoundError):
        asyncio.run(pipeline_service.get_pipeline_async("p1"))


def test_build_input_dataset_missing(harness, monkeypatch):
    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_rows)
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    with pytest.raises(PipelineBuildError, match="Dataset 不存在"):
        asyncio.run(
            harness.pipeline_service.build_pipeline_async(
                "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
            )
        )


def test_pipeline_run_detail_exposes_output_dataset(harness, monkeypatch):
    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_rows)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    run = asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    detail = asyncio.run(harness.pipeline_service.get_run_async(run["run_id"]))
    assert detail["pipeline_name"] == "shipments_clean"
    assert detail["output_dataset"]["dataset_name"] == "shipments_cleaned"
    pipeline = asyncio.run(harness.pipeline_service.get_pipeline_async("shipments_clean"))
    assert pipeline["runs"][0]["run_id"] == run["run_id"]
    listed = asyncio.run(harness.pipeline_service.list_pipelines_async())
    assert listed[0]["output_dataset_name"] == "shipments_cleaned"
    assert listed[0]["last_run"]["run_status"] == "succeeded"


# ---------------------------------------------------------------------------
# transform 纯函数
# ---------------------------------------------------------------------------


def _transform_columns():
    return [
        {"name": "shipment_id", "data_type": "string"},
        {"name": "carrier", "data_type": "string"},
        {"name": "amount", "data_type": "number"},
        {"name": "status", "data_type": "string"},
    ]


def _transform_rows():
    return [
        {"shipment_id": "S1", "carrier": "顺丰", "amount": 10, "status": "shipped"},
        {"shipment_id": "S2", "carrier": "顺丰", "amount": 20, "status": "pending"},
        {"shipment_id": "S3", "carrier": "德邦", "amount": 30, "status": "shipped"},
    ]


def test_transform_filter_operators():
    config = normalize_transform_config(
        {"transform_type": "filter", "conditions": [{"column": "amount", "operator": "gte", "value": "20"}]},
        _transform_columns(),
    )
    rows, columns = apply_transforms(_transform_rows(), _transform_columns(), [config])
    assert [row["shipment_id"] for row in rows] == ["S2", "S3"]
    assert columns == _transform_columns()

    contains = normalize_transform_config(
        {"transform_type": "filter", "column": "carrier", "operator": "contains", "value": "德"},
        _transform_columns(),
    )
    rows, _ = apply_transforms(_transform_rows(), _transform_columns(), [contains])
    assert [row["shipment_id"] for row in rows] == ["S3"]

    ne = normalize_transform_config(
        {"transform_type": "filter", "column": "status", "operator": "ne", "value": "shipped"},
        _transform_columns(),
    )
    rows, _ = apply_transforms(_transform_rows(), _transform_columns(), [ne])
    assert [row["shipment_id"] for row in rows] == ["S2"]


def test_transform_aggregate_and_multiple_transforms():
    first = normalize_transform_config(
        {"transform_type": "filter", "column": "status", "operator": "eq", "value": "shipped"},
        _transform_columns(),
    )
    second = normalize_transform_config(
        {
            "transform_type": "aggregate",
            "group_by": ["carrier"],
            "metrics": [
                {"column": "amount", "function": "sum", "alias": "total_amount"},
                {"column": "shipment_id", "function": "count", "alias": "cnt"},
            ],
        },
        _transform_columns(),
    )
    rows, columns = apply_transforms(_transform_rows(), _transform_columns(), [first, second])
    assert rows == [
        {"carrier": "顺丰", "total_amount": 10.0, "cnt": 1},
        {"carrier": "德邦", "total_amount": 30.0, "cnt": 1},
    ]
    assert [item["name"] for item in columns] == ["carrier", "total_amount", "cnt"]


def test_transform_validation_errors():
    with pytest.raises(TransformError, match="transform_type"):
        normalize_transform_config({"transform_type": "missing"})
    with pytest.raises(TransformError, match="filter.column"):
        normalize_transform_config({"transform_type": "filter", "value": 1}, _transform_columns())
    with pytest.raises(TransformError, match="filter.operator"):
        normalize_transform_config(
            {"transform_type": "filter", "column": "shipment_id", "operator": "like", "value": "x"},
            _transform_columns(),
        )
    with pytest.raises(TransformError, match="不存在的列"):
        normalize_transform_config({"transform_type": "filter", "column": "nope", "value": "x"}, _transform_columns())
    with pytest.raises(TransformError, match="aggregate.metrics"):
        normalize_transform_config({"transform_type": "aggregate"}, _transform_columns())
    with pytest.raises(TransformError, match="不合法"):
        normalize_transform_config(
            {"transform_type": "aggregate", "metrics": [{"column": "amount", "function": "unknown"}]},
            _transform_columns(),
        )
    with pytest.raises(TransformError, match="不能为空"):
        normalize_transform_config({"transform_type": "filter", "column": "", "value": 1})


def test_transform_aggregate_empty_rows_without_group_by():
    config = normalize_transform_config(
        {"transform_type": "aggregate", "metrics": [{"column": "amount", "function": "count", "alias": "cnt"}]},
        _transform_columns(),
    )
    rows, columns = apply_transforms([], _transform_columns(), [config])
    assert rows == [{"cnt": 0}]
    assert [item["name"] for item in columns] == ["cnt"]


# ---------------------------------------------------------------------------
# runner 适配层
# ---------------------------------------------------------------------------


def _dataset_with_binding(**overrides):
    dataset = {
        "dataset_name": "shipments",
        "columns": [
            {"name": "shipment_id", "physical_column": "shipment_id"},
            {"name": "carrier", "physical_column": "carrier_name"},
        ],
        "bindings": [
            {"datasource_group_id": "g1", "member_datasource_id": "ORG_CODE1", "physical_table": "shipments"},
        ],
    }
    dataset.update(overrides)
    return dataset


def test_runner_rejects_missing_or_ambiguous_binding():
    with pytest.raises(PipelineBuildError, match="member binding"):
        asyncio.run(runner_module.load_dataset_rows_async(_dataset_with_binding(bindings=[])))
    with pytest.raises(PipelineBuildError, match="多个 member"):
        asyncio.run(
            runner_module.load_dataset_rows_async(
                _dataset_with_binding(
                    bindings=[
                        {"datasource_group_id": "g1", "member_datasource_id": "A", "physical_table": "shipments"},
                        {"datasource_group_id": "g1", "member_datasource_id": "B", "physical_table": "shipments"},
                    ]
                )
            )
        )
    with pytest.raises(PipelineBuildError, match="缺少"):
        asyncio.run(
            runner_module.load_dataset_rows_async(
                _dataset_with_binding(
                    bindings=[{"datasource_group_id": "", "member_datasource_id": "", "physical_table": "t"}]
                )
            )
        )


def test_runner_rejects_unsafe_table(monkeypatch):
    async def _fake_target(group_id, member_id, **_kwargs):
        return {"member_datasource_id": member_id}

    monkeypatch.setattr(runner_module, "_select_execution_target", _fake_target)
    with pytest.raises(PipelineBuildError, match="physical_table"):
        asyncio.run(
            runner_module.load_dataset_rows_async(
                _dataset_with_binding(
                    bindings=[{"datasource_group_id": "g1", "member_datasource_id": "A", "physical_table": "t; drop"}]
                )
            )
        )


def test_runner_executes_and_maps_rows(monkeypatch):
    captured = {}

    async def _fake_target(group_id, member_id, **_kwargs):
        captured["target_called_with"] = (group_id, member_id)
        return {"member_datasource_id": member_id}

    async def _fake_execute(target, sql, **kwargs):
        captured["sql"] = sql
        captured["kwargs"] = kwargs
        return {
            "status": "succeeded",
            "data": [{"shipment_id": "S1", "carrier_name": "顺丰"}],
        }

    monkeypatch.setattr(runner_module, "_select_execution_target", _fake_target)
    monkeypatch.setattr(runner_module, "_execute_member_sql", _fake_execute)
    rows = asyncio.run(
        runner_module.load_dataset_rows_async(_dataset_with_binding(), user_id="u1", username="测试", limit=10)
    )
    assert rows == [{"shipment_id": "S1", "carrier": "顺丰"}]
    assert captured["sql"] == "SELECT * FROM shipments"
    assert captured["target_called_with"] == ("g1", "ORG_CODE1")
    assert captured["kwargs"]["limit"] == 10


def test_runner_failed_result(monkeypatch):
    async def _fake_target(group_id, member_id, **_kwargs):
        return {"member_datasource_id": member_id}

    async def _fake_execute(target, sql, **kwargs):
        return {"status": "failed", "error": "连接超时"}

    monkeypatch.setattr(runner_module, "_select_execution_target", _fake_target)
    monkeypatch.setattr(runner_module, "_execute_member_sql", _fake_execute)
    with pytest.raises(PipelineBuildError, match="连接超时"):
        asyncio.run(runner_module.load_dataset_rows_async(_dataset_with_binding()))


def test_runner_rejects_unauthorized_member(monkeypatch):
    class _Manager:
        async def list_authorized_execution_targets_async(self, group_id, **_kwargs):
            return []

    monkeypatch.setattr(runner_module, "get_datasource_manager", lambda: _Manager())
    with pytest.raises(PipelineBuildError, match="无权执行"):
        asyncio.run(runner_module.load_dataset_rows_async(_dataset_with_binding()))


# ---------------------------------------------------------------------------
# Dataset layer/origin 扩展
# ---------------------------------------------------------------------------


def test_dataset_layer_origin_defaults_and_validation(harness):
    created = asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    assert created["dataset_layer"] == "source"
    assert created["dataset_origin"] == "datasource"
    with pytest.raises(DatasetInvalidError, match="dataset_layer"):
        asyncio.run(
            harness.dataset_service.upsert_dataset_async(
                "shipments",
                _source_payload(dataset_layer="raw"),
                operator="u1",
                operator_name="测试",
            )
        )
    with pytest.raises(DatasetInvalidError, match="dataset_origin"):
        asyncio.run(
            harness.dataset_service.upsert_dataset_async(
                "shipments",
                _source_payload(dataset_origin="manual"),
                operator="u1",
                operator_name="测试",
            )
        )
    with pytest.raises(DatasetInvalidError, match="dataset_layer"):
        asyncio.run(harness.dataset_service.list_datasets_async(dataset_layer="raw"))


def test_commit_pipeline_output_requires_layer(harness):
    with pytest.raises(DatasetInvalidError, match="dataset_layer"):
        asyncio.run(
            harness.dataset_service.commit_pipeline_output_async(
                "out",
                {"columns": [{"name": "a"}], "bindings": []},
                run_id="run-1",
                input_version_ids=[],
                operation="pipeline_build",
                row_count=0,
                operator="u1",
                operator_name="测试",
            )
        )
    with pytest.raises(DatasetInvalidError, match="dataset_name"):
        asyncio.run(
            harness.dataset_service.commit_pipeline_output_async(
                "",
                {"columns": [], "bindings": [], "dataset_layer": "cleaned"},
                run_id="run-1",
                input_version_ids=[],
                operation="pipeline_build",
                row_count=0,
                operator="u1",
                operator_name="测试",
            )
        )


def test_lineage_metadata_is_json(harness, monkeypatch):
    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_rows)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    lineage = [
        item
        for item in _count_rows(harness.session_factory, ChatDataDatasetLineageEdge)
        if item.operation == "pipeline_build"
    ]
    metadata = json.loads(lineage[0].edge_metadata_json)
    assert metadata["created"] is True


def test_pipeline_output_cannot_overwrite_datasource_dataset(harness, monkeypatch):
    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_rows)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    payload = _pipeline_payload(
        nodes=[
            {"node_id": "src", "node_type": "source", "config": {"dataset_name": "shipments"}},
            {
                "node_id": "clean",
                "node_type": "transform",
                "config": {"transform_type": "filter", "column": "status", "value": "shipped"},
            },
            {"node_id": "out", "node_type": "output", "config": {"dataset_name": "shipments", "layer": "cleaned"}},
        ],
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async("overwrite", payload, operator="u1", operator_name="测试")
    )
    with pytest.raises(PipelineBuildError, match="不能覆盖 datasource Dataset"):
        asyncio.run(
            harness.pipeline_service.build_pipeline_async(
                "overwrite", operator="u1", operator_name="测试", user_id="u1", username="测试"
            )
        )
    source = asyncio.run(harness.dataset_service.get_dataset_async("shipments"))
    assert source["dataset_origin"] == "datasource"
    assert len(source["versions"]) == 1


def test_chained_cleaned_then_enriched_pipeline(harness, monkeypatch):
    async def _fake_loader(dataset, **_kwargs):
        if dataset["dataset_name"] == "shipments_cleaned":
            return [
                {"shipment_id": "S1", "carrier": "顺丰", "amount": "10", "status": "shipped"},
                {"shipment_id": "S3", "carrier": "德邦", "amount": "30", "status": "shipped"},
            ]
        return await _fake_rows(dataset, **_kwargs)

    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_loader)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "cleaned_pipeline", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    cleaned_run = asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "cleaned_pipeline", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    enriched_payload = _pipeline_payload(
        display_name="发货单聚合",
        target_layer="enriched",
        nodes=[
            {"node_id": "src", "node_type": "source", "config": {"dataset_name": "shipments_cleaned"}},
            {
                "node_id": "agg",
                "node_type": "transform",
                "config": {
                    "transform_type": "aggregate",
                    "group_by": ["carrier"],
                    "metrics": [{"column": "amount", "function": "sum", "alias": "total_amount"}],
                },
            },
            {
                "node_id": "out",
                "node_type": "output",
                "config": {"dataset_name": "shipments_enriched", "layer": "enriched"},
            },
        ],
        edges=[
            {"edge_id": "e1", "from_node_id": "src", "to_node_id": "agg"},
            {"edge_id": "e2", "from_node_id": "agg", "to_node_id": "out"},
        ],
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "enriched_pipeline", enriched_payload, operator="u1", operator_name="测试"
        )
    )
    enriched_run = asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "enriched_pipeline", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    assert enriched_run["run_status"] == "succeeded"
    output = enriched_run["output_dataset"]
    assert output["dataset_layer"] == "enriched"
    assert output["dataset_origin"] == "pipeline"
    assert output["dataset_name"] == "shipments_enriched"
    assert enriched_run["output_summary"]["row_count"] == 2

    lineage = [
        item
        for item in _count_rows(harness.session_factory, ChatDataDatasetLineageEdge)
        if item.operation == "pipeline_build"
    ]
    assert len(lineage) == 2
    cleaned_version_id = cleaned_run["output_dataset"]["versions"][0]["version_id"]
    enriched_lineage = next(item for item in lineage if item.to_version_id == output["versions"][0]["version_id"])
    assert enriched_lineage.from_version_id == cleaned_version_id


# ---------------------------------------------------------------------------
# Pipeline 定义校验矩阵（直接调用纯校验逻辑，避免落库开销）
# ---------------------------------------------------------------------------

_SOURCE_NODE = {"node_type": "source", "node_id": "src", "node_name": "源", "config": {"dataset_name": "in"}}
_TRANSFORM_NODE = {
    "node_type": "transform",
    "node_id": "tf",
    "node_name": "转换",
    "config": {"transform_type": "filter", "column": "a", "value": 1},
}
_OUTPUT_NODE = {"node_type": "output", "node_id": "out", "node_name": "输出", "config": {"dataset_name": "out"}}


def _definition(nodes=None, edges=None, **overrides):
    payload = {
        "display_name": "p",
        "target_layer": "cleaned",
        "status": "draft",
        "nodes": nodes if nodes is not None else [_SOURCE_NODE, _TRANSFORM_NODE, _OUTPUT_NODE],
        "edges": edges
        if edges is not None
        else [
            {"edge_id": "e1", "from_node_id": "src", "to_node_id": "tf"},
            {"edge_id": "e2", "from_node_id": "tf", "to_node_id": "out"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_definition(nodes=[]), "nodes 不能为空"),
        (_definition(nodes=[1]), "node 必须是对象"),
        (_definition(target_layer="raw"), "target_layer 不合法"),
        (_definition(status="unknown"), "status 不合法"),
        (
            _definition(nodes=[_SOURCE_NODE, {**_TRANSFORM_NODE, "node_type": "unknown"}, _OUTPUT_NODE]),
            "node_type 不合法",
        ),
        (
            _definition(
                nodes=[{**_SOURCE_NODE, "node_id": "dup"}, {**_TRANSFORM_NODE, "node_id": "dup"}, _OUTPUT_NODE]
            ),
            "node_id 不能重复",
        ),
        (
            _definition(nodes=[_SOURCE_NODE, {**_TRANSFORM_NODE, "config": 5}, _OUTPUT_NODE]),
            "node config 必须是对象",
        ),
        (
            _definition(nodes=[_SOURCE_NODE, _TRANSFORM_NODE, {**_OUTPUT_NODE, "config": {}}]),
            "output 节点缺少 dataset_name",
        ),
        (
            _definition(
                nodes=[_SOURCE_NODE, _TRANSFORM_NODE, {**_OUTPUT_NODE, "config": {"dataset_name": "o", "layer": "raw"}}]
            ),
            "output.layer 不合法",
        ),
        (_definition(edges=[]), "edges 不能为空"),
        (_definition(edges=[1]), "edge 必须是对象"),
        (_definition(edges=[{"from_node_id": "src"}]), "必须包含 from_node_id/to_node_id"),
    ],
)
def test_definition_normalization_errors(payload, message):
    with pytest.raises(PipelineInvalidError, match=message):
        DatasetPipelineService._normalize_definition_payload(payload)


def test_definition_nodes_limit():
    nodes = [dict(_SOURCE_NODE) for _ in range(51)]
    with pytest.raises(PipelineInvalidError, match="不能超过"):
        DatasetPipelineService._normalize_definition_payload(_definition(nodes=nodes))


def _normalized_nodes():
    return DatasetPipelineService._normalize_definition_payload(_definition())["nodes"]


@pytest.mark.parametrize(
    ("nodes", "edges", "message"),
    [
        ([_SOURCE_NODE, _TRANSFORM_NODE], [], "output"),
        ([_TRANSFORM_NODE, _OUTPUT_NODE], [], "source"),
        ([_SOURCE_NODE, _OUTPUT_NODE], [{"from_node_id": "src", "to_node_id": "out"}], "transform"),
        (
            [_SOURCE_NODE, _TRANSFORM_NODE, _OUTPUT_NODE],
            [{"from_node_id": "src", "to_node_id": "tf"}],
            "边数",
        ),
        (
            [_SOURCE_NODE, _TRANSFORM_NODE, _OUTPUT_NODE],
            [
                {"from_node_id": "src", "to_node_id": "ghost"},
                {"from_node_id": "ghost", "to_node_id": "out"},
            ],
            "不存在的节点",
        ),
        (
            [_SOURCE_NODE, _TRANSFORM_NODE, _OUTPUT_NODE],
            [
                {"from_node_id": "src", "to_node_id": "src"},
                {"from_node_id": "src", "to_node_id": "out"},
            ],
            "自环",
        ),
        (
            [_SOURCE_NODE, _TRANSFORM_NODE, _OUTPUT_NODE],
            [
                {"from_node_id": "src", "to_node_id": "tf"},
                {"from_node_id": "src", "to_node_id": "out"},
            ],
            "多条出边",
        ),
        (
            [_SOURCE_NODE, _TRANSFORM_NODE, _OUTPUT_NODE],
            [
                {"from_node_id": "src", "to_node_id": "out"},
                {"from_node_id": "tf", "to_node_id": "out"},
            ],
            "多条入边",
        ),
        (
            [
                _SOURCE_NODE,
                _TRANSFORM_NODE,
                {**_OUTPUT_NODE, "node_id": "out2"},
                {**_OUTPUT_NODE, "node_id": "out2"},
            ],
            [{"from_node_id": "src", "to_node_id": "tf"}, {"from_node_id": "tf", "to_node_id": "out2"}],
            "output",
        ),
    ],
)
def test_linear_chain_validation(nodes, edges, message):
    with pytest.raises(PipelineInvalidError, match=message):
        DatasetPipelineService._validate_linear_chain(
            [{**node, "node_id": node.get("node_id")} for node in nodes],
            edges,
        )


def test_linear_chain_unconnected_and_bad_tail():
    nodes = _normalized_nodes()
    with pytest.raises(PipelineInvalidError, match="未连通"):
        DatasetPipelineService._walk_chain(
            {node["node_id"]: node for node in nodes},
            {"src": "tf"},
        )
    with pytest.raises(PipelineInvalidError, match="output"):
        DatasetPipelineService._split_chain(
            [
                {"node_id": "src", "node_type": "source", "config": {}},
                {"node_id": "tf", "node_type": "transform", "config": {}},
                {"node_id": "tf2", "node_type": "transform", "config": {}},
            ],
            [{"from_node_id": "src", "to_node_id": "tf"}, {"from_node_id": "tf", "to_node_id": "tf2"}],
        )


def test_pipeline_detail_without_definition_version(harness):
    async def create_definition():
        async with harness.session_factory() as session:
            row = ChatDataPipelineDefinition(
                pipeline_id="unversioned",
                pipeline_name="unversioned",
                display_name="未定义版本",
                target_layer="cleaned",
                lifecycle_status="draft",
                current_version_no=0,
                delete_flag=0,
            )
            await DatasetPipelineDao().save_definition(session, row)
            await session.commit()

    asyncio.run(create_definition())
    listed = asyncio.run(harness.pipeline_service.list_pipelines_async())
    assert listed[0]["output_dataset_name"] == ""
    assert listed[0]["last_run"] is None
    detail = asyncio.run(harness.pipeline_service.get_pipeline_async("unversioned"))
    assert detail["nodes"] == []
    assert detail["edges"] == []
    assert detail["runs"] == []
    with pytest.raises(PipelineInvalidError, match="定义版本"):
        asyncio.run(
            harness.pipeline_service.build_pipeline_async(
                "unversioned", operator="u1", operator_name="测试", user_id="u1", username="测试"
            )
        )


def test_list_runs_limit_is_clamped(harness):
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async("p1", _pipeline_payload(), operator="u1", operator_name="测试")
    )
    assert asyncio.run(harness.pipeline_service.list_runs_async("p1", limit=0)) == []
    assert asyncio.run(harness.pipeline_service.list_runs_async("p1", limit=1000)) == []


def test_run_detail_after_pipeline_deleted(harness, monkeypatch):
    monkeypatch.setattr(pipeline_service_module, "load_dataset_rows_async", _fake_rows)
    asyncio.run(
        harness.dataset_service.upsert_dataset_async(
            "shipments", _source_payload(), operator="u1", operator_name="测试"
        )
    )
    asyncio.run(
        harness.pipeline_service.upsert_pipeline_async(
            "shipments_clean", _pipeline_payload(), operator="u1", operator_name="测试"
        )
    )
    run = asyncio.run(
        harness.pipeline_service.build_pipeline_async(
            "shipments_clean", operator="u1", operator_name="测试", user_id="u1", username="测试"
        )
    )
    asyncio.run(harness.pipeline_service.delete_pipeline_async("shipments_clean"))
    detail = asyncio.run(harness.pipeline_service.get_run_async(run["run_id"]))
    assert detail["pipeline_name"] == ""
    assert detail["output_dataset"]["dataset_name"] == "shipments_cleaned"


def test_run_summary_omits_preview():
    class _Row:
        run_id = "r"
        pipeline_id = "p"
        pipeline_version = 1
        run_status = "succeeded"
        trigger_type = "manual"
        output_dataset_id = 1
        output_summary_json = '{"row_count": 1, "preview": [{"a": 1}]}'
        error_message = ""
        started_at = None
        finished_at = None
        create_time = None
        create_by = ""
        create_by_name = ""

    summary = DatasetPipelineService._run_summary_for_list(_Row())
    assert "preview" not in summary["output_summary"]
    assert summary["output_summary"]["row_count"] == 1


# ---------------------------------------------------------------------------
# transforms 边界与运算
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"transform_type": "filter", "column": "amount", "operator": "eq"}, "value"),
        ({"transform_type": "filter", "conditions": ["bad"]}, "conditions"),
        (
            {"transform_type": "filter", "conditions": [{"column": "amount", "value": 1}] * 21},
            "不能超过 20",
        ),
        (
            {"transform_type": "aggregate", "group_by": ["amount", "amount"], "metrics": [{"column": "amount"}]},
            "group_by 不能重复",
        ),
        ({"transform_type": "aggregate", "metrics": ["bad"]}, "metrics"),
        (
            {
                "transform_type": "aggregate",
                "group_by": ["carrier"],
                "metrics": [{"column": "amount", "function": "sum", "alias": "carrier"}],
            },
            "alias 重复",
        ),
    ],
)
def test_transform_config_errors(config, message):
    with pytest.raises(TransformError, match=message):
        normalize_transform_config(config, _transform_columns())


def test_transform_ignores_non_dict_column_metadata():
    config = normalize_transform_config(
        {"transform_type": "filter", "column": "amount", "value": 1}, [1, {"name": "amount"}]
    )
    assert config["conditions"][0]["column"] == "amount"


@pytest.mark.parametrize(
    ("operator", "expected_ids"),
    [
        ("eq", ["S1"]),
        ("ne", ["S2", "S3"]),
        ("gt", ["S2", "S3"]),
        ("gte", ["S1", "S2", "S3"]),
        ("lt", []),
        ("lte", ["S1"]),
    ],
)
def test_transform_filter_numeric_operators(operator, expected_ids):
    config = normalize_transform_config(
        {"transform_type": "filter", "column": "amount", "operator": operator, "value": 10},
        _transform_columns(),
    )
    rows, _ = apply_transforms(_transform_rows(), _transform_columns(), [config])
    assert [row["shipment_id"] for row in rows] == expected_ids


def test_transform_filter_none_and_contains_none():
    config = normalize_transform_config(
        {"transform_type": "filter", "column": "amount", "operator": "eq", "value": None}, _transform_columns()
    )
    rows, _ = apply_transforms(
        [{"shipment_id": "S1", "carrier": None, "amount": None, "status": None}], _transform_columns(), [config]
    )
    assert [row["shipment_id"] for row in rows] == ["S1"]
    contains = normalize_transform_config(
        {"transform_type": "filter", "column": "carrier", "operator": "contains", "value": None}, _transform_columns()
    )
    rows, _ = apply_transforms(
        [{"shipment_id": "S1", "carrier": None, "amount": 1, "status": "x"}], _transform_columns(), [contains]
    )
    assert [row["shipment_id"] for row in rows] == ["S1"]


def test_compare_rejects_unknown_operator():
    from apps.chat_data_app.services.dataset.pipeline_transforms import _compare

    with pytest.raises(TransformError, match="operator"):
        _compare(1, "like", 1)


@pytest.mark.parametrize(
    ("function", "expected"),
    [("sum", 60.0), ("avg", 20.0), ("min", 10.0), ("max", 30.0), ("count", 3)],
)
def test_aggregate_functions(function, expected):
    config = normalize_transform_config(
        {
            "transform_type": "aggregate",
            "metrics": [{"column": "amount", "function": function, "alias": "metric"}],
        },
        _transform_columns(),
    )
    rows, _ = apply_transforms(_transform_rows(), _transform_columns(), [config])
    assert rows[0]["metric"] == expected


def test_aggregate_empty_numeric_values():
    config = normalize_transform_config(
        {
            "transform_type": "aggregate",
            "group_by": ["carrier"],
            "metrics": [{"column": "amount", "function": "avg", "alias": "avg_amount"}],
        },
        _transform_columns(),
    )
    rows, _ = apply_transforms(
        [{"shipment_id": "S1", "carrier": "顺丰", "amount": "n/a", "status": "x"}], _transform_columns(), [config]
    )
    assert rows[0]["avg_amount"] is None


def test_aggregate_columns_keep_display_name():
    columns = [{"name": "carrier", "display_name": "承运商", "data_type": "string"}]
    config = normalize_transform_config(
        {
            "transform_type": "aggregate",
            "group_by": ["carrier"],
            "metrics": [{"column": "carrier", "function": "count", "alias": "cnt"}],
        },
        columns,
    )
    rows, output_columns = apply_transforms([{"carrier": "顺丰"}], columns, [config])
    assert rows[0]["cnt"] == 1
    assert output_columns[0]["display_name"] == "承运商"
    assert output_columns[1]["data_type"] == "number"


def test_apply_transforms_empty_chain():
    rows, columns = apply_transforms(_transform_rows(), _transform_columns(), [])
    assert rows == _transform_rows()
    assert columns == _transform_columns()


# ---------------------------------------------------------------------------
# runner 边界
# ---------------------------------------------------------------------------


def test_runner_fetch_limit_normalization():
    from apps.chat_data_app.services.dataset import pipeline_runner

    assert pipeline_runner._normalize_fetch_limit("bad") == pipeline_runner.DEFAULT_FETCH_LIMIT
    assert pipeline_runner._normalize_fetch_limit(0) == 1
    assert pipeline_runner._normalize_fetch_limit(10**9) == pipeline_runner.MAX_FETCH_LIMIT


def test_runner_limit_and_column_mapping(monkeypatch):
    captured = {}

    async def _fake_target(group_id, member_id, **_kwargs):
        return {"member_datasource_id": member_id}

    async def _fake_execute(target, sql, **kwargs):
        captured.update(kwargs)
        return {"status": "succeeded", "data": [{"shipment_id": "S1"}, "ignored"]}

    monkeypatch.setattr(runner_module, "_select_execution_target", _fake_target)
    monkeypatch.setattr(runner_module, "_execute_member_sql", _fake_execute)
    dataset = _dataset_with_binding(columns=[{"name": "shipment_id"}])
    rows = asyncio.run(runner_module.load_dataset_rows_async(dataset, limit="bad"))
    assert rows == [{"shipment_id": "S1"}]
    assert captured["limit"] == runner_module.DEFAULT_FETCH_LIMIT


def test_runner_binding_selection_by_member_ids():
    dataset = _dataset_with_binding(
        bindings=[
            {"datasource_group_id": "g1", "member_datasource_id": "A", "physical_table": "t1"},
            {"datasource_group_id": "g1", "member_datasource_id": "B", "physical_table": "t2"},
        ]
    )
    binding = runner_module._select_binding(dataset, ["B"])
    assert binding["member_datasource_id"] == "B"


def test_runner_unauthorized_target(monkeypatch):
    class _Manager:
        async def list_authorized_execution_targets_async(self, group_id, **_kwargs):
            return [{"member_datasource_id": "OTHER"}]

    monkeypatch.setattr(runner_module, "get_datasource_manager", lambda: _Manager())
    with pytest.raises(PipelineBuildError, match="无权执行"):
        asyncio.run(runner_module.load_dataset_rows_async(_dataset_with_binding()))


def test_runner_physical_to_logical_variants():
    assert runner_module._physical_to_logical({"a": 1}, []) == {"a": 1}
    assert runner_module._physical_to_logical({"phys": 1}, [{"name": "logical", "physical_column": "phys"}]) == {
        "logical": 1
    }
    assert runner_module._physical_to_logical({"name": 1}, [{"name": "name"}]) == {"name": 1}
    assert runner_module._physical_to_logical({"x": 1}, [1, {"name": ""}]) == {"x": 1}
    assert runner_module._physical_to_logical(
        {"shipments.amount": 2}, [{"name": "amount", "physical_column": "amount"}]
    ) == {"amount": 2}
    with pytest.raises(PipelineBuildError, match="physical_table"):
        runner_module._safe_table_name("")


# ---------------------------------------------------------------------------
# dataset service bootstrap 兜底分支
# ---------------------------------------------------------------------------


def test_bootstrap_skips_invalid_columns_and_duplicate_primary_keys(harness, monkeypatch):
    class _Manager:
        async def get_all_datasources_async(self, include_tables=True):
            return [
                {
                    "group_id": "g1",
                    "member_datasource_ids": ["ORG_CODE1"],
                    "ds_tables": [
                        {
                            "table_name": "shipments",
                            "primary_keys": ["shipment_id", "shipment_id"],
                            "columns": [
                                1,
                                {"name": ""},
                                {"name": "shipment_id", "type": "string", "primary_key": True},
                                {"name": "carrier"},
                            ],
                        },
                        {"table_name": ""},
                    ],
                }
            ]

    monkeypatch.setattr(dataset_service_module, "get_datasource_manager", lambda: _Manager())
    result = asyncio.run(
        harness.dataset_service.bootstrap_from_datasource_async("g1", operator="u1", operator_name="测试")
    )
    assert len(result) == 1
    assert result[0]["dataset_name"] == "shipments"
    assert result[0]["primary_key"] == ["shipment_id"]
    assert [item["name"] for item in result[0]["columns"]] == ["shipment_id", "carrier"]
