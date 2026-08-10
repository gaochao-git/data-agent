"""AI/BI 语义查询的统一应用服务。"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from utils.logger import get_logger
from ...datasource.manager import get_datasource_manager
from ...sql_run.result import build_query_result_payload, query_result_status
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
from .semantic_model_repository import load_semantic_model
from .semantic_config_service import get_semantic_config_service

logger = get_logger(__name__)


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
        if not isinstance(node, dict):
            return
        if "and" in node:
            for child in node.get("and", []) or []:
                self._collect_tables_from_filter_node(child, tables_needed)
            return
        if "or" in node:
            for child in node.get("or", []) or []:
                self._collect_tables_from_filter_node(child, tables_needed)
            return
        table_name = node.get("tableAlias") or node.get("table")
        if table_name:
            tables_needed.add(table_name)
        for table_name in node.get("expressionTables") or []:
            if table_name:
                tables_needed.add(table_name)
        self._collect_tables_from_having_operand(node.get("rightOperand"), tables_needed)

    def _collect_tables_from_having_operand(self, operand: Any, tables_needed: set[str]):
        if not isinstance(operand, dict):
            return
        operand_type = str(operand.get("type") or "").strip().lower()
        if operand_type == "metric":
            table_name = operand.get("tableAlias") or operand.get("table")
            if table_name:
                tables_needed.add(table_name)
            return
        if operand_type == "binary":
            self._collect_tables_from_having_operand(operand.get("left"), tables_needed)
            self._collect_tables_from_having_operand(operand.get("right"), tables_needed)

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
        node = {
            "id": f"filter-{fil.get('dimension', '')}",
            "table": dim_cfg.get("table"),
            "tableAlias": self._table_alias(dim_cfg) or dim_cfg.get("table"),
            "column": dim_cfg.get("column"),
            "expression": dim_cfg.get("expression"),
            "operator": fil.get("operator", "="),
            "value": fil.get("value"),
            "logic": fil.get("logic", "AND"),
        }
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
        parsed = self._parse_aggregate_expression((metric_cfg.get("expression") or "").strip())
        func = parsed[0] if parsed else "RAW"
        column = "*"
        if parsed and parsed[1]:
            column_expr = parsed[1]
            column = "*" if column_expr == "*" else column_expr.split(".")[-1].replace("`", "")
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
        user_query: str = None,
        compare: Optional[Dict[str, Any]] = None,
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
            user_query: 历史兼容参数，保留签名但不参与 SQL 生成

        Returns:
            {
                "success": bool,
                "sql": str | None,
                "params_block": str | None,
                "error": str | None,
                "user_message": str | None  # 仅当 config_not_support 时
            }
        """
        return self._generate_sql_via_unified_engine(metrics, dimensions, filters, having, order_by, limit, offset, user_query, compare)

    def _generate_sql_via_unified_engine(
        self,
        metrics: List[str],
        dimensions: List[str],
        filters: List[Dict],
        having: List[Dict],
        order_by: List[Dict] = None,
        limit: int = None,
        offset: int = None,
        user_query: Optional[str] = None,
        compare: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if compare:
            return {"success": False, "error": "错误：DSL2SQL 不再支持 compare 同比/环比；请拆成基础 DSL 查询；复杂同比/环比请使用智能分析"}

        matched_metrics = []
        if metrics:
            matched_metrics, err = self._normalize_metrics_allow_dimension_raw(metrics)
            if err:
                return {"success": False, "error": err}

        matched_dimensions, err = self.validator.validate_dimensions(dimensions or [])
        if err:
            return {"success": False, "error": err}

        validated_filters, err = self.validator.validate_filters(filters or [])
        if err:
            return {"success": False, "error": err}

        validated_having, err = self.validator.validate_having(having or [])
        if err:
            return {"success": False, "error": err}

        having_metric_names = self._collect_having_metric_names(validated_having)

        dimension_names = [self._extract_dimension_name(d) for d in matched_dimensions]
        validated_order_by, err = self.validator.validate_order_by(order_by or [], metrics or [], dimension_names)
        if err:
            return {"success": False, "error": err}

        validated_limit, err = self.validator.validate_limit(limit)
        if err:
            return {"success": False, "error": err}

        validated_offset, err = self.validator.validate_offset(offset)
        if err:
            return {"success": False, "error": err}

        # SQL 形态只由 DSL 和语义配置决定，不能根据 user_query 的关键词猜测。
        # 只要输出中存在聚合表达式或 HAVING，RAW 字段就必须进入 GROUP BY。
        requires_grouping_for_raw_fields = bool(validated_having) or any(
            self._parse_aggregate_expression((metric.get("expression") or "").strip())
            for metric in matched_metrics
        )

        main_table = None
        if matched_dimensions:
            first_dim_name = self._extract_dimension_name(matched_dimensions[0])
            main_table = (self.dimensions_map.get(first_dim_name) or {}).get("table")
        if not main_table and matched_metrics:
            main_table = matched_metrics[0].get("table")
        if not main_table and having_metric_names:
            main_table = (self.metrics_map.get(having_metric_names[0]) or {}).get("table")
        if not main_table:
            return {"success": False, "error": "错误：查询至少需要一个指标或维度来确定数据范围"}

        builder_dimensions = []
        builder_dimension_keys: set[tuple[str, str]] = set()
        for dim_item in matched_dimensions:
            dim_name = self._extract_dimension_name(dim_item)
            dim_cfg = self.dimensions_map.get(dim_name)
            if not dim_cfg:
                continue
            time_grain = self._extract_time_grain(dim_item)
            transform = "raw"
            expression = dim_cfg.get("expression")
            dim_table = dim_cfg.get("table")
            dim_table_alias = self._table_alias(dim_cfg) or dim_table
            if time_grain in ("year", "quarter", "month", "week", "day", "hour"):
                transform = time_grain
            dim_key = (dim_table_alias, dim_cfg.get("column"))
            builder_dimensions.append({
                "id": f"dim-{dim_name}",
                "table": dim_table,
                "tableAlias": dim_table_alias,
                "column": dim_cfg.get("column"),
                "alias": dim_name,
                "expression": expression,
                "transform": transform,
            })
            builder_dimension_keys.add(dim_key)

        builder_aggregations = []
        metric_alias_to_id: Dict[str, str] = {}
        for metric in matched_metrics:
            metric_name = metric.get("name")
            metric_expression = (metric.get("expression") or "").strip()
            metric_table = metric.get("table")
            metric_table_alias = self._table_alias(metric) or metric_table
            metric_column = metric.get("column")
            agg_id = f"agg-{metric_name}"
            metric_alias_to_id[metric_name] = agg_id
            parsed = self._parse_aggregate_expression(metric_expression)
            if parsed:
                func, column_expr = parsed
                if "." in column_expr:
                    metric_column = column_expr.split(".")[-1].replace("`", "")
                elif column_expr and column_expr != "*":
                    metric_column = column_expr.replace("`", "")
                elif column_expr == "*":
                    metric_column = "*"
                builder_aggregations.append({
                    "id": agg_id,
                    "func": func,
                    "table": metric_table,
                    "tableAlias": metric_table_alias,
                    "column": metric_column or "*",
                    "alias": metric_name,
                    "expression": metric_expression,
                })
            else:
                raw_table = metric_table or main_table
                raw_table_alias = metric_table_alias or raw_table
                raw_expr = metric_expression or (f"{raw_table_alias}.{metric_column}" if raw_table_alias and metric_column else "")
                raw_column = metric_column or "*"
                # 存在聚合输出时，RAW 指标需作为分组字段，避免 SELECT 聚合+非聚合非法。
                if requires_grouping_for_raw_fields and raw_column != "*":
                    dim_key = (raw_table_alias, raw_column)
                    if dim_key not in builder_dimension_keys:
                        builder_dimensions.append({
                            "id": f"dim-from-metric-{metric_name}",
                            "table": raw_table,
                            "tableAlias": raw_table_alias,
                            "column": raw_column,
                            "alias": metric_name,
                            "expression": raw_expr,
                            "transform": "raw",
                        })
                        builder_dimension_keys.add(dim_key)
                else:
                    builder_aggregations.append({
                        "id": agg_id,
                        "func": "RAW",
                        "table": raw_table,
                        "tableAlias": raw_table_alias,
                        "column": raw_column,
                        "alias": metric_name,
                        "expression": raw_expr,
                    })

        if not builder_dimensions and not builder_aggregations:
            return {"success": False, "error": "错误：未找到可用于查询的字段"}

        builder_filters = []
        if len(validated_filters) == 1 and isinstance(validated_filters[0], dict) and ("and" in validated_filters[0] or "or" in validated_filters[0]):
            builder_filters.append(self._map_filter_node_for_builder(validated_filters[0]))
        else:
            for fil in validated_filters:
                if self._is_expression_filter_node(fil):
                    builder_filters.append(self._map_expression_filter_node_for_builder(fil))
                    continue
                self._append_filter_nodes_for_builder(builder_filters, fil)

        builder_having = []
        for hv in validated_having:
            if self._is_expression_having_node(hv):
                left_metric_name = str(((hv.get("left") or {}).get("name")) or "").strip()
                having_node = self._metric_aggregation_node(left_metric_name, main_table)
                having_node.update({
                    "id": f"having-{left_metric_name}",
                    "operator": hv.get("operator", "="),
                    "logic": hv.get("logic", "AND"),
                    "aggId": metric_alias_to_id.get(left_metric_name),
                    "rightOperand": self._map_having_operand_for_builder(hv.get("right") or {}, metric_alias_to_id, main_table),
                })
                builder_having.append(having_node)
                continue

            metric_name = hv.get("metric")
            having_node = self._metric_aggregation_node(metric_name, main_table)
            having_node.update({
                "id": f"having-{metric_name}",
                "operator": hv.get("operator", "="),
                "value": hv.get("value"),
                "logic": hv.get("logic", "AND"),
                "aggId": metric_alias_to_id.get(metric_name),
            })
            builder_having.append(having_node)
            self._copy_filter_value_type(hv, having_node)

        builder_order_by = []
        for od in validated_order_by:
            field_name = od.get("field")
            direction = od.get("direction", "DESC")
            if field_name in self.metrics_map:
                metric_cfg = self.metrics_map.get(field_name, {})
                metric_table_alias = self._table_alias(metric_cfg) or metric_cfg.get("table") or main_table
                parsed = self._parse_aggregate_expression((metric_cfg.get("expression") or "").strip())
                func = parsed[0] if parsed else "RAW"
                column = "*"
                if parsed and parsed[1]:
                    column_expr = parsed[1]
                    column = "*" if column_expr == "*" else column_expr.split(".")[-1].replace("`", "")
                elif metric_cfg.get("column"):
                    column = metric_cfg.get("column")
                builder_order_by.append({
                    "id": f"order-{field_name}",
                    "table": metric_cfg.get("table", main_table),
                    "tableAlias": metric_table_alias,
                    "column": column,
                    "expression": metric_cfg.get("expression"),
                    "direction": direction,
                    "aggFunc": func,
                    "aggId": metric_alias_to_id.get(field_name),
                })
            else:
                dim_cfg = self.dimensions_map.get(field_name, {})
                dim_table_alias = self._table_alias(dim_cfg) or dim_cfg.get("table") or main_table
                builder_order_by.append({
                    "id": f"order-{field_name}",
                    "table": dim_cfg.get("table", main_table),
                    "tableAlias": dim_table_alias,
                    "column": dim_cfg.get("column", field_name),
                    "direction": direction,
                })

        main_table_alias = None
        if matched_dimensions:
            first_dim_name = self._extract_dimension_name(matched_dimensions[0])
            main_table_alias = self._table_alias(self.dimensions_map.get(first_dim_name) or {})
        if not main_table_alias and matched_metrics:
            main_table_alias = self._table_alias(matched_metrics[0])
        main_table_alias = main_table_alias or main_table

        tables_needed = {main_table_alias}
        for dim in builder_dimensions:
            if dim.get("tableAlias") or dim.get("table"):
                tables_needed.add(dim.get("tableAlias") or dim.get("table"))
        for agg in builder_aggregations:
            if agg.get("tableAlias") or agg.get("table"):
                tables_needed.add(agg.get("tableAlias") or agg.get("table"))
        for hv in builder_having:
            if hv.get("tableAlias") or hv.get("table"):
                tables_needed.add(hv.get("tableAlias") or hv.get("table"))
            self._collect_tables_from_having_operand(hv.get("rightOperand"), tables_needed)
        for od in builder_order_by:
            if od.get("tableAlias") or od.get("table"):
                tables_needed.add(od.get("tableAlias") or od.get("table"))
        for fil in builder_filters:
            self._collect_tables_from_filter_node(fil, tables_needed)

        try:
            query_plan = {
                "mainTable": main_table,
                "mainAlias": main_table_alias,
                "joins": self._build_builder_joins(main_table_alias, tables_needed),
                "dimensions": builder_dimensions,
                "aggregations": builder_aggregations,
                "filters": builder_filters,
                "orderBy": builder_order_by,
                "limit": validated_limit,
                "offset": validated_offset,
            }
            if builder_having:
                query_plan["filters"] = query_plan["filters"] + builder_having
            sql = SemanticSQLCompiler(dialect=self.dialect_name, max_rows=self.max_rows).compile(query_plan)
        except Exception as err:
            logger.error(f"统一 DSL2SQL 编译失败: {err}", exc_info=True)
            return {"success": False, "error": f"错误：SQL 转换失败 - {str(err)}"}

        params_block = format_params_for_display(metrics, dimension_names, filters, having, validated_order_by, validated_limit, validated_offset)
        return {"success": True, "sql": sql, "params_block": params_block, "error": None}
FORBIDDEN_SEMANTIC_BUILDER_KEYS = {"mainTable", "mainAlias", "joins"}


def _as_list(value: Any, field_name: str = "字段") -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("语义可视化 DSL 字段必须是数组")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"语义可视化 DSL {field_name}[{index}] 必须是对象")
    return value


def _normalize_alias(item: Dict[str, Any]) -> str:
    return str(item.get("tableAlias") or item.get("table_alias") or item.get("alias") or item.get("table") or "").strip()


def _semantic_alias(config_item: Dict[str, Any]) -> str:
    return str(config_item.get("table_alias") or config_item.get("alias") or config_item.get("table") or "").strip()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _field_keys(table: str, table_alias: str, column: str) -> Set[Tuple[str, str]]:
    keys: Set[Tuple[str, str]] = set()
    normalized_table = _normalize_text(table)
    normalized_alias = _normalize_text(table_alias) or normalized_table
    normalized_column = _normalize_text(column)
    if normalized_table and normalized_column:
        keys.add((normalized_table, normalized_column))
    if normalized_alias and normalized_column:
        keys.add((normalized_alias, normalized_column))
    return keys


def _parse_aggregate_expression(expression: str) -> Tuple[str, str] | None:
    parsed = parse_aggregate_expression(expression)
    if not parsed:
        return None
    func, column_expr = parsed
    column = "*" if column_expr == "*" else column_expr.split(".")[-1].replace("`", "").strip()
    return func, column


class SemanticBIQueryAdapter:
    """可视化查询输入适配器。"""

    def __init__(self, semantic_config: Dict[str, Any], max_rows: Optional[int] = None):
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

    def adapt(self, semantic_builder_config: Dict[str, Any]) -> Dict[str, Any]:
        """校验并规范化 Builder 输入，不在此阶段生成 SQL。"""
        if not isinstance(semantic_builder_config, dict):
            raise ValueError("语义可视化 DSL 格式错误")
        forbidden = FORBIDDEN_SEMANTIC_BUILDER_KEYS.intersection(semantic_builder_config.keys())
        if forbidden:
            raise ValueError(f"语义可视化 DSL 不允许包含前端表关系字段: {', '.join(sorted(forbidden))}")

        dimensions = [self._map_dimension(item) for item in _as_list(semantic_builder_config.get("dimensions"), "dimensions")]
        aggregations = [self._map_aggregation(item) for item in _as_list(semantic_builder_config.get("aggregations"), "aggregations")]
        filters = self._map_filter_nodes(semantic_builder_config.get("filters") or [])
        order_by = [self._map_order_by(item, aggregations, dimensions) for item in _as_list(semantic_builder_config.get("orderBy"), "orderBy")]
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

    def compile(self, semantic_builder_config: Dict[str, Any]) -> Dict[str, Any]:
        """适配 Builder 输入后，通过统一语义 SQL 编译入口生成 SQL。"""
        query_plan = self.adapt(semantic_builder_config)
        sql = SemanticSQLCompiler(
            dialect=self.query_plan_context.dialect_name,
            max_rows=self.max_rows,
        ).compile(query_plan)
        # builder_config 是既有前端响应字段，内部统一使用 query_plan。
        return {"sql": sql, "builder_config": query_plan}

    def _validate_limit(self, value: Any) -> Optional[int]:
        limit, err = self.validator.validate_limit(value)
        if err:
            raise ValueError(err)
        return limit

    def _validate_offset(self, value: Any) -> Optional[int]:
        offset, err = self.validator.validate_offset(value)
        if err:
            raise ValueError(err)
        return offset

    def _resolve_dimension_config(self, item: Dict[str, Any]) -> Dict[str, Any]:
        name_candidates = [
            item.get("semanticName"),
            item.get("name"),
            item.get("alias"),
            item.get("displayName"),
        ]
        for name in name_candidates:
            normalized = _normalize_text(name)
            if normalized and normalized in self.dimensions_map:
                return self.dimensions_map[normalized]

        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        item_expression = _normalize_text(item.get("expression"))
        for dim in self.semantic_config.get("dimensions") or []:
            dim_keys = _field_keys(dim.get("table"), _semantic_alias(dim), dim.get("column"))
            if item_keys and item_keys.intersection(dim_keys):
                dim_expression = _normalize_text(dim.get("expression"))
                if not item_expression or not dim_expression or item_expression == dim_expression:
                    return dim
        raise ValueError(f"语义维度不存在或不在当前指标集内: {item.get('alias') or item.get('displayName') or item.get('column')}")

    def _resolve_metric_config(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        name_candidates = [
            item.get("semanticName"),
            item.get("name"),
            item.get("alias"),
            item.get("displayName"),
        ]
        for name in name_candidates:
            normalized = _normalize_text(name)
            if normalized and normalized in self.metrics_map:
                return self.metrics_map[normalized]

        item_func = _normalize_text(item.get("func") or "RAW").upper()
        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        item_expression = _normalize_text(item.get("expression"))
        matched_by_field: List[Dict[str, Any]] = []
        for metric in self.semantic_config.get("metrics") or []:
            metric_expression = _normalize_text(metric.get("expression"))
            parsed = _parse_aggregate_expression(metric_expression)
            metric_func = parsed[0] if parsed else "RAW"
            metric_column = parsed[1] if parsed else (metric.get("column") or metric.get("field_name") or "")
            metric_keys = _field_keys(metric.get("table"), _semantic_alias(metric), metric_column)
            if item_expression and metric_expression and item_expression == metric_expression:
                return metric
            if item_func == metric_func and item_keys and item_keys.intersection(metric_keys):
                matched_by_field.append(metric)
        if len(matched_by_field) == 1:
            return matched_by_field[0]
        if len(matched_by_field) > 1:
            metric_names = "、".join(_normalize_text(metric.get("name")) for metric in matched_by_field if _normalize_text(metric.get("name")))
            raise ValueError(f"指标字段匹配到多个语义指标，请传 semanticName 或 expression: {metric_names}")
        return None

    def _map_dimension(self, item: Dict[str, Any]) -> Dict[str, Any]:
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

    def _map_aggregation(self, item: Dict[str, Any]) -> Dict[str, Any]:
        metric = self._resolve_metric_config(item)
        if metric:
            parsed = _parse_aggregate_expression(_normalize_text(metric.get("expression")))
            func = parsed[0] if parsed else _normalize_text(item.get("func") or "RAW").upper()
            column = parsed[1] if parsed else (metric.get("column") or item.get("column") or "*")
            table = metric.get("table") or item.get("table")
            table_alias = _semantic_alias(metric) or table
            expression = metric.get("expression") or item.get("expression")
            display_name = metric.get("name") or item.get("alias") or item.get("displayName") or column
        else:
            dim = self._resolve_dimension_config(item)
            func = _normalize_text(item.get("func") or "RAW").upper()
            if func not in {"RAW", "COUNT", "SUM", "AVG", "MAX", "MIN", "COUNT_DISTINCT"}:
                raise ValueError(f"字段 '{item.get('alias') or item.get('displayName') or item.get('column')}' 未配置为指标，不能作为聚合指标使用")
            table = dim.get("table")
            table_alias = _semantic_alias(dim) or table
            column = dim.get("column")
            expression = dim.get("expression") or item.get("expression")
            display_name = item.get("alias") or item.get("displayName") or dim.get("name") or column

        return {
            "id": item.get("id") or f"agg-{display_name}",
            "func": func,
            "table": table,
            "tableAlias": table_alias,
            "column": column,
            "alias": display_name,
            "expression": expression,
        }

    def _map_filter_nodes(self, value: Any) -> List[Dict[str, Any]]:
        if not value:
            return []
        if not isinstance(value, list):
            raise ValueError("语义可视化 filters 必须是数组")
        mapped: List[Dict[str, Any]] = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise ValueError(f"语义可视化 filters[{index}] 必须是对象")
            node = self._map_filter_node(item)
            if isinstance(node, list):
                mapped.extend(node)
            else:
                mapped.append(node)
        return mapped

    def _map_filter_node(self, node: Dict[str, Any]) -> Dict[str, Any] | List[Dict[str, Any]]:
        if "and" in node:
            children = node.get("and") or []
            if not isinstance(children, list):
                raise ValueError("语义可视化 and 过滤节点必须是数组")
            if not children:
                raise ValueError("语义可视化 and 过滤节点不能为空")
            mapped_children: List[Dict[str, Any]] = []
            for index, child in enumerate(children):
                if not isinstance(child, dict):
                    raise ValueError(f"语义可视化 and[{index}] 必须是对象")
                mapped_child = self._map_filter_node(child)
                if isinstance(mapped_child, list):
                    mapped_children.extend(mapped_child)
                else:
                    mapped_children.append(mapped_child)
            return {"and": mapped_children}
        if "or" in node:
            children = node.get("or") or []
            if not isinstance(children, list):
                raise ValueError("语义可视化 or 过滤节点必须是数组")
            if not children:
                raise ValueError("语义可视化 or 过滤节点不能为空")
            mapped_children = []
            for index, child in enumerate(children):
                if not isinstance(child, dict):
                    raise ValueError(f"语义可视化 or[{index}] 必须是对象")
                mapped_child = self._map_filter_node(child)
                if isinstance(mapped_child, list):
                    mapped_children.append({"and": mapped_child})
                else:
                    mapped_children.append(mapped_child)
            return {"or": mapped_children}
        if isinstance(node.get("left"), dict) and "right" in node:
            return self._map_having_filter(node)
        self._validate_filter_value(node)
        if node.get("aggFunc"):
            return self._map_having_filter(node)
        mapped_filters = self._map_dimension_filter_leaves(node)
        return mapped_filters[0] if len(mapped_filters) == 1 else mapped_filters

    @staticmethod
    def _has_filter_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True

    @classmethod
    def _validate_filter_value(cls, node: Dict[str, Any]) -> None:
        operator = _normalize_text(node.get("operator") or "=").upper()
        if operator in {"IS NULL", "IS NOT NULL", "EMPTY STRING"}:
            return

        label = _normalize_text(
            node.get("displayName")
            or node.get("semanticName")
            or node.get("name")
            or node.get("column")
            or "筛选条件"
        )
        value_type = _normalize_text(node.get("valueType") or node.get("value_type") or "literal").lower()
        if value_type == "field_ref":
            if not _normalize_text(node.get("rightTable")) or not _normalize_text(node.get("rightColumn")):
                raise ValueError(f"筛选条件“{label}”请选择右侧字段")
            return
        if value_type == "expression":
            if not _normalize_text(node.get("rightExpression")):
                raise ValueError(f"筛选条件“{label}”请输入表达式")
            return

        value = node.get("value")
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

    def _copy_filter_value_fields(self, source: Dict[str, Any], target: Dict[str, Any]) -> None:
        for key in ("value", "valueType", "value_type", "rightExpression", "logic"):
            if key in source:
                target[key] = source.get(key)
        if source.get("valueType") == "field_ref":
            right = self._resolve_dimension_config({
                "table": source.get("rightTable"),
                "tableAlias": source.get("rightTableAlias"),
                "column": source.get("rightColumn"),
            })
            target["rightTable"] = right.get("table")
            target["rightTableAlias"] = _semantic_alias(right) or right.get("table")
            target["rightColumn"] = right.get("column")

    def _map_having_operand(self, operand: Any) -> Dict[str, Any]:
        if not isinstance(operand, dict):
            raise ValueError("HAVING 表达式操作数必须是对象")
        operand_type = _normalize_text(operand.get("type")).lower()
        if operand_type == "metric":
            metric = self._resolve_metric_config({
                "semanticName": operand.get("name") or operand.get("semanticName"),
                "name": operand.get("name"),
                "alias": operand.get("alias"),
                "displayName": operand.get("displayName"),
                "func": operand.get("aggFunc") or operand.get("func"),
                "table": operand.get("table"),
                "tableAlias": _normalize_alias(operand),
                "column": operand.get("column"),
                "expression": operand.get("expression"),
            })
            if not metric:
                raise ValueError(f"HAVING 表达式引用的指标不存在: {operand.get('name') or operand.get('semanticName') or operand.get('column')}")
            agg = self._map_aggregation({
                "semanticName": metric.get("name"),
                "func": operand.get("aggFunc") or operand.get("func"),
                "table": metric.get("table"),
                "tableAlias": _semantic_alias(metric),
                "column": operand.get("column"),
                "expression": operand.get("expression"),
            })
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
        if operand_type == "literal":
            value = operand.get("value")
            if isinstance(value, (dict, list)):
                raise ValueError("HAVING 表达式 literal.value 只支持字符串、数字、布尔值或空值")
            return {"type": "literal", "value": value}
        if operand_type == "binary":
            operator = _normalize_text(operand.get("operator"))
            if operator not in {"+", "-", "*", "/"}:
                raise ValueError("HAVING 表达式二元运算符只支持 +、-、*、/")
            right = operand.get("right")
            if operator == "/" and isinstance(right, dict) and right.get("type") == "literal":
                try:
                    if float(right.get("value")) == 0:
                        raise ValueError("HAVING 表达式不允许除以 0")
                except ValueError:
                    raise
                except Exception:
                    pass
            return {
                "type": "binary",
                "operator": operator,
                "left": self._map_having_operand(operand.get("left")),
                "right": self._map_having_operand(right),
            }
        raise ValueError("HAVING 表达式操作数只支持 metric、literal、binary")

    def _map_dimension_filter(self, node: Dict[str, Any]) -> Dict[str, Any]:
        dim = self._resolve_dimension_config(node)
        return self._map_dimension_filter_with_config(node, dim)

    def _map_dimension_filter_leaves(self, node: Dict[str, Any]) -> List[Dict[str, Any]]:
        dim = self._resolve_dimension_config(node)
        self._validate_dimension_filter_operator(node, dim)
        return [
            self._map_dimension_filter_with_config(normalized_node, dim, validate_operator=False)
            for normalized_node in normalize_time_filter_leaf(node, dim)
        ]

    def _validate_dimension_filter_operator(self, node: Dict[str, Any], dim: Dict[str, Any]) -> str:
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
        node: Dict[str, Any],
        dim: Dict[str, Any],
        *,
        validate_operator: bool = True,
    ) -> Dict[str, Any]:
        operator = (
            self._validate_dimension_filter_operator(node, dim)
            if validate_operator
            else str(node.get("operator") or "=").strip().upper()
        )
        mapped = {
            "id": node.get("id") or f"filter-{dim.get('name') or dim.get('column')}",
            "table": dim.get("table"),
            "tableAlias": _semantic_alias(dim) or dim.get("table"),
            "column": dim.get("column"),
            "expression": dim.get("expression") or node.get("expression"),
            "operator": operator,
            "logic": node.get("logic", "AND"),
        }
        self._copy_filter_value_fields(node, mapped)
        return mapped

    def _map_having_filter(self, node: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(node.get("left"), dict) and "right" in node:
            left = self._map_having_operand(node.get("left"))
            if left.get("type") != "metric":
                raise ValueError("HAVING 表达式 left 只支持 metric")
            agg = left
        else:
            agg = self._map_aggregation({
                "id": node.get("aggId"),
                "func": node.get("aggFunc"),
                "table": node.get("table"),
                "tableAlias": _normalize_alias(node),
                "column": node.get("column"),
                "alias": node.get("displayName"),
                "semanticName": node.get("semanticName"),
                "displayName": node.get("displayName"),
                "expression": node.get("expression"),
            })
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

    def _map_order_by(self, item: Dict[str, Any], aggregations: List[Dict[str, Any]], dimensions: List[Dict[str, Any]]) -> Dict[str, Any]:
        direction = _normalize_text(item.get("direction") or "ASC").upper()
        if direction not in {"ASC", "DESC"}:
            raise ValueError("排序方向只能是 ASC 或 DESC")
        if item.get("aggFunc"):
            agg = self._find_selected_aggregation(item, aggregations) or self._map_aggregation({
                "id": item.get("aggId"),
                "func": item.get("aggFunc"),
                "table": item.get("table"),
                "tableAlias": _normalize_alias(item),
                "column": item.get("column"),
                "alias": item.get("displayName") or item.get("alias"),
                "semanticName": item.get("semanticName"),
                "displayName": item.get("displayName"),
                "expression": item.get("expression"),
            })
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
    def _find_selected_dimension(item: Dict[str, Any], dimensions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        for dim in dimensions:
            if item_keys.intersection(_field_keys(dim.get("table"), dim.get("tableAlias"), dim.get("column"))):
                return dim
        return None

    @staticmethod
    def _find_selected_aggregation(item: Dict[str, Any], aggregations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        agg_id = _normalize_text(item.get("aggId"))
        if agg_id:
            for agg in aggregations:
                if _normalize_text(agg.get("id")) == agg_id:
                    return agg
        item_expression = _normalize_text(item.get("expression"))
        if item_expression:
            for agg in aggregations:
                if _normalize_text(agg.get("expression")) == item_expression:
                    return agg
            return None
        name_candidates = [
            _normalize_text(item.get("semanticName")),
            _normalize_text(item.get("name")),
            _normalize_text(item.get("alias")),
            _normalize_text(item.get("displayName")),
        ]
        explicit_names = {name for name in name_candidates if name}
        if explicit_names:
            for agg in aggregations:
                if _normalize_text(agg.get("alias")) in explicit_names:
                    return agg
            return None
        item_func = _normalize_text(item.get("aggFunc") or item.get("func")).upper()
        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        for agg in aggregations:
            if item_func and _normalize_text(agg.get("func")).upper() != item_func:
                continue
            if item_keys.intersection(_field_keys(agg.get("table"), agg.get("tableAlias"), agg.get("column"))):
                return agg
        return None

    @staticmethod
    def _collect_filter_tables(node: Dict[str, Any], tables_needed: Set[str]) -> None:
        if "and" in node:
            for child in node.get("and") or []:
                if isinstance(child, dict):
                    SemanticBIQueryAdapter._collect_filter_tables(child, tables_needed)
            return
        if "or" in node:
            for child in node.get("or") or []:
                if isinstance(child, dict):
                    SemanticBIQueryAdapter._collect_filter_tables(child, tables_needed)
            return
        table_alias = _normalize_alias(node)
        if table_alias:
            tables_needed.add(table_alias)
        right_alias = _normalize_text(node.get("rightTableAlias") or node.get("rightTable"))
        if node.get("valueType") == "field_ref" and right_alias:
            tables_needed.add(right_alias)

    def _collect_tables_needed(
        self,
        main_alias: str,
        dimensions: List[Dict[str, Any]],
        aggregations: List[Dict[str, Any]],
        filters: List[Dict[str, Any]],
        order_by: List[Dict[str, Any]],
    ) -> Set[str]:
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
        dimensions: List[Dict[str, Any]],
        aggregations: List[Dict[str, Any]],
        filters: List[Dict[str, Any]],
        order_by: List[Dict[str, Any]],
    ) -> Tuple[str, str]:
        for collection in (dimensions, aggregations, order_by):
            for item in collection:
                table = _normalize_text(item.get("table"))
                alias = _normalize_alias(item) or table
                if table:
                    return table, alias
        for node in filters:
            table, alias = self._first_filter_table(node)
            if table:
                return table, alias or table
        raise ValueError("语义可视化查询无法确定主表")

    def _first_filter_table(self, node: Dict[str, Any]) -> Tuple[str, str]:
        if "and" in node or "or" in node:
            children = node.get("and") if "and" in node else node.get("or")
            for child in children or []:
                if isinstance(child, dict):
                    table, alias = self._first_filter_table(child)
                    if table:
                        return table, alias
            return "", ""
        return _normalize_text(node.get("table")), _normalize_alias(node)


async def load_runtime_semantic_config(datasource_id: str, semantic_config_id: str, user_id: str | None = None) -> Dict[str, Any]:
    normalized_datasource_id = _normalize_text(datasource_id)
    normalized_config_id = _normalize_text(semantic_config_id)
    if not normalized_datasource_id or not normalized_config_id:
        raise ValueError("缺少 datasource_id 或 semantic_config_id")
    try:
        return await load_semantic_model(
            normalized_datasource_id,
            normalized_config_id,
            user_id=_normalize_text(user_id),
            mode="runtime",
            manager=get_datasource_manager(),
            semantic_service=get_semantic_config_service(),
        )
    except ValueError as exc:
        message = str(exc)
        if message == "数据源不存在":
            raise LookupError(f"数据源不存在: {normalized_datasource_id}") from exc
        if message == "未找到可用语义配置":
            raise LookupError(f"语义配置不存在: {normalized_config_id}") from exc
        if message == "语义配置未启用":
            raise ValueError(f"语义配置未启用: {normalized_config_id}") from exc
        raise


async def compile_semantic_builder_sql(
    datasource_id: str,
    semantic_config_id: str,
    semantic_builder_config: Dict[str, Any],
    user_id: str | None = None,
) -> Dict[str, Any]:
    semantic_config = await load_runtime_semantic_config(datasource_id, semantic_config_id, user_id=user_id) if user_id else await load_runtime_semantic_config(datasource_id, semantic_config_id)
    result = compile_semantic_bi_dsl(semantic_config, semantic_builder_config)
    if not result.get("success"):
        raise ValueError(str(result.get("error") or "BI 语义 DSL 编译失败"))
    return result


@dataclass(frozen=True)
class SemanticCompileOptions:
    """语义 DSL 编译选项；应用层负责注入，不由编译器读取环境变量。"""

    user_query: Optional[str] = None
    max_rows: Optional[int] = None
    offset: Optional[int] = None


def compile_semantic_ai_dsl(
    semantic_config: Dict[str, Any],
    dsl: Dict[str, Any],
    *,
    options: Optional[SemanticCompileOptions] = None,
    generator_cls: Optional[Callable[[Dict[str, Any]], Any]] = None,
    generator: Any = None,
) -> Dict[str, Any]:
    """统一语义 DSL 编译门面，所有应用入口应通过本函数编译。"""
    if not isinstance(semantic_config, dict):
        return {"success": False, "sql": "", "error": "语义模型必须是对象"}
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "语义 DSL 必须是对象"}

    compile_options = options or SemanticCompileOptions()
    compiler_factory = generator_cls or SemanticAIQueryAdapter
    compiler = generator or compiler_factory(semantic_config)
    compile_result = compiler.generate_sql(
        metrics=dsl.get("metrics") or [],
        dimensions=dsl.get("dimensions") or [],
        filters=dsl.get("filters") or [],
        having=dsl.get("having") or [],
        order_by=dsl.get("order_by") or [],
        limit=dsl.get("limit", compile_options.max_rows),
        offset=dsl.get("offset", compile_options.offset),
        user_query=compile_options.user_query,
    )
    return {
        "success": bool(compile_result.get("success")),
        "sql": str(compile_result.get("sql") or "").strip(),
        "error": compile_result.get("error"),
        "warnings": compile_result.get("warnings") or [],
    }


def compile_semantic_bi_dsl(
    semantic_config: Dict[str, Any],
    dsl: Dict[str, Any],
) -> Dict[str, Any]:
    """编译 BI 可视化语义 DSL，返回 SQL 和内部查询计划。"""
    if not isinstance(semantic_config, dict):
        return {"success": False, "sql": "", "error": "语义模型必须是对象"}
    if not isinstance(dsl, dict):
        return {"success": False, "sql": "", "error": "BI 语义 DSL 必须是对象"}
    try:
        result = SemanticBIQueryAdapter(semantic_config, max_rows=DEFAULT_ROW_LIMIT).compile(dsl)
    except (TypeError, ValueError) as exc:
        return {"success": False, "sql": "", "error": str(exc)}
    return {
        "success": True,
        "sql": str(result.get("sql") or "").strip(),
        "query_plan": result.get("builder_config") or {},
        # 保留已有可视化响应字段，新的公共门面内部使用 query_plan。
        "builder_config": result.get("builder_config") or {},
        "error": None,
    }


def normalize_member_datasource_ids(member_datasource_ids: Any) -> List[str]:
    """Normalize member datasource ids while preserving the first-seen order."""
    normalized_ids: List[str] = []
    for item in member_datasource_ids or []:
        member_id = str(item or "").strip()
        if member_id and member_id not in normalized_ids:
            normalized_ids.append(member_id)
    return normalized_ids


async def execute_semantic_query_sql(
    *,
    sql: str,
    datasource_id: Optional[str],
    member_datasource_ids: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    semantic_config_id: Optional[str] = None,
    query_text: Optional[str] = None,
    conversation_id: Optional[str] = None,
    operator_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute semantic query SQL through the unified query runtime."""
    # 延迟导入避免 query_runtime 的 BI 编译入口与本服务形成模块循环。
    from ...sql_run import runtime as query_runtime

    return await query_runtime.execute_agent_sql(
        sql=sql,
        datasource_id=datasource_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        semantic_config_id=semantic_config_id,
        query_text=query_text,
        conversation_id=conversation_id,
        operator_name=operator_name,
    )


def build_member_query_result(
    base_result: Dict[str, Any],
    sql: str,
    member_datasource_id: Optional[str] = None,
    member_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a normalized member query result."""
    return {
        "status": query_result_status(base_result),
        "sql": sql,
        "columns": base_result.get("columns", []),
        "data": base_result.get("data", []),
        "total_count": base_result.get("total_count", 0),
        "execution_time_ms": base_result.get("execution_time_ms"),
        "error": base_result.get("error"),
        "member_datasource_id": member_datasource_id,
        "member_name": member_name,
    }


def build_multi_member_query_result(sql: str, member_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a normalized multi-member query result payload."""
    return build_query_result_payload(sql=sql, member_results=member_results)


class SemanticQueryService:
    """AI/BI 语义编译和查询执行的统一无状态门面。"""

    compile_ai_dsl = staticmethod(compile_semantic_ai_dsl)
    compile_bi_dsl = staticmethod(compile_semantic_bi_dsl)
    compile_bi_query = staticmethod(compile_semantic_builder_sql)
    execute_sql = staticmethod(execute_semantic_query_sql)
    normalize_member_ids = staticmethod(normalize_member_datasource_ids)
    build_member_result = staticmethod(build_member_query_result)
    build_multi_member_result = staticmethod(build_multi_member_query_result)
