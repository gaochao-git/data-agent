"""本体↔语义模型一致性巡检（纯函数，不触库）。

定位校准后的第一优先级投资：本体的唯一价值输出是「AI 导航报告」的正确性，
而映射建立时的校验管不住后续漂移——语义模型随时可能改名/删除维度、改类型，
绑定守护（40070）只拦维度删除/改名单点动作，历史存量与类型漂移全是盲区。
失修本体比没有更糟：AI 按失效映射导航会拼出 join 不出的 SQL 或类型错乱。

对标证据（docs/palantir-ontology + Ossie 仓库，见 本体学习.md §13）：
- Palantir 在保存/索引时硬校验并具名报错（FoundryColumnNameNotFound、
  IncompatibleFoundryFieldSchemaForPropertyType、SchemaMismatch、连 Precision/Scale
  都要求与背书列一致）；官方哲学「宁可硬报错，不静默产出错误数据」。
- 关系侧官方硬约束「两端 join 属性类型必须一致」、to_columns 必须是标识属性。
- 我们的批式物化形态下对应解法是事后巡检（消费方=管理端健康检查 +
  locate_object 失修标注），不是把保存校验做重。

比较域：传入 bundle（按 semantic_config_ids 装载的 AI 可见本体面）× 传入的
语义模型 runtime configs。规则不参与任何评分；findings 形状与
semantic_quality_checker 的 findings 同构（code/severity/category/message/targets）。
"""

from __future__ import annotations

from typing import Any

from .ontology_validator import BUILTIN_VALUE_TYPES, VALUE_TYPE_ENUM

#: 规则登记：code → (severity, 中文名)。category 恒为 "ontology"。
RULES: dict[str, tuple[str, str]] = {
    "ONT_MAPPING_DIM_MISSING": ("error", "映射指向的维度在绑定模型中不存在"),
    "ONT_PROP_UNMAPPED": ("info", "对象属性未映射到任何模型维度"),
    "ONT_REL_COLUMN_INVALID": ("error", "本体关系列引用的属性不存在或未映射"),
    "ONT_REL_TO_NOT_IDENTIFIER": ("warning", "关系 to 侧列引用了非标识属性"),
    "ONT_REL_ENDPOINT_TYPE": ("warning", "关系两端 join 属性类型语义类别不一致"),
    "ONT_REL_CROSS_SOURCE": ("warning", "关系两端对象映射的语义模型分属不同数据源组"),
    "ONT_TYPE_MISMATCH": ("warning", "属性 value_type 与维度 logical_type 不兼容"),
}

#: findings 的固定 category（与单模型 checker 的 structure/provenance/... 平级）
_CATEGORY = "ontology"

#: 列表型 message 的最大展示条数（超出折叠为「等 N 项」）
_MAX_MESSAGE_ITEMS = 6

# value_type（本体属性）→ 语义类别。字面量必须引用校验器词表防大小写漂移：
# 合法 value_type 只有 BUILTIN_VALUE_TYPES（首字母大写）+ VALUE_TYPE_ENUM（小写 "enum"），
# 曾因手写 "Enum" 导致 enum 属性永远不进 ONT_TYPE_MISMATCH 检查。
_STRING_T, _INTEGER_T, _DECIMAL_T, _BOOLEAN_T, _DATE_T, _DATETIME_T = BUILTIN_VALUE_TYPES
_PROP_NUMERIC = {_INTEGER_T, _DECIMAL_T}
_PROP_TEMPORAL = {_DATE_T, _DATETIME_T}
_PROP_STRING = {_STRING_T, VALUE_TYPE_ENUM}

# logical_type（模型维度）→ 语义类别
_DIM_NUMERIC = {"number"}
_DIM_TEMPORAL = {"date", "datetime"}
_DIM_STRING = {"text", "enum"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _config_dimension_types(configs: list[dict[str, Any]] | None) -> dict[str, dict[str, str]]:
    """从 runtime configs 提取 {semantic_config_id: {维度名: logical_type}}。"""
    index: dict[str, dict[str, str]] = {}
    for config in configs or []:
        config = config or {}
        config_id = _text(config.get("id"))
        if not config_id:
            continue
        types: dict[str, str] = {}
        for dimension in config.get("dimensions") or []:
            dimension = dimension or {}
            name = _text(dimension.get("name"))
            if name:
                types[name] = _text(dimension.get("logical_type")).lower()
        index[config_id] = types
    return index


def _group_by_object_id(items: Any) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in items or []:
        grouped.setdefault(int((item or {}).get("object_id") or 0), []).append(item or {})
    return grouped


def _make_finding(code: str, message: str, targets: list[str]) -> dict[str, Any]:
    severity, _label = RULES[code]
    return {"code": code, "severity": severity, "category": _CATEGORY, "message": message, "targets": targets}


def _join_items(items: list[str]) -> str:
    shown = items[:_MAX_MESSAGE_ITEMS]
    suffix = f" 等 {len(items)} 项" if len(items) > _MAX_MESSAGE_ITEMS else ""
    return "、".join(shown) + suffix


def _check_mapping_dimensions(
    bundle: dict[str, Any],
    dimension_types: dict[str, dict[str, str]],
    object_names: dict[int, str],
) -> list[dict[str, Any]]:
    """ONT_MAPPING_DIM_MISSING：映射右值维度在绑定模型当前维度清单中不存在。

    直接消灭 order_no/order_id 类失修：模型维度改名/删除后，映射右值成为
    悬空引用，AI 拿去 execute_query 必然失败或错列。
    """
    findings: list[dict[str, Any]] = []
    for mapping in bundle.get("mappings") or []:
        mapping = mapping or {}
        object_id = int(mapping.get("object_id") or 0)
        object_name = object_names.get(object_id, "")
        config_id = _text(mapping.get("semantic_config_id"))
        dims = dimension_types.get(config_id)
        if not object_name or not config_id or dims is None:
            # 对象不可见（draft 等）或模型不在本轮比较域：不判
            continue
        column_mappings = mapping.get("column_mappings")
        if not isinstance(column_mappings, dict):
            continue
        missing = [
            f"{prop}→{dim}" for prop, dim in sorted(column_mappings.items()) if _text(prop) and _text(dim) and _text(dim) not in dims
        ]
        if missing:
            findings.append(
                _make_finding(
                    "ONT_MAPPING_DIM_MISSING",
                    f"对象「{object_name}」到模型 {config_id} 的映射存在 {len(missing)} 个失效维度引用：{_join_items(missing)}"
                    "（维度可能已被改名或删除，请修正映射或恢复维度）",
                    [object_name, config_id],
                )
            )
    return findings


def _check_unmapped_properties(
    bundle: dict[str, Any],
    object_names: dict[int, str],
) -> list[dict[str, Any]]:
    """ONT_PROP_UNMAPPED：有映射的对象中，未出现在任何映射左值的活跃属性。

    Palantir FoundryColumnNameNotFound 的正向面（映射完整性）：未映射属性
    对 AI 不可见，属信息级而非错误——属性可以有意不映射。
    """
    findings: list[dict[str, Any]] = []
    properties_by_object = _group_by_object_id(bundle.get("properties"))
    for mapping in bundle.get("mappings") or []:
        mapping = mapping or {}
        object_id = int(mapping.get("object_id") or 0)
        object_name = object_names.get(object_id, "")
        if not object_name:
            continue
        column_mappings = mapping.get("column_mappings")
        if not isinstance(column_mappings, dict) or not column_mappings:
            continue
        mapped_names = {_text(key) for key in column_mappings if _text(key)}
        unmapped = sorted(
            {
                _text(prop.get("property_name"))
                for prop in properties_by_object.get(object_id, [])
                if _text(prop.get("property_name"))
            }
            - mapped_names
        )
        if unmapped:
            findings.append(
                _make_finding(
                    "ONT_PROP_UNMAPPED",
                    f"对象「{object_name}」有 {len(unmapped)} 个属性未映射到模型维度：{_join_items(unmapped)}"
                    "（AI 导航时不可见；如属有意不映射可忽略）",
                    [object_name],
                )
            )
    return findings


def _relation_column_issues(
    relation: dict[str, Any],
    properties_by_object: dict[int, list[dict[str, Any]]],
    mapped_attrs_by_object: dict[int, set[str]],
    object_names: dict[int, str],
) -> tuple[list[str], list[str]]:
    """单条关系的列健康检查，返回 (失效列描述, to 侧非标识描述)。

    关系 from/to_columns 存「属性名」，两级翻译（属性→维度→物理列）后才可 join：
    属性不存在=硬失效；属性存在但未映射到任何启用模型=翻译断链。to 侧引用
    非标识属性时 join 语义不可靠（Ossie 规范明确 to_columns 是主/唯一键）。
    """
    invalid: list[str] = []
    not_identifier: list[str] = []
    endpoints = (
        ("from", int(relation.get("from_object_id") or 0), relation.get("from_columns") or []),
        ("to", int(relation.get("to_object_id") or 0), relation.get("to_columns") or []),
    )
    for side, object_id, columns in endpoints:
        object_name = object_names.get(object_id, "")
        if not object_name:
            continue
        prop_types = {
            _text(prop.get("property_name")): bool(prop.get("is_identifier"))
            for prop in properties_by_object.get(object_id, [])
            if _text(prop.get("property_name"))
        }
        mapped_attrs = mapped_attrs_by_object.get(object_id, set())
        for column in columns or []:
            attr = _text(column)
            if not attr:
                continue
            if attr not in prop_types:
                invalid.append(f"{side} 侧「{object_name}.{attr}」不是该对象的属性")
            elif attr not in mapped_attrs:
                invalid.append(f"{side} 侧「{object_name}.{attr}」未映射到任何启用模型维度")
            elif side == "to" and not prop_types[attr]:
                not_identifier.append(f"「{object_name}.{attr}」")
    return invalid, not_identifier


def _relation_endpoint_type_issues(
    relation: dict[str, Any],
    properties_by_object: dict[int, list[dict[str, Any]]],
    object_names: dict[int, str],
) -> list[str]:
    """单条关系的两端 join 类型检查，返回类型冲突描述。

    Palantir 官方硬约束「关系两端 join 属性类型必须一致」：语义类别冲突的列
    即便 SQL 能隐式转换，join 语义与索引利用都不可靠。from/to_columns 按位置
    配对；任一侧属性缺失或类型不可归类时跳过（缺失由 ONT_REL_COLUMN_INVALID
    负责，不双报）。
    """

    def _types(object_id: int) -> dict[str, str]:
        return {
            _text(prop.get("property_name")): _text(prop.get("value_type"))
            for prop in properties_by_object.get(object_id, [])
            if _text(prop.get("property_name"))
        }

    from_id = int(relation.get("from_object_id") or 0)
    to_id = int(relation.get("to_object_id") or 0)
    from_name = object_names.get(from_id, "")
    to_name = object_names.get(to_id, "")
    if not from_name or not to_name:
        return []
    from_types = _types(from_id)
    to_types = _types(to_id)
    from_columns = [_text(column) for column in relation.get("from_columns") or []]
    to_columns = [_text(column) for column in relation.get("to_columns") or []]
    issues: list[str] = []
    for from_col, to_col in zip(from_columns, to_columns):
        if not from_col or not to_col:
            continue
        from_category = _type_category_prop(from_types.get(from_col, ""))
        to_category = _type_category_prop(to_types.get(to_col, ""))
        if from_category and to_category and from_category != to_category:
            issues.append(
                f"「{from_name}.{from_col}」({from_types.get(from_col, '?')})"
                f" ↔ 「{to_name}.{to_col}」({to_types.get(to_col, '?')})"
            )
    return issues


def _check_relations(
    bundle: dict[str, Any],
    object_names: dict[int, str],
) -> list[dict[str, Any]]:
    """ONT_REL_COLUMN_INVALID + ONT_REL_TO_NOT_IDENTIFIER + ONT_REL_ENDPOINT_TYPE：关系列健康巡检。"""
    properties_by_object = _group_by_object_id(bundle.get("properties"))
    mapped_attrs_by_object: dict[int, set[str]] = {}
    for mapping in bundle.get("mappings") or []:
        mapping = mapping or {}
        object_id = int(mapping.get("object_id") or 0)
        column_mappings = mapping.get("column_mappings")
        if isinstance(column_mappings, dict):
            mapped_attrs_by_object.setdefault(object_id, set()).update(
                _text(key) for key in column_mappings if _text(key)
            )
    findings: list[dict[str, Any]] = []
    for relation in bundle.get("relations") or []:
        relation = relation or {}
        relation_name = _text(relation.get("relation_name"))
        if not relation_name:
            continue
        invalid, not_identifier = _relation_column_issues(
            relation, properties_by_object, mapped_attrs_by_object, object_names
        )
        endpoint_type_issues = _relation_endpoint_type_issues(relation, properties_by_object, object_names)
        if invalid:
            findings.append(
                _make_finding(
                    "ONT_REL_COLUMN_INVALID",
                    f"本体关系「{relation_name}」存在 {len(invalid)} 个失效关系列：{_join_items(invalid)}"
                    "（join 路径不可用，AI 导航到该关系将无法翻译为物理列）",
                    [relation_name],
                )
            )
        if not_identifier:
            findings.append(
                _make_finding(
                    "ONT_REL_TO_NOT_IDENTIFIER",
                    f"本体关系「{relation_name}」的 to 侧列引用了非标识属性：{_join_items(not_identifier)}"
                    "（Ossie 规范要求 to_columns 为主/唯一键，非标识属性做 join 语义不可靠）",
                    [relation_name],
                )
            )
        if endpoint_type_issues:
            findings.append(
                _make_finding(
                    "ONT_REL_ENDPOINT_TYPE",
                    f"本体关系「{relation_name}」存在 {len(endpoint_type_issues)} 对 join 属性类型语义冲突："
                    f"{_join_items(endpoint_type_issues)}"
                    "（官方硬约束：关系两端 join 属性类型必须一致，类别冲突的 join 语义与索引利用不可靠）",
                    [relation_name],
                )
            )
    return findings


def _type_category_prop(value_type: str) -> str:
    if value_type in _PROP_NUMERIC:
        return "numeric"
    if value_type in _PROP_TEMPORAL:
        return "temporal"
    if value_type in _PROP_STRING:
        return "string"
    return ""


def _type_category_dim(logical_type: str) -> str:
    if logical_type in _DIM_NUMERIC:
        return "numeric"
    if logical_type in _DIM_TEMPORAL:
        return "temporal"
    if logical_type in _DIM_STRING:
        return "string"
    return ""


def _check_type_mismatches(
    bundle: dict[str, Any],
    dimension_types: dict[str, dict[str, str]],
    properties_by_object: dict[int, list[dict[str, Any]]],
    object_names: dict[int, str],
) -> list[dict[str, Any]]:
    """ONT_TYPE_MISMATCH：属性 value_type 与维度 logical_type 语义类别冲突。

    官方同款问题（IncompatibleFoundryFieldSchemaForPropertyType，实测案例：
    订单金额目录标 String、维度实为 number）。warning 级：类型系统松、
    字符串形态的编号类可能合法，提示人工确认而非硬报错。
    """
    findings: list[dict[str, Any]] = []
    for mapping in bundle.get("mappings") or []:
        mapping = mapping or {}
        object_id = int(mapping.get("object_id") or 0)
        object_name = object_names.get(object_id, "")
        config_id = _text(mapping.get("semantic_config_id"))
        dims = dimension_types.get(config_id)
        if not object_name or not config_id or dims is None:
            continue
        column_mappings = mapping.get("column_mappings")
        if not isinstance(column_mappings, dict):
            continue
        prop_types = {
            _text(prop.get("property_name")): _text(prop.get("value_type"))
            for prop in properties_by_object.get(object_id, [])
            if _text(prop.get("property_name"))
        }
        mismatches: list[str] = []
        for prop, dim in sorted(column_mappings.items()):
            prop_name = _text(prop)
            dim_name = _text(dim)
            if not prop_name or dim_name not in dims:
                continue
            prop_category = _type_category_prop(prop_types.get(prop_name, ""))
            dim_category = _type_category_dim(dims[dim_name])
            if prop_category and dim_category and prop_category != dim_category:
                mismatches.append(
                    f"{prop_name}({prop_types.get(prop_name, '?')})→{dim_name}({dims[dim_name]})"
                )
        if mismatches:
            findings.append(
                _make_finding(
                    "ONT_TYPE_MISMATCH",
                    f"对象「{object_name}」到模型 {config_id} 的映射存在 {len(mismatches)} 个类型语义冲突："
                    f"{_join_items(mismatches)}（请确认目录属性类型与维度类型口径一致）",
                    [object_name, config_id],
                )
            )
    return findings


def _check_relation_cross_source(
    bundle: dict[str, Any],
    config_group_ids: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """ONT_REL_CROSS_SOURCE：关系两端对象映射的语义模型分属不同数据源组。

    join 可执行性红线：跨组的对象之间不存在同库 join 路径，目录渲染出的导航边
    会诱导 AI 拼出必然失败的 SQL（比没有本体更伤信任）。任一端无映射、或组信息
    不可知时跳过——无比对基准不误报；列失效由 ONT_REL_COLUMN_INVALID 负责。
    """
    if not config_group_ids:
        return []
    groups_by_object: dict[int, set[str]] = {}
    for mapping in bundle.get("mappings") or []:
        mapping = mapping or {}
        object_id = int(mapping.get("object_id") or 0)
        group = _text((config_group_ids or {}).get(_text(mapping.get("semantic_config_id"))))
        if object_id and group:
            groups_by_object.setdefault(object_id, set()).add(group)
    findings: list[dict[str, Any]] = []
    for relation in bundle.get("relations") or []:
        relation = relation or {}
        relation_name = _text(relation.get("relation_name"))
        if not relation_name:
            continue
        from_groups = sorted(groups_by_object.get(int(relation.get("from_object_id") or 0), set()))
        to_groups = sorted(groups_by_object.get(int(relation.get("to_object_id") or 0), set()))
        if not from_groups or not to_groups or set(from_groups) & set(to_groups):
            continue
        findings.append(
            _make_finding(
                "ONT_REL_CROSS_SOURCE",
                f"本体关系「{relation_name}」两端对象绑定的语义模型分属不同数据源组"
                f"（from: {'、'.join(from_groups)}；to: {'、'.join(to_groups)}），"
                "跨组路径物理上无法 join，请修正关系两端或映射归属",
                [relation_name],
            )
        )
    return findings


def check_ontology_model_consistency(
    bundle: dict[str, Any],
    configs: list[dict[str, Any]],
    *,
    config_group_ids: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """本体↔语义模型一致性巡检入口（纯函数）。

    - ``bundle``：``OntologyDao.load_injection_bundle_async`` 形状
      （objects/properties/relations/mappings，AI 可见面）；
    - ``configs``：参与比较的语义模型 runtime configs（含 dimensions）；
    - ``config_group_ids``：可选 {semantic_config_id: 数据源组}，供
      ONT_REL_CROSS_SOURCE 判定关系 join 可执行性；缺省时该规则整体跳过。

    返回 findings 列表（code/severity/category/message/targets），确定性排序：
    对象/关系名 → 规则注册序。不参与任何评分；空 bundle/空 configs 返回空。
    """
    if not isinstance(bundle, dict) or not configs:
        return []
    dimension_types = _config_dimension_types(configs)
    object_names = {
        int((obj or {}).get("id") or 0): _text((obj or {}).get("object_name"))
        for obj in bundle.get("objects") or []
        if _text((obj or {}).get("object_name"))
    }
    properties_by_object = _group_by_object_id(bundle.get("properties"))
    findings = [
        *_check_mapping_dimensions(bundle, dimension_types, object_names),
        *_check_relations(bundle, object_names),
        *_check_relation_cross_source(bundle, config_group_ids),
        *_check_type_mismatches(bundle, dimension_types, properties_by_object, object_names),
        *_check_unmapped_properties(bundle, object_names),
    ]
    findings.sort(key=lambda item: (item["targets"][0] if item["targets"] else "", item["code"], item["message"]))
    return findings
