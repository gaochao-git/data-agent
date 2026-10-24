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
from ...daos.ontology_action_object_dao import OntologyActionObjectDao
from ..datasource.manager import get_datasource_manager
from ..datasource.target_context import get_execution_target_member_id, get_execution_target_name
from ..query import QueryOrchestrator, QuerySource, QuerySpec
from . import ontology_service
from .object_errors import ObjectProjectionError
from .object_query_builder import (  # noqa: F401 - 兼容投影层内部/测试对纯函数的直接引用
    _aggregate_group_properties,
    _aggregate_group_sql,
    _aggregate_metric,
    _aggregate_projection_context,
    _aggregate_select_parts,
    _build_select_sql,
    _derived_property_names,
    _dimension_index,
    _filter_condition,
    _identifier_property,
    _in_condition,
    _keyword_condition,
    _mapping_for_projection,
    _mapping_for_property,
    _mappings,
    _numeric_literal,
    _object_search_conditions,
    _object_search_order_by,
    _projection_columns,
    _quote_identifier,
    _reject_derived_property_operations,
    _search_property_sql,
    _title_property_name,
    _where_condition,
    _where_group_condition,
    _where_leaf_condition,
    build_object_aggregate_query,
    build_object_list_query,
    build_object_query,
    build_object_search_query,
    build_related_key_query,
)

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


def _append_link_warning(warnings: list[str] | None, message: str) -> None:
    if warnings is not None:
        warnings.append(message)


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    # 派生属性值查询时合并进 properties（对齐 Palantir：derived 就是属性，不单独分组）
    properties = {name: row.get(name) for name in query["properties"]}
    properties.update(derived_values or {})
    object_type = query["object_type"]
    scope = member_scope or {}
    payload = {
        "object_type": object_type,
        "object_key": _qualified_object_key(scope.get("member_datasource_id", ""), object_type, query["object_key"]),
        "properties": properties,
        "links": links,
        **scope,
    }
    if warnings:
        payload["warnings"] = list(warnings)
    return payload


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
    """返回 (查询详情, 配置, execution targets, group_id)。

    当前唯一取数路径是 Dataset source binding；``semantic_config_id`` 仅保留为
    旧客户端兼容参数，不再回退到语义模型映射。
    """
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
    warnings: list[str] | None = None,
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
        _append_link_warning(warnings, f"{relation_name} 关系读取失败，已跳过")
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
    warnings: list[str] | None = None,
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
                warnings=warnings,
            )
            for relation in candidates
        ],
        return_exceptions=True,
    )
    links: dict[str, list[dict[str, Any]]] = {}
    for relation, result in zip(candidates, results, strict=False):
        if isinstance(result, BaseException):
            logger.warning("读取对象关系失败，已跳过: %s: %s", relation.get("relation_type_name"), result)
            _append_link_warning(warnings, f"{relation.get('relation_type_name')} 关系读取失败，已跳过")
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
    if (_text(detail.get("write_mode")) or "read_only") == "action_created":
        return await _read_action_object(detail, object_key)
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
    link_warnings: list[str] = []
    links = await _read_links(
        detail,
        row,
        member_id=scope["member_datasource_id"],
        user_id=user_id,
        username=username,
        warnings=link_warnings,
    )
    return _assemble_object(detail, query, row, links, scope, derived_values=derived, warnings=link_warnings)


async def _read_action_object(detail: dict[str, Any], object_key: str) -> dict[str, Any]:
    """动作对象读取：行存业务库动作对象表（无源投影），属性集原样展开。"""
    key_member, local_key = _parse_object_key(object_key, _text(detail.get("object_type_name")))
    stored = await OntologyActionObjectDao().get_object_async(int(detail.get("id") or 0), local_key)
    if stored is None:
        raise ObjectProjectionError(f"对象不存在: {detail.get('object_type_name')}:{local_key}")
    properties = stored.get("properties") or {}
    return {
        "object_type": _text(detail.get("object_type_name")),
        "object_key": _qualified_object_key(key_member, _text(detail.get("object_type_name")), local_key),
        "properties": properties,
        "links": [],
        "title_property": _title_property_name(
            [prop for prop in detail.get("properties") or [] if isinstance(prop, dict)],
            _identifier_property_name_of(detail),
        ),
        "write_mode": "action_created",
        "created_by": _text(stored.get("create_by")),
        "create_time": _text(stored.get("create_time")),
    }


async def _search_action_objects(
    detail: dict[str, Any],
    *,
    keyword: str = "",
    filters: list[dict[str, Any]] | None = None,
    sorts: list[dict[str, Any]] | None = None,
    where: dict[str, Any] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """动作对象列表：业务库动作对象表关键词查询（MVP 不做属性过滤/排序下推）。"""
    if filters or sorts or where:
        raise ObjectProjectionError("动作对象列表暂不支持属性过滤/排序/where，仅支持 keyword 与分页")
    objects, total = await OntologyActionObjectDao().list_objects_async(
        int(detail.get("id") or 0), keyword=keyword, limit=limit, offset=offset
    )
    return {
        "objects": [
            {
                "object_type": _text(detail.get("object_type_name")),
                "object_key": _text(item.get("object_key")),
                "properties": item.get("properties") or {},
            }
            for item in objects
        ],
        "has_more": offset + len(objects) < total,
        "limit": max(1, min(int(limit), 100)),
        "offset": int(offset),
        "total": total,
        "title_property": "",
        "write_mode": "action_created",
    }


def _identifier_property_name_of(detail: dict[str, Any]) -> str:
    for prop in detail.get("properties") or []:
        if isinstance(prop, dict) and bool(prop.get("is_identifier")):
            return _text(prop.get("property_name"))
    return ""


_DERIVED_AGGREGATE_SQL = {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX"}
_DERIVED_FILTER_OPERATORS = {"eq", "ne", "contains", "gt", "gte", "lt", "lte", "in"}


def _derived_aggregate_expression(operator: str, target_column: str, *, numeric: bool) -> str:
    """聚合表达式：数值目标先 CAST，避免 Hive 对字符串列按字典序 MAX/MIN。"""
    column = _quote_identifier(target_column)
    if numeric and operator in {"sum", "avg", "min", "max"}:
        column = f"CAST({column} AS DECIMAL(38,6))"
    return f"{_DERIVED_AGGREGATE_SQL[operator]}({column})"


def _build_derived_value_sql(
    table: str,
    filter_column: str,
    filter_value: Any,
    *,
    operator: str = "",
    target_column: str = "",
    extra_where: str = "",
    numeric: bool = False,
) -> str:
    """派生属性取值 SQL（纯函数，供单测复用）：聚合（多行→单值）或选取（单行单列）。

    值经 escape_string 转义、标识符白名单引用，与对象投影其它查询同口径。
    """
    if operator == "count":
        expression = "COUNT(*)"
    elif operator == "count_distinct":
        expression = f"COUNT(DISTINCT {_quote_identifier(target_column)})"
    elif operator:
        expression = _derived_aggregate_expression(operator, target_column, numeric=numeric)
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
    cache: dict[Any, Any] = {}
    results = await asyncio.gather(
        *[
            _compute_derived_value(
                prop,
                row,
                current_id=current_id,
                member_id=member_id,
                user_id=user_id,
                username=username,
                cache=cache,
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
    cache: dict[Any, Any] | None = None,
) -> Any:
    """单条派生属性计算：沿关系遍历到对端对象类型的物理表取值。"""
    derivation = prop["derivation"]
    relation = await _load_derived_relation(derivation, cache)
    side = _derived_relation_side(derivation, relation, current_id)
    context = await _derived_query_context(
        derivation, side, member_id=member_id, user_id=user_id, username=username, cache=cache
    )
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
        numeric=_text(prop.get("value_type")) in {"Integer", "Decimal"},
    )
    rows = await _query_rows(
        context["targets"], sql, group_id=context["group_id"], user_id=user_id, username=username, limit=1
    )
    return rows[0].get("derived_value") if rows else None


async def _load_derived_relation(derivation: dict[str, Any], cache: dict[Any, Any] | None = None) -> dict[str, Any]:
    relation_name = _text(derivation.get("relation_type_name"))
    # list_relations 返回的行带两端对象类型名（原始 DAO 行只有 id）；与一跳 links 同口径
    relations = (cache or {}).get("relations") if cache is not None else None
    if relations is None:
        relations = await ontology_service.list_relations_async()
        if cache is not None:
            cache["relations"] = relations
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
    cache: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    base_key = (
        "derived_context",
        side["related_object_type"],
        side["current_property"],
        side["related_filter_property"],
        member_id,
    )
    base = cache.get(base_key) if cache is not None else None
    if base is None:
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
        base = {
            "targets": targets,
            "group_id": group_id,
            "table": table,
            "filter_column": filter_column,
            "column_mappings": column_mappings,
            "dimensions": dimensions,
        }
        if cache is not None:
            cache[base_key] = base
    return {
        **base,
        "target_column": _derived_target_column(derivation, base["column_mappings"], base["dimensions"], base["table"]),
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
            aggregate = _derived_aggregate_expression(
                operator,
                context["target_column"],
                numeric=_text(prop.get("value_type")) in {"Integer", "Decimal"},
            )
        extra_where = _derived_extra_where(
            derivation, context["column_mappings"], context["dimensions"], context["table"]
        )
        where_sql = f" WHERE {extra_where}" if extra_where else ""
        join = (
            f" LEFT JOIN (SELECT {filter_column} AS `join_key`, {aggregate} AS `value` "
            f"FROM {target_table}{where_sql} GROUP BY {filter_column}) AS `{alias}` "
            f"ON `{alias}`.`join_key` = {join_key}"
        )
        expression = f"`{alias}`.`value`"
        if operator in {"count", "count_distinct", "sum"}:
            # 无对端行时 COUNT/SUM 语义为 0，而不是 NULL；同时避免 Hive DESC 把 NULL 排在最前。
            expression = f"COALESCE({expression}, 0)"
        return join, expression
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
    if (_text(detail.get("write_mode")) or "read_only") == "action_created":
        return await _search_action_objects(
            detail,
            keyword=keyword,
            filters=filters,
            sorts=sorts,
            where=where,
            limit=limit,
            offset=offset,
        )
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
