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
from typing import Any

from pymysql.converters import escape_string

from utils.logger import get_logger

from ...daos.ontology_action_execution_dao import OntologyActionExecutionDao
from ..datasource.manager import get_datasource_manager
from ..semantic.run.semantic_config_service import get_semantic_config_service
from ..sql_run.runtime import execute_sql_for_member
from . import ontology_service

logger = get_logger(__name__)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_MAX_KEY_LENGTH = 512
_MAX_LINKS_PER_RELATION = 20
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
    sql = (
        f"SELECT {_quote_identifier(identifier_dimension_meta['column'])} AS `related_key` "
        f"FROM {_quote_identifier(identifier_dimension_meta['table'])} "
        f"WHERE {_quote_identifier(filter_dimension['column'])} = '{escape_string(filter_value)}' "
        f"LIMIT {_MAX_LINKS_PER_RELATION}"
    )
    return {
        "sql": sql,
        "related_object_type": _text(detail.get("object_type_name")),
        "identifier_property": identifier_property,
        "filter_property": filter_property,
    }


def _assemble_object(
    detail: dict[str, Any],
    query: dict[str, Any],
    row: dict[str, Any],
    links: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    properties = {name: row.get(name) for name in query["properties"]}
    object_type = query["object_type"]
    return {
        "object_type": object_type,
        "object_key": f"{object_type}:{query['object_key']}",
        "properties": properties,
        "links": links,
    }


async def _mapping_context(
    mapping: dict[str, Any],
    *,
    user_id: str,
    username: str,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    config_id = _text(mapping.get("semantic_config_id"))
    if not config_id:
        raise ObjectProjectionError("对象映射缺少 semantic_config_id")
    group_ids = await get_semantic_config_service().dao.get_config_group_ids_async([config_id])
    group_id = group_ids.get(config_id, "")
    if not group_id:
        raise ObjectProjectionError(f"语义模型不存在或已删除: {config_id}")
    config = await get_semantic_config_service().get_config_async(group_id, config_id, user_id)
    if not config:
        raise ObjectProjectionError(f"语义模型不可用: {config_id}")
    targets = await get_datasource_manager().list_authorized_execution_targets_async(
        group_id, user_id=user_id or None, username=username or None
    )
    if not targets:
        raise ObjectProjectionError("当前用户在该数据源组下没有可执行数据源")
    return config, group_id, targets


async def _query_rows(
    targets: list[dict[str, Any]],
    sql: str,
    *,
    group_id: str,
    user_id: str,
    username: str,
    limit: int,
) -> list[dict[str, Any]]:
    result = await execute_sql_for_member(
        targets[0],
        sql,
        limit=limit,
        timeout=10,
        user_id=user_id or None,
        username=username or None,
        datasource_id=group_id,
    )
    if result.get("status") != "succeeded":
        raise ObjectProjectionError(f"对象查询失败: {result.get('error') or '未知错误'}")
    return [row for row in result.get("data") or [] if isinstance(row, dict)]


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


def _link_entries(
    relation_name: str,
    side: dict[str, str],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """对端主键行 → link 实例列表（去重、保持确定性）。"""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for related_row in rows:
        key = _text(related_row.get("related_key"))
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "relation": relation_name,
                "direction": side["direction"],
                "related_object_type": side["related_object_type"],
                "related_object_key": f"{side['related_object_type']}:{key}",
            }
        )
    return entries


async def _relation_link_entries(
    relation: dict[str, Any],
    row: dict[str, Any],
    *,
    current_id: int,
    user_id: str,
    username: str,
) -> list[dict[str, Any]] | None:
    """单条关系类型的一跳 link；不适用/失败返回 None。"""
    relation_name = _text(relation.get("relation_type_name"))
    status = _text(relation.get("status"))
    side = _relation_side(relation, current_id)
    if not relation_name or not side or status == "deprecated":
        return None
    current_value = row.get(side["current_property"])
    if current_value is None or _text(current_value) == "":
        return None
    related_detail = await ontology_service.get_object_type_async(side["related_object_type"])
    if related_detail is None:
        return None
    try:
        mapping = _mapping_for_property(related_detail, side["related_filter_property"])
        config, group_id, targets = await _mapping_context(mapping, user_id=user_id, username=username)
        query = build_related_key_query(
            related_detail,
            config,
            filter_property=side["related_filter_property"],
            filter_value=str(current_value),
        )
        rows = await _query_rows(
            targets,
            query["sql"],
            group_id=group_id,
            user_id=user_id,
            username=username,
            limit=_MAX_LINKS_PER_RELATION,
        )
    except ObjectProjectionError as err:
        logger.warning("关系一跳查询跳过: relation=%s error=%s", relation_name, err)
        return None
    return _link_entries(relation_name, side, rows)


async def _read_links(
    detail: dict[str, Any],
    row: dict[str, Any],
    *,
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
            _relation_link_entries(relation, row, current_id=current_id, user_id=user_id, username=username)
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
    user_id: str = "",
    username: str = "",
) -> dict[str, Any]:
    """按对象类型 + 主键读取一个对象实例和一跳 links（只读数据库投影）。"""
    detail = await ontology_service.get_object_type_async(object_type_name)
    if detail is None:
        raise ObjectProjectionError(f"本体对象类型不存在: {object_type_name}")
    mapping = _mapping_for_projection(detail, semantic_config_id)
    config, group_id, targets = await _mapping_context(mapping, user_id=user_id, username=username)
    query = build_object_query(
        detail,
        config,
        object_key=object_key,
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
        raise ObjectProjectionError(f"对象不存在: {object_type_name}:{object_key}")
    row = await _apply_object_edits(detail, object_key, rows[0])
    links = await _read_links(detail, row, user_id=user_id, username=username)
    return _assemble_object(detail, query, row, links)


async def list_object_projection(
    object_type_name: str,
    *,
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
    mapping = _mapping_for_projection(detail, semantic_config_id)
    config, group_id, targets = await _mapping_context(mapping, user_id=user_id, username=username)
    query = build_object_list_query(
        detail,
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
    objects = [
        {
            "object_type": query["object_type"],
            "object_key": f"{query['object_type']}:{_text(row.get(query['identifier_property']))}",
            "properties": {name: row.get(name) for name in query["properties"]},
        }
        for row in rows
    ]
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
    "build_related_key_query",
    "ontology_action_execute_enabled",
    "list_object_projection",
    "ontology_object_read_enabled",
    "read_object_projection",
]
