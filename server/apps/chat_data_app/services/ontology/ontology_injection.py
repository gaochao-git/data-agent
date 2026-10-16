"""本体消费注入：把绑定到当前会话启用语义模型的本体对象物化成 workspace 目录。

消费链路（对齐语义层预物化的形状）：run 启动前语义模型预物化的同一步，把
certified/experimental、绑定到本轮启用语义模型的对象
连同属性摘要与关系子图，物化成 `workspace/ontology/_ontology_catalog.yaml`
只读目录；提示词只声明「存在时先读」，实体识别与关系导航由模型运行时完成。
目录为尽力而为：任何异常只告警，不阻塞语义模型物化与会话启动。

开关 `CHAT_DATA_ONTOLOGY_INJECTION_ENABLED` 默认关闭（实验分支验证期开启）。
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Any

import yaml
from utils.logger import get_logger

from ...daos.ontology_dao import OntologyDao

logger = get_logger(__name__)

ONTOLOGY_SNAPSHOT_DIR = "/mnt/user-data/workspace/ontology"
ONTOLOGY_CATALOG_FILE_NAME = "_ontology_catalog.yaml"

_TRUTHY = {"1", "true", "yes", "on"}


def ontology_injection_enabled() -> bool:
    """本体注入开关（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED") or "").strip().lower() in _TRUTHY


def _config_display_names(configs: list[dict[str, Any]] | None) -> dict[str, str]:
    """从 runtime configs 提取 {semantic_config_id: 模型名}，供目录回显模型名。"""
    names: dict[str, str] = {}
    for config in configs or []:
        config = config or {}
        config_id = str(config.get("id") or config.get("semantic_config_id") or "").strip()
        if config_id:
            names[config_id] = str(config.get("name") or config.get("semantic_config_name") or "").strip()
    return names


def _group_by_object_id(items: list[dict[str, Any]] | None) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in items or []:
        grouped.setdefault(int(item.get("object_id") or 0), []).append(item)
    return grouped


def _render_object_entry(
    obj: dict[str, Any],
    properties_by_object: dict[int, list[dict[str, Any]]],
    mappings_by_object: dict[int, list[dict[str, Any]]],
    config_names: dict[str, str],
) -> dict[str, Any]:
    """单个对象 → 目录条目（属性摘要 + 绑定列表）。"""
    object_id = int(obj.get("id") or 0)
    properties = [
        {
            "name": prop.get("property_name"),
            "display_name": prop.get("display_name"),
            "type": prop.get("value_type"),
            "identifier": bool(prop.get("is_identifier")),
            "title": bool(prop.get("is_title")),
        }
        for prop in sorted(
            properties_by_object.get(object_id, []),
            key=lambda p: str(p.get("property_name") or ""),
        )
    ]
    bindings = [
        {
            "semantic_config_id": str(mapping.get("semantic_config_id") or ""),
            "semantic_config_name": config_names.get(str(mapping.get("semantic_config_id") or ""), ""),
            "table_alias": mapping.get("table_alias"),
            "column_mappings": mapping.get("column_mappings") or {},
        }
        for mapping in sorted(
            mappings_by_object.get(object_id, []),
            key=lambda m: (str(m.get("semantic_config_id") or ""), str(m.get("table_alias") or "")),
        )
    ]
    return {
        "object": obj.get("object_name"),
        "display_name": obj.get("display_name"),
        "status": obj.get("status"),
        "description": obj.get("description"),
        "properties": properties,
        "bindings": bindings,
    }


def _translate_to_physical_columns(
    columns: list[Any] | None,
    bindings: list[dict[str, Any]] | None,
) -> list[str]:
    """关系列是概念属性名，经对象绑定 column_mappings 翻译为物理列名供 join 直接使用。

    取第一个绑定的映射；属性名不在映射内（本身即物理列名）原样保留；无绑定时原样返回。
    """
    mapping: dict[str, str] = {}
    for binding in bindings or []:
        mapping = {str(k): str(v) for k, v in (binding.get("column_mappings") or {}).items()}
        if mapping:
            break
    return [mapping.get(str(col), str(col)) for col in columns or []]


def _render_relation_entry(
    rel: dict[str, Any],
    object_names: dict[int, str],
    mappings_by_object: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    """单条关系 → 目录条目（两端对象名 + 翻译为物理列的关联列 + 自然语言模式）。"""
    return {
        "name": rel.get("relation_name"),
        "from": object_names.get(int(rel.get("from_object_id") or 0), ""),
        "to": object_names.get(int(rel.get("to_object_id") or 0), ""),
        "multiplicity": rel.get("multiplicity"),
        "from_columns": _translate_to_physical_columns(
            rel.get("from_columns"), mappings_by_object.get(int(rel.get("from_object_id") or 0))
        ),
        "to_columns": _translate_to_physical_columns(
            rel.get("to_columns"), mappings_by_object.get(int(rel.get("to_object_id") or 0))
        ),
        "from_display_name": rel.get("from_display_name"),
        "to_display_name": rel.get("to_display_name"),
        "from_verbalize": rel.get("from_verbalize"),
        "to_verbalize": rel.get("to_verbalize"),
    }


def _render_catalog(bundle: dict[str, Any], config_names: dict[str, str]) -> str:
    """渲染本体目录 YAML（对象 → 属性摘要/绑定/关系邻接，给模型检索用）。"""
    properties_by_object = _group_by_object_id(bundle.get("properties"))
    mappings_by_object = _group_by_object_id(bundle.get("mappings"))
    object_names = {int(obj.get("id") or 0): str(obj.get("object_name") or "") for obj in bundle.get("objects") or []}
    payload = {
        "comment": "本体目录（系统生成，只读）：业务对象 → 属性/语义模型绑定/对象间关系；跨模型关联先查此目录",
        "objects": [
            _render_object_entry(obj, properties_by_object, mappings_by_object, config_names)
            for obj in bundle.get("objects") or []
        ],
        "relations": [
            _render_relation_entry(rel, object_names, mappings_by_object)
            for rel in bundle.get("relations") or []
        ],
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)


async def materialize_ontology_catalog_async(
    workspace_runtime: Any,
    semantic_config_ids: list[str],
    configs: list[dict[str, Any]] | None = None,
) -> int:
    """物化本体目录到 workspace/ontology/，返回写入对象数（开关关或无对象返回 0）。

    尽力而为：内部异常只告警不抛出，本体故障不阻塞语义模型预物化与会话启动。
    """
    if workspace_runtime is None or not ontology_injection_enabled():
        return 0
    try:
        bundle = await OntologyDao().load_injection_bundle_async(list(semantic_config_ids or []))
        objects = bundle.get("objects") or []
        if not objects:
            return 0
        content = _render_catalog(bundle, _config_display_names(configs))
        catalog_path = str(PurePosixPath(ONTOLOGY_SNAPSHOT_DIR) / ONTOLOGY_CATALOG_FILE_NAME)
        await workspace_runtime.write_internal_text_files([(catalog_path, content)])
        logger.info("本体目录物化完成: objects=%s relations=%s", len(objects), len(bundle.get("relations") or []))
        return len(objects)
    except Exception as exc:  # noqa: BLE001 - 目录属尽力而为，不阻塞会话启动
        logger.warning("本体目录物化失败（不影响会话启动）: %s", exc)
        return 0
