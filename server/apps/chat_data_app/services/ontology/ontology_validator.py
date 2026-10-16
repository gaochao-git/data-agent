"""本体校验器：对象/属性/关系的字段与结构红线校验（确定性规则）。

校验器只做封闭词表、格式、结构一致性检查（对齐 Palantir Ontology Manager 的
error 分级，全部 error 阻断）；涉及数据库存在性的校验（对象存在、全局枚举
存在、绑定唯一归属）在 service 层组合完成。校验失败统一抛
OntologyInvalidError / OntologyNameInvalidError。
"""

from __future__ import annotations

import re
from typing import Any

from .errors import (
    OntologyInvalidError,
    OntologyNameInvalidError,
)

API_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,128}$")

BUILTIN_VALUE_TYPES = ("String", "Integer", "Decimal", "Boolean", "Date", "DateTime")
VALUE_TYPE_ENUM = "enum"
ALLOWED_VALUE_TYPES = (*BUILTIN_VALUE_TYPES, VALUE_TYPE_ENUM)

OBJECT_STATUSES = ("draft", "experimental", "certified", "deprecated")
ACTIVE_STATUSES = ("draft", "experimental", "certified")
OBJECT_VISIBILITIES = ("prominent", "normal", "hidden")
RELATION_MULTIPLICITIES = ("many_to_one", "one_to_one")
RENDER_HINT_KEYS = ("searchable", "sortable", "low_cardinality")

BINDING_TYPE_SEMANTIC = "semantic"

_NAME_RULE_MSG = "仅允许字母、数字、下划线，长度 1-128"


def validate_api_name(name: str, label: str) -> str:
    """API 名（对象/属性/关系共用）格式校验，返回规范化名称。"""
    text = str(name or "").strip()
    if not API_NAME_PATTERN.match(text):
        raise OntologyNameInvalidError(f"{label} {_NAME_RULE_MSG}")
    return text


def _invalid(message: str) -> OntologyInvalidError:
    return OntologyInvalidError(message)


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def validate_object_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化对象元数据字段（不含 status，状态走独立接口）。"""
    display_name = _normalized_text(payload.get("display_name"))
    if not display_name or len(display_name) > 128:
        raise _invalid("对象展示名必填且不超过 128 字符")
    plural = _normalized_text(payload.get("plural_display_name"))
    if len(plural) > 128:
        raise _invalid("复数展示名不超过 128 字符")
    description = _normalized_text(payload.get("description"))
    if len(description) > 1024:
        raise _invalid("对象描述不超过 1024 字符")
    visibility = _normalized_text(payload.get("visibility")) or "normal"
    if visibility not in OBJECT_VISIBILITIES:
        raise _invalid(f"对象可见性必须是 {'/'.join(OBJECT_VISIBILITIES)} 之一")
    domain = _normalized_text(payload.get("domain"))
    if len(domain) > 128:
        raise _invalid("业务域标签不超过 128 字符")
    return {
        "display_name": display_name,
        "plural_display_name": plural,
        "description": description,
        "business_notes": _normalized_text(payload.get("business_notes")),
        "visibility": visibility,
        "domain": domain,
    }


def validate_property_items(raw_items: Any) -> list[dict[str, Any]]:
    """校验并规范化属性列表（结构红线：唯一、类型封闭词表、主键/标题规则）。"""
    if not isinstance(raw_items, list) or not raw_items:
        raise _invalid("对象至少需要一个属性")
    seen: set[str] = set()
    title_count = 0
    identifier_count = 0
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        item = _validate_property_item(raw, seen)
        seen.add(item["property_name"])
        identifier_count += 1 if item["is_identifier"] else 0
        title_count += 1 if item["is_title"] else 0
        items.append(item)
    if identifier_count < 1:
        raise _invalid("对象至少需要一个标识属性（is_identifier）作为主键组成，禁止用自增行号当主键")
    if title_count > 1:
        raise _invalid("对象至多一个标题属性（is_title）")
    return items


def _validate_property_item(raw: Any, seen: set[str]) -> dict[str, Any]:
    """校验单条属性：API 名、值类型封闭词表、enum_ref 与 value_type 匹配。"""
    if not isinstance(raw, dict):
        raise _invalid("属性条目格式非法")
    name = validate_api_name(raw.get("property_name") or "", "属性API名")
    if name in seen:
        raise _invalid(f"属性API名重复: {name}")
    value_type = _normalized_text(raw.get("value_type")) or "String"
    if value_type not in ALLOWED_VALUE_TYPES:
        raise _invalid(f"属性 {name} 的值类型必须是 {'/'.join(BUILTIN_VALUE_TYPES)} 或 enum")
    enum_ref = _normalized_text(raw.get("enum_ref"))
    if value_type == VALUE_TYPE_ENUM and not enum_ref:
        raise _invalid(f"属性 {name} 值类型为 enum 时必须提供全局枚举引用 enum_ref")
    if value_type != VALUE_TYPE_ENUM and enum_ref:
        raise _invalid(f"属性 {name} 值类型非 enum 时不能携带 enum_ref")
    return {
        "property_name": name,
        "display_name": _normalized_text(raw.get("display_name")),
        "description": _normalized_text(raw.get("description")),
        "value_type": value_type,
        "enum_ref": enum_ref,
        "render_hints": _validate_render_hints(name, raw.get("render_hints")),
        "is_identifier": bool(raw.get("is_identifier")),
        "is_title": bool(raw.get("is_title")),
    }


def _validate_render_hints(property_name: str, raw_hints: Any) -> dict[str, bool]:
    """渲染提示只允许封闭键集合且取布尔值。"""
    if raw_hints in (None, {}):
        return {}
    if not isinstance(raw_hints, dict):
        raise _invalid(f"属性 {property_name} 的 render_hints 必须是对象")
    hints: dict[str, bool] = {}
    for key, value in raw_hints.items():
        if key not in RENDER_HINT_KEYS:
            raise _invalid(f"属性 {property_name} 的 render_hints 不支持字段: {key}")
        if not isinstance(value, bool):
            raise _invalid(f"属性 {property_name} 的 render_hints.{key} 必须是布尔值")
        hints[key] = value
    return hints


def validate_bindings(raw_bindings: Any, property_names: set[str], object_name_for_error: str) -> list[dict[str, Any]]:
    """校验并规范化语义模型绑定列表，返回标准化条目。

    本体统一基于语义模型（Palantir 物化物理表，我们物化语义模型），不触碰物理表。
    存在性校验（semantic_config 是否真实存在）与「一源一对象」唯一归属检查由
    service 层完成；本函数只做结构与封闭词表红线。
    """
    if raw_bindings is None:
        return []
    if not isinstance(raw_bindings, list):
        raise _invalid(f"对象 {object_name_for_error} 的绑定必须是数组")
    identities: set[tuple[str, str, str]] = set()
    return [_validate_binding_item(raw, identities, property_names, object_name_for_error) for raw in raw_bindings]


def _validate_binding_item(
    raw: Any,
    identities: set[tuple[str, str, str]],
    property_names: set[str],
    object_name_for_error: str,
) -> dict[str, Any]:
    """校验单条绑定：semantic_config_id + table_alias 必填、列映射引用已定义属性。"""
    if not isinstance(raw, dict):
        raise _invalid(f"对象 {object_name_for_error} 的绑定条目格式非法")
    subject, table = _binding_subject_and_table(raw)
    identity = (BINDING_TYPE_SEMANTIC, subject, table)
    if identity in identities:
        raise _invalid(f"对象 {object_name_for_error} 存在重复绑定: {subject}:{table}")
    identities.add(identity)
    return {
        "binding_type": BINDING_TYPE_SEMANTIC,
        "semantic_config_id": subject,
        "table_alias": table,
        "column_mappings": _validate_column_mappings(raw.get("column_mappings"), property_names, object_name_for_error),
    }


def _binding_subject_and_table(raw: dict[str, Any]) -> tuple[str, str]:
    """提取绑定主体与表标识（缺失即拒绝）。"""
    subject = _normalized_text(raw.get("semantic_config_id"))
    table = _normalized_text(raw.get("table_alias"))
    if not subject or not table:
        raise _invalid("绑定必须同时提供 semantic_config_id 与 table_alias")
    return subject, table


def _validate_column_mappings(
    raw_mappings: Any, property_names: set[str], object_name_for_error: str
) -> dict[str, str]:
    """列映射红线：必须是对象且引用的属性已定义、物理列名非空。"""
    if not isinstance(raw_mappings, dict):
        raise _invalid("column_mappings 必须是 {属性API名: 物理列名} 对象")
    normalized: dict[str, str] = {}
    for prop, column in raw_mappings.items():
        prop_name = _normalized_text(prop)
        column_name = _normalized_text(column)
        if prop_name not in property_names:
            raise _invalid(f"绑定 column_mappings 引用了未定义属性: {prop_name}（对象 {object_name_for_error}）")
        if not column_name:
            raise _invalid(f"属性 {prop_name} 的绑定列名不能为空")
        normalized[prop_name] = column_name
    return normalized


def validate_relation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化关系字段（结构红线：基数封闭词表、方向列等长非空）。"""
    multiplicity = _normalized_text(payload.get("multiplicity")) or "many_to_one"
    if multiplicity not in RELATION_MULTIPLICITIES:
        raise _invalid(f"关系基数必须是 {'/'.join(RELATION_MULTIPLICITIES)} 之一（多对多用 junction 对象表达）")
    from_columns = _validate_column_list(payload.get("from_columns"), "起始侧")
    to_columns = _validate_column_list(payload.get("to_columns"), "目标侧")
    if len(from_columns) != len(to_columns):
        raise _invalid("起始侧与目标侧关联列必须等长且顺序一一对应")
    fields: dict[str, str] = {}
    for key, label, limit in (
        ("from_display_name", "正向展示名", 128),
        ("to_display_name", "反向展示名", 128),
        ("from_verbalize", "正向关系模式串", 512),
        ("to_verbalize", "反向关系模式串", 512),
        ("description", "关系描述", 1024),
    ):
        value = _normalized_text(payload.get(key))
        if len(value) > limit:
            raise _invalid(f"{label}不超过 {limit} 字符")
        fields[key] = value
    return {
        "multiplicity": multiplicity,
        "from_columns": from_columns,
        "to_columns": to_columns,
        **fields,
        "business_notes": _normalized_text(payload.get("business_notes")),
    }


def _validate_column_list(raw_columns: Any, label: str) -> list[str]:
    if not isinstance(raw_columns, list) or not raw_columns:
        raise _invalid(f"{label}关联列不能为空（引用属性API名）")
    columns: list[str] = []
    for raw in raw_columns:
        column = validate_api_name(raw, f"{label}关联列")
        columns.append(column)
    return columns
