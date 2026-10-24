"""对象实例只读投影：按对象类型映射查询数据库并组装对象契约。

当前阶段只做「数据库投影」，不建对象存储、不做写入：
- 对象类型仍是逻辑定义，事实来源仍是物理表/语义模型；
- 本模块只负责按对象主键查一行、按映射转成 object instance；
- 后续对象存储/图数据库只替换投影实现，对象契约保持不变。

对齐 Palantir Link Type 文档（object-link-types/link-types-overview、create-link-type）：
- Link Type 双向，单条关系类型同时支持 from/to 两方向；
- 当前只实现 foreign key → primary key 型 link（一跳，双向遍历）；
- Link 由关系两侧属性值匹配产生，查询时投影，不落 link 表；
- one-to-many / many-to-one 用方向表达，many-to-many / join table / backing object 暂不支持。

MVP 约束：
- 当前对象单映射配置、单标识属性；
- 属性必须同表；关联只支持单列外键/主键；
- 一跳 links，单关系最多返回 20 条；跨模型关系按对端对象自己的映射查询。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import date, timedelta
from typing import Any

from pymysql.converters import escape_string

from utils.logger import get_logger

from ...daos.ontology_action_execution_dao import OntologyActionExecutionDao
from ..datasource.manager import get_datasource_manager
from ..datasource.target_context import get_execution_target_member_id, get_execution_target_name
from ..query import QueryOrchestrator, QuerySource, QuerySpec
from . import ontology_service

logger = get_logger(__name__)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MAX_KEY_LENGTH = 512
_MAX_LINKS_PER_RELATION = 20
# 对象查询包含派生属性 JOIN；Hive 上 4 个派生子查询可能超过 10s，给独立超时窗口。
_OBJECT_QUERY_TIMEOUT_SECONDS = 30
_TRUTHY = {"1", "true", "yes", "on"}


def ontology_object_read_enabled() -> bool:
    """本体对象实例读取开关（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED") or "").strip().lower() in _TRUTHY


def ontology_action_execute_enabled() -> bool:
    """本体 Action 执行/对象写覆盖层开关（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED") or "").strip().lower() in _TRUTHY


_MAX_RELATIONS = 20


class ObjectProjectionError(ValueError):
    """对象投影入参/配置不可用。"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _derived_property_names(detail: dict[str, Any]) -> set[str]:
    """对象类型中派生属性名集合（查询时计算、无物理列，不能参与过滤/排序/聚合）。"""
    return {
        _text(prop.get("property_name"))
        for prop in detail.get("properties") or []
        if isinstance(prop, dict) and isinstance(prop.get("derivation"), dict) and prop["derivation"]
    }


def _reject_derived_property_operations(detail: dict[str, Any], property_names: list[Any], operation: str) -> None:
    """派生属性尚无批量计算能力：过滤/排序/聚合等入口给出明确错误，避免落到「未映射属性」。"""
    derived = _derived_property_names(detail)
    hits = sorted({_text(name) for name in property_names if _text(name) in derived})
    if hits:
        raise ObjectProjectionError(f"派生属性不支持{operation}: {', '.join(hits)}")


def _quote_identifier(value: str) -> str:
    """校验并引用表/列标识符；不允许任意 SQL 片段。"""
    parts = value.split(".")
    if not parts or any(not _IDENTIFIER_PATTERN.fullmatch(part) for part in parts):
        raise ObjectProjectionError(f"非法表/列标识符: {value!r}")
    return ".".join(f"`{part}`" for part in parts)


def _mappings(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return [mapping for mapping in detail.get("mappings") or [] if isinstance(mapping, dict)]


def _mapping_for_projection(detail: dict[str, Any], semantic_config_id: str | None) -> dict[str, Any]:
    mappings = _mappings(detail)
    if not mappings:
        raise ObjectProjectionError("对象类型未绑定语义模型，无法投影对象")
    if semantic_config_id:
        selected = next((m for m in mappings if _text(m.get("semantic_config_id")) == semantic_config_id), None)
        if selected is None:
            raise ObjectProjectionError(f"对象类型未绑定语义模型: {semantic_config_id}")
        return selected
    if len(mappings) > 1:
        raise ObjectProjectionError("对象类型绑定多个语义模型，请指定 semantic_config_id 后再投影")
    return mappings[0]


def _mapping_for_property(
    detail: dict[str, Any],
    property_name: str,
    semantic_config_id: str | None = None,
) -> dict[str, Any]:
    """选择包含指定属性的映射；关系对端查询用。"""
    mappings = _mappings(detail)
    if semantic_config_id:
        mappings = [m for m in mappings if _text(m.get("semantic_config_id")) == semantic_config_id]
    for mapping in mappings:
        column_mappings = mapping.get("column_mappings")
        if isinstance(column_mappings, dict) and _text(column_mappings.get(property_name)):
            return mapping
    raise ObjectProjectionError(f"关系属性未映射到语义模型: {property_name}")


def _dimension_index(config: dict[str, Any]) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for dimension in config.get("dimensions") or []:
        dimension = dimension or {}
        name = _text(dimension.get("name"))
        if name:
            index[name] = {
                "table": _text(dimension.get("table")),
                "column": _text(dimension.get("column")),
            }
    return index


def _projection_columns(
    properties: list[dict[str, Any]],
    column_mappings: dict[str, Any],
    dimensions: dict[str, dict[str, str]],
) -> tuple[str, list[tuple[str, str]]]:
    """返回 (表名, [(属性名, 物理列名)])；MVP 要求全部属性同表。"""
    table = ""
    columns: list[tuple[str, str]] = []
    for prop in properties:
        property_name = _text(prop.get("property_name"))
        dimension_name = _text(column_mappings.get(property_name))
        if not property_name or not dimension_name:
            continue
        dimension = dimensions.get(dimension_name)
        if not dimension or not dimension["table"] or not dimension["column"]:
            raise ObjectProjectionError(f"属性 {property_name} 的映射维度不存在或缺少表/列: {dimension_name}")
        if table and dimension["table"] != table:
            raise ObjectProjectionError("MVP 暂不支持跨表对象投影：映射属性必须来自同一张表")
        table = dimension["table"]
        columns.append((property_name, dimension["column"]))
    if not table or not columns:
        raise ObjectProjectionError("对象类型没有可投影的属性映射")
    return table, columns


def _identifier_property(
    properties: list[dict[str, Any]],
    column_mappings: dict[str, Any],
) -> tuple[str, str]:
    identifiers = [prop for prop in properties if bool(prop.get("is_identifier"))]
    if len(identifiers) != 1:
        raise ObjectProjectionError("对象投影 MVP 只支持单个标识属性；复合主键待后续版本")
    property_name = _text(identifiers[0].get("property_name"))
    dimension_name = _text(column_mappings.get(property_name))
    if not property_name or not dimension_name:
        raise ObjectProjectionError(f"标识属性未映射: {property_name or '?'}")
    return property_name, dimension_name


def _build_select_sql(
    table: str,
    columns: list[tuple[str, str]],
    identifier_column: str,
    object_key: str,
) -> str:
    select_list = ", ".join(f"{_quote_identifier(column)} AS `{property_name}`" for property_name, column in columns)
    return (
        f"SELECT {select_list} FROM {_quote_identifier(table)} "
        f"WHERE {_quote_identifier(identifier_column)} = '{escape_string(object_key)}' LIMIT 1"
    )


def build_object_query(
    detail: dict[str, Any],
    config: dict[str, Any],
    *,
    object_key: str,
    semantic_config_id: str | None = None,
) -> dict[str, Any]:
    """构造对象投影查询（纯函数，供 service 与单测复用）。"""
    key = _text(object_key)
    if not key or len(key) > _MAX_KEY_LENGTH:
        raise ObjectProjectionError("object_key 不能为空且长度不能超过 512")
    mapping = _mapping_for_projection(detail, semantic_config_id)
    dimensions = _dimension_index(config)
    column_mappings = mapping.get("column_mappings")
    if not isinstance(column_mappings, dict):
        raise ObjectProjectionError("对象映射缺少 column_mappings")
    properties = [prop for prop in detail.get("properties") or [] if isinstance(prop, dict)]
    table, columns = _projection_columns(properties, column_mappings, dimensions)
    identifier_property, identifier_dimension = _identifier_property(properties, column_mappings)
    identifier_dimension_meta = dimensions.get(identifier_dimension)
    if not identifier_dimension_meta or identifier_dimension_meta["table"] != table:
        raise ObjectProjectionError("标识属性与普通属性不在同一张表，MVP 暂不支持")
    sql = _build_select_sql(table, columns, identifier_dimension_meta["column"], key)
    return {
        "sql": sql,
        "table": table,
        "object_type": _text(detail.get("object_type_name")),
        "object_key": key,
        "identifier_property": identifier_property,
        "properties": [name for name, _column in columns],
    }


def _title_property_name(properties: list[dict[str, Any]], identifier_property: str) -> str:
    """列表搜索优先用标题键；未配置时退回主键。"""
    for prop in properties:
        if bool(prop.get("is_title")):
            return _text(prop.get("property_name")) or identifier_property
    return identifier_property


def build_object_list_query(
    detail: dict[str, Any],
    config: dict[str, Any],
    *,
    keyword: str = "",
    limit: int = 20,
    offset: int = 0,
    semantic_config_id: str | None = None,
) -> dict[str, Any]:
    """构造对象列表查询（MVP：单映射/单表，标题键 + 主键关键词搜索，limit+1 判 has_more）。"""
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    mapping = _mapping_for_projection(detail, semantic_config_id)
    dimensions = _dimension_index(config)
    column_mappings = mapping.get("column_mappings")
    if not isinstance(column_mappings, dict):
        raise ObjectProjectionError("对象映射缺少 column_mappings")
    properties = [prop for prop in detail.get("properties") or [] if isinstance(prop, dict)]
    table, columns = _projection_columns(properties, column_mappings, dimensions)
    identifier_property, identifier_dimension = _identifier_property(properties, column_mappings)
    identifier_meta = dimensions.get(identifier_dimension)
    if not identifier_meta or identifier_meta["table"] != table:
        raise ObjectProjectionError("标识属性与普通属性不在同一张表，MVP 暂不支持")

    search_property = _title_property_name(properties, identifier_property)
    search_dimension = dimensions.get(_text(column_mappings.get(search_property)))
    search_column = search_dimension["column"] if search_dimension else identifier_meta["column"]
    select_list = ", ".join(f"{_quote_identifier(column)} AS `{property_name}`" for property_name, column in columns)
    where = ""
    value = _text(keyword)
    if value:
        if len(value) > 200:
            raise ObjectProjectionError("搜索关键词不能超过 200 字符")
        escaped = escape_string(value)
        search_columns = {_quote_identifier(search_column), _quote_identifier(identifier_meta["column"])}
        where = " WHERE " + " OR ".join(f"{column} LIKE '%{escaped}%'" for column in sorted(search_columns))
    sql = (
        f"SELECT {select_list} FROM {_quote_identifier(table)}{where} "
        f"ORDER BY {_quote_identifier(identifier_meta['column'])} "
        f"LIMIT {safe_limit + 1} OFFSET {safe_offset}"
    )
    return {
        "sql": sql,
        "table": table,
        "object_type": _text(detail.get("object_type_name")),
        "identifier_property": identifier_property,
        "title_property": search_property,
        "properties": [name for name, _column in columns],
        "limit": safe_limit,
        "offset": safe_offset,
    }


_FILTER_OPERATORS = {"eq", "ne", "contains", "gt", "gte", "lt", "lte", "in"}
_MAX_FILTERS = 10
_MAX_SORTS = 5


_FILTER_SQL_SYMBOLS = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


def _keyword_condition(search_column: str, identifier_column: str, keyword: str) -> str:
    keyword_value = _text(keyword)
    if not keyword_value:
        return ""
    if len(keyword_value) > 200:
        raise ObjectProjectionError("搜索关键词不能超过 200 字符")
    escaped = escape_string(keyword_value)
    search_columns = {_quote_identifier(search_column), _quote_identifier(identifier_column)}
    return "(" + " OR ".join(f"{column} LIKE '%{escaped}%'" for column in sorted(search_columns)) + ")"


def _in_condition(column: str, item: dict[str, Any]) -> str:
    values = [_text(entry) for entry in (item.get("values") or []) if _text(entry)]
    if not values:
        return "1 = 0"
    escaped_values = ", ".join(f"'{escape_string(entry)}'" for entry in values[:100])
    return f"{column} IN ({escaped_values})"


def _filter_condition(column: str, operator: str, item: dict[str, Any], value: str) -> str:
    if operator == "in":
        return _in_condition(column, item)
    if operator == "contains":
        return f"{column} LIKE '%{escape_string(value)}%'"
    return f"{column} {_FILTER_SQL_SYMBOLS[operator]} '{escape_string(value)}'"


def _search_property_sql(
    property_name: str,
    column_by_property: dict[str, str],
    derived_expressions: dict[str, str] | None,
    main_table: str = "",
) -> str:
    """属性 → 物理列或派生 JOIN 表达式；派生属性无物理列。"""
    if property_name in column_by_property:
        column = _quote_identifier(column_by_property[property_name])
        return f"{_quote_identifier(main_table)}.{column}" if main_table else column
    derived = (derived_expressions or {}).get(property_name)
    if derived:
        return derived
    raise ObjectProjectionError(f"属性未映射: {property_name or '?'}")


_WHERE_GROUP_OPERATORS = {"and", "or", "not"}
_WHERE_LEAF_OPERATORS = {"eq", "ne", "contains", "gt", "gte", "lt", "lte", "in", "is_null", "is_not_null"}
_MAX_WHERE_DEPTH = 3
_MAX_WHERE_NODES = 50


def _where_condition(
    where: dict[str, Any] | None,
    column_by_property: dict[str, str],
    derived_expressions: dict[str, str] | None,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
    main_table: str = "",
) -> str:
    """编译官方风格 where 树（and/or/not + 比较/包含/isNull），最多 3 层。"""
    if where is None:
        return ""
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if counter[0] > _MAX_WHERE_NODES:
        raise ObjectProjectionError(f"where 条件节点不能超过 {_MAX_WHERE_NODES} 个")
    if depth > _MAX_WHERE_DEPTH:
        raise ObjectProjectionError(f"where 条件嵌套不能超过 {_MAX_WHERE_DEPTH} 层")
    operator = _text(where.get("operator")).lower()
    if operator in _WHERE_GROUP_OPERATORS:
        return _where_group_condition(
            operator,
            where,
            column_by_property,
            derived_expressions,
            depth=depth,
            counter=counter,
            main_table=main_table,
        )
    return _where_leaf_condition(operator, where, column_by_property, derived_expressions, main_table=main_table)


def _where_group_condition(
    operator: str,
    where: dict[str, Any],
    column_by_property: dict[str, str],
    derived_expressions: dict[str, str] | None,
    *,
    depth: int,
    counter: list[int],
    main_table: str = "",
) -> str:
    children = [child for child in where.get("children") or [] if isinstance(child, dict)]
    if operator == "not" and len(children) != 1:
        raise ObjectProjectionError("where.not 必须且只能有一个子条件")
    if operator != "not" and not children:
        raise ObjectProjectionError(f"where.{operator} 至少需要一个子条件")
    parts = [
        _where_condition(child, column_by_property, derived_expressions, depth=depth + 1, counter=counter)
        for child in children
    ]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    if operator == "not":
        return f"NOT ({parts[0]})"
    return "(" + f" {operator.upper()} ".join(f"({part})" for part in parts) + ")"


def _where_leaf_condition(
    operator: str,
    where: dict[str, Any],
    column_by_property: dict[str, str],
    derived_expressions: dict[str, str] | None,
    main_table: str = "",
) -> str:
    if operator not in _WHERE_LEAF_OPERATORS:
        raise ObjectProjectionError(f"不支持的 where 操作符: {operator or '?'}")
    column = _search_property_sql(_text(where.get("property")), column_by_property, derived_expressions, main_table)
    if operator in {"is_null", "is_not_null"}:
        return f"{column} IS {'NOT ' if operator == 'is_not_null' else ''}NULL"
    return _filter_condition(column, operator, where, _text(where.get("value")))


def _object_search_conditions(
    column_by_property: dict[str, str],
    *,
    keyword: str,
    search_column: str,
    identifier_column: str,
    filters: list[dict[str, Any]],
    derived_expressions: dict[str, str] | None = None,
    where: dict[str, Any] | None = None,
    main_table: str = "",
) -> str:
    """通用对象过滤条件；只接受已映射属性，不拼接任意 SQL。"""
    conditions: list[str] = []
    keyword_condition = _keyword_condition(search_column, identifier_column, keyword)
    if keyword_condition:
        conditions.append(keyword_condition)
    where_condition = _where_condition(where, column_by_property, derived_expressions, main_table=main_table)
    if where_condition:
        conditions.append(where_condition)
    for item in filters[:_MAX_FILTERS]:
        property_name = _text(item.get("property"))
        operator = _text(item.get("operator")).lower()
        if operator not in _FILTER_OPERATORS:
            raise ObjectProjectionError(f"不支持的过滤操作符: {operator or '?'}")
        column = _search_property_sql(property_name, column_by_property, derived_expressions, main_table)
        conditions.append(_filter_condition(column, operator, item, _text(item.get("value"))))
    return " AND ".join(conditions)


def _object_search_order_by(
    column_by_property: dict[str, str],
    sorts: list[dict[str, Any]],
    identifier_column: str,
    derived_expressions: dict[str, str] | None = None,
    main_table: str = "",
) -> str:
    order_parts: list[str] = []
    for item in sorts[:_MAX_SORTS]:
        property_name = _text(item.get("property"))
        column = _search_property_sql(property_name, column_by_property, derived_expressions, main_table)
        direction = "DESC" if _text(item.get("direction")).lower() == "desc" else "ASC"
        order_parts.append(f"{column} {direction}")
    if not order_parts:
        order_parts.append(f"{_quote_identifier(identifier_column)} ASC")
    return ", ".join(order_parts)


def build_object_search_query(
    detail: dict[str, Any],
    config: dict[str, Any],
    *,
    keyword: str = "",
    filters: list[dict[str, Any]] | None = None,
    sorts: list[dict[str, Any]] | None = None,
    limit: int = 20,
    offset: int = 0,
    semantic_config_id: str | None = None,
    where: dict[str, Any] | None = None,
    derived_joins: list[str] | None = None,
    derived_expressions: dict[str, str] | None = None,
    derived_select: dict[str, str] | None = None,
    count_joins: list[str] | None = None,
) -> dict[str, Any]:
    """构造通用对象查询：关键词 + 属性过滤 + 排序 + 分页，并附带 count SQL。"""
    safe_limit = max(1, min(int(limit), 100))
    safe_offset = max(0, int(offset))
    mapping = _mapping_for_projection(detail, semantic_config_id)
    dimensions = _dimension_index(config)
    column_mappings = mapping.get("column_mappings")
    if not isinstance(column_mappings, dict):
        raise ObjectProjectionError("对象映射缺少 column_mappings")
    properties = [prop for prop in detail.get("properties") or [] if isinstance(prop, dict)]
    table, columns = _projection_columns(properties, column_mappings, dimensions)
    identifier_property, identifier_dimension = _identifier_property(properties, column_mappings)
    identifier_meta = dimensions.get(identifier_dimension)
    if not identifier_meta or identifier_meta["table"] != table:
        raise ObjectProjectionError("标识属性与普通属性不在同一张表，MVP 暂不支持")
    column_by_property = dict(columns)
    search_property = _title_property_name(properties, identifier_property)
    search_dimension = dimensions.get(_text(column_mappings.get(search_property)))
    search_column = search_dimension["column"] if search_dimension else identifier_meta["column"]
    where_sql_text = _object_search_conditions(
        column_by_property,
        keyword=keyword,
        search_column=search_column,
        identifier_column=identifier_meta["column"],
        filters=filters or [],
        derived_expressions=derived_expressions,
        where=where,
        main_table=table,
    )
    where_sql = f" WHERE {where_sql_text}" if where_sql_text else ""
    order_sql = _object_search_order_by(
        column_by_property, sorts or [], identifier_meta["column"], derived_expressions, main_table=table
    )
    select_parts = [
        f"{_quote_identifier(table)}.{_quote_identifier(column)} AS `{property_name}`"
        for property_name, column in columns
    ]
    select_parts.extend(f"{expression} AS `{name}`" for name, expression in (derived_select or {}).items())
    select_list = ", ".join(select_parts)
    from_sql = f"{_quote_identifier(table)}{''.join(derived_joins or [])}"
    sql = (
        f"SELECT {select_list} FROM {from_sql}{where_sql} "
        f"ORDER BY {order_sql} LIMIT {safe_limit + 1} OFFSET {safe_offset}"
    )
    count_from_sql = f"{_quote_identifier(table)}{''.join(count_joins or [])}"
    count_sql = f"SELECT COUNT(*) AS `total` FROM {count_from_sql}{where_sql}"
    return {
        "sql": sql,
        "count_sql": count_sql,
        "table": table,
        "object_type": _text(detail.get("object_type_name")),
        "identifier_property": identifier_property,
        "title_property": search_property,
        "properties": [name for name, _column in columns] + list((derived_select or {}).keys()),
        "limit": safe_limit,
        "offset": safe_offset,
    }


_AGGREGATE_OPERATIONS = {"count", "count_distinct", "sum", "avg", "min", "max"}
_AGGREGATE_NUMERIC_OPERATIONS = {"sum", "avg"}
_MAX_AGGREGATE_METRICS = 5
_MAX_AGGREGATE_GROUP_BY = 5
_MAX_AGGREGATE_LIMIT = 200


def _aggregate_metric(
    metric: dict[str, Any],
    column_by_property: dict[str, str],
    value_types: dict[str, str],
    derived_expressions: dict[str, str] | None = None,
) -> tuple[str, str]:
    """校验单个聚合指标并返回 (SQL 表达式, 输出字段名)。"""
    operation = _text(metric.get("operation")).lower()
    if operation not in _AGGREGATE_OPERATIONS:
        raise ObjectProjectionError(f"不支持的聚合操作: {operation or '?'}")
    if operation == "count":
        return "COUNT(*) AS `count`", "count"
    property_name = _text(metric.get("property"))
    derived_expression = (derived_expressions or {}).get(property_name)
    if property_name not in column_by_property and not derived_expression:
        raise ObjectProjectionError(f"聚合属性未映射: {property_name or '?'}")
    if (
        not derived_expression
        and operation in _AGGREGATE_NUMERIC_OPERATIONS
        and _text(value_types.get(property_name)) not in {"Integer", "Decimal"}
    ):
        raise ObjectProjectionError(f"聚合操作 {operation} 仅支持数值属性: {property_name}")
    column = derived_expression or _quote_identifier(column_by_property[property_name])
    alias = f"{operation}_{property_name}"
    if operation == "count_distinct":
        return f"COUNT(DISTINCT {column}) AS `{alias}`", alias
    return f"{operation.upper()}({column}) AS `{alias}`", alias


def _aggregate_projection_context(
    detail: dict[str, Any],
    config: dict[str, Any],
    semantic_config_id: str | None,
) -> dict[str, Any]:
    """聚合所需的映射上下文：表、属性列、类型、标识属性与搜索列。"""
    mapping = _mapping_for_projection(detail, semantic_config_id)
    dimensions = _dimension_index(config)
    column_mappings = mapping.get("column_mappings")
    if not isinstance(column_mappings, dict):
        raise ObjectProjectionError("对象映射缺少 column_mappings")
    properties = [prop for prop in detail.get("properties") or [] if isinstance(prop, dict)]
    table, columns = _projection_columns(properties, column_mappings, dimensions)
    identifier_property, identifier_dimension = _identifier_property(properties, column_mappings)
    identifier_meta = dimensions.get(identifier_dimension)
    if not identifier_meta or identifier_meta["table"] != table:
        raise ObjectProjectionError("标识属性与普通属性不在同一张表，MVP 暂不支持")
    search_property = _title_property_name(properties, identifier_property)
    search_dimension = dimensions.get(_text(column_mappings.get(search_property)))
    return {
        "table": table,
        "column_by_property": dict(columns),
        "value_types": {_text(prop.get("property_name")): _text(prop.get("value_type")) for prop in properties},
        "identifier_property": identifier_property,
        "search_column": search_dimension["column"] if search_dimension else identifier_meta["column"],
    }


def _aggregate_group_properties(group_by: list[str] | None, column_by_property: dict[str, str]) -> list[str]:
    names: list[str] = []
    for raw_name in (group_by or [])[:_MAX_AGGREGATE_GROUP_BY]:
        property_name = _text(raw_name)
        if property_name not in column_by_property:
            raise ObjectProjectionError(f"分组属性未映射: {property_name or '?'}")
        names.append(property_name)
    return names


def _aggregate_select_parts(
    group_properties: list[str],
    metric_items: list[dict[str, Any]],
    column_by_property: dict[str, str],
    value_types: dict[str, str],
    derived_expressions: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    if not metric_items:
        raise ObjectProjectionError("至少需要一个聚合指标")
    select_parts = [f"{_quote_identifier(column_by_property[name])} AS `{name}`" for name in group_properties]
    metric_keys: list[str] = []
    for item in metric_items:
        expression, key = _aggregate_metric(item, column_by_property, value_types, derived_expressions)
        select_parts.append(expression)
        metric_keys.append(key)
    return select_parts, metric_keys


def _aggregate_group_sql(group_properties: list[str], column_by_property: dict[str, str]) -> tuple[str, str]:
    if not group_properties:
        return "", ""
    group_columns = ", ".join(_quote_identifier(column_by_property[name]) for name in group_properties)
    return f" GROUP BY {group_columns}", f" ORDER BY {group_columns}"


def build_object_aggregate_query(
    detail: dict[str, Any],
    config: dict[str, Any],
    *,
    group_by: list[str] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    filters: list[dict[str, Any]] | None = None,
    keyword: str = "",
    limit: int = 100,
    semantic_config_id: str | None = None,
    where: dict[str, Any] | None = None,
    derived_joins: list[str] | None = None,
    derived_expressions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造对象聚合查询：映射属性 group by + 白名单指标聚合，不拼接任意 SQL。"""
    _reject_derived_property_operations(
        detail,
        list(group_by or []) + [_text((item or {}).get("property")) for item in filters or []],
        "聚合分组/过滤",
    )
    safe_limit = max(1, min(int(limit), _MAX_AGGREGATE_LIMIT))
    context = _aggregate_projection_context(detail, config, semantic_config_id)
    column_by_property = context["column_by_property"]
    group_properties = _aggregate_group_properties(group_by, column_by_property)
    metric_items = (metrics or [])[:_MAX_AGGREGATE_METRICS]
    select_parts, metric_keys = _aggregate_select_parts(
        group_properties,
        metric_items,
        column_by_property,
        context["value_types"],
        derived_expressions,
    )
    where_sql_text = _object_search_conditions(
        column_by_property,
        keyword=keyword,
        search_column=context["search_column"],
        identifier_column=context["identifier_property"],
        filters=filters or [],
        where=where,
    )
    where_sql = f" WHERE {where_sql_text}" if where_sql_text else ""
    group_sql, order_sql = _aggregate_group_sql(group_properties, column_by_property)
    from_sql = f"{_quote_identifier(context['table'])}{''.join(derived_joins or [])}"
    sql = f"SELECT {', '.join(select_parts)} FROM {from_sql}{where_sql}{group_sql}{order_sql} LIMIT {safe_limit}"
    return {
        "sql": sql,
        "table": context["table"],
        "object_type": _text(detail.get("object_type_name")),
        "identifier_property": context["identifier_property"],
        "group_by": group_properties,
        "metric_keys": metric_keys,
        "limit": safe_limit,
    }


def build_related_key_query(
    detail: dict[str, Any],
    config: dict[str, Any],
    *,
    filter_property: str,
    filter_value: str,
) -> dict[str, Any]:
    """构造对端对象主键查询：WHERE 关系属性 = 当前对象键值，SELECT 对端标识属性。

    Palantir foreign-key link：一侧 foreign key 值匹配另一侧 primary key；
    本函数统一按“对端关系属性 → 对端标识属性”返回 link 目标。
    """
    _reject_derived_property_operations(detail, [filter_property], "关联查询")
    mapping = _mapping_for_property(detail, filter_property)
    dimensions = _dimension_index(config)
    column_mappings = mapping.get("column_mappings")
    if not isinstance(column_mappings, dict):
        raise ObjectProjectionError("对象映射缺少 column_mappings")
    properties = [prop for prop in detail.get("properties") or [] if isinstance(prop, dict)]
    identifier_property, identifier_dimension = _identifier_property(properties, column_mappings)
    filter_dimension_name = _text(column_mappings.get(filter_property))
    filter_dimension = dimensions.get(filter_dimension_name)
    identifier_dimension_meta = dimensions.get(identifier_dimension)
    if not filter_dimension or not identifier_dimension_meta:
        raise ObjectProjectionError("关系属性或标识属性缺少映射维度")
    if filter_dimension["table"] != identifier_dimension_meta["table"]:
        raise ObjectProjectionError("关系属性与标识属性不在同一张表，MVP 暂不支持")
    select_list = f"{_quote_identifier(identifier_dimension_meta['column'])} AS `related_key`"
    title_property = ""
    for prop in properties:
        if not bool(prop.get("is_title")):
            continue
        candidate_name = _text(prop.get("property_name"))
        candidate_dimension_name = _text(column_mappings.get(candidate_name))
        candidate_dimension = dimensions.get(candidate_dimension_name)
        if (
            not candidate_name
            or not candidate_dimension
            or candidate_dimension["table"] != identifier_dimension_meta["table"]
        ):
            continue
        select_list += f", {_quote_identifier(candidate_dimension['column'])} AS `related_title`"
        title_property = candidate_name
        break
    sql = (
        f"SELECT {select_list} "
        f"FROM {_quote_identifier(identifier_dimension_meta['table'])} "
        f"WHERE {_quote_identifier(filter_dimension['column'])} = '{escape_string(filter_value)}' "
        f"LIMIT {_MAX_LINKS_PER_RELATION}"
    )
    return {
        "sql": sql,
        "related_object_type": _text(detail.get("object_type_name")),
        "identifier_property": identifier_property,
        "filter_property": filter_property,
        "title_property": title_property,
    }


def _qualified_object_key(member_id: str, object_type: str, local_key: str) -> str:
    """对象身份：member + object_type + local_key；不同机构同本地键不是同一对象。"""
    member = _text(member_id)
    return f"{member}:{object_type}:{local_key}" if member else f"{object_type}:{local_key}"


def _parse_object_key(object_key: str, object_type: str) -> tuple[str, str]:
    """兼容查询结果回传的 [member:]object_type:local_key，返回(member, local_key)。"""
    parts = str(object_key or "").split(":")
    if len(parts) >= 3 and parts[1] == object_type:
        return parts[0], ":".join(parts[2:])
    if len(parts) >= 2 and parts[0] == object_type:
        return "", ":".join(parts[1:])
    return "", str(object_key or "")


def _assemble_object(
    detail: dict[str, Any],
    query: dict[str, Any],
    row: dict[str, Any],
    links: dict[str, list[dict[str, Any]]],
    member_scope: dict[str, str] | None = None,
    derived_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 派生属性值查询时合并进 properties（对齐 Palantir：derived 就是属性，不单独分组）
    properties = {name: row.get(name) for name in query["properties"]}
    properties.update(derived_values or {})
    object_type = query["object_type"]
    scope = member_scope or {}
    return {
        "object_type": object_type,
        "object_key": _qualified_object_key(scope.get("member_datasource_id", ""), object_type, query["object_key"]),
        "properties": properties,
        "links": links,
        **scope,
    }


def _select_dataset_binding(dataset: dict[str, Any], requested_ids: list[str] | None) -> dict[str, Any]:
    bindings = [item for item in dataset.get("bindings") or [] if isinstance(item, dict)]
    requested = [str(item).strip() for item in requested_ids or [] if str(item).strip()]
    if requested:
        bindings = [item for item in bindings if str(item.get("member_datasource_id") or "") in requested]
    if len(bindings) > 1:
        raise ObjectProjectionError("Dataset 有多个 member，请指定 member_datasource_ids")
    if not bindings:
        raise ObjectProjectionError("Dataset 没有可用的 member binding")
    return bindings[0]


def _dataset_dimensions(
    dataset: dict[str, Any], binding: dict[str, Any], table: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    columns = [item for item in dataset.get("columns") or [] if isinstance(item, dict)]
    column_by_name = {_text(item.get("name")): item for item in columns}
    raw_mappings = binding.get("column_mappings")
    column_mappings: dict[str, Any] = raw_mappings if isinstance(raw_mappings, dict) else {}
    dimensions: list[dict[str, Any]] = []
    mapping_columns: dict[str, str] = {}
    for property_name, logical_name in column_mappings.items():
        column = column_by_name.get(_text(logical_name))
        if column is None:
            continue
        physical = _text(column.get("physical_column")) or _text(column.get("name"))
        if not physical:
            continue
        dimensions.append({"name": logical_name, "table": table, "column": physical})
        mapping_columns[str(property_name)] = str(logical_name)
    if not dimensions:
        raise ObjectProjectionError("Dataset source binding 没有有效列映射")
    return dimensions, mapping_columns


async def _dataset_mapping_context(
    detail: dict[str, Any],
    member_datasource_ids: list[str] | None,
    *,
    user_id: str,
    username: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]:
    """Dataset source binding → 单一 member 的 physical table/column 查询上下文。"""
    from apps.chat_data_app.services.dataset.dataset_service import logical_dataset_service

    binding = detail.get("source_binding")
    if not isinstance(binding, dict):
        raise ObjectProjectionError("对象类型未配置 Dataset source binding")
    dataset = await logical_dataset_service.get_dataset_async_by_id_async(int(binding.get("dataset_id") or 0))
    if dataset is None:
        raise ObjectProjectionError(f"Dataset 不存在: {binding.get('dataset_id')}")
    selected = _select_dataset_binding(dataset, member_datasource_ids)
    group_id = _text(selected.get("datasource_group_id"))
    member_id = _text(selected.get("member_datasource_id"))
    if not group_id or not member_id:
        raise ObjectProjectionError("Dataset binding 缺少 datasource_group_id/member_datasource_id")
    targets = await get_datasource_manager().list_authorized_execution_targets_async(
        group_id, user_id=user_id or None, username=username or None
    )
    selected_targets = [
        target for target in targets if str(target.get("member_datasource_id") or target.get("id") or "") == member_id
    ]
    if not selected_targets:
        raise ObjectProjectionError(f"当前用户无权访问 Dataset member: {member_id}")
    table = _text(selected.get("physical_table"))
    dimensions, mapping_columns = _dataset_dimensions(dataset, binding, table)
    config = {"dimensions": dimensions}
    query_detail = {
        **detail,
        "mappings": [{"semantic_config_id": "dataset", "column_mappings": mapping_columns}],
    }
    return config, query_detail, selected_targets, group_id


async def _projection_context(
    detail: dict[str, Any],
    *,
    semantic_config_id: str | None,
    member_datasource_ids: list[str] | None,
    user_id: str,
    username: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str]:
    """返回 (查询详情, 配置, execution targets, group_id)；Dataset 优先，旧映射回退。"""
    if not isinstance(detail.get("source_binding"), dict):
        raise ObjectProjectionError("对象类型未配置 Dataset 数据源绑定")
    config, query_detail, targets, group_id = await _dataset_mapping_context(
        detail, member_datasource_ids, user_id=user_id, username=username
    )
    return query_detail, config, targets, group_id


async def _query_rows(
    targets: list[dict[str, Any]],
    sql: str,
    *,
    group_id: str,
    user_id: str,
    username: str,
    limit: int,
) -> list[dict[str, Any]]:
    spec = QuerySpec(
        source=QuerySource.ONTOLOGY,
        datasource_group_id=group_id,
        sql=sql,
        limit=limit,
        timeout=_OBJECT_QUERY_TIMEOUT_SECONDS,
        user_id=user_id or "",
        username=username or "",
        query_mode="ontology",
    )
    result = await QueryOrchestrator().execute_member_raw_async(spec, targets[0])
    if result.get("status") != "succeeded":
        raise ObjectProjectionError(f"对象查询失败: {result.get('error') or '未知错误'}")
    return [row for row in result.get("data") or [] if isinstance(row, dict)]


def _member_scope(targets: list[dict[str, Any]]) -> dict[str, str]:
    """对象投影当前执行的首个成员数据源（后续多机构查询再扩展）。"""
    target = targets[0] if targets else {}
    return {
        "member_datasource_id": get_execution_target_member_id(target),
        "member_name": get_execution_target_name(target, 0),
    }


def _relation_side(relation: dict[str, Any], current_object_type_id: int) -> dict[str, str] | None:
    """判定当前对象在关系类型中的一侧；自关联/多列关联 MVP 跳过。"""
    from_id = int(relation.get("from_object_type_id") or 0)
    to_id = int(relation.get("to_object_type_id") or 0)
    if from_id == to_id or current_object_type_id not in {from_id, to_id}:
        return None
    from_columns = [_text(column) for column in relation.get("from_columns") or [] if _text(column)]
    to_columns = [_text(column) for column in relation.get("to_columns") or [] if _text(column)]
    if len(from_columns) != 1 or len(to_columns) != 1:
        return None
    if from_id == current_object_type_id:
        return {
            "direction": "from",
            "current_property": from_columns[0],
            "related_filter_property": to_columns[0],
            "related_object_type": _text(relation.get("to_object_type_name")),
        }
    return {
        "direction": "to",
        "current_property": to_columns[0],
        "related_filter_property": from_columns[0],
        "related_object_type": _text(relation.get("from_object_type_name")),
    }


async def _relation_link_entries(
    relation: dict[str, Any],
    row: dict[str, Any],
    *,
    current_id: int,
    member_id: str,
    user_id: str,
    username: str,
) -> list[dict[str, Any]] | None:
    """单条关系类型的一跳 link；同 member 内查询，不跨机构。"""
    relation_name = _text(relation.get("relation_type_name"))
    status = _text(relation.get("status"))
    side = _relation_side(relation, current_id)
    if not relation_name or not side or status == "deprecated":
        return None
    current_value = row.get(side["current_property"])
    if current_value is None or _text(current_value) == "":
        return None
    try:
        result = await search_object_projection(
            side["related_object_type"],
            member_datasource_ids=[member_id] if member_id else None,
            filters=[
                {
                    "property": side["related_filter_property"],
                    "operator": "eq",
                    "value": str(current_value),
                }
            ],
            limit=_MAX_LINKS_PER_RELATION,
            user_id=user_id,
            username=username,
        )
    except ObjectProjectionError as err:
        logger.warning("关系一跳查询跳过: relation=%s error=%s", relation_name, err)
        return None
    entries: list[dict[str, Any]] = []
    for obj in result.get("objects") or []:
        key = _text(obj.get("object_key"))
        if not key:
            continue
        entry = {
            "relation": relation_name,
            "direction": side["direction"],
            "related_object_type": side["related_object_type"],
            "related_object_key": key,
        }
        title = _text((obj.get("properties") or {}).get(result.get("title_property") or ""))
        if title:
            entry["related_title"] = title
        entries.append(entry)
    return entries


async def _read_links(
    detail: dict[str, Any],
    row: dict[str, Any],
    *,
    member_id: str,
    user_id: str,
    username: str,
) -> dict[str, list[dict[str, Any]]]:
    """查询一跳 links：只读、最佳努力，单条关系失败只跳过并记录。"""
    current_id = int(detail.get("id") or 0)
    relations = await ontology_service.list_relations_async()
    candidates = relations[:_MAX_RELATIONS]
    # 各关系一跳查询相互独立；并发执行避免每点一行都串行等 N 次数据源。
    results = await asyncio.gather(
        *[
            _relation_link_entries(
                relation,
                row,
                current_id=current_id,
                member_id=member_id,
                user_id=user_id,
                username=username,
            )
            for relation in candidates
        ],
        return_exceptions=True,
    )
    links: dict[str, list[dict[str, Any]]] = {}
    for relation, result in zip(candidates, results, strict=False):
        if isinstance(result, BaseException):
            logger.warning("读取对象关系失败，已跳过: %s: %s", relation.get("relation_type_name"), result)
            continue
        if result:
            links[_text(relation.get("relation_type_name"))] = result
    return links


async def _apply_object_edits(detail: dict[str, Any], object_key: str, row: dict[str, Any]) -> dict[str, Any]:
    """执行开关打开时，把 Action 覆盖值合并到数据库行上。"""
    if not ontology_action_execute_enabled():
        return row
    object_type_id = int(detail.get("id") or 0)
    if not object_type_id:
        return row
    edits = await OntologyActionExecutionDao().latest_object_values_async(object_type_id, object_key)
    return {**row, **edits} if edits else row


async def read_object_projection(
    object_type_name: str,
    object_key: str,
    *,
    semantic_config_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    user_id: str = "",
    username: str = "",
) -> dict[str, Any]:
    """按对象类型 + 主键读取一个对象实例和一跳 links（只读数据库投影）。"""
    detail = await ontology_service.get_object_type_async(object_type_name)
    if detail is None:
        raise ObjectProjectionError(f"本体对象类型不存在: {object_type_name}")
    key_member, local_key = _parse_object_key(object_key, object_type_name)
    requested_members = member_datasource_ids or ([key_member] if key_member else None)
    query_detail, config, targets, group_id = await _projection_context(
        detail,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=requested_members,
        user_id=user_id,
        username=username,
    )
    query = build_object_query(
        query_detail,
        config,
        object_key=local_key,
        semantic_config_id=semantic_config_id,
    )
    rows = await _query_rows(
        targets,
        query["sql"],
        group_id=group_id,
        user_id=user_id,
        username=username,
        limit=1,
    )
    if not rows:
        raise ObjectProjectionError(f"对象不存在: {object_type_name}:{local_key}")
    scope = _member_scope(targets)
    qualified_key = _qualified_object_key(
        key_member or _text(scope.get("member_datasource_id")), object_type_name, local_key
    )
    row = await _apply_object_edits(detail, qualified_key, rows[0])
    derived = await _derived_property_values(
        detail, row, member_id=scope["member_datasource_id"], user_id=user_id, username=username
    )
    links = await _read_links(detail, row, member_id=scope["member_datasource_id"], user_id=user_id, username=username)
    return _assemble_object(detail, query, row, links, scope, derived_values=derived)


_DERIVED_AGGREGATE_SQL = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX"}
_DERIVED_FILTER_OPERATORS = {"eq", "ne", "contains", "gt", "gte", "lt", "lte", "in"}


def _build_derived_value_sql(
    table: str,
    filter_column: str,
    filter_value: Any,
    *,
    operator: str = "",
    target_column: str = "",
    extra_where: str = "",
) -> str:
    """派生属性取值 SQL（纯函数，供单测复用）：聚合（多行→单值）或选取（单行单列）。

    值经 escape_string 转义、标识符白名单引用，与对象投影其它查询同口径。
    """
    if operator == "count":
        expression = "COUNT(*)"
    elif operator == "count_distinct":
        expression = f"COUNT(DISTINCT {_quote_identifier(target_column)})"
    elif operator:
        expression = f"{_DERIVED_AGGREGATE_SQL[operator]}({_quote_identifier(target_column)})"
    else:
        expression = _quote_identifier(target_column)
    limit = "" if operator else " LIMIT 1"
    conditions = [f"{_quote_identifier(filter_column)} = '{escape_string(str(filter_value))}'"]
    if extra_where:
        conditions.append(extra_where)
    return (
        f"SELECT {expression} AS `derived_value` FROM {_quote_identifier(table)} "
        f"WHERE {' AND '.join(conditions)}{limit}"
    )


def _derived_extra_where(
    derivation: dict[str, Any],
    column_mappings: dict[str, Any],
    dimensions: dict[str, dict[str, str]],
    table: str,
) -> str:
    """派生聚合的对端过滤 + 时间窗（列白名单、值转义）。"""
    conditions: list[str] = []
    for item in derivation.get("filters") or []:
        property_name = _text(item.get("property"))
        operator = _text(item.get("operator")) or "eq"
        if operator not in _DERIVED_FILTER_OPERATORS:
            raise ObjectProjectionError(f"派生属性过滤操作符不支持: {operator}")
        target_table, column = _derived_physical_column(property_name, column_mappings, dimensions)
        if target_table != table:
            raise ObjectProjectionError("派生属性过滤属性与关联表不一致，MVP 不支持")
        conditions.append(_filter_condition(_quote_identifier(column), operator, item, _text(item.get("value"))))
    period = derivation.get("period")
    if isinstance(period, dict):
        property_name = _text(period.get("time_property"))
        target_table, column = _derived_physical_column(property_name, column_mappings, dimensions)
        if target_table != table:
            raise ObjectProjectionError("派生属性时间窗属性与关联表不一致，MVP 不支持")
        days = max(1, min(int(period.get("days") or 1), 3650))
        threshold = (date.today() - timedelta(days=days)).isoformat()
        conditions.append(f"{_quote_identifier(column)} >= '{threshold}'")
    return " AND ".join(conditions)


def _derived_physical_column(
    property_name: str,
    column_mappings: dict[str, Any],
    dimensions: dict[str, dict[str, str]],
) -> tuple[str, str]:
    """属性 → (物理表, 物理列)；未映射或解析失败抛 ObjectProjectionError。"""
    dimension = dimensions.get(_text(column_mappings.get(property_name)))
    if not dimension or not dimension.get("table") or not dimension.get("column"):
        raise ObjectProjectionError(f"派生属性目标属性无法解析物理列: {property_name}")
    return dimension["table"], dimension["column"]


async def _derived_property_values(
    detail: dict[str, Any],
    row: dict[str, Any],
    *,
    member_id: str,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    """派生属性值：查询时计算、不落存储（对齐 Palantir derived property）。

    与一跳 links 同语义的最佳努力：单条失败置空并告警，不影响对象读取。
    """
    derived_props = [
        prop
        for prop in detail.get("properties") or []
        if isinstance(prop, dict) and isinstance(prop.get("derivation"), dict) and prop["derivation"]
    ]
    if not derived_props:
        return {}
    current_id = int(detail.get("id") or 0)
    results = await asyncio.gather(
        *[
            _compute_derived_value(
                prop,
                row,
                current_id=current_id,
                member_id=member_id,
                user_id=user_id,
                username=username,
            )
            for prop in derived_props
        ],
        return_exceptions=True,
    )
    values: dict[str, Any] = {}
    for prop, result in zip(derived_props, results, strict=False):
        if isinstance(result, BaseException):
            logger.warning(
                "派生属性计算失败，置空: %s.%s: %s",
                detail.get("object_type_name"),
                prop.get("property_name"),
                result,
            )
        values[_text(prop.get("property_name"))] = None if isinstance(result, BaseException) else result
    return values


async def _compute_derived_value(
    prop: dict[str, Any],
    row: dict[str, Any],
    *,
    current_id: int,
    member_id: str,
    user_id: str,
    username: str,
) -> Any:
    """单条派生属性计算：沿关系遍历到对端对象类型的物理表取值。"""
    derivation = prop["derivation"]
    relation = await _load_derived_relation(derivation)
    side = _derived_relation_side(derivation, relation, current_id)
    context = await _derived_query_context(derivation, side, member_id=member_id, user_id=user_id, username=username)
    extra_where = _derived_extra_where(derivation, context["column_mappings"], context["dimensions"], context["table"])
    current_value = row.get(side["current_property"])
    if current_value is None or _text(current_value) == "":
        return None
    sql = _build_derived_value_sql(
        context["table"],
        context["filter_column"],
        current_value,
        operator=_text(derivation.get("operator")),
        target_column=context["target_column"],
        extra_where=extra_where,
    )
    rows = await _query_rows(
        context["targets"], sql, group_id=context["group_id"], user_id=user_id, username=username, limit=1
    )
    return rows[0].get("derived_value") if rows else None


async def _load_derived_relation(derivation: dict[str, Any]) -> dict[str, Any]:
    relation_name = _text(derivation.get("relation_type_name"))
    # list_relations 返回的行带两端对象类型名（原始 DAO 行只有 id）；与一跳 links 同口径
    relations = await ontology_service.list_relations_async()
    relation = next((item for item in relations if _text(item.get("relation_type_name")) == relation_name), None)
    if relation is None:
        raise ObjectProjectionError(f"派生属性引用的关系类型不存在: {relation_name}")
    return relation


def _derived_relation_side(derivation: dict[str, Any], relation: dict[str, Any], current_id: int) -> dict[str, str]:
    side = _relation_side(relation, current_id)
    if side is None:
        raise ObjectProjectionError("派生属性仅支持单列关联的非自关联关系（MVP）")
    kind = _text(derivation.get("kind"))
    if kind == "aggregate" and side["direction"] != "to":
        raise ObjectProjectionError("聚合派生属性要求当前对象类型在关系 to 侧")
    if kind == "select" and side["direction"] != "from":
        raise ObjectProjectionError("选取派生属性要求当前对象类型在关系 from 侧")
    return side


async def _derived_query_context(
    derivation: dict[str, Any],
    side: dict[str, str],
    *,
    member_id: str,
    user_id: str,
    username: str,
) -> dict[str, Any]:
    related_detail = await ontology_service.get_object_type_async(side["related_object_type"])
    if related_detail is None:
        raise ObjectProjectionError(f"派生属性对端对象类型不存在: {side['related_object_type']}")
    config, query_detail, targets, group_id = await _dataset_mapping_context(
        related_detail,
        [member_id] if member_id else None,
        user_id=user_id,
        username=username,
    )
    dimensions = _dimension_index(config)
    column_mappings = (query_detail.get("mappings") or [{}])[0].get("column_mappings") or {}
    if not isinstance(column_mappings, dict):
        raise ObjectProjectionError("对端对象类型映射缺少 column_mappings")
    table, filter_column = _derived_physical_column(side["related_filter_property"], column_mappings, dimensions)
    return {
        "targets": targets,
        "group_id": group_id,
        "table": table,
        "filter_column": filter_column,
        "target_column": _derived_target_column(derivation, column_mappings, dimensions, table),
        "column_mappings": column_mappings,
        "dimensions": dimensions,
    }


async def _derived_join_for_property(
    prop: dict[str, Any],
    *,
    index: int,
    main_table: str,
    main_column_mappings: dict[str, Any],
    main_dimensions: dict[str, dict[str, str]],
    relation_cache: dict[str, dict[str, Any]],
    current_id: int,
    member_id: str,
    user_id: str,
    username: str,
) -> tuple[str, str]:
    """单条派生属性 → (LEFT JOIN SQL, 过滤/排序表达式)。"""
    derivation = prop["derivation"]
    relation_name = _text(derivation.get("relation_type_name"))
    relation = relation_cache.get(relation_name)
    if relation is None:
        relation = await _load_derived_relation(derivation)
        relation_cache[relation_name] = relation
    side = _derived_relation_side(derivation, relation, current_id)
    current_table, current_column = _derived_physical_column(
        side["current_property"], main_column_mappings, main_dimensions
    )
    if current_table != main_table:
        raise ObjectProjectionError("派生属性关联列与对象主表不一致，MVP 不支持")
    join_key = f"{_quote_identifier(main_table)}.{_quote_identifier(current_column)}"
    context = await _derived_query_context(derivation, side, member_id=member_id, user_id=user_id, username=username)
    alias = f"d{index}"
    target_table = _quote_identifier(context["table"])
    filter_column = _quote_identifier(context["filter_column"])
    if _text(derivation.get("kind")) == "aggregate":
        operator = _text(derivation.get("operator"))
        if operator == "count":
            aggregate = "COUNT(*)"
        elif operator == "count_distinct":
            aggregate = f"COUNT(DISTINCT {_quote_identifier(context['target_column'])})"
        else:
            aggregate = f"{_DERIVED_AGGREGATE_SQL[operator]}({_quote_identifier(context['target_column'])})"
        extra_where = _derived_extra_where(
            derivation, context["column_mappings"], context["dimensions"], context["table"]
        )
        where_sql = f" WHERE {extra_where}" if extra_where else ""
        join = (
            f" LEFT JOIN (SELECT {filter_column} AS `join_key`, {aggregate} AS `value` "
            f"FROM {target_table}{where_sql} GROUP BY {filter_column}) AS `{alias}` "
            f"ON `{alias}`.`join_key` = {join_key}"
        )
        return join, f"`{alias}`.`value`"
    join = f" LEFT JOIN {target_table} AS `{alias}` ON `{alias}`.{filter_column} = {join_key}"
    return join, f"`{alias}`.{_quote_identifier(context['target_column'])}"


def _where_property_names(where: dict[str, Any] | None) -> set[str]:
    """收集 where 树引用的属性名。"""
    if not isinstance(where, dict):
        return set()
    if _text(where.get("operator")).lower() in _WHERE_GROUP_OPERATORS:
        names: set[str] = set()
        for child in where.get("children") or []:
            names.update(_where_property_names(child))
        return names
    name = _text(where.get("property"))
    return {name} if name else set()


async def _resolve_derived_filter_sort_async(
    detail: dict[str, Any],
    config: dict[str, Any],
    filters: list[dict[str, Any]],
    sorts: list[dict[str, Any]],
    *,
    member_id: str,
    user_id: str,
    username: str,
    semantic_config_id: str | None = None,
    where: dict[str, Any] | None = None,
    include_all_derived: bool = False,
) -> tuple[list[str], dict[str, str]]:
    """把过滤/排序/where 引用的派生属性编译成 LEFT JOIN + 表达式（官方 filter/sort 的 MVP 下推）。"""
    property_names = {_text((item or {}).get("property")) for item in [*filters, *sorts] if isinstance(item, dict)}
    property_names |= _where_property_names(where)
    derived = {
        _text(prop.get("property_name")): prop
        for prop in detail.get("properties") or []
        if isinstance(prop, dict) and isinstance(prop.get("derivation"), dict) and prop["derivation"]
    }
    if include_all_derived:
        property_names |= set(derived)
    names = sorted(name for name in property_names if name in derived)
    if not names:
        return [], {}
    mapping = _mapping_for_projection(detail, semantic_config_id)
    column_mappings = mapping.get("column_mappings")
    if not isinstance(column_mappings, dict):
        raise ObjectProjectionError("对象映射缺少 column_mappings")
    properties = [prop for prop in detail.get("properties") or [] if isinstance(prop, dict)]
    dimensions = _dimension_index(config)
    table, _columns = _projection_columns(properties, column_mappings, dimensions)
    current_id = int(detail.get("id") or 0)
    joins: list[str] = []
    expressions: dict[str, str] = {}
    relation_cache: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(names):
        join, expression = await _derived_join_for_property(
            derived[name],
            index=index,
            main_table=table,
            main_column_mappings=column_mappings,
            main_dimensions=dimensions,
            relation_cache=relation_cache,
            current_id=current_id,
            member_id=member_id,
            user_id=user_id,
            username=username,
        )
        joins.append(join)
        expressions[name] = expression
    return joins, expressions


def _derived_target_column(
    derivation: dict[str, Any],
    column_mappings: dict[str, Any],
    dimensions: dict[str, dict[str, str]],
    table: str,
) -> str:
    target_property = _text(derivation.get("target_property"))
    if not target_property:
        return ""
    target_table, target_column = _derived_physical_column(target_property, column_mappings, dimensions)
    if target_table != table:
        raise ObjectProjectionError("派生属性目标属性与关联列不在同一张表，MVP 不支持")
    return target_column


def _assemble_list_item(
    query: dict[str, Any],
    row: dict[str, Any],
    member_scope: dict[str, str] | None = None,
    derived_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scope = member_scope or {}
    local_key = _text(row.get(query["identifier_property"]))
    properties = {name: row.get(name) for name in query["properties"]}
    properties.update(derived_values or {})
    return {
        "object_type": query["object_type"],
        "object_key": _qualified_object_key(scope.get("member_datasource_id", ""), query["object_type"], local_key),
        "properties": properties,
        **scope,
    }


async def search_object_projection(
    object_type_name: str,
    *,
    member_datasource_ids: list[str] | None = None,
    keyword: str = "",
    filters: list[dict[str, Any]] | None = None,
    sorts: list[dict[str, Any]] | None = None,
    limit: int = 20,
    offset: int = 0,
    semantic_config_id: str | None = None,
    where: dict[str, Any] | None = None,
    user_id: str = "",
    username: str = "",
) -> dict[str, Any]:
    """通用对象查询（只读投影）：关键词、where 过滤、排序、分页和总数。"""
    detail = await ontology_service.get_object_type_async(object_type_name)
    if detail is None:
        raise ObjectProjectionError(f"本体对象类型不存在: {object_type_name}")
    query_detail, config, targets, group_id = await _projection_context(
        detail,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        username=username,
    )
    scope = _member_scope(targets)
    derived_joins, derived_expressions = await _resolve_derived_filter_sort_async(
        query_detail,
        config,
        filters or [],
        sorts or [],
        member_id=scope.get("member_datasource_id", ""),
        user_id=user_id,
        username=username,
        semantic_config_id=semantic_config_id,
        where=where,
        include_all_derived=True,
    )
    derived_select = dict(derived_expressions)
    derived_names = _derived_property_names(detail)
    count_needs_derived = any(
        _text((item or {}).get("property")) in derived_names for item in [*(filters or []), *(sorts or [])]
    ) or bool(_where_property_names(where) & derived_names)
    query = build_object_search_query(
        query_detail,
        config,
        keyword=keyword,
        filters=filters or [],
        sorts=sorts or [],
        limit=limit,
        offset=offset,
        semantic_config_id=semantic_config_id,
        where=where,
        derived_joins=derived_joins,
        derived_expressions=derived_expressions,
        derived_select=derived_select,
        count_joins=derived_joins if count_needs_derived else [],
    )
    started_at = time.perf_counter()
    count_rows = await _query_rows(
        targets,
        query["count_sql"],
        group_id=group_id,
        user_id=user_id,
        username=username,
        limit=1,
    )
    total = int((count_rows[0].get("total") if count_rows else 0) or 0)
    rows = await _query_rows(
        targets,
        query["sql"],
        group_id=group_id,
        user_id=user_id,
        username=username,
        limit=query["limit"] + 1,
    )
    execution_time_ms = int((time.perf_counter() - started_at) * 1000)
    has_more = len(rows) > query["limit"]
    page_rows = rows[: query["limit"]]
    return {
        "objects": [_assemble_list_item(query, row, scope) for row in page_rows],
        "has_more": has_more,
        "limit": query["limit"],
        "offset": query["offset"],
        "total": total,
        "title_property": query["title_property"],
        "properties": query["properties"],
        "sql": query["sql"],
        "execution_time_ms": execution_time_ms,
        **_member_scope(targets),
    }


async def aggregate_object_projection(
    object_type_name: str,
    *,
    member_datasource_ids: list[str] | None = None,
    group_by: list[str] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    filters: list[dict[str, Any]] | None = None,
    keyword: str = "",
    limit: int = 100,
    semantic_config_id: str | None = None,
    where: dict[str, Any] | None = None,
    user_id: str = "",
    username: str = "",
) -> dict[str, Any]:
    """对象聚合（只读投影）：按映射属性分组，返回白名单指标结果。"""
    detail = await ontology_service.get_object_type_async(object_type_name)
    if detail is None:
        raise ObjectProjectionError(f"本体对象类型不存在: {object_type_name}")
    query_detail, config, targets, group_id = await _projection_context(
        detail,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        username=username,
    )
    scope = _member_scope(targets)
    derived_joins, derived_expressions = await _resolve_derived_filter_sort_async(
        query_detail,
        config,
        filters or [],
        [],
        member_id=scope.get("member_datasource_id", ""),
        user_id=user_id,
        username=username,
        semantic_config_id=semantic_config_id,
        where=where,
        include_all_derived=True,
    )
    query = build_object_aggregate_query(
        query_detail,
        config,
        group_by=group_by or [],
        metrics=metrics or [],
        filters=filters or [],
        keyword=keyword,
        limit=limit,
        semantic_config_id=semantic_config_id,
        where=where,
        derived_joins=derived_joins,
        derived_expressions=derived_expressions,
    )
    started_at = time.perf_counter()
    rows = await _query_rows(
        targets,
        query["sql"],
        group_id=group_id,
        user_id=user_id,
        username=username,
        limit=query["limit"],
    )
    execution_time_ms = int((time.perf_counter() - started_at) * 1000)
    groups = [
        {
            "keys": {name: row.get(name) for name in query["group_by"]},
            "metrics": {name: row.get(name) for name in query["metric_keys"]},
        }
        for row in rows
    ]
    return {
        "object_type": query["object_type"],
        "group_by": query["group_by"],
        "metric_keys": query["metric_keys"],
        "groups": groups,
        "limit": query["limit"],
        "sql": query["sql"],
        "execution_time_ms": execution_time_ms,
        **_member_scope(targets),
    }


async def list_object_projection(
    object_type_name: str,
    *,
    member_datasource_ids: list[str] | None = None,
    keyword: str = "",
    limit: int = 20,
    offset: int = 0,
    semantic_config_id: str | None = None,
    user_id: str = "",
    username: str = "",
) -> dict[str, Any]:
    """对象实例列表（只读投影；MVP 不批量加载 links/overrides）。"""
    detail = await ontology_service.get_object_type_async(object_type_name)
    if detail is None:
        raise ObjectProjectionError(f"本体对象类型不存在: {object_type_name}")
    query_detail, config, targets, group_id = await _projection_context(
        detail,
        semantic_config_id=semantic_config_id,
        member_datasource_ids=member_datasource_ids,
        user_id=user_id,
        username=username,
    )
    query = build_object_list_query(
        query_detail,
        config,
        keyword=keyword,
        limit=limit,
        offset=offset,
        semantic_config_id=semantic_config_id,
    )
    rows = await _query_rows(
        targets,
        query["sql"],
        group_id=group_id,
        user_id=user_id,
        username=username,
        limit=query["limit"] + 1,
    )
    has_more = len(rows) > query["limit"]
    rows = rows[: query["limit"]]
    objects = [_assemble_list_item(query, row, _member_scope(targets)) for row in rows]
    return {
        "objects": objects,
        "has_more": has_more,
        "limit": query["limit"],
        "offset": query["offset"],
        "title_property": query["title_property"],
        "properties": query["properties"],
    }


__all__ = [
    "ObjectProjectionError",
    "build_object_list_query",
    "build_object_query",
    "build_object_aggregate_query",
    "build_object_search_query",
    "build_related_key_query",
    "ontology_action_execute_enabled",
    "aggregate_object_projection",
    "list_object_projection",
    "search_object_projection",
    "ontology_object_read_enabled",
    "read_object_projection",
]
