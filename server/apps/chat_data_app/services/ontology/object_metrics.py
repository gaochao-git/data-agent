"""对象类型 ↔ 语义指标关联的解析层。

关联模型（对齐 Ossie mapping 的简化形态）：
- ``chat_data_dataset_semantic_link`` 只保存「Dataset ↔ 语义模型」引用与列对照；
- 对象类型通过 ``source_binding``（属性 → Dataset 逻辑列）与同一 Dataset 的关联组合，
  派生得到「对象属性 ↔ 指标维度」，指标定义仍只存在于语义层；
- 本模块只做解析/翻译，不执行 SQL（执行见 ``object_projection.aggregate_metric_projection``）。

约束：
- 指标与对象 Dataset 必须是同一张物理表（MVP；跨表复合指标先标记不可执行）；
- 命名指标存在时不允许回退为属性 ad-hoc 聚合（调用方负责）。
"""

from __future__ import annotations

from typing import Any

from utils.logger import get_logger

from ..dataset.dataset_semantic_link_service import (
    DatasetSemanticLinkInvalidError,
    dataset_semantic_link_service,
)
from ..dataset.dataset_service import logical_dataset_service
from ..semantic.run.semantic_query_plan_builder import (
    resolve_composite_metric,
    resolve_derived_metric,
    substitute_composite_formula,
)
from . import ontology_service

logger = get_logger(__name__)

_OPERATOR_MAP = {
    "=": "eq",
    "==": "eq",
    "!=": "ne",
    "<>": "ne",
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
    "in": "in",
}
_UNSUPPORTED_OPERATORS = {"like", "not like", "starts with", "ends with", "between", "is null", "is not null"}


class ObjectMetricError(Exception):
    """对象类型 ↔ 指标解析失败。"""


class ObjectMetricNotExecutableError(ObjectMetricError):
    """指标存在但不能以该对象集为作用域执行。"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _metric_kind(metric: dict[str, Any]) -> str:
    kind = _text(metric.get("metric_kind")).lower()
    return kind if kind in {"derived", "composite"} else "atomic"


def _describe_metric(metric: dict[str, Any], config: dict[str, Any], link: dict[str, Any]) -> dict[str, Any]:
    """指标元数据（不暴露表达式，避免口径被二次实现）。"""
    return {
        "name": _text(metric.get("name")),
        "kind": _metric_kind(metric),
        "description": _text(metric.get("description")) or _text(metric.get("business_notes")),
        "semantic_config_id": _text(link.get("semantic_config_id")),
        "semantic_config_name": _text(config.get("name")),
        "table": _text(metric.get("table")),
    }


def _resolve_group_id(dataset: dict[str, Any], datasource_group_id: str, member_ids: list[str] | None) -> str:
    group_id = _text(datasource_group_id)
    bindings = [item for item in dataset.get("bindings") or [] if isinstance(item, dict)]
    if group_id:
        return group_id
    requested_members = {_text(item) for item in member_ids or [] if _text(item)}
    for binding in bindings:
        if _text(binding.get("member_datasource_id")) in requested_members:
            return _text(binding.get("datasource_group_id"))
    return _text(bindings[0].get("datasource_group_id")) if bindings else ""


def _link_tables(link: dict[str, Any], dataset: dict[str, Any], group_id: str) -> set[str]:
    tables = {_text(link.get("table_name"))}
    tables.update(
        _text(item.get("physical_table"))
        for item in dataset.get("bindings") or []
        if isinstance(item, dict) and _text(item.get("datasource_group_id")) == group_id
    )
    tables.discard("")
    return tables


def _metric_unavailable_reason(
    metric: dict[str, Any],
    config: dict[str, Any] | None,
    link: dict[str, Any],
    dataset: dict[str, Any],
    group_id: str,
) -> str:
    """返回不可执行原因；空串表示可执行。"""
    if config is None:
        return "语义模型不存在或不可访问"
    if _text(link.get("lifecycle_status")) != "active":
        return "关联未启用（lifecycle_status 需为 active）"
    metric_table = _text(metric.get("table"))
    if metric_table and metric_table not in _link_tables(link, dataset, group_id):
        return f"指标归属表 {metric_table} 与 Dataset 物理表不一致"
    return ""


async def _load_object_dataset_async(
    object_type_name: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """对象类型 → (detail, source_binding, dataset)；detail 不存在抛错。"""
    detail = await ontology_service.get_object_type_async(_text(object_type_name))
    if detail is None:
        raise ObjectMetricError(f"本体对象类型不存在: {object_type_name}")
    binding = detail.get("source_binding")
    if not isinstance(binding, dict) or not binding.get("dataset_id"):
        return detail, None, None
    dataset = await logical_dataset_service.get_dataset_async_by_id_async(int(binding["dataset_id"]))
    return detail, binding, dataset


def _metric_entries_for_link(
    link: dict[str, Any],
    config: dict[str, Any] | None,
    dataset: dict[str, Any],
    group_id: str,
) -> list[dict[str, Any]]:
    """单个关联下的指标条目（含可执行性判定）。"""
    scope = [item for item in link.get("metric_scope") or [] if isinstance(item, str) and item.strip()]
    entries: list[dict[str, Any]] = []
    for metric in (config or {}).get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        name = _text(metric.get("name"))
        if not name or (scope and name not in scope):
            continue
        reason = _metric_unavailable_reason(metric, config, link, dataset, group_id)
        entry = _describe_metric(metric, config or {}, link)
        entry["executable_in_scope"] = not reason
        entry["unavailable_reason"] = reason
        entries.append(entry)
    return entries


async def list_object_metrics_async(
    object_type_name: str,
    *,
    datasource_group_id: str = "",
    member_datasource_ids: list[str] | None = None,
    user_id: str = "",
) -> dict[str, Any]:
    """对象类型可执行指标清单（只读；用于目录曝光与管理端查看）。"""
    _detail, binding, dataset = await _load_object_dataset_async(object_type_name)
    if dataset is None:
        return {
            "object_type": _text(object_type_name),
            "dataset_name": "",
            "datasource_group_id": _text(datasource_group_id),
            "metrics": [],
            "reason": "对象类型未绑定 Dataset" if binding is None else "对象类型绑定的 Dataset 不存在",
        }
    group_id = _resolve_group_id(dataset, datasource_group_id, member_datasource_ids)
    links = await dataset_semantic_link_service.list_links_for_dataset_id_async(
        int(dataset["id"]), datasource_group_id=group_id or None
    )
    metrics: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        config = await dataset_semantic_link_service.load_config_for_link_async(link, user_id=user_id)
        for entry in _metric_entries_for_link(link, config, dataset, group_id):
            key = (_text(link.get("semantic_config_id")), entry["name"])
            if key in seen:
                continue
            seen.add(key)
            metrics.append(entry)
    return {
        "object_type": _text(object_type_name),
        "dataset_name": _text(dataset.get("dataset_name")),
        "datasource_group_id": group_id,
        "metrics": metrics,
        "reason": "" if metrics else "对象类型绑定的 Dataset 未关联语义模型指标",
    }


def _inverse_map(mapping: dict[str, Any]) -> dict[str, str]:
    return {_text(value): _text(key) for key, value in (mapping or {}).items() if _text(key) and _text(value)}


def _translate_filter_leaf(
    leaf: dict[str, Any],
    logical_by_dimension: dict[str, str],
    property_by_logical: dict[str, str],
) -> dict[str, Any]:
    """单条派生过滤叶子 → 对象属性过滤；任一步映射缺失即不可执行。"""
    dimension = _text(leaf.get("dimension"))
    operator = _text(leaf.get("operator")).lower() or "="
    if operator in _UNSUPPORTED_OPERATORS or operator not in _OPERATOR_MAP:
        raise ObjectMetricNotExecutableError(f"指标过滤操作符不支持映射到对象属性: {operator or '?'}")
    logical_column = logical_by_dimension.get(dimension)
    if not logical_column:
        raise ObjectMetricNotExecutableError(f"指标维度 {dimension} 未映射到 Dataset 逻辑列，无法在对象作用域执行")
    property_name = property_by_logical.get(logical_column)
    if not property_name:
        raise ObjectMetricNotExecutableError(f"Dataset 逻辑列 {logical_column} 未映射到对象属性，无法在对象作用域执行")
    mapped_operator = _OPERATOR_MAP[operator]
    if mapped_operator != "in":
        return {"property": property_name, "operator": mapped_operator, "value": _text(leaf.get("value"))}
    raw_value = leaf.get("value")
    values = raw_value if isinstance(raw_value, list) else [raw_value]
    return {
        "property": property_name,
        "operator": "in",
        "values": [_text(item) for item in values if _text(item)],
    }


def translate_metric_filters(
    filter_leaves: list[dict[str, Any]],
    *,
    link: dict[str, Any],
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    """派生指标周期/修饰词（语义维度）→ 对象属性过滤（供对象投影复用）。"""
    logical_by_dimension = _inverse_map(link.get("column_mappings") or {})
    property_by_logical = _inverse_map(binding.get("column_mappings") or {})
    return [_translate_filter_leaf(leaf, logical_by_dimension, property_by_logical) for leaf in filter_leaves]


def _resolve_derived_term(
    metric: dict[str, Any],
    metrics_map: dict[str, dict[str, Any]],
    dimensions_map: dict[str, dict[str, Any]],
    name: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    try:
        base_cfg, leaves = resolve_derived_metric(metric, metrics_map, dimensions_map)
    except ValueError as exc:
        raise ObjectMetricNotExecutableError(str(exc)) from exc
    expression = _text(base_cfg.get("expression"))
    table = _text(base_cfg.get("table")) or _text(metric.get("table"))
    return expression, table, leaves


def _resolve_composite_term(
    metric: dict[str, Any],
    metrics_map: dict[str, dict[str, Any]],
    name: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    try:
        plan = resolve_composite_metric(metric, metrics_map)
    except ValueError as exc:
        raise ObjectMetricNotExecutableError(str(exc)) from exc
    if plan is None:
        raise ObjectMetricNotExecutableError(f"复合指标解析失败: {name}")
    expression = substitute_composite_formula(_text(plan.get("formula")), plan.get("members") or [])
    return expression, _text(plan.get("table")), []


def _resolve_metric_term(
    name: str,
    *,
    metrics_map: dict[str, dict[str, Any]],
    dimensions_map: dict[str, dict[str, Any]],
    scope: list[str],
    config: dict[str, Any],
    link: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    metric = metrics_map.get(name)
    if metric is None:
        raise ObjectMetricError(f"语义模型中不存在指标: {name}")
    if scope and name not in scope:
        raise ObjectMetricNotExecutableError(f"指标未在该 Dataset 关联的 metric_scope 内: {name}")
    kind = _metric_kind(metric)
    if kind == "derived":
        expression, table, leaves = _resolve_derived_term(metric, metrics_map, dimensions_map, name)
    elif kind == "composite":
        expression, table, leaves = _resolve_composite_term(metric, metrics_map, name)
    else:
        expression, table, leaves = _text(metric.get("expression")), _text(metric.get("table")), []
    if not expression:
        raise ObjectMetricNotExecutableError(f"指标缺少聚合表达式: {name}")
    term = {"name": name, "expression": expression, "table": table, "kind": kind}
    return term, leaves, _describe_metric(metric, config, link)


def resolve_metric_expressions(
    metric_names: list[str],
    *,
    config: dict[str, Any],
    link: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    """把语义指标名解析成可执行的聚合表达式（派生/复合在语义层展开）。"""
    metrics_map = {
        _text(item.get("name")): item
        for item in config.get("metrics") or []
        if isinstance(item, dict) and _text(item.get("name"))
    }
    dimensions_map = {
        _text(item.get("name")): item
        for item in config.get("dimensions") or []
        if isinstance(item, dict) and _text(item.get("name"))
    }
    scope = [item for item in link.get("metric_scope") or [] if isinstance(item, str) and item.strip()]
    terms: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    filter_leaves: list[dict[str, Any]] = []
    metrics_with_filters: list[str] = []
    for raw_name in metric_names:
        name = _text(raw_name)
        term, leaves, entry_meta = _resolve_metric_term(
            name,
            metrics_map=metrics_map,
            dimensions_map=dimensions_map,
            scope=scope,
            config=config,
            link=link,
        )
        terms.append(term)
        meta.append(entry_meta)
        if leaves:
            metrics_with_filters.append(name)
        filter_leaves.extend(leaves)
    # 口径敏感：派生指标的时间窗/修饰词当前以整条查询的 WHERE 表达，只允许单独查询。
    if metrics_with_filters and len(metric_names) > 1:
        raise ObjectMetricNotExecutableError(
            f"派生指标的时间窗/修饰词暂不支持与其他指标同时聚合，请单独查询: {metrics_with_filters}"
        )
    translated = translate_metric_filters(filter_leaves, link=link, binding=binding)
    return {"metric_terms": terms, "metric_filters": translated, "metric_meta": meta}


async def resolve_metric_projection_async(
    object_type_name: str,
    metric_names: list[str],
    *,
    datasource_group_id: str = "",
    member_datasource_ids: list[str] | None = None,
    user_id: str = "",
    semantic_config_id: str | None = None,
) -> dict[str, Any]:
    """解析「对象类型 + 指标名」为可执行投影参数；不可执行时抛 ObjectMetricNotExecutableError。"""
    if not metric_names:
        raise ObjectMetricError("至少需要一个指标名")
    detail = await ontology_service.get_object_type_async(_text(object_type_name))
    if detail is None:
        raise ObjectMetricError(f"本体对象类型不存在: {object_type_name}")
    binding = detail.get("source_binding")
    if not isinstance(binding, dict) or not binding.get("dataset_id"):
        raise ObjectMetricNotExecutableError("对象类型未绑定 Dataset，不能按指标聚合")
    dataset = await logical_dataset_service.get_dataset_async_by_id_async(int(binding["dataset_id"]))
    if dataset is None:
        raise ObjectMetricNotExecutableError("对象类型绑定的 Dataset 不存在")
    group_id = _resolve_group_id(dataset, datasource_group_id, member_datasource_ids)
    if not group_id:
        raise ObjectMetricNotExecutableError("无法确定数据源组，不能按指标聚合")
    link = await dataset_semantic_link_service.get_active_link_async(
        int(dataset["id"]), group_id, semantic_config_id=semantic_config_id
    )
    if link is None:
        raise ObjectMetricNotExecutableError("对象类型绑定的 Dataset 未关联语义模型")
    if _text(link.get("lifecycle_status")) != "active":
        raise ObjectMetricNotExecutableError("Dataset ↔ 语义模型关联未启用（lifecycle_status 需为 active）")
    try:
        config = await dataset_semantic_link_service.load_config_for_link_async(link, user_id=user_id)
    except DatasetSemanticLinkInvalidError:
        config = None
    if config is None:
        raise ObjectMetricNotExecutableError("关联的语义模型不存在或不可访问")
    resolved = resolve_metric_expressions(metric_names, config=config, link=link, binding=binding)
    table = _text(link.get("table_name"))
    mismatched = [
        term["name"]
        for term in resolved["metric_terms"]
        if _text(term.get("table")) and _text(term.get("table")) != table
    ]
    if mismatched:
        raise ObjectMetricNotExecutableError(f"指标归属表与 Dataset 物理表不一致，暂不支持: {mismatched}")
    resolved["link"] = link
    resolved["config"] = config
    resolved["dataset"] = dataset
    resolved["datasource_group_id"] = group_id
    return resolved


async def load_object_metrics_map_async(
    object_type_names: list[str],
    *,
    datasource_group_id: str = "",
    user_id: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """批量加载对象类型指标清单（目录曝光用；失败降级为空，不阻塞会话）。"""
    result: dict[str, list[dict[str, Any]]] = {}
    for name in object_type_names:
        try:
            data = await list_object_metrics_async(name, datasource_group_id=datasource_group_id, user_id=user_id)
            result[_text(name)] = data.get("metrics") or []
        except Exception as exc:  # noqa: BLE001 - 目录增强信息，失败不影响主体
            logger.warning("加载对象类型指标清单失败: object_type=%s error=%s", name, exc)
            result[_text(name)] = []
    return result


__all__ = [
    "ObjectMetricError",
    "ObjectMetricNotExecutableError",
    "list_object_metrics_async",
    "load_object_metrics_map_async",
    "resolve_metric_expressions",
    "resolve_metric_projection_async",
    "translate_metric_filters",
]
