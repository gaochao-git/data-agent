"""本体对象查询翻译器（``query_object`` action 的纯翻译层）。

把「对象级意图」（对象 API 名 + 本体属性谓词）翻译成语义模型 builder DSL，
SQL 由既有 ``SemanticBIQueryAdapter`` → ``SemanticSQLCompiler`` 链编译——本模块
不生成任何 SQL 字符串，转义/操作符白名单/行数上限全部由编译层负责。本体在本
链路中的职责是确定性路由与词汇翻译：对象 → 语义模型（column_mappings 的
semantic_config_id）、属性 → 模型维度（column_mappings 值）。

可查范围结构性收敛：传入 bundle 只含本轮启用语义模型绑定的对象（与
``materialize_ontology_catalog_async`` 同一取数口径），对象/属性不在 bundle 或
映射内即明确报错，不泄露全量本体。本模块为纯函数：bundle 由调用方加载传入，
不触库、不读环境变量（开关判断在工具层）。

DSL 形状对齐 ``SemanticBIQueryAdapter``（BI builder DSL）：
dimensions=[{"name": 维度名, "displayName": 属性名}]（displayName 透传为 SQL
结果列别名，返回列天然标注成本体属性）、filters 叶子 {"name", "operator",
"value"}（支持 {"and": [...]}/{"or": [...]} 嵌套树）、orderBy
[{"name", "direction"}]、limit/offset。dimensions-only 合法（无聚合不生成
GROUP BY，见 query_plan_compiler），即纯明细查询。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000
_MAX_HINT_RELATIONS = 8
_MAX_ERROR_CANDIDATES = 20

_TRUTHY = {"1", "true", "yes", "on"}

_FILTER_LEAF_KEYS = {"property", "operator", "value"}


def object_query_enabled() -> bool:
    """本体对象查询开关（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_QUERY_ENABLED") or "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class ObjectQueryPlan:
    """翻译结果：builder DSL + 供结果呈现的来源信息（字段顺序即输出顺序）。"""

    object_name: str
    object_display_name: str
    semantic_config_id: str
    dsl: dict[str, Any]
    columns: list[str]
    property_dimensions: dict[str, str]


def _candidate_summary(names: list[str]) -> str:
    shown = "、".join(names[:_MAX_ERROR_CANDIDATES])
    extra = len(names) - _MAX_ERROR_CANDIDATES
    if extra > 0:
        shown += f" 等 {len(names)} 个"
    return shown


def _find_object(bundle: dict[str, Any], object_name: str) -> dict[str, Any]:
    """按对象 API 名精确查找；不在 bundle 内即报错（列出可查对象，均属启用范围）。"""
    normalized = str(object_name or "").strip()
    if not normalized:
        raise ValueError("缺少 object：请传入本体对象 API 名")
    objects = bundle.get("objects") or []
    for obj in objects:
        if str((obj or {}).get("object_name") or "").strip() == normalized:
            return obj
    available = sorted(str((obj or {}).get("object_name") or "") for obj in objects if (obj or {}).get("object_name"))
    if available:
        raise ValueError(f"本体对象不存在或未绑定本轮启用语义模型: {normalized}；可查对象: {_candidate_summary(available)}")
    raise ValueError(f"本体对象不存在: {normalized}；当前启用语义模型没有绑定任何本体对象")


def _object_property_names(properties: list[dict[str, Any]] | None) -> list[str]:
    return sorted(
        {
            str((prop or {}).get("property_name") or "").strip()
            for prop in properties or []
            if str((prop or {}).get("property_name") or "").strip()
        }
    )


def _mapping_column_map(mapping: dict[str, Any] | None) -> dict[str, str]:
    """映射行的「属性 API 名 → 模型维度名」字典（空/非法一律按空映射处理）。"""
    raw = (mapping or {}).get("column_mappings") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(key).strip(): str(value).strip() for key, value in raw.items() if str(key).strip() and str(value).strip()}


def _usable_mappings(mappings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """带非空 column_mappings 的映射行，按 semantic_config_id 升序（确定性）。"""
    usable: list[dict[str, Any]] = []
    for mapping in mappings or []:
        column_map = _mapping_column_map(mapping)
        config_id = str((mapping or {}).get("semantic_config_id") or "").strip()
        if column_map and config_id:
            usable.append({"semantic_config_id": config_id, "column_mappings": column_map})
    usable.sort(key=lambda item: item["semantic_config_id"])
    return usable


def _select_mapping(usable: list[dict[str, Any]], explicit_config_id: str | None) -> dict[str, Any]:
    """选映射：显式指定 > 唯一映射自动选 > 多映射报候选（绝不静默取第一个）。"""
    if explicit_config_id:
        normalized = str(explicit_config_id).strip()
        for mapping in usable:
            if mapping["semantic_config_id"] == normalized:
                return mapping
        raise ValueError(
            f"对象未绑定语义模型 {normalized}；"
            f"可用绑定: {_candidate_summary([item['semantic_config_id'] for item in usable]) or '无'}"
        )
    if len(usable) == 1:
        return usable[0]
    if not usable:
        raise ValueError("该对象没有可用的语义模型映射（column_mappings 为空），请先在模型管理中补全映射")
    raise ValueError(
        "该对象绑定了多个语义模型，请传 semantic_config_id 指定其一: "
        + _candidate_summary([item["semantic_config_id"] for item in usable])
    )


def _resolve_columns(
    property_entries: list[dict[str, Any]],
    column_map: dict[str, str],
    requested: list[str] | None,
    object_name: str,
) -> list[str]:
    """解析输出属性：缺省=全部已映射属性；请求属性必须存在且已映射（明确报错）。"""
    if requested is None:
        return sorted(column_map)
    known = {str(entry.get("property_name") or "").strip() for entry in property_entries}
    known.discard("")
    unknown = [str(item).strip() for item in requested if str(item).strip() not in known]
    if unknown:
        available = _object_property_names(property_entries)
        raise ValueError(
            f"对象 {object_name} 不存在属性: {', '.join(unknown)}；"
            f"对象属性: {_candidate_summary(available) or '无'}"
        )
    unmapped = [str(item).strip() for item in requested if str(item).strip() not in column_map]
    if unmapped:
        raise ValueError(
            f"对象 {object_name} 的属性未映射到语义模型维度: {', '.join(unmapped)}；"
            "请在模型管理中补全该对象的 column_mappings 后再查询"
        )
    seen: set[str] = set()
    ordered: list[str] = []
    for item in requested:
        name = str(item).strip()
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _translate_filter_nodes(nodes: list[dict[str, Any]], column_map: dict[str, str]) -> list[dict[str, Any]]:
    """把属性谓词翻译成 builder DSL 过滤树：属性 → 维度名，operator/value 原样透传。

    operator 白名单与 value 转义由编译层（SemanticBIQueryAdapter / SQL 编译器）
    负责，本层只做形状校验与属性名解析；属性未映射即报错，绝不把原始属性名
    放进 DSL（防未经验证的标识符进入编译输入）。
    """
    if not nodes:
        return []
    if not isinstance(nodes, list):
        raise ValueError("filters 必须是数组")
    translated: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"filters[{index}] 必须是对象")
        if "and" in node or "or" in node:
            key = "and" if "and" in node else "or"
            children = node.get(key)
            if not isinstance(children, list) or not children:
                raise ValueError(f"filters[{index}].{key} 必须是非空数组")
            translated.append({key: _translate_filter_nodes(children, column_map)})
            continue
        unknown_keys = set(node.keys()) - _FILTER_LEAF_KEYS
        if unknown_keys:
            raise ValueError(
                f"filters[{index}] 含不支持的字段: {', '.join(sorted(unknown_keys))}；"
                f'叶子节点仅支持 {{property, operator, value}}'
            )
        property_name = str(node.get("property") or "").strip()
        if not property_name:
            raise ValueError(f"filters[{index}].property 不能为空")
        dimension_name = column_map.get(property_name)
        if dimension_name is None:
            raise ValueError(
                f"过滤属性未映射到语义模型维度: {property_name}；"
                "仅支持该对象 column_mappings 中已映射的属性"
            )
        leaf: dict[str, Any] = {"name": dimension_name}
        operator = str(node.get("operator") or "=").strip()
        leaf["operator"] = operator or "="
        if "value" in node:
            leaf["value"] = node.get("value")
        translated.append(leaf)
    return translated


def _translate_order_by(
    items: list[dict[str, Any]] | None,
    column_map: dict[str, str],
    columns: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """把排序项翻译成 builder DSL orderBy；排序属性自动并入返回列（保证列对齐）。"""
    if not items:
        return [], list(columns)
    if not isinstance(items, list):
        raise ValueError("order_by 必须是数组")
    order_nodes: list[dict[str, Any]] = []
    extra_columns: list[str] = []
    known_columns = set(columns)
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"order_by[{index}] 必须是对象")
        property_name = str(item.get("property") or "").strip()
        if not property_name:
            raise ValueError(f"order_by[{index}].property 不能为空")
        dimension_name = column_map.get(property_name)
        if dimension_name is None:
            raise ValueError(f"排序属性未映射到语义模型维度: {property_name}")
        direction = str(item.get("direction") or "ASC").strip().upper()
        if direction not in {"ASC", "DESC"}:
            raise ValueError(f"order_by[{index}].direction 只能是 ASC 或 DESC")
        order_nodes.append({"name": dimension_name, "direction": direction})
        if property_name not in known_columns:
            known_columns.add(property_name)
            extra_columns.append(property_name)
    return order_nodes, list(columns) + extra_columns


def build_object_query_plan(
    bundle: dict[str, Any],
    *,
    object_name: str,
    properties: list[str] | None = None,
    filters: list[dict[str, Any]] | None = None,
    order_by: list[dict[str, Any]] | None = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    semantic_config_id: str | None = None,
) -> ObjectQueryPlan:
    """把对象查询意图翻译成 dimensions-only builder DSL（纯函数，不触库）。

    消费链路：调用方先取「本轮启用模型」的注入 bundle（与目录物化同源），
    本函数完成 对象查找 → 选映射（显式 > 唯一 > 多报候选）→ 属性解析 →
    谓词/排序翻译 → DSL 组装；编译与执行由调用方走既有链路。
    """
    if not isinstance(bundle, dict):
        raise ValueError("本体 bundle 必须是对象")
    obj = _find_object(bundle, object_name)
    obj_id = int(obj.get("id") or 0)
    properties_by_object: dict[int, list[dict[str, Any]]] = {}
    for prop in bundle.get("properties") or []:
        properties_by_object.setdefault(int((prop or {}).get("object_id") or 0), []).append(prop)
    mappings_by_object: dict[int, list[dict[str, Any]]] = {}
    for mapping in bundle.get("mappings") or []:
        mappings_by_object.setdefault(int((mapping or {}).get("object_id") or 0), []).append(mapping)

    object_properties = properties_by_object.get(obj_id, [])
    mapping = _select_mapping(_usable_mappings(mappings_by_object.get(obj_id, [])), semantic_config_id)
    column_map = mapping["column_mappings"]
    columns = _resolve_columns(object_properties, column_map, properties, str(obj.get("object_name") or ""))

    dimensions: list[dict[str, Any]] = []
    property_dimensions: dict[str, str] = {}
    emitted_dimensions: set[str] = set()
    for property_name in columns:
        dimension_name = column_map[property_name]
        property_dimensions[property_name] = dimension_name
        if dimension_name in emitted_dimensions:
            continue
        emitted_dimensions.add(dimension_name)
        dimensions.append({"name": dimension_name, "displayName": property_name})

    order_nodes, columns = _translate_order_by(order_by, column_map, columns)
    for node in order_nodes:
        if node["name"] not in emitted_dimensions:
            emitted_dimensions.add(node["name"])
            dimensions.append({"name": node["name"]})

    normalized_limit = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    normalized_offset = max(0, int(offset or 0))
    dsl: dict[str, Any] = {
        "dimensions": dimensions,
        "limit": normalized_limit,
        "offset": normalized_offset,
    }
    filter_nodes = _translate_filter_nodes(filters, column_map)
    if filter_nodes:
        dsl["filters"] = filter_nodes
    if order_nodes:
        dsl["orderBy"] = order_nodes

    return ObjectQueryPlan(
        object_name=str(obj.get("object_name") or ""),
        object_display_name=str(obj.get("display_name") or ""),
        semantic_config_id=mapping["semantic_config_id"],
        dsl=dsl,
        columns=columns,
        property_dimensions=property_dimensions,
    )


def build_relation_hint(bundle: dict[str, Any], object_name: str) -> str:
    """对象的一跳关系提示（引导跨对象分步查询；一期不做服务端导航）。"""
    obj = next(
        (item for item in bundle.get("objects") or [] if str((item or {}).get("object_name") or "") == object_name),
        None,
    )
    if obj is None:
        return ""
    obj_id = int(obj.get("id") or 0)
    object_names = {
        int((item or {}).get("id") or 0): str((item or {}).get("object_name") or "") for item in bundle.get("objects") or []
    }
    descriptions: list[str] = []
    for rel in bundle.get("relations") or []:
        rel = rel or {}
        from_id = int(rel.get("from_object_id") or 0)
        to_id = int(rel.get("to_object_id") or 0)
        if obj_id not in {from_id, to_id}:
            continue
        other = object_names.get(to_id if from_id == obj_id else from_id, "")
        if not other:
            continue
        descriptions.append(f"{other}({rel.get('multiplicity') or ''})")
        if len(descriptions) >= _MAX_HINT_RELATIONS:
            break
    if not descriptions:
        return ""
    return f"对象 {object_name} 的关联对象: {', '.join(descriptions)}；跨对象查询请分别调用本工具后在服务端组装，不要自行拼接 SQL。"
