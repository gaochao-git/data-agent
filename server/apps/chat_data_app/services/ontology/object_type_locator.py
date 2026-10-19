"""本体对象类型定位器（``locate_object_type`` action 的纯翻译层）。

把「对象类型名列表」翻译成语义模型定位报告：对象类型 → 绑定的语义模型（含快照文件名）、
属性 → 维度对照、对象类型间关系类型 → 可导航的关联清单。定位是导航能力，不执行查询——
查询仍由 AI 依据报告走 ``execute_query``，语义模型层是唯一编译执行权威。

定位的存在动机：省掉 AI 为找一个模型而 grep/精读模型 YAML 的猜测轮次。
可定位范围结构性收敛：传入 bundle 只含本轮启用语义模型绑定的对象类型（与本体目录
物化同源取数），对象类型不在 bundle 即列入 not_found 并给出可定位候选，不泄露全量
本体。报告组装是纯函数：bundle/模型名/文件名映射均由调用方传入，不触库；
开关判断（``object_type_locator_enabled``）读环境变量，供工具层与提示词层共用。
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple

_MAX_LOCATE_OBJECTS = 10
_MAX_ERROR_CANDIDATES = 20
_MAX_RELATIONS_PER_OBJECT = 12

_TRUTHY = {"1", "true", "yes", "on"}


def object_type_locator_enabled() -> bool:
    """本体对象类型定位开关（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_LOCATE_ENABLED") or "").strip().lower() in _TRUTHY


def _candidate_summary(names: list[str]) -> str:
    shown = "、".join(names[:_MAX_ERROR_CANDIDATES])
    if len(names) > _MAX_ERROR_CANDIDATES:
        shown += f" 等 {len(names)} 个"
    return shown


def _clean_mapping(raw: Any) -> dict[str, str]:
    """映射行的「属性 API 名 → 模型维度名」字典（空/非法一律按空映射处理）。"""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value).strip() for key, value in raw.items() if str(key).strip() and str(value).strip()
    }


def _dedupe_object_type_names(object_type_names: list[str]) -> list[str]:
    """入参对象类型名去空去重保序；全空或超单次上限直接报错。"""
    requested: list[str] = []
    seen: set[str] = set()
    for item in object_type_names:
        name = str(item or "").strip()
        if name and name not in seen:
            seen.add(name)
            requested.append(name)
    if not requested:
        raise ValueError("object_types 不能为空：请传入至少一个本体对象类型 API 名")
    if len(requested) > _MAX_LOCATE_OBJECTS:
        raise ValueError(f"object_types 单次最多定位 {_MAX_LOCATE_OBJECTS} 个对象类型，收到 {len(requested)} 个")
    return requested


class _BundleIndexes(NamedTuple):
    """bundle 一次遍历得到的定位索引。"""

    objects_by_name: dict[str, dict[str, Any]]
    properties_by_object_type: dict[int, list[dict[str, Any]]]
    relations_by_object: dict[int, list[dict[str, Any]]]
    mappings_by_object: dict[int, list[dict[str, Any]]]
    object_type_names_by_id: dict[int, str]


def _objects_by_name(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str((obj or {}).get("object_type_name") or "").strip(): (obj or {})
        for obj in bundle.get("object_types") or []
        if str((obj or {}).get("object_type_name") or "").strip()
    }


def _group_by_object_type_id(items: Any) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in items or []:
        grouped.setdefault(int((item or {}).get("object_type_id") or 0), []).append(item or {})
    return grouped


def _relations_by_object(bundle: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """关系类型按两端对象类型各挂一份（对象类型视角取对端 + 口语化描述）。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for relation in bundle.get("relation_types") or []:
        grouped.setdefault(int((relation or {}).get("from_object_type_id") or 0), []).append(relation or {})
        grouped.setdefault(int((relation or {}).get("to_object_type_id") or 0), []).append(relation or {})
    return grouped


def _object_type_names_by_id(bundle: dict[str, Any]) -> dict[int, str]:
    return {
        int((obj or {}).get("id") or 0): str((obj or {}).get("object_type_name") or "")
        for obj in bundle.get("object_types") or []
    }


def _bundle_indexes(bundle: dict[str, Any]) -> _BundleIndexes:
    return _BundleIndexes(
        objects_by_name=_objects_by_name(bundle),
        properties_by_object_type=_group_by_object_type_id(bundle.get("properties")),
        relations_by_object=_relations_by_object(bundle),
        mappings_by_object=_group_by_object_type_id(bundle.get("mappings")),
        object_type_names_by_id=_object_type_names_by_id(bundle),
    )


def _object_binding(
    mapping: dict[str, Any],
    config_names: dict[str, str] | None,
    model_files: dict[str, str] | None,
    dimension_names: dict[str, set[str]] | None,
) -> dict[str, Any] | None:
    """单条映射 → 绑定条目；缺 config_id 或映射为空时丢弃。

    失修标注（一致性巡检的消费场景 A）：column_mappings 右值维度不在绑定模型
    当前维度清单中（维度被改名/删除）时逐条标 stale——模型可随时漂移，绑定
    守护只拦删除/改名单点动作，报告必须在消费点让 AI 可见失修，否则地图
    静默撒谎。
    """
    column_mappings = _clean_mapping(mapping.get("column_mappings"))
    config_id = str(mapping.get("semantic_config_id") or "").strip()
    if not config_id or not column_mappings:
        return None
    dims = (dimension_names or {}).get(config_id)
    stale_dimensions = [
        {"property": prop, "dimension": dim}
        for prop, dim in sorted(column_mappings.items())
        if dims is not None and dim not in dims
    ]
    return {
        "semantic_config_id": config_id,
        "model_name": str((config_names or {}).get(config_id) or ""),
        "model_file": str((model_files or {}).get(config_id) or ""),
        "column_mappings": dict(sorted(column_mappings.items())),
        "healthy": not stale_dimensions,
        "stale_dimensions": stale_dimensions,
    }


def _object_bindings(
    mappings: list[dict[str, Any]],
    *,
    config_filter: str,
    config_names: dict[str, str] | None,
    model_files: dict[str, str] | None,
    dimension_names: dict[str, set[str]] | None,
) -> list[dict[str, Any]]:
    """对象类型的全部有效绑定（可选按模型过滤），按 config_id 升序。"""
    bindings = [
        binding
        for binding in (_object_binding(mapping, config_names, model_files, dimension_names) for mapping in mappings)
        if binding
    ]
    if config_filter:
        bindings = [item for item in bindings if item["semantic_config_id"] == config_filter]
    bindings.sort(key=lambda item: item["semantic_config_id"])
    return bindings


def _object_properties(properties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """属性清单（按属性名排序），空名属性丢弃。"""
    return [
        {
            "name": str(prop.get("property_name") or ""),
            "display_name": str(prop.get("display_name") or ""),
            "value_type": str(prop.get("value_type") or ""),
            "is_identifier": bool(prop.get("is_identifier")),
        }
        for prop in sorted(
            properties,
            key=lambda item: str(item.get("property_name") or ""),
        )
        if str(prop.get("property_name") or "")
    ]


def _locate_entry(
    obj: dict[str, Any],
    *,
    obj_id: int,
    mappings: list[dict[str, Any]],
    properties: list[dict[str, Any]],
    relation_types: list[dict[str, Any]],
    config_filter: str,
    config_names: dict[str, str] | None,
    model_files: dict[str, str] | None,
    object_type_names_by_id: dict[int, str],
    dimension_names: dict[str, set[str]] | None,
) -> dict[str, Any]:
    """单个命中对象类型的定位条目。"""
    bindings = _object_bindings(
        mappings,
        config_filter=config_filter,
        config_names=config_names,
        model_files=model_files,
        dimension_names=dimension_names,
    )
    property_names = {str(prop.get("property_name") or "").strip() for prop in properties}
    property_names.discard("")
    mapped_attrs: set[str] = set()
    for binding in bindings:
        mapped_attrs.update(binding["column_mappings"])
    return {
        "object_type": str(obj.get("object_type_name") or ""),
        "display_name": str(obj.get("display_name") or ""),
        "status": str(obj.get("status") or ""),
        "properties": _object_properties(properties),
        "bindings": bindings,
        "relation_types": _object_relations(
            relation_types,
            obj_id,
            object_type_names_by_id,
            property_names=property_names,
            mapped_attrs=mapped_attrs,
        ),
    }


def build_object_type_locator_report(
    bundle: dict[str, Any],
    *,
    object_type_names: list[str],
    config_names: dict[str, str] | None = None,
    model_files: dict[str, str] | None = None,
    semantic_config_id: str | None = None,
    dimension_names: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """按对象类型名批量生成语义模型定位报告（纯函数，不触库）。

    - ``object_type_names``：1..``_MAX_LOCATE_OBJECTS`` 个对象类型 API 名，去重保序；
    - ``config_names`` / ``model_files``：semantic_config_id → 模型显示名 / 快照
      文件名（纯展示索引，缺省留空，不影响定位结论）；
    - ``semantic_config_id``：可选，只保留该模型的绑定（多绑定对象类型据此过滤）；
    - ``dimension_names``：semantic_config_id → 当前维度名集合（调用方从启用
      模型 runtime configs 构建）。提供时对映射右值做失修比对并标注
      ``stale_dimensions``；缺省不标（无比对基准时保持静默，不误报）。

    返回 ``{"object_types": [命中对象类型的定位条目], "not_found": [未命中及候选]}``。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    requested = _dedupe_object_type_names(object_type_names)
    indexes = _bundle_indexes(bundle)

    config_filter = str(semantic_config_id or "").strip()
    located: list[dict[str, Any]] = []
    not_found: list[dict[str, Any]] = []
    available = sorted(indexes.objects_by_name)
    for name in requested:
        obj = indexes.objects_by_name.get(name)
        if not obj:
            not_found.append(
                {
                    "object_type": name,
                    "reason": "对象类型不存在或未绑定本轮启用语义模型"
                    if available
                    else "当前启用语义模型没有绑定任何本体对象类型",
                    "available": _candidate_summary(available),
                }
            )
            continue
        obj_id = int(obj.get("id") or 0)
        located.append(
            _locate_entry(
                obj,
                obj_id=obj_id,
                mappings=indexes.mappings_by_object.get(obj_id, []),
                properties=indexes.properties_by_object_type.get(obj_id, []),
                relation_types=indexes.relations_by_object.get(obj_id, []),
                config_filter=config_filter,
                config_names=config_names,
                model_files=model_files,
                object_type_names_by_id=indexes.object_type_names_by_id,
                dimension_names=dimension_names,
            )
        )
    return {"object_types": located, "not_found": not_found}


def _stale_reason(
    own_columns: list[Any],
    property_names: set[str],
    mapped_attrs: set[str] | None,
) -> str | None:
    """本侧关系类型列的失修原因；全部列健康时返回 None。

    属性不存在（模型/目录漂移）或未映射到任何启用模型维度（翻译断链）均视为失修。
    """
    broken: list[str] = []
    for column in own_columns or []:
        attr = str(column).strip()
        if not attr:
            continue
        if attr not in property_names or (mapped_attrs is not None and attr not in mapped_attrs):
            broken.append(attr)
    return f"本侧关系类型列未映射或不存在：{('、'.join(broken))}" if broken else None


def _relation_entry(
    relation: dict[str, Any],
    obj_id: int,
    object_type_names_by_id: dict[int, str],
    *,
    property_names: set[str] | None = None,
    mapped_attrs: set[str] | None = None,
) -> dict[str, Any] | None:
    """单条关系类型 → 对端对象类型视角条目；对端对象类型不可知时丢弃。

    失修标注：本对象类型侧（from 或 to）的关系类型列引用的属性不存在（模型/目录
    漂移）或未映射到任何启用模型维度（翻译断链）时，标 ``stale`` + 原因——
    该关系类型的 join 路径当前不可用，AI 不应按此 hint 拼 SQL。对端侧由对端对象类型
    自己的定位条目负责（同一条关系类型挂在两端对象类型下）。
    """
    from_id = int(relation.get("from_object_type_id") or 0)
    to_id = int(relation.get("to_object_type_id") or 0)
    if from_id == obj_id:
        other_id, direction, verbalize_key, columns_key = to_id, "from", "from_verbalize", "from_columns"
    else:
        other_id, direction, verbalize_key, columns_key = from_id, "to", "to_verbalize", "to_columns"
    other = object_type_names_by_id.get(other_id, "")
    if not other:
        return None
    entry: dict[str, Any] = {
        "relation": str(relation.get("relation_type_name") or ""),
        "related_object": other,
        "direction": direction,
        "multiplicity": str(relation.get("multiplicity") or ""),
    }
    verbalize = str(relation.get(verbalize_key) or "")
    if verbalize:
        entry["verbalize"] = verbalize
    if property_names is not None:
        stale = _stale_reason(relation.get(columns_key) or [], property_names, mapped_attrs)
        if stale:
            entry["stale"] = True
            entry["stale_reason"] = stale
    return entry


def _object_relations(
    relation_types: list[dict[str, Any]],
    obj_id: int,
    object_type_names_by_id: dict[int, str],
    *,
    property_names: set[str] | None = None,
    mapped_attrs: set[str] | None = None,
) -> list[dict[str, Any]]:
    """对象类型的关系类型条目（对端对象类型视角），超出单对象类型上限即截断。"""
    entries: list[dict[str, Any]] = []
    for relation in relation_types:
        entry = _relation_entry(
            relation,
            obj_id,
            object_type_names_by_id,
            property_names=property_names,
            mapped_attrs=mapped_attrs,
        )
        if not entry:
            continue
        entries.append(entry)
        if len(entries) >= _MAX_RELATIONS_PER_OBJECT:
            break
    return entries


def build_locator_usage_hint() -> str:
    """定位报告的使用说明（引导 AI 从导航进入既有查询路径）。"""
    return (
        "查询执行走 data_analyzing_tool 的 execute_query（DSL 传上方绑定的 semantic_config_id；"
        "维度名取对应 binding 的 column_mappings 值）；binding 的 healthy=false 或 stale_dimensions "
        "非空、relation 带 stale=true 时表示该条目已失修（维度可能被改名/删除），不要使用失修条目，"
        "改用 file_read workspace 的 semantic/<model_file> 精读模型 YAML 核实当前口径。"
        "需要模型完整口径（指标/周期/relation-typeships）时同样 file_read 精读。跨对象类型关联在 SQL 模式下用模型 relationships 完成。"
    )


def _mappings_index(bundle: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """bundle.mappings 按对象类型分组（对象类型 → 映射列表）。"""
    index: dict[int, list[dict[str, Any]]] = {}
    for mapping in bundle.get("mappings") or []:
        mapping = mapping or {}
        index.setdefault(int(mapping.get("object_type_id") or 0), []).append(mapping)
    return index


def _object_binding_models(
    mappings: list[dict[str, Any]],
    config_names: dict[str, str],
    model_files: dict[str, str],
) -> list[dict[str, str]]:
    """对象类型的绑定模型条目（id/名/快照文件名），按模型 id 升序。"""
    models = [
        {
            "semantic_config_id": config_id,
            "model_name": str(config_names.get(config_id) or ""),
            "model_file": str(model_files.get(config_id) or ""),
        }
        for mapping in mappings
        if (config_id := str(mapping.get("semantic_config_id") or "").strip())
    ]
    models.sort(key=lambda item: item["semantic_config_id"])
    return models


def build_object_list_report(
    bundle: dict[str, Any],
    *,
    config_names: dict[str, str] | None = None,
    model_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """列出可定位本体对象类型（``list_object_types`` action 的轻量清单，纯函数，不触库）。

    与 ``build_object_type_locator_report`` 的分层：清单只含「对象类型名/显示名/描述 +
    对象类型→绑定模型」一跳（模型 id/名/快照文件名），**不含**属性、映射对照与
    关系类型——详情一律走 locate_object_type。bundle 与 locate 同源（本轮启用语义模型
    绑定的对象类型，不泄露全量本体），条目按对象类型名升序。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    config_names = config_names or {}
    model_files = model_files or {}
    mappings_by_object = _mappings_index(bundle)
    entries: list[dict[str, Any]] = []
    for obj in sorted(
        bundle.get("object_types") or [], key=lambda item: str((item or {}).get("object_type_name") or "")
    ):
        obj = obj or {}
        obj_name = str(obj.get("object_type_name") or "").strip()
        if not obj_name:
            continue
        entries.append(
            {
                "object_type": obj_name,
                "display_name": str(obj.get("display_name") or ""),
                "description": str(obj.get("description") or ""),
                "models": _object_binding_models(
                    mappings_by_object.get(int(obj.get("id") or 0), []), config_names, model_files
                ),
            }
        )
    return {"count": len(entries), "object_types": entries}


def build_object_list_usage_hint() -> str:
    """对象类型清单的使用说明（轻量列表 → 详情导航 → 查询）。"""
    return (
        "清单只含对象类型名与绑定模型。单对象类型取数：直接 execute_query（DSL 传 models 里的 semantic_config_id，"
        "维度名先 file_read workspace 的 semantic/<model_file> 确认口径）；需要属性→维度对照、对象类型间关系类型"
        "或失修状态时，调 locate_object_type（object_types 传对象类型 API 名）获取定位报告。"
    )
