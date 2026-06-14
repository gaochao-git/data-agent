"""
语义可视化查询编译服务。

该服务接收前端手工选择的语义字段 DSL，不接受前端传入的 mainTable/joins。
表关系统一从后端语义配置 relationships 推导，底层 SQL 仍复用 builder compiler。
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from utils.logger import get_logger

from ..semantic.compiler import SemanticSQLGenerator, compile_builder_sql
from ..semantic.validators import InputValidator
from .datasource_manager import get_datasource_manager

logger = get_logger(__name__)

FORBIDDEN_SEMANTIC_BUILDER_KEYS = {"mainTable", "mainAlias", "joins"}


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("语义可视化 DSL 字段必须是数组")
    return [item for item in value if isinstance(item, dict)]


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


def _parse_aggregate_expression(generator: SemanticSQLGenerator, expression: str) -> Tuple[str, str] | None:
    parsed = generator._parse_aggregate_expression(expression)
    if not parsed:
        return None
    func, column_expr = parsed
    column = "*" if column_expr == "*" else column_expr.split(".")[-1].replace("`", "").strip()
    return func, column


class SemanticBuilderCompiler:
    """将语义可视化 DSL 编译为 SQL。"""

    def __init__(self, semantic_config: Dict[str, Any]):
        self.semantic_config = semantic_config or {}
        self.generator = SemanticSQLGenerator(self.semantic_config)
        self.validator = InputValidator(self.generator.metrics_map, self.generator.dimensions_map)

    def compile(self, semantic_builder_config: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(semantic_builder_config, dict):
            raise ValueError("语义可视化 DSL 格式错误")
        forbidden = FORBIDDEN_SEMANTIC_BUILDER_KEYS.intersection(semantic_builder_config.keys())
        if forbidden:
            raise ValueError(f"语义可视化 DSL 不允许包含前端表关系字段: {', '.join(sorted(forbidden))}")

        dimensions = [self._map_dimension(item) for item in _as_list(semantic_builder_config.get("dimensions"))]
        aggregations = [self._map_aggregation(item) for item in _as_list(semantic_builder_config.get("aggregations"))]
        filters = self._map_filter_nodes(semantic_builder_config.get("filters") or [])
        order_by = [self._map_order_by(item, aggregations, dimensions) for item in _as_list(semantic_builder_config.get("orderBy"))]
        limit = self._validate_limit(semantic_builder_config.get("limit"))
        offset = self._validate_offset(semantic_builder_config.get("offset"))

        if not dimensions and not aggregations:
            raise ValueError("语义可视化查询至少需要一个维度或指标")

        main_table, main_alias = self._resolve_main_table(dimensions, aggregations, filters, order_by)
        tables_needed = self._collect_tables_needed(main_alias, dimensions, aggregations, filters, order_by)
        builder_config = {
            "mainTable": main_table,
            "mainAlias": main_alias,
            "joins": self.generator._build_builder_joins(main_alias, tables_needed),
            "dimensions": dimensions,
            "aggregations": aggregations,
            "filters": filters,
            "orderBy": order_by,
            "limit": limit,
            "offset": offset,
        }
        sql = compile_builder_sql(builder_config, dialect=self.semantic_config.get("source_type", "mysql"))
        return {"sql": sql, "builder_config": builder_config}

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
            if normalized and normalized in self.generator.dimensions_map:
                return self.generator.dimensions_map[normalized]

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
            if normalized and normalized in self.generator.metrics_map:
                return self.generator.metrics_map[normalized]

        item_func = _normalize_text(item.get("func") or "RAW").upper()
        item_keys = _field_keys(item.get("table"), _normalize_alias(item), item.get("column"))
        item_expression = _normalize_text(item.get("expression"))
        matched_by_field: List[Dict[str, Any]] = []
        for metric in self.semantic_config.get("metrics") or []:
            metric_expression = _normalize_text(metric.get("expression"))
            parsed = _parse_aggregate_expression(self.generator, metric_expression)
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
            parsed = _parse_aggregate_expression(self.generator, _normalize_text(metric.get("expression")))
            func = parsed[0] if parsed else _normalize_text(item.get("func") or "RAW").upper()
            column = parsed[1] if parsed else (metric.get("column") or item.get("column") or "*")
            table = metric.get("table") or item.get("table")
            table_alias = _semantic_alias(metric) or table
            expression = metric.get("expression") or item.get("expression")
            display_name = metric.get("name") or item.get("alias") or item.get("displayName") or column
        else:
            dim = self._resolve_dimension_config(item)
            func = _normalize_text(item.get("func") or "RAW").upper()
            if func != "RAW":
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
        return [self._map_filter_node(item) for item in value if isinstance(item, dict)]

    def _map_filter_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        if "and" in node:
            children = node.get("and") or []
            if not isinstance(children, list):
                raise ValueError("语义可视化 and 过滤节点必须是数组")
            return {"and": [self._map_filter_node(child) for child in children if isinstance(child, dict)]}
        if "or" in node:
            children = node.get("or") or []
            if not isinstance(children, list):
                raise ValueError("语义可视化 or 过滤节点必须是数组")
            return {"or": [self._map_filter_node(child) for child in children if isinstance(child, dict)]}
        if node.get("aggFunc"):
            return self._map_having_filter(node)
        return self._map_dimension_filter(node)

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

    def _map_dimension_filter(self, node: Dict[str, Any]) -> Dict[str, Any]:
        dim = self._resolve_dimension_config(node)
        mapped = {
            "id": node.get("id") or f"filter-{dim.get('name') or dim.get('column')}",
            "table": dim.get("table"),
            "tableAlias": _semantic_alias(dim) or dim.get("table"),
            "column": dim.get("column"),
            "expression": dim.get("expression") or node.get("expression"),
            "operator": node.get("operator", "="),
            "logic": node.get("logic", "AND"),
        }
        self._copy_filter_value_fields(node, mapped)
        return mapped

    def _map_having_filter(self, node: Dict[str, Any]) -> Dict[str, Any]:
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
            "id": node.get("id") or f"having-{agg.get('alias')}",
            "table": agg.get("table"),
            "tableAlias": agg.get("tableAlias"),
            "column": agg.get("column"),
            "operator": node.get("operator", "="),
            "logic": node.get("logic", "AND"),
            "aggFunc": agg.get("func"),
            "aggId": agg.get("id"),
        }
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
                    SemanticBuilderCompiler._collect_filter_tables(child, tables_needed)
            return
        if "or" in node:
            for child in node.get("or") or []:
                if isinstance(child, dict):
                    SemanticBuilderCompiler._collect_filter_tables(child, tables_needed)
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


async def load_runtime_semantic_config(datasource_id: str, semantic_config_id: str) -> Dict[str, Any]:
    normalized_datasource_id = _normalize_text(datasource_id)
    normalized_config_id = _normalize_text(semantic_config_id)
    if not normalized_datasource_id or not normalized_config_id:
        raise ValueError("缺少 datasource_id 或 semantic_config_id")

    ds_manager = get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(normalized_datasource_id)
    if not datasource:
        raise LookupError(f"数据源不存在: {normalized_datasource_id}")
    if hasattr(ds_manager, "get_runtime_semantic_config_async"):
        config = await ds_manager.get_runtime_semantic_config_async(normalized_datasource_id, normalized_config_id)
    else:
        config = await ds_manager.get_semantic_config_async(normalized_datasource_id, normalized_config_id)
    if not config:
        raise LookupError(f"语义配置不存在: {normalized_config_id}")
    if not config.get("enabled", True):
        raise ValueError(f"语义配置未启用: {normalized_config_id}")

    result = dict(config)
    if result.get("relationships") and not result.get("joins"):
        result["joins"] = result.get("relationships")
    if datasource.get("source_type"):
        result["source_type"] = datasource.get("source_type")
    if not result.get("id"):
        result["id"] = normalized_config_id
    return result


async def compile_semantic_builder_sql(
    datasource_id: str,
    semantic_config_id: str,
    semantic_builder_config: Dict[str, Any],
) -> Dict[str, Any]:
    semantic_config = await load_runtime_semantic_config(datasource_id, semantic_config_id)
    return SemanticBuilderCompiler(semantic_config).compile(semantic_builder_config)
