"""本体消费注入：把当前业务域（数据源组）的可用对象类型物化成 workspace 目录。

2026-10-23 起对象类型取数走 Logical Dataset source binding，语义模型映射表下线：
目录不再渲染「对象类型 → 语义模型」映射，改为「对象类型 → Dataset/物理表/列映射」，
关系类型的 join 列经 Dataset 列映射直接翻译成物理列（属性 → 逻辑列 → 物理列），
整条链路不依赖语义模型。

消费链路：run 启动前与语义模型预物化同一步，把 certified/experimental、
绑定到当前数据源组（经对象类型 source binding 的 Dataset member binding）的对象类型
连同属性摘要、数据源绑定与关系类型子图，物化成
`workspace/semantic/_ontology_catalog.yaml` 只读目录。目录与语义资产目录
（`_semantic_catalog.yaml`）严格二选一：本体模式只写本体目录，语义模式只写语义
目录，选择逻辑见 ``query_mode``（服务层单一入口）。

Action Type 定义以只读清单随目录注入（名称/目标对象类型/状态/参数），供模型判断
「有哪些动作可用」；执行仍受 ``CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED`` 门控。

目录为尽力而为：任何异常只告警，不阻塞会话启动。
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

import yaml

from utils.logger import get_logger

from .action_type_service import OntologyActionTypeService
from .query_mode import (
    load_domain_ontology_bundle_async,
    ontology_injection_enabled,
    translate_binding_columns,
)

logger = get_logger(__name__)

# 本体目录与语义资产同放 workspace/semantic/（不开独立 ontology/ 目录）
ONTOLOGY_SNAPSHOT_DIR = "/mnt/user-data/workspace/semantic"
ONTOLOGY_CATALOG_FILE_NAME = "_ontology_catalog.yaml"
# 旧版独立目录残留（历史会话 workspace 里可能存在），物化新目录时顺手清理
ONTOLOGY_LEGACY_CATALOG_PATH = "/mnt/user-data/workspace/ontology/_ontology_catalog.yaml"

# 目录 object_types 段的字符预算：40k 渲染字符 ≈ 14-20k tokens（中文约 1 字 1 token，
# YAML 键名/缩进按英文估算），在 48k 上下文窗口里还要留给模型精读与工具往返，
# 超过这个体积「先整读目录」就不可行。按对象类型名升序装入，剩余对象类型交给 file_grep。
_OBJECT_BUDGET_CHARS = 40_000

# 目录只注入 experimental/certified 的 Action 定义；draft 未定型、deprecated 不可用。
_ACTIVE_ACTION_STATUSES = ("certified", "experimental")

_CATALOG_COMMENT = (
    "本体目录（系统生成，只读）：当前业务域（数据源组）的可用对象类型与关系类型。"
    "object_types 是对象类型导航子集（属性 + Dataset 数据源绑定），"
    "source_binding 给出 Dataset 名/物理表/member 数据源/属性→逻辑列/逻辑列→物理列映射，"
    "对象查询只走这些映射，不使用语义模型；"
    "relation_types 的 from/to 列是翻译后的物理连接列，unresolved: true 表示存在未解析列，"
    "必须回读对应对象类型的 source_binding 确认；action_types 是可执行动作定义（只读清单）。"
    "truncated: true 时 object_types 仅为按对象类型名排序的前一部分，剩余对象类型用 file_grep 检索。"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _render_property_entry(prop: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": prop.get("property_name"),
        "display_name": prop.get("display_name"),
        "type": prop.get("value_type"),
        "identifier": bool(prop.get("is_identifier")),
        "title": bool(prop.get("is_title")),
    }
    derivation = prop.get("derivation")
    if isinstance(derivation, dict) and derivation:
        # 派生属性：只读、查询时计算、无物理列映射（对齐 Palantir derived property）
        entry["derived"] = True
        entry["derivation"] = {key: value for key, value in derivation.items() if value}
    description = _text(prop.get("description"))
    if description:
        entry["description"] = description
    enum_ref = _text(prop.get("enum_ref"))
    if enum_ref:
        entry["enum_ref"] = enum_ref
    return entry


def _render_source_binding(binding: dict[str, Any] | None) -> dict[str, Any]:
    """对象类型的数据源绑定视图（目录可读化）：Dataset/物理表/member/列映射。"""
    binding = binding or {}
    return {
        "dataset": binding.get("dataset_name"),
        "dataset_display_name": binding.get("dataset_display_name"),
        "physical_table": binding.get("physical_table"),
        "members": [
            {
                "member_datasource_id": _text(item.get("member_datasource_id")),
                "physical_table": _text(item.get("physical_table")),
            }
            for item in binding.get("members") or []
            if isinstance(item, dict)
        ],
        "column_mappings": binding.get("column_mappings") or {},
        "columns": binding.get("columns") or {},
    }


def _render_object_entry(
    obj: dict[str, Any],
    properties: list[dict[str, Any]],
    metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """单个对象类型 → 目录条目（属性摘要 + Dataset 数据源绑定 + 只读指标引用）。"""
    entry: dict[str, Any] = {
        "object_type": obj.get("object_type_name"),
        "display_name": obj.get("display_name"),
        "status": obj.get("status"),
        "description": obj.get("description"),
        "properties": [
            _render_property_entry(prop)
            for prop in sorted(properties, key=lambda item: _text(item.get("property_name")))
        ],
        "source_binding": _render_source_binding(obj.get("source_binding")),
    }
    if metrics:
        # 只暴露引用与可执行性，不复制指标表达式（口径权威在语义层）。
        entry["metrics"] = [
            {
                "name": item.get("name"),
                "kind": item.get("kind"),
                "description": item.get("description"),
                "semantic_config_id": item.get("semantic_config_id"),
                "executable_in_scope": bool(item.get("executable_in_scope")),
                "unavailable_reason": item.get("unavailable_reason"),
            }
            for item in metrics
        ]
    return entry


def _bindings_by_object_type(bundle: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(obj.get("id") or 0): obj.get("source_binding") or {}
        for obj in bundle.get("object_types") or []
        if isinstance(obj, dict)
    }


def _render_relation_entry(
    rel: dict[str, Any],
    object_type_names: dict[int, str],
    bindings_by_object: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """单条关系类型 → 目录条目（两端对象类型名 + 物理连接列 + 自然语言模式）。"""
    from_id = int(rel.get("from_object_type_id") or 0)
    to_id = int(rel.get("to_object_type_id") or 0)
    from_columns, from_unresolved = translate_binding_columns(rel.get("from_columns"), bindings_by_object.get(from_id))
    to_columns, to_unresolved = translate_binding_columns(rel.get("to_columns"), bindings_by_object.get(to_id))
    entry: dict[str, Any] = {
        "name": rel.get("relation_type_name"),
        "from": object_type_names.get(from_id, ""),
        "to": object_type_names.get(to_id, ""),
        "multiplicity": rel.get("multiplicity"),
        "from_columns": from_columns,
        "to_columns": to_columns,
        "from_display_name": rel.get("from_display_name"),
        "to_display_name": rel.get("to_display_name"),
        "from_verbalize": rel.get("from_verbalize"),
        "to_verbalize": rel.get("to_verbalize"),
        "description": rel.get("description"),
        "status": rel.get("status"),
    }
    if from_unresolved or to_unresolved:
        entry["unresolved"] = True
    return entry


def _render_action_entry(action: dict[str, Any], object_type_names: dict[int, str]) -> dict[str, Any]:
    """单条 Action Type → 目录条目（只读清单：参数定义 + 目标对象类型）。"""
    parameters = [
        {
            "name": _text(param.get("name")),
            "type": _text(param.get("type")),
            "required": bool(param.get("required")),
            "description": _text(param.get("description")),
        }
        for param in action.get("parameters") or []
        if isinstance(param, dict) and _text(param.get("name"))
    ]
    return {
        "action": action.get("action_type_name") or action.get("name"),
        "display_name": action.get("display_name"),
        "object_type": object_type_names.get(int(action.get("object_type_id") or 0), ""),
        "status": action.get("status"),
        "description": action.get("description"),
        "parameters": parameters,
    }


def _yaml_render_size(value: Any) -> int:
    """单条对象类型条目的 YAML 渲染字符数（截断预算度量）。"""
    return len(yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120))


def _select_objects_within_budget(
    entries: list[dict[str, Any]],
    budget: int,
) -> tuple[list[dict[str, Any]], int]:
    """按对象类型名升序装入对象类型直到字符预算，返回 (保留对象类型, 省略对象类型数)。

    预算按单条对象类型 YAML 的渲染长度累加（YAML 头部字段与 relation_types 段不计入，
    实际文件略大于预算，量级足够）。首条对象类型即使超预算也保留，避免空目录。
    """
    kept: list[dict[str, Any]] = []
    used = 0
    for entry in entries:
        size = _yaml_render_size(entry)
        if kept and used + size > budget:
            break
        kept.append(entry)
        used += size
    return kept, len(entries) - len(kept)


def _properties_by_object_type(bundle: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """bundle.properties 按对象类型分组（目录渲染用）。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for prop in bundle.get("properties") or []:
        if isinstance(prop, dict):
            grouped.setdefault(int(prop.get("object_type_id") or 0), []).append(prop)
    return grouped


def _render_object_entries(
    object_types: list[dict[str, Any]],
    properties_by_object_type: dict[int, list[dict[str, Any]]],
    metrics_by_object_type: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    metrics_map = metrics_by_object_type or {}
    return [
        _render_object_entry(
            obj,
            properties_by_object_type.get(int(obj.get("id") or 0), []),
            metrics_map.get(_text(obj.get("object_type_name"))),
        )
        for obj in object_types
        if isinstance(obj, dict)
    ]


def _render_relation_entries(
    bundle: dict[str, Any],
    kept_ids: set[int],
    object_type_names: dict[int, str],
    bindings_by_object: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    relations = sorted(
        (item for item in bundle.get("relation_types") or [] if isinstance(item, dict)),
        key=lambda rel: _text(rel.get("relation_type_name")),
    )
    return [
        _render_relation_entry(rel, object_type_names, bindings_by_object)
        for rel in relations
        if int(rel.get("from_object_type_id") or 0) in kept_ids and int(rel.get("to_object_type_id") or 0) in kept_ids
    ]


def _render_action_entries(
    action_types: list[dict[str, Any]] | None,
    kept_ids: set[int],
    object_type_names: dict[int, str],
) -> list[dict[str, Any]]:
    actions = sorted(
        (item for item in action_types or [] if isinstance(item, dict)),
        key=lambda item: _text(item.get("action_type_name") or item.get("name")),
    )
    return [
        _render_action_entry(action, object_type_names)
        for action in actions
        if int(action.get("object_type_id") or 0) in kept_ids
    ]


def _render_catalog(
    bundle: dict[str, Any],
    action_types: list[dict[str, Any]] | None = None,
    metrics_by_object_type: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """渲染本体目录 YAML（对象类型 + 数据源绑定 + 关系类型 + Action 定义 + 只读指标引用）。"""
    object_types = [obj for obj in bundle.get("object_types") or [] if isinstance(obj, dict)]
    object_types.sort(key=lambda obj: _text(obj.get("object_type_name")))
    rendered_objects = _render_object_entries(
        object_types,
        _properties_by_object_type(bundle),
        metrics_by_object_type,
    )
    kept_objects, omitted_objects = _select_objects_within_budget(rendered_objects, _OBJECT_BUDGET_CHARS)
    kept_rows = object_types[: len(kept_objects)]
    kept_ids = {int(obj.get("id") or 0) for obj in kept_rows}
    object_type_names = {int(obj.get("id") or 0): _text(obj.get("object_type_name")) for obj in kept_rows}
    bindings_by_object = _bindings_by_object_type(bundle)
    payload: dict[str, Any] = {
        "comment": _CATALOG_COMMENT,
        "datasource_group_id": bundle.get("datasource_group_id") or "",
        "object_types": kept_objects,
        "relation_types": _render_relation_entries(bundle, kept_ids, object_type_names, bindings_by_object),
        "action_types": _render_action_entries(action_types, kept_ids, object_type_names),
    }
    if omitted_objects:
        payload["truncated"] = True
        payload["omitted_objects"] = omitted_objects
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)


async def _load_object_metrics_map_async(
    object_type_names: list[str],
    *,
    datasource_group_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """目录增强：加载对象类型可执行指标清单；失败降级为空，不阻塞目录主体。"""
    from .object_metrics import load_object_metrics_map_async

    try:
        return await load_object_metrics_map_async(
            [name for name in object_type_names if _text(name)],
            datasource_group_id=datasource_group_id,
        )
    except Exception as exc:  # noqa: BLE001 - 指标索引属展示增强
        logger.warning("加载对象类型指标索引失败（不影响本体目录）: %s", exc)
        return {}


async def _load_action_types_async(object_type_ids: set[int]) -> list[dict[str, Any]]:
    """读取域内对象类型的活跃 Action 定义（只读清单；失败只告警返回空）。"""
    if not object_type_ids:
        return []
    try:
        items = await OntologyActionTypeService().list_action_types_async()
    except Exception as exc:  # noqa: BLE001 - Action 清单属展示增强，取不到不影响目录主体
        logger.warning("读取 Action Type 定义失败（不影响对象目录）: %s", exc)
        return []
    return [
        item
        for item in items or []
        if isinstance(item, dict)
        and int(item.get("object_type_id") or 0) in object_type_ids
        and _text(item.get("status")) in _ACTIVE_ACTION_STATUSES
    ]


async def materialize_ontology_catalog_async(
    workspace_runtime: Any,
    *,
    datasource_group_id: str,
    bundle: dict[str, Any] | None = None,
    action_types: list[dict[str, Any]] | None = None,
) -> int:
    """物化本体目录到 workspace/semantic/，返回写入对象类型数（开关关/无对象返回 0）。

    ``datasource_group_id`` 是判断范围（业务域 = 数据源组，见 ``query_mode``）。
    ``bundle`` 可由调用方复用已加载的域 bundle；不传时经 ``query_mode`` 加载。
    尽力而为：内部异常只告警不抛出，本体故障不阻塞会话启动。
    """
    group_id = _text(datasource_group_id)
    if workspace_runtime is None or not ontology_injection_enabled() or not group_id:
        return 0
    try:
        resolved_bundle = bundle
        if resolved_bundle is None:
            resolved_bundle = await load_domain_ontology_bundle_async(group_id)
        object_types = resolved_bundle.get("object_types") or []
        if not object_types:
            return 0
        object_type_ids = {int(obj.get("id") or 0) for obj in object_types if isinstance(obj, dict)}
        resolved_actions = action_types if action_types is not None else await _load_action_types_async(object_type_ids)
        metrics_by_object_type = await _load_object_metrics_map_async(
            [_text(obj.get("object_type_name")) for obj in object_types if isinstance(obj, dict)],
            datasource_group_id=group_id,
        )
        content = _render_catalog(
            resolved_bundle,
            resolved_actions,
            metrics_by_object_type=metrics_by_object_type,
        )
        catalog_path = str(PurePosixPath(ONTOLOGY_SNAPSHOT_DIR) / ONTOLOGY_CATALOG_FILE_NAME)
        await workspace_runtime.write_internal_text_files([(catalog_path, content)])
        logger.info(
            "本体目录物化完成: datasource_group_id=%s object_types=%s relation_types=%s action_types=%s",
            group_id,
            len(object_types),
            len(resolved_bundle.get("relation_types") or []),
            len(resolved_actions or []),
        )
        await _cleanup_legacy_ontology_dir(workspace_runtime)
        return len(object_types)
    except Exception as exc:  # noqa: BLE001 - 目录属尽力而为，不阻塞会话启动
        logger.warning("本体目录物化失败（不影响会话启动）: %s", exc)
        return 0


# 旧目录清理被沙箱拒绝（sandbox 只放行 workspace/semantic/）只提示一次，
# 避免每次物化都刷 warning 噪音。
_legacy_cleanup_denied_logged = False


async def _cleanup_legacy_ontology_dir(workspace_runtime: Any) -> None:
    """清理旧版独立 ontology/ 目录残留，避免模型读到过期目录文本。尽力而为。"""
    global _legacy_cleanup_denied_logged
    try:
        await workspace_runtime.delete_internal_text_files(path=ONTOLOGY_LEGACY_CATALOG_PATH)
        logger.info("已清理旧版本体目录残留: %s", ONTOLOGY_LEGACY_CATALOG_PATH)
    except FileNotFoundError:
        return
    except ValueError as exc:
        # sandbox 后端只放行 workspace/semantic/，旧目录清理必然被拒：
        # 属历史残留清理，首次记 info，之后不再重复刷 warning。
        if not _legacy_cleanup_denied_logged:
            logger.info("旧版本体目录清理被后端拒绝（沙箱路径限制，跳过）: %s", exc)
            _legacy_cleanup_denied_logged = True
    except Exception as exc:  # noqa: BLE001 - 清理属尽力而为
        logger.warning("清理旧版本体目录失败: %s", exc)


__all__ = [
    "ONTOLOGY_CATALOG_FILE_NAME",
    "ONTOLOGY_LEGACY_CATALOG_PATH",
    "ONTOLOGY_SNAPSHOT_DIR",
    "materialize_ontology_catalog_async",
    "ontology_injection_enabled",
]
