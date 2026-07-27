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

from apps.chat_data_app.agent.tools import artifact_payloads, dsl2sql
from apps.chat_data_app.services.artifact_registry import parse_output_artifact


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _strip_tool_payload(text: str) -> dict:
    return json.loads(text.replace("[dsl2sql_tool]", "", 1).strip())


def test_output_artifact_registry_preserves_suffix_specific_table_type():
    table_payload = {
        "version": "1.0",
        "chart_type": "line",
        "data": [{"month": "2026-01", "sales": 1}],
        "encoding": {"x": "month", "y": ["sales"]},
    }

    artifact = parse_output_artifact(
        visible_path="/outputs/monthly.table.json",
        data=json.dumps(table_payload).encode("utf-8"),
    )

    assert artifact == {
        "file_path": "/outputs/monthly.table.json",
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
        visible_path="/outputs/sales-analysis/SKILL.md",
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
        visible_path="/outputs/nested/sales-analysis/SKILL.md",
        data=skill_bytes,
    ) == {}
    assert parse_output_artifact(
        visible_path="/outputs/sales-analysis/SKILL.md",
        data=skill_bytes.replace(b"name: sales-analysis", b"name: another-skill"),
    ) == {}


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

    assert artifact_payloads._is_outputs_target(str(root), output) is True
    assert artifact_payloads._is_outputs_target(str(root), workspace) is False
    assert [path.name for path in artifact_payloads._iter_targets(str(root), ["outputs/chart.json", "outputs/invalid.json"])] == ["invalid.json", "chart.json"]
    assert artifact_payloads.parse_chart_spec_payload(workspace)["chart_type"] == "line"
    assert artifact_payloads.parse_chart_spec_payload(root / "missing.json") is None
    assert artifact_payloads.parse_chart_spec_payload(_write_json(root / "outputs" / "bad.json", "not dict")) is None
    assert artifact_payloads.parse_chart_spec_payload(invalid_output) is None

    loaded = artifact_payloads.load_chart_spec_artifact_payload(str(root), ["workspace/chart.json", "outputs/chart.json"])

    assert loaded["file_path"] == "/outputs/chart.json"
    assert loaded["artifact_type"] == "chart_spec"
    assert loaded["chart_spec"]["chart_type"] == "line"
    assert artifact_payloads.load_chart_spec_artifact_payload(str(root), ["workspace/chart.json"]) == {}


def test_chart_artifact_payload_supports_graph_and_rejects_incomplete_specs(tmp_path):
    graph_path = _write_json(tmp_path / "outputs" / "graph.json", {
        "version": "1.0",
        "chart_type": "graph",
        "data": {"nodes": [{"id": "a"}], "edges": [{"source": "a", "target": "b"}]},
    })
    pie_without_y = _write_json(tmp_path / "outputs" / "pie.json", {
        "version": "1.0",
        "chart_type": "pie",
        "data": [{"name": "A", "value": 1}],
        "encoding": {"x": "name", "y": []},
    })
    wrong_version = _write_json(tmp_path / "outputs" / "wrong-version.json", {
        "version": "2.0",
        "chart_type": "bar",
        "data": [{"x": "A", "y": 1}],
        "encoding": {"x": "x", "y": ["y"]},
    })

    assert artifact_payloads.parse_chart_spec_payload(graph_path)["chart_type"] == "graph"
    assert artifact_payloads.parse_chart_spec_payload(pie_without_y) is None
    assert artifact_payloads.parse_chart_spec_payload(wrong_version) is None

    graph_top_level = _write_json(tmp_path / "outputs" / "graph-top.json", {
        "version": "1.0",
        "chart_type": "graph",
        "nodes": [{"id": "a"}],
        "links": [{"source": "a", "target": "b"}],
    })
    graph_empty_nodes = _write_json(tmp_path / "outputs" / "graph-empty-nodes.json", {
        "version": "1.0",
        "chart_type": "graph",
        "data": {"nodes": [], "links": [{"source": "a", "target": "b"}]},
    })
    assert artifact_payloads.parse_chart_spec_payload(graph_top_level)["chart_type"] == "graph"
    assert artifact_payloads.parse_chart_spec_payload(graph_empty_nodes) is None


def test_chart_artifact_payload_limits_combo_to_two_y_fields(tmp_path):
    target = _write_json(tmp_path / "outputs" / "combo.json", {
        "version": "1.0",
        "chart_type": "combo",
        "data": [{"month": "2026-01", "a": 1, "b": 2, "c": 3}],
        "encoding": {"x": "month", "y": ["a", "b", "c"]},
    })

    assert artifact_payloads.parse_chart_spec_payload(target) is None


def test_chart_artifact_payload_rejects_invalid_json_shape_and_missing_encoding(tmp_path):
    invalid_json = tmp_path / "outputs" / "invalid-json.json"
    invalid_json.parent.mkdir(parents=True, exist_ok=True)
    invalid_json.write_text("{bad", encoding="utf-8")
    unsupported_type = _write_json(tmp_path / "outputs" / "unsupported.json", {
        "version": "1.0",
        "chart_type": "unknown",
        "data": [],
        "encoding": {},
    })
    empty_line = _write_json(tmp_path / "outputs" / "empty-line.json", {
        "version": "1.0",
        "chart_type": "line",
        "data": [],
        "encoding": {"x": "month", "y": ["sales"]},
    })
    missing_encoding = _write_json(tmp_path / "outputs" / "missing-encoding.json", {
        "version": "1.0",
        "chart_type": "bar",
        "data": [{"month": "2026-01", "sales": 1}],
    })
    missing_x = _write_json(tmp_path / "outputs" / "missing-x.json", {
        "version": "1.0",
        "chart_type": "bar",
        "data": [{"month": "2026-01", "sales": 1}],
        "encoding": {"x": "", "y": ["sales"]},
    })
    graph_without_links = _write_json(tmp_path / "outputs" / "graph-without-links.json", {
        "version": "1.0",
        "chart_type": "graph",
        "data": {"nodes": [{"id": "a"}], "edges": []},
    })
    y_missing = _write_json(tmp_path / "outputs" / "missing-y-field.json", {
        "version": "1.0",
        "chart_type": "line",
        "data": [{"month": "2026-01", "sales": 1}],
        "encoding": {"x": "month", "y": ["missing"]},
    })
    non_dict_rows = _write_json(tmp_path / "outputs" / "non-dict-rows.json", {
        "version": "1.0",
        "chart_type": "table",
        "data": ["row"],
        "encoding": {"x": "", "y": []},
    })

    assert artifact_payloads.parse_chart_spec_payload(invalid_json) is None
    assert artifact_payloads.parse_chart_spec_payload(unsupported_type) is None
    assert artifact_payloads.parse_chart_spec_payload(empty_line) is None
    assert artifact_payloads.parse_chart_spec_payload(missing_encoding) is None
    assert artifact_payloads.parse_chart_spec_payload(missing_x) is None
    assert artifact_payloads.parse_chart_spec_payload(graph_without_links) is None
    assert artifact_payloads.parse_chart_spec_payload(y_missing) is None
    assert artifact_payloads.parse_chart_spec_payload(non_dict_rows)["chart_type"] == "table"


def test_artifact_payload_loaders_skip_invalid_targets_until_valid_one(tmp_path):
    root = tmp_path
    missing_chart = root / "outputs" / "missing.json"
    bad_chart = _write_json(root / "outputs" / "bad-chart.json", {
        "version": "1.0",
        "chart_type": "line",
        "data": [],
        "encoding": {"x": "month", "y": ["sales"]},
    })
    valid_chart = _write_json(root / "outputs" / "valid-chart.json", {
        "version": "1.0",
        "chart_type": "table",
        "data": [],
        "encoding": {"x": "", "y": []},
    })
    bad_dashboard = root / "outputs" / "bad-dashboard-json.json"
    bad_dashboard.write_text("{bad", encoding="utf-8")
    valid_dashboard = _write_json(root / "outputs" / "valid-dashboard.json", {"cards": []})

    loaded_chart = artifact_payloads.load_chart_spec_artifact_payload(str(root), [valid_chart, bad_chart, missing_chart])
    loaded_dashboard = artifact_payloads.load_dashboard_spec_artifact_payload(str(root), [valid_dashboard, bad_dashboard, root / "outputs" / "missing-dashboard.json"])

    assert loaded_chart["file_path"] == "/outputs/valid-chart.json"
    assert loaded_chart["chart_spec"]["chart_type"] == "table"
    assert loaded_dashboard["file_path"] == "/outputs/valid-dashboard.json"
    assert loaded_dashboard["dashboard_spec"]["cards"] == []


def test_dashboard_artifact_payload_reads_cards_from_outputs(tmp_path):
    root = tmp_path
    output = _write_json(root / "outputs" / "dashboard.json", {
        "dashboard_name": "订单看板",
        "cards": [{"title": "销售额", "dsl": {"metrics": ["销售额"]}}],
    })
    _write_json(root / "outputs" / "bad-dashboard.json", {"cards": "bad"})
    _write_json(root / "workspace" / "dashboard.json", {"cards": []})

    loaded = artifact_payloads.load_dashboard_spec_artifact_payload(str(root), [output])

    assert loaded["file_path"] == "/outputs/dashboard.json"
    assert loaded["artifact_type"] == "dashboard_spec"
    assert loaded["dashboard_spec"]["dashboard_name"] == "订单看板"
    assert artifact_payloads.load_dashboard_spec_artifact_payload(str(root), ["workspace/dashboard.json", "outputs/bad-dashboard.json"]) == {}


@pytest.mark.asyncio
async def test_dsl2sql_tool_handles_single_batch_compare_and_invalid_payload(monkeypatch):
    class FakeGenerator:
        def __init__(self, semantic_config):
            self.semantic_config = semantic_config

        def generate_sql(self, **kwargs):
            assert kwargs["metrics"] == ["销售额"]
            assert kwargs["offset"] == 5
            assert kwargs["user_query"] == "查销售额"
            return {"success": True, "sql": "select sum(amount) from orders", "error": ""}

    async def fake_get_config(datasource_id, semantic_config_id, user_id=None):
        return {"id": semantic_config_id, "datasource_id": datasource_id}

    def fake_remove_mixed(dsl, semantic_config):
        return {**dsl, "normalized": True}, ["已移除明细字段"]

    async def fake_validate(dashboard_spec, datasource_id, semantic_config_id, user_query=None, user_id="system", username=None):
        assert datasource_id == "ds_a"
        assert semantic_config_id == "sc_a"
        assert user_id == "user_a"
        assert username == "测试用户"
        return {"valid": True, "valid_count": len(dashboard_spec["cards"]), "warnings": ["ok"], "panels": [{"title": "面板"}]}

    monkeypatch.setattr(dsl2sql, "get_dashboard_spec_semantic_config", fake_get_config)
    monkeypatch.setattr(dsl2sql, "remove_mixed_raw_metrics_for_aggregate_dsl", fake_remove_mixed)
    monkeypatch.setattr(dsl2sql, "validate_dashboard_spec_payload", fake_validate)
    monkeypatch.setattr(dsl2sql, "SemanticSQLGenerator", FakeGenerator)

    tool = dsl2sql.create_dsl2sql_tool("ds_a", "sc_a", user_id="user_a", operator_name="测试用户")

    single = _strip_tool_payload(await tool.ainvoke({
        "semantic_config_id": "sc_a",
        "dsl": json.dumps({"metrics": ["销售额"], "offset": 5}, ensure_ascii=False),
        "user_query": "查销售额",
    }))
    assert single["success"] is True
    assert single["mode"] == "single"
    assert single["dsl"]["normalized"] is True
    assert single["warnings"] == ["已移除明细字段"]

    batch_from_cards = _strip_tool_payload(await tool.ainvoke({
        "semantic_config_id": "sc_a",
        "dsl": {"cards": [{"title": "销售额"}]},
        "user_query": "生成看板",
    }))
    batch_from_list = _strip_tool_payload(await tool.ainvoke({
        "semantic_config_id": "sc_a",
        "dsl": [{"title": "销售额"}, {"title": "订单数"}],
        "user_query": "生成看板",
    }))
    assert batch_from_cards["mode"] == "batch"
    assert batch_from_cards["valid_count"] == 1
    assert batch_from_list["valid_count"] == 2

    compare = _strip_tool_payload(await tool.ainvoke({
        "semantic_config_id": "sc_a",
        "dsl": {"metrics": ["销售额"], "compare": {"type": "yoy"}},
    }))
    assert compare["success"] is False
    assert "复杂同比/环比请使用智能分析" in compare["error"]

    assert await dsl2sql.create_dsl2sql_tool("", "sc_a").ainvoke({"semantic_config_id": "sc_a", "dsl": {"metrics": ["销售额"]}}) == "dsl2sql_tool 执行失败: 当前会话未绑定数据源"
    assert await dsl2sql.create_dsl2sql_tool("ds_a", None).ainvoke({"semantic_config_id": "", "dsl": {"metrics": ["销售额"]}}) == "dsl2sql_tool 执行失败: 缺少 semantic_config_id，请先调用 select_semantic_config_tool 后显式传入指标集ID"
    assert await tool.ainvoke({"semantic_config_id": "sc_a", "dsl": "不是json"}) == "dsl2sql_tool 执行失败: dsl 必须是对象或数组"


@pytest.mark.asyncio
async def test_dsl2sql_tool_returns_error_text_when_dependency_raises(monkeypatch):
    async def fake_get_config(_datasource_id, _semantic_config_id, user_id=None):
        raise RuntimeError("配置不存在")

    monkeypatch.setattr(dsl2sql, "get_dashboard_spec_semantic_config", fake_get_config)

    result = await dsl2sql.create_dsl2sql_tool("ds_a", "sc_a").ainvoke({"semantic_config_id": "sc_a", "dsl": {"metrics": ["销售额"]}})

    assert result == "dsl2sql_tool 执行失败: 配置不存在"
