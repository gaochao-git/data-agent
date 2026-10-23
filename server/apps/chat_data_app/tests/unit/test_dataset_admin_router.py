"""Logical Dataset 管理路由：入参转换与错误映射。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.chat_data_app.routers import dataset_admin
from apps.chat_data_app.schema import DatasetBootstrapRequest, DatasetBranchCreateRequest, DatasetUpsertRequest
from apps.chat_data_app.services.dataset.dataset_service import (
    DatasetInvalidError,
    DatasetNotFoundError,
)


class _FakeService:
    def __init__(self):
        self.list_datasets_async = AsyncMock(return_value=[{"dataset_name": "shipments"}])
        self.bootstrap_from_datasource_async = AsyncMock(return_value=[{"dataset_name": "shipments"}])
        self.get_dataset_async = AsyncMock(return_value={"dataset_name": "shipments"})
        self.upsert_dataset_async = AsyncMock(return_value={"dataset_name": "shipments"})
        self.delete_dataset_async = AsyncMock()
        self.create_branch_async = AsyncMock(return_value={"branch_name": "feature/a"})
        self.list_error = None


def _request():
    return SimpleNamespace()


def _payload():
    return DatasetUpsertRequest(
        display_name="发货单",
        primary_key=["shipment_id"],
        columns=[{"name": "shipment_id", "physical_column": ""}],
        bindings=[{"datasource_group_id": "g1", "member_datasource_id": "ORG_CODE1", "physical_table": "shipments"}],
    )


@pytest.fixture()
def fake_service(monkeypatch):
    service = _FakeService()
    monkeypatch.setattr(dataset_admin, "logical_dataset_service", service)
    monkeypatch.setattr(
        dataset_admin, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1", username="测试用户")
    )
    return service


def test_dataset_admin_crud(fake_service):
    listed = asyncio.run(dataset_admin.list_datasets(_request()))
    assert json.loads(listed.body)["data"]["datasets"][0]["dataset_name"] == "shipments"
    detail = asyncio.run(dataset_admin.get_dataset("shipments", _request()))
    assert json.loads(detail.body)["data"]["dataset_name"] == "shipments"
    saved = asyncio.run(dataset_admin.upsert_dataset("shipments", _payload(), _request()))
    assert json.loads(saved.body)["status"] == "success"
    payload = fake_service.upsert_dataset_async.await_args.args[1]
    assert payload["columns"][0]["physical_column"] == "shipment_id"
    deleted = asyncio.run(dataset_admin.delete_dataset("shipments", _request()))
    assert json.loads(deleted.body)["status"] == "success"


def test_dataset_admin_branch(fake_service):
    result = asyncio.run(
        dataset_admin.create_dataset_branch(
            "shipments", DatasetBranchCreateRequest(branch_name="feature/a"), _request()
        )
    )
    assert json.loads(result.body)["data"]["branch_name"] == "feature/a"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (DatasetInvalidError("bad"), 40076),
        (DatasetNotFoundError("missing"), 40489),
        (RuntimeError("boom"), 50103),
    ],
)
def test_dataset_admin_error_mapping(fake_service, error, code):
    fake_service.list_datasets_async.side_effect = error
    result = asyncio.run(dataset_admin.list_datasets(_request()))
    payload = json.loads(result.body)
    assert payload["status"] == "error"
    assert payload["code"] == code


def test_dataset_admin_get_and_mutation_error_branches(fake_service):
    fake_service.get_dataset_async.side_effect = DatasetNotFoundError("missing")
    assert json.loads(asyncio.run(dataset_admin.get_dataset("missing", _request())).body)["code"] == 40489
    fake_service.upsert_dataset_async.side_effect = DatasetInvalidError("bad")
    assert json.loads(asyncio.run(dataset_admin.upsert_dataset("x", _payload(), _request())).body)["code"] == 40076
    fake_service.upsert_dataset_async.side_effect = RuntimeError("boom")
    assert json.loads(asyncio.run(dataset_admin.upsert_dataset("x", _payload(), _request())).body)["code"] == 50103
    fake_service.delete_dataset_async.side_effect = DatasetNotFoundError("missing")
    assert json.loads(asyncio.run(dataset_admin.delete_dataset("missing", _request())).body)["code"] == 40489
    fake_service.create_branch_async.side_effect = DatasetNotFoundError("missing")
    assert (
        json.loads(
            asyncio.run(
                dataset_admin.create_dataset_branch("missing", DatasetBranchCreateRequest(branch_name="a"), _request())
            ).body
        )["code"]
        == 40489
    )


def test_dataset_admin_already_exists_mapping(fake_service):
    from apps.chat_data_app.services.dataset.dataset_service import DatasetAlreadyExistsError

    fake_service.list_datasets_async.side_effect = DatasetAlreadyExistsError("duplicate")
    assert json.loads(asyncio.run(dataset_admin.list_datasets(_request())).body)["code"] == 40077


def test_dataset_admin_bootstrap(fake_service):
    result = asyncio.run(
        dataset_admin.bootstrap_datasets(
            DatasetBootstrapRequest(datasource_group_id="g1", table_names=["shipments"]), _request()
        )
    )
    assert json.loads(result.body)["data"]["datasets"][0]["dataset_name"] == "shipments"
