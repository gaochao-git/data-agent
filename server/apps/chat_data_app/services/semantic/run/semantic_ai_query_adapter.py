"""AI 语义 DSL 输入适配器（智能问数 agent 链路）。

输入是模型产出的紧凑 AI DSL（语义名称 + 结构化过滤），上游已过 InputValidator
校验，因此本适配器对畸形节点采取宽松策略；校验与报错契约见 InputValidator。
BI 可视化入口见 semantic_bi_query_adapter.SemanticBIQueryAdapter，两者共享的
查询计划组装原语在 semantic_query_plan_builder。
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

from ...runtime_config import DEFAULT_ROW_LIMIT
from ..core.filter_normalization import normalize_time_filter_leaf
from ..core.formatter import format_params_for_display
from ..core.input_validator import InputValidator
from ..core.query_plan import (
    SemanticQueryPlanContext,
    parse_aggregate_expression,
    semantic_table_alias,
)
from ..core.semantic_sql.compiler import SemanticSQLCompiler
from ..core.semantic_sql.dialect import get_dialect
from .semantic_query_plan_builder import (
    assemble_dimension_filter_node,
    collect_filter_node_tables,
    collect_operand_tables,
    metric_func_column,
)

logger = get_logger(__name__)


def _error(error: str) -> Dict[str, Any]:
    """统一引擎各阶段的错误返回形状。"""
    return {"success": False, "error": error}


@dataclass
class _UnifiedEnginePlan:
    """统一引擎流水线（校验 → 映射 → 组装 → 编译）的中间产物，按组装顺序填充。"""

    matched_metrics: List[Dict[str, Any]] = field(default_factory=list)
    matched_dimensions: List[Any] = field(default_factory=list)
    dimension_names: List[str] = field(default_factory=list)
    having_metric_names: List[str] = field(default_factory=list)
    validated_filters: List[Dict[str, Any]] = field(default_factory=list)
    validated_having: List[Dict[str, Any]] = field(default_factory=list)
    validated_order_by: List[Dict[str, Any]] = field(default_factory=list)
    validated_limit: Optional[int] = None
    validated_offset: Optional[int] = None
    requires_grouping_for_raw_fields: bool = False
    main_table: Optional[str] = None
    main_table_alias: Optional[str] = None
    builder_dimensions: List[Dict[str, Any]] = field(default_factory=list)
    builder_dimension_keys: set = field(default_factory=set)
    builder_aggregations: List[Dict[str, Any]] = field(default_factory=list)
    builder_filters: List[Dict[str, Any]] = field(default_factory=list)
    builder_having: List[Dict[str, Any]] = field(default_factory=list)
    builder_order_by: List[Dict[str, Any]] = field(default_factory=list)
    metric_alias_to_id: Dict[str, str] = field(default_factory=dict)
    tables_needed: set = field(default_factory=set)

    @property
    def has_selectable_fields(self) -> bool:
        return bool(self.builder_dimensions or self.builder_aggregations)


class SemanticAIQueryAdapter:
    """语义 DSL 输入适配器。"""

    def __init__(self, semantic_config: Dict, max_rows: Optional[int] = None):
        """
        初始化语义 DSL 适配器

        Args:
            semantic_config: 语义配置，包含 metrics, dimensions, relationships
        """
        self.query_plan_context = SemanticQueryPlanContext(semantic_config)
        self.semantic_config = self.query_plan_context.semantic_config
        self.metrics_map = self.query_plan_context.metrics_map
        self.dimensions_map = self.query_plan_context.dimensions_map
        self.max_rows = DEFAULT_ROW_LIMIT if max_rows is None else max_rows
        self.validator = InputValidator(self.metrics_map, self.dimensions_map, max_limit=self.max_rows)
        self.dialect_name = self.query_plan_context.dialect_name
        self.dialect = get_dialect(self.dialect_name)

        # 构建表和字段的存在性校验映射
        self._build_table_column_map()

    @staticmethod
    def _extract_dimension_name(dim_item: Any) -> str:
        if isinstance(dim_item, dict):
            return dim_item.get("name", "")
        return str(dim_item)

    @staticmethod
    def _extract_time_grain(dim_item: Any) -> Optional[str]:
        if isinstance(dim_item, dict):
            return dim_item.get("time_grain")
        return None

    @staticmethod
    def _parse_aggregate_expression(expression: str) -> Optional[tuple[str, str]]:
        return parse_aggregate_expression(expression)

    @staticmethod
    def _table_alias(item: Dict[str, Any]) -> str:
        return semantic_table_alias(item)

    @staticmethod
    def _quote_column_name(column: Any) -> str:
        cleaned = str(column or "").strip().replace("`", "")
        return f"`{cleaned}`"

    def _quote_dimension_column(self, column: Any) -> str:
        cleaned = str(column or "").strip().replace("`", "").replace('"', "")
        return self.dialect.quote_identifier(cleaned)

    @staticmethod
    def _sql_literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        escaped = str(value).replace("'", "''")
        return f"'{escaped}'"

    @staticmethod
    def _rewrite_expression_table_qualifier(expression: str, table_alias: str, physical_table: str) -> str:
        expr = str(expression or "").strip()
        if not expr or not table_alias or not physical_table or table_alias == physical_table:
            return expr
        escaped = re.escape(physical_table)
        return re.sub(rf"(?<![\w`])`?{escaped}`?\.", f"{table_alias}.", expr)

    @staticmethod
    def _relationship_left_alias(rel: Dict[str, Any]) -> str:
        return str((rel or {}).get("left_alias") or (rel or {}).get("left_table") or "").strip()

    @staticmethod
    def _relationship_right_alias(rel: Dict[str, Any]) -> str:
        return str((rel or {}).get("right_alias") or (rel or {}).get("right_table") or "").strip()

    def _collect_tables_from_filter_node(self, node: Dict[str, Any], tables_needed: set[str]):
        collect_filter_node_tables(node, tables_needed, self._extract_filter_leaf_tables)

    def _extract_filter_leaf_tables(self, node: Dict[str, Any], tables_needed: set[str]):
        table_name = node.get("tableAlias") or node.get("table")
        if table_name:
            tables_needed.add(table_name)
        for table_name in node.get("expressionTables") or []:
            if table_name:
                tables_needed.add(table_name)
        self._collect_tables_from_having_operand(node.get("rightOperand"), tables_needed)

    def _collect_tables_from_having_operand(self, operand: Any, tables_needed: set[str]):
        collect_operand_tables(operand, tables_needed, self._extract_metric_operand_table)

    @staticmethod
    def _extract_metric_operand_table(operand: Dict[str, Any], tables_needed: set[str]):
        table_name = operand.get("tableAlias") or operand.get("table")
        if table_name:
            tables_needed.add(table_name)

    def _map_filter_node_for_builder(self, node: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(node, dict):
            return {}

        if "and" in node:
            return {"and": [self._map_filter_node_for_builder(child) for child in node.get("and", []) or [] if isinstance(child, dict)]}
        if "or" in node:
            return {"or": [self._map_filter_node_for_builder(child) for child in node.get("or", []) or [] if isinstance(child, dict)]}

        if self._is_expression_filter_node(node):
            return self._map_expression_filter_node_for_builder(node)

        dim_name = node.get("dimension")
        dim_config = self.dimensions_map.get(dim_name, {})
        normalized_filters = self._map_filter_value_for_builder(node, dim_config)
        builder_nodes = [
            self._filter_leaf_to_builder_node(normalized_filter, dim_config)
            for normalized_filter in normalized_filters
        ]
        if len(builder_nodes) == 1:
            return builder_nodes[0]
        return {"and": builder_nodes}

    @staticmethod
    def _copy_filter_value_type(source: Dict[str, Any], target: Dict[str, Any]):
        """透传固定值类型或已通过输入校验的自由右侧表达式。"""
        if "value_type" in source:
            target["value_type"] = source.get("value_type")
        if source.get("valueType") == "expression" and "rightExpression" in source:
            target["valueType"] = "expression"
            target["rightExpression"] = source.get("rightExpression")

    def _filter_leaf_to_builder_node(self, fil: Dict[str, Any], dim_cfg: Dict[str, Any]) -> Dict[str, Any]:
        node = assemble_dimension_filter_node(
            dim_cfg,
            node_id=f"filter-{fil.get('dimension', '')}",
            operator=fil.get("operator", "="),
            logic=fil.get("logic", "AND"),
        )
        node["value"] = fil.get("value")
        self._copy_filter_value_type(fil, node)
        return node

    def _map_filter_value_for_builder(self, node: Dict[str, Any], dim_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return normalize_time_filter_leaf(node, dim_config)

    def _append_filter_nodes_for_builder(self, builder_filters: List[Dict[str, Any]], fil: Dict[str, Any]) -> None:
        dim_cfg = self.dimensions_map.get(fil.get("dimension"), {})
        for normalized_filter in self._map_filter_value_for_builder(fil, dim_cfg):
            builder_filters.append(self._filter_leaf_to_builder_node(normalized_filter, dim_cfg))

    @staticmethod
    def _is_expression_filter_node(node: Dict[str, Any]) -> bool:
        return isinstance(node, dict) and isinstance(node.get("left"), dict) and "right" in node

    def _dimension_sql_expression(self, dim_name: str) -> tuple[str, set[str]]:
        dim_cfg = self.dimensions_map.get(dim_name) or {}
        dim_table = dim_cfg.get("table")
        dim_table_alias = self._table_alias(dim_cfg) or dim_table
        dim_column = dim_cfg.get("column")
        dim_expression = str(dim_cfg.get("expression") or "").strip()
        if dim_expression:
            return self._rewrite_expression_table_qualifier(dim_expression, dim_table_alias, dim_table), {dim_table_alias} if dim_table_alias else set()
        column_expr = self._quote_dimension_column(dim_column)
        if dim_table_alias:
            column_expr = f"{dim_table_alias}.{column_expr}"
        return column_expr, {dim_table_alias} if dim_table_alias else set()

    def _compile_filter_operand(self, operand: Dict[str, Any]) -> tuple[str, set[str]]:
        operand_type = str((operand or {}).get("type") or "").strip().lower()
        if operand_type == "dimension":
            return self._dimension_sql_expression(str(operand.get("name") or "").strip())
        if operand_type == "literal":
            return self._sql_literal(operand.get("value")), set()
        if operand_type == "binary":
            left_expr, left_tables = self._compile_filter_operand(operand.get("left") or {})
            right_expr, right_tables = self._compile_filter_operand(operand.get("right") or {})
            operator = str(operand.get("operator") or "").strip()
            return f"({left_expr} {operator} {right_expr})", left_tables | right_tables
        return "NULL", set()

    def _map_expression_filter_node_for_builder(self, node: Dict[str, Any]) -> Dict[str, Any]:
        left = node.get("left") or {}
        left_dim_name = str(left.get("name") or "").strip()
        left_dim_cfg = self.dimensions_map.get(left_dim_name, {})
        left_table = left_dim_cfg.get("table")
        left_table_alias = self._table_alias(left_dim_cfg) or left_table
        right_expression, right_tables = self._compile_filter_operand(node.get("right") or {})
        return {
            "id": f"filter-{left_dim_name}",
            "table": left_table,
            "tableAlias": left_table_alias,
            "column": left_dim_cfg.get("column"),
            "expression": left_dim_cfg.get("expression"),
            "operator": node.get("operator", "="),
            "logic": node.get("logic", "AND"),
            "valueType": "expression",
            "rightExpression": right_expression,
            "expressionTables": sorted(table for table in right_tables if table),
        }

    @staticmethod
    def _is_expression_having_node(node: Dict[str, Any]) -> bool:
        return isinstance(node, dict) and isinstance(node.get("left"), dict) and "right" in node

    def _metric_aggregation_node(self, metric_name: str, main_table: str) -> Dict[str, Any]:
        metric_cfg = self.metrics_map.get(metric_name, {})
        metric_table = metric_cfg.get("table") or main_table
        metric_table_alias = self._table_alias(metric_cfg) or metric_table
        parsed = metric_func_column(metric_cfg.get("expression"))
        func = parsed[0] if parsed else "RAW"
        column = "*"
        if parsed and parsed[1]:
            column = parsed[1]
        elif metric_cfg.get("column"):
            column = metric_cfg.get("column")
        return {
            "table": metric_table,
            "tableAlias": metric_table_alias,
            "column": column,
            "expression": metric_cfg.get("expression"),
            "aggFunc": func,
        }

    def _map_having_operand_for_builder(
        self,
        operand: Dict[str, Any],
        metric_alias_to_id: Dict[str, str],
        main_table: str,
    ) -> Dict[str, Any]:
        operand_type = str((operand or {}).get("type") or "").strip().lower()
        if operand_type == "metric":
            metric_name = str((operand or {}).get("name") or "").strip()
            metric_node = self._metric_aggregation_node(metric_name, main_table)
            metric_node.update({
                "type": "metric",
                "name": metric_name,
                "aggId": metric_alias_to_id.get(metric_name),
            })
            return metric_node
        if operand_type == "literal":
            return {"type": "literal", "value": (operand or {}).get("value")}
        if operand_type == "binary":
            return {
                "type": "binary",
                "operator": str((operand or {}).get("operator") or "").strip(),
                "left": self._map_having_operand_for_builder((operand or {}).get("left") or {}, metric_alias_to_id, main_table),
                "right": self._map_having_operand_for_builder((operand or {}).get("right") or {}, metric_alias_to_id, main_table),
            }
        return {"type": "literal", "value": None}

    def _collect_having_metric_names(self, having: List[Dict[str, Any]]) -> List[str]:
        metric_names: List[str] = []
        seen: set[str] = set()

        def add_metric(name: Any):
            metric_name = str(name or "").strip()
            if metric_name and metric_name not in seen:
                seen.add(metric_name)
                metric_names.append(metric_name)

        def collect_operand(operand: Any):
            if not isinstance(operand, dict):
                return
            operand_type = str(operand.get("type") or "").strip().lower()
            if operand_type == "metric":
                add_metric(operand.get("name"))
            elif operand_type == "binary":
                collect_operand(operand.get("left"))
                collect_operand(operand.get("right"))

        for item in having or []:
            if not isinstance(item, dict):
                continue
            add_metric(item.get("metric"))
            if self._is_expression_having_node(item):
                collect_operand(item.get("left"))
                collect_operand(item.get("right"))

        return metric_names

    def _build_builder_joins(self, main_table: str, tables_needed: set[str]) -> List[Dict[str, Any]]:
        return self.query_plan_context.build_joins(main_table, tables_needed)

    def _normalize_metrics_allow_dimension_raw(self, metrics: List[str]) -> tuple[List[Dict[str, Any]], Optional[str]]:
        """
        允许 metrics 里包含维度字段：
        - 命中指标：按指标处理
        - 命中维度：转为 RAW 指标（用于明细展示）
        - 未命中：报错
        """
        normalized_metrics: List[Dict[str, Any]] = []
        valid_metric_names = list(self.metrics_map.keys())
        valid_dimension_names = list(self.dimensions_map.keys())
        for name in metrics or []:
            if name in self.metrics_map:
                normalized_metrics.append(self.metrics_map[name])
                continue
            if name in self.dimensions_map:
                dim_cfg = self.dimensions_map[name]
                raw_expression = dim_cfg.get("expression")
                if not raw_expression:
                    dim_table = dim_cfg.get("table")
                    dim_column = dim_cfg.get("column")
                    if dim_table and dim_column:
                        raw_expression = f"{dim_table}.{dim_column}"
                    else:
                        raw_expression = dim_column or ""
                normalized_metrics.append({
                    "name": name,
                    "table": dim_cfg.get("table"),
                    "table_alias": self._table_alias(dim_cfg),
                    "column": dim_cfg.get("column"),
                    "expression": raw_expression,
                    "description": dim_cfg.get("description"),
                    "_from_dimension": True,
                })
                continue
            return [], f"错误：指标 '{name}' 不存在。可用指标：{', '.join(valid_metric_names)}。可用维度：{', '.join(valid_dimension_names)}"
        return normalized_metrics, None

    def _build_table_column_map(self):
        """构建表和字段的映射，用于存在性校验"""
        self.tables_map = {}  # table_name -> {column_name: column_info}

        # 从数据源的 tables 配置中获取表结构
        tables = self.semantic_config.get('tables', [])
        for table in tables:
            table_name = table.get('table_name')
            columns = table.get('columns', [])
            if table_name:
                self.tables_map[table_name] = {
                    col.get('name'): col for col in columns if col.get('name')
                }

        # 如果没有 tables 配置，从指标和维度中提取表名（不校验字段）
        if not self.tables_map:
            for m in self.semantic_config.get('metrics', []):
                table = m.get('table')
                if table and table not in self.tables_map:
                    self.tables_map[table] = None  # None 表示不校验字段

            for d in self.semantic_config.get('dimensions', []):
                table = d.get('table')
                if table and table not in self.tables_map:
                    self.tables_map[table] = None

    def _validate_table_column(self, table_name: str, column_name: str) -> Optional[str]:
        """
        校验表和字段是否存在

        Args:
            table_name: 表名
            column_name: 字段名

        Returns:
            错误消息（如果校验失败），None（如果校验通过）
        """
        # 校验表是否存在
        if table_name not in self.tables_map:
            available_tables = list(self.tables_map.keys())
            return f"表 '{table_name}' 不存在。可用表：{', '.join(available_tables)}"

        # 如果有字段信息，校验字段是否存在
        columns_map = self.tables_map[table_name]
        if columns_map is not None:  # None 表示跳过字段校验
            if column_name not in columns_map:
                available_columns = list(columns_map.keys())
                return f"表 '{table_name}' 中不存在字段 '{column_name}'。可用字段：{', '.join(available_columns[:10])}{'...' if len(available_columns) > 10 else ''}"

        return None

    def generate_sql(
        self,
        metrics: List[str],
        dimensions: List[str],
        filters: List[Dict],
        having: List[Dict],
        order_by: List[Dict] = None,
        limit: int = None,
        offset: int = None,
    ) -> Dict[str, Any]:
        """
        生成 SQL（统一入口）

        Args:
            metrics: 指标名称列表
            dimensions: 维度名称列表
            filters: WHERE 过滤条件
            having: HAVING 过滤条件
            order_by: 排序条件
            limit: 限制条数
            offset: 跳过条数

        Returns:
            {
                "success": bool,
                "sql": str | None,
                "params_block": str | None,
                "error": str | None,
                "user_message": str | None  # 仅当 config_not_support 时
            }
        """
        return self._generate_sql_via_unified_engine(metrics, dimensions, filters, having, order_by, limit, offset)

    def _generate_sql_via_unified_engine(
        self,
        metrics: List[str],
        dimensions: List[str],
        filters: List[Dict],
        having: List[Dict],
        order_by: List[Dict] = None,
        limit: int = None,
        offset: int = None,
    ) -> Dict[str, Any]:
        """统一引擎流水线：校验 → 主表推断 → 维度/指标映射 → 过滤/排序映射 → 编译。"""
        plan = _UnifiedEnginePlan()
        err = self._validate_unified_engine_inputs(plan, metrics, dimensions, filters, having, order_by, limit, offset)
        if err:
            return _error(err)

        self._infer_main_table_and_alias(plan)
        if not plan.main_table:
            return _error("错误：查询至少需要一个指标或维度来确定数据范围")

        self._build_builder_dimensions(plan)
        self._build_builder_aggregations(plan)
        if not plan.has_selectable_fields:
            return _error("错误：未找到可用于查询的字段")
        self._build_builder_filters_and_having(plan)
        self._build_builder_order_by(plan)
        self._collect_tables_needed(plan)
        return self._compile_unified_engine_sql(plan, metrics, dimensions, filters, having)

    def _validate_unified_engine_inputs(
        self,
        plan: _UnifiedEnginePlan,
        metrics: List[str],
        dimensions: List[str],
        filters: List[Dict],
        having: List[Dict],
        order_by: List[Dict],
        limit: int,
        offset: int,
    ) -> Optional[str]:
        """串行校验各入参并填充校验结果；任一失败返回错误文案，全部通过返回 None。"""
        err = self._validate_and_fill_query_fields(plan, metrics, dimensions, filters, having, order_by, limit, offset)
        if err:
            return err
        self._fill_query_field_names_and_grouping(plan)
        return None

    def _validate_and_fill_query_fields(
        self,
        plan: _UnifiedEnginePlan,
        metrics: List[str],
        dimensions: List[str],
        filters: List[Dict],
        having: List[Dict],
        order_by: List[Dict],
        limit: int,
        offset: int,
    ) -> Optional[str]:
        """逐项校验指标/维度/过滤/HAVING/排序/limit/offset 并写入 plan。"""
        if metrics:
            plan.matched_metrics, err = self._normalize_metrics_allow_dimension_raw(metrics)
            if err:
                return err

        plan.matched_dimensions, err = self.validator.validate_dimensions(dimensions or [])
        if err:
            return err

        plan.validated_filters, err = self.validator.validate_filters(filters or [])
        if err:
            return err

        plan.validated_having, err = self.validator.validate_having(having or [])
        if err:
            return err

        plan.having_metric_names = self._collect_having_metric_names(plan.validated_having)
        plan.dimension_names = [self._extract_dimension_name(d) for d in plan.matched_dimensions]

        plan.validated_order_by, err = self.validator.validate_order_by(order_by or [], metrics or [], plan.dimension_names)
        if err:
            return err

        plan.validated_limit, err = self.validator.validate_limit(limit)
        if err:
            return err

        plan.validated_offset, err = self.validator.validate_offset(offset)
        if err:
            return err
        return None

    def _fill_query_field_names_and_grouping(self, plan: _UnifiedEnginePlan) -> None:
        """推导 RAW 字段是否必须进 GROUP BY。

        SQL 形态只由 DSL 和语义配置决定，不能根据 user_query 的关键词猜测。
        只要输出中存在聚合表达式或 HAVING，RAW 字段就必须进入 GROUP BY。
        having_metric_names / dimension_names 已在校验阶段填充。
        """
        plan.requires_grouping_for_raw_fields = bool(plan.validated_having) or any(
            metric_func_column(metric.get("expression"))
            for metric in plan.matched_metrics
        )

    def _infer_main_table_and_alias(self, plan: _UnifiedEnginePlan) -> None:
        """按第一维度表 > 第一指标表 > 第一 HAVING 指标表推断主表及别名。"""
        if plan.matched_dimensions:
            first_dim_name = self._extract_dimension_name(plan.matched_dimensions[0])
            first_dim_cfg = self.dimensions_map.get(first_dim_name) or {}
            plan.main_table = first_dim_cfg.get("table")
            plan.main_table_alias = self._table_alias(first_dim_cfg)
        if not plan.main_table and plan.matched_metrics:
            first_metric = plan.matched_metrics[0]
            plan.main_table = first_metric.get("table")
            plan.main_table_alias = self._table_alias(first_metric)
        if not plan.main_table and plan.having_metric_names:
            plan.main_table = (self.metrics_map.get(plan.having_metric_names[0]) or {}).get("table")
        plan.main_table_alias = plan.main_table_alias or plan.main_table

    def _build_builder_dimensions(self, plan: _UnifiedEnginePlan) -> None:
        for dim_item in plan.matched_dimensions:
            dim_name = self._extract_dimension_name(dim_item)
            dim_cfg = self.dimensions_map.get(dim_name)
            if not dim_cfg:
                continue
            time_grain = self._extract_time_grain(dim_item)
            transform = "raw"
            if time_grain in ("year", "quarter", "month", "week", "day", "hour"):
                transform = time_grain
            dim_table = dim_cfg.get("table")
            dim_table_alias = self._table_alias(dim_cfg) or dim_table
            plan.builder_dimensions.append({
                "id": f"dim-{dim_name}",
                "table": dim_table,
                "tableAlias": dim_table_alias,
                "column": dim_cfg.get("column"),
                "alias": dim_name,
                "expression": dim_cfg.get("expression"),
                "transform": transform,
            })
            plan.builder_dimension_keys.add((dim_table_alias, dim_cfg.get("column")))

    def _build_builder_aggregations(self, plan: _UnifiedEnginePlan) -> None:
        for metric in plan.matched_metrics:
            metric_name = metric.get("name")
            metric_expression = (metric.get("expression") or "").strip()
            metric_table = metric.get("table")
            metric_table_alias = self._table_alias(metric) or metric_table
            metric_column = metric.get("column")
            agg_id = f"agg-{metric_name}"
            plan.metric_alias_to_id[metric_name] = agg_id
            parsed = metric_func_column(metric_expression)
            if parsed:
                func = parsed[0]
                if parsed[1]:
                    metric_column = parsed[1]
                plan.builder_aggregations.append({
                    "id": agg_id,
                    "func": func,
                    "table": metric_table,
                    "tableAlias": metric_table_alias,
                    "column": metric_column or "*",
                    "alias": metric_name,
                    "expression": metric_expression,
                })
            else:
                self._append_raw_metric_block(
                    plan, metric_name, metric_table, metric_table_alias, metric_expression, metric_column,
                )

    def _append_raw_metric_block(
        self,
        plan: _UnifiedEnginePlan,
        metric_name: Any,
        metric_table: Optional[str],
        metric_table_alias: Optional[str],
        metric_expression: str,
        metric_column: Optional[str],
    ) -> None:
        """RAW 指标映射：存在聚合输出时转分组字段，否则作为 RAW 聚合块。"""
        raw_table = metric_table or plan.main_table
        raw_table_alias = metric_table_alias or raw_table
        raw_expr = metric_expression or (f"{raw_table_alias}.{metric_column}" if raw_table_alias and metric_column else "")
        raw_column = metric_column or "*"
        # 存在聚合输出时，RAW 指标需作为分组字段，避免 SELECT 聚合+非聚合非法。
        if plan.requires_grouping_for_raw_fields and raw_column != "*":
            dim_key = (raw_table_alias, raw_column)
            if dim_key not in plan.builder_dimension_keys:
                plan.builder_dimensions.append({
                    "id": f"dim-from-metric-{metric_name}",
                    "table": raw_table,
                    "tableAlias": raw_table_alias,
                    "column": raw_column,
                    "alias": metric_name,
                    "expression": raw_expr,
                    "transform": "raw",
                })
                plan.builder_dimension_keys.add(dim_key)
        else:
            plan.builder_aggregations.append({
                "id": f"agg-{metric_name}",
                "func": "RAW",
                "table": raw_table,
                "tableAlias": raw_table_alias,
                "column": raw_column,
                "alias": metric_name,
                "expression": raw_expr,
            })

    def _build_builder_filters_and_having(self, plan: _UnifiedEnginePlan) -> None:
        validated_filters = plan.validated_filters
        if len(validated_filters) == 1 and isinstance(validated_filters[0], dict) and ("and" in validated_filters[0] or "or" in validated_filters[0]):
            plan.builder_filters.append(self._map_filter_node_for_builder(validated_filters[0]))
        else:
            for fil in validated_filters:
                if self._is_expression_filter_node(fil):
                    plan.builder_filters.append(self._map_expression_filter_node_for_builder(fil))
                    continue
                self._append_filter_nodes_for_builder(plan.builder_filters, fil)

        for hv in plan.validated_having:
            if self._is_expression_having_node(hv):
                left_metric_name = str(((hv.get("left") or {}).get("name")) or "").strip()
                having_node = self._metric_aggregation_node(left_metric_name, plan.main_table)
                having_node.update({
                    "id": f"having-{left_metric_name}",
                    "operator": hv.get("operator", "="),
                    "logic": hv.get("logic", "AND"),
                    "aggId": plan.metric_alias_to_id.get(left_metric_name),
                    "rightOperand": self._map_having_operand_for_builder(hv.get("right") or {}, plan.metric_alias_to_id, plan.main_table),
                })
                plan.builder_having.append(having_node)
                continue

            metric_name = hv.get("metric")
            having_node = self._metric_aggregation_node(metric_name, plan.main_table)
            having_node.update({
                "id": f"having-{metric_name}",
                "operator": hv.get("operator", "="),
                "value": hv.get("value"),
                "logic": hv.get("logic", "AND"),
                "aggId": plan.metric_alias_to_id.get(metric_name),
            })
            plan.builder_having.append(having_node)
            self._copy_filter_value_type(hv, having_node)

    def _build_builder_order_by(self, plan: _UnifiedEnginePlan) -> None:
        for od in plan.validated_order_by:
            field_name = od.get("field")
            direction = od.get("direction", "DESC")
            if field_name in self.metrics_map:
                metric_cfg = self.metrics_map.get(field_name, {})
                metric_table_alias = self._table_alias(metric_cfg) or metric_cfg.get("table") or plan.main_table
                parsed = metric_func_column(metric_cfg.get("expression"))
                func = parsed[0] if parsed else "RAW"
                column = "*"
                if parsed and parsed[1]:
                    column = parsed[1]
                elif metric_cfg.get("column"):
                    column = metric_cfg.get("column")
                plan.builder_order_by.append({
                    "id": f"order-{field_name}",
                    "table": metric_cfg.get("table", plan.main_table),
                    "tableAlias": metric_table_alias,
                    "column": column,
                    "expression": metric_cfg.get("expression"),
                    "direction": direction,
                    "aggFunc": func,
                    "aggId": plan.metric_alias_to_id.get(field_name),
                })
            else:
                dim_cfg = self.dimensions_map.get(field_name, {})
                dim_table_alias = self._table_alias(dim_cfg) or dim_cfg.get("table") or plan.main_table
                plan.builder_order_by.append({
                    "id": f"order-{field_name}",
                    "table": dim_cfg.get("table", plan.main_table),
                    "tableAlias": dim_table_alias,
                    "column": dim_cfg.get("column", field_name),
                    "direction": direction,
                })

    @staticmethod
    def _block_table_alias(block: Dict[str, Any]) -> Optional[str]:
        return block.get("tableAlias") or block.get("table")

    def _collect_tables_needed(self, plan: _UnifiedEnginePlan) -> None:
        plan.tables_needed = {plan.main_table_alias}
        for dim in plan.builder_dimensions:
            if self._block_table_alias(dim):
                plan.tables_needed.add(self._block_table_alias(dim))
        for agg in plan.builder_aggregations:
            if self._block_table_alias(agg):
                plan.tables_needed.add(self._block_table_alias(agg))
        for hv in plan.builder_having:
            if self._block_table_alias(hv):
                plan.tables_needed.add(self._block_table_alias(hv))
            self._collect_tables_from_having_operand(hv.get("rightOperand"), plan.tables_needed)
        for od in plan.builder_order_by:
            if self._block_table_alias(od):
                plan.tables_needed.add(self._block_table_alias(od))
        for fil in plan.builder_filters:
            self._collect_tables_from_filter_node(fil, plan.tables_needed)

    def _compile_unified_engine_sql(
        self,
        plan: _UnifiedEnginePlan,
        metrics: List[str],
        dimensions: List[str],
        filters: List[Dict],
        having: List[Dict],
    ) -> Dict[str, Any]:
        try:
            query_plan = {
                "mainTable": plan.main_table,
                "mainAlias": plan.main_table_alias,
                "joins": self._build_builder_joins(plan.main_table_alias, plan.tables_needed),
                "dimensions": plan.builder_dimensions,
                "aggregations": plan.builder_aggregations,
                "filters": plan.builder_filters + plan.builder_having,
                "orderBy": plan.builder_order_by,
                "limit": plan.validated_limit,
                "offset": plan.validated_offset,
            }
            sql = SemanticSQLCompiler(dialect=self.dialect_name, max_rows=self.max_rows).compile(query_plan)
        except Exception as err:
            logger.error(f"统一 DSL2SQL 编译失败: {err}", exc_info=True)
            return _error(f"错误：SQL 转换失败 - {str(err)}")

        params_block = format_params_for_display(
            metrics, plan.dimension_names, filters, having,
            plan.validated_order_by, plan.validated_limit, plan.validated_offset,
        )
        return {"success": True, "sql": sql, "params_block": params_block, "error": None}
