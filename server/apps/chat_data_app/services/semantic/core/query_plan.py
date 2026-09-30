"""语义模型到统一查询计划的共享上下文。"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from .common import ensure_dimension_logical_type

AGGREGATE_EXPRESSION_PATTERN = re.compile(
    r"^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$",
    re.IGNORECASE,
)

#: 派生指标的 metric_kind 标识；缺省（无该字段）即为原子指标。
DERIVED_METRIC_KIND = "derived"

#: 派生指标支持的统计周期预设；between 为自定义起止区间。
DERIVED_PERIOD_KINDS = {"today", "yesterday", "last_n_days", "this_week", "this_month", "between"}

#: 复合指标的 metric_kind 标识；口径由 formula 对多个原子指标做四则运算得出。
COMPOSITE_METRIC_KIND = "composite"

#: 复合指标公式中的指标引用占位符，如 "{销售额} / {订单数}"。
FORMULA_REF_PATTERN = re.compile(r"\{([^{}]+)\}")

#: 复合指标公式去除引用后允许的字符（四则运算、括号、数字、空白）。
FORMULA_OPERATOR_PATTERN = re.compile(r"^[\d\s+\-*/().]*$")


def is_derived_metric(metric_cfg: Any) -> bool:
    """判断指标配置是否为派生指标（原子指标 + 周期 + 修饰词）。"""
    return str((metric_cfg or {}).get("metric_kind") or "").strip().lower() == DERIVED_METRIC_KIND


def is_composite_metric(metric_cfg: Any) -> bool:
    """判断指标配置是否为复合指标（多指标四则运算公式）。"""
    return str((metric_cfg or {}).get("metric_kind") or "").strip().lower() == COMPOSITE_METRIC_KIND


def parse_formula_refs(formula: str) -> list[str]:
    """提取公式中的 {指标引用}，按首次出现顺序去重。"""
    refs: list[str] = []
    seen: set[str] = set()
    for match in FORMULA_REF_PATTERN.finditer(str(formula or "")):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            refs.append(name)
    return refs


def parse_aggregate_expression(expression: str) -> tuple[str, str] | None:
    """解析语义指标支持的聚合表达式。"""
    match = AGGREGATE_EXPRESSION_PATTERN.match(str(expression or "").strip())
    if not match:
        return None
    function = match.group(1).upper()
    column_expression = (match.group(3) or "").strip()
    if function == "COUNT" and (match.group(2) or "").strip():
        return "COUNT_DISTINCT", column_expression
    return function, column_expression


def semantic_table_alias(item: dict[str, Any]) -> str:
    return str((item or {}).get("table_alias") or (item or {}).get("alias") or (item or {}).get("table") or "").strip()


def _relationship_alias(relationship: dict[str, Any], side: str) -> str:
    return str((relationship or {}).get(f"{side}_alias") or (relationship or {}).get(f"{side}_table") or "").strip()


class SemanticQueryPlanContext:
    """两种输入适配器共享的只读语义模型索引和 JOIN 规划。"""

    def __init__(self, semantic_config: dict[str, Any]):
        normalized_config = dict(semantic_config or {})
        normalized_config["dimensions"] = [
            ensure_dimension_logical_type(item) for item in normalized_config.get("dimensions") or []
        ]
        self.semantic_config = normalized_config
        self.metrics_map = {item["name"]: item for item in normalized_config.get("metrics") or [] if item.get("name")}
        self.dimensions_map = {
            item["name"]: item for item in normalized_config.get("dimensions") or [] if item.get("name")
        }
        self.dialect_name = str(normalized_config.get("source_type") or "mysql").strip().lower() or "mysql"

    def build_joins(self, main_alias: str, tables_needed: set[str]) -> list[dict[str, Any]]:
        tables_needed = {str(table or "").strip() for table in tables_needed if str(table or "").strip()}
        relationships = self.semantic_config.get("relationships") or []
        if len(tables_needed) <= 1:
            return []
        if not relationships:
            self._raise_disconnected_tables(main_alias, tables_needed - {main_alias})

        adjacency: dict[str, list[tuple[dict[str, Any], str]]] = {}
        for relationship in relationships:
            left = _relationship_alias(relationship, "left")
            right = _relationship_alias(relationship, "right")
            if left and right:
                adjacency.setdefault(left, []).append((relationship, right))
                adjacency.setdefault(right, []).append((relationship, left))

        previous = self._find_paths(main_alias, adjacency)
        disconnected_tables = tables_needed - {main_alias} - set(previous)
        if disconnected_tables:
            self._raise_disconnected_tables(main_alias, disconnected_tables)
        required_nodes, required_relationships = self._collect_required_paths(
            main_alias,
            tables_needed,
            previous,
        )
        return self._build_ordered_joins(main_alias, required_nodes, required_relationships)

    @staticmethod
    def _raise_disconnected_tables(main_alias: str, tables: set[str]) -> None:
        table_names = "、".join(sorted(tables))
        raise ValueError(f"语义模型缺少从主表 {main_alias} 到表 {table_names} 的关联路径")

    @staticmethod
    def _find_paths(
        main_alias: str,
        adjacency: dict[str, list[tuple[dict[str, Any], str]]],
    ) -> dict[str, tuple[str, dict[str, Any]]]:
        previous: dict[str, tuple[str, dict[str, Any]]] = {}
        visited = {main_alias}
        queue = deque([main_alias])
        while queue:
            current = queue.popleft()
            for relationship, next_alias in adjacency.get(current, []):
                if next_alias in visited:
                    continue
                visited.add(next_alias)
                previous[next_alias] = (current, relationship)
                queue.append(next_alias)
        return previous

    @staticmethod
    def _collect_required_paths(
        main_alias: str,
        tables_needed: set[str],
        previous: dict[str, tuple[str, dict[str, Any]]],
    ) -> tuple[set[str], list[dict[str, Any]]]:
        required_nodes = {main_alias}
        required_relationships: list[dict[str, Any]] = []
        seen_relationships: set[int] = set()
        for target in tables_needed:
            node = target
            while node != main_alias and node in previous:
                required_nodes.add(node)
                parent, relationship = previous[node]
                relationship_identity = id(relationship)
                if relationship_identity not in seen_relationships:
                    seen_relationships.add(relationship_identity)
                    required_relationships.append(relationship)
                node = parent
        return required_nodes, required_relationships

    @classmethod
    def _build_ordered_joins(
        cls,
        main_alias: str,
        required_nodes: set[str],
        relationships: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        joins: list[dict[str, Any]] = []
        added = {main_alias}
        while len(added) < len(required_nodes):
            selected = cls._select_next_relationship(added, required_nodes, relationships)
            if not selected:
                break
            relationship, left_alias, right_alias, reversed_pair = selected
            joins.append(cls._relationship_to_join(relationship, left_alias, right_alias, reversed_pair))
            added.add(right_alias)
        return joins

    @staticmethod
    def _select_next_relationship(
        added: set[str],
        required_nodes: set[str],
        relationships: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str, str, bool] | None:
        for relationship in relationships:
            left = _relationship_alias(relationship, "left")
            right = _relationship_alias(relationship, "right")
            if left in added and right in required_nodes and right not in added:
                return relationship, left, right, False
            if right in added and left in required_nodes and left not in added:
                return relationship, right, left, True
        return None

    @staticmethod
    def _relationship_condition(
        relationship: dict[str, Any],
        left_alias: str,
        reversed_pair: bool,
        left_column: Any,
        right_column: Any,
    ) -> dict[str, Any]:
        """单条关联条件；reversed_pair 时左右列互换。"""
        return {
            "leftTable": relationship.get("right_table") if reversed_pair else relationship.get("left_table"),
            "leftAlias": left_alias,
            "leftColumn": right_column if reversed_pair else left_column,
            "rightColumn": left_column if reversed_pair else right_column,
        }

    @classmethod
    def _relationship_join_conditions(
        cls,
        relationship: dict[str, Any],
        left_alias: str,
        right_alias: str,
        reversed_pair: bool,
    ) -> list[dict[str, Any]]:
        """收集 join_conditions；为空时回退关系级单条件。"""
        conditions = []
        for condition in relationship.get("join_conditions") or []:
            left_column = (condition or {}).get("left_column")
            right_column = (condition or {}).get("right_column")
            if not left_column or not right_column:
                continue
            conditions.append(
                cls._relationship_condition(relationship, left_alias, reversed_pair, left_column, right_column)
            )
        if not conditions and relationship.get("left_column") and relationship.get("right_column"):
            conditions.append(
                cls._relationship_condition(
                    relationship,
                    left_alias,
                    reversed_pair,
                    relationship.get("left_column"),
                    relationship.get("right_column"),
                )
            )
        return conditions

    @classmethod
    def _relationship_to_join(
        cls,
        relationship: dict[str, Any],
        left_alias: str,
        right_alias: str,
        reversed_pair: bool,
    ) -> dict[str, Any]:
        conditions = cls._relationship_join_conditions(relationship, left_alias, right_alias, reversed_pair)
        if not conditions:
            raise ValueError(f"语义关系 {left_alias} -> {right_alias} 缺少有效关联条件")

        return {
            "id": f"{left_alias}-{right_alias}",
            "leftTable": relationship.get("right_table") if reversed_pair else relationship.get("left_table"),
            "leftAlias": left_alias,
            "rightTable": relationship.get("left_table") if reversed_pair else relationship.get("right_table"),
            "rightAlias": right_alias,
            "joinType": str(relationship.get("join_type", "left")).upper(),
            "joinConditions": conditions,
            "leftColumn": conditions[0]["leftColumn"],
            "rightColumn": conditions[0]["rightColumn"],
        }
