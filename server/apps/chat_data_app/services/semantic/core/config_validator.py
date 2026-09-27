"""语义模型配置的结构校验原子件与编排函数。

判定与错误文案的单点来源：本模块的编排函数（_validate_* / validate_semantic_relationships）
被 run 层质量检查器的 structure 域复用；保存链路的结构硬门槛走
run/semantic_quality_checker.first_structure_error（本模块不再提供独立入口）。
表达式解析原子件（expression_uses_physical_column 等）同时供 service 层直接使用。
"""

import re
from typing import Any, Dict, List, Optional, Set

from .common import is_time_logical_dimension
from .input_validator import FILTER_OPERATORS, _NO_VALUE_FILTER_OPERATORS
from .query_plan import (
    DERIVED_PERIOD_KINDS,
    FORMULA_OPERATOR_PATTERN,
    FORMULA_REF_PATTERN,
    is_composite_metric,
    is_derived_metric,
    parse_aggregate_expression,
    parse_formula_refs,
)


def normalize_semantic_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("`", "")


def expression_uses_physical_column(expression: str, table_columns: Set[str]) -> bool:
    if not expression:
        return False
    expression_without_quotes = expression.replace("`", "")
    for column in table_columns:
        escaped_column = re.escape(column)
        pattern = rf"(^|[^A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\.)?{escaped_column}([^A-Za-z0-9_]|$)"
        if re.search(pattern, expression_without_quotes, flags=re.IGNORECASE):
            return True
    return False


def _validate_field_display_names(dimensions: List[Dict], metrics: List[Dict]) -> Optional[str]:
    """维度/指标的显示名称不能为空且不能重复。"""
    seen_names: Set[str] = set()
    for item in [*(dimensions or []), *(metrics or [])]:
        display_name = str((item or {}).get("name") or "").strip()
        normalized_name = normalize_semantic_text(display_name)
        if not normalized_name:
            return "字段显示名称不能为空"
        if normalized_name in seen_names:
            return f"字段显示名称重复: {display_name}"
        seen_names.add(normalized_name)
    return None


def _validate_dimensions_against_tables(dimensions: List[Dict], table_columns_map: Dict[str, Set[str]]) -> Optional[str]:
    for dimension in dimensions or []:
        table_name = dimension.get("table")
        column_name = dimension.get("column")
        if not table_name or table_name not in table_columns_map:
            return f"维度 {dimension.get('name', '')} 所属表不存在: {table_name}"
        if not column_name or column_name not in table_columns_map[table_name]:
            return f"维度 {dimension.get('name', '')} 字段不存在: {table_name}.{column_name}"
        allowed_operators_error = _validate_allowed_operators(dimension)
        if allowed_operators_error:
            return allowed_operators_error
    return None


_FUNCTION_CALL_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*\(")
# 标识符提取用 Unicode 感知 pattern（\w 原生含 CJK）：非 ASCII 的坏字段名也要能被
# 提取出来报未知列；否则中文错误标识符对校验不可见（甚至把 SUM(中文) 误判成常量聚合）。
# 函数名/表前缀保持 ASCII：SQL 函数名与物理表名就是 ASCII 命名。
_IDENTIFIER_TOKEN_PATTERN = re.compile(r"[^\W\d][\w]*", re.UNICODE)
_TABLE_PREFIX_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.")
_SQL_KEYWORD_TOKENS: Set[str] = {
    "select", "from", "where", "group", "by", "having", "order", "case",
    "when", "then", "else", "end", "and", "or", "not", "in", "is", "null",
    "as", "between", "like", "distinct", "interval", "asc", "desc", "cast",
    "union", "all", "exists", "join", "on", "over", "partition", "div",
}


def _strip_expression_literals(expression: str) -> str:
    """剥离字符串字面量内容；反引号是标识符引用壳，剥壳保留标识符本身。"""
    without_single = re.sub(r"'(?:[^']|'')*'", " ", expression)
    without_double = re.sub(r'"(?:[^"]|"")*"', " ", without_single)
    return without_double.replace("`", "")


def _find_unknown_expression_columns(expression: str, table_columns: Set[str]) -> List[str]:
    """找出表达式中引用了不存在物理列的标识符。

    锚定校验（expression_uses_physical_column）只保证表达式至少命中一个物理列，
    多标识符表达式里写错的其余列无人检查。这里做全量校验：剥离字符串字面量、
    函数名与表前缀后，剩余标识符必须全部命中所属表列集合（忽略大小写，与锚定口径一致）。
    """
    cleaned = _strip_expression_literals(str(expression or ""))
    without_functions = _FUNCTION_CALL_NAME_PATTERN.sub(" ", cleaned)
    without_prefixes = _TABLE_PREFIX_PATTERN.sub(" ", without_functions)
    lowered_columns = {column.lower() for column in table_columns}
    unknown: List[str] = []
    for token in _IDENTIFIER_TOKEN_PATTERN.findall(without_prefixes):
        if token.lower() in _SQL_KEYWORD_TOKENS or token.lower() in lowered_columns:
            continue
        if token not in unknown:
            unknown.append(token)
    return unknown


def _is_constant_aggregate_expression(expression: str) -> bool:
    """COUNT(1)/COUNT(*) 等不引用任何标识符的常量聚合。

    这类表达式物理上必然合法（逐行常量聚合，锚定在所属表上），
    不可能引用错列，豁免物理字段校验；引用了标识符的一律走原校验。
    """
    cleaned = str(expression or "").replace("`", "")
    if not parse_aggregate_expression(cleaned):
        return False
    without_functions = _FUNCTION_CALL_NAME_PATTERN.sub(" ", cleaned)
    return not _IDENTIFIER_TOKEN_PATTERN.search(without_functions)


def _validate_metrics_against_tables(metrics: List[Dict], table_columns_map: Dict[str, Set[str]]) -> Optional[str]:
    for metric in metrics or []:
        if is_derived_metric(metric) or is_composite_metric(metric):
            continue  # 派生/复合指标不直接携带物理引用：派生复用基础指标，复合口径由成员表达式决定
        table_name = metric.get("table")
        expression = str(metric.get("expression") or "").strip()
        if not table_name or table_name not in table_columns_map:
            return f"指标 {metric.get('name', '')} 所属表不存在: {table_name}"
        if _is_constant_aggregate_expression(expression):
            continue
        if not expression_uses_physical_column(expression, table_columns_map[table_name]):
            return f"指标 {metric.get('name', '')} 表达式必须引用表 {table_name} 的物理字段"
        unknown_columns = _find_unknown_expression_columns(expression, table_columns_map[table_name])
        if unknown_columns:
            return f"指标 {metric.get('name', '')} 表达式引用了表 {table_name} 不存在的字段: {', '.join(unknown_columns)}"
    return None


def _validate_derived_metrics(metrics: List[Dict], dimensions: List[Dict]) -> Optional[str]:
    """派生指标结构校验：基础指标、统计周期与修饰词过滤的合法性。"""
    if not any(is_derived_metric(metric) for metric in metrics or []):
        return None
    metrics_map = {metric.get("name"): metric for metric in metrics or [] if metric.get("name")}
    dimensions_map = {dim.get("name"): dim for dim in dimensions or [] if dim.get("name")}
    for metric in metrics or []:
        if not is_derived_metric(metric):
            continue
        error = _validate_derived_metric_item(metric, metrics_map, dimensions_map)
        if error:
            return error
    return None


def _validate_derived_metric_item(
    metric: Dict[str, Any],
    metrics_map: Dict[str, Dict[str, Any]],
    dimensions_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    name = metric.get("name", "")
    if str(metric.get("expression") or "").strip():
        return f"派生指标 {name} 不能配置 expression，口径由基础指标决定"
    base_name = str(metric.get("base_metric") or "").strip()
    base_metric = metrics_map.get(base_name)
    if not base_name or not base_metric:
        return f"派生指标 {name} 的基础指标不存在: {base_name or '(空)'}"
    if is_derived_metric(base_metric):
        return f"派生指标 {name} 的基础指标不能是派生指标: {base_name}"
    if is_composite_metric(base_metric):
        return f"派生指标 {name} 的基础指标不能是复合指标: {base_name}"
    if not parse_aggregate_expression(str(base_metric.get("expression") or "")):
        return f"派生指标 {name} 的基础指标必须是聚合指标: {base_name}"
    period_error = _validate_derived_period(name, metric.get("period"), dimensions_map)
    if period_error:
        return period_error
    return _validate_derived_filters(name, metric.get("filters"), dimensions_map)


def _validate_derived_period(
    metric_name: str,
    period: Any,
    dimensions_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    label = f"派生指标 {metric_name} 的统计周期"
    if period in (None, {}):
        return None
    if not isinstance(period, dict):
        return f"{label}必须是对象"
    kind = str(period.get("kind") or "").strip().lower()
    if kind not in DERIVED_PERIOD_KINDS:
        return f"{label}类型不支持: {kind or '(空)'}"
    range_error = _validate_derived_period_range(label, kind, period)
    if range_error:
        return range_error
    return _validate_period_time_dimension(label, period, dimensions_map)


def _validate_derived_period_range(label: str, kind: str, period: Dict[str, Any]) -> Optional[str]:
    """周期区间参数校验：last_n_days 需要正整数 n，between 需要起止值。"""
    if kind == "last_n_days":
        n = period.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return f"{label}的 n 必须是正整数"
    if kind == "between" and (not period.get("start") or not period.get("end")):
        return f"{label}between 必须提供 start 和 end"
    return None


def _validate_period_time_dimension(
    label: str,
    period: Dict[str, Any],
    dimensions_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """周期的时间维度必须存在且为日期/时间型逻辑字段。"""
    time_dimension = str(period.get("time_dimension") or "").strip()
    dim_config = dimensions_map.get(time_dimension)
    if not dim_config:
        return f"{label}的时间维度不存在: {time_dimension or '(空)'}"
    if not is_time_logical_dimension(dim_config):
        return f"{label}的时间维度必须 logical_type 为 date/datetime: {time_dimension}"
    return None


def _validate_derived_filters(
    metric_name: str,
    filters: Any,
    dimensions_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    if filters in (None, []):
        return None
    if not isinstance(filters, list):
        return f"派生指标 {metric_name} 的修饰词过滤必须是数组"
    for index, item in enumerate(filters):
        error = _validate_derived_filter_item(metric_name, index, item, dimensions_map)
        if error:
            return error
    return None


def _validate_derived_filter_item(
    metric_name: str,
    index: int,
    item: Any,
    dimensions_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    label = f"派生指标 {metric_name} 的修饰词过滤[{index + 1}]"
    if not isinstance(item, dict):
        return f"{label}必须是对象"
    dimension_name = str(item.get("dimension") or "").strip()
    dim_config = dimensions_map.get(dimension_name)
    if not dim_config:
        return f"{label}引用的维度不存在: {dimension_name or '(空)'}"
    operator = str(item.get("operator") or "=").strip().upper()
    if operator not in FILTER_OPERATORS:
        return f"{label}操作符不支持: {operator}"
    return _validate_derived_filter_value(label, operator, item.get("value"))


def _is_blank_text(value: Any) -> bool:
    """值缺失或仅空白：BETWEEN/IN 的每个元素都按该口径判断。"""
    return not str(value or "").strip()


def _validate_derived_filter_value(label: str, operator: str, value: Any) -> Optional[str]:
    if operator in _NO_VALUE_FILTER_OPERATORS:
        return None
    if operator == "BETWEEN":
        return _validate_between_filter_value(label, value)
    if operator in {"IN", "NOT IN"}:
        return _validate_in_filter_value(label, value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return f"{label}缺少筛选值"
    return None


def _validate_between_filter_value(label: str, value: Any) -> Optional[str]:
    if not isinstance(value, list) or len(value) != 2 or any(_is_blank_text(part) for part in value):
        return f"{label}BETWEEN 需要完整的起止值"
    return None


def _validate_in_filter_value(label: str, value: Any) -> Optional[str]:
    if not isinstance(value, list) or not value or any(_is_blank_text(part) for part in value):
        return f"{label}IN 过滤需要至少一个有效值"
    return None


def _validate_composite_metrics(metrics: List[Dict]) -> Optional[str]:
    """复合指标结构校验：公式语法、成员存在性、同表聚合原子约束。"""
    if not any(is_composite_metric(metric) for metric in metrics or []):
        return None
    metrics_map = {metric.get("name"): metric for metric in metrics or [] if metric.get("name")}
    for metric in metrics or []:
        if not is_composite_metric(metric):
            continue
        error = _validate_composite_metric_item(metric, metrics_map)
        if error:
            return error
    return None


def _validate_composite_metric_item(
    metric: Dict[str, Any],
    metrics_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    name = metric.get("name", "")
    if str(metric.get("expression") or "").strip():
        return f"复合指标 {name} 不能配置 expression，口径由 formula 决定"
    formula = str(metric.get("formula") or "").strip()
    if not formula:
        return f"复合指标 {name} 缺少 formula 公式"
    refs = parse_formula_refs(formula)
    if not refs:
        return f"复合指标 {name} 的公式没有任何 {{指标}} 引用"
    operator_text = FORMULA_REF_PATTERN.sub("", formula)
    if not FORMULA_OPERATOR_PATTERN.match(operator_text):
        return f"复合指标 {name} 的公式包含不支持的字符，仅允许四则运算、括号和数字: {operator_text.strip()}"
    member_table = ""
    for ref in refs:
        error = _validate_composite_member(name, ref, metrics_map, member_table)
        if error:
            return error
        if not member_table:
            # 同表约束以首个成员的表名为基准
            member_table = _metric_table(metrics_map.get(ref))
    return None


def _metric_table(metric: Optional[Dict[str, Any]]) -> str:
    return str((metric or {}).get("table") or "").strip()


def _validate_composite_member(
    name: str,
    ref: str,
    metrics_map: Dict[str, Dict[str, Any]],
    member_table: str,
) -> Optional[str]:
    """复合指标单个公式成员校验：存在性、类型、聚合性与同表约束。"""
    member_metric = metrics_map.get(ref)
    if not member_metric:
        return f"复合指标 {name} 引用的指标不存在: {ref}"
    if is_composite_metric(member_metric):
        return f"复合指标 {name} 不能引用其他复合指标: {ref}"
    if is_derived_metric(member_metric):
        return f"复合指标 {name} 的成员暂不支持派生指标: {ref}"
    if not parse_aggregate_expression(str(member_metric.get("expression") or "")):
        return f"复合指标 {name} 引用的指标不是聚合指标: {ref}"
    ref_table = _metric_table(member_metric)
    if not ref_table:
        return f"复合指标 {name} 引用的指标缺少物理表: {ref}"
    if member_table and ref_table != member_table:
        return f"复合指标 {name} 的成员指标跨表（{member_table} / {ref_table}），暂不支持"
    return None


def _validate_allowed_operators(dimension: Dict[str, Any]) -> Optional[str]:
    configured = dimension.get("allowed_operators")
    if configured is None:
        return None
    if not isinstance(configured, list):
        return f"维度 {dimension.get('name', '')} 的 allowed_operators 必须是数组"
    for operator in configured:
        normalized = str(operator or "").strip().upper()
        if normalized not in FILTER_OPERATORS:
            return f"维度 {dimension.get('name', '')} 配置了不支持的过滤操作符: {operator}"
    return None


def _semantic_alias(item: Dict[str, Any]) -> str:
    return str(item.get("table_alias") or item.get("table") or "").strip()


def _relationship_alias(relationship: Dict[str, Any], side: str) -> str:
    return str(
        relationship.get(f"{side}_alias")
        or relationship.get(f"{side}_table")
        or ""
    ).strip()


def _relationship_conditions(relationship: Dict[str, Any]) -> List[Dict[str, str]]:
    conditions = relationship.get("join_conditions") or []
    if conditions:
        return conditions
    if relationship.get("left_column") or relationship.get("right_column"):
        return [{
            "left_column": relationship.get("left_column"),
            "right_column": relationship.get("right_column"),
        }]
    return []


def _register_alias(alias_tables: Dict[str, str], alias: str, table: str) -> Optional[str]:
    existing = alias_tables.get(alias)
    if existing and existing != table:
        return f"语义表别名 {alias} 同时指向了 {existing} 和 {table}"
    alias_tables[alias] = table
    return None


def _register_field_aliases(
    alias_tables: Dict[str, str],
    field_aliases: Set[str],
    dimensions: List[Dict],
    metrics: List[Dict],
) -> Optional[str]:
    for item in [*dimensions, *metrics]:
        table = str((item or {}).get("table") or "").strip()
        alias = _semantic_alias(item or {})
        if table and alias:
            error = _register_alias(alias_tables, alias, table)
            if error:
                return error
            field_aliases.add(alias)
    return None


def _validate_relationship_conditions(
    table_columns_map: Dict[str, Set[str]],
    label: str,
    left_table: str,
    right_table: str,
    conditions: List[Dict],
) -> Optional[str]:
    for condition_index, condition in enumerate(conditions):
        left_column = str((condition or {}).get("left_column") or "").strip()
        right_column = str((condition or {}).get("right_column") or "").strip()
        condition_label = f"{label}关联条件[{condition_index + 1}]"
        if not left_column or not right_column:
            return f"{condition_label}左右字段不能为空"
        if left_column not in table_columns_map[left_table]:
            return f"{condition_label}左字段不存在: {left_table}.{left_column}"
        if right_column not in table_columns_map[right_table]:
            return f"{condition_label}右字段不存在: {right_table}.{right_column}"
    return None


def _validate_relationship_sides(
    table_columns_map: Dict[str, Set[str]],
    alias_tables: Dict[str, str],
    label: str,
    sides: tuple[tuple[str, str, str], ...],
) -> Optional[str]:
    for side_name, table, alias in sides:
        if not table or table not in table_columns_map:
            return f"{label}{side_name}不存在: {table}"
        if not alias:
            return f"{label}{side_name}别名不能为空"
        error = _register_alias(alias_tables, alias, table)
        if error:
            return error
    return None


def _validate_single_relationship(
    table_columns_map: Dict[str, Set[str]],
    alias_tables: Dict[str, str],
    adjacency: Dict[str, Set[str]],
    index: int,
    relationship: Dict,
) -> Optional[str]:
    left_table = str((relationship or {}).get("left_table") or "").strip()
    right_table = str((relationship or {}).get("right_table") or "").strip()
    left_alias = _relationship_alias(relationship or {}, "left")
    right_alias = _relationship_alias(relationship or {}, "right")
    label = f"关系[{index + 1}]"
    error = _validate_relationship_sides(
        table_columns_map,
        alias_tables,
        label,
        (
            ("左表", left_table, left_alias),
            ("右表", right_table, right_alias),
        ),
    )
    if error:
        return error
    if left_alias == right_alias:
        return f"{label}左右表别名不能相同: {left_alias}"

    conditions = _relationship_conditions(relationship or {})
    if not conditions:
        return f"{label}缺少关联条件"
    error = _validate_relationship_conditions(
        table_columns_map, label, left_table, right_table, conditions,
    )
    if error:
        return error
    adjacency.setdefault(left_alias, set()).add(right_alias)
    adjacency.setdefault(right_alias, set()).add(left_alias)
    return None


def _find_disconnected_aliases(field_aliases: Set[str], adjacency: Dict[str, Set[str]]) -> List[str]:
    if len(field_aliases) <= 1:
        return []
    start = next(iter(field_aliases))
    reachable = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for neighbor in adjacency.get(current, set()):
            if neighbor not in reachable:
                reachable.add(neighbor)
                pending.append(neighbor)
    return sorted(field_aliases - reachable)


def validate_semantic_relationships(
    table_columns_map: Dict[str, Set[str]],
    metrics: List[Dict],
    dimensions: List[Dict],
    relationships: List[Dict],
) -> Optional[str]:
    """Validate relationship endpoints, aliases, columns and field connectivity."""
    alias_tables: Dict[str, str] = {}
    field_aliases: Set[str] = set()
    error = _register_field_aliases(alias_tables, field_aliases, dimensions, metrics)
    if error:
        return error

    adjacency: Dict[str, Set[str]] = {}
    for index, relationship in enumerate(relationships):
        error = _validate_single_relationship(
            table_columns_map, alias_tables, adjacency, index, relationship,
        )
        if error:
            return error

    disconnected = _find_disconnected_aliases(field_aliases, adjacency)
    if disconnected:
        return f"语义字段所在表缺少关联路径: {'、'.join(disconnected)}"
    return None
