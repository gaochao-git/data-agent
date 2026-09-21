import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.tools import data_analyzing
from apps.chat_data_app.services.semantic.run import semantic_query_service
from apps.chat_data_app.agent.workspace.artifact_registry import (
    parse_chart_spec_payload_value,
    parse_output_artifact,
)
from apps.chat_data_app.services.agent_run.conversation_workspace import ConversationWorkspaceRuntime


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _strip_tool_payload(text: str) -> dict:
    return json.loads(text.replace("[data_analyzing_tool]", "", 1).strip())


def _local_artifact_payload(root, file_path: str) -> dict:
    runtime = ConversationWorkspaceRuntime(workspace_root=str(root), user_id="system")
    return asyncio.run(runtime.artifact_payload(file_path=file_path))


def test_output_artifact_registry_preserves_suffix_specific_table_type():
    table_payload = {
        "version": "1.0",
        "chart_type": "line",
        "data": [{"month": "2026-01", "sales": 1}],
        "encoding": {"x": "month", "y": ["sales"]},
    }

    artifact = parse_output_artifact(
        visible_path="/mnt/user-data/outputs/monthly.table.json",
        data=json.dumps(table_payload).encode("utf-8"),
    )

    assert artifact == {
        "file_path": "/mnt/user-data/outputs/monthly.table.json",
        "artifact_type": "table",
        "renderer": "table",
        "artifact_version": "v1",
    }


def test_output_artifact_registry_accepts_only_direct_output_skill():
    skill_bytes = (
        b"---\nname: sales-analysis\ntitle: Sales Analysis\n"
        b"description: Analyze sales.\n---\n# Sales Analysis\n"
    )

    local_artifact = parse_output_artifact(
        visible_path="/mnt/user-data/outputs/sales-analysis/SKILL.md",
        data=skill_bytes,
    )
    sandbox_artifact = parse_output_artifact(
        visible_path="/mnt/user-data/outputs/sales-analysis/SKILL.md",
        data=skill_bytes,
    )

    assert local_artifact["artifact_type"] == "skill"
    assert sandbox_artifact["artifact_type"] == "skill"
    assert {**local_artifact, "file_path": sandbox_artifact["file_path"]} == sandbox_artifact
    assert parse_output_artifact(
        visible_path="/mnt/user-data/outputs/nested/sales-analysis/SKILL.md",
        data=skill_bytes,
    ) == {}
    assert parse_output_artifact(
        visible_path="/mnt/user-data/outputs/sales-analysis/SKILL.md",
        data=skill_bytes.replace(b"name: sales-analysis", b"name: another-skill"),
    ) == {}


def test_output_artifact_registry_accepts_office_presentation():
    artifact = parse_output_artifact(
        visible_path="/mnt/user-data/outputs/经营汇报.pptx",
        data=b"pptx",
    )

    assert artifact == {
        "file_path": "/mnt/user-data/outputs/经营汇报.pptx",
        "artifact_type": "presentation",
        "renderer": "presentation",
        "artifact_version": "v1",
    }


def test_output_artifact_registry_accepts_markdown_and_reserves_skill_file():
    artifact = parse_output_artifact(
        visible_path="/mnt/user-data/outputs/语义模型分析报告.md",
        data=b"# Report\n",
    )

    assert artifact == {
        "file_path": "/mnt/user-data/outputs/语义模型分析报告.md",
        "artifact_type": "markdown",
        "renderer": "markdown",
        "artifact_version": "v1",
    }
    assert parse_output_artifact(
        visible_path="/mnt/user-data/outputs/invalid-skill/SKILL.md",
        data=b"# Not a skill\n",
    ) == {}


def test_output_artifact_registry_accepts_self_contained_html():
    artifact = parse_output_artifact(
        visible_path="/mnt/user-data/outputs/销售月报-2026-09.html",
        data=b"<!DOCTYPE html><html><body>report</body></html>",
    )

    assert artifact == {
        "file_path": "/mnt/user-data/outputs/销售月报-2026-09.html",
        "artifact_type": "html",
        "renderer": "html",
        "artifact_version": "v1",
    }
    # htm 后缀同样注册为 html 类型
    assert parse_output_artifact(
        visible_path="/mnt/user-data/outputs/report.htm",
        data=b"<html><body>x</body></html>",
    )["artifact_type"] == "html"


def test_output_artifact_registry_accepts_yaml_output():
    artifact = parse_output_artifact(
        visible_path="/mnt/user-data/outputs/chat_data_query_execution基础模型.yaml",
        data=b"version: 0.2.0.dev0\nname: demo\ndatasets: []\n",
    )

    assert artifact == {
        "file_path": "/mnt/user-data/outputs/chat_data_query_execution基础模型.yaml",
        "artifact_type": "yaml",
        "renderer": "yaml",
        "artifact_version": "v1",
    }
    # .yml 后缀同样注册为 yaml 类型
    assert parse_output_artifact(
        visible_path="/mnt/user-data/outputs/model.yml",
        data=b"version: 1.0\n",
    )["artifact_type"] == "yaml"


def test_chart_artifact_payload_only_reads_valid_outputs_specs(tmp_path):
    root = tmp_path
    output = root / "outputs" / "chart.json"
    invalid_output = root / "outputs" / "invalid.json"
    workspace = root / "workspace" / "chart.json"
    chart_payload = {
        "version": "1.0",
        "chart_type": "line",
        "data": [{"月份": "2026-06", "销售额": 100}],
        "encoding": {"x": "月份", "y": ["销售额"]},
    }
    _write_json(output, chart_payload)
    _write_json(invalid_output, {**chart_payload, "encoding": {"x": "不存在", "y": ["销售额"]}})
    _write_json(workspace, chart_payload)

    assert parse_chart_spec_payload_value(chart_payload)["chart_type"] == "line"
    assert parse_chart_spec_payload_value({**chart_payload, "encoding": {"x": "不存在", "y": ["销售额"]}}) is None
    assert parse_output_artifact(visible_path="/mnt/user-data/outputs/bad.json", data=b'"not dict"') == {}

    loaded = _local_artifact_payload(root, "/mnt/user-data/outputs/chart.json")

    assert loaded["file_path"] == "/mnt/user-data/outputs/chart.json"
    assert loaded["artifact_type"] == "chart_spec"
    assert loaded["chart_spec"]["chart_type"] == "line"
    assert _local_artifact_payload(root, "/mnt/user-data/workspace/chart.json") == {}


def test_chart_artifact_payload_supports_graph_and_rejects_incomplete_specs(tmp_path):
    graph_payload = {
        "version": "1.0",
        "chart_type": "graph",
        "data": {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "b"}]},
    }
    pie_without_y = {
        "version": "1.0",
        "chart_type": "pie",
        "data": [{"name": "A", "value": 1}],
        "encoding": {"x": "name", "y": []},
    }
    wrong_version = {
        "version": "2.0",
        "chart_type": "bar",
        "data": [{"x": "A", "y": 1}],
        "encoding": {"x": "x", "y": ["y"]},
    }

    assert parse_chart_spec_payload_value(graph_payload)["chart_type"] == "graph"
    assert parse_chart_spec_payload_value(pie_without_y) is None
    assert parse_chart_spec_payload_value(wrong_version) is None

    graph_top_level = {
        "version": "1.0",
        "chart_type": "graph",
        "nodes": [{"id": "a"}],
        "links": [{"source": "a", "target": "b"}],
    }
    graph_empty_nodes = {
        "version": "1.0",
        "chart_type": "graph",
        "data": {"nodes": [], "links": [{"source": "a", "target": "b"}]},
    }
    assert parse_chart_spec_payload_value(graph_top_level)["chart_type"] == "graph"
    assert parse_chart_spec_payload_value(graph_empty_nodes) is None


def test_chart_artifact_payload_limits_combo_to_two_y_fields(tmp_path):
    payload = {
        "version": "1.0",
        "chart_type": "combo",
        "data": [{"month": "2026-01", "a": 1, "b": 2, "c": 3}],
        "encoding": {"x": "month", "y": ["a", "b", "c"]},
    }

    assert parse_chart_spec_payload_value(payload) is None


def test_chart_artifact_payload_rejects_invalid_json_shape_and_missing_encoding(tmp_path):
    unsupported_type = {
        "version": "1.0",
        "chart_type": "unknown",
        "data": [],
        "encoding": {},
    }
    empty_line = {
        "version": "1.0",
        "chart_type": "line",
        "data": [],
        "encoding": {"x": "month", "y": ["sales"]},
    }
    missing_encoding = {
        "version": "1.0",
        "chart_type": "bar",
        "data": [{"month": "2026-01", "sales": 1}],
    }
    missing_x = {
        "version": "1.0",
        "chart_type": "bar",
        "data": [{"month": "2026-01", "sales": 1}],
        "encoding": {"x": "", "y": ["sales"]},
    }
    graph_without_links = {
        "version": "1.0",
        "chart_type": "graph",
        "data": {"nodes": [{"id": "a"}], "edges": []},
    }
    y_missing = {
        "version": "1.0",
        "chart_type": "line",
        "data": [{"month": "2026-01", "sales": 1}],
        "encoding": {"x": "month", "y": ["missing"]},
    }
    non_dict_rows = {
        "version": "1.0",
        "chart_type": "table",
        "data": ["row"],
        "encoding": {"x": "", "y": []},
    }

    assert parse_output_artifact(visible_path="/mnt/user-data/outputs/invalid-json.json", data=b"{bad") == {}
    assert parse_chart_spec_payload_value(unsupported_type) is None
    assert parse_chart_spec_payload_value(empty_line) is None
    assert parse_chart_spec_payload_value(missing_encoding) is None
    assert parse_chart_spec_payload_value(missing_x) is None
    assert parse_chart_spec_payload_value(graph_without_links) is None
    assert parse_chart_spec_payload_value(y_missing) is None
    assert parse_chart_spec_payload_value(non_dict_rows)["chart_type"] == "table"


def test_artifact_payload_loaders_skip_invalid_targets_until_valid_one(tmp_path):
    root = tmp_path
    _write_json(root / "outputs" / "bad-chart.json", {
        "version": "1.0",
        "chart_type": "line",
        "data": [],
        "encoding": {"x": "month", "y": ["sales"]},
    })
    _write_json(root / "outputs" / "valid-chart.json", {
        "version": "1.0",
        "chart_type": "table",
        "data": [],
        "encoding": {"x": "", "y": []},
    })
    (root / "outputs" / "bad-dashboard-json.json").write_text("{bad", encoding="utf-8")
    _write_json(root / "outputs" / "valid-dashboard.json", {"cards": []})

    assert _local_artifact_payload(root, "/mnt/user-data/outputs/missing.json") == {}
    assert _local_artifact_payload(root, "/mnt/user-data/outputs/bad-chart.json") == {}
    assert _local_artifact_payload(root, "/mnt/user-data/outputs/bad-dashboard-json.json") == {}
    loaded_chart = _local_artifact_payload(root, "/mnt/user-data/outputs/valid-chart.json")
    loaded_dashboard = _local_artifact_payload(root, "/mnt/user-data/outputs/valid-dashboard.json")

    assert loaded_chart["file_path"] == "/mnt/user-data/outputs/valid-chart.json"
    assert loaded_chart["chart_spec"]["chart_type"] == "table"
    assert loaded_dashboard["file_path"] == "/mnt/user-data/outputs/valid-dashboard.json"
    assert loaded_dashboard["dashboard_spec"]["cards"] == []


def test_dashboard_artifact_payload_reads_cards_from_outputs(tmp_path):
    root = tmp_path
    _write_json(root / "outputs" / "dashboard.json", {
        "dashboard_name": "订单看板",
        "cards": [{"title": "销售额", "dsl": {"metrics": ["销售额"]}}],
    })
    _write_json(root / "outputs" / "bad-dashboard.json", {"cards": "bad"})
    _write_json(root / "workspace" / "dashboard.json", {"cards": []})

    loaded = _local_artifact_payload(root, "/mnt/user-data/outputs/dashboard.json")

    assert loaded["file_path"] == "/mnt/user-data/outputs/dashboard.json"
    assert loaded["artifact_type"] == "dashboard_spec"
    assert loaded["dashboard_spec"]["dashboard_name"] == "订单看板"
    assert _local_artifact_payload(root, "/mnt/user-data/workspace/dashboard.json") == {}
    assert _local_artifact_payload(root, "/mnt/user-data/outputs/bad-dashboard.json") == {}


@pytest.mark.asyncio
async def test_data_analyzing_tool_handles_single_batch_compare_and_invalid_payload(monkeypatch):
    class FakeGenerator:
        def __init__(self, semantic_config):
            self.semantic_config = semantic_config

        def generate_sql(self, **kwargs):
            assert kwargs["metrics"] == ["销售额"]
            assert kwargs["offset"] == 5
            return {"success": True, "sql": "select sum(amount) from orders", "error": ""}

    async def fake_get_config(datasource_id, semantic_config_id, user_id=None, mode="summary"):
        assert mode == "full"
        return {"id": semantic_config_id, "datasource_id": datasource_id}

    def fake_remove_mixed(dsl, semantic_config):
        return {**dsl, "normalized": True}, ["已移除明细字段"]

    async def fake_validate(dashboard_spec, datasource_id, semantic_config_id, user_query=None, user_id="system", username=None):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "sc_a"
        assert user_id == "user_a"
        assert username == "测试用户"
        return {"valid": True, "valid_count": len(dashboard_spec["cards"]), "warnings": ["ok"], "panels": [{"title": "面板"}]}

    monkeypatch.setattr(data_analyzing, "load_semantic_model", fake_get_config)
    monkeypatch.setattr(data_analyzing, "remove_mixed_raw_metrics_for_aggregate_dsl", fake_remove_mixed)
    monkeypatch.setattr(data_analyzing, "validate_dashboard_spec_payload", fake_validate)
    monkeypatch.setattr(semantic_query_service, "SemanticAIQueryAdapter", FakeGenerator)

    tool = data_analyzing.create_data_analyzing_tool("ds_a", user_id="user_a", operator_name="测试用户")

    single = _strip_tool_payload(await tool.ainvoke({
        "action": "validate_dsl",
        "action_payload": {
            "semantic_config_id": "sc_a",
            "dsl": json.dumps({"metrics": ["销售额"], "offset": 5}, ensure_ascii=False),
        },
    }))
    assert single["success"] is True
    assert single["mode"] == "single"
    assert single["dsl"]["normalized"] is True
    assert single["warnings"] == ["已移除明细字段"]

    batch_from_cards = _strip_tool_payload(await tool.ainvoke({
        "action": "validate_dashboard",
        "action_payload": {"semantic_config_id": "sc_a", "dsl": {"cards": [{"title": "销售额"}]}},
    }))
    batch_from_list = _strip_tool_payload(await tool.ainvoke({
        "action": "validate_dashboard",
        "action_payload": {"semantic_config_id": "sc_a", "dsl": [{"title": "销售额"}, {"title": "订单数"}]},
    }))
    assert batch_from_cards["mode"] == "batch"
    assert batch_from_cards["valid_count"] == 1
    assert batch_from_list["valid_count"] == 2

    compare = _strip_tool_payload(await tool.ainvoke({
        "action": "validate_dsl",
        "action_payload": {"semantic_config_id": "sc_a", "dsl": {"metrics": ["销售额"], "compare": {"type": "yoy"}}},
    }))
    assert compare["success"] is False
    assert "复杂同比/环比请使用智能分析" in compare["error"]

    # 校验失败现在以 ValueError 抛出，由 ToolErrorMiddleware 统一转成错误 ToolMessage
    with pytest.raises(ValueError, match="当前会话未绑定数据源"):
        await data_analyzing.create_data_analyzing_tool("").ainvoke({
            "action": "validate_dsl",
            "action_payload": {"semantic_config_id": "sc_a", "dsl": {"metrics": ["销售额"]}},
        })
    with pytest.raises(ValueError, match="semantic_config_id"):
        await data_analyzing.create_data_analyzing_tool("ds_a", None).ainvoke({
            "action": "validate_dsl",
            "action_payload": {"semantic_config_id": "", "dsl": {"metrics": ["销售额"]}},
        })
    with pytest.raises(ValueError, match="dsl 必须是对象或有效的 JSON 对象字符串"):
        await tool.ainvoke({
            "action": "validate_dsl",
            "action_payload": {"semantic_config_id": "sc_a", "dsl": "不是json"},
        })


@pytest.mark.asyncio
async def test_data_analyzing_tool_propagates_dependency_errors(monkeypatch):
    async def fake_get_config(_datasource_id, _semantic_config_id, user_id=None, mode="summary"):
        assert mode == "full"
        raise RuntimeError("配置不存在")

    monkeypatch.setattr(data_analyzing, "load_semantic_model", fake_get_config)

    with pytest.raises(RuntimeError, match="配置不存在"):
        await data_analyzing.create_data_analyzing_tool("ds_a").ainvoke({
            "action": "validate_dsl",
            "action_payload": {"semantic_config_id": "sc_a", "dsl": {"metrics": ["销售额"]}},
        })
