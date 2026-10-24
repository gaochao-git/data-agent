"""evaluation 路由兜底分支：通用异常脱敏、HTTPException 映射与后台入口包装。"""

import asyncio
import json
from types import SimpleNamespace

from fastapi import HTTPException

from apps.chat_data_app.routers import evaluation
from utils.error_codes import ErrorCode


def _body(response):
    return json.loads(response.body)


def _patch_user(monkeypatch):
    monkeypatch.setattr(evaluation, "_get_current_user", lambda _request: SimpleNamespace(user_id="u1"))


def test_build_evaluation_service_and_format_datetime_are_passthroughs():
    assert evaluation._build_evaluation_service() is not None
    assert evaluation._format_datetime(None) is None


def test_history_route_masks_unexpected_error(monkeypatch):
    _patch_user(monkeypatch)

    async def failing_permission(*_args, **_kwargs):
        raise RuntimeError("permission backend down")

    monkeypatch.setattr(evaluation, "_ensure_evaluation_semantic_permission", failing_permission)
    response = asyncio.run(
        evaluation._list_accuracy_evaluation_runs_route(
            SimpleNamespace(), datasource_id="ds_1", semantic_config_id="cfg_1"
        )
    )
    body = _body(response)
    assert body["code"] == ErrorCode.CHATDATA_EVALUATION_HISTORY_FAILED
    assert "permission backend down" not in body["msg"]


def test_detail_route_masks_unexpected_error(monkeypatch):
    _patch_user(monkeypatch)

    async def detail(_run_id):
        return {"run": {"datasource_id": "ds_1", "semantic_config_id": "cfg_1"}}

    monkeypatch.setattr(
        evaluation,
        "_build_evaluation_service",
        lambda: SimpleNamespace(get_eval_run_detail=detail),
    )

    async def failing_permission(*_args, **_kwargs):
        raise RuntimeError("permission backend down")

    monkeypatch.setattr(evaluation, "_ensure_evaluation_semantic_permission", failing_permission)
    response = asyncio.run(evaluation._get_accuracy_evaluation_run_detail_route("run_1", SimpleNamespace()))
    body = _body(response)
    assert body["code"] == ErrorCode.CHATDATA_EVALUATION_DETAIL_FAILED
    assert "permission backend down" not in body["msg"]


def test_save_route_returns_http_exception_business_code(monkeypatch):
    _patch_user(monkeypatch)

    async def failing_permission(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="not found")

    monkeypatch.setattr(evaluation, "_ensure_evaluation_semantic_permission", failing_permission)
    response = asyncio.run(
        evaluation._save_scene_evaluation_cases_route(
            "ds_1",
            "cfg_1",
            SimpleNamespace(cases=[], business_domain=""),
            SimpleNamespace(),
        )
    )
    assert _body(response)["code"] == ErrorCode.CHATDATA_NOT_FOUND


def test_save_route_masks_unexpected_error(monkeypatch):
    _patch_user(monkeypatch)

    async def failing_permission(*_args, **_kwargs):
        raise RuntimeError("save backend down")

    monkeypatch.setattr(evaluation, "_ensure_evaluation_semantic_permission", failing_permission)
    response = asyncio.run(
        evaluation._save_scene_evaluation_cases_route(
            "ds_1",
            "cfg_1",
            SimpleNamespace(cases=[], business_domain=""),
            SimpleNamespace(),
        )
    )
    body = _body(response)
    assert body["code"] == ErrorCode.CHATDATA_EVALUATION_CASE_SAVE_FAILED
    assert body["msg"].startswith("保存测试集失败:")


def test_draft_route_masks_unexpected_error(monkeypatch):
    _patch_user(monkeypatch)

    async def failing_permission(*_args, **_kwargs):
        raise RuntimeError("draft backend down")

    monkeypatch.setattr(evaluation, "_ensure_evaluation_semantic_permission", failing_permission)
    response = asyncio.run(
        evaluation._draft_scene_evaluation_case_route(
            "ds_1",
            "cfg_1",
            SimpleNamespace(),
            SimpleNamespace(),
        )
    )
    body = _body(response)
    assert body["code"] == ErrorCode.CHATDATA_EVALUATION_DETAIL_FAILED
    assert body["msg"].startswith("生成用例草稿失败:")


def test_admin_entries_forward_admin_manage(monkeypatch):
    _patch_user(monkeypatch)
    seen = []

    async def fake_route(*args, **kwargs):
        seen.append((args, kwargs))
        return "ok"

    monkeypatch.setattr(evaluation, "_get_scene_evaluation_cases_route", fake_route)
    monkeypatch.setattr(evaluation, "_save_scene_evaluation_cases_route", fake_route)
    monkeypatch.setattr(evaluation, "_generate_scene_evaluation_cases_route", fake_route)
    monkeypatch.setattr(evaluation, "_draft_scene_evaluation_case_route", fake_route)

    request = SimpleNamespace()
    assert asyncio.run(evaluation.get_admin_scene_evaluation_cases("ds", "cfg", request)) == "ok"
    assert asyncio.run(evaluation.save_admin_scene_evaluation_cases("ds", "cfg", "payload", request)) == "ok"
    assert asyncio.run(evaluation.generate_admin_scene_evaluation_cases("ds", "cfg", request)) == "ok"
    assert asyncio.run(evaluation.draft_admin_scene_evaluation_case("ds", "cfg", "payload", request)) == "ok"
    assert all(kwargs["admin_manage"] is True for _args, kwargs in seen)
    assert [args for args, _kwargs in seen] == [
        ("ds", "cfg", request),
        ("ds", "cfg", "payload", request),
        ("ds", "cfg", request),
        ("ds", "cfg", "payload", request),
    ]
