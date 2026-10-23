"""ontology_tool：本体对象 / 关系 / Action 查询与导航工具（独立于语义查询工具）。

2026-10-23 起语义层与本体层在同一业务域内二选一（``services/ontology/query_mode.py``
是唯一选择入口）：本体模式下本工具是唯一的数据查询入口，**不依赖语义模型、
不回退语义查询**；对象取数统一走 Logical Dataset source binding 与
``services/ontology/object_projection.py``。

- ``list_object_types``：列出当前业务域可导航对象类型（轻量清单：对象类型名/展示名/
  描述 + Dataset 数据源绑定摘要）——本体导航的发现步，详情走 locate_object_type；
- ``locate_object_type``：按对象类型批量返回属性、Dataset 数据源绑定（属性→逻辑列→
  物理列）与关系类型跳转（纯导航，不执行查询）；
- ``search_around``：以单个对象类型为中心沿关系走一跳——每条关系返回物理连接列
  （Dataset 列映射单源翻译）与对端对象完整导航条目；
- ``get_object`` / ``find_objects`` / ``aggregate_objects``：对象实例读取、明细查询与
  分组聚合（只读，走 Dataset source binding；受 ``CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED``
  门控）；
- ``list_action_types``：列出当前业务域可执行 Action 定义（只读）；
- ``execute_action``：执行 Action（写对象属性覆盖层 + 审计，不直接改源库；受
  ``CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED`` 门控）；
- ``list_execution_targets``：列出当前用户可执行的成员数据源（member 维度查询范围）。

入参契约：``action`` + ``action_payload``（结构随 action 变化）+ ``intent``；payload
校验在工具实现入口经 ``validate_ontology_payload`` 完成（共用
``ONTOLOGY_ACTION_PAYLOAD_MODELS`` 映射表）——校验不能放进 args model_validator：
langgraph 会把 model_validator 抛出的校验错误过滤成空白消息，模型与用户都看不到原因。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field, ValidationError

from apps.chat_data_app.services.datasource.manager import get_datasource_manager
from apps.chat_data_app.services.datasource.target_context import (
    get_execution_target_member_id,
    get_execution_target_name,
    get_execution_target_org_code,
)
from apps.chat_data_app.services.ontology import ontology_service
from apps.chat_data_app.services.ontology.action_type_service import OntologyActionTypeService
from apps.chat_data_app.services.ontology.object_metrics import (
    ObjectMetricError,
    list_object_metrics_async,
    resolve_metric_projection_async,
)
from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    aggregate_metric_projection,
    aggregate_object_projection,
    ontology_action_execute_enabled,
    ontology_object_read_enabled,
    read_object_projection,
    search_object_projection,
)
from apps.chat_data_app.services.ontology.object_type_locator import (
    build_locator_usage_hint,
    build_object_list_report,
    build_object_list_usage_hint,
    build_object_type_locator_report,
    build_search_around_report,
    build_search_around_usage_hint,
    object_type_locator_enabled,
)
from apps.chat_data_app.services.ontology.query_mode import load_domain_ontology_bundle_async
from apps.chat_data_app.services.sql_run.result import build_query_result_payload
from utils.logger import get_logger

from ..tool_results import build_internal_query_result
from .common import tool_result_payload

logger = get_logger(__name__)

ONTOLOGY_TOOL_NAME = "ontology_tool"

# 失败统一走 raise：ToolErrorMiddleware 转 status="error" 的 ToolMessage，content 带详情。


def _get_datasource_id_from_state(state: dict | None) -> str | None:
    """从统一工具执行上下文取数据源 ID；缺失返回 None。"""
    context = (state or {}).get("execution_context")
    if not isinstance(context, dict):
        return None
    datasource_id = str(context.get("datasource_id") or "").strip()
    return datasource_id or None


class _ListObjectTypesPayload(BaseModel):
    """``list_object_types`` 的入参结构：列出可定位本体对象类型（轻量清单，只读发现通道）。

    无业务参数：范围由服务端结构性收敛（⊆本轮启用语义模型绑定的对象类型），
    模型不可指定、也不可扩大——与 data_analyzing_tool 的 list_execution_targets
    同一防越权口径。
    """


class _LocateObjectTypePayload(BaseModel):
    """``locate_object_type`` 的入参结构：按本体对象类型批量定位语义模型（纯导航，不执行查询）。

    可定位对象类型结构性收敛（⊆本轮启用模型绑定的对象类型）；返回绑定模型（含快照
    文件名）、属性→维度对照与对象类型间关系类型，供 AI 直接进入 execute_query，省掉
    grep/精读模型 YAML 的定位轮次。
    """

    object_types: list[str] = Field(
        ...,
        min_length=1,
        description='本体对象类型 API 名列表（如 ["Customer", "Order"]），单次最多 10 个；必须是绑定本轮启用语义模型的对象类型',
    )
    semantic_config_id: str | None = Field(
        default=None,
        description="可选；只看对象类型在该语义模型上的绑定（多绑定对象类型据此过滤）",
    )


class _SearchAroundPayload(BaseModel):
    """``search_around`` 的入参结构：以单个对象类型为中心沿关系走一跳。

    中心对象范围收敛为当前业务域本体对象类型；``relation`` 可选精准展开其中一条
    关系（控量）。
    """

    object_type: str = Field(
        ...,
        description='中心对象类型 API 名（如 "Order"，单个）；通常是 locate_object_type 命中的对象',
    )
    relation: str | None = Field(
        default=None,
        description="可选；只展开指定关系名的那条关系（关系名来自 locate 返回的 relation_types 或上一次 search_around 的 neighbor.relations）",
    )


class _GetObjectPayload(BaseModel):
    """``get_object`` 的入参结构：按对象类型 + 主键读取对象实例与一跳 links。

    只读；对象类型必须是 certified/experimental，读取权限复用当前用户的数据源授权。
    """

    object_type: str = Field(..., min_length=1, description='本体对象类型 API 名，如 "sales_order"')
    object_key: str = Field(..., min_length=1, description='对象主键值，如 "ORD2024000001"')
    member_datasource_ids: list[str] | None = Field(
        default=None, max_length=20, description="执行对象/机构范围；多机构下用于定位对象"
    )


class _FindObjectsFilter(BaseModel):
    """对象查询属性过滤条件；属性必须属于目标对象类型。"""

    property: str = Field(..., min_length=1, max_length=128, description="属性 API 名")
    operator: Literal["eq", "ne", "contains", "gt", "gte", "lt", "lte", "in"] = Field(
        default="eq", description="比较操作符"
    )
    value: str = Field(default="", max_length=512, description="比较值；in 使用 values")
    values: list[str] = Field(default_factory=list, description="in 操作符的候选值")


class _OntologyWhere(BaseModel):
    """对象查询过滤树：and/or/not 组 + 比较/isNull 叶子（与 API where 同形）。"""

    operator: Literal[
        "and", "or", "not", "eq", "ne", "contains", "gt", "gte", "lt", "lte", "in", "is_null", "is_not_null"
    ] = Field(..., description="组操作符或叶子比较操作符")
    property: str = Field(default="", max_length=128, description="叶子属性 API 名")
    value: str = Field(default="", max_length=512, description="叶子比较值")
    values: list[str] = Field(default_factory=list, max_length=100, description="in 候选值")
    children: list[_OntologyWhere] = Field(default_factory=list, max_length=50, description="组子条件")


class _FindObjectsSort(BaseModel):
    """对象查询排序项。"""

    property: str = Field(..., min_length=1, max_length=128, description="属性 API 名")
    direction: Literal["asc", "desc"] = Field(default="asc", description="排序方向")


class _FindObjectsPayload(BaseModel):
    """``find_objects`` 的入参结构：按对象类型查询实例（关键词 + 过滤 + 排序 + 分页）。"""

    object_type: str = Field(..., min_length=1, max_length=128, description='对象类型 API 名，如 "customer"')
    member_datasource_ids: list[str] | None = Field(
        default=None, max_length=20, description="执行对象/机构范围；当前对象查询 MVP 一次只支持一个机构"
    )
    keyword: str = Field(default="", max_length=200, description="标题键/主键模糊搜索")
    filters: list[_FindObjectsFilter] = Field(default_factory=list, max_length=10, description="属性过滤（旧扁平格式）")
    where: _OntologyWhere | None = Field(
        default=None, description="过滤树（and/or/not + 比较/isNull），与 filters 叠加"
    )
    sort: list[_FindObjectsSort] = Field(default_factory=list, max_length=5, description="属性排序")
    limit: int = Field(default=10, ge=1, le=50, description="返回对象数上限")
    offset: int = Field(default=0, ge=0, description="偏移量")


class _AggregateObjectsMetric(BaseModel):
    """对象聚合计指标；count 不需要 property，其余操作必须指定映射属性。"""

    operation: Literal["count", "count_distinct", "sum", "avg", "min", "max"] = Field(..., description="聚合操作")
    property: str | None = Field(default=None, max_length=128, description="聚合属性 API 名；count 可省略")


class _AggregateObjectsPayload(BaseModel):
    """``aggregate_objects`` 的入参结构：对象实例按属性分组聚合。"""

    object_type: str = Field(..., min_length=1, max_length=128, description='对象类型 API 名，如 "shipment"')
    member_datasource_ids: list[str] | None = Field(
        default=None, max_length=20, description="执行对象/机构范围；当前对象查询 MVP 一次只支持一个机构"
    )
    group_by: list[str] = Field(default_factory=list, max_length=5, description="分组属性 API 名列表")
    metrics: list[_AggregateObjectsMetric] = Field(
        default_factory=list, max_length=5, description="属性白名单聚合指标（与 metric_names 二选一）"
    )
    metric_names: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="语义层命名指标（原子/派生/复合），需对象类型绑定的 Dataset 已关联语义模型；与 metrics 二选一",
    )
    filters: list[_FindObjectsFilter] = Field(default_factory=list, max_length=10, description="属性过滤（旧扁平格式）")
    where: _OntologyWhere | None = Field(
        default=None, description="过滤树（and/or/not + 比较/isNull），与 filters 叠加"
    )
    keyword: str = Field(default="", max_length=200, description="标题键/主键模糊搜索")
    limit: int = Field(default=100, ge=1, le=200, description="返回分组数上限")


class _ListExecutionTargetsPayload(BaseModel):
    """``list_execution_targets`` 的入参结构：列出当前用户可执行的成员数据源。"""


class _ListActionTypesPayload(BaseModel):
    """``list_action_types`` 的入参结构：列出当前业务域可执行的 Action 定义（只读）。"""


class _ExecuteActionPayload(BaseModel):
    """``execute_action`` 的入参结构：执行 Action（写对象属性覆盖层 + 审计）。"""

    action_type: str = Field(
        ..., min_length=1, max_length=128, description="Action Type API 名（来自 list_action_types）"
    )
    object_key: str = Field(..., min_length=1, max_length=512, description="目标对象主键值")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Action 参数；按 list_action_types 的参数定义传"
    )


class _ListObjectMetricsPayload(BaseModel):
    """``list_object_metrics`` 的入参结构：列出对象类型可执行的语义指标（只读引用）。"""

    object_type: str | None = Field(
        default=None,
        max_length=128,
        description="可选；对象类型 API 名。为空时返回当前业务域全部对象类型的指标清单",
    )


# action -> action_payload 校验模型。与 data_analyzing_tool 的同名机制一致：
# 入参校验与 handler 内的解析共用一张表，保证两处口径一致。
ONTOLOGY_ACTION_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "list_object_types": _ListObjectTypesPayload,
    "locate_object_type": _LocateObjectTypePayload,
    "search_around": _SearchAroundPayload,
    "get_object": _GetObjectPayload,
    "find_objects": _FindObjectsPayload,
    "aggregate_objects": _AggregateObjectsPayload,
    "list_object_metrics": _ListObjectMetricsPayload,
    "list_execution_targets": _ListExecutionTargetsPayload,
    "list_action_types": _ListActionTypesPayload,
    "execute_action": _ExecuteActionPayload,
}


class OntologyToolInput(BaseModel):
    """本体导航工具入参：按 ``action`` 分派，``action_payload`` 结构随 action 变化。

    共 10 个 action：``list_object_types`` / ``locate_object_type`` / ``search_around`` 负责导航，
    ``get_object`` / ``find_objects`` 负责对象读取，``aggregate_objects`` 负责聚合（属性白名单或
    ``metric_names`` 引用语义层指标），``list_object_metrics`` 列出对象类型可执行的语义指标，
    ``list_execution_targets`` 列出执行对象，``list_action_types`` / ``execute_action`` 负责 Action。
    action_payload 的校验在工具实现入口经 ``validate_ontology_payload`` 完成——不能放进
    model_validator：langgraph 的 ToolInvocationError 会过滤 loc 为空的校验错误，
    模型与用户都看不到原因；在工具函数体内抛错则走 ToolErrorMiddleware，详情完整可见。
    """

    action: Literal[
        "list_object_types",
        "locate_object_type",
        "search_around",
        "get_object",
        "find_objects",
        "aggregate_objects",
        "list_object_metrics",
        "list_execution_targets",
        "list_action_types",
        "execute_action",
    ] = Field(
        ...,
        description=(
            "操作类型：list_object_types 列出当前业务域可导航对象类型（轻量清单：对象类型名/展示名/描述 + 数据源绑定摘要，不执行查询）；"
            "locate_object_type 按对象类型批量返回属性、数据源绑定（属性→逻辑列→物理列）与关系类型跳转（不执行查询）；"
            "search_around 以单个对象类型为中心沿关系走一跳（返回物理连接列与对端对象完整导航条目，不执行查询）；"
            "get_object 按对象类型 + 主键读取对象属性与一跳 links（只读，不回写）；"
            "find_objects 按对象类型查询实例（关键词/属性过滤/排序/分页，只读）；"
            "aggregate_objects 按对象类型分组聚合（count/sum/avg/min/max，或用 metric_names 引用语义层命名指标；只读）；"
            "list_object_metrics 列出对象类型可执行的语义指标（只读引用，不含口径表达式）；"
            "list_execution_targets 列出当前用户可执行的成员数据源；"
            "list_action_types 列出当前业务域可执行的 Action 定义（只读）；"
            "execute_action 执行 Action（写对象属性覆盖层 + 审计，需部署开启 Action 执行开关）"
        ),
    )
    action_payload: dict[str, Any] = Field(
        ...,
        description=(
            "操作参数，结构随 action 变化："
            "list_object_types 传 {}；"
            'locate_object_type 传 {"object_types": [<对象类型API名>...]}；'
            'search_around 传 {"object_type": "<中心对象类型API名>", "relation": "可选，只展开该关系"}；'
            'get_object 传 {"object_type": "<对象类型API名>", "object_key": "<对象主键值>", "member_datasource_ids": ["可选机构ID"]}；'
            'find_objects 传 {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "keyword": "可选", "filters": [{"property": "<属性>", "operator": "eq", "value": "<值>"}], "sort": [{"property": "<属性>", "direction": "asc"}], "limit": 10, "offset": 0}；'
            'aggregate_objects 传 {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "group_by": ["<属性>"], "metrics": [{"operation": "count"}, {"operation": "sum", "property": "<数值属性>"}]}，或 {"object_type": "...", "metric_names": ["<语义指标名>"], "group_by": ["<属性>"]}；'
            'list_object_metrics 传 {"object_type": "<可选对象类型API名>"}（为空返回当前业务域全部对象类型）；'
            "list_execution_targets 传 {}；"
            "list_action_types 传 {}；"
            'execute_action 传 {"action_type": "<Action Type API名>", "object_key": "<对象主键值>", "parameters": {"<参数名>": "<值>"}}'
        ),
    )
    intent: str = Field(
        default="",
        description="一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示",
    )


def validate_ontology_payload(action: str, action_payload: dict[str, Any]) -> Any:
    """按 action 校验 ``action_payload``，返回已验证的 payload 模型实例。

    与 handler 内的解析共用 :data:`ONTOLOGY_ACTION_PAYLOAD_MODELS` 一张表，保证入参
    校验与 handler 解析口径一致。必须在工具实现入口调用而不是放进 ``OntologyToolInput``
    的 model_validator——原因见该类 docstring。校验失败转 ValueError 的格式与
    ``validate_data_analyzing_payload`` 同口径（ToolErrorMiddleware 原样透出给模型）。
    """
    payload_model = ONTOLOGY_ACTION_PAYLOAD_MODELS.get(action)
    if payload_model is None:
        raise ValueError(f"不支持的 action: {action}")
    try:
        return payload_model.model_validate(action_payload)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ())) or 'action_payload'}: {error.get('msg', '')}"
            for error in exc.errors()
        )
        raise ValueError(f"action={action} 的 action_payload 校验失败：{detail}") from None


def _resolve_bound_datasource_id(state: dict | None, bound_datasource_id: str) -> str:
    """会话执行上下文的数据源组优先，回落到工具装配时绑定的数据源组。"""
    datasource_id = _get_datasource_id_from_state(state) or str(bound_datasource_id or "").strip()
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")
    return datasource_id


async def _load_domain_bundle(state: dict | None, bound_datasource_id: str) -> dict[str, Any]:
    """当前业务域（数据源组）本体 bundle；导航动作与目录物化同源收敛。"""
    datasource_id = _resolve_bound_datasource_id(state, bound_datasource_id)
    return await load_domain_ontology_bundle_async(datasource_id)


async def _run_locate_object_type(
    *,
    payload: Any,
    state: dict | None,
    bound_datasource_id: str,
) -> str:
    """按对象类型批量返回属性 / Dataset 数据源绑定 / 关系类型跳转（纯导航，不执行查询）。"""
    if not object_type_locator_enabled():
        raise ValueError("对象定位未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED=1")
    bundle = await _load_domain_bundle(state, bound_datasource_id)
    report = build_object_type_locator_report(bundle, object_type_names=payload.object_types)
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {**report, "usage": build_locator_usage_hint()},
    )


async def _run_list_object_types(
    *,
    state: dict | None,
    bound_datasource_id: str,
) -> str:
    """当前业务域对象类型轻量清单（对象类型名/展示名/描述 + 数据源绑定摘要）。"""
    if not object_type_locator_enabled():
        raise ValueError("本体对象类型清单未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED=1")
    bundle = await _load_domain_bundle(state, bound_datasource_id)
    report = build_object_list_report(bundle)
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {**report, "usage": build_object_list_usage_hint()},
    )


async def _run_search_around(
    *,
    payload: Any,
    state: dict | None,
    bound_datasource_id: str,
) -> str:
    """沿关系走一跳：返回 Dataset 列映射翻译后的物理连接列与对端导航条目。"""
    if not object_type_locator_enabled():
        raise ValueError("本体关系探索未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED=1")
    bundle = await _load_domain_bundle(state, bound_datasource_id)
    report = build_search_around_report(bundle, object_type=payload.object_type, relation=payload.relation)
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {**report, "usage": build_search_around_usage_hint()},
    )


async def _ensure_navigable_object_type(object_type_name: str) -> None:
    detail = await ontology_service.get_object_type_async(object_type_name)
    if detail is None:
        raise ValueError(f"本体对象类型不存在: {object_type_name}")
    if str(detail.get("status") or "") not in {"certified", "experimental"}:
        raise ValueError(f"对象类型未处于可导航状态（certified/experimental）: {object_type_name}")


def _state_member_ids(state: dict | None) -> list[str]:
    """从执行上下文取当前可选机构范围；显式 payload 未传时作为默认范围。"""
    context = (state or {}).get("execution_context")
    if not isinstance(context, dict):
        return []
    for key in ("matched_member_datasource_ids", "available_member_datasource_ids"):
        raw = context.get(key)
        if isinstance(raw, list):
            members = [str(item).strip() for item in raw if str(item).strip()]
            if members:
                return members
    return []


def _query_member_ids(payload_member_ids: list[str] | None, state: dict | None) -> list[str | None]:
    """请求机构优先；未请求且上下文有多个机构时必须显式选择，避免误扫分库。"""
    requested = [str(item).strip() for item in payload_member_ids or [] if str(item).strip()]
    if requested:
        return list(requested)
    members = _state_member_ids(state)
    if len(members) == 1:
        return [members[0]]
    if len(members) > 1:
        raise ValueError(
            "当前会话有多个可执行数据源，请先调用 list_execution_targets，"
            f"并显式传入 member_datasource_ids；可用: {', '.join(members)}"
        )
    return [None]


def _object_member_result(
    data: dict[str, Any],
    *,
    columns: list[str],
    rows: list[dict[str, Any]],
    total_count: int,
) -> dict[str, Any]:
    return {
        "member_datasource_id": data.get("member_datasource_id") or "",
        "member_name": data.get("member_name") or "",
        "status": "succeeded",
        "sql": data.get("sql") or "",
        "columns": columns,
        "data": rows,
        "total_count": total_count,
        "execution_time_ms": data.get("execution_time_ms"),
        "error": None,
    }


def _build_object_query_result(
    member_results: list[dict[str, Any]],
    *,
    query_mode: str,
    preview: dict[str, Any],
) -> str:
    """把（可分机构的）对象查询结果接到统一 query_result 协议。"""
    sql = next((str(item.get("sql") or "") for item in member_results if item.get("sql")), "")
    payload = build_query_result_payload(sql=sql, member_results=member_results)
    payload["query_mode"] = query_mode
    payload["preview"] = preview
    return build_internal_query_result(payload, query_mode=query_mode)


async def _run_list_execution_targets(
    *,
    state: dict | None,
    bound_datasource_id: str,
    bound_user_id: str,
    bound_operator_name: str | None,
) -> str:
    """列出当前用户在会话数据源组下可执行的成员数据源，供显式机构查询选范围。"""
    state = state or {}
    raw_context = state.get("execution_context")
    context = raw_context if isinstance(raw_context, dict) else {}
    datasource_id = str(context.get("datasource_id") or "").strip() or bound_datasource_id
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    operator_name = str(state.get("operator_name") or "").strip() or bound_operator_name or ""
    targets = await get_datasource_manager().list_authorized_execution_targets_async(
        datasource_id,
        user_id=user_id,
        username=operator_name,
    )
    entries = [
        {
            "member_datasource_id": get_execution_target_member_id(target),
            "member_name": get_execution_target_name(target, index),
            "org_code": get_execution_target_org_code(target),
        }
        for index, target in enumerate(targets, start=1)
    ]
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            "count": len(entries),
            "execution_targets": entries,
            "usage": "用户指定机构时，把选中的 member_datasource_id 作为 member_datasource_ids 传给 find_objects/aggregate_objects/get_object。",
        },
    )


async def _run_list_action_types(
    *,
    state: dict | None,
    bound_datasource_id: str,
) -> str:
    """列出当前业务域可执行的 Action 定义（只读；不含执行审计）。"""
    bundle = await _load_domain_bundle(state, bound_datasource_id)
    object_type_ids = {int(obj.get("id") or 0) for obj in bundle.get("object_types") or [] if isinstance(obj, dict)}
    items = [
        item
        for item in await OntologyActionTypeService().list_action_types_async()
        if isinstance(item, dict)
        and int(item.get("object_type_id") or 0) in object_type_ids
        and str(item.get("status") or "").strip() in {"certified", "experimental"}
    ]
    entries = [
        {
            "action_type": item.get("action_type_name") or item.get("name"),
            "display_name": item.get("display_name"),
            "object_type": item.get("object_type_name"),
            "status": item.get("status"),
            "description": item.get("description"),
            "parameters": [param for param in item.get("parameters") or [] if isinstance(param, dict)],
        }
        for item in items
    ]
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "count": len(entries),
            "action_types": entries,
            "usage": (
                "只有 experimental/certified Action 可执行；执行用 execute_action，参数按上面的 parameters 定义传。"
                "Action 只写对象属性覆盖层并记录审计，不直接改源库。"
            ),
        },
    )


async def _run_execute_action(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """执行 Action：校验参数/属性值后写对象属性覆盖层 + 执行审计（需部署开关）。"""
    if not ontology_action_execute_enabled():
        raise ValueError("Action 执行未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED=1")
    state = state or {}
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    operator_name = str(state.get("operator_name") or "").strip()
    result = await OntologyActionTypeService().execute_action_type_async(
        payload.action_type,
        object_key=payload.object_key,
        parameters=dict(payload.parameters or {}),
        operator=user_id,
        operator_name=operator_name or user_id,
    )
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "execution": result.get("execution"),
            "object_type": result.get("object_type_name"),
            "object_key": result.get("object_key"),
            "edits": result.get("edits"),
            "usage": "Action 已写入对象属性覆盖层并记录审计；需要确认时用 get_object 重新读取对象属性。",
        },
    )


async def _run_find_objects(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """按对象类型查询实例（``find_objects`` action）：关键词、过滤、排序、分页，只读。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    await _ensure_navigable_object_type(payload.object_type)
    state = state or {}
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    member_ids = _query_member_ids(payload.member_datasource_ids, state)
    semaphore = asyncio.Semaphore(8)

    async def load_member(member_id: str | None) -> dict[str, Any]:
        async with semaphore:
            try:
                return await search_object_projection(
                    payload.object_type,
                    keyword=payload.keyword,
                    filters=[item.model_dump() for item in payload.filters],
                    where=payload.where.model_dump() if payload.where else None,
                    sorts=[item.model_dump() for item in payload.sort],
                    limit=payload.limit,
                    offset=payload.offset,
                    member_datasource_ids=[member_id] if member_id else None,
                    user_id=user_id,
                )
            except ObjectProjectionError as err:
                raise ValueError(str(err)) from err

    member_results: list[dict[str, Any]] = []
    preview_objects: list[dict[str, Any]] = []
    for data in await asyncio.gather(*(load_member(member_id) for member_id in member_ids)):
        objects = [item for item in data.get("objects") or [] if isinstance(item, dict)]
        rows = [{"object_key": item.get("object_key"), **(item.get("properties") or {})} for item in objects]
        columns = ["object_key", *[str(name) for name in data.get("properties") or []]]
        member_results.append(
            _object_member_result(
                data,
                columns=columns,
                rows=rows,
                total_count=int(data.get("total") or len(rows)),
            )
        )
        preview_objects.extend(objects[:5])
    return _build_object_query_result(
        member_results,
        query_mode="ontology_find_objects",
        preview={"kind": "objects", "objects": preview_objects[:10]},
    )


async def _run_aggregate_metrics(
    payload: Any,
    *,
    state: dict | None,
    member_ids: list[str | None],
    user_id: str,
) -> str:
    """按命名指标（原子/派生/复合）在对象作用域聚合；口径由语义层唯一实现。"""
    group_id = _get_datasource_id_from_state(state) or ""
    requested_members = [str(item) for item in member_ids if str(item or "").strip()]
    if len(requested_members) != 1:
        raise ValueError("aggregate_objects 按指标聚合时一次只支持一个 member_datasource_id")
    try:
        resolved = await resolve_metric_projection_async(
            payload.object_type,
            [str(name).strip() for name in payload.metric_names if str(name).strip()],
            datasource_group_id=group_id,
            member_datasource_ids=requested_members,
            user_id=user_id,
        )
        data = await aggregate_metric_projection(
            payload.object_type,
            metric_items=resolved["metric_terms"],
            group_by=payload.group_by,
            filters=[*payload.filters, *resolved["metric_filters"]],
            where=payload.where.model_dump() if payload.where else None,
            keyword=payload.keyword,
            member_datasource_ids=requested_members,
            user_id=user_id,
        )
    except (ObjectMetricError, ObjectProjectionError) as err:
        raise ValueError(str(err)) from err
    group_by = [str(name) for name in data.get("group_by") or []]
    metric_keys = [str(name) for name in data.get("metric_keys") or []]
    groups = [group for group in data.get("groups") or [] if isinstance(group, dict)]
    rows = [{**(group.get("keys") or {}), **(group.get("metrics") or {})} for group in groups]
    member_result = _object_member_result(
        data,
        columns=[*group_by, *metric_keys],
        rows=rows,
        total_count=len(rows),
    )
    metric_names = [str(item.get("name") or "") for item in resolved.get("metric_meta") or []]
    return _build_object_query_result(
        [member_result],
        query_mode="ontology_metric_aggregate",
        preview={
            "kind": "aggregate",
            "group_by": group_by,
            "metric_keys": metric_keys,
            "metrics": metric_names,
            "semantic_config_id": resolved["link"].get("semantic_config_id") or "",
            "groups": data.get("groups") or [],
        },
    )


async def _object_metric_entry_async(name: str, group_id: str, user_id: str) -> dict[str, Any]:
    """单个对象类型的指标清单条目；失败降级为 reason。"""
    try:
        data = await list_object_metrics_async(name, datasource_group_id=group_id, user_id=user_id)
    except ObjectMetricError as err:
        return {"object_type": name, "metrics": [], "reason": str(err)}
    return {
        "object_type": name,
        "dataset_name": data.get("dataset_name") or "",
        "datasource_group_id": data.get("datasource_group_id") or "",
        "metrics": data.get("metrics") or [],
        "reason": data.get("reason") or "",
    }


async def _object_metric_entries_async(
    object_type_names: list[str],
    *,
    group_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    return [await _object_metric_entry_async(name, group_id, user_id) for name in object_type_names]


async def _run_list_object_metrics(
    *,
    payload: Any,
    state: dict | None,
    bound_datasource_id: str,
    bound_user_id: str,
) -> str:
    """列出对象类型可执行的语义指标（只读引用；不含口径表达式）。"""
    state = state or {}
    group_id = _get_datasource_id_from_state(state) or bound_datasource_id
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    object_type = str(getattr(payload, "object_type", "") or "").strip()
    names: list[str] = [object_type] if object_type else []
    if not names:
        bundle = await _load_domain_bundle(state, bound_datasource_id)
        names = [
            str(obj.get("object_type_name") or "")
            for obj in bundle.get("object_types") or []
            if isinstance(obj, dict) and str(obj.get("object_type_name") or "").strip()
        ]
    entries = await _object_metric_entries_async(names, group_id=group_id, user_id=user_id)
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "object_types": entries,
            "usage": (
                "命名指标的口径由语义层定义，执行只用 aggregate_objects 的 metric_names；"
                "不要把命名指标改写为属性 ad-hoc 聚合近似，也不要回退语义查询。"
            ),
        },
    )


async def _run_aggregate_objects(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """对象聚合入口：metric_names 走语义指标，否则走属性白名单聚合。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    await _ensure_navigable_object_type(payload.object_type)
    state = state or {}
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    member_ids = _query_member_ids(payload.member_datasource_ids, state)
    if payload.metric_names:
        return await _run_aggregate_metrics(
            payload,
            state=state,
            member_ids=member_ids,
            user_id=user_id,
        )
    if not payload.metrics:
        raise ValueError("aggregate_objects 需要 metrics 或 metric_names（命名指标）之一")
    return await _run_property_aggregate_objects(
        payload=payload,
        member_ids=member_ids,
        user_id=user_id,
    )


async def _run_property_aggregate_objects(
    *,
    payload: Any,
    member_ids: list[str | None],
    user_id: str,
) -> str:
    """属性白名单聚合（count/count_distinct/sum/avg/min/max + group_by）。"""
    semaphore = asyncio.Semaphore(8)

    async def load_member(member_id: str | None) -> dict[str, Any]:
        async with semaphore:
            try:
                return await aggregate_object_projection(
                    payload.object_type,
                    group_by=payload.group_by,
                    metrics=[item.model_dump() for item in payload.metrics],
                    filters=[item.model_dump() for item in payload.filters],
                    where=payload.where.model_dump() if payload.where else None,
                    keyword=payload.keyword,
                    limit=payload.limit,
                    member_datasource_ids=[member_id] if member_id else None,
                    user_id=user_id,
                )
            except ObjectProjectionError as err:
                raise ValueError(str(err)) from err

    member_results: list[dict[str, Any]] = []
    preview_groups: list[dict[str, Any]] = []
    group_by: list[str] = []
    metric_keys: list[str] = []
    for data in await asyncio.gather(*(load_member(member_id) for member_id in member_ids)):
        group_by = [str(name) for name in data.get("group_by") or []]
        metric_keys = [str(name) for name in data.get("metric_keys") or []]
        groups = [group for group in data.get("groups") or [] if isinstance(group, dict)]
        rows = [{**(group.get("keys") or {}), **(group.get("metrics") or {})} for group in groups]
        member_results.append(
            _object_member_result(
                data,
                columns=[*group_by, *metric_keys],
                rows=rows,
                total_count=len(rows),
            )
        )
        preview_groups.extend(groups[:10])
    return _build_object_query_result(
        member_results,
        query_mode="ontology_aggregate_objects",
        preview={"kind": "aggregate", "group_by": group_by, "metric_keys": metric_keys, "groups": preview_groups[:20]},
    )


async def _run_get_object(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """本体对象实例读取（``get_object`` action）：对象属性 + 一跳 links，只读。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    state = state or {}
    member_ids = _query_member_ids(payload.member_datasource_ids, state)
    if len(member_ids) != 1:
        raise ValueError("get_object 必须且只能指定一个 member_datasource_id")
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    await _ensure_navigable_object_type(payload.object_type)
    try:
        data = await read_object_projection(
            payload.object_type,
            payload.object_key,
            member_datasource_ids=[member_ids[0]] if member_ids[0] else None,
            user_id=user_id,
        )
    except ObjectProjectionError as err:
        raise ValueError(str(err)) from err
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            **data,
            "usage": (
                "get_object 返回对象属性与一跳 links（links 只是对端对象引用，需要属性时再用对端 key 调 get_object）；"
                "聚合/统计走 aggregate_objects，明细过滤走 find_objects，不要改用语义模型查询或自己拼 SQL。"
            ),
        },
    )


async def _run_ontology_impl(
    action: str,
    action_payload: dict,
    state: Annotated[dict | None, InjectedState] | None,
    bound_datasource_id: str,
    bound_user_id: str,
) -> str:
    """按 action 分发到本体对象类型清单 / 定位实现。"""
    # payload 校验必须在函数体内做（不能用 args_schema 的 model_validator）：
    # langgraph 会把 model_validator 抛出的校验错误过滤成空白消息，模型看不到原因
    payload = validate_ontology_payload(action, action_payload)

    if action == "list_object_types":
        return await _run_list_object_types(
            state=state,
            bound_datasource_id=bound_datasource_id,
        )
    if action == "search_around":
        return await _run_search_around(
            payload=payload,
            state=state,
            bound_datasource_id=bound_datasource_id,
        )
    if action == "get_object":
        return await _run_get_object(
            payload=payload,
            state=state,
            bound_user_id=bound_user_id,
        )
    if action == "find_objects":
        return await _run_find_objects(
            payload=payload,
            state=state,
            bound_user_id=bound_user_id,
        )
    if action == "aggregate_objects":
        return await _run_aggregate_objects(
            payload=payload,
            state=state,
            bound_user_id=bound_user_id,
        )
    if action == "list_object_metrics":
        return await _run_list_object_metrics(
            payload=payload,
            state=state,
            bound_datasource_id=bound_datasource_id,
            bound_user_id=bound_user_id,
        )
    if action == "list_execution_targets":
        return await _run_list_execution_targets(
            state=state,
            bound_datasource_id=bound_datasource_id,
            bound_user_id=bound_user_id,
            bound_operator_name=None,
        )
    if action == "list_action_types":
        return await _run_list_action_types(
            state=state,
            bound_datasource_id=bound_datasource_id,
        )
    if action == "execute_action":
        return await _run_execute_action(
            payload=payload,
            state=state,
            bound_user_id=bound_user_id,
        )
    # locate_object_type
    return await _run_locate_object_type(
        payload=payload,
        state=state,
        bound_datasource_id=bound_datasource_id,
    )


def create_ontology_tool(
    datasource_id: str | None = None,
    user_id: str | None = None,
    operator_name: str | None = None,
) -> BaseTool:
    bound_datasource_id = str(datasource_id or "").strip()
    bound_user_id = str(user_id or "system").strip() or "system"
    # operator_name 仅保持与 data_analyzing_tool 工厂签名同构；本体导航不区分操作者展示名

    @tool(ONTOLOGY_TOOL_NAME, args_schema=OntologyToolInput)
    async def ontology_tool(
        action: str,
        action_payload: dict,
        state: Annotated[dict | None, InjectedState] = None,
        intent: str = "",
    ) -> str:
        """
        本体对象 / 关系 / Action 查询工具（当前业务域已启用本体层时的唯一数据查询入口；不依赖语义模型，不回退语义查询）。

        使用顺序：先读 /mnt/user-data/workspace/semantic/_ontology_catalog.yaml 了解对象类型与数据源绑定；
        需要定位时用 list_object_types / locate_object_type；跨对象关联用 search_around 的物理连接列；
        查明细用 find_objects、聚合用 aggregate_objects、按主键读单对象用 get_object；
        需要动作时用 list_action_types 查可用 Action，再 execute_action 执行。

        Args:
            action: 操作类型，枚举：
        - list_object_types: 列出当前业务域可导航对象类型（轻量清单：对象类型API名/展示名/描述 + Dataset/物理表/member 摘要），范围限定当前数据源组
        - locate_object_type: 按对象类型批量返回属性、数据源绑定（column_mappings 属性→逻辑列、columns 逻辑列→物理列）与关系类型跳转；纯导航不执行查询
        - search_around: 以单个对象类型为中心沿关系走一跳——每条关系返回物理连接列（join）与对端对象完整导航条目（neighbor）；跨对象关联优先用它拿 join 列
        - get_object: 按对象类型 + 主键读取对象属性与一跳 links（只读；需指定单一 member_datasource_id）
        - find_objects: 按对象类型查询实例——关键词、属性过滤、排序、分页，返回对象主键/属性与 total；只读
        - aggregate_objects: 按对象类型分组聚合——group_by + 属性白名单聚合（count/count_distinct/sum/avg/min/max），或用 metric_names 引用语义层命名指标（原子/派生/复合）；只读
        - list_object_metrics: 列出对象类型可执行的语义指标（只读引用；名称/类型/描述/是否可在对象作用域执行）
        - list_execution_targets: 列出当前用户可执行的成员数据源（member_datasource_id/名称/机构编码）；多机构时先调用它，再把选中的 ID 传给对象查询
        - list_action_types: 列出当前业务域可执行 Action 定义（只读）
        - execute_action: 执行 Action（写对象属性覆盖层 + 审计；受部署开关门控）
            action_payload: 操作参数，结构随 action 变化：
        - list_object_types: {}（无参数，范围=当前业务域）
        - locate_object_type: {"object_types": [<对象API名>...]（单次最多10个）}
        - search_around: {"object_type": "<中心对象API名>", "relation": 可选只展开该关系名}
        - get_object: {"object_type": "<对象类型API名>", "object_key": "<对象主键值>", "member_datasource_ids": ["机构ID"]}
        - find_objects: {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "keyword": 可选, "filters": [{"property": "<属性>", "operator": "eq", "value": "<值>"}], "sort": [{"property": "<属性>", "direction": "asc"}], "limit": 10, "offset": 0}
        - aggregate_objects: {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "group_by": ["<属性>"], "metrics": [{"operation": "count"}, {"operation": "sum", "property": "<数值属性>"}]}；或 {"object_type": "...", "metric_names": ["<语义指标名>"], "group_by": ["<属性>"], "member_datasource_ids": ["机构ID"]}（命名指标口径由语义层定义，禁止用属性聚合近似）
        - list_object_metrics: {"object_type": "<可选对象类型API名>"}（为空返回当前业务域全部对象类型的指标清单）
        - list_execution_targets: {}（无参数；返回当前用户可见执行对象）
        - list_action_types: {}（无参数；返回当前业务域可执行 Action 定义）
        - execute_action: {"action_type": "<Action Type API名>", "object_key": "<对象主键值>", "parameters": {"<参数名>": "<值>"}}
            intent: 一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示。
        """
        return await _run_ontology_impl(
            action,
            action_payload,
            state,
            bound_datasource_id,
            bound_user_id,
        )

    return ontology_tool


__all__ = [
    "ONTOLOGY_TOOL_NAME",
    "ONTOLOGY_ACTION_PAYLOAD_MODELS",
    "OntologyToolInput",
    "create_ontology_tool",
    "validate_ontology_payload",
]
