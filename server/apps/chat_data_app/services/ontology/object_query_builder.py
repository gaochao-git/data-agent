"""对象投影的纯 SQL 构造层。

只负责把对象类型/映射/属性/过滤/排序/聚合编译成 SQL，不触库、不组装对象。
执行与对象契约由 ``object_projection`` 负责；拆出本模块是为了让投影服务层
不再同时承担 SQL 编译与 IO/组装两类职责。
"""

from __future__ import annotations

import re
from typing import Any

from pymysql.converters import escape_string

from .object_errors import ObjectProjectionError


def _text(value: Any) -> str:
    return str(value or "").strip()


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MAX_KEY_LENGTH = 512
_MAX_LINKS_PER_RELATION = 20

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

_WHERE_GROUP_OPERATORS = {"and", "or", "not"}
_WHERE_LEAF_OPERATORS = {"eq", "ne", "contains", "gt", "gte", "lt", "lte", "in", "is_null", "is_not_null"}
_MAX_WHERE_DEPTH = 3
_MAX_WHERE_NODES = 50

_AGGREGATE_OPERATIONS = {"count", "count_distinct", "sum", "avg", "min", "max"}
_AGGREGATE_NUMERIC_OPERATIONS = {"sum", "avg"}
_MAX_AGGREGATE_METRICS = 5
_MAX_AGGREGATE_GROUP_BY = 5
_MAX_AGGREGATE_LIMIT = 200


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


def _keyword_condition(search_column: str, identifier_column: str, keyword: str) -> str:
    keyword_value = _text(keyword)
    if not keyword_value:
        return ""
    if len(keyword_value) > 200:
        raise ObjectProjectionError("搜索关键词不能超过 200 字符")
    escaped = escape_string(keyword_value)
    search_columns = {_quote_identifier(search_column), _quote_identifier(identifier_column)}
    return "(" + " OR ".join(f"{column} LIKE '%{escaped}%'" for column in sorted(search_columns)) + ")"


def _in_condition(column: str, item: dict[str, Any], value_type: str = "") -> str:
    values = [_text(entry) for entry in (item.get("values") or []) if _text(entry)]
    if not values:
        # 兼容旧前端/API：in 只传 value 时按中英文逗号、分号、空白拆分
        values = [part.strip() for part in re.split(r"[,，;；\s]+", _text(item.get("value"))) if part.strip()]
    if not values:
        return "1 = 0"
    if _text(value_type) in {"Integer", "Decimal"}:
        literals = [_numeric_literal(entry, value_type) for entry in values[:100]]
    else:
        literals = [f"'{escape_string(entry)}'" for entry in values[:100]]
    return f"{column} IN ({', '.join(literals)})"


def _numeric_literal(value: str, value_type: str) -> str:
    normalized = _text(value)
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", normalized):
        raise ObjectProjectionError(f"数值属性过滤值非法: {normalized}")
    return normalized


def _filter_condition(column: str, operator: str, item: dict[str, Any], value: str, value_type: str = "") -> str:
    if operator == "in":
        return _in_condition(column, item, value_type)
    if operator == "contains":
        return f"{column} LIKE '%{escape_string(value)}%'"
    literal = (
        _numeric_literal(value, value_type)
        if _text(value_type) in {"Integer", "Decimal"}
        else f"'{escape_string(value)}'"
    )
    return f"{column} {_FILTER_SQL_SYMBOLS[operator]} {literal}"


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


def _where_condition(
    where: dict[str, Any] | None,
    column_by_property: dict[str, str],
    derived_expressions: dict[str, str] | None,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
    main_table: str = "",
    value_types: dict[str, str] | None = None,
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
            value_types=value_types,
        )
    return _where_leaf_condition(
        operator, where, column_by_property, derived_expressions, main_table=main_table, value_types=value_types
    )


def _where_group_condition(
    operator: str,
    where: dict[str, Any],
    column_by_property: dict[str, str],
    derived_expressions: dict[str, str] | None,
    *,
    depth: int,
    counter: list[int],
    main_table: str = "",
    value_types: dict[str, str] | None = None,
) -> str:
    children = [child for child in where.get("children") or [] if isinstance(child, dict)]
    if operator == "not" and len(children) != 1:
        raise ObjectProjectionError("where.not 必须且只能有一个子条件")
    if operator != "not" and not children:
        raise ObjectProjectionError(f"where.{operator} 至少需要一个子条件")
    parts = [
        _where_condition(
            child,
            column_by_property,
            derived_expressions,
            depth=depth + 1,
            counter=counter,
            main_table=main_table,
            value_types=value_types,
        )
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
    value_types: dict[str, str] | None = None,
) -> str:
    if operator not in _WHERE_LEAF_OPERATORS:
        raise ObjectProjectionError(f"不支持的 where 操作符: {operator or '?'}")
    column = _search_property_sql(_text(where.get("property")), column_by_property, derived_expressions, main_table)
    if operator in {"is_null", "is_not_null"}:
        return f"{column} IS {'NOT ' if operator == 'is_not_null' else ''}NULL"
    return _filter_condition(
        column,
        operator,
        where,
        _text(where.get("value")),
        _text((value_types or {}).get(_text(where.get("property")))),
    )


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
    value_types: dict[str, str] | None = None,
) -> str:
    """通用对象过滤条件；只接受已映射属性，不拼接任意 SQL。"""
    conditions: list[str] = []
    keyword_condition = _keyword_condition(search_column, identifier_column, keyword)
    if keyword_condition:
        conditions.append(keyword_condition)
    where_condition = _where_condition(
        where, column_by_property, derived_expressions, main_table=main_table, value_types=value_types
    )
    if where_condition:
        conditions.append(where_condition)
    for item in filters[:_MAX_FILTERS]:
        property_name = _text(item.get("property"))
        operator = _text(item.get("operator")).lower()
        if operator not in _FILTER_OPERATORS:
            raise ObjectProjectionError(f"不支持的过滤操作符: {operator or '?'}")
        column = _search_property_sql(property_name, column_by_property, derived_expressions, main_table)
        conditions.append(
            _filter_condition(
                column,
                operator,
                item,
                _text(item.get("value")),
                _text((value_types or {}).get(property_name)),
            )
        )
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
    value_types = {_text(prop.get("property_name")): _text(prop.get("value_type")) for prop in properties}
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
        value_types=value_types,
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
    return {
        "sql": sql,
        "table": table,
        "object_type": _text(detail.get("object_type_name")),
        "identifier_property": identifier_property,
        "title_property": search_property,
        "properties": [name for name, _column in columns] + list((derived_select or {}).keys()),
        "limit": safe_limit,
        "offset": safe_offset,
    }


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
        value_types=context["value_types"],
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
