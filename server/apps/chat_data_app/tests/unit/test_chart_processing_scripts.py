import argparse
import json
from pathlib import Path

import importlib.util


def load_compare_period_chart_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "agent"
        / "skills"
        / "chart-processing"
        / "scripts"
        / "compare_period_chart.py"
    )
    spec = importlib.util.spec_from_file_location("compare_period_chart", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_month_yoy_chart_uses_month_label_axis(tmp_path):
    current = tmp_path / "current.json"
    previous = tmp_path / "previous.json"
    current.write_text(json.dumps({"data": [{"period": "2025-01", "sales": 87}]}), encoding="utf-8")
    previous.write_text(json.dumps({"data": [{"period": "2024-01", "sales": 71}]}), encoding="utf-8")
    module = load_compare_period_chart_module()

    chart_spec = module.build_chart_spec(argparse.Namespace(
        current=str(current),
        previous=str(previous),
        title="2024年和2025年月销量同比",
        key_field="period",
        value_field="sales",
        chart_type="line",
        current_field="sales_2025",
        previous_field="sales_2024",
        current_label="2025年销量",
        previous_label="2024年销量",
        rate_label="同比增长率",
        missing_policy="fill_zero",
        align_by="month-of-year",
    ))

    assert chart_spec["encoding"] == {"x": "month_label", "y": ["sales_2025", "sales_2024"]}
    assert chart_spec["meta"]["sort_field"] == "month_no"
    assert chart_spec["data"] == [{
        "period": "2024-01",
        "compare_period": "2025-01",
        "sales_2025": 87.0,
        "sales_2024": 71.0,
        "diff": 16.0,
        "change_rate": 16 / 71,
        "month_no": "01",
        "month_label": "1月",
    }]


def test_compare_period_helpers_cover_rows_numbers_labels_and_rate(tmp_path):
    module = load_compare_period_chart_module()
    payload_path = tmp_path / "payload.json"
    output_path = tmp_path / "nested" / "chart.json"

    module.dump_json(str(payload_path), {"data": [{"month": "2026-01", "sales": "12.5"}]})
    payload = module.load_json(str(payload_path))

    assert module.rows_from_query_result(payload) == [{"month": "2026-01", "sales": "12.5"}]
    assert module.rows_from_query_result([{"a": 1}, "bad"]) == [{"a": 1}]
    assert module.to_number(None) is None
    assert module.to_number(True) is None
    assert module.to_number("bad") is None
    assert module.to_number("12.5") == 12.5
    assert module.build_value_map([{"month": "01", "sales": 3}, {"sales": 4}], "month", "sales") == {"01": 3.0}
    assert module.month_of_year_key("2026-12") == "12"
    assert module.month_of_year_key("bad") == "bad"
    assert module.month_label("02") == "2月"
    assert module.month_label("bad") == "bad"
    assert module.sorted_keys({"b": 1}, {"a": 2}) == ["a", "b"]
    assert module.rate(None, 1) is None
    assert module.rate(1, None) is None
    assert module.rate(1, 0) is None
    assert module.rate(12, 10) == 0.2

    module.dump_json(str(output_path), {"ok": True})
    assert output_path.exists()
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"ok": True}


def test_compare_period_rows_reject_invalid_payload():
    module = load_compare_period_chart_module()

    try:
        module.rows_from_query_result({"data": "bad"})
    except ValueError as exc:
        assert "data[]" in str(exc)
    else:
        raise AssertionError("rows_from_query_result should reject invalid payload")


def test_compare_period_chart_missing_policy_exact_alignment_and_sources(tmp_path):
    current = tmp_path / "current.json"
    previous = tmp_path / "previous.json"
    current.write_text(json.dumps({
        "sql": "select month, sales from current",
        "data": [
            {"month": "2026-01", "sales": "10"},
            {"month": "2026-02", "sales": None},
        ],
    }), encoding="utf-8")
    previous.write_text(json.dumps({
        "sql": "select month, sales from previous",
        "data": [
            {"month": "2026-01", "sales": "5"},
            {"month": "2026-03", "sales": "9"},
        ],
    }), encoding="utf-8")
    module = load_compare_period_chart_module()

    keep_null = module.build_chart_spec(argparse.Namespace(
        current=str(current),
        previous=str(previous),
        title="销售额对比",
        key_field="month",
        value_field="sales",
        chart_type="bar",
        current_field="current",
        previous_field="previous",
        current_label="本期",
        previous_label="上期",
        rate_label="变化率",
        missing_policy="keep_null",
        align_by="exact",
    ))
    drop_missing = module.build_chart_spec(argparse.Namespace(
        current=str(current),
        previous=str(previous),
        title="销售额对比",
        key_field="month",
        value_field="sales",
        chart_type="bar",
        current_field="current",
        previous_field="previous",
        current_label="本期",
        previous_label="上期",
        rate_label="变化率",
        missing_policy="drop_missing",
        align_by="exact",
    ))

    assert keep_null["encoding"] == {"x": "month", "y": ["current", "previous"]}
    assert keep_null["sources"] == [
        {"name": "current", "sql": "select month, sales from current"},
        {"name": "previous", "sql": "select month, sales from previous"},
    ]
    assert [row["month"] for row in keep_null["data"]] == ["2026-01", "2026-02", "2026-03"]
    assert keep_null["data"][0]["diff"] == 5.0
    assert keep_null["data"][1]["diff"] is None
    assert [row["month"] for row in drop_missing["data"]] == ["2026-01"]


def test_compare_period_main_writes_output_from_cli_args(tmp_path, monkeypatch):
    current = tmp_path / "current.json"
    previous = tmp_path / "previous.json"
    output = tmp_path / "output.chart.json"
    current.write_text(json.dumps([{"month": "2026-01", "sales": 10}]), encoding="utf-8")
    previous.write_text(json.dumps([{"month": "2026-01", "sales": 5}]), encoding="utf-8")
    module = load_compare_period_chart_module()
    monkeypatch.setattr(module, "parse_args", lambda: argparse.Namespace(
        current=str(current),
        previous=str(previous),
        output=str(output),
        title="销售额对比",
        key_field="month",
        value_field="sales",
        chart_type="line",
        current_field="current",
        previous_field="previous",
        current_label="本期",
        previous_label="上期",
        rate_label="变化率",
        missing_policy="fill_zero",
        align_by="exact",
    ))

    module.main()

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["title"] == "销售额对比"
    assert payload["data"][0]["change_rate"] == 1.0
