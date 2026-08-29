"""AI/BI 语义查询适配器共享的查询计划组装原语。

只做查询计划节点的形状组装与无校验的树遍历，不做输入校验、不产生用户可见
错误文案；各入口的校验与报错契约由适配器自己保留（AI 侧上游已过
InputValidator 校验故宽松，BI 侧直面原始前端输入故严格）。
任何影响两条入口的横切语义（新过滤操作符、行级权限注入等）应优先落在
本模块与 core/semantic_sql 编译层，而不是在单个适配器里各改一份。
"""

from typing import Any, Callable, Dict, Set, Tuple

from ..core.query_plan import semantic_table_alias, parse_aggregate_expression


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def metric_func_column(expression: Any) -> Tuple[str, str] | None:
    """解析 `SUM(col)` / `COUNT_DISTINCT(col)` 形态的聚合表达式。

    返回 (聚合函数, 列名)：列名取最后一个列段并去除反引号与空白，`*` 原样保留；
    无法解析时返回 None。
    """
    parsed = parse_aggregate_expression(normalize_text(expression))
    if not parsed:
        return None
    func, column_expr = parsed
    column = "*" if column_expr == "*" else column_expr.split(".")[-1].replace("`", "").strip()
    return func, column


def builder_node_alias(item: Any) -> str:
    """查询计划节点别名：优先 builder 驼峰 tableAlias，回退语义配置别名。"""
    camel_alias = normalize_text((item or {}).get("tableAlias"))
    if camel_alias:
        return camel_alias
    return semantic_table_alias(item)


def assemble_dimension_filter_node(
    dim_cfg: Dict[str, Any],
    *,
    node_id: str,
    operator: Any,
    logic: Any = "AND",
    expression: Any = None,
) -> Dict[str, Any]:
    """组装维度过滤计划节点的身份字段（表定位/操作符/逻辑）。

    值字段（value/valueType/rightExpression 等）语义两条入口不同，由各适配器
    在返回节点上自行补充。
    """
    config = dim_cfg or {}
    table = config.get("table")
    return {
        "id": node_id,
        "table": table,
        "tableAlias": semantic_table_alias(config) or table,
        "column": config.get("column"),
        "expression": expression if expression is not None else config.get("expression"),
        "operator": operator,
        "logic": logic,
    }


def collect_filter_node_tables(
    node: Any,
    tables_needed: Set[str],
    leaf_table_extractor: Callable[[Dict[str, Any], Set[str]], None],
) -> None:
    """递归收集 and/or 过滤树涉及的表。

    叶子表提取交给调用方；非 dict 子节点静默跳过（两条入口现有行为一致）。
    """
    if not isinstance(node, dict):
        return
    if "and" in node:
        for child in node.get("and") or []:
            collect_filter_node_tables(child, tables_needed, leaf_table_extractor)
        return
    if "or" in node:
        for child in node.get("or") or []:
            collect_filter_node_tables(child, tables_needed, leaf_table_extractor)
        return
    leaf_table_extractor(node, tables_needed)


def collect_operand_tables(
    operand: Any,
    tables_needed: Set[str],
    metric_table_extractor: Callable[[Dict[str, Any], Set[str]], None],
) -> None:
    """递归收集 HAVING 操作数树（metric/binary）涉及的表。"""
    if not isinstance(operand, dict):
        return
    operand_type = normalize_text(operand.get("type")).lower()
    if operand_type == "metric":
        metric_table_extractor(operand, tables_needed)
        return
    if operand_type == "binary":
        collect_operand_tables(operand.get("left"), tables_needed, metric_table_extractor)
        collect_operand_tables(operand.get("right"), tables_needed, metric_table_extractor)
