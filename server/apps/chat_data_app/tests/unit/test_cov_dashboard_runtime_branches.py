"""dashboard/runtime.py 剩余分支：执行对象解析、成员归一、执行计划与流式面板。"""

from __future__ import annotations

import asyncio

import pytest

from apps.chat_data_app.services.dashboard import runtime as dashboard_runtime


def test_resolve_dashboard_panel_execution_target_without_datasource_returns_none():
    """面板没有 datasource_id 时直接返回 None，不访问数据源管理器。"""
    panel = {"id": "panel_a", "datasource_id": "  "}

    assert asyncio.run(dashboard_runtime.resolve_dashboard_panel_execution_target(panel, None)) is None


def test_execute_dashboard_panel_task_payload_skips_success_validation(monkeypatch):
    """validate_success=False 时保留失败 payload 给调用方自行处理。"""
    created: list[dict] = []

    async def fake_create(**kwargs):
        created.append(kwargs)
        return "exec_1"

    async def fake_build(**kwargs):
        return {"panel_id": "panel_a", "result": {"status": "failed", "error": "boom"}}

    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", fake_create)
    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", fake_build)
    panel = {"id": "panel_a", "name": "面板A", "chart_type": "table", "chart_config": {}, "datasource_id": "ds_a"}

    payload = asyncio.run(
        dashboard_runtime.execute_dashboard_panel_task_payload(
            "panel_a",
            panel=panel,
            execution_target={"member_datasource_id": "m1"},
            validate_success=False,
        )
    )

    assert payload["result"]["status"] == "failed"
    assert created[0]["execution_target"] == {"member_datasource_id": "m1"}

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(
            dashboard_runtime.execute_dashboard_panel_task_payload(
                "panel_a",
                panel=panel,
                execution_target={"member_datasource_id": "m1"},
            )
        )


def test_resolve_panel_member_datasource_ids_falls_back_when_multi_source_empty():
    """multiSource 已开启但成员为空时回退到 override / 旧单选字段。"""
    panel = {"chart_config": {"multiSource": {"enabled": True, "memberDatasourceIds": []}}}

    assert dashboard_runtime.resolve_panel_member_datasource_ids(panel) == []
    assert dashboard_runtime.resolve_panel_member_datasource_ids(panel, "override_m") == ["override_m"]


def test_normalize_dashboard_member_ids_drops_blank_and_duplicate_entries():
    """空串与重复成员 ID 在归一化时被跳过。"""
    assert dashboard_runtime.normalize_dashboard_member_ids(["m1", " m1 ", "", None, "m2"]) == ["m1", "m2"]


def test_normalize_dashboard_member_execution_result_wraps_exception_member():
    """单成员执行异常归一为 failed 成员结果，保证多成员可部分成功。"""
    target = {"member_datasource_id": "m1", "group_name": "组一"}

    failed = dashboard_runtime.normalize_dashboard_member_execution_result(target, RuntimeError("boom"))

    assert failed["status"] == "failed"
    assert failed["error"] == "boom"
    assert failed["member_name"] == "组一"
    assert failed["member_datasource_id"] == "m1"
    assert failed["data"] == []
    assert dashboard_runtime.normalize_dashboard_member_execution_result(target, RuntimeError())["error"] == "查询失败"


def test_build_panel_execution_plan_falls_back_to_first_member_id():
    """多成员面板未保存单选成员时，主成员回退到成员列表首项。"""
    panel = {"chart_config": {"multiSource": {"enabled": True, "memberDatasourceIds": ["m1", "m2"]}}}

    plan = dashboard_runtime.build_panel_execution_plan(["panel_a"], {"panel_a": panel})

    assert plan == {
        "panel_a": {
            "mode": "multi",
            "member_datasource_ids": ["m1", "m2"],
            "primary_member_datasource_id": "m1",
        }
    }


def _stream_panel(member_ids: list[str]) -> dict:
    return {
        "id": "panel_a",
        "name": "面板A",
        "chart_type": "table",
        "chart_config": {"multiSource": {"enabled": True, "memberDatasourceIds": member_ids}},
        "sql": "select 1",
        "datasource_id": "ds_a",
    }


def test_execute_dashboard_stream_panel_sets_and_resets_semaphore_context(monkeypatch):
    """直接调用流式面板时自行注入 semaphore 上下文，并在结束时复原。"""
    semaphore = asyncio.Semaphore(2)
    observed: list[bool] = []
    dashboard_runtime._dashboard_sql_semaphore.set(None)

    async def fake_create(**_kwargs):
        raise AssertionError("should not create execution for multi-member panel")

    async def fake_result(**kwargs):
        observed.append(dashboard_runtime._dashboard_sql_semaphore.get() is semaphore)
        return {"panel_id": "panel_a", "result": {"status": "succeeded"}}

    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", fake_create)
    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", fake_result)
    panel = _stream_panel(["m1", "m2"])

    payload = asyncio.run(
        dashboard_runtime.execute_dashboard_stream_panel(
            "panel_a",
            {"panel_a": panel},
            {("ds_a", "m1"): {"member_datasource_id": "m1"}},
            {"panel_a": "m1"},
            "task_1",
            "u1",
            "用户一",
            semaphore,
        )
    )

    assert payload == {"panel_id": "panel_a", "result": {"status": "succeeded"}}
    assert observed == [True]
    assert dashboard_runtime._dashboard_sql_semaphore.get() is None


def test_execute_dashboard_stream_panel_single_member_creates_execution(monkeypatch):
    """单成员面板走执行记录创建分支，且在 semaphore 已存在时不重复设置。"""
    semaphore = asyncio.Semaphore(2)
    created: list[dict] = []
    token = dashboard_runtime._dashboard_sql_semaphore.set(semaphore)

    async def fake_create(**kwargs):
        created.append(kwargs)
        assert dashboard_runtime._dashboard_sql_semaphore.get() is semaphore
        return "exec_1"

    async def fake_result(**kwargs):
        return {"panel_id": "panel_a", "result": {"status": "succeeded"}}

    monkeypatch.setattr(dashboard_runtime, "create_dashboard_panel_execution", fake_create)
    monkeypatch.setattr(dashboard_runtime, "build_dashboard_panel_execution_result", fake_result)
    panel = _stream_panel(["m1"])
    try:
        payload = asyncio.run(
            dashboard_runtime.execute_dashboard_stream_panel(
                "panel_a",
                {"panel_a": panel},
                {},
                {"panel_a": "m1"},
                "task_1",
                "u1",
                "用户一",
                semaphore,
            )
        )
    finally:
        dashboard_runtime._dashboard_sql_semaphore.reset(token)

    assert payload["panel_id"] == "panel_a"
    assert len(created) == 1
    assert created[0]["task_id"] == "task_1"
    assert created[0]["execution_target"] is None
