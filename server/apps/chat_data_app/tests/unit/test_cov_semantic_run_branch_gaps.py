"""semantic 运行链路剩余分支补测。

覆盖目标（均来自全量覆盖率审计的缺失弧）：
- semantic_quality_checker：跨模型辅助函数、声明键收集与关系边去噪；
- semantic_history_learning_service：表达式字段提取、人工复核原因、样本组装、
  跳过计数与 LLM 失败透传；
- chat_data_ossie_importer：旧根形状、无定位信息的 YAML 错误、枚举清洗、
  字典去重、空关联条件与校验清单计数；
- semantic_conversation_context：空数据源上下文、未启用指标集、授权回退与
  查询命中重叠去重；
- semantic_ai_query_adapter / semantic_bi_query_adapter / query_plan_compiler /
  filter_compiler / query_plan / formatter / plan builder / repository 的
  单点防御分支。

所有用例均为纯内存 dict / 假对象驱动，不触碰数据库、Redis、网络或时钟。
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from apps.chat_data_app.services.semantic.core import formatter
from apps.chat_data_app.services.semantic.core import query_plan as core_query_plan
from apps.chat_data_app.services.semantic.core.semantic_sql import filter_compiler, query_plan_compiler
from apps.chat_data_app.services.semantic.run import chat_data_ossie_importer as importer
from apps.chat_data_app.services.semantic.run import semantic_ai_query_adapter as ai_adapter
from apps.chat_data_app.services.semantic.run import semantic_bi_query_adapter as bi_adapter
from apps.chat_data_app.services.semantic.run import semantic_conversation_context as context_module
from apps.chat_data_app.services.semantic.run import semantic_history_learning_service as history_module
from apps.chat_data_app.services.semantic.run import semantic_model_repository as repository
from apps.chat_data_app.services.semantic.run import semantic_quality_checker as quality_checker
from apps.chat_data_app.services.semantic.run import semantic_query_plan_builder as plan_builder

SEMANTIC_CONFIG = {
    "source_type": "mysql",
    "dimensions": [
        {"name": "城市", "table": "orders", "table_alias": "o", "column": "city", "logical_type": "text"},
    ],
    "metrics": [
        {"name": "GMV", "table": "orders", "table_alias": "o", "column": "amount", "expression": "SUM(o.amount)"},
        {"name": "总行数", "table": "orders", "table_alias": "o", "column": "*", "expression": "COUNT(*)"},
        {"name": "无列指标", "table": "orders", "table_alias": "o", "expression": "COUNT(*)"},
    ],
    "relationships": [],
}


# ---------------------------------------------------------------- quality checker


def test_check_derived_or_composite_ignores_unknown_kind():
    raw: list[tuple[str, str]] = []
    quality_checker.SemanticQualityChecker._check_derived_or_composite(
        {"name": "m", "metric_kind": "other"}, set(), raw
    )
    assert raw == []


def test_unresolved_formula_tokens_skips_blank_names_and_repeated_tokens():
    checker = quality_checker.get_semantic_quality_checker()
    assert checker._unresolved_formula_tokens("foo + foo + bar", {"", "bar"}) == ["foo"]


def test_declared_table_keys_tolerates_junk_and_duplicates():
    config = {
        "tables": [
            "junk",
            {"table": ""},
            {
                "table": "T",
                "primary_keys": [],
                "unique_indexes": [{"columns": ["id"]}, {"columns": ["id"]}, {"columns": []}],
                "manual_indexes": [{"index_type": "normal", "columns": ["x"]}, "junk"],
            },
            {"table": "T2"},
        ]
    }
    assert quality_checker.SemanticQualityChecker._declared_table_keys(config) == {"t": [{"id"}]}


def test_relationship_key_coverage_skips_incomplete_relationships():
    raw: list[tuple[str, str]] = []
    quality_checker.SemanticQualityChecker._check_relationship_key_coverage(
        [
            {"left_table": "", "right_table": "users"},
            {"left_table": "orders", "right_table": "users", "join_conditions": []},
        ],
        raw,
        {"users": [{"id"}]},
    )
    assert raw == []


def test_duplicate_expressions_skips_blank_expression():
    raw: list[tuple[str, str]] = []
    owner = quality_checker.SemanticQualityChecker._collect_duplicate_expressions(
        [{"name": "m", "expression": "  "}], raw
    )
    assert owner == {}
    assert raw == []


def test_add_relationship_edge_ignores_blank_sides():
    raw: list[tuple[str, str]] = []
    seen_pairs: dict[tuple[str, str, str], int] = {}
    edges: dict[str, set[str]] = {}
    quality_checker.SemanticQualityChecker._add_relationship_edge(
        {"left_table": "", "right_table": "", "join_conditions": [None]},
        set(),
        raw,
        seen_pairs,
        edges,
    )
    assert raw == []
    assert edges == {}
    assert seen_pairs == {("", "", ""): 1}


def test_cross_model_helpers_ignore_blank_model_and_non_dict_config():
    owners: dict[str, dict[str, str]] = {}
    quality_checker._add_config_metric_owners({"name": "  "}, owners)
    assert owners == {}
    assert quality_checker._collect_metric_owners(
        ["junk", {"name": "m", "metrics": [{"name": "x", "expression": "SUM(a)"}]}]
    ) == {"x": {"m": "SUM(A)"}}


# ---------------------------------------------------------------- history learning


def test_extract_expression_columns_skips_blank_backtick_token():
    assert history_module._extract_expression_columns("` `") == set()


def test_dimension_review_reasons_flags_missing_column():
    assert history_module._dimension_review_reasons({"name": "d"}, "", {}) == ["缺少字段名，需要人工确认"]


def test_metric_review_reasons_flags_missing_expression():
    assert history_module._metric_review_reasons({"name": "m"}, "", {}) == ["缺少指标表达式，需要人工确认"]


def test_history_suggestion_review_reasons_flags_missing_table():
    reasons = history_module._history_suggestion_review_reasons({"name": "m"}, "metrics", {})
    assert reasons[0] == "缺少表名，需要人工确认"


def test_build_sample_attaches_builder_config():
    execution = SimpleNamespace(
        execution_id="e1",
        task_id="t1",
        sql_status="succeeded",
        sql_text="select 1",
        error_message=None,
        finished_time=None,
        started_time=None,
        create_time=None,
    )
    task = SimpleNamespace(error_message="")
    payload = {"conversation_id": "c1", "query_text": "q", "builder_config": {"mainTable": "orders"}}

    sample = history_module.SemanticHistoryLearningService._build_sample(
        execution, task, payload, "builder", ["orders"]
    )

    assert sample["builder_config"] == {"mainTable": "orders"}


def test_record_config_skip_ignores_unknown_reason():
    skipped = {"semantic_config": 0}
    stats = {"builder": {"scanned": 0, "matched": 0, "skipped": 0}}

    history_module.SemanticHistoryLearningService._record_config_skip(
        "builder",
        "unknown_reason",
        skipped,
        stats,
        "ds",
        "cfg",
        SimpleNamespace(sql_text="select 1"),
        "",
    )

    assert skipped == {"semantic_config": 1}
    assert stats["builder"]["skipped"] == 1


@pytest.mark.asyncio
async def test_generate_history_suggestions_reraises_llm_failure():
    class _FailingLLM:
        async def ainvoke(self, _messages):
            raise RuntimeError("llm down")

    def factory(**_kwargs):
        return _FailingLLM()

    with pytest.raises(RuntimeError, match="llm down"):
        await history_module._generate_history_learning_suggestions_by_llm(
            {"id": "ds", "ds_tables": []},
            {"id": "cfg", "metrics": [], "dimensions": []},
            [{"execution_id": "e1", "source": "sql", "sql": "select 1"}],
            llm_factory=factory,
        )


# ---------------------------------------------------------------- ossie importer


def test_normalize_document_root_rejects_non_mapping_model():
    with pytest.raises(ValueError, match="必须是映射"):
        importer._normalize_document_root({"semantic_model": ["bad"]})


def test_parse_document_reports_yaml_error_without_mark(monkeypatch):
    def _raise(_content):
        raise importer.yaml.YAMLError("broken")

    monkeypatch.setattr(importer.yaml, "safe_load", _raise)

    with pytest.raises(ValueError, match="不是合法的 YAML"):
        importer.parse_ossie_document("bad")


def test_enum_values_skips_non_dict_items():
    assert importer._enum_values_from_raw(["junk", {"value": "A", "label": "a"}]) == [{"value": "A", "label": "a"}]


def test_record_column_dict_skips_duplicate_target():
    result = importer.OssieSemanticModelImport(name="m")
    result.column_dicts.append({"table": "t", "column": "c", "dict": {"unit": "元"}})

    importer.OssieToChatDataImporter._record_column_dict(result, {"column_dict": {"unit": "元"}}, "t", "c")

    assert result.column_dicts == [{"table": "t", "column": "c", "dict": {"unit": "元"}}]


def test_convert_relationship_without_join_conditions(monkeypatch):
    monkeypatch.setattr(
        importer.OssieToChatDataImporter,
        "_relation_columns",
        staticmethod(lambda *_args, **_kwargs: ([], [])),
    )
    relationship = SimpleNamespace(
        from_dataset="orders",
        to="users",
        from_columns=["user_id"],
        to_columns=["id"],
        custom_extensions=[],
    )
    result = importer.OssieSemanticModelImport(name="m")

    importer.OssieToChatDataImporter()._convert_relationship(relationship, 0, result)

    assert result.relationships[0]["join_conditions"] == []
    assert "left_column" not in result.relationships[0]


def test_quality_severity_counts_ignores_unknown_severity():
    assert importer._quality_severity_counts([{"severity": "unknown"}, {"severity": "error"}]) == {
        "error": 1,
        "warning": 0,
        "info": 0,
    }


def test_append_dict_row_summarizes_more_than_five_items():
    item = importer.OssieSemanticModelImport(name="m")
    item.column_dicts.append({"table": "t", "column": "c1", "dict": {"unit": "元"}})
    plan = [{"table": "t", "column": f"c{index}", "fields": ["unit"]} for index in range(6)]
    checklist: list[dict[str, str]] = []

    importer._append_dict_row(checklist, item, plan)

    assert "等 6 项" in checklist[-1]["message"]


# ---------------------------------------------------------------- conversation context


def test_resolved_semantic_config_id_backfills_resolved_config():
    assert context_module._resolved_semantic_config_id(None, {"semantic_config_id": "cfg"}) == "cfg"


@pytest.mark.asyncio
async def test_prepare_context_without_datasource_returns_empty_context(monkeypatch):
    def _unexpected_manager():
        raise AssertionError("空数据源不应加载数据源管理器")

    monkeypatch.setattr(context_module, "get_datasource_manager", _unexpected_manager)

    context = await context_module.prepare_semantic_conversation_context(
        datasource_id="  ",
        semantic_config_id="cfg",
        member_datasource_ids=["m1"],
        user_id="u",
        username="n",
        user_query="查询订单",
    )

    assert context.datasource_id is None
    assert context.semantic_config_error == "未找到可用指标集: cfg"
    assert context.selected_member_datasource_ids == ["m1"]


def test_semantic_config_error_reports_disabled_config():
    assert context_module._semantic_config_error(semantic_config_id="cfg", resolved_config={"enabled": False}) == (
        "当前指标集未启用: cfg"
    )


@pytest.mark.asyncio
async def test_load_authorized_targets_falls_back_without_datasource_meta():
    class _Manager:
        async def get_authorized_execution_datasources_async(self, datasource_id, member_ids, *, user_id, username):
            assert datasource_id == "ds"
            return [{"member_datasource_id": member_ids[0], "user_id": user_id, "user": username}, "junk"]

    targets = await context_module._load_authorized_execution_targets(
        manager=_Manager(),
        datasource_id="ds",
        datasource_meta=None,
        member_datasource_ids=["m1"],
        user_id="u",
        username="n",
    )

    assert targets == [{"member_datasource_id": "m1", "user_id": "u", "user": "n"}]


def test_match_execution_targets_skips_target_without_id_and_overlapping_span():
    targets = [
        {"member_datasource_id": "", "member_name": "无编号机构"},
        {"member_datasource_id": "m1", "member_name": "ab"},
        {"member_datasource_id": "m2", "member_name": "bc"},
    ]

    assert context_module.match_execution_targets_from_query(targets, "abc") == ["m1"]


# ---------------------------------------------------------------- ai / bi adapters


@pytest.fixture
def ai_query_adapter():
    return ai_adapter.SemanticAIQueryAdapter(dict(SEMANTIC_CONFIG))


def test_build_aggregations_without_parsed_column(monkeypatch, ai_query_adapter):
    monkeypatch.setattr(ai_adapter, "metric_func_column", lambda _expression: ("COUNT", ""))
    plan = ai_adapter._UnifiedEnginePlan(main_table="orders", main_table_alias="o")
    plan.matched_metrics = [
        {"name": "总行数", "table": "orders", "table_alias": "o", "column": "*", "expression": "COUNT(*)"}
    ]

    ai_query_adapter._build_builder_aggregations(plan)

    assert plan.builder_aggregations[0]["column"] == "*"


def test_append_order_by_entry_metric_without_column(monkeypatch, ai_query_adapter):
    monkeypatch.setattr(ai_adapter, "metric_func_column", lambda _expression: ("COUNT", ""))
    ai_query_adapter._metric_base_cfgs = {}
    plan = ai_adapter._UnifiedEnginePlan(main_table="orders", main_table_alias="o")

    ai_query_adapter._append_order_by_entry(plan, {"field": "无列指标", "direction": "ASC"})

    assert plan.builder_order_by[0]["column"] == "*"


def test_collect_tables_needed_ignores_blocks_without_alias(ai_query_adapter):
    plan = ai_adapter._UnifiedEnginePlan(main_table_alias="o")
    plan.builder_dimensions = [{"column": "city"}]
    plan.builder_aggregations = [{"alias": "gmv"}]
    plan.builder_having = [{"rightOperand": None}]
    plan.builder_order_by = [{"column": "city"}]

    ai_query_adapter._collect_tables_needed(plan)

    assert plan.tables_needed == {"o"}


def test_bi_validate_between_operator_accepts_two_values():
    bi_adapter.SemanticBIQueryAdapter._validate_operator_filter_value("BETWEEN", ["a", "b"], "标签")


def test_bi_resolve_main_table_requires_table_in_filters():
    adapter = bi_adapter.SemanticBIQueryAdapter(dict(SEMANTIC_CONFIG))

    with pytest.raises(ValueError, match="无法确定主表"):
        adapter._resolve_main_table([], [], [{"table": ""}], [])


# ---------------------------------------------------------------- core compilers


def test_validate_query_plan_rejects_non_dict():
    with pytest.raises(ValueError, match="查询计划格式错误"):
        query_plan_compiler._validate_query_plan(None)


def test_build_order_by_fields_uses_group_and_dimension_expressions():
    common_kwargs = {
        "aggregations": [],
        "agg_by_id": {},
        "main_alias": "orders",
        "is_multi_table": False,
        "dimension_alias_map": {},
        "dimension_expr_map": {},
        "has_real_aggregation": False,
    }
    order_by = [{"column": "city", "direction": "asc"}]

    group_fields = query_plan_compiler._build_order_by_fields(
        order_by,
        group_expr_map={("orders", "city"): "`orders`.`city`"},
        **common_kwargs,
    )
    dimension_fields = query_plan_compiler._build_order_by_fields(
        order_by,
        group_expr_map={},
        **{**common_kwargs, "dimension_expr_map": {("orders", "city"): "`city`"}},
    )

    assert group_fields == ["`orders`.`city` ASC"]
    assert dimension_fields == ["`city` ASC"]


def test_ensure_safe_sql_expression_rejects_blank_expression():
    with pytest.raises(ValueError, match="不能为空"):
        filter_compiler.ensure_safe_sql_expression("   ")


def test_parse_quoted_sql_literal_unwraps_double_quoted_value():
    assert filter_compiler._parse_quoted_sql_literal('"paid"') == "paid"


def test_build_having_operand_expr_returns_null_for_unknown_type():
    assert filter_compiler.build_having_operand_expr({"type": "unknown"}) == "NULL"


def test_build_ordered_joins_stops_when_no_relationship_matches():
    assert core_query_plan.SemanticQueryPlanContext._build_ordered_joins("orders", {"orders", "users"}, []) == []
    assert core_query_plan.SemanticQueryPlanContext._select_next_relationship({"orders"}, {"users"}, []) is None


def test_format_expression_operand_handles_non_dict_and_unknown_type():
    assert formatter._format_expression_operand("literal") == ""
    assert formatter._format_expression_operand({"type": "unknown"}) == ""


# ---------------------------------------------------------------- plan builder / repository


def test_between_period_range_accepts_timestamp_start():
    start, end = plan_builder._between_period_range({"start": "2024-01-01 08:00:00", "end": "2024-01-02 18:30:00"})

    assert start == "2024-01-01 00:00:00"
    assert end == "2024-01-02 18:30:00"


def test_build_metric_base_configs_falls_back_on_invalid_derived_metric():
    base_cfgs, fixed_leaves = plan_builder.build_metric_base_configs(
        {"m": {"name": "m", "metric_kind": "derived", "base_metric": "missing"}},
        {},
    )

    assert base_cfgs["m"]["metric_kind"] == "derived"
    assert fixed_leaves["m"] == []


@pytest.mark.asyncio
async def test_list_directory_without_datasource_returns_empty():
    assert await repository.list_runtime_semantic_config_directory(None) == []
