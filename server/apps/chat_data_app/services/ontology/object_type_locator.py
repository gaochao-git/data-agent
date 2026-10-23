"""本体对象类型导航器（``ontology_tool`` 的纯翻译层）。

2026-10-23 起对象取数统一走 Logical Dataset source binding（语义模型映射表下线），
导航报告随之只依赖 Dataset 数据源绑定，**不再读取语义模型/维度**：

- 对象类型 → Dataset / 物理表 / member 数据源 / 属性→逻辑列→物理列映射；
- 关系类型 → 两端对象类型的物理连接列（属性经 Dataset 列映射翻译）；
- Action Type 定义清单 → 动作参数与目标对象类型。

报告组装是纯函数：bundle 由调用方从 ``query_mode.load_domain_ontology_bundle_async``
取得（已按当前数据源组收敛），不触库；开关判断（``object_type_locator_enabled``）
读环境变量，供工具层与提示词层共用。
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple

from .query_mode import translate_binding_columns

_MAX_LOCATE_OBJECTS = 10
_MAX_ERROR_CANDIDATES = 20
_MAX_RELATIONS_PER_OBJECT = 12

_TRUTHY = {"1", "true", "yes", "on"}


def object_type_locator_enabled() -> bool:
    """本体对象类型导航开关（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED") or "").strip().lower() in _TRUTHY


def _text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_summary(names: list[str]) -> str:
    shown = "、".join(names[:_MAX_ERROR_CANDIDATES])
    if len(names) > _MAX_ERROR_CANDIDATES:
        shown += f" 等 {len(names)} 个"
    return shown


def _dedupe_object_type_names(object_type_names: list[str]) -> list[str]:
    """入参对象类型名去空去重保序；全空或超单次上限直接报错。"""
    requested: list[str] = []
    seen: set[str] = set()
    for item in object_type_names:
        name = _text(item)
        if name and name not in seen:
            seen.add(name)
            requested.append(name)
    if not requested:
        raise ValueError("object_types 不能为空：请传入至少一个本体对象类型 API 名")
    if len(requested) > _MAX_LOCATE_OBJECTS:
        raise ValueError(f"object_types 单次最多定位 {_MAX_LOCATE_OBJECTS} 个对象类型，收到 {len(requested)} 个")
    return requested


class _BundleIndexes(NamedTuple):
    """bundle 一次遍历得到的导航索引。"""

    objects_by_name: dict[str, dict[str, Any]]
    properties_by_object_type: dict[int, list[dict[str, Any]]]
    relations_by_object: dict[int, list[dict[str, Any]]]
    object_type_names_by_id: dict[int, str]
    bindings_by_object: dict[int, dict[str, Any]]


def _group_by_object_type_id(items: Any) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in items or []:
        if isinstance(item, dict):
            grouped.setdefault(int(item.get("object_type_id") or 0), []).append(item)
    return grouped


def _relations_by_object(bundle: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """关系类型按两端对象类型各挂一份（对象类型视角取对端 + 口语化描述）。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for relation in bundle.get("relation_types") or []:
        if not isinstance(relation, dict):
            continue
        grouped.setdefault(int(relation.get("from_object_type_id") or 0), []).append(relation)
        grouped.setdefault(int(relation.get("to_object_type_id") or 0), []).append(relation)
    return grouped


def _bundle_indexes(bundle: dict[str, Any]) -> _BundleIndexes:
    objects = [obj for obj in bundle.get("object_types") or [] if isinstance(obj, dict)]
    return _BundleIndexes(
        objects_by_name={
            _text(obj.get("object_type_name")): obj for obj in objects if _text(obj.get("object_type_name"))
        },
        properties_by_object_type=_group_by_object_type_id(bundle.get("properties")),
        relations_by_object=_relations_by_object(bundle),
        object_type_names_by_id={int(obj.get("id") or 0): _text(obj.get("object_type_name")) for obj in objects},
        bindings_by_object={int(obj.get("id") or 0): obj.get("source_binding") or {} for obj in objects},
    )


def _source_binding_entry(binding: dict[str, Any] | None) -> dict[str, Any]:
    """对象类型的数据源绑定条目（导航可读化：Dataset/物理表/member/列映射）。"""
    binding = binding or {}
    return {
        "dataset": _text(binding.get("dataset_name")),
        "dataset_display_name": _text(binding.get("dataset_display_name")),
        "physical_table": _text(binding.get("physical_table")),
        "members": [
            _text(item.get("member_datasource_id"))
            for item in binding.get("members") or []
            if isinstance(item, dict) and _text(item.get("member_datasource_id"))
        ],
        "column_mappings": binding.get("column_mappings") or {},
        "columns": binding.get("columns") or {},
    }


def _object_properties(properties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """属性清单（按属性名排序），空名属性丢弃；派生属性带 derived 标记。"""
    entries: list[dict[str, Any]] = []
    for prop in sorted(properties, key=lambda item: _text(item.get("property_name"))):
        name = _text(prop.get("property_name"))
        if not name:
            continue
        entry = {
            "name": name,
            "display_name": _text(prop.get("display_name")),
            "value_type": _text(prop.get("value_type")),
            "is_identifier": bool(prop.get("is_identifier")),
        }
        derivation = prop.get("derivation")
        if isinstance(derivation, dict) and derivation:
            entry["derived"] = True
            entry["derivation"] = {key: value for key, value in derivation.items() if value}
        entries.append(entry)
    return entries


def _relation_join(
    relation: dict[str, Any],
    indexes: _BundleIndexes,
) -> dict[str, Any]:
    """关系类型的物理连接列（Dataset 列映射单源翻译；未解析标 unresolved）。"""
    from_id = int(relation.get("from_object_type_id") or 0)
    to_id = int(relation.get("to_object_type_id") or 0)
    from_columns, from_unresolved = translate_binding_columns(
        relation.get("from_columns"), indexes.bindings_by_object.get(from_id)
    )
    to_columns, to_unresolved = translate_binding_columns(
        relation.get("to_columns"), indexes.bindings_by_object.get(to_id)
    )
    join: dict[str, Any] = {"from_columns": from_columns, "to_columns": to_columns}
    if from_unresolved or to_unresolved:
        join["unresolved"] = True
    return join


def _relation_entry(
    relation: dict[str, Any],
    obj_id: int,
    indexes: _BundleIndexes,
) -> dict[str, Any] | None:
    """单条关系类型 → 对端对象类型视角条目；对端对象类型不可知时丢弃。"""
    from_id = int(relation.get("from_object_type_id") or 0)
    to_id = int(relation.get("to_object_type_id") or 0)
    if from_id == obj_id:
        other_id, direction, verbalize_key = to_id, "from", "from_verbalize"
    else:
        other_id, direction, verbalize_key = from_id, "to", "to_verbalize"
    other = indexes.object_type_names_by_id.get(other_id, "")
    if not other:
        return None
    entry: dict[str, Any] = {
        "relation": _text(relation.get("relation_type_name")),
        "related_object": other,
        "direction": direction,
        "multiplicity": _text(relation.get("multiplicity")),
    }
    verbalize = _text(relation.get(verbalize_key))
    if verbalize:
        entry["verbalize"] = verbalize
    return entry


def _object_relations(
    relation_types: list[dict[str, Any]],
    obj_id: int,
    indexes: _BundleIndexes,
) -> list[dict[str, Any]]:
    """对象类型的关系类型条目（对端对象类型视角），超出单对象类型上限即截断。"""
    entries: list[dict[str, Any]] = []
    for relation in relation_types:
        entry = _relation_entry(relation, obj_id, indexes)
        if not entry:
            continue
        entries.append(entry)
        if len(entries) >= _MAX_RELATIONS_PER_OBJECT:
            break
    return entries


def _locate_entry(obj: dict[str, Any], indexes: _BundleIndexes) -> dict[str, Any]:
    """单个命中对象类型的定位条目（属性 + 数据源绑定 + 关系类型跳转）。"""
    obj_id = int(obj.get("id") or 0)
    return {
        "object_type": _text(obj.get("object_type_name")),
        "display_name": _text(obj.get("display_name")),
        "status": _text(obj.get("status")),
        "properties": _object_properties(indexes.properties_by_object_type.get(obj_id, [])),
        "source_binding": _source_binding_entry(indexes.bindings_by_object.get(obj_id)),
        "relation_types": _object_relations(indexes.relations_by_object.get(obj_id, []), obj_id, indexes),
    }


def _not_found_entry(name: str, available: list[str]) -> dict[str, Any]:
    """未命中对象类型条目（与 locate 同口径：给可定位候选）。"""
    return {
        "object_type": name,
        "reason": "对象类型不在当前业务域本体范围" if available else "当前业务域没有可用本体对象类型",
        "available": _candidate_summary(available),
    }


def build_object_type_locator_report(
    bundle: dict[str, Any],
    *,
    object_type_names: list[str],
) -> dict[str, Any]:
    """按对象类型名批量生成本体导航报告（纯函数，不触库）。

    - ``object_type_names``：1..``_MAX_LOCATE_OBJECTS`` 个对象类型 API 名，去重保序；
    - ``bundle``：``query_mode.load_domain_ontology_bundle_async`` 产物（已按数据源组收敛）。

    返回 ``{"object_types": [命中对象类型的定位条目], "not_found": [未命中及候选]}``。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    requested = _dedupe_object_type_names(object_type_names)
    indexes = _bundle_indexes(bundle)
    available = sorted(indexes.objects_by_name)
    located: list[dict[str, Any]] = []
    not_found: list[dict[str, Any]] = []
    for name in requested:
        obj = indexes.objects_by_name.get(name)
        if not obj:
            not_found.append(_not_found_entry(name, available))
            continue
        located.append(_locate_entry(obj, indexes))
    return {"object_types": located, "not_found": not_found}


def build_object_list_report(bundle: dict[str, Any]) -> dict[str, Any]:
    """列出当前业务域可导航对象类型（``list_object_types`` 的轻量清单，纯函数）。

    清单只含对象类型名/展示名/描述 + Dataset 数据源绑定一跳（Dataset/物理表/
    member 数量/属性数量），不含属性与关系类型详情——详情走 locate_object_type。
    条目按对象类型名升序。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    indexes = _bundle_indexes(bundle)
    entries: list[dict[str, Any]] = []
    for obj in sorted(bundle.get("object_types") or [], key=lambda item: _text((item or {}).get("object_type_name"))):
        obj = obj or {}
        obj_name = _text(obj.get("object_type_name"))
        if not obj_name:
            continue
        binding = _source_binding_entry(indexes.bindings_by_object.get(int(obj.get("id") or 0)))
        entries.append(
            {
                "object_type": obj_name,
                "display_name": _text(obj.get("display_name")),
                "description": _text(obj.get("description")),
                "dataset": binding["dataset"],
                "physical_table": binding["physical_table"],
                "member_datasource_ids": binding["members"],
                "property_count": len(indexes.properties_by_object_type.get(int(obj.get("id") or 0), [])),
            }
        )
    return {"count": len(entries), "object_types": entries}


def build_search_around_report(
    bundle: dict[str, Any],
    *,
    object_type: str,
    relation: str | None = None,
) -> dict[str, Any]:
    """search-around：以单个对象类型为中心沿关系「走一跳」的展开报告（纯函数）。

    每条 hop 给出：物理连接列（``join``，Dataset 列映射单源翻译）+ 对端对象类型的
    导航条目（``neighbor``，其 ``relations`` 只保留关系名提示下一跳）。
    ``relation`` 可选过滤（只展开指定关系名，精准控量）。
    返回 ``{"center", "hops", "not_found"}``：中心不可定位时 hops 为空并给候选。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    name = _text(object_type)
    if not name:
        raise ValueError("object_type 不能为空：请传入中心对象类型 API 名")
    indexes = _bundle_indexes(bundle)
    obj = indexes.objects_by_name.get(name)
    if not obj:
        return {"center": None, "hops": [], "not_found": [_not_found_entry(name, sorted(indexes.objects_by_name))]}
    obj_id = int(obj.get("id") or 0)
    relation_filter = _text(relation)
    hops: list[dict[str, Any]] = []
    for rel in indexes.relations_by_object.get(obj_id, []):
        hop = _search_around_hop(rel, obj_id, indexes, relation_filter)
        if not hop:
            continue
        hops.append(hop)
        if len(hops) >= _MAX_RELATIONS_PER_OBJECT:
            break
    return {
        "center": {"object_type": name, "display_name": _text(obj.get("display_name"))},
        "hops": hops,
        "not_found": [],
    }


def _search_around_hop(
    relation: dict[str, Any],
    obj_id: int,
    indexes: _BundleIndexes,
    relation_filter: str,
) -> dict[str, Any] | None:
    """单条关系的 hop 组装；未命中 relation 过滤或对端不可知时返回 None。"""
    entry = _relation_entry(relation, obj_id, indexes)
    if not entry or (relation_filter and entry["relation"] != relation_filter):
        return None
    hop: dict[str, Any] = {
        "relation": entry["relation"],
        "direction": entry["direction"],
        "multiplicity": entry["multiplicity"],
    }
    if entry.get("verbalize"):
        hop["verbalize"] = entry["verbalize"]
    hop["join"] = _relation_join(relation, indexes)
    neighbor = indexes.objects_by_name.get(entry["related_object"])
    hop["neighbor"] = _neighbor_entry(neighbor, indexes) if neighbor else None
    return hop


def _neighbor_entry(obj: dict[str, Any], indexes: _BundleIndexes) -> dict[str, Any]:
    """对端对象类型的导航条目；``relations`` 收敛为关系名列表（下一跳提示）。"""
    neighbor = _locate_entry(obj, indexes)
    neighbor["relations"] = [_text(item.get("relation")) for item in neighbor.pop("relation_types", [])]
    return neighbor


def build_locator_usage_hint() -> str:
    """定位报告的使用说明（导航 → 对象查询，不走语义层）。"""
    return (
        "source_binding 给出对象类型取数的 Dataset/物理表/member 与属性映射：column_mappings 是"
        "属性→逻辑列，columns 是逻辑列→物理列；查询用 ontology_tool 的 find_objects/aggregate_objects"
        "（member_datasource_ids 取 source_binding.members），跨对象关联用 search_around 的 join 物理列。"
        "对象查询不走语义模型 DSL/SQL，也不要回退语义查询。"
    )


def build_object_list_usage_hint() -> str:
    """对象类型清单的使用说明（轻量列表 → 详情导航 → 对象查询）。"""
    return (
        "清单只含对象类型与数据源绑定摘要。查询对象直接调 find_objects/aggregate_objects"
        "（member_datasource_ids 用清单的 member_datasource_ids）；需要属性、列映射或关系类型详情时"
        "调 locate_object_type，跨对象关联调 search_around。"
    )


def build_search_around_usage_hint() -> str:
    """search-around 报告的使用说明（一跳展开 → 直接 join → 继续展开再调用）。"""
    return (
        "每条 hop 的 join 给出物理连接列（from_columns/to_columns，Dataset 列映射翻译；unresolved=true 时"
        "回读对象类型 source_binding 确认）；neighbor 是对端对象的完整导航条目，继续展开时以它的 "
        "object_type 为中心再次调用 search_around。对象查询用 find_objects/aggregate_objects，"
        "不要回退语义模型查询。"
    )


__all__ = [
    "build_locator_usage_hint",
    "build_object_list_report",
    "build_object_list_usage_hint",
    "build_object_type_locator_report",
    "build_search_around_report",
    "build_search_around_usage_hint",
    "object_type_locator_enabled",
]
