"""ontology_tool：本体对象类型导航工具（独立于数据分析合并工具）。

2026-10-19 自 ``data_analyzing_tool`` 拆出：两个纯导航 action 不再混在问数主链路
工具里，便于本体灰度的「注册/下线/演进」成为单点操作（注册层按
``CHAT_DATA_ONTOLOGY_LOCATE_ENABLED`` 门控，见 ``registry.TOOL_SPECS``）。

- ``list_object_types``：列出可定位本体对象类型（轻量清单：对象类型名/显示名/描述 +
  对象类型→绑定模型一跳，不含属性与关系类型详情）——本体导航的发现步，详情走
  locate_object_type；
- ``locate_object_type``：按本体对象类型批量定位语义模型（纯导航，不执行查询）——返回
  绑定模型（含快照文件名）、属性→维度对照与对象类型间关系类型（含失修标注），供模型
  直接进入 data_analyzing_tool 的 execute_query（定位纯函数见
  services/ontology/object_type_locator.py，开关 ``CHAT_DATA_ONTOLOGY_LOCATE_ENABLED``
  默认关闭）；
- ``get_object``：按对象类型 + 主键读取对象属性与一跳外键型 links（只读；受
  ``CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED`` 门控）；
- ``search_around``：以单个对象类型为中心沿关系走一跳——每条关系返回物理连接列
  （目录同源翻译，多模型绑定以 by_model 为准，未解析标 unresolved）与对端对象完整
  定位条目（其关系只留名字提示下一跳），跨对象类型关联不再靠模型 grep YAML 猜 join。

入参契约与 ``data_analyzing_tool`` 同构：``action`` + ``action_payload``（结构随
action 变化）+ ``intent``；payload 校验在工具实现入口经 ``validate_ontology_payload``
完成（共用 ``ONTOLOGY_ACTION_PAYLOAD_MODELS`` 映射表）——校验不能放进 args
model_validator：langgraph 会把 model_validator 抛出的校验错误过滤成空白消息，模型
与用户都看不到原因；在函数体内抛错则详情完整可见。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from pydantic import BaseModel, Field, ValidationError

from apps.chat_data_app.daos.ontology_dao import OntologyDao
from apps.chat_data_app.services.datasource.manager import get_datasource_manager
from apps.chat_data_app.services.datasource.target_context import (
    get_execution_target_member_id,
    get_execution_target_name,
    get_execution_target_org_code,
)
from apps.chat_data_app.services.ontology import ontology_service
from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    aggregate_object_projection,
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
from apps.chat_data_app.services.ontology.ontology_injection import config_dimension_columns
from apps.chat_data_app.services.semantic.run.semantic_config_service import (
    get_semantic_config_service,
)
from apps.chat_data_app.services.semantic.run.semantic_materializer import (
    enabled_candidate_ids,
    model_snapshot_file_name,
)
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

    中心对象结构性收敛（⊆本轮启用语义模型绑定的对象类型）；``relation`` 可选
    精准展开其中一条关系（控量），``semantic_config_id`` 可选过滤两端绑定。
    """

    object_type: str = Field(
        ...,
        description='中心对象类型 API 名（如 "Order"，单个）；通常是 locate_object_type 命中的对象',
    )
    relation: str | None = Field(
        default=None,
        description="可选；只展开指定关系名的那条关系（关系名来自 locate 返回的 relation_types 或上一次 search_around 的 neighbor.relations）",
    )
    semantic_config_id: str | None = Field(
        default=None,
        description="可选；只看绑定该语义模型的映射与连接列（多绑定对象类型据此过滤）",
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
    semantic_config_id: str | None = Field(
        default=None,
        description="可选；对象类型绑定多个语义模型时指定投影使用的模型",
    )


class _FindObjectsFilter(BaseModel):
    """对象查询属性过滤条件；属性必须属于目标对象类型。"""

    property: str = Field(..., min_length=1, max_length=128, description="属性 API 名")
    operator: Literal["eq", "ne", "contains", "gt", "gte", "lt", "lte", "in"] = Field(
        default="eq", description="比较操作符"
    )
    value: str = Field(default="", max_length=512, description="比较值；in 使用 values")
    values: list[str] = Field(default_factory=list, description="in 操作符的候选值")


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
    filters: list[_FindObjectsFilter] = Field(default_factory=list, max_length=10, description="属性过滤")
    sort: list[_FindObjectsSort] = Field(default_factory=list, max_length=5, description="属性排序")
    limit: int = Field(default=10, ge=1, le=50, description="返回对象数上限")
    offset: int = Field(default=0, ge=0, description="偏移量")
    semantic_config_id: str | None = Field(default=None, description="多映射对象指定语义模型")


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
    metrics: list[_AggregateObjectsMetric] = Field(..., min_length=1, max_length=5, description="聚合指标")
    filters: list[_FindObjectsFilter] = Field(default_factory=list, max_length=10, description="属性过滤")
    keyword: str = Field(default="", max_length=200, description="标题键/主键模糊搜索")
    limit: int = Field(default=100, ge=1, le=200, description="返回分组数上限")
    semantic_config_id: str | None = Field(default=None, description="多映射对象指定语义模型")


class _ListExecutionTargetsPayload(BaseModel):
    """``list_execution_targets`` 的入参结构：列出当前用户可执行的成员数据源。"""


# action -> action_payload 校验模型。与 data_analyzing_tool 的同名机制一致：
# 入参校验与 handler 内的解析共用一张表，保证两处口径一致。
ONTOLOGY_ACTION_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "list_object_types": _ListObjectTypesPayload,
    "locate_object_type": _LocateObjectTypePayload,
    "search_around": _SearchAroundPayload,
    "get_object": _GetObjectPayload,
    "find_objects": _FindObjectsPayload,
    "aggregate_objects": _AggregateObjectsPayload,
    "list_execution_targets": _ListExecutionTargetsPayload,
}


class OntologyToolInput(BaseModel):
    """本体导航工具入参：按 ``action`` 分派，``action_payload`` 结构随 action 变化。

    七个 action：``list_object_types`` 列出可定位本体对象类型（轻量清单，只读发现通道）、
    ``locate_object_type`` 按本体对象类型批量定位语义模型（纯导航，不执行查询）、
    ``search_around`` 以单个对象类型为中心沿关系走一跳（返回物理连接列与对端完整定位条目）、
    ``get_object`` 按对象类型 + 主键读取对象属性与一跳 links（只读）。
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
        "list_execution_targets",
    ] = Field(
        ...,
        description=(
            "操作类型：list_object_types 列出可定位本体对象类型（轻量清单：对象类型名/显示名/描述 + 各自绑定模型，不执行查询）；"
            "locate_object_type 按本体对象类型批量定位语义模型（返回绑定模型/属性→维度对照/对象类型间关系类型，不执行查询）；"
            "search_around 以单个对象类型为中心沿关系走一跳（返回物理连接列与对端对象完整定位条目，不执行查询）；"
            "get_object 按对象类型 + 主键读取对象属性与一跳 links（只读，不回写）；"
            "find_objects 按对象类型查询实例（关键词/属性过滤/排序/分页，只读）；"
            "aggregate_objects 按对象类型分组聚合（count/sum/avg/min/max，只读；不替代指标 SQL）"
        ),
    )
    action_payload: dict[str, Any] = Field(
        ...,
        description=(
            "操作参数，结构随 action 变化："
            "list_object_types 传 {}；"
            'locate_object_type 传 {"object_types": [<对象类型API名>...], "semantic_config_id": "可选，只看该模型的绑定"}；'
            'search_around 传 {"object_type": "<中心对象类型API名>", "relation": "可选，只展开该关系", "semantic_config_id": "可选"}；'
            'get_object 传 {"object_type": "<对象类型API名>", "object_key": "<对象主键值>", "member_datasource_ids": ["可选机构ID"], "semantic_config_id": "可选"}；'
            'find_objects 传 {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "keyword": "可选", "filters": [{"property": "<属性>", "operator": "eq", "value": "<值>"}], "sort": [{"property": "<属性>", "direction": "asc"}], "limit": 10, "offset": 0}；'
            'aggregate_objects 传 {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "group_by": ["<属性>"], "metrics": [{"operation": "count"}, {"operation": "sum", "property": "<数值属性>"}]}'
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


def _locator_enabled_view(configs: list[dict]) -> tuple[list[dict], list[str]]:
    """按启用候选集过滤语义配置，返回 (启用配置列表, 对应 id 列表)，顺序一致。"""
    enabled_id_set = set(enabled_candidate_ids(configs))
    enabled_configs = [config for config in configs if str((config or {}).get("id") or "") in enabled_id_set]
    enabled_ids = [str(config.get("id")) for config in enabled_configs]
    return enabled_configs, enabled_ids


def _locator_model_index(
    enabled_ids: list[str],
    enabled_configs: list[dict],
) -> tuple[dict[str, str], dict[str, str], dict[str, set[str]]]:
    """启用模型的三张索引：id→显示名、id→模型快照文件名、id→维度名集合。

    维度名集合同时是失修比对基准：映射右值（模型维度名）不在集合内即标 stale。
    """
    config_names: dict[str, str] = {}
    model_files: dict[str, str] = {}
    dimension_names: dict[str, set[str]] = {}
    for config_id, config in zip(enabled_ids, enabled_configs, strict=True):
        config_names[config_id] = str(config.get("name") or config.get("semantic_config_name") or "")
        model_files[config_id] = model_snapshot_file_name(config)
        dimension_names[config_id] = {
            str((dim or {}).get("name") or "").strip()
            for dim in (config.get("dimensions") or [])
            if str((dim or {}).get("name") or "").strip()
        }
    return config_names, model_files, dimension_names


async def _run_locate_object_type(
    *,
    payload: Any,
    state: dict | None,
    bound_datasource_id: str,
    bound_user_id: str,
) -> str:
    """本体对象类型定位：批量返回对象类型绑定的语义模型/属性→维度对照/关系类型。

    可定位范围结构性收敛：注入 bundle 按「本轮启用语义模型」加载（与本体目录
    物化同源），对象不在范围内即列入 not_found 并给出候选，不泄露全量本体。
    定位不执行查询——查询仍由模型依据报告走 execute_query。
    """
    if not object_type_locator_enabled():
        raise ValueError("对象定位未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED=1")
    state = state or {}
    datasource_id = _get_datasource_id_from_state(state) or bound_datasource_id
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")
    user_id = str(state.get("user_id") or "").strip() or bound_user_id

    service = get_semantic_config_service()
    configs = await service.get_configs_async(datasource_id, str(user_id or ""))
    enabled_configs, enabled_ids = _locator_enabled_view(configs)
    if not enabled_configs:
        raise ValueError("当前数据源组没有启用语义模型，无法定位对象")
    bundle = await OntologyDao().load_injection_bundle_async(enabled_ids)
    config_names, model_files, dimension_names = _locator_model_index(enabled_ids, enabled_configs)

    report = build_object_type_locator_report(
        bundle,
        object_type_names=payload.object_types,
        config_names=config_names,
        model_files=model_files,
        semantic_config_id=payload.semantic_config_id,
        dimension_names=dimension_names,
    )
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            **report,
            "usage": build_locator_usage_hint(),
        },
    )


async def _run_list_object_types(
    *,
    state: dict | None,
    bound_datasource_id: str,
    bound_user_id: str,
) -> str:
    """本体对象类型清单（``list_object_types`` action）：轻量列出可定位对象类型与各自绑定模型。

    与 locate_object_type 同源同开关：bundle 按「本轮启用语义模型」加载，不泄露
    全量本体；清单只含对象类型名/显示名/描述 + 对象类型→绑定模型一跳，属性对照/
    关系类型/失修状态等详情走 locate_object_type。
    """
    if not object_type_locator_enabled():
        raise ValueError("本体对象类型清单未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED=1")
    state = state or {}
    datasource_id = _get_datasource_id_from_state(state) or bound_datasource_id
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")
    user_id = str(state.get("user_id") or "").strip() or bound_user_id

    service = get_semantic_config_service()
    configs = await service.get_configs_async(datasource_id, str(user_id or ""))
    enabled_configs, enabled_ids = _locator_enabled_view(configs)
    if not enabled_configs:
        raise ValueError("当前数据源组没有启用语义模型，无对象可列")
    bundle = await OntologyDao().load_injection_bundle_async(enabled_ids)
    config_names, model_files, _ = _locator_model_index(enabled_ids, enabled_configs)

    report = build_object_list_report(
        bundle,
        config_names=config_names,
        model_files=model_files,
    )
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            **report,
            "usage": build_object_list_usage_hint(),
        },
    )


async def _run_search_around(
    *,
    payload: Any,
    state: dict | None,
    bound_datasource_id: str,
    bound_user_id: str,
) -> str:
    """本体 search-around（``search_around`` action）：以单个对象类型为中心沿关系走一跳。

    与 locate 同源同开关：bundle 按「本轮启用语义模型」加载；``dimension_columns``
    从同一批启用模型 configs 构建（目录渲染同源的「维度名 → 物理列」索引），join
    翻译口径与 _ontology_catalog.yaml 单源。定位不执行查询——join 列供模型直接写
    关联 SQL。
    """
    if not object_type_locator_enabled():
        raise ValueError("本体关系探索未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_LOCATE_ENABLED=1")
    state = state or {}
    datasource_id = _get_datasource_id_from_state(state) or bound_datasource_id
    if not datasource_id:
        raise ValueError("当前会话未绑定数据源")
    user_id = str(state.get("user_id") or "").strip() or bound_user_id

    service = get_semantic_config_service()
    configs = await service.get_configs_async(datasource_id, str(user_id or ""))
    enabled_configs, enabled_ids = _locator_enabled_view(configs)
    if not enabled_configs:
        raise ValueError("当前数据源组没有启用语义模型，无法探索关系")
    bundle = await OntologyDao().load_injection_bundle_async(enabled_ids)
    config_names, model_files, dimension_names = _locator_model_index(enabled_ids, enabled_configs)

    report = build_search_around_report(
        bundle,
        object_type=payload.object_type,
        relation=payload.relation,
        semantic_config_id=payload.semantic_config_id,
        config_names=config_names,
        model_files=model_files,
        dimension_names=dimension_names,
        dimension_columns=config_dimension_columns(enabled_configs),
    )
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            **report,
            "usage": build_search_around_usage_hint(),
        },
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
                    sorts=[item.model_dump() for item in payload.sort],
                    limit=payload.limit,
                    offset=payload.offset,
                    semantic_config_id=payload.semantic_config_id,
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


async def _run_aggregate_objects(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """对象聚合（``aggregate_objects`` action）：分组 + 白名单指标，只读。"""
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
                return await aggregate_object_projection(
                    payload.object_type,
                    group_by=payload.group_by,
                    metrics=[item.model_dump() for item in payload.metrics],
                    filters=[item.model_dump() for item in payload.filters],
                    keyword=payload.keyword,
                    limit=payload.limit,
                    semantic_config_id=payload.semantic_config_id,
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
            semantic_config_id=payload.semantic_config_id,
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
                "聚合/统计仍走 data_analyzing_tool 的 execute_query，不要用对象读取结果自己拼 SQL。"
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
            bound_user_id=bound_user_id,
        )
    if action == "search_around":
        return await _run_search_around(
            payload=payload,
            state=state,
            bound_datasource_id=bound_datasource_id,
            bound_user_id=bound_user_id,
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
    if action == "list_execution_targets":
        return await _run_list_execution_targets(
            state=state,
            bound_datasource_id=bound_datasource_id,
            bound_user_id=bound_user_id,
            bound_operator_name=None,
        )
    # locate_object_type
    return await _run_locate_object_type(
        payload=payload,
        state=state,
        bound_datasource_id=bound_datasource_id,
        bound_user_id=bound_user_id,
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
        本体对象类型导航工具：按对象类型定位语义模型、沿关系探索关联对象（纯导航，不执行查询）。适合「订单相关的数据在哪个模型里」「订单和用户怎么关联」「下单用户的地区分布」这类业务对象导航与跨对象关联问题；常规查数流程以 `data-analyzing` 技能为准。

        Args:
            action: 操作类型，枚举：
        - list_object_types: 列出可定位本体对象类型——轻量清单（对象类型API名/显示名/描述 + 各自绑定模型及快照文件名），不含属性与关系类型详情；范围限定为本轮启用语义模型绑定的对象类型
        - locate_object_type: 按本体对象类型批量定位语义模型——返回绑定模型（含快照文件名）、属性→维度对照、对象类型间关系类型与失修标注；纯导航不执行查询，可定位对象类型先用 list_object_types 获取
        - search_around: 以单个对象类型为中心沿关系走一跳——每条关系返回物理连接列（from/to_columns，多模型绑定用 from/to_columns_by_model）与对端对象完整定位条目（属性/绑定/映射，其 relations 只留名字提示下一跳）；跨对象类型关联优先用它拿 join 列，不要靠 grep 模型 YAML 猜
        - get_object: 按对象类型 + 主键读取对象属性与一跳 links（只读；links 是对端对象引用，聚合查询仍走 execute_query）
        - find_objects: 按对象类型查询实例——关键词、属性过滤、排序、分页，返回对象标题/主键/属性与 total；只读，适合先找到对象再 get_object
        - aggregate_objects: 按对象类型分组聚合——group_by + count/count_distinct/sum/avg/min/max；适合对象数量/分布等只读聚合，复杂指标仍走 execute_query
        - list_execution_targets: 列出当前用户可执行的成员数据源（member_datasource_id/名称/机构编码）；用户指定机构或数据源时先调用它，再把选中的 ID 传给对象查询
            action_payload: 操作参数，结构随 action 变化：
        - list_object_types: {}（无参数，范围=本轮启用语义模型绑定的对象）
        - locate_object_type: {"object_types": [<对象API名>...]（单次最多10个）, "semantic_config_id": 可选只看该模型绑定}
        - search_around: {"object_type": "<中心对象API名>", "relation": 可选只展开该关系名, "semantic_config_id": 可选}
        - get_object: {"object_type": "<对象类型API名>", "object_key": "<对象主键值>", "member_datasource_ids": ["可选机构ID"], "semantic_config_id": 可选}
        - find_objects: {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "keyword": 可选, "filters": [{"property": "<属性>", "operator": "eq", "value": "<值>"}], "sort": [{"property": "<属性>", "direction": "asc"}], "limit": 10, "offset": 0}
        - aggregate_objects: {"object_type": "<对象类型API名>", "member_datasource_ids": ["可选机构ID"], "group_by": ["<属性>"], "metrics": [{"operation": "count"}, {"operation": "sum", "property": "<数值属性>"}]}
        - list_execution_targets: {}（无参数；返回当前用户可见执行对象）
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
