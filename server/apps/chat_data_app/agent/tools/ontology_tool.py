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
- ``save_object_set`` / ``load_object_set`` / ``list_object_sets``：static 对象集合的保存、
  分页加载与列表，让查询结果跨轮传递（temporary 24h 过期）；
- ``list_execution_targets``：列出当前用户可执行的成员数据源（member 维度查询范围）。

入参契约：``action`` + ``action_payload``（结构随 action 变化）+ ``intent``；payload
校验在工具实现入口经 ``validate_ontology_payload`` 完成（共用
``ONTOLOGY_ACTION_PAYLOAD_MODELS`` 映射表）——校验不能放进 args model_validator：
langgraph 会把 model_validator 抛出的校验错误过滤成空白消息，模型与用户都看不到原因。

参数契约由 ``action="help"`` 按 ``ONTOLOGY_ACTION_PAYLOAD_MODELS`` 现算（字段/必填/默认/上限），
不写进工具 description，也不写进技能文档——技能 ``references/ontology/`` 只负责
何时用哪个动作、顺序、口径与边界。同一份契约只维护在 pydantic 模型一处。
"""

from __future__ import annotations

from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

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
from apps.chat_data_app.services.ontology.graph import (
    GraphQueryError,
    GraphQueryFacade,
    latest_graph_as_of_async,
)
from apps.chat_data_app.services.ontology.object_projection import (
    ObjectProjectionError,
    aggregate_objects_multi_projection,
    ontology_action_execute_enabled,
    ontology_object_read_enabled,
    read_object_projection,
    search_objects_multi_projection,
)
from apps.chat_data_app.services.ontology.object_set_service import (
    OntologyObjectSetService,
    build_default_object_set_name,
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
        default=None, max_length=20, description="执行对象/机构范围；可传多个 member，工具按机构并行返回 results[]"
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
        default=None, max_length=20, description="执行对象/机构范围；可传多个 member，工具按机构并行返回 results[]"
    )
    group_by: list[str] = Field(default_factory=list, max_length=5, description="分组属性 API 名列表")
    metrics: list[_AggregateObjectsMetric] = Field(
        default_factory=list, max_length=5, description="属性白名单聚合指标（count/count_distinct/sum/avg/min/max）"
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
    object_key: str = Field(
        default="",
        max_length=512,
        description="目标对象主键值；create_object 类 Action 留空（标识值来自 parameters）",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Action 参数；按 list_action_types 的参数定义传"
    )


class _SaveObjectSetPayload(BaseModel):
    """``save_object_set`` 的入参结构：把查询结果主键保存为 static 对象集合。"""

    object_type: str = Field(..., min_length=1, max_length=128, description='对象类型 API 名，如 "customer"')
    member_datasource_id: str = Field(
        ..., min_length=1, max_length=100, description="集合内对象所属机构 ID（同一集合只放同机构对象）"
    )
    object_keys: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="对象主键列表（find_objects 返回的 object_key 或 local_key），单次最多 100 个",
    )
    set_name: str = Field(default="", max_length=128, description="可选集合 API 名；留空自动生成")
    display_name: str = Field(default="", max_length=128, description="可选展示名")
    temporary: bool = Field(default=True, description="临时集合（24h 过期）；False 为长期集合")
    allow_overwrite: bool = Field(default=False, description="覆盖同名既有集合时为 True")


class _LoadObjectSetPayload(BaseModel):
    """``load_object_set`` 的入参结构：按集合名分页加载对象属性。"""

    object_type: str = Field(..., min_length=1, max_length=128, description='对象类型 API 名，如 "customer"')
    set_name: str = Field(..., min_length=1, max_length=128, description="save_object_set 返回的集合名")
    limit: int = Field(default=20, ge=1, le=100, description="本次加载对象数上限")
    offset: int = Field(default=0, ge=0, description="集合内偏移量")


class _ListObjectSetsPayload(BaseModel):
    """``list_object_sets`` 的入参结构：列出当前业务域已保存的对象集合。"""

    object_type: str = Field(default="", max_length=128, description="可选；只列指定对象类型的集合")


class _TraverseObjectsPayload(BaseModel):
    """``traverse_objects`` 的入参结构：对象起点 + 有界多跳/路径。"""

    object_key: str = Field(
        ..., min_length=1, max_length=512, description="起点对象完整身份 member:object_type:local_key"
    )
    depth: int = Field(default=1, ge=1, le=3, description="最大跳数，1~3")
    direction: Literal["out", "in", "both"] = Field(default="both", description="关系方向：out/in/both")
    limit: int = Field(default=50, ge=1, le=200, description="返回路径上限")
    member_datasource_ids: list[str] | None = Field(
        default=None, max_length=20, description="机构范围；图查询必须且只能指定一个 member"
    )


class _FindGraphPathsPayload(BaseModel):
    """``find_graph_paths`` 的入参结构：两个对象之间的有界路径。"""

    from_object_key: str = Field(..., min_length=1, max_length=512, description="起点对象完整身份")
    to_object_key: str = Field(..., min_length=1, max_length=512, description="终点对象完整身份")
    max_depth: int = Field(default=3, ge=1, le=3, description="最大路径长度，1~3")
    limit: int = Field(default=10, ge=1, le=200, description="返回路径上限")
    member_datasource_ids: list[str] | None = Field(
        default=None, max_length=20, description="机构范围；图查询必须且只能指定一个 member"
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
    "list_execution_targets": _ListExecutionTargetsPayload,
    "list_action_types": _ListActionTypesPayload,
    "execute_action": _ExecuteActionPayload,
    "save_object_set": _SaveObjectSetPayload,
    "load_object_set": _LoadObjectSetPayload,
    "list_object_sets": _ListObjectSetsPayload,
    "traverse_objects": _TraverseObjectsPayload,
    "find_graph_paths": _FindGraphPathsPayload,
}

# help 动作：模型不确定某动作的 payload 结构时按需查字段契约。字段/必填/上限一律由
# ONTOLOGY_ACTION_PAYLOAD_MODELS 现算（见 _payload_fields），不手写第二份参数文档；
# 这里只维护每个动作的一句话用法与对应能力文档，供 help 输出指路。
HELP_ACTION = "help"
ONTOLOGY_HELP_DOC_ROOT = "/mnt/skills/data-analyzing/references/ontology"
ONTOLOGY_ACTION_HELP: dict[str, tuple[str, str]] = {
    "list_object_types": ("列出当前业务域可导航对象类型（轻量清单，不执行查询），只读。", "query.md"),
    "locate_object_type": (
        "按对象类型批量返回属性、数据源绑定与关系跳转，只读导航，不执行查询。",
        "query.md",
    ),
    "find_objects": ("按对象类型查询实例（关键词/属性过滤/排序/分页），只读，按机构返回 results[]。", "query.md"),
    "get_object": ("按对象类型 + 主键读取单个对象属性与一跳 links，只读。", "query.md"),
    "aggregate_objects": (
        "按对象类型做属性白名单分组聚合（count/count_distinct/sum/avg/min/max），只读。",
        "query.md",
    ),
    "list_execution_targets": ("列出当前用户可执行的成员数据源（机构），用于取 member_datasource_ids。", "query.md"),
    "save_object_set": ("把查询结果的对象主键保存为对象集合，供跨轮或后续动作引用。", "query.md"),
    "load_object_set": ("按集合名分页加载对象属性；缺失对象跳过，过期集合报错。", "query.md"),
    "list_object_sets": ("列出当前业务域已保存的对象集合。", "query.md"),
    "search_around": (
        "以单个对象类型为中心沿关系走一跳，返回物理连接列与对端对象导航条目。",
        "relations.md",
    ),
    "traverse_objects": ("从对象出发做 1~3 跳关系遍历（图查询，需 Nebula 且图已同步）。", "relations.md"),
    "find_graph_paths": ("查询两个对象之间的有界路径（图查询，需 Nebula 且图已同步）。", "relations.md"),
    "list_action_types": ("列出当前业务域可执行 Action 定义与参数，只读。", "actions.md"),
    "execute_action": ("执行 Action（属性覆盖层/创建对象 + 审计），受部署开关门控。", "actions.md"),
}


class OntologyToolInput(BaseModel):
    """本体导航工具入参：按 ``action`` 分派，``action_payload`` 结构随 action 变化。

    共 14 个业务 action：``list_object_types`` / ``locate_object_type`` / ``search_around`` 负责导航，
    ``get_object`` / ``find_objects`` 负责对象读取，``aggregate_objects`` 负责属性白名单聚合，
    ``list_execution_targets`` 列出执行对象，``list_action_types`` / ``execute_action`` 负责 Action，
    ``save_object_set`` / ``load_object_set`` / ``list_object_sets`` 负责对象集合跨轮传递；
    另有 ``help`` 动作按需返回某个业务 action 的字段契约（由 payload 模型现算）。
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
        "list_action_types",
        "execute_action",
        "save_object_set",
        "load_object_set",
        "list_object_sets",
        "traverse_objects",
        "find_graph_paths",
        "help",
    ] = Field(
        ...,
        description=(
            "操作类型：导航 / 对象读取 / 对象集合 / 关系与图 / 动作"
            "（分组与选型见技能 references/ontology/ 文档）；"
            "参数不确定时用 action=help 查字段/必填/上限。"
        ),
    )
    action_payload: dict[str, Any] = Field(
        ...,
        description=(
            "操作参数 JSON，结构随 action 变化；完整模板与字段约束见 data-analyzing 技能的 "
            "references/ontology/ 对应能力文档（参数不合法会返回具体字段错误，按提示修正）。"
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
        raise ValueError(
            f"不支持的 action: {action}；可用 action: {', '.join(ONTOLOGY_ACTION_PAYLOAD_MODELS)}；"
            f'不确定 payload 结构时先传 action="{HELP_ACTION}" 查询'
        )
    try:
        return payload_model.model_validate(action_payload)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ())) or 'action_payload'}: {error.get('msg', '')}"
            for error in exc.errors()
        )
        raise ValueError(f"action={action} 的 action_payload 校验失败：{detail}") from None


# help 输出用的 pydantic 约束标签：值直接从 Field 元数据读，不复制字段说明
_CONSTRAINT_LABELS = {
    "Ge": "ge",
    "Gt": "gt",
    "Le": "le",
    "Lt": "lt",
    "MinLen": "min_length",
    "MaxLen": "max_length",
    "MultipleOf": "multiple_of",
}


def _unwrap_optional(annotation: Any) -> Any:
    """去掉 Optional 外壳；多类型联合原样返回。"""
    origin = get_origin(annotation)
    if origin not in (Union, UnionType):
        return annotation
    members = [member for member in get_args(annotation) if member is not type(None)]
    return members[0] if len(members) == 1 else annotation


def _type_label(annotation: Any) -> str:
    """把 pydantic 字段注解压成模型可读的短类型标签。"""
    normalized = _unwrap_optional(annotation)
    origin = get_origin(normalized)
    if origin in (list, tuple):
        args = get_args(normalized)
        return f"array<{_type_label(args[0])}>" if args else "array"
    if origin is dict:
        return "object"
    if origin is Literal:
        return "enum(" + "|".join(str(value) for value in get_args(normalized)) + ")"
    if isinstance(normalized, type) and issubclass(normalized, BaseModel):
        return "object"
    if isinstance(normalized, type):
        return normalized.__name__
    return str(normalized)


def _field_type_constraints(field: Any) -> dict[str, Any]:
    """提取 Field(ge/le/min_length/max_length/...) 约束。"""
    constraints: dict[str, Any] = {}
    for meta in field.metadata or ():
        label = _CONSTRAINT_LABELS.get(type(meta).__name__)
        value = getattr(meta, label, None) if label else None
        if label and value is not None:
            constraints[label] = value
    return constraints


def _nested_payload_model(annotation: Any) -> tuple[type[BaseModel], str] | None:
    """嵌套 payload 模型及挂载键：数组用 item_fields，单个对象用 fields。"""
    normalized = _unwrap_optional(annotation)
    origin = get_origin(normalized)
    if origin in (list, tuple):
        args = get_args(normalized)
        if not args:
            return None
        item = _unwrap_optional(args[0])
        return (item, "item_fields") if isinstance(item, type) and issubclass(item, BaseModel) else None
    if isinstance(normalized, type) and issubclass(normalized, BaseModel):
        return normalized, "fields"
    return None


def _payload_field_spec(name: str, field: Any, *, depth: int) -> dict[str, Any]:
    """单个 payload 字段的紧凑契约：类型/必填/默认/上限；嵌套对象下沉一层。"""
    spec: dict[str, Any] = {
        "name": name,
        "type": _type_label(field.annotation),
        "required": field.is_required(),
    }
    if not spec["required"]:
        default = field.get_default(call_default_factory=True)
        if default is not None:
            spec["default"] = default
    spec.update(_field_type_constraints(field))
    if spec["type"].startswith("array"):
        # 数组的 min_length/max_length 是元素个数，改标签避免和字符串长度混淆
        for old, new in (("min_length", "min_items"), ("max_length", "max_items")):
            if old in spec:
                spec[new] = spec.pop(old)
    nested = _nested_payload_model(field.annotation)
    if nested is not None and depth > 0:
        nested_model, nested_key = nested
        spec[nested_key] = [
            _payload_field_spec(nested_name, nested_field, depth=depth - 1)
            for nested_name, nested_field in nested_model.model_fields.items()
        ]
    return spec


def _payload_fields(model: type[BaseModel]) -> list[dict[str, Any]]:
    """help 输出的字段契约：遍历 pydantic 模型现算，不维护第二份手写参数表。"""
    return [_payload_field_spec(name, field, depth=1) for name, field in model.model_fields.items()]


def _payload_spec_map(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """字段契约转成「字段名 → 契约」的紧凑 JSON 结构，避免每项重复 name 键。"""
    spec_map: dict[str, dict[str, Any]] = {}
    for item in fields:
        entry = {key: value for key, value in item.items() if key != "name"}
        for nested_key in ("fields", "item_fields"):
            nested = entry.get(nested_key)
            if nested:
                entry[nested_key] = _payload_spec_map(nested)
        spec_map[str(item["name"])] = entry
    return spec_map


def _run_help(action_payload: Any) -> str:
    """按需返回单个动作的用法与 payload 字段契约（JSON）；不传动作时列出全部动作。"""
    target = str((action_payload or {}).get("action") or "").strip()
    if not target:
        return tool_result_payload(
            ONTOLOGY_TOOL_NAME,
            {
                "success": True,
                "usage": 'ontology_tool(action="<action>", action_payload={...}, intent="<一句中文目的>")',
                "actions": {name: usage for name, (usage, _doc) in ONTOLOGY_ACTION_HELP.items()},
                "help": 'ontology_tool(action="help", action_payload={"action": "<action>"})  # 查单个动作参数',
            },
        )
    payload_model = ONTOLOGY_ACTION_PAYLOAD_MODELS.get(target)
    if payload_model is None:
        raise ValueError(f"未知 action: {target}；可用 action: {', '.join(ONTOLOGY_ACTION_PAYLOAD_MODELS)}")
    usage, doc_file = ONTOLOGY_ACTION_HELP[target]
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            "action": target,
            "usage": usage,
            "payload": _payload_spec_map(_payload_fields(payload_model)),
            "workflow_doc": f"{ONTOLOGY_HELP_DOC_ROOT}/{doc_file}",
        },
    )


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


def _object_preview_rows(object_type: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 results[] 抽前几个对象做卡片预览（与历史 preview.objects 同形）。"""
    objects: list[dict[str, Any]] = []
    for member in result.get("results") or []:
        for row in member.get("data") or []:
            if not isinstance(row, dict):
                continue
            properties = {key: value for key, value in row.items() if key != "object_key"}
            objects.append({"object_type": object_type, "object_key": row.get("object_key"), "properties": properties})
            if len(objects) >= 10:
                return objects
    return objects


def _aggregate_metric_keys(group_by: list[str], result: dict[str, Any]) -> list[str]:
    """取首个成功成员的列名，去掉 group_by 得到聚合指标列。"""
    for member in result.get("results") or []:
        if member.get("status") != "succeeded":
            continue
        columns = [str(name) for name in member.get("columns") or []]
        return columns[len(group_by) :]
    return []


def _aggregate_preview_rows(group_by: list[str], result: dict[str, Any]) -> list[dict[str, Any]]:
    """从 results[] 抽前几组聚合行，恢复 preview.groups 的 keys/metrics 结构。"""
    groups: list[dict[str, Any]] = []
    for member in result.get("results") or []:
        for row in member.get("data") or []:
            if not isinstance(row, dict):
                continue
            groups.append(
                {
                    "keys": {name: row.get(name) for name in group_by},
                    "metrics": {key: value for key, value in row.items() if key not in group_by},
                }
            )
            if len(groups) >= 20:
                return groups
    return groups


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


def _object_set_summary(item: dict[str, Any]) -> dict[str, Any]:
    """对象集合对外摘要：不返回全部主键，避免工具结果过大。"""
    return {
        "set_name": item.get("set_name"),
        "display_name": item.get("display_name"),
        "object_type": item.get("object_type_name"),
        "member_datasource_id": item.get("member_datasource_id"),
        "object_key_count": item.get("object_key_count"),
        "temporary": item.get("temporary"),
        "expires_at": item.get("expires_at"),
        "update_time": item.get("update_time"),
    }


async def _run_save_object_set(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """保存 static 对象集合：只存主键，不复制属性；供跨轮/后续动作引用。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    await _ensure_navigable_object_type(payload.object_type)
    state = state or {}
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    operator_name = str(state.get("operator_name") or "").strip() or user_id
    set_name = str(payload.set_name or "").strip() or build_default_object_set_name(payload.object_type)
    saved = await OntologyObjectSetService().upsert_object_set_async(
        payload.object_type,
        set_name,
        payload.model_dump(),
        allow_overwrite=payload.allow_overwrite,
        operator=user_id,
        operator_name=operator_name,
    )
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            "object_set": _object_set_summary(saved),
            "usage": "集合已保存；后续可用 load_object_set 按 set_name 加载对象属性，或把 set_name 作为动作目标集合引用。",
        },
    )


async def _run_load_object_set(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """按集合顺序分页加载对象属性；过期集合显式报错，缺失对象跳过。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    await _ensure_navigable_object_type(payload.object_type)
    state = state or {}
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    operator_name = str(state.get("operator_name") or "").strip() or user_id
    result = await OntologyObjectSetService().load_object_set_objects_async(
        payload.object_type,
        payload.set_name,
        limit=min(int(payload.limit), 50),
        offset=int(payload.offset),
        user_id=user_id,
        username=operator_name,
    )
    return tool_result_payload(ONTOLOGY_TOOL_NAME, {"success": True, **result})


async def _run_list_object_sets(
    *,
    payload: Any,
    state: dict | None,
    bound_datasource_id: str,
) -> str:
    """列出当前业务域已保存的对象集合（可限定对象类型）。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    service = OntologyObjectSetService()
    if payload.object_type:
        await _ensure_navigable_object_type(payload.object_type)
        items = await service.list_object_sets_async(payload.object_type)
        return tool_result_payload(
            ONTOLOGY_TOOL_NAME,
            {"success": True, "count": len(items), "object_sets": [_object_set_summary(item) for item in items]},
        )
    bundle = await _load_domain_bundle(state, bound_datasource_id)
    domain_names = {
        str(item.get("object_type_name") or "") for item in bundle.get("object_types") or [] if isinstance(item, dict)
    }
    items = [item for item in await service.list_object_sets_async() if item.get("object_type_name") in domain_names]
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {"success": True, "count": len(items), "object_sets": [_object_set_summary(item) for item in items]},
    )


async def _run_find_objects(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """按对象类型查询实例（``find_objects`` action）：多机构一次下发，返回统一 results[]。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    await _ensure_navigable_object_type(payload.object_type)
    state = state or {}
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    member_ids = [member for member in _query_member_ids(payload.member_datasource_ids, state) if member] or None
    try:
        result = await search_objects_multi_projection(
            payload.object_type,
            member_datasource_ids=member_ids,
            keyword=payload.keyword,
            filters=[item.model_dump() for item in payload.filters],
            where=payload.where.model_dump() if payload.where else None,
            sorts=[item.model_dump() for item in payload.sort],
            limit=payload.limit,
            offset=payload.offset,
            user_id=user_id,
        )
    except ObjectProjectionError as err:
        raise ValueError(str(err)) from err
    result["preview"] = {"kind": "objects", "objects": _object_preview_rows(payload.object_type, result)}
    return build_internal_query_result(result, query_mode="ontology_find_objects")


async def _run_aggregate_objects(
    *,
    payload: Any,
    state: dict | None,
    bound_user_id: str,
) -> str:
    """对象聚合入口：按属性白名单聚合。"""
    if not ontology_object_read_enabled():
        raise ValueError("对象实例读取未启用：需设置环境变量 CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1")
    await _ensure_navigable_object_type(payload.object_type)
    state = state or {}
    user_id = str(state.get("user_id") or "").strip() or bound_user_id
    member_ids = _query_member_ids(payload.member_datasource_ids, state)
    if not payload.metrics:
        raise ValueError("aggregate_objects 需要 metrics 聚合指标")
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
    """属性白名单聚合（count/count_distinct/sum/avg/min/max + group_by），多机构统一 results[]。"""
    members = [member for member in member_ids if member] or None
    try:
        result = await aggregate_objects_multi_projection(
            payload.object_type,
            member_datasource_ids=members,
            group_by=payload.group_by,
            metrics=[item.model_dump() for item in payload.metrics],
            filters=[item.model_dump() for item in payload.filters],
            where=payload.where.model_dump() if payload.where else None,
            keyword=payload.keyword,
            limit=payload.limit,
            user_id=user_id,
        )
    except ObjectProjectionError as err:
        raise ValueError(str(err)) from err
    group_by = [str(name) for name in payload.group_by or []]
    result["preview"] = {
        "kind": "aggregate",
        "group_by": group_by,
        "metric_keys": _aggregate_metric_keys(group_by, result),
        "groups": _aggregate_preview_rows(group_by, result),
    }
    return build_internal_query_result(result, query_mode="ontology_aggregate_objects")


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


async def _run_traverse_objects(
    *,
    payload: Any,
    state: dict | None,
) -> str:
    """从对象出发做有界多跳；只返回对象身份/标题/关系，属性回源。"""
    member_ids = _query_member_ids(payload.member_datasource_ids, state)
    members = [member for member in member_ids if member]
    if len(members) != 1:
        raise ValueError("图查询必须且只能指定一个 member_datasource_id")
    facade = GraphQueryFacade(
        user_id=str((state or {}).get("user_id") or ""),
        username=str((state or {}).get("operator_name") or ""),
    )
    try:
        result = await facade.traverse_objects(
            payload.object_key,
            depth=payload.depth,
            direction=payload.direction,
            limit=payload.limit,
            member_ids=members,
        )
    except GraphQueryError as err:
        raise ValueError(str(err)) from err
    if facade.backend == "nebula":
        result.graph_as_of = await latest_graph_as_of_async(members[0])
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            **result.as_dict(),
            "usage": "图查询只返回对象身份/关系；需要属性时用 get_object 回源，不要用图结果代替对象属性。",
        },
    )


async def _run_find_graph_paths(
    *,
    payload: Any,
    state: dict | None,
) -> str:
    """两个对象之间的有界路径；用于多跳关联/路径解释。"""
    member_ids = _query_member_ids(payload.member_datasource_ids, state)
    members = [member for member in member_ids if member]
    if len(members) != 1:
        raise ValueError("图查询必须且只能指定一个 member_datasource_id")
    facade = GraphQueryFacade(
        user_id=str((state or {}).get("user_id") or ""),
        username=str((state or {}).get("operator_name") or ""),
    )
    try:
        result = await facade.find_paths(
            payload.from_object_key,
            payload.to_object_key,
            max_depth=payload.max_depth,
            limit=payload.limit,
            member_ids=members,
        )
    except GraphQueryError as err:
        raise ValueError(str(err)) from err
    if facade.backend == "nebula":
        result.graph_as_of = await latest_graph_as_of_async(members[0])
    return tool_result_payload(
        ONTOLOGY_TOOL_NAME,
        {
            "success": True,
            **result.as_dict(),
            "usage": "路径只说明关系链；属性与业务口径仍需回源对象/语义层。",
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
    if action == HELP_ACTION:
        return _run_help(action_payload)
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
    if action == "save_object_set":
        return await _run_save_object_set(payload=payload, state=state, bound_user_id=bound_user_id)
    if action == "load_object_set":
        return await _run_load_object_set(payload=payload, state=state, bound_user_id=bound_user_id)
    if action == "list_object_sets":
        return await _run_list_object_sets(
            payload=payload,
            state=state,
            bound_datasource_id=bound_datasource_id,
        )
    if action == "traverse_objects":
        return await _run_traverse_objects(payload=payload, state=state)
    if action == "find_graph_paths":
        return await _run_find_graph_paths(payload=payload, state=state)
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
        本体对象 / 关系 / Action 查询工具。

        调用前的目录阅读、业务工作流、查询顺序与 action_payload 结构见 data-analyzing 技能的
        references/ontology/ 文档（query.md / relations.md / actions.md）；
        字段契约（必填/默认/上限）用 action=help 动态查询，本文与技能均不内联参数模板。
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
    "ONTOLOGY_ACTION_HELP",
    "HELP_ACTION",
    "OntologyToolInput",
    "create_ontology_tool",
    "validate_ontology_payload",
]
