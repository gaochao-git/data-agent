import json
import os

os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

from apps.chat_data_app.agent.tools.artifact_payloads import parse_chart_spec_payload
from apps.chat_data_app.chart_types import (
    CHART_TYPES,
    is_supported_chart_type_input,
    normalize_chart_type,
)


def test_chart_types_use_canonical_values_only():
    assert "kpi" in CHART_TYPES
    assert "horizontal_bar" in CHART_TYPES
    assert "funnel" in CHART_TYPES
    assert "graph" in CHART_TYPES
    assert "card" not in CHART_TYPES
    assert "hbar" not in CHART_TYPES


def test_chart_type_normalization_does_not_accept_aliases():
    assert is_supported_chart_type_input("kpi") is True
    assert is_supported_chart_type_input("horizontal_bar") is True
    assert is_supported_chart_type_input("funnel") is True
    assert is_supported_chart_type_input("graph") is True
    assert is_supported_chart_type_input("card") is False
    assert is_supported_chart_type_input("hbar") is False
    assert normalize_chart_type("card") == "table"
    assert normalize_chart_type("hbar") == "table"


def test_graph_chart_spec_accepts_nodes_links_payload(tmp_path):
    target = tmp_path / "chart_spec_graph.json"
    target.write_text(json.dumps({
        "version": "1.0",
        "title": "商品订单关系图",
        "chart_type": "graph",
        "nodes": [{"name": "商品A"}, {"name": "订单1"}],
        "links": [{"source": "商品A", "target": "订单1", "value": 1}],
    }, ensure_ascii=False), encoding="utf-8")

    payload = parse_chart_spec_payload(target)

    assert payload is not None
    assert payload["chart_type"] == "graph"
    assert payload["nodes"][0]["name"] == "商品A"


def test_graph_chart_spec_accepts_data_nodes_links_payload(tmp_path):
    target = tmp_path / "chart_spec_graph.json"
    target.write_text(json.dumps({
        "version": "1.0",
        "title": "订单-商品关系图",
        "chart_type": "graph",
        "data": {
            "nodes": [{"id": "ORD2024000001", "label": "ORD2024000001"}],
            "links": [{"source": "ORD2024000001", "target": "商品A", "value": 1}],
        },
    }, ensure_ascii=False), encoding="utf-8")

    payload = parse_chart_spec_payload(target)

    assert payload is not None
    assert payload["chart_type"] == "graph"
    assert payload["data"]["nodes"][0]["id"] == "ORD2024000001"


def test_graph_chart_spec_accepts_data_nodes_edges_payload(tmp_path):
    target = tmp_path / "chart_spec_graph.json"
    target.write_text(json.dumps({
        "version": "1.0",
        "title": "订单-商品关系图",
        "chart_type": "graph",
        "data": {
            "nodes": [{"id": "ORD2024000001", "label": "ORD2024000001"}],
            "edges": [{"source": "ORD2024000001", "target": "商品A", "value": 1}],
        },
    }, ensure_ascii=False), encoding="utf-8")

    payload = parse_chart_spec_payload(target)

    assert payload is not None
    assert payload["chart_type"] == "graph"
    assert payload["data"]["edges"][0]["target"] == "商品A"
