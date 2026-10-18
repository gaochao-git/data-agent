"""本体对象定位器（``locate_object`` action 的纯翻译层）。

把「对象名列表」翻译成语义模型定位报告：对象 → 绑定的语义模型（含快照文件名）、
属性 → 维度对照、对象关系 → 可导航的关联清单。定位是导航能力，不执行查询——
查询仍由 AI 依据报告走 ``execute_query``，语义模型层是唯一编译执行权威。

定位的存在动机：省掉 AI 为找一个模型而 grep/精读模型 YAML 的猜测轮次。
可定位范围结构性收敛：传入 bundle 只含本轮启用语义模型绑定的对象（与本体目录
物化同源取数），对象不在 bundle 即列入 not_found 并给出可定位候选，不泄露全量
本体。报告组装是纯函数：bundle/模型名/文件名映射均由调用方传入，不触库；
开关判断（``object_locator_enabled``）读环境变量，供工具层与提示词层共用。
"""

from __future__ import annotations

import os
from typing import Any, NamedTuple

_MAX_LOCATE_OBJECTS = 10
_MAX_ERROR_CANDIDATES = 20
_MAX_RELATIONS_PER_OBJECT = 12

_TRUTHY = {"1", "true", "yes", "on"}


def object_locator_enabled() -> bool:
    """本体对象定位开关（env 控制，默认关闭）。"""
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


def _dedupe_object_names(object_names: list[str]) -> list[str]:
    """入参对象名去空去重保序；全空或超单次上限直接报错。"""
    requested: list[str] = []
    seen: set[str] = set()
    for item in object_names:
        name = str(item or "").strip()
        if name and name not in seen:
            seen.add(name)
            requested.append(name)
    if not requested:
        raise ValueError("objects 不能为空：请传入至少一个本体对象 API 名")
    if len(requested) > _MAX_LOCATE_OBJECTS:
        raise ValueError(f"objects 单次最多定位 {_MAX_LOCATE_OBJECTS} 个对象，收到 {len(requested)} 个")
    return requested


class _BundleIndexes(NamedTuple):
    """bundle 一次遍历得到的定位索引。"""

    objects_by_name: dict[str, dict[str, Any]]
    properties_by_object: dict[int, list[dict[str, Any]]]
    relations_by_object: dict[int, list[dict[str, Any]]]
    mappings_by_object: dict[int, list[dict[str, Any]]]
    object_names_by_id: dict[int, str]


def _objects_by_name(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str((obj or {}).get("object_name") or "").strip(): (obj or {})
        for obj in bundle.get("objects") or []
        if str((obj or {}).get("object_name") or "").strip()
    }


def _group_by_object_id(items: Any) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in items or []:
        grouped.setdefault(int((item or {}).get("object_id") or 0), []).append(item or {})
    return grouped


def _relations_by_object(bundle: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """关系按两端对象各挂一份（对象视角取对端 + 口语化描述）。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for relation in bundle.get("relations") or []:
        grouped.setdefault(int((relation or {}).get("from_object_id") or 0), []).append(relation or {})
        grouped.setdefault(int((relation or {}).get("to_object_id") or 0), []).append(relation or {})
    return grouped


def _object_names_by_id(bundle: dict[str, Any]) -> dict[int, str]:
    return {
        int((obj or {}).get("id") or 0): str((obj or {}).get("object_name") or "")
        for obj in bundle.get("objects") or []
    }


def _bundle_indexes(bundle: dict[str, Any]) -> _BundleIndexes:
    return _BundleIndexes(
        objects_by_name=_objects_by_name(bundle),
        properties_by_object=_group_by_object_id(bundle.get("properties")),
        relations_by_object=_relations_by_object(bundle),
        mappings_by_object=_group_by_object_id(bundle.get("mappings")),
        object_names_by_id=_object_names_by_id(bundle),
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
    """对象的全部有效绑定（可选按模型过滤），按 config_id 升序。"""
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
    relations: list[dict[str, Any]],
    config_filter: str,
    config_names: dict[str, str] | None,
    model_files: dict[str, str] | None,
    object_names_by_id: dict[int, str],
    dimension_names: dict[str, set[str]] | None,
) -> dict[str, Any]:
    """单个命中对象的定位条目。"""
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
        "object": str(obj.get("object_name") or ""),
        "display_name": str(obj.get("display_name") or ""),
        "status": str(obj.get("status") or ""),
        "properties": _object_properties(properties),
        "bindings": bindings,
        "relations": _object_relations(
            relations,
            obj_id,
            object_names_by_id,
            property_names=property_names,
            mapped_attrs=mapped_attrs,
        ),
    }


def build_object_locator_report(
    bundle: dict[str, Any],
    *,
    object_names: list[str],
    config_names: dict[str, str] | None = None,
    model_files: dict[str, str] | None = None,
    semantic_config_id: str | None = None,
    dimension_names: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """按对象名批量生成语义模型定位报告（纯函数，不触库）。

    - ``object_names``：1..``_MAX_LOCATE_OBJECTS`` 个对象 API 名，去重保序；
    - ``config_names`` / ``model_files``：semantic_config_id → 模型显示名 / 快照
      文件名（纯展示索引，缺省留空，不影响定位结论）；
    - ``semantic_config_id``：可选，只保留该模型的绑定（多绑定对象据此过滤）；
    - ``dimension_names``：semantic_config_id → 当前维度名集合（调用方从启用
      模型 runtime configs 构建）。提供时对映射右值做失修比对并标注
      ``stale_dimensions``；缺省不标（无比对基准时保持静默，不误报）。

    返回 ``{"objects": [命中对象的定位条目], "not_found": [未命中及候选]}``。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    requested = _dedupe_object_names(object_names)
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
                    "object": name,
                    "reason": "对象不存在或未绑定本轮启用语义模型"
                    if available
                    else "当前启用语义模型没有绑定任何本体对象",
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
                properties=indexes.properties_by_object.get(obj_id, []),
                relations=indexes.relations_by_object.get(obj_id, []),
                config_filter=config_filter,
                config_names=config_names,
                model_files=model_files,
                object_names_by_id=indexes.object_names_by_id,
                dimension_names=dimension_names,
            )
        )
    return {"objects": located, "not_found": not_found}


def _relation_entry(
    relation: dict[str, Any],
    obj_id: int,
    object_names_by_id: dict[int, str],
    *,
    property_names: set[str] | None = None,
    mapped_attrs: set[str] | None = None,
) -> dict[str, Any] | None:
    """单条关系 → 对端对象视角条目；对端对象不可知时丢弃。

    失修标注：本对象侧（from 或 to）的关系列引用的属性不存在（模型/目录
    漂移）或未映射到任何启用模型维度（翻译断链）时，标 ``stale`` + 原因——
    该关系的 join 路径当前不可用，AI 不应按此 hint 拼 SQL。对端侧由对端对象
    自己的定位条目负责（同一条关系挂在两端对象下）。
    """
    from_id = int(relation.get("from_object_id") or 0)
    to_id = int(relation.get("to_object_id") or 0)
    is_from = from_id == obj_id
    other = object_names_by_id.get(to_id if is_from else from_id, "")
    if not other:
        return None
    verbalize = str(relation.get("from_verbalize" if is_from else "to_verbalize") or "")
    entry: dict[str, Any] = {
        "relation": str(relation.get("relation_name") or ""),
        "related_object": other,
        "direction": "from" if is_from else "to",
        "multiplicity": str(relation.get("multiplicity") or ""),
    }
    if verbalize:
        entry["verbalize"] = verbalize
    own_columns = relation.get("from_columns" if is_from else "to_columns") or []
    if property_names is not None:
        broken = [
            attr
            for attr in (str(column).strip() for column in own_columns)
            if attr and (attr not in property_names or (mapped_attrs is not None and attr not in mapped_attrs))
        ]
        if broken:
            entry["stale"] = True
            entry["stale_reason"] = f"本侧关系列未映射或不存在：{('、'.join(broken))}"
    return entry


def _object_relations(
    relations: list[dict[str, Any]],
    obj_id: int,
    object_names_by_id: dict[int, str],
    *,
    property_names: set[str] | None = None,
    mapped_attrs: set[str] | None = None,
) -> list[dict[str, Any]]:
    """对象的关系条目（对端对象视角），超出单对象上限即截断。"""
    entries: list[dict[str, Any]] = []
    for relation in relations:
        entry = _relation_entry(
            relation,
            obj_id,
            object_names_by_id,
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
        "需要模型完整口径（指标/周期/relationships）时同样 file_read 精读。跨对象关联在 SQL 模式下用模型 relationships 完成。"
    )


def build_object_list_report(
    bundle: dict[str, Any],
    *,
    config_names: dict[str, str] | None = None,
    model_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """列出可定位本体对象（``list_objects`` action 的轻量清单，纯函数，不触库）。

    与 ``build_object_locator_report`` 的分层：清单只含「对象名/显示名/描述 +
    对象→绑定模型」一跳（模型 id/名/快照文件名），**不含**属性、映射对照与
    关系——详情一律走 locate_object。bundle 与 locate 同源（本轮启用语义模型
    绑定的对象，不泄露全量本体），条目按对象名升序。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    config_names = config_names or {}
    model_files = model_files or {}
    mappings_by_object: dict[int, list[dict[str, Any]]] = {}
    for mapping in bundle.get("mappings") or []:
        mapping = mapping or {}
        mappings_by_object.setdefault(int(mapping.get("object_id") or 0), []).append(mapping)
    entries: list[dict[str, Any]] = []
    for obj in sorted(bundle.get("objects") or [], key=lambda item: str((item or {}).get("object_name") or "")):
        obj = obj or {}
        obj_name = str(obj.get("object_name") or "").strip()
        if not obj_name:
            continue
        models: list[dict[str, str]] = []
        for mapping in mappings_by_object.get(int(obj.get("id") or 0), []):
            config_id = str(mapping.get("semantic_config_id") or "").strip()
            if not config_id:
                continue
            models.append(
                {
                    "semantic_config_id": config_id,
                    "model_name": str(config_names.get(config_id) or ""),
                    "model_file": str(model_files.get(config_id) or ""),
                }
            )
        models.sort(key=lambda item: item["semantic_config_id"])
        entries.append(
            {
                "object": obj_name,
                "display_name": str(obj.get("display_name") or ""),
                "description": str(obj.get("description") or ""),
                "models": models,
            }
        )
    return {"count": len(entries), "objects": entries}


def build_object_list_usage_hint() -> str:
    """对象清单的使用说明（轻量列表 → 详情导航 → 查询）。"""
    return (
        "清单只含对象名与绑定模型。单对象取数：直接 execute_query（DSL 传 models 里的 semantic_config_id，"
        "维度名先 file_read workspace 的 semantic/<model_file> 确认口径）；需要属性→维度对照、对象关系"
        "或失修状态时，调 locate_object（objects 传对象 API 名）获取定位报告。"
    )
