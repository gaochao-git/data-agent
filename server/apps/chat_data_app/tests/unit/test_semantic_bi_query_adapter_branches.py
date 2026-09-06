"""``services/semantic/run/semantic_query_service.py`` 的分支补测。

聚焦 ``SemanticBIQueryAdapter`` 的校验/映射错误分支，以及模块级编译门面
（``compile_semantic_ai_dsl`` / ``compile_semantic_bi_dsl``）的参数守卫。
这些分支都是纯 dict 判断，不需要 mock 数据库或数据源管理器。

注意两处容易被误判的实现细节：
* ``_map_having_operand`` 的除零检查里 ``except ValueError: raise`` 会**重新抛出**
  ``float()`` 转换失败产生的 ValueError，因此「右操作数不是数字」只有传入
  ``None``（触发 TypeError）才会走到 ``except Exception: pass``；
  传 ``"abc"`` 会抛出 float 的原始英文错误。这里用测试固化该行为。
* ``_relationship_alias`` 读取的是扁平键 ``left_alias/left_table``，
  而不是嵌套的 ``{"left": {...}}`` 结构，fixture 里必须用扁平写法。
"""

import pytest

from apps.chat_data_app.services.semantic.run import semantic_query_service as sqs
from apps.chat_data_app.services.semantic.run.semantic_query_service import (
    SemanticBIQueryAdapter,
    compile_semantic_ai_dsl,
    compile_semantic_bi_dsl,
    load_runtime_semantic_config,
)


SEMANTIC_CONFIG = {
    "source_type": "mysql",
    "dimensions": [
        {
            "name": "城市",
            "table": "orders",
            "table_alias": "o",
            "column": "city",
            "logical_type": "text",
        },
        {
            "name": "下单日期",
            "table": "orders",
            "table_alias": "o",
            "column": "order_date",
            "logical_type": "date",
        },
        {
            "name": "客户等级",
            "table": "customers",
            "table_alias": "c",
            "column": "level",
            "logical_type": "text",
        },
    ],
    "metrics": [
        {
            "name": "GMV",
            "table": "orders",
            "table_alias": "o",
            "column": "amount",
            "expression": "SUM(o.amount)",
        },
    ],
    "relationships": [
        {
            "left_table": "orders",
            "left_alias": "o",
            "right_table": "customers",
            "right_alias": "c",
            "conditions": [{"left_column": "customer_id", "right_column": "id"}],
        },
    ],
}

# 两个字段完全同构的指标，用于触发「字段匹配到多个语义指标」的歧义分支
AMBIGUOUS_METRICS_CONFIG = {
    "source_type": "mysql",
    "dimensions": list(SEMANTIC_CONFIG["dimensions"]),
    "metrics": [
        {"name": "GMV", "table": "orders", "table_alias": "o", "column": "amount", "expression": "SUM(o.amount)"},
        {"name": "GMV_含税", "table": "orders", "table_alias": "o", "column": "amount", "expression": "SUM(o.amount)"},
    ],
    "relationships": list(SEMANTIC_CONFIG["relationships"]),
}


@pytest.fixture
def adapter():
    return SemanticBIQueryAdapter(dict(SEMANTIC_CONFIG))


def city_filter(**overrides):
    base = {"name": "城市", "operator": "=", "value": "北京"}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# 模块级辅助函数：_as_list / _has_filter_value
# --------------------------------------------------------------------------


def test_as_list_accepts_none_and_rejects_non_list():
    assert sqs._as_list(None) == []
    with pytest.raises(ValueError, match="语义可视化 DSL 字段必须是数组"):
        sqs._as_list({"not": "a list"})


def test_as_list_rejects_non_dict_element():
    with pytest.raises(ValueError, match=r"语义可视化 DSL orderBy\[1\] 必须是对象"):
        sqs._as_list([{"direction": "ASC"}, "oops"], "orderBy")


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("北京", True),
        (0, True),
        (False, True),
        ([], True),
    ],
)
def test_has_filter_value(adapter, value, expected):
    assert SemanticBIQueryAdapter._has_filter_value(value) is expected


# --------------------------------------------------------------------------
# _map_filter_nodes / _map_filter_node
# --------------------------------------------------------------------------


def test_map_filter_nodes_rejects_non_list(adapter):
    with pytest.raises(ValueError, match="语义可视化 filters 必须是数组"):
        adapter._map_filter_nodes({"and": []})


def test_map_filter_nodes_rejects_non_dict_element(adapter):
    with pytest.raises(ValueError, match=r"语义可视化 filters\[1\] 必须是对象"):
        adapter._map_filter_nodes([city_filter(), "oops"])


def test_map_filter_node_rejects_non_list_and_children(adapter):
    with pytest.raises(ValueError, match="语义可视化 and 过滤节点必须是数组"):
        adapter._map_filter_node({"and": "children"})
    with pytest.raises(ValueError, match="语义可视化 or 过滤节点必须是数组"):
        adapter._map_filter_node({"or": "children"})


def test_map_filter_node_rejects_empty_logical_group(adapter):
    with pytest.raises(ValueError, match="语义可视化 and 过滤节点不能为空"):
        adapter._map_filter_node({"and": []})
    with pytest.raises(ValueError, match="语义可视化 or 过滤节点不能为空"):
        adapter._map_filter_node({"or": []})


def test_map_filter_node_rejects_non_dict_child(adapter):
    with pytest.raises(ValueError, match=r"语义可视化 and\[0\] 必须是对象"):
        adapter._map_filter_node({"and": ["oops"]})
    with pytest.raises(ValueError, match=r"语义可视化 or\[1\] 必须是对象"):
        adapter._map_filter_node({"or": [city_filter(), "oops"]})


def test_map_filter_node_and_group_flattens_multi_leaf_child(adapter):
    """时间维度等值过滤会展开成两个叶子，and 组用 extend 展平（966->967）。"""
    mapped = adapter._map_filter_node(
        {"and": [{"name": "下单日期", "operator": "=", "value": "2024"}]}
    )
    assert len(mapped["and"]) == 2
    assert [item["operator"] for item in mapped["and"]] == [">=", "<"]


def test_map_filter_node_or_group_wraps_multi_leaf_child(adapter):
    """or 组不能展平叶子，必须包一层 and 保持语义（982->983）。"""
    mapped = adapter._map_filter_node(
        {"or": [{"name": "下单日期", "operator": "=", "value": "2024"}]}
    )
    assert len(mapped["or"]) == 1
    assert set(mapped["or"][0]) == {"and"}
    assert len(mapped["or"][0]["and"]) == 2


def test_map_filter_node_routes_left_right_to_having(adapter):
    """left/right 结构走 HAVING 映射（987->988）。"""
    mapped = adapter._map_filter_node(
        {
            "left": {"type": "metric", "name": "GMV"},
            "right": {"type": "literal", "value": 1000},
            "operator": ">",
        }
    )
    assert mapped["aggFunc"] == "SUM"
    assert mapped["rightOperand"] == {"type": "literal", "value": 1000}


def test_map_filter_node_routes_aggfunc_to_having(adapter):
    """带 aggFunc 的普通节点同样走 HAVING 映射（990->991）。

    ``_map_having_filter`` 只把 ``semanticName`` 透传给 ``_map_aggregation``，
    ``name`` 会被丢弃，因此这里必须用 ``semanticName`` 才能命中指标。
    """
    mapped = adapter._map_filter_node(
        {"semanticName": "GMV", "aggFunc": "SUM", "operator": ">", "value": 1000}
    )
    assert mapped["aggFunc"] == "SUM"
    assert "rightOperand" not in mapped


def test_map_filter_node_returns_multiple_leaves_as_list(adapter):
    """非时间组的多叶子直接返回 list，由 _map_filter_nodes 展平。"""
    mapped = adapter._map_filter_node({"name": "下单日期", "operator": "=", "value": "2024"})
    assert isinstance(mapped, list)
    assert len(mapped) == 2


# --------------------------------------------------------------------------
# _validate_filter_value
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operator", ["IS NULL", "IS NOT NULL", "EMPTY STRING"])
def test_validate_filter_value_skips_null_operators(adapter, operator):
    """空值类运算符不需要右侧值，直接放行（1006->1007）。"""
    adapter._validate_filter_value({"name": "城市", "operator": operator})


def test_validate_filter_value_requires_field_ref_target(adapter):
    with pytest.raises(ValueError, match="请选择右侧字段"):
        adapter._validate_filter_value(
            {"name": "城市", "valueType": "field_ref", "rightTable": "orders"}
        )


def test_validate_filter_value_accepts_complete_field_ref(adapter):
    adapter._validate_filter_value(
        {
            "name": "城市",
            "valueType": "field_ref",
            "rightTable": "customers",
            "rightColumn": "city",
        }
    )


def test_validate_filter_value_requires_expression_body(adapter):
    with pytest.raises(ValueError, match="请输入表达式"):
        adapter._validate_filter_value({"name": "城市", "valueType": "expression"})


@pytest.mark.parametrize(
    "value",
    [
        "2024-01-01",  # 非 list
        ["2024-01-01"],  # 长度不足
        ["2024-01-01", "2024-02-01", "2024-03-01"],  # 长度超出
        ["2024-01-01", "  "],  # 含空项
    ],
)
def test_validate_filter_value_between_requires_exact_two_values(adapter, value):
    with pytest.raises(ValueError, match="请输入完整的起止值"):
        adapter._validate_filter_value({"name": "下单日期", "operator": "BETWEEN", "value": value})


@pytest.mark.parametrize(
    "value",
    [
        "北京",  # 非 list
        [],  # 空 list
        ["北京", ""],  # 全是空项 / 含空项
    ],
)
def test_validate_filter_value_in_requires_non_empty_values(adapter, value):
    with pytest.raises(ValueError, match="请至少输入一个有效值"):
        adapter._validate_filter_value({"name": "城市", "operator": "IN", "value": value})


def test_validate_filter_value_not_in_requires_non_empty_values(adapter):
    with pytest.raises(ValueError, match="请至少输入一个有效值"):
        adapter._validate_filter_value({"name": "城市", "operator": "NOT IN", "value": []})


def test_validate_filter_value_requires_scalar_value(adapter):
    with pytest.raises(ValueError, match="请输入筛选值"):
        adapter._validate_filter_value({"name": "城市", "operator": "=", "value": None})


# --------------------------------------------------------------------------
# _copy_filter_value_fields
# --------------------------------------------------------------------------


def test_copy_filter_value_fields_resolves_field_ref_target(adapter):
    """field_ref 需要把右侧字段解析成语义模型里的表/列（1042->1050）。"""
    target = {}
    adapter._copy_filter_value_fields(
        {
            "valueType": "field_ref",
            "rightTable": "customers",
            "rightColumn": "level",
            "logic": "OR",
        },
        target,
    )
    assert target["logic"] == "OR"
    assert target["rightTable"] == "customers"
    assert target["rightTableAlias"] == "c"
    assert target["rightColumn"] == "level"


def test_copy_filter_value_fields_skips_right_side_for_literal(adapter):
    target = {}
    adapter._copy_filter_value_fields({"value": "北京", "valueType": "literal"}, target)
    assert target == {"value": "北京", "valueType": "literal"}


# --------------------------------------------------------------------------
# _map_having_operand
# --------------------------------------------------------------------------


def test_map_having_operand_rejects_non_dict(adapter):
    with pytest.raises(ValueError, match="HAVING 表达式操作数必须是对象"):
        adapter._map_having_operand("GMV")


def test_map_having_operand_rejects_unknown_metric(adapter):
    with pytest.raises(ValueError, match="HAVING 表达式引用的指标不存在"):
        adapter._map_having_operand({"type": "metric", "name": "不存在的指标"})


def test_map_having_operand_maps_metric(adapter):
    mapped = adapter._map_having_operand({"type": "metric", "name": "GMV"})
    assert mapped["type"] == "metric"
    assert mapped["name"] == "GMV"
    assert mapped["aggFunc"] == "SUM"
    assert mapped["column"] == "amount"


@pytest.mark.parametrize("value", [{"nested": 1}, [1, 2]])
def test_map_having_operand_rejects_container_literal(adapter, value):
    with pytest.raises(ValueError, match="literal.value 只支持字符串、数字、布尔值或空值"):
        adapter._map_having_operand({"type": "literal", "value": value})


def test_map_having_operand_accepts_scalar_literal(adapter):
    assert adapter._map_having_operand({"type": "literal", "value": None}) == {
        "type": "literal",
        "value": None,
    }


@pytest.mark.parametrize("operator", ["%", "MOD", ""])
def test_map_having_operand_rejects_unsupported_binary_operator(adapter, operator):
    with pytest.raises(ValueError, match="二元运算符只支持"):
        adapter._map_having_operand(
            {
                "type": "binary",
                "operator": operator,
                "left": {"type": "metric", "name": "GMV"},
                "right": {"type": "literal", "value": 1},
            }
        )


def test_map_having_operand_rejects_division_by_zero(adapter):
    with pytest.raises(ValueError, match="不允许除以 0"):
        adapter._map_having_operand(
            {
                "type": "binary",
                "operator": "/",
                "left": {"type": "metric", "name": "GMV"},
                "right": {"type": "literal", "value": 0},
            }
        )


def test_map_having_operand_allows_non_zero_divisor(adapter):
    """除零检查通过时正常返回二元节点（1100->1106）。"""
    mapped = adapter._map_having_operand(
        {
            "type": "binary",
            "operator": "/",
            "left": {"type": "metric", "name": "GMV"},
            "right": {"type": "literal", "value": 100},
        }
    )
    assert mapped["operator"] == "/"
    assert mapped["right"] == {"type": "literal", "value": 100}


def test_map_having_operand_tolerates_non_numeric_divisor(adapter):
    """除零检查里 float() 抛 TypeError 时静默跳过（1104->1105）。

    只有非 ValueError 异常才会走到 ``except Exception: pass``；
    ``value=None`` 触发 TypeError，是唯一能覆盖该分支的取值。
    """
    mapped = adapter._map_having_operand(
        {
            "type": "binary",
            "operator": "/",
            "left": {"type": "metric", "name": "GMV"},
            "right": {"type": "literal", "value": None},
        }
    )
    assert mapped["operator"] == "/"
    assert mapped["right"] == {"type": "literal", "value": None}


def test_map_having_operand_non_numeric_string_divisor_surfaces_float_error(adapter):
    """固化现状：非数字字符串会抛出 float 的原始错误，而不是友好提示。

    ``except ValueError: raise`` 会重新抛出 ``float("abc")`` 的 ValueError，
    导致错误文案对用户不友好。这里锁定该行为，便于后续修复时对照。
    """
    with pytest.raises(ValueError) as excinfo:
        adapter._map_having_operand(
            {
                "type": "binary",
                "operator": "/",
                "left": {"type": "metric", "name": "GMV"},
                "right": {"type": "literal", "value": "abc"},
            }
        )
    assert "不允许除以 0" not in str(excinfo.value)


def test_map_having_operand_maps_nested_binary(adapter):
    mapped = adapter._map_having_operand(
        {
            "type": "binary",
            "operator": "+",
            "left": {"type": "metric", "name": "GMV"},
            "right": {"type": "literal", "value": 10},
        }
    )
    assert mapped["type"] == "binary"
    assert mapped["left"]["name"] == "GMV"
    assert mapped["right"] == {"type": "literal", "value": 10}


def test_map_having_operand_rejects_unknown_type(adapter):
    with pytest.raises(ValueError, match="HAVING 表达式操作数只支持 metric、literal、binary"):
        adapter._map_having_operand({"type": "column"})


# --------------------------------------------------------------------------
# _map_having_filter
# --------------------------------------------------------------------------


def test_map_having_filter_requires_metric_on_left(adapter):
    with pytest.raises(ValueError, match="HAVING 表达式 left 只支持 metric"):
        adapter._map_having_filter(
            {
                "left": {"type": "literal", "value": 100},
                "right": {"type": "literal", "value": 50},
                "operator": ">",
            }
        )


def test_map_having_filter_honours_explicit_logic(adapter):
    """left/right 形态下显式 logic 覆盖默认值（1191->1192）。"""
    mapped = adapter._map_having_filter(
        {
            "left": {"type": "metric", "name": "GMV"},
            "right": {"type": "literal", "value": 1000},
            "operator": ">",
            "logic": "OR",
        }
    )
    assert mapped["logic"] == "OR"


def test_map_having_filter_defaults_logic_and_copies_value(adapter):
    mapped = adapter._map_having_filter(
        {"semanticName": "GMV", "aggFunc": "SUM", "operator": ">", "value": 1000}
    )
    assert mapped["logic"] == "AND"
    assert mapped["value"] == 1000


# --------------------------------------------------------------------------
# _map_order_by
# --------------------------------------------------------------------------


def test_map_order_by_rejects_invalid_direction(adapter):
    with pytest.raises(ValueError, match="排序方向只能是 ASC 或 DESC"):
        adapter._map_order_by({"direction": "UP", "name": "城市"}, [], [])


# --------------------------------------------------------------------------
# 字段解析的歧义与缺失
# --------------------------------------------------------------------------


def test_resolve_dimension_config_rejects_unknown_dimension(adapter):
    with pytest.raises(ValueError, match="语义维度不存在或不在当前指标集内"):
        adapter._resolve_dimension_config({"name": "不存在的维度"})


def test_resolve_metric_config_rejects_ambiguous_field_match():
    """同构字段命中多个指标时要求调用方显式指定（888->890）。"""
    adapter = SemanticBIQueryAdapter(dict(AMBIGUOUS_METRICS_CONFIG))
    with pytest.raises(ValueError, match="指标字段匹配到多个语义指标"):
        adapter._resolve_metric_config({"table": "orders", "tableAlias": "o", "column": "amount", "func": "SUM"})


def test_resolve_metric_config_returns_none_when_unmatched(adapter):
    assert adapter._resolve_metric_config({"name": "不存在的指标"}) is None


def test_map_aggregation_rejects_unsupported_func_on_dimension(adapter):
    """维度当指标用且聚合函数不在白名单时报错（920->921）。"""
    with pytest.raises(ValueError, match="未配置为指标，不能作为聚合指标使用"):
        adapter._map_aggregation({"name": "城市", "func": "MEDIAN"})


# --------------------------------------------------------------------------
# 主表推断
# --------------------------------------------------------------------------


def test_resolve_main_table_falls_back_to_filter_table(adapter):
    """维度和指标都没带表名时，从 filters 里找主表（1323->1326）。"""
    filters = adapter._map_filter_nodes([city_filter()])
    table, alias = adapter._resolve_main_table([], [], filters, [])
    assert table == "orders"
    assert alias == "o"


def test_resolve_main_table_raises_when_no_table_anywhere(adapter):
    with pytest.raises(ValueError, match="语义可视化查询无法确定主表"):
        adapter._resolve_main_table([], [], [], [])


def test_first_filter_table_recurses_into_logical_group(adapter):
    """_first_filter_table 读的是节点的 table/tableAlias，只对已映射节点有效。"""
    leaf = adapter._map_filter_nodes([city_filter()])[0]
    assert adapter._first_filter_table({"and": [leaf]}) == ("orders", "o")
    assert adapter._first_filter_table({"or": [leaf]}) == ("orders", "o")
    assert adapter._first_filter_table({"and": []}) == ("", "")
    assert adapter._first_filter_table({"and": ["skipped"]}) == ("", "")


def test_collect_filter_tables_includes_field_ref_target(adapter):
    """field_ref 过滤会把右侧表也算进需要的表集合（1290->1291）。"""
    needed = set()
    SemanticBIQueryAdapter._collect_filter_tables(
        {
            "table": "orders",
            "tableAlias": "o",
            "valueType": "field_ref",
            "rightTable": "customers",
        },
        needed,
    )
    assert needed == {"o", "customers"}


def test_collect_filter_tables_recurses_logical_groups(adapter):
    """and / or 组要递归下探，非 dict 子节点跳过（1276->1285）。"""
    needed = set()
    SemanticBIQueryAdapter._collect_filter_tables(
        {
            "and": [
                {"table": "orders", "tableAlias": "o"},
                {"or": [{"table": "customers", "tableAlias": "c"}, "skipped"]},
                "skipped",
            ]
        },
        needed,
    )
    assert needed == {"o", "c"}


# --------------------------------------------------------------------------
# 编译门面的参数守卫
# --------------------------------------------------------------------------


def test_compile_semantic_ai_dsl_rejects_non_dict_arguments():
    assert compile_semantic_ai_dsl("not a dict", {})["error"] == "语义模型必须是对象"
    assert compile_semantic_ai_dsl({}, "not a dict")["error"] == "语义 DSL 必须是对象"


def test_compile_semantic_bi_dsl_rejects_non_dict_arguments():
    assert compile_semantic_bi_dsl("not a dict", {})["error"] == "语义模型必须是对象"
    assert compile_semantic_bi_dsl({}, "not a dict")["error"] == "BI 语义 DSL 必须是对象"


def test_compile_semantic_bi_dsl_wraps_adapter_errors():
    result = compile_semantic_bi_dsl(SEMANTIC_CONFIG, {"dimensions": [], "aggregations": []})
    assert result["success"] is False
    assert "至少需要一个维度或指标" in result["error"]


def test_compile_semantic_bi_dsl_returns_query_plan():
    result = compile_semantic_bi_dsl(
        SEMANTIC_CONFIG,
        {"dimensions": [{"name": "城市"}], "aggregations": [{"name": "GMV"}]},
    )
    assert result["success"] is True
    assert result["query_plan"]["mainTable"] == "orders"
    assert result["query_plan"] == result["builder_config"]


def test_compile_semantic_bi_dsl_captures_type_error():
    """adapt 抛 TypeError 时同样被收敛成失败结果（1434->1435）。"""
    result = compile_semantic_bi_dsl(SEMANTIC_CONFIG, {"dimensions": [{}], "aggregations": []})
    assert result["success"] is False


def test_adapt_rejects_non_dict_dsl(adapter):
    with pytest.raises(ValueError, match="语义可视化 DSL 格式错误"):
        adapter.adapt(["not", "a", "dict"])


def test_adapt_rejects_forbidden_frontend_keys(adapter):
    with pytest.raises(ValueError, match="不允许包含前端表关系字段"):
        adapter.adapt({"dimensions": [{"name": "城市"}], "joins": []})


# --------------------------------------------------------------------------
# load_runtime_semantic_config 的错误改写
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_runtime_semantic_config_requires_ids():
    with pytest.raises(ValueError, match="缺少 datasource_id 或 semantic_config_id"):
        await load_runtime_semantic_config("", "cfg-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message, expected",
    [
        ("数据源不存在", "数据源不存在: ds-1"),
        ("未找到可用语义配置", "语义配置不存在: cfg-1"),
    ],
)
async def test_load_runtime_semantic_config_maps_to_lookup_error(monkeypatch, message, expected):
    async def _boom(*args, **kwargs):
        raise ValueError(message)

    monkeypatch.setattr(sqs, "load_semantic_model", _boom)

    with pytest.raises(LookupError, match=expected):
        await load_runtime_semantic_config("ds-1", "cfg-1", user_id="u-1")


@pytest.mark.asyncio
async def test_load_runtime_semantic_config_maps_disabled_config(monkeypatch):
    """语义配置未启用仍是 ValueError，但要带上配置 ID（1361->1363）。"""

    async def _boom(*args, **kwargs):
        raise ValueError("语义配置未启用")

    monkeypatch.setattr(sqs, "load_semantic_model", _boom)

    with pytest.raises(ValueError, match="语义配置未启用: cfg-1"):
        await load_runtime_semantic_config("ds-1", "cfg-1")


@pytest.mark.asyncio
async def test_load_runtime_semantic_config_reraises_other_errors(monkeypatch):
    async def _boom(*args, **kwargs):
        raise ValueError("数据库连接失败")

    monkeypatch.setattr(sqs, "load_semantic_model", _boom)

    with pytest.raises(ValueError, match="数据库连接失败"):
        await load_runtime_semantic_config("ds-1", "cfg-1")


def test_validate_filter_value_rejects_unsafe_expression(adapter):
    """expression 筛选值与 AI 链路同口径：注释/分号/写操作关键字一律拒绝。"""
    for unsafe in ("1; DROP TABLE users", "1 -- comment", "1 /* block */"):
        with pytest.raises(ValueError, match="只允许单条只读 SQL 表达式"):
            adapter._validate_filter_value(
                {"name": "城市", "operator": "=", "valueType": "expression", "rightExpression": unsafe}
            )


def test_validate_filter_value_allows_normal_expression(adapter):
    adapter._validate_filter_value(
        {"name": "城市", "operator": "IN", "valueType": "expression", "rightExpression": "(SELECT city FROM white_list)"}
    )
