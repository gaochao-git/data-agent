"""本体 Action Type 定义与执行服务。

对齐 Palantir Action Type 的三段结构：
- parameters：参数名/类型/必填/说明；
- edits：两种互斥操作——set_property（属性覆盖编辑，要求目标对象类型
  write_mode=action_writable，编辑只落覆盖层不改源库）与 create_object
  （创建动作对象，要求 write_mode=action_created，行存业务库动作对象表）；
- submission_criteria：提交条件（能否提交 + 失败文案），官方用于把业务规则
  编码进编辑权限（action-types/submission-criteria.md:10）。

写性门禁对齐 Palantir Edits toggle 的 opt-in 模型：read_only 对象类型不可配置
任何编辑（原始业务数据只读）。执行经覆盖层/动作对象表 + 执行审计，不改源库。
test run 只算不写（action-types/test-run.md:12），输出对齐官方的
proposed changes / execution log / 错误分级三段。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from apps.chat_data_app.daos.dao_session import transaction_scope
from apps.chat_data_app.daos.ontology_action_execution_dao import OntologyActionExecutionDao
from apps.chat_data_app.daos.ontology_action_object_dao import OntologyActionObjectDao
from apps.chat_data_app.daos.ontology_action_type_dao import OntologyActionTypeDao
from apps.chat_data_app.daos.ontology_dao import OntologyDao
from apps.chat_data_app.daos.ontology_object_instance_dao import OntologyObjectInstanceDao
from apps.chat_data_app.services.ontology import ontology_service
from apps.chat_data_app.services.ontology.errors import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNotFoundError,
)
from apps.chat_data_app.services.ontology.object_errors import ObjectProjectionError
from apps.chat_data_app.services.ontology.object_projection import (
    ontology_action_execute_enabled,
    read_object_projection,
)
from apps.chat_data_app.services.ontology.ontology_validator import (
    BUILTIN_VALUE_TYPES,
    validate_api_name,
)

_ACTION_STATUSES = ("draft", "experimental", "certified")
_PARAMETER_TYPES = set(BUILTIN_VALUE_TYPES)
_PROPERTY_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,128}$")
_MAX_PARAMETERS = 20
_MAX_EDITS = 20
_MAX_SUBMISSION_CONDITIONS = 10
_SUBMISSION_LOGIC = ("all", "any")
_SUBMISSION_OPERATORS = {
    "is",
    "is_not",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "includes_any",
    "is_null",
    "is_not_null",
}
# 提交时冲突检测的三档（对齐 consistency-guarantees.md:55 的对象级写写冲突）
_CONFLICT_CHECK_LEVELS = ("object", "property", "off")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _local_object_key(object_key: str, object_type_name: str) -> str:
    """从 [member:]object_type:local_key 取本地主键，用于动作对象行定位。"""
    parts = _text(object_key).split(":")
    if len(parts) >= 3 and parts[1] == object_type_name:
        return ":".join(parts[2:])
    if len(parts) >= 2 and parts[0] == object_type_name:
        return ":".join(parts[1:])
    return _text(object_key)


def _normalize_parameters(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise OntologyInvalidError("Action Type 至少需要一个参数")
    if len(raw) > _MAX_PARAMETERS:
        raise OntologyInvalidError(f"Action Type 参数不能超过 {_MAX_PARAMETERS} 个")
    parameters: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in raw:
        item = item or {}
        name = _text(item.get("name"))
        if not name or not _PROPERTY_PATTERN.fullmatch(name):
            raise OntologyInvalidError("Action Type 参数名仅允许字母、数字、下划线，长度 1-128")
        if name in names:
            raise OntologyInvalidError(f"Action Type 参数名重复: {name}")
        parameter_type = _text(item.get("type"))
        if parameter_type not in _PARAMETER_TYPES:
            raise OntologyInvalidError(f"Action Type 参数类型不支持: {parameter_type or '?'}")
        description = _text(item.get("description"))
        if len(description) > 512:
            raise OntologyInvalidError("Action Type 参数描述不能超过 512 字符")
        names.add(name)
        parameters.append(
            {
                "name": name,
                "type": parameter_type,
                "required": bool(item.get("required", True)),
                "description": description,
            }
        )
    return parameters


def _normalize_edit_value(
    value: Any,
    *,
    property_name: str,
    parameter_names: set[str],
) -> dict[str, Any]:
    """校验单个编辑值来源并返回规范化 JSON 结构。"""
    if not isinstance(value, dict):
        raise OntologyInvalidError(f"Action Type 编辑值必须是对象: {property_name}")
    kind = _text(value.get("kind"))
    if kind == "parameter":
        parameter_name = _text(value.get("parameter"))
        if parameter_name not in parameter_names:
            raise OntologyInvalidError(f"Action Type 编辑引用了不存在的参数: {parameter_name or '?'}")
    elif kind == "static":
        if "value" not in value:
            raise OntologyInvalidError(f"Action Type 静态值缺少 value: {property_name}")
    else:
        raise OntologyInvalidError(f"Action Type 编辑值来源不支持: {kind or '?'}")
    return dict(value)


def _normalize_edit_item(
    item: dict[str, Any],
    *,
    properties: dict[str, dict[str, Any]],
    parameter_names: set[str],
    edited_properties: set[str],
) -> dict[str, Any]:
    """校验单个 set_property 编辑，并登记已编辑属性防重复。"""
    property_name = _text(item.get("property"))
    prop = properties.get(property_name)
    if prop is None:
        raise OntologyInvalidError(f"Action Type 目标属性不存在: {property_name or '?'}")
    if bool(prop.get("is_identifier")):
        raise OntologyInvalidError(f"标识属性不能通过 Action 修改: {property_name}")
    if isinstance(prop.get("derivation"), dict) and prop["derivation"]:
        raise OntologyInvalidError(f"派生属性只读（查询时计算），不能通过 Action 修改: {property_name}")
    if property_name in edited_properties:
        raise OntologyInvalidError(f"Action Type 重复编辑属性: {property_name}")
    value = _normalize_edit_value(item.get("value"), property_name=property_name, parameter_names=parameter_names)
    edited_properties.add(property_name)
    return {"operation": "set_property", "property": property_name, "value": value}


def _normalize_create_object_edit(
    item: dict[str, Any],
    *,
    properties: dict[str, dict[str, Any]],
    parameter_names: set[str],
) -> dict[str, Any]:
    """校验 create_object 编辑：属性值集（标识属性必填，值可引用参数/静态）。"""
    raw_props = item.get("properties")
    if not isinstance(raw_props, list) or not raw_props:
        raise OntologyInvalidError("create_object 编辑必须提供非空 properties 属性值集")
    if len(raw_props) > _MAX_EDITS:
        raise OntologyInvalidError(f"create_object 属性值不能超过 {_MAX_EDITS} 个")
    values: dict[str, dict[str, Any]] = {}
    for entry in raw_props:
        entry = entry or {}
        property_name = _text(entry.get("property"))
        prop = properties.get(property_name)
        if prop is None:
            raise OntologyInvalidError(f"create_object 属性不存在: {property_name or '?'}")
        if isinstance(prop.get("derivation"), dict) and prop["derivation"]:
            raise OntologyInvalidError(f"派生属性只读，不能出现在 create_object 值集: {property_name}")
        if property_name in values:
            raise OntologyInvalidError(f"create_object 属性重复: {property_name}")
        values[property_name] = _normalize_edit_value(
            entry.get("value"), property_name=property_name, parameter_names=parameter_names
        )
    missing = [name for name, prop in properties.items() if bool(prop.get("is_identifier")) and name not in values]
    if missing:
        raise OntologyInvalidError(f"create_object 必须提供标识属性值: {'、'.join(missing)}")
    return {
        "operation": "create_object",
        "properties": [{"property": name, "value": value} for name, value in values.items()],
    }


def _normalize_edits(
    raw: Any,
    *,
    properties: dict[str, dict[str, Any]],
    parameter_names: set[str],
    write_mode: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise OntologyInvalidError("Action Type 至少需要一个编辑操作")
    if len(raw) > _MAX_EDITS:
        raise OntologyInvalidError(f"Action Type 编辑不能超过 {_MAX_EDITS} 个")
    operations = {_text((item or {}).get("operation")) for item in raw}
    if operations == {"create_object"}:
        if len(raw) != 1:
            raise OntologyInvalidError("create_object 编辑只能单独一条")
        if write_mode != "action_created":
            raise OntologyInvalidError("create_object 仅支持写性为 action_created 的动作对象类型")
        return [_normalize_create_object_edit(raw[0] or {}, properties=properties, parameter_names=parameter_names)]
    if operations == {"delete_object"}:
        # 官方 Delete object(s) 规则（action-types/rules.md:21）：只能删已存在的对象，
        # 不能删「本 Action 内创建」的对象（rules.md:54），因此必须单独一条且作用于目标对象。
        if len(raw) != 1:
            raise OntologyInvalidError("delete_object 编辑只能单独一条")
        if write_mode == "read_only":
            raise OntologyInvalidError("read_only 对象类型禁止删除（原始业务数据只读）")
        return [{"operation": "delete_object"}]
    if operations == {"set_property"}:
        if write_mode not in {"action_writable", "action_created"}:
            raise OntologyInvalidError(
                "set_property 仅支持写性为 action_writable/action_created 的对象类型"
                "（原始业务数据只读，写能力需显式开启，对齐 Palantir Edits opt-in）"
            )
        edited_properties: set[str] = set()
        return [
            _normalize_edit_item(
                item or {},
                properties=properties,
                parameter_names=parameter_names,
                edited_properties=edited_properties,
            )
            for item in raw
        ]
    raise OntologyInvalidError(
        "Action Type 编辑 operation 仅支持 set_property/create_object/delete_object，且不能混合"
    )


async def _target_object_type(object_type_name: str) -> dict[str, Any]:
    detail = await ontology_service.get_object_type_async(object_type_name)
    if detail is None:
        raise OntologyNotFoundError(f"本体对象类型不存在: {object_type_name}")
    if _text(detail.get("status")) == "deprecated":
        raise OntologyInvalidError("不能为 deprecated 对象类型定义 Action Type")
    return detail


def _object_type_properties(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(prop.get("property_name")): prop
        for prop in detail.get("properties") or []
        if _text(prop.get("property_name"))
    }


async def _attach_object_type_name(item: dict[str, Any]) -> dict[str, Any]:
    object_rows = await OntologyDao().get_objects_by_ids_async([int(item.get("object_type_id") or 0)])
    target = object_rows.get(int(item.get("object_type_id") or 0)) or {}
    return {**item, "object_type_name": _text(target.get("object_type_name"))}


def _validate_parameter_value(param: dict[str, Any], value: Any) -> None:
    """试运行参数类型校验（只做基础 Python 类型，不做隐式转换）。"""
    parameter_type = _text(param.get("type"))
    name = _text(param.get("name"))
    if parameter_type == "String" or parameter_type in {"Date", "DateTime"}:
        valid = isinstance(value, str)
    elif parameter_type == "Integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif parameter_type == "Decimal":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif parameter_type == "Boolean":
        valid = isinstance(value, bool)
    else:
        valid = False
    if not valid:
        raise OntologyInvalidError(f"Action Type 参数 {name} 类型应为 {parameter_type}")


def _validate_test_parameters(item: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """试运行参数校验：未知参数拒绝、必填检查、基础类型检查。"""
    known = {_text(param.get("name")): param for param in item.get("parameters") or [] if _text(param.get("name"))}
    normalized: dict[str, Any] = {}
    for key, value in (parameters or {}).items():
        if key not in known:
            raise OntologyInvalidError(f"未知参数: {key}")
        normalized[key] = value
    for param in known.values():
        value = normalized.get(_text(param.get("name")))
        if param.get("required") and value in (None, ""):
            raise OntologyInvalidError(f"缺少必填参数: {param['name']}")
        if value is not None and value != "":
            _validate_parameter_value(param, value)
    return normalized


def _resolve_value_source(value: Any, normalized: dict[str, Any]) -> Any:
    """单个值来源解析：参数引用取参数值，静态值原样返回。"""
    value = value or {}
    parameter_name = _text(value.get("parameter"))
    return normalized.get(parameter_name) if value.get("kind") == "parameter" else value.get("value")


def _resolve_test_edits(item: dict[str, Any], normalized: dict[str, Any]) -> list[dict[str, Any]]:
    """试运行编辑解析：参数引用取参数值，静态值原样返回；不写对象。

    create_object 编辑解析进 properties[].resolved_value（供试运行预览与执行复用）。
    """
    edits: list[dict[str, Any]] = []
    for edit in item.get("edits") or []:
        operation = _text(edit.get("operation"))
        if operation == "create_object":
            resolved_props = [
                {**entry, "resolved_value": _resolve_value_source(entry.get("value"), normalized)}
                for entry in edit.get("properties") or []
            ]
            edits.append({**edit, "properties": resolved_props})
            continue
        if operation == "delete_object":
            edits.append({"operation": "delete_object"})
            continue
        edits.append({**edit, "resolved_value": _resolve_value_source(edit.get("value"), normalized)})
    return edits


def _validate_resolved_edits(edits: list[dict[str, Any]], properties: dict[str, dict[str, Any]]) -> None:
    """set_property 执行前校验：属性存在、非派生、值类型匹配。"""
    for edit in edits:
        property_name = _text(edit.get("property"))
        prop = properties.get(property_name)
        if prop is None:
            raise OntologyInvalidError(f"Action 目标属性不存在: {property_name}")
        if isinstance(prop.get("derivation"), dict) and prop["derivation"]:
            raise OntologyInvalidError(f"派生属性只读（查询时计算），Action 不能写入: {property_name}")
        _validate_property_value(property_name, prop, edit.get("resolved_value"))


def _resolve_create_values(edit: dict[str, Any], properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """create_object 属性值集解析 + 类型校验；返回 {属性名: 值}。"""
    resolved: dict[str, Any] = {}
    for entry in edit.get("properties") or []:
        property_name = _text(entry.get("property"))
        prop = properties.get(property_name)
        if prop is None:
            raise OntologyInvalidError(f"create_object 属性不存在: {property_name or '?'}")
        value = entry.get("resolved_value")
        _validate_property_value(property_name, prop, value)
        if value in (None, ""):
            raise OntologyInvalidError(f"create_object 属性值缺失: {property_name}")
        resolved[property_name] = value
    return resolved


def _normalize_submission_criteria(
    raw: Any,
    *,
    properties: dict[str, dict[str, Any]],
    parameter_names: set[str],
) -> dict[str, Any]:
    """校验提交条件（对齐 Palantir「条件 + 算子 + 失败文案」结构）。

    只支持能在本平台身份体系内求值的条件主体：参数与目标对象属性。官方还支持
    current user / execution context 模板，依赖 multipass 身份，本期不实现。
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise OntologyInvalidError("Action Type 提交条件必须是对象")
    logic = _text(raw.get("logic")) or "all"
    if logic not in _SUBMISSION_LOGIC:
        raise OntologyInvalidError(f"提交条件逻辑算子不支持: {logic}（仅支持 all/any）")
    conditions = raw.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        raise OntologyInvalidError("提交条件至少需要一个条件项")
    if len(conditions) > _MAX_SUBMISSION_CONDITIONS:
        raise OntologyInvalidError(f"提交条件不能超过 {_MAX_SUBMISSION_CONDITIONS} 个")
    normalized: list[dict[str, Any]] = []
    for entry in conditions:
        entry = entry or {}
        subject = entry.get("subject") or {}
        subject_kind = _text(subject.get("kind"))
        if subject_kind == "parameter":
            name = _text(subject.get("name"))
            if name not in parameter_names:
                raise OntologyInvalidError(f"提交条件引用了不存在的参数: {name or '?'}")
            subject_norm = {"kind": "parameter", "name": name}
        elif subject_kind == "object_property":
            property_name = _text(subject.get("property"))
            if property_name not in properties:
                raise OntologyInvalidError(f"提交条件引用了不存在的属性: {property_name or '?'}")
            subject_norm = {"kind": "object_property", "property": property_name}
        else:
            raise OntologyInvalidError(f"提交条件主体类型不支持: {subject_kind or '?'}")
        operator = _text(entry.get("operator"))
        if operator not in _SUBMISSION_OPERATORS:
            raise OntologyInvalidError(f"提交条件算子不支持: {operator or '?'}")
        raw_value = entry.get("value")
        value = (
            _normalize_edit_value(raw_value, property_name="提交条件", parameter_names=parameter_names)
            if raw_value is not None
            else None
        )
        if operator not in {"is_null", "is_not_null"} and value is None:
            raise OntologyInvalidError(f"提交条件缺少比较值: {operator}")
        failure_message = _text(entry.get("failure_message"))
        if not failure_message:
            raise OntologyInvalidError("提交条件必须提供 failure_message（失败文案）")
        normalized.append(
            {
                "subject": subject_norm,
                "operator": operator,
                "value": value,
                "failure_message": failure_message,
            }
        )
    return {"logic": logic, "conditions": normalized}


def _coerce_comparable(value: Any) -> Any:
    """比较前归一：数值统一 float、时间统一 isoformat，避免类型差异造成假失败。"""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _compare_values(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "is_null":
        return actual in (None, "")
    if operator == "is_not_null":
        return actual not in (None, "")
    if operator == "in":
        options = expected if isinstance(expected, list) else [expected]
        return any(_compare_values(actual, "is", option) for option in options)
    if operator == "includes_any":
        options = expected if isinstance(expected, list) else [expected]
        actual_values = actual if isinstance(actual, list) else ([] if actual in (None, "") else [actual])
        return any(_compare_values(item, "is", option) for item in actual_values for option in options)
    left = _coerce_comparable(actual)
    right = _coerce_comparable(expected)
    if operator == "is":
        return left == right
    if operator == "is_not":
        return left != right
    try:
        if operator == "gt":
            return left > right
        if operator == "gte":
            return left >= right
        if operator == "lt":
            return left < right
        if operator == "lte":
            return left <= right
    except TypeError:
        return False
    return False


def _evaluate_submission_criteria(
    criteria: dict[str, Any] | None,
    *,
    parameters: dict[str, Any],
    row_properties: dict[str, Any] | None,
) -> list[str]:
    """求值提交条件；返回不满足项的失败文案（空列表表示可提交）。

    对齐官方：all 下逐条求值并收集失败文案；any 下只要有一条通过即放行。
    """
    conditions = (criteria or {}).get("conditions") or []
    if not conditions:
        return []
    failures: list[str] = []
    passed = 0
    for condition in conditions:
        subject = condition.get("subject") or {}
        if _text(subject.get("kind")) == "parameter":
            actual = parameters.get(_text(subject.get("name")))
        else:
            actual = (row_properties or {}).get(_text(subject.get("property")))
        expected = _resolve_value_source(condition.get("value"), parameters)
        if _compare_values(actual, _text(condition.get("operator")), expected):
            passed += 1
        else:
            failures.append(_text(condition.get("failure_message")))
    if _text((criteria or {}).get("logic")) == "any" and passed:
        return []
    return failures


def _compile_edits(edits: list[dict[str, Any]], *, object_key: str) -> dict[str, Any]:
    """把扁平编辑编译成「每对象一条编辑」（对齐 action-types/rules.md:52）。

    本平台一条 Action 目前只作用单个目标对象，编译结果恒为一个对象；属性按编辑
    顺序合并（定义期已禁止同属性重复，这里只需保序，不涉及后者覆盖前者）。
    """
    property_changes: dict[str, Any] = {}
    effects: list[dict[str, Any]] = []
    for edit in edits:
        operation = _text(edit.get("operation"))
        if operation == "set_property":
            property_changes[_text(edit.get("property"))] = edit.get("resolved_value")
            continue
        if operation == "create_object":
            effects.append(
                {
                    "operation": "create_object",
                    "properties": {
                        _text(entry.get("property")): entry.get("resolved_value")
                        for entry in edit.get("properties") or []
                    },
                }
            )
            continue
        if operation == "delete_object":
            # 删除不是编辑：不产生属性变更，只改变对象可见性（how-edits-applied.md:86）
            effects.append({"operation": "delete_object"})
            continue
        effects.append({"operation": operation, "raw": edit})
    return {"object_key": object_key, "property_changes": property_changes, "effects": effects}


def _build_proposed_changes(
    compiled: dict[str, Any], current_properties: dict[str, Any] | None
) -> dict[str, Any]:
    """构造 test run 的 proposed changes（对齐官方 current vs proposed 对比口径）。"""
    property_changes = compiled.get("property_changes") or {}
    if property_changes:
        changes = []
        for name, proposed in property_changes.items():
            current = (current_properties or {}).get(name)
            if _coerce_comparable(current) == _coerce_comparable(proposed):
                continue
            changes.append({"property": name, "current": current, "proposed": proposed})
        return {
            "objects": [
                {
                    "kind": "modified" if changes else "unchanged",
                    "object_key": compiled.get("object_key", ""),
                    "property_changes": changes,
                }
            ],
            "links": [],
        }
    for effect in compiled.get("effects") or []:
        if effect.get("operation") == "create_object":
            values = effect.get("properties") or {}
            return {
                "objects": [
                    {
                        "kind": "created",
                        "object_key": compiled.get("object_key", ""),
                        "property_changes": [
                            {"property": name, "current": None, "proposed": value}
                            for name, value in values.items()
                        ],
                    }
                ],
                "links": [],
            }
        if effect.get("operation") == "delete_object":
            # 删除不产生属性差异，只让对象不可见；编辑值保留、不随删除失效
            return {
                "objects": [
                    {
                        "kind": "deleted",
                        "object_key": compiled.get("object_key", ""),
                        "property_changes": [],
                    }
                ],
                "links": [],
            }
    return {"objects": [], "links": []}


def _fingerprint_value(value: Any) -> Any:
    """哈希前归一：Decimal 去尾零、时间统一 UTC 秒级、字符串 trim。

    目的是消除「值没变但表示不同」造成的假冲突（§16 风险表第 2 条）。
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        normalized = value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
        return normalized.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def _row_fingerprint(values: dict[str, Any], *, scope: str) -> str:
    """对象基线指纹：`sha256:<scope>:<digest>`。

    把 scope 写进指纹，避免「按属性级算出的指纹」被拿去按对象级比较时出现
    口径不一致却看着像真冲突。
    """
    payload = {name: _fingerprint_value(value) for name, value in values.items()}
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"sha256:{scope}:{digest[:32]}"


def _normalize_conflict_check_level(level: str) -> str:
    text = _text(level) or "object"
    if text not in _CONFLICT_CHECK_LEVELS:
        raise OntologyInvalidError(
            f"冲突检测级别不支持: {text}（仅支持 {'/'.join(_CONFLICT_CHECK_LEVELS)}）"
        )
    return text


def _baseline_source(target: dict[str, Any]) -> dict[str, Any]:
    """基线必须比**源数据**：`source_properties` 未合并覆盖值，缺失时退化为投影值。"""
    source = target.get("source_properties")
    if isinstance(source, dict):
        return source
    return target.get("properties") or {}


def _build_baseline(
    target: dict[str, Any], edits: list[dict[str, Any]], *, level: str
) -> dict[str, Any]:
    """执行前快照：指纹（冲突检测）+ 生效前有效值（撤销回填）+ 代际。"""
    source = _baseline_source(target)
    effective = target.get("properties") or {}
    edited_properties = [
        _text(edit.get("property")) for edit in edits if _text(edit.get("operation")) == "set_property"
    ]
    scope_values = source if level == "object" else {name: source.get(name) for name in edited_properties}
    return {
        "object_key": _text(target.get("object_key")),
        "object_generation": int(target.get("object_generation") or 1),
        "row_hash": _row_fingerprint(scope_values, scope=level) if level != "off" else "",
        "row_hash_scope": level,
        "effective_values": {name: effective.get(name) for name in edited_properties},
        "read_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _check_baseline(expected: str, current: dict[str, Any], *, level: str) -> None:
    """对象级写写冲突检测：不一致即整单取消（不写任何覆盖值）。

    对齐 consistency-guarantees.md:55/59：检测在**对象级**，即便改的是不同属性；
    冲突时官方会丢弃整个编辑，这里通过抛错在写事务之前中断。
    """
    expected_text = _text(expected)
    if not expected_text or level == "off":
        return
    expected_scope = expected_text.split(":")[1] if expected_text.count(":") >= 2 else ""
    if expected_scope and expected_scope != level:
        raise OntologyInvalidError(
            f"基线口径不一致：提交基线按 {expected_scope} 计算，当前冲突检测级别为 {level}，请重新试运行"
        )
    if expected_text != _text(current.get("row_hash")):
        raise OntologyInvalidError(
            "对象数据在预演后被修改（对象级写写冲突），本次提交已整单取消；请重新试运行后再提交"
        )


def _validate_property_value(property_name: str, prop: dict[str, Any], value: Any) -> None:
    """执行前校验编辑值是否符合目标属性 value_type。"""
    value_type = _text(prop.get("value_type"))
    if value_type == "String":
        valid = isinstance(value, str)
    elif value_type == "Integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif value_type == "Decimal":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif value_type == "Boolean":
        valid = isinstance(value, bool)
    elif value_type in {"Date", "DateTime", "enum"}:
        valid = isinstance(value, str)
    else:
        valid = False
    if not valid:
        raise OntologyInvalidError(f"Action 编辑值类型不符合属性 {property_name}（{value_type or '?'}）")


class OntologyActionTypeService:
    """Action Type 定义 CRUD 与试运行校验。"""

    def __init__(
        self,
        dao: OntologyActionTypeDao | None = None,
        execution_dao: OntologyActionExecutionDao | None = None,
        action_object_dao: OntologyActionObjectDao | None = None,
        instance_dao: OntologyObjectInstanceDao | None = None,
    ) -> None:
        self._dao = dao or OntologyActionTypeDao()
        self._execution_dao = execution_dao or OntologyActionExecutionDao()
        self._action_object_dao = action_object_dao or OntologyActionObjectDao()
        self._instance_dao = instance_dao or OntologyObjectInstanceDao()

    async def list_action_types_async(self, object_type_name: str = "") -> list[dict[str, Any]]:
        object_type_id = None
        if object_type_name:
            detail = await _target_object_type(object_type_name)
            object_type_id = int(detail["id"])
        items = await self._dao.list_action_types_async(object_type_id)
        return [await _attach_object_type_name(item) for item in items]

    async def get_action_type_async(self, name: str) -> dict[str, Any] | None:
        item = await self._dao.get_action_type_async(name)
        return await _attach_object_type_name(item) if item else None

    async def upsert_action_type_async(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        allow_overwrite: bool = False,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        action_name = validate_api_name(name, "Action Type")
        object_type_name = _text(payload.get("object_type_name"))
        if not object_type_name:
            raise OntologyInvalidError("Action Type 必须指定目标对象类型")
        detail = await _target_object_type(object_type_name)
        parameters = _normalize_parameters(payload.get("parameters"))
        parameter_names = {item["name"] for item in parameters}
        edits = _normalize_edits(
            payload.get("edits"),
            properties=_object_type_properties(detail),
            parameter_names=parameter_names,
            write_mode=_text(detail.get("write_mode")) or "read_only",
        )
        submission_criteria = _normalize_submission_criteria(
            payload.get("submission_criteria"),
            properties=_object_type_properties(detail),
            parameter_names=parameter_names,
        )
        status = _text(payload.get("status")) or "draft"
        if status not in _ACTION_STATUSES:
            raise OntologyInvalidError(f"Action Type 状态不支持: {status}")
        display_name = _text(payload.get("display_name")) or action_name
        description = _text(payload.get("description"))
        if len(display_name) > 128 or len(description) > 1024:
            raise OntologyInvalidError("Action Type 展示名/描述超过长度限制")
        stored = await self._dao.upsert_action_type_async(
            action_name,
            {
                "display_name": display_name,
                "description": description,
                "object_type_id": int(detail["id"]),
                "parameters": parameters,
                "edits": edits,
                "submission_criteria": submission_criteria,
                "status": status,
            },
            allow_overwrite=allow_overwrite,
            create_by=operator,
            create_by_name=operator_name,
            update_by=operator,
            update_by_name=operator_name,
        )
        if stored is None:
            raise OntologyAlreadyExistsError(f"Action Type 已存在: {action_name}")
        return await _attach_object_type_name(stored)

    async def delete_action_type_async(
        self,
        name: str,
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> None:
        deleted = await self._dao.soft_delete_action_type_async(name, update_by=operator, update_by_name=operator_name)
        if not deleted:
            raise OntologyNotFoundError(f"Action Type 不存在: {name}")

    async def execute_action_type_async(
        self,
        name: str,
        *,
        object_key: str,
        parameters: dict[str, Any],
        operator: str = "",
        operator_name: str = "",
        idempotency_key: str = "",
        expected_baseline: str = "",
        conflict_check_level: str = "object",
    ) -> dict[str, Any]:
        """执行 Action：校验参数/属性值/提交条件，写覆盖层 + 执行审计（不改源库）。

        `expected_baseline` 是 test run 返回的对象指纹：传了就在写事务前重算并比对，
        不一致即整单取消（对齐官方对象级写写冲突「entire edit is discarded」）。
        """
        if not ontology_action_execute_enabled():
            raise OntologyInvalidError("Action 执行未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED=1")
        item = await self.get_action_type_async(name)
        if item is None:
            raise OntologyNotFoundError(f"Action Type 不存在: {name}")
        if _text(item.get("status")) not in {"experimental", "certified"}:
            raise OntologyInvalidError("只有 experimental/certified Action Type 可以执行")
        object_type_name = _text(item.get("object_type_name"))
        if not object_type_name:
            raise OntologyInvalidError("Action Type 缺少目标对象类型")
        detail = await _target_object_type(object_type_name)
        properties = _object_type_properties(detail)
        normalized = _validate_test_parameters(item, parameters)
        edits = _resolve_test_edits(item, normalized)
        write_mode = _text(detail.get("write_mode")) or "read_only"
        # 幂等：同一 (action type, key) 的重复提交直接返回既有执行，不再写第二份。
        # 官方没有幂等键（其内部重试是「从头重算」），这是本平台 HTTP 场景的扩展。
        replay = await self._replay_execution_if_any(name, idempotency_key)
        if replay is not None:
            return {
                "execution": replay,
                "object_type_name": object_type_name,
                "object_key": _text(replay.get("object_key")),
                "edits": replay.get("edits") or [],
                "compiled_edits": replay.get("compiled_edits") or {},
                "idempotent_replay": True,
            }
        operation = _text(edits[0].get("operation")) if edits else ""
        if operation == "create_object":
            return await self._execute_create_object(
                name,
                detail=detail,
                properties=properties,
                parameters=normalized,
                edits=edits,
                write_mode=write_mode,
                action_type_version=int(item.get("config_version") or 0),
                idempotency_key=idempotency_key,
                operator=operator,
                operator_name=operator_name,
            )
        if operation == "delete_object":
            return await self._execute_delete_object(
                name,
                detail=detail,
                parameters=normalized,
                edits=edits,
                object_key=object_key,
                write_mode=write_mode,
                submission_criteria=item.get("submission_criteria"),
                action_type_version=int(item.get("config_version") or 0),
                idempotency_key=idempotency_key,
                operator=operator,
                operator_name=operator_name,
            )
        if write_mode not in {"action_writable", "action_created"}:
            raise OntologyInvalidError("目标对象类型写性为 read_only，禁止属性编辑（原始业务数据只读）")
        _validate_resolved_edits(edits, properties)
        check_level = _normalize_conflict_check_level(conflict_check_level)
        # 先确认对象存在（读取同样会合并已有 overrides）；失败则不产生任何写入。
        # 覆盖层统一用完整对象身份 member:object_type:local_key，避免 Action 传入裸 key 后读不到。
        target_object = await read_object_projection(
            object_type_name,
            object_key,
            user_id=operator,
            username=operator_name,
            with_source_values=True,
        )
        overlay_object_key = _text(target_object.get("object_key")) or object_key
        # 提交条件：官方语义是「不满足即不可提交，并展示失败文案」（submission-criteria.md:18,103）
        failures = _evaluate_submission_criteria(
            item.get("submission_criteria"),
            parameters=normalized,
            row_properties=target_object.get("properties") or {},
        )
        if failures:
            raise OntologyInvalidError("；".join(failures))
        baseline = _build_baseline(target_object, edits, level=check_level)
        _check_baseline(expected_baseline, baseline, level=check_level)
        compiled = _compile_edits(edits, object_key=overlay_object_key)
        async with transaction_scope():
            execution = await self._execution_dao.create_execution_async(
                action_type_name=name,
                object_type_id=int(detail["id"]),
                object_key=overlay_object_key,
                parameters=normalized,
                edits=edits,
                operator=operator,
                operator_name=operator_name,
                action_type_version=int(item.get("config_version") or 0),
                edited_object_keys=[overlay_object_key],
                compiled_edits=compiled,
                idempotency_key=idempotency_key,
                baseline=baseline,
                conflict_check_level=check_level,
            )
            instance = await self._instance_dao.ensure_active_async(
                object_type_id=int(detail["id"]),
                object_key=overlay_object_key,
                execution_id=int(execution["id"]),
                operator=operator,
                operator_name=operator_name,
            )
            await self._append_overlay_edits(
                name,
                detail=detail,
                edits=edits,
                overlay_object_key=overlay_object_key,
                execution_id=int(execution["id"]),
                object_generation=int(instance.get("generation") or 1),
                operator=operator,
                operator_name=operator_name,
            )
        return {
            "execution": execution,
            "object_type_name": object_type_name,
            "object_key": overlay_object_key,
            "object_generation": int(instance.get("generation") or 1),
            "baseline": baseline,
            "conflict_check_level": check_level,
            "edits": edits,
            "compiled_edits": compiled,
            "idempotent_replay": False,
        }

    async def _append_overlay_edits(
        self,
        name: str,
        *,
        detail: dict[str, Any],
        edits: list[dict[str, Any]],
        overlay_object_key: str,
        execution_id: int,
        object_generation: int = 1,
        operator: str = "",
        operator_name: str = "",
    ) -> None:
        """set_property 编辑逐条落覆盖层（须在 transaction_scope 内调用）。"""
        for edit in edits:
            await self._execution_dao.append_edit_async(
                object_type_id=int(detail["id"]),
                object_key=overlay_object_key,
                property_name=_text(edit.get("property")),
                value=edit.get("resolved_value"),
                action_execution_id=execution_id,
                action_type_name=name,
                object_generation=int(object_generation or 1),
                operator=operator,
                operator_name=operator_name,
            )

    async def _execute_create_object(
        self,
        name: str,
        *,
        detail: dict[str, Any],
        properties: dict[str, dict[str, Any]],
        parameters: dict[str, Any],
        edits: list[dict[str, Any]],
        write_mode: str,
        action_type_version: int = 0,
        idempotency_key: str = "",
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """create_object 执行：解析属性值 → 校验类型/唯一性 → 写动作对象表 + 审计。"""
        if write_mode != "action_created":
            raise OntologyInvalidError("create_object 仅支持写性为 action_created 的动作对象类型")
        resolved_values = _resolve_create_values(edits[0], properties)
        identifier_names = [n for n, p in properties.items() if bool(p.get("is_identifier"))]
        object_key = "::".join(_text(resolved_values.get(n)) for n in sorted(identifier_names)) or "unknown"
        title_names = [n for n, p in properties.items() if bool(p.get("is_title"))]
        title_value = _text(resolved_values.get(title_names[0])) if title_names else ""
        # 同主键重建 = 新实例：代际 +1，旧代际覆盖值不参与合并（how-edits-applied.md:86）
        existing = await self._find_action_object_any_state(int(detail["id"]), object_key)
        recreated = bool(existing and int(existing.get("delete_flag") or 0) != 0)
        # 实例表与读路径统一用 [member:]object_type:local_key；动作对象无成员前缀
        instance_key = f"{_text(detail.get('object_type_name'))}:{object_key}"
        # 新建没有「执行前值」可回填：撤销语义是「把这次创建撤掉」（对象回到不可见）
        baseline = {
            "object_key": object_key,
            "object_generation": 0,
            "row_hash": "",
            "row_hash_scope": "off",
            "effective_values": {},
            "read_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        compiled = _compile_edits(edits, object_key=object_key)
        async with transaction_scope():
            execution = await self._execution_dao.create_execution_async(
                action_type_name=name,
                object_type_id=int(detail["id"]),
                object_key=object_key,
                parameters=parameters,
                edits=edits,
                operator=operator,
                operator_name=operator_name,
                action_type_version=int(action_type_version or 0),
                edited_object_keys=[object_key],
                compiled_edits=compiled,
                idempotency_key=idempotency_key,
                baseline=baseline,
                conflict_check_level="off",
            )
            if recreated:
                created = await self._action_object_dao.revive_object_async(
                    object_type_id=int(detail["id"]),
                    object_key=object_key,
                    properties=resolved_values,
                    title_value=title_value,
                    action_execution_id=int(execution["id"]),
                    action_type_name=name,
                    operator=operator,
                    operator_name=operator_name,
                )
                instance = await self._instance_dao.revive_async(
                    object_type_id=int(detail["id"]),
                    object_key=instance_key,
                    execution_id=int(execution["id"]),
                    operator=operator,
                    operator_name=operator_name,
                )
            else:
                created = await self._action_object_dao.create_object_async(
                    object_type_id=int(detail["id"]),
                    object_key=object_key,
                    properties=resolved_values,
                    title_value=title_value,
                    action_execution_id=int(execution["id"]),
                    action_type_name=name,
                    operator=operator,
                    operator_name=operator_name,
                )
                instance = await self._instance_dao.ensure_active_async(
                    object_type_id=int(detail["id"]),
                    object_key=instance_key,
                    execution_id=int(execution["id"]),
                    operator=operator,
                    operator_name=operator_name,
                )
            if created is None:
                raise OntologyAlreadyExistsError(f"动作对象已存在: {detail.get('object_type_name')}:{object_key}")
        return {
            "execution": execution,
            "object_type_name": _text(detail.get("object_type_name")),
            "object_key": object_key,
            "object_generation": int(instance.get("generation") or 1),
            "object": created,
            "recreated": recreated,
            "edits": edits,
            "compiled_edits": compiled,
            "idempotent_replay": False,
        }

    async def _find_action_object_any_state(self, object_type_id: int, object_key: str) -> dict[str, Any] | None:
        """查对象行（含已删除）；DAO 不支持时按「不存在」处理，保持既有行为。"""
        getter = getattr(self._action_object_dao, "get_object_including_deleted_async", None)
        if getter is None:
            return None
        return await getter(object_type_id, object_key)

    async def _execute_delete_object(
        self,
        name: str,
        *,
        detail: dict[str, Any],
        parameters: dict[str, Any],
        edits: list[dict[str, Any]],
        object_key: str,
        write_mode: str,
        submission_criteria: dict[str, Any] | None = None,
        action_type_version: int = 0,
        idempotency_key: str = "",
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """delete_object 执行：只让对象不可见，不改源库、不删覆盖值。

        对齐官方三条语义（object-edits/how-edits-applied.md:86、action-types/rules.md:21,54）：
        - 删除不是编辑：删除后对象不可见，与数据源是否还有该行无关；
        - 编辑值保留（不随删除失效），代际不变；
        - 对已删除对象再次执行会因「对象不可见」直接失败（与官方「不能修改已删对象」一致）。
        """
        if write_mode == "read_only":
            raise OntologyInvalidError("read_only 对象类型禁止删除（原始业务数据只读）")
        if not _text(object_key):
            raise OntologyInvalidError("delete_object 必须指定 object_key")
        object_type_name = _text(detail.get("object_type_name"))
        target_object = await read_object_projection(
            object_type_name,
            object_key,
            user_id=operator,
            username=operator_name,
            with_source_values=True,
        )
        overlay_object_key = _text(target_object.get("object_key")) or object_key
        failures = _evaluate_submission_criteria(
            submission_criteria,
            parameters=parameters,
            row_properties=target_object.get("properties") or {},
        )
        if failures:
            raise OntologyInvalidError("；".join(failures))
        # 删除不改属性：基线只记代际，供撤销时确认「删的就是这个实例」
        baseline = {
            "object_key": overlay_object_key,
            "object_generation": int(target_object.get("object_generation") or 1),
            "row_hash": "",
            "row_hash_scope": "off",
            "effective_values": {},
            "read_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        compiled = _compile_edits(edits, object_key=overlay_object_key)
        async with transaction_scope():
            execution = await self._execution_dao.create_execution_async(
                action_type_name=name,
                object_type_id=int(detail["id"]),
                object_key=overlay_object_key,
                parameters=parameters,
                edits=edits,
                operator=operator,
                operator_name=operator_name,
                action_type_version=int(action_type_version or 0),
                edited_object_keys=[overlay_object_key],
                compiled_edits=compiled,
                idempotency_key=idempotency_key,
                baseline=baseline,
                conflict_check_level="off",
            )
            if write_mode == "action_created":
                soft_delete = getattr(self._action_object_dao, "soft_delete_object_async", None)
                if soft_delete is not None:
                    await soft_delete(
                        object_type_id=int(detail["id"]),
                        object_key=_local_object_key(overlay_object_key, object_type_name),
                        action_execution_id=int(execution["id"]),
                        operator=operator,
                        operator_name=operator_name,
                    )
            instance = await self._instance_dao.mark_deleted_async(
                object_type_id=int(detail["id"]),
                object_key=overlay_object_key,
                execution_id=int(execution["id"]),
                operator=operator,
                operator_name=operator_name,
            )
        return {
            "execution": execution,
            "object_type_name": object_type_name,
            "object_key": overlay_object_key,
            "object_generation": int(instance.get("generation") or 1),
            "lifecycle_status": _text(instance.get("lifecycle_status")),
            "edits": edits,
            "compiled_edits": compiled,
            "idempotent_replay": False,
        }

    async def revert_execution_async(
        self,
        name: str,
        execution_id: int,
        *,
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """撤销一次已成功的执行（追加反向编辑，不物理删除任何行）。

        对齐官方三条硬约束（action-types/action-reverts.md:14,18,42）：
        - **仅执行者本人**可撤销（管理员也不例外，官方原文 "only be reverted by the
          user who applied the action"）；
        - 仅当该 action 是对象**最近一次编辑**（对象级，不区分属性）；
        - 撤销只还原对象实例的编辑，不撤销副作用（本期无副作用，写进语义避免以后误解）。
        """
        if not ontology_action_execute_enabled():
            raise OntologyInvalidError("Action 执行未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED=1")
        item = await self.get_action_type_async(name)
        if item is None:
            raise OntologyNotFoundError(f"Action Type 不存在: {name}")
        execution = await self._execution_dao.get_execution_async(int(execution_id))
        if execution is None or _text(execution.get("action_type_name")) != name:
            raise OntologyNotFoundError(f"Action 执行记录不存在: {execution_id}")
        if int(execution.get("revert_of_execution_id") or 0):
            raise OntologyInvalidError("撤销记录本身不能再被撤销")
        status = _text(execution.get("status"))
        if status != "succeeded":
            raise OntologyInvalidError(f"只有 succeeded 的执行可以撤销（当前 {status or '?'}）")
        if not _text(operator) or _text(execution.get("create_by")) != _text(operator):
            raise OntologyInvalidError("只有该 Action 的执行者本人可以撤销（官方 action-reverts.md:14）")

        detail = await _target_object_type(_text(item.get("object_type_name")))
        object_type_id = int(detail["id"])
        object_type_name = _text(detail.get("object_type_name"))
        object_key = _text(execution.get("object_key"))
        baseline = execution.get("baseline") or {}
        edits = execution.get("edits") or []
        operation = _text(edits[0].get("operation")) if edits else ""
        generation = int(baseline.get("object_generation") or 1)

        if operation == "set_property":
            reversed_edits = self._plan_property_revert(baseline)
            latest = await self._execution_dao.latest_edit_async(object_type_id, object_key, generation)
            if latest is None or int(latest.get("execution_id") or 0) != int(execution["id"]):
                raise OntologyInvalidError(
                    "该对象在此之后又有新的编辑（哪怕改的是别的属性），这次执行不能再撤销"
                )
        elif operation == "delete_object":
            instance = await self._instance_dao.get_instance_async(object_type_id, object_key)
            if (
                instance is None
                or _text(instance.get("lifecycle_status")) != "deleted"
                or int(instance.get("last_execution_id") or 0) != int(execution["id"])
            ):
                raise OntologyInvalidError("对象状态已变化（可能已被重建），不能再撤销这次删除")
            generation = int(instance.get("generation") or generation or 1)
            reversed_edits = [{"operation": "delete_object", "reverted": True}]
        elif operation == "create_object":
            local_key = _local_object_key(object_key, object_type_name)
            instance_key = f"{object_type_name}:{local_key}"
            instance = await self._instance_dao.get_instance_async(object_type_id, instance_key)
            if (
                instance is None
                or _text(instance.get("lifecycle_status")) != "active"
                or int(instance.get("last_execution_id") or 0) != int(execution["id"])
            ):
                raise OntologyInvalidError("对象创建后又有新的变更，不能再撤销这次创建")
            generation = int(instance.get("generation") or 1)
            latest = await self._execution_dao.latest_edit_async(object_type_id, object_key, generation)
            if latest is not None and int(latest.get("execution_id") or 0) != int(execution["id"]):
                raise OntologyInvalidError("对象创建后又被编辑过，不能再撤销这次创建")
            reversed_edits = [{"operation": "create_object", "reverted": True}]
        else:
            raise OntologyInvalidError(f"不支持的撤销操作: {operation or '?'}")

        compiled = _compile_edits(reversed_edits, object_key=object_key)
        async with transaction_scope():
            revert_execution = await self._execution_dao.create_execution_async(
                action_type_name=name,
                object_type_id=object_type_id,
                object_key=object_key,
                parameters={},
                edits=reversed_edits,
                operator=operator,
                operator_name=operator_name,
                action_type_version=int(execution.get("action_type_version") or 0),
                edited_object_keys=execution.get("edited_object_keys") or [object_key],
                compiled_edits=compiled,
                revert_of_execution_id=int(execution["id"]),
                baseline={"reverted_from": int(execution["id"])},
                conflict_check_level="off",
            )
            revert_id = int(revert_execution["id"])
            if operation == "set_property":
                await self._append_overlay_edits(
                    name,
                    detail=detail,
                    edits=reversed_edits,
                    overlay_object_key=object_key,
                    execution_id=revert_id,
                    object_generation=generation,
                    operator=operator,
                    operator_name=operator_name,
                )
            elif operation == "delete_object":
                if _text(detail.get("write_mode")) == "action_created":
                    restore = getattr(self._action_object_dao, "restore_object_async", None)
                    if restore is not None:
                        await restore(
                            object_type_id=object_type_id,
                            object_key=_local_object_key(object_key, object_type_name),
                            action_execution_id=revert_id,
                            operator=operator,
                            operator_name=operator_name,
                        )
                await self._instance_dao.restore_async(
                    object_type_id=object_type_id,
                    object_key=object_key,
                    execution_id=revert_id,
                    operator=operator,
                    operator_name=operator_name,
                )
            else:  # create_object：撤销创建 = 对象回到不可见
                local_key = _local_object_key(object_key, object_type_name)
                soft_delete = getattr(self._action_object_dao, "soft_delete_object_async", None)
                if soft_delete is not None:
                    await soft_delete(
                        object_type_id=object_type_id,
                        object_key=local_key,
                        action_execution_id=revert_id,
                        operator=operator,
                        operator_name=operator_name,
                    )
                await self._instance_dao.mark_deleted_async(
                    object_type_id=object_type_id,
                    object_key=f"{object_type_name}:{local_key}",
                    execution_id=revert_id,
                    operator=operator,
                    operator_name=operator_name,
                )
            await self._execution_dao.mark_reverted_async(int(execution["id"]), operator=operator)
        return {
            "execution": revert_execution,
            "reverted_execution_id": int(execution["id"]),
            "action_type_name": name,
            "object_type_name": object_type_name,
            "object_key": object_key,
            "operation": operation,
            "object_generation": generation,
            "reversed_edits": reversed_edits,
            "original_status": "reverted",
        }

    @staticmethod
    def _plan_property_revert(baseline: dict[str, Any]) -> list[dict[str, Any]]:
        """按执行前有效值生成反向编辑；缺基线（旧执行）直接拒绝，不猜。"""
        effective = baseline.get("effective_values")
        if not isinstance(effective, dict) or not effective:
            raise OntologyInvalidError("该执行缺少基线快照，无法计算撤销后的值（仅支持带基线的执行）")
        return [
            {
                "operation": "set_property",
                "property": property_name,
                "value": {"kind": "static", "value": value},
                "resolved_value": value,
            }
            for property_name, value in effective.items()
        ]

    async def _replay_execution_if_any(self, name: str, idempotency_key: str) -> dict[str, Any] | None:
        """幂等命中则返回既有执行；键为空或 DAO 不支持时不查（保持向后兼容）。"""
        key = _text(idempotency_key)
        if not key:
            return None
        finder = getattr(self._execution_dao, "find_execution_by_idempotency_async", None)
        if finder is None:
            return None
        return await finder(name, key)

    async def list_executions_async(
        self,
        name: str,
        *,
        object_key: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """读取 Action Type 执行审计（不含定义修改；按 id 倒序）。"""
        item = await self.get_action_type_async(name)
        if item is None:
            raise OntologyNotFoundError(f"Action Type 不存在: {name}")
        return await self._execution_dao.list_executions_async(
            action_type_name=name,
            object_key=object_key,
            limit=limit,
        )

    async def test_run_action_type_async(
        self,
        name: str,
        parameters: dict[str, Any],
        *,
        object_key: str = "",
        operator: str = "",
        operator_name: str = "",
    ) -> dict[str, Any]:
        """test run：只算不写（对齐 action-types/test-run.md:12）。

        传 `object_key` 时补对象级 current/proposed 差异与提交条件结果；不传则退化为
        「只校验参数 + 解析编辑」，不读对象，保持既有调用方兼容。
        输出结构对齐官方三段：proposed_changes / execution_log / errors（admin 与 end-user 分级）。
        """
        item = await self.get_action_type_async(name)
        if item is None:
            raise OntologyNotFoundError(f"Action Type 不存在: {name}")
        object_type_name = _text(item.get("object_type_name"))
        normalized = _validate_test_parameters(item, parameters)
        detail = await _target_object_type(object_type_name) if object_type_name else {}
        properties = _object_type_properties(detail) if detail else {}
        edits = _resolve_test_edits(item, normalized)
        operation = _text(edits[0].get("operation")) if edits else ""

        warnings: list[str] = []
        errors: dict[str, list[str]] = {"admin_facing": [], "end_user_facing": []}
        if operation == "create_object":
            resolved_values = _resolve_create_values(edits[0], properties)
            identifier_names = [n for n, p in properties.items() if bool(p.get("is_identifier"))]
            compiled_key = "::".join(_text(resolved_values.get(n)) for n in sorted(identifier_names)) or "unknown"
        elif operation == "delete_object":
            # 删除不产生属性变更，只需定位对象；缺 object_key 属 end-user 可修正问题
            compiled_key = object_key
            if not _text(object_key):
                errors["end_user_facing"].append("delete_object 必须指定 object_key")
        else:
            if properties:
                _validate_resolved_edits(edits, properties)
            compiled_key = object_key
        compiled = _compile_edits(edits, object_key=compiled_key)
        proposed_changes = _build_proposed_changes(compiled, None)

        criteria_status = "skipped"
        criteria_detail: list[str] = []
        baseline: dict[str, Any] | None = None
        if object_key and operation != "create_object":
            try:
                target = await read_object_projection(
                    object_type_name,
                    object_key,
                    user_id=operator,
                    username=operator_name,
                    with_source_values=True,
                )
            except ObjectProjectionError as exc:
                # 官方把「对象不可见/不存在」归到 end-user-facing（test-run.md:50-53）
                errors["end_user_facing"].append(str(exc))
            else:
                current_properties = target.get("properties") or {}
                compiled_key = _text(target.get("object_key")) or object_key
                compiled = _compile_edits(edits, object_key=compiled_key)
                proposed_changes = _build_proposed_changes(compiled, current_properties)
                failures = _evaluate_submission_criteria(
                    item.get("submission_criteria"),
                    parameters=normalized,
                    row_properties=current_properties,
                )
                criteria_status = "failed" if failures else "ok"
                criteria_detail = failures
                errors["end_user_facing"].extend(failures)
                baseline = _build_baseline(target, edits, level="object")

        change_count = len(compiled.get("property_changes") or {}) or len(compiled.get("effects") or [])
        execution_log = [
            {"step": "metadata_loading", "status": "ok"},
            {"step": "dependency_validation", "status": "ok"},
            {"step": "submission_criteria", "status": criteria_status, "detail": criteria_detail},
            {"step": "parameter_validation", "status": "ok"},
            {"step": "edits_calculation", "status": "ok", "detail": {"compiled_edits": change_count}},
        ]
        return {
            "action_type_name": item["action_type_name"],
            "object_type_name": item.get("object_type_name", ""),
            "parameters": normalized,
            "edits": edits,
            "compiled_edits": compiled,
            "proposed_changes": proposed_changes,
            "execution_log": execution_log,
            "errors": errors,
            "warnings": warnings,
            "baseline": baseline,
            "executed": False,
        }


__all__ = ["OntologyActionTypeService"]
