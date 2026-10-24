"""本体 Action Type 定义服务。

对齐 Palantir Action Type 的「参数 + 编辑契约」两段结构：
- parameters：参数名/类型/必填/说明；
- edits：两种互斥操作——set_property（属性覆盖编辑，要求目标对象类型
  write_mode=action_writable，编辑只落覆盖层不改源库）与 create_object
  （创建动作对象，要求 write_mode=action_created，行存业务库动作对象表）。

写性门禁对齐 Palantir Edits toggle 的 opt-in 模型：read_only 对象类型不可配置
任何编辑（原始业务数据只读）。执行经覆盖层/动作对象表 + 执行审计，不改源库。
"""

from __future__ import annotations

import re
from typing import Any

from apps.chat_data_app.daos.dao_session import transaction_scope
from apps.chat_data_app.daos.ontology_action_execution_dao import OntologyActionExecutionDao
from apps.chat_data_app.daos.ontology_action_object_dao import OntologyActionObjectDao
from apps.chat_data_app.daos.ontology_action_type_dao import OntologyActionTypeDao
from apps.chat_data_app.daos.ontology_dao import OntologyDao
from apps.chat_data_app.services.ontology import ontology_service
from apps.chat_data_app.services.ontology.errors import (
    OntologyAlreadyExistsError,
    OntologyInvalidError,
    OntologyNotFoundError,
)
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


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    if operations == {"set_property"}:
        if write_mode != "action_writable":
            raise OntologyInvalidError(
                "set_property 仅支持写性为 action_writable 的对象类型"
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
    raise OntologyInvalidError("Action Type 编辑 operation 仅支持 set_property/create_object，且不能混合")


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
        if _text(edit.get("operation")) == "create_object":
            resolved_props = [
                {**entry, "resolved_value": _resolve_value_source(entry.get("value"), normalized)}
                for entry in edit.get("properties") or []
            ]
            edits.append({**edit, "properties": resolved_props})
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
    ) -> None:
        self._dao = dao or OntologyActionTypeDao()
        self._execution_dao = execution_dao or OntologyActionExecutionDao()
        self._action_object_dao = action_object_dao or OntologyActionObjectDao()

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
    ) -> dict[str, Any]:
        """执行 Action：校验参数/属性值，写对象属性覆盖层 + 执行审计（不直接改源库）。"""
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
        if edits and _text(edits[0].get("operation")) == "create_object":
            return await self._execute_create_object(
                name,
                detail=detail,
                properties=properties,
                parameters=normalized,
                edits=edits,
                write_mode=write_mode,
                operator=operator,
                operator_name=operator_name,
            )
        if write_mode != "action_writable":
            raise OntologyInvalidError("目标对象类型写性为 read_only，禁止属性编辑（原始业务数据只读）")
        _validate_resolved_edits(edits, properties)
        # 先确认对象存在（读取同样会合并已有 overrides）；失败则不产生任何写入。
        # 覆盖层统一用完整对象身份 member:object_type:local_key，避免 Action 传入裸 key 后读不到。
        target_object = await read_object_projection(
            object_type_name, object_key, user_id=operator, username=operator_name
        )
        overlay_object_key = _text(target_object.get("object_key")) or object_key
        async with transaction_scope():
            execution = await self._execution_dao.create_execution_async(
                action_type_name=name,
                object_type_id=int(detail["id"]),
                object_key=overlay_object_key,
                parameters=normalized,
                edits=edits,
                operator=operator,
                operator_name=operator_name,
            )
            await self._append_overlay_edits(
                name,
                detail=detail,
                edits=edits,
                overlay_object_key=overlay_object_key,
                execution_id=int(execution["id"]),
                operator=operator,
                operator_name=operator_name,
            )
        return {
            "execution": execution,
            "object_type_name": object_type_name,
            "object_key": overlay_object_key,
            "edits": edits,
        }

    async def _append_overlay_edits(
        self,
        name: str,
        *,
        detail: dict[str, Any],
        edits: list[dict[str, Any]],
        overlay_object_key: str,
        execution_id: int,
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
        async with transaction_scope():
            execution = await self._execution_dao.create_execution_async(
                action_type_name=name,
                object_type_id=int(detail["id"]),
                object_key=object_key,
                parameters=parameters,
                edits=edits,
                operator=operator,
                operator_name=operator_name,
            )
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
            if created is None:
                raise OntologyAlreadyExistsError(f"动作对象已存在: {detail.get('object_type_name')}:{object_key}")
        return {
            "execution": execution,
            "object_type_name": _text(detail.get("object_type_name")),
            "object_key": object_key,
            "object": created,
            "edits": edits,
        }

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

    async def test_run_action_type_async(self, name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        item = await self.get_action_type_async(name)
        if item is None:
            raise OntologyNotFoundError(f"Action Type 不存在: {name}")
        normalized = _validate_test_parameters(item, parameters)
        edits = _resolve_test_edits(item, normalized)
        return {
            "action_type_name": item["action_type_name"],
            "object_type_name": item.get("object_type_name", ""),
            "parameters": normalized,
            "edits": edits,
            "executed": False,
        }


__all__ = ["OntologyActionTypeService"]
