"""查询能力路由单一入口：当前业务域二选一（本体 / 语义）。

2026-10-23 起语义层与本体层在同一业务域内彻底二选一，规则只在这里判定，
提示词、工具注册、目录物化、上下文预算等消费方一律读取本模块的结论，
不允许各自探测 ``_ontology_catalog.yaml`` / ``_semantic_catalog.yaml`` 或
按模型映射表写第二份选择逻辑。

判断范围（明确口径）：

- **业务域 = 当前数据源组**（会话绑定的 ``datasource_id``，即数据源组 ID）。
- **有本体**：该数据源组内至少存在一个 ``certified`` / ``experimental`` 的
  活跃对象类型，且该对象类型已配置 Logical Dataset source binding、其 Dataset
  在该组下有 member binding（对象查询的必要条件，见 ``object_projection``）。
  只有对象定义没有可用数据源绑定不算「有本体」——那种对象查不了数，进本体模式
  只会把可用链路一起关掉。
- **无本体**：其余情况（含部署级开关关闭、会话未绑定数据源组、探测异常）。
  无本体域保持语义模型目录与 ``data_analyzing_tool`` 链路。

部署级总闸 :func:`ontology_injection_enabled`（``CHAT_DATA_ONTOLOGY_INJECTION_ENABLED``
默认关闭）关闭时一律语义模式：本体链路是可选部署能力，未开启的部署不应因
库里存在本体对象类型而改变既有问数行为。

一次 run 内经过 ``prematerialize_enabled_semantic_models`` 时把判定结果发布到
ContextVar，供同步装配路径（工具注册）与提示词构建读取；未发布时默认语义模式
（历史回放、测试直连装配等入口保持原行为）。判定结果按数据源组做短 TTL 缓存，
避免一次 run 内目录物化 / 导航工具重复探测；管理端修改对象类型后最迟
``_BUNDLE_CACHE_TTL_SECONDS`` 秒生效。
"""

from __future__ import annotations

import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from typing import Any

from utils.logger import get_logger

from ...daos.ontology_dao import OntologyDao
from ..dataset.dataset_service import logical_dataset_service

logger = get_logger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

# 活跃对象类型状态（目录与查询共同口径）
ACTIVE_OBJECT_TYPE_STATUSES = ("certified", "experimental")

# 领域 bundle 短 TTL 缓存：一次 run 内目录物化 + 导航工具共用；管理端改动最迟 TTL 后生效。
_BUNDLE_CACHE_TTL_SECONDS = 5.0
_BUNDLE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class QueryMode(str, Enum):
    """当前业务域的查询能力形态。"""

    ONTOLOGY = "ontology"
    SEMANTIC = "semantic"


@dataclass(frozen=True)
class QueryModeDecision:
    """一次模式判定结果（含判定依据，便于日志与排查）。"""

    mode: QueryMode
    datasource_group_id: str | None = None
    active_object_type_count: int = 0
    reason: str = ""
    # 探测异常降级时保留真实原因；正常判定为空。调用方/健康检查据此区分
    # 「业务域没有本体」和「本体探测基础设施故障」，避免静默降级。
    degraded_reason: str = ""

    @property
    def is_degraded(self) -> bool:
        return bool(self.degraded_reason)

    @property
    def is_ontology(self) -> bool:
        return self.mode is QueryMode.ONTOLOGY

    @property
    def is_semantic(self) -> bool:
        return self.mode is QueryMode.SEMANTIC


def hybrid_query_mode_enabled() -> bool:
    """混合模式开关：本体业务域同时注入语义模型目录并装配语义查询工具。"""
    return str(os.getenv("CHAT_DATA_QUERY_MODE_HYBRID") or "").strip().lower() in _TRUTHY


def ontology_injection_enabled() -> bool:
    """本体消费链路部署级总闸（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED") or "").strip().lower() in _TRUTHY


def clear_query_mode_cache() -> None:
    """清空领域 bundle 缓存（测试与显式刷新入口）。"""
    _BUNDLE_CACHE.clear()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _cached_bundle(datasource_group_id: str) -> dict[str, Any] | None:
    cached = _BUNDLE_CACHE.get(datasource_group_id)
    if cached is None:
        return None
    expires_at, bundle = cached
    if time.monotonic() >= expires_at:
        _BUNDLE_CACHE.pop(datasource_group_id, None)
        return None
    return bundle


def _cache_bundle(datasource_group_id: str, bundle: dict[str, Any]) -> None:
    _BUNDLE_CACHE[datasource_group_id] = (time.monotonic() + _BUNDLE_CACHE_TTL_SECONDS, bundle)


def _dataset_columns(dataset: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Dataset 逻辑列索引与「逻辑列 → 物理列」映射。"""
    columns: dict[str, dict[str, Any]] = {}
    physical_columns: dict[str, str] = {}
    for column in dataset.get("columns") or []:
        if not isinstance(column, dict):
            continue
        name = _text(column.get("name"))
        if not name:
            continue
        columns[name] = column
        physical = _text(column.get("physical_column")) or name
        physical_columns[name] = physical
    return columns, physical_columns


def _group_bindings(dataset: dict[str, Any], datasource_group_id: str) -> list[dict[str, Any]]:
    """Dataset 在指定数据源组下的 member binding（按 member ID 排序，保证确定性）。"""
    bindings = [
        binding
        for binding in dataset.get("bindings") or []
        if isinstance(binding, dict) and _text(binding.get("datasource_group_id")) == datasource_group_id
    ]
    bindings.sort(key=lambda item: _text(item.get("member_datasource_id")))
    return bindings


def _source_binding_view(
    binding: dict[str, Any],
    dataset: dict[str, Any],
    group_bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    """单个对象类型的数据源绑定视图（目录/导航共用的唯一形状）。

    ``column_mappings`` 是对象属性 → Dataset 逻辑列；``columns`` 是逻辑列 →
    物理列。关系类型的 join 列翻译只走这两级，不再依赖语义模型的维度。
    """
    _, physical_columns = _dataset_columns(dataset)
    column_mappings = {
        _text(key): _text(value) for key, value in (binding.get("column_mappings") or {}).items() if _text(key)
    }
    members = [
        {
            "member_datasource_id": _text(item.get("member_datasource_id")),
            "physical_table": _text(item.get("physical_table")),
        }
        for item in group_bindings
    ]
    return {
        "dataset_id": int(dataset.get("id") or binding.get("dataset_id") or 0),
        "dataset_name": _text(dataset.get("dataset_name")),
        "dataset_display_name": _text(dataset.get("display_name")) or _text(dataset.get("dataset_name")),
        "physical_table": _text(group_bindings[0].get("physical_table")) if group_bindings else "",
        "column_mappings": column_mappings,
        "columns": {
            logical: physical for logical, physical in physical_columns.items() if logical in column_mappings.values()
        },
        "members": members,
    }


def translate_binding_columns(columns: list[Any] | None, binding: dict[str, Any] | None) -> tuple[list[str], bool]:
    """按 Dataset 列映射把属性名翻译成物理列，返回 (物理列, 是否未解析)。

    两级翻译：属性 → 逻辑列（``source_binding.column_mappings``）→ 物理列
    （``source_binding.columns``）。任一级缺失时保留上一级名称并标未解析；
    目录渲染与关系导航共用这一份翻译口径。
    """
    column_mappings = (binding or {}).get("column_mappings") or {}
    physical_columns = (binding or {}).get("columns") or {}
    translated: list[str] = []
    unresolved = False
    for column in columns or []:
        property_name = _text(column)
        if not property_name:
            continue
        logical_column = _text(column_mappings.get(property_name))
        if not logical_column:
            translated.append(property_name)
            unresolved = True
            continue
        physical_column = _text(physical_columns.get(logical_column))
        if not physical_column:
            translated.append(logical_column)
            unresolved = True
            continue
        translated.append(physical_column)
    return translated, unresolved


async def _load_dataset_detail(dataset_id: int, cache: dict[int, dict[str, Any] | None]) -> dict[str, Any] | None:
    if dataset_id in cache:
        return cache[dataset_id]
    try:
        detail = await logical_dataset_service.get_dataset_async_by_id_async(dataset_id)
    except Exception as exc:  # noqa: BLE001 - 单个 dataset 读取失败按「无绑定」处理并记录
        logger.warning("本体域探测：读取 Dataset 失败 dataset_id=%s error=%s", dataset_id, exc)
        detail = None
    cache[dataset_id] = detail
    return detail


async def _domain_object_type_entry(
    dao: OntologyDao,
    object_type: dict[str, Any],
    datasource_group_id: str,
    dataset_cache: dict[int, dict[str, Any] | None],
) -> dict[str, Any] | None:
    """单个对象类型 → 域内条目（无 source binding / Dataset 不在本组时返回 None）。"""
    binding = await dao.get_source_binding_async(int(object_type.get("id") or 0))
    dataset_id = int((binding or {}).get("dataset_id") or 0)
    if not dataset_id:
        return None
    dataset = await _load_dataset_detail(dataset_id, dataset_cache)
    if dataset is None:
        return None
    group_bindings = _group_bindings(dataset, datasource_group_id)
    if not group_bindings:
        # 对象类型绑定的是其它数据源组的 Dataset：不属于当前业务域。
        return None
    entry = dict(object_type)
    entry["source_binding"] = _source_binding_view((binding or {}), dataset, group_bindings)
    return entry


async def _domain_object_types(
    raw_bundle: dict[str, Any],
    datasource_group_id: str,
    *,
    stop_at_first: bool,
) -> list[dict[str, Any]]:
    """过滤出绑定到指定数据源组的活跃对象类型，附带 ``source_binding`` 视图。

    ``stop_at_first`` 供模式判定提前短路（找到一个可用对象类型即可判有本体）；
    目录/导航需要完整集合时关闭。
    """
    dao = OntologyDao()
    dataset_cache: dict[int, dict[str, Any] | None] = {}
    kept: list[dict[str, Any]] = []
    for object_type in raw_bundle.get("object_types") or []:
        if not isinstance(object_type, dict):
            continue
        entry = await _domain_object_type_entry(dao, object_type, datasource_group_id, dataset_cache)
        if entry is None:
            continue
        kept.append(entry)
        if stop_at_first:
            break
    return kept


def _filter_bundle(
    raw_bundle: dict[str, Any],
    object_types: list[dict[str, Any]],
    datasource_group_id: str,
) -> dict[str, Any]:
    """按域内对象类型裁剪属性/关系类型，返回目录与导航共用的 bundle。"""
    kept_ids = {int(item.get("id") or 0) for item in object_types}
    properties = [
        item
        for item in raw_bundle.get("properties") or []
        if isinstance(item, dict) and int(item.get("object_type_id") or 0) in kept_ids
    ]
    relation_types = [
        item
        for item in raw_bundle.get("relation_types") or []
        if isinstance(item, dict)
        and int(item.get("from_object_type_id") or 0) in kept_ids
        and int(item.get("to_object_type_id") or 0) in kept_ids
    ]
    return {
        "datasource_group_id": datasource_group_id,
        "object_types": object_types,
        "properties": properties,
        "relation_types": relation_types,
    }


async def load_domain_ontology_bundle_async(datasource_group_id: str | None) -> dict[str, Any]:
    """当前数据源组可消费的本体 bundle（对象类型/属性/关系类型 + 数据源绑定视图）。

    返回空 ``object_types`` 表示该业务域无可用本体。异常向上抛出，由调用方决定
    降级口（模式判定降级语义、目录物化只告警）。
    """
    group_id = _text(datasource_group_id)
    if not group_id:
        return {"datasource_group_id": "", "object_types": [], "properties": [], "relation_types": []}
    cached = _cached_bundle(group_id)
    if cached is not None:
        return cached
    raw_bundle = await OntologyDao().load_injection_bundle_async()
    object_types = await _domain_object_types(raw_bundle, group_id, stop_at_first=False)
    bundle = _filter_bundle(raw_bundle, object_types, group_id)
    _cache_bundle(group_id, bundle)
    return bundle


async def _domain_has_active_object_type(datasource_group_id: str) -> int:
    """探测域内可用对象类型数量（0/1，短路；数量只作判定依据展示）。"""
    raw_bundle = await OntologyDao().load_injection_bundle_async()
    has_any_active = any(
        _text((item or {}).get("status")) in ACTIVE_OBJECT_TYPE_STATUSES
        for item in raw_bundle.get("object_types") or []
    )
    if not has_any_active:
        return 0
    object_types = await _domain_object_types(raw_bundle, datasource_group_id, stop_at_first=True)
    return 1 if object_types else 0


def decide_query_mode(
    datasource_group_id: str | None,
    *,
    object_type_count: int,
    injection_enabled: bool | None = None,
) -> QueryModeDecision:
    """纯函数判定（不触库）：唯一选择规则，消费方与测试共用。"""
    group_id = _text(datasource_group_id)
    enabled = ontology_injection_enabled() if injection_enabled is None else bool(injection_enabled)
    if not enabled:
        return QueryModeDecision(QueryMode.SEMANTIC, group_id or None, 0, "ontology_injection_disabled")
    if not group_id:
        return QueryModeDecision(QueryMode.SEMANTIC, None, 0, "datasource_group_missing")
    if int(object_type_count or 0) <= 0:
        return QueryModeDecision(QueryMode.SEMANTIC, group_id, 0, "no_domain_object_type")
    return QueryModeDecision(QueryMode.ONTOLOGY, group_id, int(object_type_count), "domain_object_type_active")


async def resolve_query_mode_async(datasource_group_id: str | None) -> QueryModeDecision:
    """异步判定入口：部署开关 → 数据源组 → 域内活跃对象类型。

    探测异常降级为语义模式并记录真实错误：模式探测属于基础设施故障，
    不能把会话启动一起打挂；数据库不可用时语义物化本身也会失败并走原有
    fail-fast 口径。
    """
    group_id = _text(datasource_group_id)
    if not ontology_injection_enabled():
        return decide_query_mode(group_id, object_type_count=0, injection_enabled=False)
    if not group_id:
        return decide_query_mode(None, object_type_count=0)
    try:
        count = await _domain_has_active_object_type(group_id)
    except Exception as exc:  # noqa: BLE001 - 探测失败降级语义，记录真实原因
        detail = f"{type(exc).__name__}: {exc}"
        logger.error(
            "本体域模式探测失败，降级语义模式: datasource_group_id=%s error=%s",
            group_id,
            detail,
            exc_info=True,
        )
        return QueryModeDecision(
            QueryMode.SEMANTIC,
            group_id,
            0,
            "probe_failed",
            degraded_reason=detail[:500],
        )
    return decide_query_mode(group_id, object_type_count=count, injection_enabled=True)


_CURRENT_MODE: ContextVar[QueryModeDecision | None] = ContextVar("chat_data_query_mode", default=None)
_UNRESOLVED_DECISION = QueryModeDecision(QueryMode.SEMANTIC, None, 0, "unresolved")


def publish_query_mode(decision: QueryModeDecision) -> None:
    """发布本次 run 的判定结果（由预物化在 agent 装配前调用）。"""
    _CURRENT_MODE.set(decision)


def current_query_mode() -> QueryModeDecision:
    """读取本次 run 的判定结果；未发布时按语义模式处理（历史回放/直连装配）。"""
    return _CURRENT_MODE.get() or _UNRESOLVED_DECISION


def ontology_query_mode_active() -> bool:
    return current_query_mode().is_ontology


def reset_query_mode() -> None:
    """清空本次 run 的判定结果（测试与请求边界清理用）。"""
    _CURRENT_MODE.set(None)


__all__ = [
    "ACTIVE_OBJECT_TYPE_STATUSES",
    "QueryMode",
    "QueryModeDecision",
    "clear_query_mode_cache",
    "current_query_mode",
    "decide_query_mode",
    "load_domain_ontology_bundle_async",
    "ontology_injection_enabled",
    "ontology_query_mode_active",
    "publish_query_mode",
    "reset_query_mode",
    "resolve_query_mode_async",
    "translate_binding_columns",
]
