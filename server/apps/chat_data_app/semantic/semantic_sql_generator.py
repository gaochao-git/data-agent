"""
语义 SQL 生成器

封装 DSL 生成和 SQL 转换的完整逻辑，
包括参数验证、意图匹配、SQL 验证等功能。
"""

import re
from collections import deque
from typing import List, Dict, Optional, Any

from utils.logger import get_logger
from .builder_sql_compiler import compile_builder_sql
from .filter_normalization import normalize_time_filter_leaf
from .formatter import format_params_for_display
from .input_validator import InputValidator

logger = get_logger(__name__)


class QueryIntentType:
    """查询意图类型常量。"""
    DETAIL = "detail"
    AGGREGATE = "aggregate"


class SemanticSQLGenerator:
    """语义 SQL 生成器（统一 DSL2SQL 引擎）"""

    def __init__(self, semantic_config: Dict):
        """
        初始化生成器

        Args:
            semantic_config: 语义配置，包含 metrics, dimensions, relationships
        """
        # 构建映射
        metrics_map = {m['name']: m for m in semantic_config.get('metrics', [])}
        dimensions_map = {d['name']: d for d in semantic_config.get('dimensions', [])}
        self.metrics_map = metrics_map
        self.dimensions_map = dimensions_map

        self.validator = InputValidator(metrics_map, dimensions_map)

        self.semantic_config = semantic_config

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
        expr = (expression or "").strip()
        if not expr:
            return None
        match = re.match(r"^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$", expr, flags=re.IGNORECASE)
        if not match:
            return None
        func = match.group(1).upper()
        column_expr = (match.group(3) or "").strip()
        if func == "COUNT" and (match.group(2) or "").strip():
            return "COUNT_DISTINCT", column_expr
        return func, column_expr

    @staticmethod
    def _has_detail_intent(user_query: Optional[str]) -> bool:
        text = (user_query or "").strip().lower()
        if not text:
            return False
        keywords = ("明细", "详情", "详细", "列表", "list", "detail", "drill", "下钻")
        return any(k in text for k in keywords)

    @staticmethod
    def _table_alias(item: Dict[str, Any]) -> str:
        return str((item or {}).get("table_alias") or (item or {}).get("alias") or (item or {}).get("table") or "").strip()

    @staticmethod
    def _quote_column_name(column: Any) -> str:
        cleaned = str(column or "").strip().replace("`", "")
        return f"`{cleaned}`"

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

    @staticmethod
    def _detail_intent_only_from_metric_name(user_query: Optional[str], matched_metrics: List[Dict[str, Any]]) -> bool:
        """识别“明细”只是指标名称一部分的情况，避免误判为明细查询。"""
        text = (user_query or "").strip().lower()
        if not text:
            return False
        metric_names = [str(item.get("name") or "").strip().lower() for item in matched_metrics]
        return any(name and name in text for name in metric_names)

    def _determine_query_intent_type(
        self,
        metrics: List[str],
        matched_metrics: List[Dict[str, Any]],
        dimensions: List[Any],
        having: List[Dict[str, Any]],
        user_query: Optional[str],
    ) -> str:
        """
        判断当前查询应按明细还是聚合处理。

        判定优先级：
        1. 用户明确表达明细意图 -> 明细
        2. 出现 HAVING -> 聚合
        3. 选择了聚合指标 -> 聚合
        4. 其余场景回落为明细（dimensions 是明细展示字段）
        """
        if self._has_detail_intent(user_query) and not self._detail_intent_only_from_metric_name(user_query, matched_metrics):
            return QueryIntentType.DETAIL
        if having:
            return QueryIntentType.AGGREGATE
        if metrics and self._has_aggregate_metric(matched_metrics):
            return QueryIntentType.AGGREGATE
        return QueryIntentType.DETAIL

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
        """语义 DSL 只允许透传固定值的 value_type，不透传自由 SQL 表达式。"""
        for key in ("value_type",):
            if key in source:
                target[key] = source.get(key)

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
        column_expr = self._quote_column_name(dim_column)
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
        relationships = self.semantic_config.get("relationships", []) or []
        if len(tables_needed) <= 1 or not relationships:
            return []

        adjacency: Dict[str, List[tuple[Dict[str, Any], str]]] = {}
        for rel in relationships:
            left = self._relationship_left_alias(rel)
            right = self._relationship_right_alias(rel)
            if not left or not right:
                continue
            adjacency.setdefault(left, []).append((rel, right))
            adjacency.setdefault(right, []).append((rel, left))

        prev: Dict[str, tuple[str, Dict[str, Any]]] = {}
        visited = {main_table}
        queue = deque([main_table])
        while queue:
            current = queue.popleft()
            for rel, nxt in adjacency.get(current, []):
                if nxt in visited:
                    continue
                visited.add(nxt)
                prev[nxt] = (current, rel)
                queue.append(nxt)

        required_nodes = {main_table}
        required_rels: List[Dict[str, Any]] = []
        rel_seen: set[str] = set()
        for target in tables_needed:
            if target == main_table:
                continue
            node = target
            while node != main_table and node in prev:
                required_nodes.add(node)
                parent, rel = prev[node]
                rel_id = f"{self._relationship_left_alias(rel)}|{self._relationship_right_alias(rel)}|{rel.get('join_type')}|{str(rel.get('join_conditions') or [])}"
                if rel_id not in rel_seen:
                    rel_seen.add(rel_id)
                    required_rels.append(rel)
                node = parent

        joins: List[Dict[str, Any]] = []
        added = {main_table}
        while len(added) < len(required_nodes):
            selected_rel = None
            for rel in required_rels:
                left = self._relationship_left_alias(rel)
                right = self._relationship_right_alias(rel)
                if left in added and right in required_nodes and right not in added:
                    selected_rel = (rel, left, right, False)
                    break
                if right in added and left in required_nodes and left not in added:
                    selected_rel = (rel, right, left, True)
                    break
            if not selected_rel:
                break
            rel, left_alias, right_alias, reversed_pair = selected_rel
            conditions = rel.get("join_conditions") or []
            join_conditions = []
            for cond in conditions:
                left_col = (cond or {}).get("left_column")
                right_col = (cond or {}).get("right_column")
                if not left_col or not right_col:
                    continue
                if reversed_pair:
                    join_conditions.append({"leftTable": rel.get("right_table"), "leftAlias": left_alias, "leftColumn": right_col, "rightColumn": left_col})
                else:
                    join_conditions.append({"leftTable": rel.get("left_table"), "leftAlias": left_alias, "leftColumn": left_col, "rightColumn": right_col})
            if not join_conditions and rel.get("left_column") and rel.get("right_column"):
                if reversed_pair:
                    join_conditions.append({"leftTable": rel.get("right_table"), "leftAlias": left_alias, "leftColumn": rel.get("right_column"), "rightColumn": rel.get("left_column")})
                else:
                    join_conditions.append({"leftTable": rel.get("left_table"), "leftAlias": left_alias, "leftColumn": rel.get("left_column"), "rightColumn": rel.get("right_column")})

            left_physical_table = rel.get("right_table") if reversed_pair else rel.get("left_table")
            right_physical_table = rel.get("left_table") if reversed_pair else rel.get("right_table")

            joins.append({
                "id": f"{left_alias}-{right_alias}",
                "leftTable": left_physical_table,
                "leftAlias": left_alias,
                "rightTable": right_physical_table,
                "rightAlias": right_alias,
                "joinType": str(rel.get("join_type", "left")).upper(),
                "joinConditions": join_conditions,
                "leftColumn": join_conditions[0]["leftColumn"] if join_conditions else "",
                "rightColumn": join_conditions[0]["rightColumn"] if join_conditions else "",
            })
            added.add(right_alias)
        return joins

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
            user_query: 用户原始查询（用于日志记录）

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

        query_intent_type = self._determine_query_intent_type(
            metrics=metrics or [],
            matched_metrics=matched_metrics,
            dimensions=matched_dimensions,
            having=validated_having,
            user_query=user_query,
        )
        is_detail_query = query_intent_type == QueryIntentType.DETAIL
        is_aggregate_query = query_intent_type == QueryIntentType.AGGREGATE
        logger.debug(
            f"[语义查询判定] type={query_intent_type}, metrics={metrics}, "
            f"dimensions={[self._extract_dimension_name(d) for d in matched_dimensions]}, "
            f"having={bool(validated_having)}"
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
                if is_detail_query:
                    column_expr = parsed[1]
                    if column_expr == "*":
                        continue
                    raw_column = column_expr.split(".")[-1].replace("`", "")
                    raw_table = metric_table or main_table
                    raw_table_alias = metric_table_alias or raw_table
                    raw_expr = f"{raw_table_alias}.{raw_column}" if raw_table_alias else raw_column
                    builder_aggregations.append({
                        "id": agg_id,
                        "func": "RAW",
                        "table": raw_table,
                        "tableAlias": raw_table_alias,
                        "column": raw_column,
                        "alias": metric_name,
                        "expression": raw_expr,
                    })
                    continue
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
                # 聚合查询中，RAW 指标需作为分组字段，避免 SELECT 聚合+非聚合非法
                if is_aggregate_query and raw_column != "*":
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

        builder_config = {
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
            builder_config["filters"] = builder_config["filters"] + builder_having
        try:
            sql = compile_builder_sql(builder_config, dialect=self.semantic_config.get("source_type", "mysql"))
        except Exception as err:
            logger.error(f"统一 DSL2SQL 编译失败: {err}", exc_info=True)
            return {"success": False, "error": f"错误：SQL 转换失败 - {str(err)}"}

        params_block = format_params_for_display(metrics, dimension_names, filters, having, validated_order_by, validated_limit, validated_offset)
        return {"success": True, "sql": sql, "params_block": params_block, "error": None}

    def _has_aggregate_metric(self, matched_metrics: List[Dict]) -> bool:
        """判断指标列表中是否包含聚合表达式。"""
        for metric in matched_metrics:
            expression = (metric.get("expression") or "").strip()
            if not expression:
                continue
            if self._is_aggregate_expression(expression):
                return True
        return False

    def _is_aggregate_expression(self, expression: str) -> bool:
        """判断单个表达式是否为聚合表达式。"""
        expr = (expression or "").strip().upper()
        return bool(expr) and (
            expr.startswith("COUNT(")
            or expr.startswith("SUM(")
            or expr.startswith("AVG(")
            or expr.startswith("MAX(")
            or expr.startswith("MIN(")
        )
