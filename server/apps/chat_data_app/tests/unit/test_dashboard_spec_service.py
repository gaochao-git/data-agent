import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import asyncio

from apps.chat_data_app.services.dashboard_spec_service import (
    _build_dashboard_panel_chart_config,
    _build_panel_name_fallback,
    _chart_type_label,
    _coerce_int,
    _normalize_filter_node,
    _shorten_dashboard_panel_error,
    _truncate_text,
    build_panel_sql_from_dashboard_card,
    build_dashboard_panel_plans,
    create_dashboard_from_spec_payload,
    execute_dashboard_panel_sql_preflight,
    filter_executable_dashboard_panel_plans,
    get_dashboard_spec_semantic_config,
    normalize_dashboard_card_layout,
    normalize_dashboard_card_chart_config,
    normalize_dashboard_spec_dimensions,
    normalize_dashboard_spec_filters,
    normalize_dashboard_spec_having,
    normalize_dashboard_spec_metrics,
    normalize_dashboard_spec_order_by,
    normalize_semantic_dsl_payload,
    remove_mixed_raw_metrics_for_aggregate_dsl,
    validate_dashboard_card_structure,
    validate_dashboard_spec_structure,
    validate_dashboard_spec_payload,
)


def test_normalize_semantic_dsl_payload_accepts_field_op_filters():
    payload = normalize_semantic_dsl_payload({
        "metrics": ["订单金额"],
        "dimensions": [{"dimension": "下单月份", "grain": "month"}],
        "filters": [{"field": "订单状态", "op": "=", "value": "已支付"}],
        "order_by": [{"dimension": "下单月份", "order": "asc"}],
    })

    assert payload["dimensions"] == [{"name": "下单月份", "time_grain": "month"}]
    assert payload["filters"] == [{"dimension": "订单状态", "operator": "=", "value": "已支付"}]
    assert payload["order_by"] == [{"field": "下单月份", "direction": "ASC"}]


def test_normalize_semantic_dsl_payload_accepts_metric_objects():
    payload = normalize_semantic_dsl_payload({
        "metrics": [
            {"name": "订单金额", "aggregate": "SUM"},
            {"metric": "订单数量"},
            {"field": "用户数量"},
        ],
        "dimensions": [],
    })

    assert payload["metrics"] == ["订单金额", "订单数量", "用户数量"]


def test_remove_mixed_raw_metrics_for_aggregate_dsl_keeps_aggregate_metrics():
    semantic_config = {
        "metrics": [
            {"name": "订单金额", "expression": "SUM(orders.amount)"},
            {"name": "订单数量", "expression": "COUNT(orders.order_id)"},
            {"name": "用户数量", "expression": "users.user_id"},
        ]
    }

    payload, warnings = remove_mixed_raw_metrics_for_aggregate_dsl({
        "metrics": ["订单金额", "订单数量", "用户数量"],
        "dimensions": ["下单月份"],
    }, semantic_config)

    assert payload["metrics"] == ["订单金额", "订单数量"]
    assert warnings


def test_get_dashboard_spec_semantic_config_keeps_selected_source_type(monkeypatch):
    class FakeManager:
        async def get_datasource_async(self, datasource_id):
            assert datasource_id == "ds_a"
            return {
                "group_name": "订单数据源",
                "source_type": "",
                "ds_tables": [],
                "semantic_configs": [
                    {
                        "id": "sc_a",
                        "source_type": "hive",
                        "metrics": [],
                        "dimensions": [],
                    }
                ],
            }

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_datasource_manager", lambda: FakeManager())

    config = asyncio.run(get_dashboard_spec_semantic_config("ds_a", "sc_a"))

    assert config["source_type"] == "hive"


def test_build_panel_sql_from_dashboard_card_rejects_compare():
    class FakeGenerator:
        semantic_config = {}

    card = {
        "title": "同比卡片",
        "chart_type": "line",
        "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
        "dsl": {
            "metrics": ["订单金额"],
            "dimensions": [{"name": "下单时间", "time_grain": "month"}],
            "compare": {"type": "yoy", "mode": "trend", "strategy": "calc"},
        },
    }

    try:
        build_panel_sql_from_dashboard_card(card, FakeGenerator())
    except ValueError as err:
        assert "不再支持 compare" in str(err)
    else:
        raise AssertionError("compare DSL should be rejected")


def test_validate_dashboard_spec_structure_rejects_compare():
    errors = validate_dashboard_spec_structure({
        "dashboard_name": "同比看板",
        "semantic_config_id": "sem_1",
        "cards": [
            {
                "title": "同比卡片",
                "chart_type": "line",
                "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
                "dsl": {
                    "metrics": ["订单金额"],
                    "dimensions": [{"name": "下单时间", "time_grain": "month"}],
                    "compare": {"type": "yoy", "mode": "trend", "strategy": "calc"},
                },
            }
        ],
    })

    assert any("不支持 compare" in item for item in errors)


def test_filter_executable_dashboard_panel_plans_filters_failed_sql(monkeypatch):
    async def fake_preflight(_datasource_id: str, sql: str, user_id: str = "system", username: str | None = None):
        assert username == "测试用户"
        if "bad_column" in sql:
            return False, "字段不存在"
        return True, ""

    monkeypatch.setattr(
        "apps.chat_data_app.services.dashboard_spec_service.execute_dashboard_panel_sql_preflight",
        fake_preflight,
    )

    plans = [
        {"index": 0, "card": {"title": "可执行卡片"}, "sql": "select 1", "chart_type": "kpi"},
        {"index": 1, "card": {"title": "失败卡片"}, "sql": "select bad_column from t", "chart_type": "table"},
    ]

    executable, warnings = asyncio.run(filter_executable_dashboard_panel_plans(plans, datasource_id="ds_a", username="测试用户"))

    assert len(executable) == 2
    assert executable[0]["card"]["title"] == "可执行卡片"
    assert executable[1]["card"]["title"] == "失败卡片"
    assert executable[1]["error"] == "字段不存在"
    assert warnings == ["卡片「失败卡片」预执行失败：字段不存在"]




def test_create_dashboard_from_spec_payload_skips_sql_precheck(monkeypatch):
    class FakeService:
        async def create_dashboard(self, **_kwargs):
            return {"id": "dash_1"}

        async def add_panel(self, **kwargs):
            return {
                "id": f"panel_{kwargs['name']}",
                "layout": {
                    "x": kwargs["layout_x"],
                    "y": kwargs["layout_y"],
                    "w": kwargs["layout_w"],
                    "h": kwargs["layout_h"],
                },
            }

        async def get_dashboard(self, _dashboard_id):
            return {"id": "dash_1", "panels": [{"id": "panel_订单状态分布", "layout": {"x": 9, "y": 0, "w": 3, "h": 2}}]}

        async def delete_dashboard(self, _dashboard_id):
            return None

    captured_validate_kwargs = {}

    async def fake_validate_dashboard_spec_payload(**kwargs):
        captured_validate_kwargs.update(kwargs)
        return {
            "valid": True,
            "valid_count": 1,
            "warnings": [],
            "panels": [{
                "index": 0,
                "title": "订单状态分布",
                "chart_type": "pie",
                "sql": "select status, count(*) as cnt from orders group by status",
            }],
        }

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.validate_dashboard_spec_payload", fake_validate_dashboard_spec_payload)
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_dashboard_service", lambda: FakeService())

    result = asyncio.run(create_dashboard_from_spec_payload(
        dashboard_spec={
            "dashboard_name": "订单经营分析看板",
            "semantic_config_id": "sem_from_spec",
            "cards": [{
                "title": "订单状态分布",
                "chart_type": "pie",
                "layout": {"x": 9, "y": 0, "w": 3, "h": 2},
                "dsl": {"metrics": ["订单数"], "dimensions": ["订单状态"]},
            }],
        },
        datasource_id="ds_a",
        conversation_id="conv_1",
        user_id="u_1",
    ))

    assert result["dashboard"]["id"] == "dash_1"
    assert len(result["panels"]) == 1
    assert result["warnings"] == []
    assert captured_validate_kwargs["semantic_config_id"] == "sem_from_spec"


def test_create_dashboard_from_spec_payload_keeps_invalid_sql_cards(monkeypatch):
    class FakeService:
        def __init__(self):
            self.panels = []

        async def create_dashboard(self, **_kwargs):
            return {"id": "dash_1"}

        async def add_panel(self, **kwargs):
            panel = {
                "id": f"panel_{len(self.panels) + 1}",
                "name": kwargs["name"],
                "sql": kwargs["sql"],
                "chart_type": kwargs["chart_type"],
                "chart_config": kwargs["chart_config"],
                "layout": {
                    "x": kwargs["layout_x"],
                    "y": kwargs["layout_y"],
                    "w": kwargs["layout_w"],
                    "h": kwargs["layout_h"],
                },
            }
            self.panels.append(panel)
            return panel

        async def get_dashboard(self, _dashboard_id):
            return {"id": "dash_1", "panels": list(self.panels)}

        async def delete_dashboard(self, _dashboard_id):
            return None

    service = FakeService()

    async def fake_validate_dashboard_spec_payload(**_kwargs):
        return {
            "valid": True,
            "valid_count": 1,
            "warnings": ["卡片「失败排行」创建失败：字段不存在"],
            "panels": [
                {"index": 0, "title": "有效趋势", "chart_type": "line", "sql": "select 1", "error": ""},
                {"index": 1, "title": "失败排行", "chart_type": "horizontal_bar", "sql": "", "error": "字段不存在"},
            ],
        }

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.validate_dashboard_spec_payload", fake_validate_dashboard_spec_payload)
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_dashboard_service", lambda: service)

    result = asyncio.run(create_dashboard_from_spec_payload(
        dashboard_spec={
            "dashboard_name": "订单经营分析看板",
            "cards": [
                {
                    "title": "有效趋势",
                    "chart_type": "line",
                    "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
                    "dsl": {"metrics": ["订单金额"], "dimensions": ["下单月份"]},
                },
                {
                    "title": "失败排行",
                    "chart_type": "horizontal_bar",
                    "layout": {"x": 6, "y": 0, "w": 6, "h": 4},
                    "dsl": {"metrics": ["不存在指标"], "dimensions": ["商品名称"]},
                },
            ],
        },
        datasource_id="ds_a",
        semantic_config_id="sem_1",
        conversation_id="conv_1",
        user_id="u_1",
    ))

    assert len(result["panels"]) == 2
    assert result["panels"][1]["name"] == "失败排行"
    assert result["panels"][1]["sql"] == ""
    assert result["panels"][1]["layout"] == {"x": 6, "y": 0, "w": 6, "h": 4}
    assert result["panels"][1]["chart_config"]["creation_status"] == "invalid_sql"
    assert result["panels"][1]["chart_config"]["creation_error"] == "字段不存在"
    class FakeGenerator:
        semantic_config = {"metrics": [{"name": "订单数", "expression": "COUNT(*)"}]}

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select count(*) as value from orders"}

    sql = build_panel_sql_from_dashboard_card(
        {
            "sql": "select bad_column from raw_table",
            "dsl": {"metrics": ["订单数"], "dimensions": []},
        },
        FakeGenerator(),
    )

    assert sql == "select count(*) as value from orders"


def test_build_panel_sql_from_dashboard_card_does_not_add_default_limit():
    class FakeGenerator:
        semantic_config = {}

        def generate_sql(self, **kwargs):
            assert kwargs["limit"] is None
            return {"success": True, "sql": "select count(*) as value from orders"}

    sql = build_panel_sql_from_dashboard_card(
        {
            "title": "订单数量",
            "chart_type": "kpi",
            "layout": {"x": 0, "y": 0, "w": 3, "h": 2},
            "dsl": {"metrics": ["订单数量"], "dimensions": []},
        },
        FakeGenerator(),
    )

    assert "LIMIT" not in sql.upper()


def test_normalize_dashboard_card_chart_config_keeps_allowed_style_options():
    config = normalize_dashboard_card_chart_config({
        "purpose": "展示风险规模",
        "diagnosis": "关注风险变化",
        "chart_config": {
            "kpiIcon": "fa-bolt",
            "kpiColor": "#ffdc3d",
            "chartColor": "#00e5ff",
            "lineStyle": "smooth",
            "unsafe": "ignored",
        },
        "dsl": {"metrics": ["风险事件数"], "dimensions": []},
    })

    assert config["kpiIcon"] == "fa-bolt"
    assert config["kpiColor"] == "#ffdc3d"
    assert config["chartColor"] == "#00e5ff"
    assert config["lineStyle"] == "smooth"
    assert config["metrics"] == ["风险事件数"]
    assert "unsafe" not in config


def test_normalize_dashboard_card_chart_config_drops_invalid_style_values():
    config = normalize_dashboard_card_chart_config({
        "chart_config": {
            "kpiIcon": "fa-not-exists",
            "kpiColor": "red",
            "chartColor": "javascript:alert(1)",
            "lineStyle": "curved",
            "xField": "月份",
        },
        "dsl": {"metrics": ["订单金额"], "dimensions": ["月份"]},
    })

    assert "kpiIcon" not in config
    assert "kpiColor" not in config
    assert "chartColor" not in config
    assert "lineStyle" not in config
    assert config["xField"] == "月份"


def test_validate_dashboard_spec_structure_requires_layout():
    errors = validate_dashboard_spec_structure({
        "dashboard_name": "订单经营看板",
        "semantic_config_id": "sem_1",
        "cards": [
            {
                "title": "总订单金额",
                "chart_type": "kpi",
                "dsl": {"metrics": ["订单金额"], "dimensions": []},
            }
        ],
    })

    assert "卡片「总订单金额」缺少 layout 对象" in errors


def test_validate_dashboard_spec_structure_rejects_invalid_layout_range():
    errors = validate_dashboard_spec_structure({
        "dashboard_name": "订单经营看板",
        "semantic_config_id": "sem_1",
        "cards": [
            {
                "title": "趋势",
                "chart_type": "line",
                "layout": {"x": 8, "y": 0, "w": 6, "h": 4},
                "dsl": {"metrics": ["订单金额"], "dimensions": ["下单月份"]},
            }
        ],
    })

    assert "卡片「趋势」layout.x + layout.w 不能超过 12" in errors


def test_dashboard_spec_normalizers_drop_invalid_values():
    assert normalize_dashboard_spec_dimensions([
        {"dimension": "", "grain": "month"},
        {"dimension": "下单时间", "grain": "day"},
        "",
        "商品分类",
    ]) == [{"name": "下单时间", "time_grain": "day"}, "商品分类"]
    assert normalize_dashboard_spec_filters({"dimension": "状态"}) == []
    assert normalize_dashboard_spec_filters([
        {"and": [{"field": "状态", "op": "=", "value": "paid"}, "bad"]},
        "bad",
    ]) == [{"and": [{"dimension": "状态", "operator": "=", "value": "paid"}]}]
    assert normalize_dashboard_spec_having({"metric": "销售额"}) == []
    assert normalize_dashboard_spec_having([
        {"field": "销售额", "op": ">", "value": 100},
        "bad",
    ]) == [{"metric": "销售额", "operator": ">", "value": 100}]
    assert normalize_dashboard_spec_order_by([
        {"metric": "", "order": "asc"},
        {"dimension": "月份", "order": "asc"},
        "bad",
    ]) == [{"field": "月份", "direction": "ASC"}]


def test_get_dashboard_spec_semantic_config_errors_and_lazy_configs(monkeypatch):
    class MissingManager:
        async def get_datasource_async(self, datasource_id):
            return None

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_datasource_manager", lambda: MissingManager())
    try:
        asyncio.run(get_dashboard_spec_semantic_config("missing", "sc_a"))
    except ValueError as err:
        assert str(err) == "数据源不存在"
    else:
        raise AssertionError("missing datasource should be rejected")

    try:
        asyncio.run(get_dashboard_spec_semantic_config("ds_a", None))
    except ValueError as err:
        assert "缺少指标集ID" in str(err)
    else:
        raise AssertionError("missing semantic config id should be rejected")

    class LazyManager:
        async def get_datasource_async(self, datasource_id):
            return {"source_type": "mysql", "ds_tables": [{"table_name": "orders"}], "semantic_configs": None}

        async def get_semantic_configs_async(self, datasource_id):
            return [{"id": "disabled", "enabled": False}, {"id": "enabled", "enabled": True, "relationships": [{"left_table": "a"}]}]

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_datasource_manager", lambda: LazyManager())
    config = asyncio.run(get_dashboard_spec_semantic_config("ds_a", "enabled"))
    assert config["id"] == "enabled"
    assert config["joins"] == [{"left_table": "a"}]
    assert config["tables"] == [{"table_name": "orders"}]

    class NoConfigManager:
        async def get_datasource_async(self, datasource_id):
            return {"source_type": "mysql", "semantic_configs": [{"id": "disabled", "enabled": False}]}

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_datasource_manager", lambda: NoConfigManager())
    try:
        asyncio.run(get_dashboard_spec_semantic_config("ds_a", "missing_config"))
    except ValueError as err:
        assert "未找到可用语义配置" in str(err)
    else:
        raise AssertionError("missing semantic config should be rejected")


def test_build_panel_sql_from_dashboard_card_passes_normalized_payload():
    captured = {}

    class FakeGenerator:
        semantic_config = {
            "metrics": [
                {"name": "销售额", "expression": "SUM(amount)"},
                {"name": "用户ID", "expression": "user_id"},
            ]
        }

        def generate_sql(self, **kwargs):
            captured.update(kwargs)
            return {"success": True, "sql": "select sum(amount) from orders limit 5 offset 2"}

    sql = build_panel_sql_from_dashboard_card(
        {
            "title": "销售额",
            "chart_type": "bar",
            "layout": {"x": 0, "y": 0, "w": 6, "h": 4},
            "dsl": {
                "metrics": ["销售额", "用户ID"],
                "dimensions": [{"dimension": "月份", "grain": "month"}],
                "filters": [{"field": "状态", "op": "=", "value": "paid"}],
                "having": [{"field": "销售额", "op": ">", "value": 100}],
                "order_by": [{"dimension": "月份", "order": "asc"}],
                "limit": 5,
                "offset": 2,
            },
        },
        FakeGenerator(),
        user_query="按月统计销售额",
    )

    assert sql.endswith("offset 2")
    assert captured["metrics"] == ["销售额"]
    assert captured["dimensions"] == [{"name": "月份", "time_grain": "month"}]
    assert captured["filters"] == [{"dimension": "状态", "operator": "=", "value": "paid"}]
    assert captured["having"] == [{"metric": "销售额", "operator": ">", "value": 100}]
    assert captured["order_by"] == [{"field": "月份", "direction": "ASC"}]
    assert captured["limit"] == 5
    assert captured["offset"] == 2
    assert captured["user_query"] == "按月统计销售额"


def test_build_panel_sql_from_dashboard_card_raises_generator_error():
    class FakeGenerator:
        semantic_config = {}

        def generate_sql(self, **_kwargs):
            return {"success": False, "error": "指标不存在"}

    try:
        build_panel_sql_from_dashboard_card({"dsl": {"metrics": ["不存在"]}}, FakeGenerator())
    except ValueError as err:
        assert str(err) == "指标不存在"
    else:
        raise AssertionError("generator error should be raised")


def test_validate_dashboard_card_structure_reports_invalid_shape():
    errors = validate_dashboard_card_structure({
        "title": "坏卡片",
        "chart_type": "unknown",
        "dsl": [],
        "layout": {"x": True, "y": 1.5, "w": 0, "h": 0},
    }, 0)

    assert "卡片「坏卡片」chart_type 缺失或不支持" in errors
    assert "卡片「坏卡片」缺少 dsl 对象" in errors
    assert "卡片「坏卡片」layout.x 必须是数字" in errors
    assert "卡片「坏卡片」layout.y 必须是整数" in errors


def test_validate_dashboard_spec_structure_rejects_missing_name_and_non_object_card():
    errors = validate_dashboard_spec_structure({"dashboard_name": "", "semantic_config_id": "sem_1", "cards": ["bad"]})
    assert "dashboard_name 必填" in errors
    assert "卡片1 必须是对象" in errors
    assert validate_dashboard_spec_structure({"dashboard_name": "空看板", "cards": []}) == ["cards 必须是非空数组"]


def test_build_dashboard_panel_plans_records_success_warnings_and_errors():
    class FakeGenerator:
        semantic_config = {
            "metrics": [
                {"name": "销售额", "expression": "SUM(amount)"},
                {"name": "用户ID", "expression": "user_id"},
            ]
        }

        def generate_sql(self, metrics, **_kwargs):
            if metrics == ["空SQL"]:
                return {"success": True, "sql": ""}
            return {"success": True, "sql": f"select {metrics[0]} from orders"}

    plans, warnings = build_dashboard_panel_plans([
        "bad",
        {"title": "混用指标", "chart_type": "line", "dsl": {"metrics": ["销售额", "用户ID"], "dimensions": ["月份"]}},
        {"title": "空SQL", "chart_type": "table", "dsl": {"metrics": ["空SQL"]}},
        {"title": "同比", "chart_type": "line", "dsl": {"metrics": ["销售额"], "compare": {"type": "yoy"}}},
    ], FakeGenerator())

    assert len(plans) == 3
    assert plans[0]["sql"].startswith("select 销售额")
    assert plans[1]["error"] == "SQL 为空"
    assert "不能混用" in warnings[0]
    assert any("SQL 为空" in item for item in warnings)
    assert any("不再支持 compare" in item for item in warnings)


def test_execute_dashboard_panel_sql_preflight_success_failure_and_exception(monkeypatch):
    class FakeManager:
        async def get_execution_datasources_async(self, datasource_id, member_ids):
            if datasource_id == "empty":
                return []
            return [{"source_type": "mysql", "mode": datasource_id}]

    class FakeExecutor:
        def __init__(self, target):
            self.target = target
            self.closed = False

        async def execute(self, sql, limit, timeout, user_id, username=None):
            assert username == "测试用户"
            if self.target["mode"] == "fail":
                return {"success": False, "error": "表不存在"}
            if self.target["mode"] == "raise":
                raise RuntimeError("连接失败")
            return {"success": True}

        async def close(self):
            self.closed = True

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_datasource_manager", lambda: FakeManager())
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_executor", lambda target: FakeExecutor(target))
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.resolve_query_timeout", lambda source_type: 600)

    assert asyncio.run(execute_dashboard_panel_sql_preflight("empty", "select 1")) == (False, "当前数据源组下没有可执行的数据源")
    assert asyncio.run(execute_dashboard_panel_sql_preflight("ok", "select 1", user_id="u1", username="测试用户")) == (True, "")
    assert asyncio.run(execute_dashboard_panel_sql_preflight("fail", "select 1", user_id="u1", username="测试用户")) == (False, "表不存在")
    assert asyncio.run(execute_dashboard_panel_sql_preflight("raise", "select 1", user_id="u1", username="测试用户")) == (False, "连接失败")


def test_validate_dashboard_spec_payload_validation_and_execute_sql(monkeypatch):
    try:
        asyncio.run(validate_dashboard_spec_payload(dashboard_spec={}, datasource_id=""))
    except ValueError as err:
        assert "缺少数据源ID" in str(err)
    else:
        raise AssertionError("missing datasource should be rejected")

    try:
        asyncio.run(validate_dashboard_spec_payload(dashboard_spec={"need_clarify": True, "cards": []}, datasource_id="ds_a"))
    except ValueError as err:
        assert "需补充确认" in str(err)
    else:
        raise AssertionError("need clarify should be rejected")

    try:
        asyncio.run(validate_dashboard_spec_payload(dashboard_spec={"cards": []}, datasource_id="ds_a"))
    except ValueError as err:
        assert "缺少卡片" in str(err)
    else:
        raise AssertionError("missing cards should be rejected")

    invalid = asyncio.run(validate_dashboard_spec_payload(
        dashboard_spec={"dashboard_name": "", "semantic_config_id": "sem_1", "cards": ["bad"]},
        datasource_id="ds_a",
    ))
    assert invalid["valid"] is False
    assert invalid["panels"] == []

    class FakeGenerator:
        def __init__(self, semantic_config):
            self.semantic_config = semantic_config

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "select 1"}

    async def fake_semantic_config(*_args, **_kwargs):
        return {"metrics": [], "dimensions": []}

    async def fake_filter(plans, **_kwargs):
        return plans, ["预执行警告"]

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_dashboard_spec_semantic_config", fake_semantic_config)
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.SemanticSQLGenerator", FakeGenerator)
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.filter_executable_dashboard_panel_plans", fake_filter)

    valid = asyncio.run(validate_dashboard_spec_payload(
        dashboard_spec={
            "dashboard_name": "看板",
            "semantic_config_id": "sem_1",
            "cards": [{"title": "卡片", "chart_type": "kpi", "layout": {"x": 0, "y": 0, "w": 3, "h": 2}, "dsl": {"metrics": ["订单数"]}}],
        },
        datasource_id="ds_a",
        execute_sql=True,
        username="测试用户",
    ))
    assert valid["valid"] is True
    assert valid["valid_count"] == 1
    assert valid["warnings"] == ["预执行警告"]
    assert valid["panels"][0]["sql"] == "select 1"


def test_dashboard_layout_and_chart_config_error_helpers():
    layout = normalize_dashboard_card_layout(
        {"layout": {"x": 11, "y": -2, "w": 6, "h": "bad"}},
        index=0,
        chart_type="kpi",
    )
    assert layout == {"x": 11, "y": 0, "w": 1, "h": 3}

    config = normalize_dashboard_card_chart_config({
        "chart_config": {"seriesField": "机构", "xField": "月份", "yField": "销售额"},
        "metrics": ["销售额"],
        "dimensions": ["月份"],
    })
    assert config["seriesField"] == "机构"
    assert config["xField"] == "月份"
    assert config["yField"] == "销售额"


def test_create_dashboard_from_spec_payload_invalid_and_cleanup_branches(monkeypatch):
    class FakeService:
        def __init__(self, mode):
            self.mode = mode
            self.deleted = []

        async def create_dashboard(self, **_kwargs):
            return {"id": "dash_1"}

        async def add_panel(self, **kwargs):
            if self.mode == "add_fail":
                raise RuntimeError("写入失败")
            return {"id": "panel_1", "name": kwargs["name"]}

        async def get_dashboard(self, _dashboard_id):
            if self.mode == "read_empty":
                return {"id": "dash_1", "panels": []}
            return {"id": "dash_1", "panels": [{"id": "panel_1"}]}

        async def delete_dashboard(self, dashboard_id):
            self.deleted.append(dashboard_id)

    async def invalid_validation(**_kwargs):
        return {"valid": False, "warnings": ["结构错误"], "panels": []}

    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.validate_dashboard_spec_payload", invalid_validation)
    result = asyncio.run(create_dashboard_from_spec_payload(dashboard_spec={"cards": []}, datasource_id="ds_a"))
    assert result == {"dashboard": None, "panels": [], "warnings": ["结构错误"]}

    async def valid_validation(**_kwargs):
        return {"valid": True, "warnings": [], "panels": [{"index": 0, "title": "卡片", "chart_type": "kpi", "sql": "select 1", "error": ""}]}

    service = FakeService("add_fail")
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.validate_dashboard_spec_payload", valid_validation)
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_dashboard_service", lambda: service)
    failed = asyncio.run(create_dashboard_from_spec_payload(
        dashboard_spec={"dashboard_name": "看板", "cards": [{"title": "卡片", "chart_type": "kpi", "layout": {"x": 0, "y": 0, "w": 3, "h": 2}, "dsl": {}}]},
        datasource_id="ds_a",
    ))
    assert failed["dashboard"] is None
    assert service.deleted == ["dash_1"]
    assert any("写入失败" in item for item in failed["warnings"])

    service = FakeService("read_empty")
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_dashboard_service", lambda: service)
    unreadable = asyncio.run(create_dashboard_from_spec_payload(
        dashboard_spec={"dashboard_name": "看板", "cards": [{"title": "卡片", "chart_type": "kpi", "layout": {"x": 0, "y": 0, "w": 3, "h": 2}, "dsl": {}}]},
        datasource_id="ds_a",
    ))
    assert unreadable["dashboard"] is None
    assert service.deleted == ["dash_1"]
    assert "看板写入后回读失败，未确认数据已持久化" in unreadable["warnings"]


def test_dashboard_spec_small_helper_edges():
    assert _truncate_text("   ", 10) == ""
    assert _truncate_text(" abcdef ", 3) == "abc"
    assert _chart_type_label("kpi")
    assert _build_panel_name_fallback("kpi", max_length=8)
    assert _coerce_int("bad", 7) == 7
    assert _coerce_int("3", 7) == 3
    assert _normalize_filter_node("raw") == "raw"
    assert _normalize_filter_node({"and": [{"field": "城市", "op": "=", "value": "上海"}, "skip"]}) == {
        "and": [{"dimension": "城市", "operator": "=", "value": "上海"}]
    }
    assert _normalize_filter_node({"or": [{"field": "城市", "op": "=", "value": "北京"}]}) == {
        "or": [{"dimension": "城市", "operator": "=", "value": "北京"}]
    }
    assert _shorten_dashboard_panel_error("") == ""
    long_error = _shorten_dashboard_panel_error(" line1\n" + "x" * 300, max_length=20)
    assert "\n" not in long_error
    assert long_error.endswith("...")
    assert _build_dashboard_panel_chart_config({"chart_config": {}}, "")["purpose"] == ""
    invalid_config = _build_dashboard_panel_chart_config({"chart_config": {}}, "字段不存在\n请检查")
    assert invalid_config["creation_status"] == "invalid_sql"
    assert invalid_config["creation_error"] == "字段不存在 请检查"


def test_dashboard_spec_normalizers_cover_empty_and_tree_branches():
    assert normalize_dashboard_spec_dimensions([{}, {"name": "下单时间", "grain": "month"}, " 城市 ", ""]) == [
        {"name": "下单时间", "time_grain": "month"},
        "城市",
    ]
    assert normalize_dashboard_spec_metrics([{}, {"field": "销售额"}, " 订单数 ", ""]) == ["销售额", "订单数"]
    assert normalize_dashboard_spec_filters({"bad": True}) == []
    assert normalize_dashboard_spec_filters([
        {"and": [{"field": "城市", "op": "=", "value": "上海"}, "skip"]},
        "skip",
    ]) == [{"and": [{"dimension": "城市", "operator": "=", "value": "上海"}]}]
    assert normalize_dashboard_spec_having({"bad": True}) == []
    assert normalize_dashboard_spec_having([
        "skip",
        {"field": "销售额", "op": ">", "value": 10},
        {"metric": "订单数", "operator": ">", "value": 1},
    ]) == [
        {"metric": "销售额", "operator": ">", "value": 10},
        {"metric": "订单数", "operator": ">", "value": 1},
    ]
    assert normalize_dashboard_spec_order_by({"bad": True}) == []
    assert normalize_dashboard_spec_order_by([
        "skip",
        {"field": "", "direction": "asc"},
        {"metric": "销售额", "order": "asc"},
        {"dimension": "城市"},
    ]) == [
        {"field": "销售额", "direction": "ASC"},
        {"field": "城市", "direction": "DESC"},
    ]


def test_dashboard_spec_structure_invalid_layout_branches():
    errors = validate_dashboard_card_structure(
        {
            "title": "坏布局",
            "chart_type": "not-supported",
            "dsl": {},
            "layout": {"x": True, "y": 1.2, "w": 13, "h": 0},
        },
        0,
    )

    assert any("chart_type" in item for item in errors)
    assert any("layout.x 必须是数字" in item for item in errors)
    assert any("layout.y 必须是整数" in item for item in errors)
    range_errors = validate_dashboard_card_structure(
        {
            "title": "范围越界",
            "chart_type": "kpi",
            "dsl": {},
            "layout": {"x": 0, "y": 0, "w": 13, "h": 0},
        },
        1,
    )
    assert any("layout.w 必须在 1-12" in item for item in range_errors)
    assert any("layout.h 必须大于等于 1" in item for item in range_errors)

    missing_layout = validate_dashboard_card_structure({"title": "无布局", "chart_type": "kpi", "dsl": {}}, 1)
    assert missing_layout == ["卡片「无布局」缺少 layout 对象"]

    overflow = validate_dashboard_card_structure(
        {"title": "越界", "chart_type": "kpi", "dsl": {}, "layout": {"x": 10, "y": -1, "w": 3, "h": 1}},
        2,
    )
    assert any("layout.y 必须大于等于 0" in item for item in overflow)
    assert any("layout.x + layout.w 不能超过 12" in item for item in overflow)

    spec_errors = validate_dashboard_spec_structure({
        "dashboard_name": "",
        "semantic_config_id": "",
        "cards": [{"title": "无 DSL", "chart_type": "kpi", "layout": {"x": 0, "y": 0, "w": 3, "h": 2}}],
    })
    assert "dashboard_name 必填" in spec_errors
    assert "semantic_config_id 必填" in spec_errors
    assert any("缺少 dsl 对象" in item for item in spec_errors)


def test_build_dashboard_panel_plans_skips_non_dict_and_records_empty_sql():
    class EmptySqlGenerator:
        semantic_config = {}

        def generate_sql(self, **_kwargs):
            return {"success": True, "sql": "   "}

    plans, warnings = build_dashboard_panel_plans(
        [
            "skip",
            {"title": "空 SQL", "chart_type": "kpi", "dsl": {"metrics": ["订单数"]}},
        ],
        EmptySqlGenerator(),
    )

    assert len(plans) == 1
    assert plans[0]["error"] == "SQL 为空"
    assert warnings == ["卡片「空 SQL」创建失败：SQL 为空"]


def test_filter_executable_dashboard_panel_plans_keeps_existing_errors(monkeypatch):
    calls = []

    async def fake_preflight(*_args, **_kwargs):
        calls.append(True)
        return True, ""

    monkeypatch.setattr(
        "apps.chat_data_app.services.dashboard_spec_service.execute_dashboard_panel_sql_preflight",
        fake_preflight,
    )

    plans = [{"index": 0, "card": {"title": "已失败"}, "sql": "", "chart_type": "kpi", "error": "SQL 为空"}]
    marked, warnings = asyncio.run(filter_executable_dashboard_panel_plans(plans, datasource_id="ds_a"))

    assert marked == plans
    assert warnings == []
    assert calls == []


def test_create_dashboard_from_spec_payload_skips_non_dict_cards(monkeypatch):
    class FakeService:
        def __init__(self):
            self.added = []

        async def create_dashboard(self, **_kwargs):
            return {"id": "dash_1"}

        async def add_panel(self, **kwargs):
            self.added.append(kwargs)
            return {"id": "panel_1", "name": kwargs["name"]}

        async def get_dashboard(self, _dashboard_id):
            return {"id": "dash_1", "panels": [{"id": "panel_1"}]}

        async def delete_dashboard(self, _dashboard_id):
            return None

    async def fake_validate(**_kwargs):
        return {
            "valid": True,
            "warnings": [],
            "panels": [{"index": 1, "title": "有效卡片", "chart_type": "kpi", "sql": "select 1", "error": ""}],
        }

    service = FakeService()
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.validate_dashboard_spec_payload", fake_validate)
    monkeypatch.setattr("apps.chat_data_app.services.dashboard_spec_service.get_dashboard_service", lambda: service)

    result = asyncio.run(create_dashboard_from_spec_payload(
        dashboard_spec={"dashboard_name": "", "analysis_target": "  " + "x" * 300, "cards": ["skip", {"title": "", "chart_type": "kpi", "layout": {}}]},
        datasource_id="ds_a",
        semantic_config_id="sem_1",
        username="测试用户",
    ))

    assert result["dashboard"]["id"] == "dash_1"
    assert len(service.added) == 1
    assert service.added[0]["name"]
    assert service.added[0]["operator_name"] == "测试用户"
