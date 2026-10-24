"""data_analyzing 工具缺失分支：入参互斥、上下文校验与批量看板入口。"""

import json
from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.tools import data_analyzing as da


def test_prepare_request_requires_exactly_one_of_dsl_sql():
    with pytest.raises(ValueError, match="必须且只能"):
        da._prepare_tool_request(dsl=None, sql="")
    with pytest.raises(ValueError, match="必须且只能"):
        da._prepare_tool_request(dsl={"metrics": []}, sql="select 1")


def test_prepare_request_rejects_non_object_and_embedded_sql():
    with pytest.raises(ValueError, match="dsl 必须是对象"):
        da._prepare_tool_request(dsl="plain text", sql=None)
    with pytest.raises(ValueError, match="sql 必须放在顶层"):
        da._prepare_tool_request(dsl={"sql": "select 1"}, sql=None)


@pytest.mark.asyncio
async def test_resolve_context_requires_semantic_config_and_datasource():
    with pytest.raises(ValueError, match="缺少指标集ID"):
        await da._resolve_tool_context(state={}, semantic_config_id=" ", member_datasource_ids=None)
    with pytest.raises(ValueError, match="未绑定数据源"):
        await da._resolve_tool_context(state={}, semantic_config_id="sc-1", member_datasource_ids=None)


def test_validate_member_scope_rejects_conflicting_and_unavailable_members():
    with pytest.raises(ValueError, match="与用户问题中指定的机构不一致"):
        da._validate_member_scope(
            requested_member_datasource_ids=["m2"],
            available_member_datasource_ids=["m1", "m2"],
            matched_member_datasource_ids=["m1"],
        )
    with pytest.raises(ValueError, match="包含不可用的执行对象"):
        da._validate_member_scope(
            requested_member_datasource_ids=["m9"],
            available_member_datasource_ids=["m1"],
            matched_member_datasource_ids=[],
        )


def test_validate_member_scope_accepts_requested_subset_of_matched():
    da._validate_member_scope(
        requested_member_datasource_ids=["m1"],
        available_member_datasource_ids=["m1", "m2"],
        matched_member_datasource_ids=["m1"],
    )


@pytest.mark.asyncio
async def test_validate_dashboard_accepts_list_with_card_config(monkeypatch):
    async def _validate(**_kwargs):
        return {"valid": True, "valid_count": 1, "panels": [{"card": 1}]}

    monkeypatch.setattr(da, "validate_dashboard_spec_payload", _validate)
    raw = await da._run_validate_dashboard(
        dsl=[{"semantic_config_id": "sc-card"}],
        semantic_config_id=None,
        datasource_id="ds-1",
        user_id="u1",
        operator_name=None,
    )
    payload = json.loads(raw.split("\n", 1)[1])
    assert payload["valid_count"] == 1


@pytest.mark.asyncio
async def test_validate_dsl_rejects_non_object_and_blank_config():
    with pytest.raises(ValueError, match="dsl 必须是对象"):
        await da._run_validate_dsl(
            dsl="plain", semantic_config_id="sc-1", datasource_id="ds-1", user_id="u1", operator_name=None
        )
    with pytest.raises(ValueError, match="缺少 semantic_config_id"):
        await da._run_validate_dsl(
            dsl={}, semantic_config_id="", datasource_id="ds-1", user_id="u1", operator_name=None
        )


@pytest.mark.asyncio
async def test_validate_dashboard_requires_ids_or_dict_payload():
    with pytest.raises(ValueError, match="批量看板卡片缺少 semantic_config_id"):
        await da._run_validate_dashboard(
            dsl=[{"card": 1}], semantic_config_id=None, datasource_id="ds-1", user_id="u1", operator_name=None
        )
    with pytest.raises(ValueError, match="dsl 必须是对象或数组"):
        await da._run_validate_dashboard(
            dsl=42, semantic_config_id="sc-1", datasource_id="ds-1", user_id="u1", operator_name=None
        )


@pytest.mark.asyncio
async def test_check_yaml_reports_duplicate_names(monkeypatch):
    async def _yaml(_runtime, _path):
        return "models: []"

    class _Manager:
        async def get_datasource_async(self, _datasource_id):
            return {"group_id": "g1"}

    async def _prepare(*_args, **_kwargs):
        return [], "重复模型名: a"

    monkeypatch.setattr(da, "_read_workspace_yaml_text", _yaml)
    monkeypatch.setattr(da, "get_datasource_manager", lambda: _Manager())
    monkeypatch.setattr(da, "prepare_ossie_import_models", _prepare)
    monkeypatch.setattr(da, "get_semantic_config_service", lambda: SimpleNamespace())

    raw = await da._run_check_yaml(file_path="m.yaml", datasource_id="ds-1", user_id="u1")
    payload = json.loads(raw.split("\n", 1)[1])
    assert payload["stage"] == "duplicate_names"
    assert payload["error"] == "重复模型名: a"
