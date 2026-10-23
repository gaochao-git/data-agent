"""Dataset Pipeline 管理路由：入参转换、查询参数与错误映射。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.chat_data_app.routers import dataset_admin
from apps.chat_data_app.schema import (
    DatasetPipelineUpsertRequest,
    PipelineEdgeItem,
    PipelineNodeItem,
)
from apps.chat_data_app.services.dataset.pipeline_errors import (
    PipelineBuildError,
    PipelineInvalidError,
    PipelineNotFoundError,
)


def _request():
    return SimpleNamespace()


def _pipeline_body():
    return DatasetPipelineUpsertRequest(
        display_name="发货单清洗",
        target_layer="cleaned",
        nodes=[
            PipelineNodeItem(node_id="src", node_type="source", config={"dataset_name": "shipments"}),
            PipelineNodeItem(
                node_id="clean",
                node_type="transform",
                config={"transform_type": "filter", "column": "status", "value": "shipped"},
            ),
            PipelineNodeItem(node_id="out", node_type="output", config={"dataset_name": "shipments_cleaned"}),
        ],
        edges=[
            PipelineEdgeItem(edge_id="e1", from_node_id="src", to_node_id="clean"),
            PipelineEdgeItem(edge_id="e2", from_node_id="clean", to_node_id="out"),
        ],
    )


@pytest.fixture()
def fake_services(monkeypatch):
    pipeline = SimpleNamespace(
        list_pipelines_async=AsyncMock(return_value=[{"pipeline_name": "p1"}]),
        get_pipeline_async=AsyncMock(return_value={"pipeline_name": "p1"}),
        upsert_pipeline_async=AsyncMock(return_value={"pipeline_name": "p1"}),
        delete_pipeline_async=AsyncMock(),
        build_pipeline_async=AsyncMock(return_value={"run_id": "r1", "run_status": "succeeded"}),
        list_runs_async=AsyncMock(return_value=[{"run_id": "r1"}]),
        get_run_async=AsyncMock(return_value={"run_id": "r1", "output_dataset": {"dataset_name": "out"}}),
    )
    dataset = SimpleNamespace(list_datasets_async=AsyncMock(return_value=[{"dataset_name": "shipments"}]))
    monkeypatch.setattr(dataset_admin, "dataset_pipeline_service", pipeline)
    monkeypatch.setattr(dataset_admin, "logical_dataset_service", dataset)
    monkeypatch.setattr(
        dataset_admin, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试用户")
    )
    return SimpleNamespace(pipeline=pipeline, dataset=dataset)


def test_list_datasets_passes_filters(fake_services):
    result = asyncio.run(dataset_admin.list_datasets(_request(), "cleaned", "pipeline"))
    payload = json.loads(result.body)
    assert payload["data"]["datasets"][0]["dataset_name"] == "shipments"
    fake_services.dataset.list_datasets_async.assert_awaited_once_with(
        dataset_layer="cleaned", dataset_origin="pipeline"
    )


def test_pipeline_crud_routes(fake_services):
    listed = json.loads(asyncio.run(dataset_admin.list_pipelines(_request())).body)
    assert listed["data"]["pipelines"][0]["pipeline_name"] == "p1"
    detail = json.loads(asyncio.run(dataset_admin.get_pipeline("p1", _request())).body)
    assert detail["data"]["pipeline_name"] == "p1"

    saved = asyncio.run(dataset_admin.upsert_pipeline("p1", _pipeline_body(), _request()))
    assert json.loads(saved.body)["status"] == "success"
    payload = fake_services.pipeline.upsert_pipeline_async.await_args.args[1]
    assert payload["nodes"][0]["node_type"] == "source"
    assert payload["target_layer"] == "cleaned"
    assert fake_services.pipeline.upsert_pipeline_async.await_args.kwargs["operator"] == "u1"

    deleted = json.loads(asyncio.run(dataset_admin.delete_pipeline("p1", _request())).body)
    assert deleted["status"] == "success"


def test_pipeline_build_and_runs_routes(fake_services):
    built = json.loads(asyncio.run(dataset_admin.build_pipeline("p1", _request())).body)
    assert built["data"]["run_id"] == "r1"
    build_kwargs = fake_services.pipeline.build_pipeline_async.await_args.kwargs
    assert build_kwargs["operator"] == "u1"
    assert build_kwargs["user_id"] == "u1"
    assert build_kwargs["username"] == "测试用户"

    runs = json.loads(asyncio.run(dataset_admin.list_pipeline_runs("p1", _request(), limit=5)).body)
    assert runs["data"]["runs"][0]["run_id"] == "r1"
    fake_services.pipeline.list_runs_async.assert_awaited_once_with("p1", limit=5)

    run_detail = json.loads(asyncio.run(dataset_admin.get_pipeline_run("r1", _request())).body)
    assert run_detail["data"]["output_dataset"]["dataset_name"] == "out"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (PipelineNotFoundError("missing"), 40489),
        (PipelineInvalidError("bad"), 40076),
        (PipelineBuildError("boom"), 50103),
        (RuntimeError("unknown"), 50103),
    ],
)
def test_pipeline_error_mapping(fake_services, error, code):
    fake_services.pipeline.list_pipelines_async.side_effect = error
    result = asyncio.run(dataset_admin.list_pipelines(_request()))
    payload = json.loads(result.body)
    assert payload["status"] == "error"
    assert payload["code"] == code


def test_pipeline_mutation_error_mapping(fake_services):
    fake_services.pipeline.get_pipeline_async.side_effect = PipelineNotFoundError("missing")
    assert json.loads(asyncio.run(dataset_admin.get_pipeline("missing", _request())).body)["code"] == 40489
    fake_services.pipeline.upsert_pipeline_async.side_effect = PipelineInvalidError("bad")
    assert (
        json.loads(asyncio.run(dataset_admin.upsert_pipeline("p1", _pipeline_body(), _request())).body)["code"] == 40076
    )
    fake_services.pipeline.delete_pipeline_async.side_effect = PipelineNotFoundError("missing")
    assert json.loads(asyncio.run(dataset_admin.delete_pipeline("missing", _request())).body)["code"] == 40489
    fake_services.pipeline.build_pipeline_async.side_effect = PipelineBuildError("boom")
    assert json.loads(asyncio.run(dataset_admin.build_pipeline("p1", _request())).body)["code"] == 50103
    fake_services.pipeline.list_runs_async.side_effect = PipelineNotFoundError("missing")
    assert (
        json.loads(asyncio.run(dataset_admin.list_pipeline_runs("missing", _request(), limit=20)).body)["code"] == 40489
    )
    fake_services.pipeline.get_run_async.side_effect = PipelineNotFoundError("missing")
    assert json.loads(asyncio.run(dataset_admin.get_pipeline_run("missing", _request())).body)["code"] == 40489
