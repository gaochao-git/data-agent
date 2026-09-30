"""BI 可视化语义 DSL 输入适配器（可视化查询链路）。

输入是前端查询构建器产出的 builder 风格 DSL，字段可能以语义名称或物理
table/column/expression 两种方式引用语义字段，且直面未校验的原始输入，
因此本适配器采取严格校验策略，报错文案被测试断言、变更需同步测试。
AI 侧入口见 semantic_ai_query_adapter.SemanticAIQueryAdapter，两者共享的
查询计划组装原语在 semantic_query_plan_builder。
"""

from typing import Any

from utils.logger import get_logger

from ...runtime_config import DEFAULT_ROW_LIMIT
from ..core.filter_normalization import normalize_time_filter_leaf
from ..core.input_validator import InputValidator
from ..core.query_plan import (
    SemanticQueryPlanContext,
    is_composite_metric,
    semantic_table_alias,
)
from ..core.semantic_sql.compiler import SemanticSQLCompiler
from ..core.semantic_sql.filter_compiler import ensure_safe_sql_expression
from .semantic_query_plan_builder import (
    assemble_dimension_filter_node,
    build_metric_base_configs,
    builder_node_alias,
    check_mixed_derived_periods,
    collect_filter_node_tables,
    metric_func_column,
    normalize_text,
)

logger = get_logger(__name__)

FORBIDDEN_SEMANTIC_BUILDER_KEYS = {"mainTable", "mainAlias", "joins"}

# 历史兼容别名：实现已收敛到共享组装原语，名称保留给存量调用与测试。
_normalize_alias = builder_node_alias
_normalize_text = normalize_text
_parse_aggregate_expression = metric_func_column
_semantic_alias = semantic_table_alias


def _as_list(value: Any, field_name: str = "字段") -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("语义可视化 DSL 字段必须是数组")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"语义可视化 DSL {field_name}[{index}] 必须是对象")
    return value


def _field_keys(table: str, table_alias: str, column: str) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    normalized_table = normalize_text(table)
    normalized_alias = normalize_text(table_alias) or normalized_table
    normalized_column = normalize_text(column)
    if normalized_table and normalized_column:
        keys.add((normalized_table, normalized_column))
    if normalized_alias and normalized_column:
        keys.add((normalized_alias, normalized_column))
    return keys


class SemanticBIQueryAdapter:
    """可视化查询输入适配器。"""

    def __init__(self, semantic_config: dict[str, Any], max_rows: int | None = None) -> None:
        self.query_plan_context = SemanticQueryPlanContext(semantic_config)
        self.semantic_config = self.query_plan_context.semantic_config
        self.metrics_map = self.query_plan_context.metrics_map
        self.dimensions_map = self.query_plan_context.dimensions_map
        self.max_rows = DEFAULT_ROW_LIMIT if max_rows is None else max_rows
        self.validator = InputValidator(
            self.metrics_map,
            self.dimensions_map,
            max_limit=self.max_rows,
        )
        # 派生/复合指标统一预解析（共享入口）：编译配置 + 派生固定过滤叶子
        self._metric_base_cfgs, self._derived_fixed_leaves = build_metric_base_configs(
            self.metrics_map,
            self.dimensions_map,
        )

    def adapt(self, semantic_builder_config: dict[str, Any]) -> dict[str, Any]:
        """校验并规范化 Builder 输入，不在此阶段生成 SQL。"""
        if not isinstance(semantic_builder_config, dict):
            raise ValueError("语义可视化 DSL 格式错误")
        forbidden = FORBIDDEN_SEMANTIC_BUILDER_KEYS.intersection(semantic_builder_config.keys())
        if forbidden:
            raise ValueError(f"语义可视化 DSL 不允许包含前端表关系字段: {', '.join(sorted(forbidden))}")

        self._used_derived_names: list[str] = []
        dimensions = [
            self._map_dimension(item) for item in _as_list(semantic_builder_config.get("dimensions"), "dimensions")
        ]
        aggregations = [
            self._map_aggregation(item)
            for item in _as_list(semantic_builder_config.get("aggregations"), "aggregations")
        ]
        filters = self._map_filter_nodes(semantic_builder_config.get("filters") or [])
        order_by = [
            self._map_order_by(item, aggregations, dimensions)
            for item in _as_list(semantic_builder_config.get("orderBy"), "orderBy")
        ]
        # 不同周期的派生指标混用会因固定过滤交集而算错，提前拦截
        mixed_err = check_mixed_derived_periods(self._used_derived_names, self.metrics_map)
        if mixed_err:
            raise ValueError(mixed_err)
        # 派生指标固定过滤在全部指标解析完成后注入，确保 order_by/HAVING 引用的派生指标也被覆盖
        filters.extend(self._derived_fixed_filter_nodes())
        limit = self._validate_limit(semantic_builder_config.get("limit"))
        offset = self._validate_offset(semantic_builder_config.get("offset"))

        if not dimensions and not aggregations:
            raise ValueError("语义可视化查询至少需要一个维度或指标")

        main_table, main_alias = self._resolve_main_table(dimensions, aggregations, filters, order_by)
        tables_needed = self._collect_tables_needed(main_alias, dimensions, aggregations, filters, order_by)
        query_plan = {
            "mainTable": main_table,
            "mainAlias": main_alias,
            "joins": self.query_plan_context.build_joins(main_alias, tables_needed),
            "dimensions": dimensions,
            "aggregations": aggregations,
            "filters": filters,
            "orderBy": order_by,
            "limit": limit,
            "offset": offset,
        }
        return query_plan

    def compile(self, semantic_builder_config: dict[str, Any]) -> dict[str, Any]:
        """适配 Builder 输入后，通过统一语义 SQL 编译入口生成 SQL。"""
        query_plan = self.adapt(semantic_builder_config)
        sql = SemanticSQLCompiler(
            dialect=self.query_plan_context.dialect_name,
            max_rows=self.max_rows,
        ).compile(query_plan)
        # builder_config 是既有前端响应字段，内部统一使用 query_plan。
        return {"sql": sql, "builder_config": query_plan}

    def _validate_limit(self, value: Any) -> int | None:
        limit, err = self.validator.validate_limit(value)
        if err:
            raise ValueError(err)
        return limit

    def _validate_offset(self, value: Any) -> int | None:
        offset, err = self.validator.validate_offset(value)
        if err:
            raise ValueError(err)
        return offset

    def _resolve_dimension_config(self, item: dict[str, Any]) -> dict[str, Any]:
        name_candidates = [
            item.get("semanticName"),
            item.get("name"),
            item.get("alias"),
            item.get("displayName"),
        ]
        for name in name_candidates:
            normalized = normalize_text(name)
            if normalized and normalized in self.dimensions_map:
                return self.dimensions_map[normalized]

        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        item_expression = normalize_text(item.get("expression"))
        for dim in self.semantic_config.get("dimensions") or []:
            dim_keys = _field_keys(dim.get("table"), _semantic_alias(dim), dim.get("column"))
            if item_keys and item_keys.intersection(dim_keys):
                dim_expression = normalize_text(dim.get("expression"))
                if not item_expression or not dim_expression or item_expression == dim_expression:
                    return dim
        raise ValueError(
            f"语义维度不存在或不在当前指标集内: {item.get('alias') or item.get('displayName') or item.get('column')}"
        )

    @staticmethod
    def _metric_field_signature(metric: dict[str, Any]) -> tuple[str, str, set]:
        """解析语义指标的 (expression, func, field_keys) 匹配签名。"""
        metric_expression = normalize_text(metric.get("expression"))
        parsed = metric_func_column(metric_expression)
        metric_func = parsed[0] if parsed else "RAW"
        metric_column = parsed[1] if parsed else (metric.get("column") or metric.get("field_name") or "")
        metric_keys = _field_keys(metric.get("table"), _semantic_alias(metric), metric_column)
        return metric_expression, metric_func, metric_keys

    def _match_metric_by_field(
        self,
        item: dict[str, Any],
        item_expression: str,
        item_func: str,
        item_keys: set,
    ) -> dict[str, Any] | None:
        """按表达式/字段键扫描语义指标；命中多个时要求显式指定。"""
        matched_by_field: list[dict[str, Any]] = []
        for metric in self.semantic_config.get("metrics") or []:
            metric_expression, metric_func, metric_keys = self._metric_field_signature(metric)
            if item_expression and metric_expression and item_expression == metric_expression:
                return metric
            if item_func == metric_func and item_keys and item_keys.intersection(metric_keys):
                matched_by_field.append(metric)
        if len(matched_by_field) == 1:
            return matched_by_field[0]
        if len(matched_by_field) > 1:
            metric_names = "、".join(
                normalize_text(metric.get("name")) for metric in matched_by_field if normalize_text(metric.get("name"))
            )
            raise ValueError(f"指标字段匹配到多个语义指标，请传 semanticName 或 expression: {metric_names}")
        return None

    def _resolve_metric_config(self, item: dict[str, Any]) -> dict[str, Any] | None:
        name_candidates = [
            item.get("semanticName"),
            item.get("name"),
            item.get("alias"),
            item.get("displayName"),
        ]
        for name in name_candidates:
            normalized = normalize_text(name)
            if normalized and normalized in self.metrics_map:
                # 派生指标按预解析的基础原子指标参与编译，别名保留派生指标名
                self._track_used_derived_metric(normalized)
                return {**self._metric_base_cfgs[normalized], "name": normalized}

        item_func = normalize_text(item.get("func") or "RAW").upper()
        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        item_expression = normalize_text(item.get("expression"))
        return self._match_metric_by_field(item, item_expression, item_func, item_keys)

    def _track_used_derived_metric(self, metric_name: str) -> None:
        """记录本次查询用到的派生指标名（保持首次出现顺序，供固定过滤注入）。"""
        if self._derived_fixed_leaves.get(metric_name) and metric_name not in self._used_derived_names:
            self._used_derived_names.append(metric_name)

    def _derived_fixed_filter_nodes(self) -> list[dict[str, Any]]:
        """把本次查询涉及的派生指标的周期/修饰词固定过滤映射为 BI 过滤节点。

        v1 口径：固定过滤作用于整条查询；同一查询混用不同周期的派生指标时
        取过滤的交集，由派生指标 verbalization 向模型说明口径。
        """
        nodes: list[dict[str, Any]] = []
        for metric_name in self._used_derived_names:
            for leaf in self._derived_fixed_leaves.get(metric_name) or []:
                nodes.extend(
                    self._map_dimension_filter_leaves(
                        {
                            "semanticName": leaf.get("dimension"),
                            "operator": leaf.get("operator"),
                            "value": leaf.get("value"),
                        }
                    )
                )
        return nodes

    def _map_dimension(self, item: dict[str, Any]) -> dict[str, Any]:
        dim = self._resolve_dimension_config(item)
        alias = _semantic_alias(dim) or dim.get("table")
        transform = item.get("transform") or item.get("time_grain") or "raw"
        return {
            "id": item.get("id") or f"dim-{dim.get('name') or dim.get('column')}",
            "table": dim.get("table"),
            "tableAlias": alias,
            "column": dim.get("column"),
            "alias": item.get("alias") or item.get("displayName") or dim.get("name") or dim.get("column"),
            "expression": dim.get("expression") or item.get("expression"),
            "transform": transform,
        }

    @staticmethod
    def _aggregation_field_name(item: dict[str, Any], metric: dict[str, Any], fallback: Any) -> Any:
        """聚合字段展示名：指标名 > 查询项别名/显示名 > 兜底（列名或物理表名）。"""
        return metric.get("name") or item.get("alias") or item.get("displayName") or fallback

    @staticmethod
    def _aggregation_fields_from_metric(item: dict[str, Any], metric: dict[str, Any]) -> tuple:
        """命中语义指标时，聚合字段优先取指标配置。"""
        table = metric.get("table") or item.get("table")
        alias = _semantic_alias(metric) or table
        if is_composite_metric(metric):
            # 复合指标：formula 已代入为完整聚合表达式，按 RAW 原样直出并绑定成员物理表
            return (
                "RAW",
                table,
                alias,
                "*",
                metric.get("expression"),
                SemanticBIQueryAdapter._aggregation_field_name(item, metric, table),
            )
        parsed = metric_func_column(normalize_text(metric.get("expression")))
        func = parsed[0] if parsed else normalize_text(item.get("func") or "RAW").upper()
        column = parsed[1] if parsed else (metric.get("column") or item.get("column") or "*")
        return (
            func,
            table,
            alias,
            column,
            metric.get("expression") or item.get("expression"),
            SemanticBIQueryAdapter._aggregation_field_name(item, metric, column),
        )

    def _aggregation_fields_from_dimension(self, item: dict[str, Any]) -> tuple:
        """未命中指标时回退维度聚合，仅允许基础聚合函数。"""
        dim = self._resolve_dimension_config(item)
        func = normalize_text(item.get("func") or "RAW").upper()
        if func not in {"RAW", "COUNT", "SUM", "AVG", "MAX", "MIN", "COUNT_DISTINCT"}:
            raise ValueError(
                f"字段 '{item.get('alias') or item.get('displayName') or item.get('column')}' 未配置为指标，不能作为聚合指标使用"
            )
        table = dim.get("table")
        column = dim.get("column")
        return (
            func,
            table,
            _semantic_alias(dim) or table,
            column,
            dim.get("expression") or item.get("expression"),
            item.get("alias") or item.get("displayName") or dim.get("name") or column,
        )

    def _map_aggregation(self, item: dict[str, Any]) -> dict[str, Any]:
        metric = self._resolve_metric_config(item)
        if metric:
            func, table, table_alias, column, expression, display_name = self._aggregation_fields_from_metric(
                item, metric
            )
        else:
            func, table, table_alias, column, expression, display_name = self._aggregation_fields_from_dimension(item)

        return {
            "id": item.get("id") or f"agg-{display_name}",
            "func": func,
            "table": table,
            "tableAlias": table_alias,
            "column": column,
            "alias": display_name,
            "expression": expression,
        }

    def _map_filter_nodes(self, value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        if not isinstance(value, list):
            raise ValueError("语义可视化 filters 必须是数组")
        mapped: list[dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"语义可视化 filters[{index}] 必须是对象")
            node = self._map_filter_node(item)
            if isinstance(node, list):
                mapped.extend(node)
            else:
                mapped.append(node)
        return mapped

    def _map_filter_node(self, node: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        if "and" in node:
            return {"and": self._map_boolean_children(node, "and", extend=True)}
        if "or" in node:
            return {"or": self._map_boolean_children(node, "or", extend=False)}
        if isinstance(node.get("left"), dict) and "right" in node:
            return self._map_having_filter(node)
        self._validate_filter_value(node)
        if node.get("aggFunc"):
            return self._map_having_filter(node)
        mapped_filters = self._map_dimension_filter_leaves(node)
        return mapped_filters[0] if len(mapped_filters) == 1 else mapped_filters

    def _map_boolean_children(self, node: dict[str, Any], key: str, *, extend: bool) -> list[dict[str, Any]]:
        """映射 and/or 节点的子节点列表；and 平铺子节点，or 把列表子节点包成 and。"""
        children = node.get(key) or []
        if not isinstance(children, list):
            raise ValueError(f"语义可视化 {key} 过滤节点必须是数组")
        if not children:
            raise ValueError(f"语义可视化 {key} 过滤节点不能为空")
        mapped_children: list[dict[str, Any]] = []
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                raise ValueError(f"语义可视化 {key}[{index}] 必须是对象")
            mapped_child = self._map_filter_node(child)
            if isinstance(mapped_child, list):
                if extend:
                    mapped_children.extend(mapped_child)
                else:
                    mapped_children.append({"and": mapped_child})
            else:
                mapped_children.append(mapped_child)
        return mapped_children

    @staticmethod
    def _has_filter_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True

    @classmethod
    def _filter_label(cls, node: dict[str, Any]) -> str:
        return normalize_text(
            node.get("displayName") or node.get("semanticName") or node.get("name") or node.get("column") or "筛选条件"
        )

    @classmethod
    def _validate_typed_filter_value(cls, node: dict[str, Any], value_type: str, label: str) -> bool:
        """field_ref/expression 类型校验；返回 True 表示已按类型完成校验。"""
        if value_type == "field_ref":
            if not normalize_text(node.get("rightTable")) or not normalize_text(node.get("rightColumn")):
                raise ValueError(f"筛选条件“{label}”请选择右侧字段")
            return True
        if value_type == "expression":
            if not normalize_text(node.get("rightExpression")):
                raise ValueError(f"筛选条件“{label}”请输入表达式")
            # 与 AI 链路同口径：自由表达式禁止注释、分号与写操作关键字
            ensure_safe_sql_expression(node.get("rightExpression"), label=f"筛选条件“{label}”的表达式")
            return True
        return False

    @classmethod
    def _validate_operator_filter_value(cls, operator: str, value: Any, label: str) -> None:
        if operator == "BETWEEN":
            if not isinstance(value, list) or len(value) != 2 or any(not cls._has_filter_value(item) for item in value):
                raise ValueError(f"筛选条件“{label}”请输入完整的起止值")
            return
        if operator in {"IN", "NOT IN"}:
            if not isinstance(value, list) or not value or any(not cls._has_filter_value(item) for item in value):
                raise ValueError(f"筛选条件“{label}”请至少输入一个有效值")
            return
        if not cls._has_filter_value(value):
            raise ValueError(f"筛选条件“{label}”请输入筛选值")

    @classmethod
    def _validate_filter_value(cls, node: dict[str, Any]) -> None:
        operator = normalize_text(node.get("operator") or "=").upper()
        if operator in {"IS NULL", "IS NOT NULL", "EMPTY STRING"}:
            return

        label = cls._filter_label(node)
        value_type = normalize_text(node.get("valueType") or node.get("value_type") or "literal").lower()
        if cls._validate_typed_filter_value(node, value_type, label):
            return
        cls._validate_operator_filter_value(operator, node.get("value"), label)

    def _copy_filter_value_fields(self, source: dict[str, Any], target: dict[str, Any]) -> None:
        for key in ("value", "valueType", "value_type", "rightExpression", "logic"):
            if key in source:
                target[key] = source.get(key)
        if source.get("valueType") == "field_ref":
            right = self._resolve_dimension_config(
                {
                    "table": source.get("rightTable"),
                    "tableAlias": source.get("rightTableAlias"),
                    "column": source.get("rightColumn"),
                }
            )
            target["rightTable"] = right.get("table")
            target["rightTableAlias"] = _semantic_alias(right) or right.get("table")
            target["rightColumn"] = right.get("column")

    def _map_having_operand(self, operand: Any) -> dict[str, Any]:
        if not isinstance(operand, dict):
            raise ValueError("HAVING 表达式操作数必须是对象")
        operand_type = normalize_text(operand.get("type")).lower()
        if operand_type == "metric":
            return self._map_having_metric_operand(operand)
        if operand_type == "literal":
            return self._map_having_literal_operand(operand)
        if operand_type == "binary":
            return self._map_having_binary_operand(operand)
        raise ValueError("HAVING 表达式操作数只支持 metric、literal、binary")

    def _map_having_metric_operand(self, operand: dict[str, Any]) -> dict[str, Any]:
        metric = self._resolve_metric_config(
            {
                "semanticName": operand.get("name") or operand.get("semanticName"),
                "name": operand.get("name"),
                "alias": operand.get("alias"),
                "displayName": operand.get("displayName"),
                "func": operand.get("aggFunc") or operand.get("func"),
                "table": operand.get("table"),
                "tableAlias": _normalize_alias(operand),
                "column": operand.get("column"),
                "expression": operand.get("expression"),
            }
        )
        if not metric:
            raise ValueError(
                f"HAVING 表达式引用的指标不存在: {operand.get('name') or operand.get('semanticName') or operand.get('column')}"
            )
        agg = self._map_aggregation(
            {
                "semanticName": metric.get("name"),
                "func": operand.get("aggFunc") or operand.get("func"),
                "table": metric.get("table"),
                "tableAlias": _semantic_alias(metric),
                "column": operand.get("column"),
                "expression": operand.get("expression"),
            }
        )
        return {
            "type": "metric",
            "name": metric.get("name"),
            "table": agg.get("table"),
            "tableAlias": agg.get("tableAlias"),
            "column": agg.get("column"),
            "aggFunc": agg.get("func"),
            "aggId": agg.get("id"),
            "expression": agg.get("expression"),
        }

    @staticmethod
    def _map_having_literal_operand(operand: dict[str, Any]) -> dict[str, Any]:
        value = operand.get("value")
        if isinstance(value, (dict, list)):
            raise ValueError("HAVING 表达式 literal.value 只支持字符串、数字、布尔值或空值")
        return {"type": "literal", "value": value}

    def _map_having_binary_operand(self, operand: dict[str, Any]) -> dict[str, Any]:
        operator = normalize_text(operand.get("operator"))
        if operator not in {"+", "-", "*", "/"}:
            raise ValueError("HAVING 表达式二元运算符只支持 +、-、*、/")
        right = operand.get("right")
        if operator == "/" and isinstance(right, dict) and right.get("type") == "literal":
            try:
                if float(right.get("value")) == 0:
                    raise ValueError("HAVING 表达式不允许除以 0")
            except ValueError:
                raise
            except Exception as exc:
                # 除零校验是防御性前置检查:读取 literal.value 的意外异常不在此处拦截,
                # 交由后续表达式编译与参数校验兜底,这里仅留痕避免静默。
                logger.warning(
                    "HAVING 除零校验读取 literal.value 异常,跳过前置校验: value=%r error=%s", right.get("value"), exc
                )
        return {
            "type": "binary",
            "operator": operator,
            "left": self._map_having_operand(operand.get("left")),
            "right": self._map_having_operand(right),
        }

    def _map_dimension_filter(self, node: dict[str, Any]) -> dict[str, Any]:
        dim = self._resolve_dimension_config(node)
        return self._map_dimension_filter_with_config(node, dim)

    def _map_dimension_filter_leaves(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        dim = self._resolve_dimension_config(node)
        self._validate_dimension_filter_operator(node, dim)
        return [
            self._map_dimension_filter_with_config(normalized_node, dim, validate_operator=False)
            for normalized_node in normalize_time_filter_leaf(node, dim)
        ]

    def _validate_dimension_filter_operator(self, node: dict[str, Any], dim: dict[str, Any]) -> str:
        operator = str(node.get("operator") or "=").strip().upper()
        operator_error = self.validator.validate_dimension_operator(
            str(dim.get("name") or "").strip(),
            operator,
        )
        if operator_error:
            raise ValueError(operator_error)
        return operator

    def _map_dimension_filter_with_config(
        self,
        node: dict[str, Any],
        dim: dict[str, Any],
        *,
        validate_operator: bool = True,
    ) -> dict[str, Any]:
        operator = (
            self._validate_dimension_filter_operator(node, dim)
            if validate_operator
            else str(node.get("operator") or "=").strip().upper()
        )
        mapped = assemble_dimension_filter_node(
            dim,
            node_id=node.get("id") or f"filter-{dim.get('name') or dim.get('column')}",
            operator=operator,
            logic=node.get("logic", "AND"),
            expression=dim.get("expression") or node.get("expression"),
        )
        self._copy_filter_value_fields(node, mapped)
        return mapped

    def _map_having_filter(self, node: dict[str, Any]) -> dict[str, Any]:
        if isinstance(node.get("left"), dict) and "right" in node:
            left = self._map_having_operand(node.get("left"))
            if left.get("type") != "metric":
                raise ValueError("HAVING 表达式 left 只支持 metric")
            agg = left
        else:
            agg = self._map_aggregation(
                {
                    "id": node.get("aggId"),
                    "func": node.get("aggFunc"),
                    "table": node.get("table"),
                    "tableAlias": _normalize_alias(node),
                    "column": node.get("column"),
                    "alias": node.get("displayName"),
                    "semanticName": node.get("semanticName"),
                    "displayName": node.get("displayName"),
                    "expression": node.get("expression"),
                }
            )
        mapped = {
            "id": node.get("id") or f"having-{agg.get('name') or agg.get('alias')}",
            "table": agg.get("table"),
            "tableAlias": agg.get("tableAlias"),
            "column": agg.get("column"),
            "operator": node.get("operator", "="),
            "logic": node.get("logic", "AND"),
            "aggFunc": agg.get("aggFunc") or agg.get("func"),
            "aggId": agg.get("aggId") or agg.get("id"),
            "expression": agg.get("expression"),
        }
        if isinstance(node.get("left"), dict) and "right" in node:
            mapped["rightOperand"] = self._map_having_operand(node.get("right"))
            if "logic" in node:
                mapped["logic"] = node.get("logic")
        else:
            self._copy_filter_value_fields(node, mapped)
        return mapped

    def _map_order_by(
        self, item: dict[str, Any], aggregations: list[dict[str, Any]], dimensions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        direction = normalize_text(item.get("direction") or "ASC").upper()
        if direction not in {"ASC", "DESC"}:
            raise ValueError("排序方向只能是 ASC 或 DESC")
        if item.get("aggFunc"):
            agg = self._find_selected_aggregation(item, aggregations) or self._map_aggregation(
                {
                    "id": item.get("aggId"),
                    "func": item.get("aggFunc"),
                    "table": item.get("table"),
                    "tableAlias": _normalize_alias(item),
                    "column": item.get("column"),
                    "alias": item.get("displayName") or item.get("alias"),
                    "semanticName": item.get("semanticName"),
                    "displayName": item.get("displayName"),
                    "expression": item.get("expression"),
                }
            )
            return {
                "id": item.get("id") or f"order-{agg.get('alias')}",
                "table": agg.get("table"),
                "tableAlias": agg.get("tableAlias"),
                "column": agg.get("column"),
                "direction": direction,
                "aggFunc": agg.get("func"),
                "aggId": agg.get("id"),
                "expression": agg.get("expression"),
            }
        dim = self._find_selected_dimension(item, dimensions) or self._map_dimension(item)
        return {
            "id": item.get("id") or f"order-{dim.get('alias')}",
            "table": dim.get("table"),
            "tableAlias": dim.get("tableAlias"),
            "column": dim.get("column"),
            "direction": direction,
        }

    @staticmethod
    def _find_selected_dimension(item: dict[str, Any], dimensions: list[dict[str, Any]]) -> dict[str, Any] | None:
        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        for dim in dimensions:
            if item_keys.intersection(_field_keys(dim.get("table"), dim.get("tableAlias"), dim.get("column"))):
                return dim
        return None

    @staticmethod
    def _find_selected_aggregation(item: dict[str, Any], aggregations: list[dict[str, Any]]) -> dict[str, Any] | None:
        agg_id = normalize_text(item.get("aggId"))
        if agg_id:
            for agg in aggregations:
                if normalize_text(agg.get("id")) == agg_id:
                    return agg
        item_expression = normalize_text(item.get("expression"))
        if item_expression:
            for agg in aggregations:
                if normalize_text(agg.get("expression")) == item_expression:
                    return agg
            return None
        return SemanticBIQueryAdapter._find_aggregation_by_name_or_func(item, aggregations)

    @staticmethod
    def _find_aggregation_by_name_or_func(
        item: dict[str, Any], aggregations: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """先按显式名称（semanticName/name/alias/displayName）匹配别名；全部缺失时回退聚合函数+字段键匹配。"""
        name_candidates = [
            normalize_text(item.get("semanticName")),
            normalize_text(item.get("name")),
            normalize_text(item.get("alias")),
            normalize_text(item.get("displayName")),
        ]
        explicit_names = {name for name in name_candidates if name}
        if explicit_names:
            for agg in aggregations:
                if normalize_text(agg.get("alias")) in explicit_names:
                    return agg
            return None
        item_func = normalize_text(item.get("aggFunc") or item.get("func")).upper()
        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        for agg in aggregations:
            if item_func and normalize_text(agg.get("func")).upper() != item_func:
                continue
            if item_keys.intersection(_field_keys(agg.get("table"), agg.get("tableAlias"), agg.get("column"))):
                return agg
        return None

    @staticmethod
    def _extract_filter_leaf_tables(node: dict[str, Any], tables_needed: set[str]) -> None:
        table_alias = builder_node_alias(node)
        if table_alias:
            tables_needed.add(table_alias)
        right_alias = normalize_text(node.get("rightTableAlias") or node.get("rightTable"))
        if node.get("valueType") == "field_ref" and right_alias:
            tables_needed.add(right_alias)

    @classmethod
    def _collect_filter_tables(cls, node: dict[str, Any], tables_needed: set[str]) -> None:
        collect_filter_node_tables(node, tables_needed, cls._extract_filter_leaf_tables)

    def _collect_tables_needed(
        self,
        main_alias: str,
        dimensions: list[dict[str, Any]],
        aggregations: list[dict[str, Any]],
        filters: list[dict[str, Any]],
        order_by: list[dict[str, Any]],
    ) -> set[str]:
        tables_needed = {main_alias}
        for item in [*dimensions, *aggregations, *order_by]:
            alias = _normalize_alias(item)
            if alias:
                tables_needed.add(alias)
        for node in filters:
            self._collect_filter_tables(node, tables_needed)
        return tables_needed

    def _resolve_main_table(
        self,
        dimensions: list[dict[str, Any]],
        aggregations: list[dict[str, Any]],
        filters: list[dict[str, Any]],
        order_by: list[dict[str, Any]],
    ) -> tuple[str, str]:
        for collection in (dimensions, aggregations, order_by):
            for item in collection:
                table = normalize_text(item.get("table"))
                alias = _normalize_alias(item) or table
                if table:
                    return table, alias
        for node in filters:
            table, alias = self._first_filter_table(node)
            if table:
                return table, alias or table
        raise ValueError("语义可视化查询无法确定主表")

    def _first_filter_table(self, node: dict[str, Any]) -> tuple[str, str]:
        if "and" in node or "or" in node:
            children = node.get("and") if "and" in node else node.get("or")
            for child in children or []:
                if isinstance(child, dict):
                    table, alias = self._first_filter_table(child)
                    if table:
                        return table, alias
            return "", ""
        return normalize_text(node.get("table")), _normalize_alias(node)
