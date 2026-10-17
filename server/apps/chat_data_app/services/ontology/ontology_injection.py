"""本体消费注入：把绑定到当前会话启用语义模型的本体对象物化成 workspace 目录。

消费链路（对齐语义层预物化的形状）：run 启动前语义模型预物化的同一步，把
certified/experimental、绑定到本轮启用语义模型的对象
连同属性摘要与关系子图，物化成 `workspace/semantic/_ontology_catalog.yaml`
只读目录。目录二选一：本体目录物化成功时语义资产目录（_semantic_catalog.yaml）
不写——本体目录的 mappings.file 直接给出模型快照文件名，覆盖语义目录的索引职责。
提示词只声明「存在时先读」，实体识别与关系导航由模型运行时完成。
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

# 本体目录与语义资产同放 workspace/semantic/（不再单开 ontology/ 目录）
ONTOLOGY_SNAPSHOT_DIR = "/mnt/user-data/workspace/semantic"
ONTOLOGY_CATALOG_FILE_NAME = "_ontology_catalog.yaml"
# 旧版独立目录残留（历史会话 workspace 里可能存在），物化新目录时顺手清理
ONTOLOGY_LEGACY_CATALOG_PATH = "/mnt/user-data/workspace/ontology/_ontology_catalog.yaml"

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


def _config_dimension_columns(configs: list[dict[str, Any]] | None) -> dict[str, dict[str, str]]:
    """从 runtime configs 提取 {semantic_config_id: {维度名: 物理列名}}。

    关系列翻译用：本体映射右值是模型维度名（概念层不重复定义物理列），
    目录渲染时经此索引把「属性 → 维度 → 物理列」翻译到物理列供 join 直接使用。
    """
    index: dict[str, dict[str, str]] = {}
    for config in configs or []:
        config = config or {}
        config_id = str(config.get("id") or config.get("semantic_config_id") or "").strip()
        if not config_id:
            continue
        columns: dict[str, str] = {}
        for dimension in config.get("dimensions") or []:
            dimension = dimension or {}
            name = str(dimension.get("name") or "").strip()
            column = str(dimension.get("column") or "").strip()
            if name and column:
                columns[name] = column
        index[config_id] = columns
    return index


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
    domain_names: dict[int, str] | None = None,
    model_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    """单个对象 → 目录条目（属性摘要 + 语义模型映射列表；domain=领域名，未分域为空）。

    mappings.file = 该模型在工作区 semantic/ 下的快照文件名：本体目录与语义资产目录
    二选一物化后，模型凭它直接 file_read 取数模型，不再依赖 _semantic_catalog.yaml。
    """
    domain_names = domain_names or {}
    model_files = model_files or {}
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
    mappings = [
        {
            "semantic_config_id": str(mapping.get("semantic_config_id") or ""),
            "semantic_config_name": config_names.get(str(mapping.get("semantic_config_id") or ""), ""),
            "file": model_files.get(str(mapping.get("semantic_config_id") or ""), ""),
            "column_mappings": mapping.get("column_mappings") or {},
        }
        for mapping in sorted(
            mappings_by_object.get(object_id, []),
            key=lambda m: str(m.get("semantic_config_id") or ""),
        )
    ]
    return {
        "object": obj.get("object_name"),
        "display_name": obj.get("display_name"),
        "status": obj.get("status"),
        "domain": domain_names.get(int(obj.get("domain_id") or 0), ""),
        "description": obj.get("description"),
        "properties": properties,
        "mappings": mappings,
    }


def _translate_to_physical_columns(
    columns: list[Any] | None,
    mappings: list[dict[str, Any]] | None,
    dimension_columns: dict[str, dict[str, str]] | None,
) -> list[str]:
    """关系列是概念属性名，经「对象映射 → 模型维度 → 物理列」两级翻译为物理列供 join 使用。

    映射右值是模型维度名：属性 → 维度名（column_mappings）→ 物理列（dimension_columns）。
    任一级未知时保留原文（属性名本身即物理列名的等价映射天然兼容）。
    """
    mapping: dict[str, str] = {}
    for item in mappings or []:
        mapping = {str(k): str(v) for k, v in (item.get("column_mappings") or {}).items()}
        if mapping:
            break
    config_id = str((mappings or [{}])[0].get("semantic_config_id") or "") if mappings else ""
    columns_of_dims = (dimension_columns or {}).get(config_id, {})
    translated: list[str] = []
    for column in columns or []:
        dimension_name = mapping.get(str(column), str(column))
        translated.append(columns_of_dims.get(dimension_name, dimension_name))
    return translated


def _render_relation_entry(
    rel: dict[str, Any],
    object_names: dict[int, str],
    mappings_by_object: dict[int, list[dict[str, Any]]],
    dimension_columns: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """单条关系 → 目录条目（两端对象名 + 翻译为物理列的关联列 + 自然语言模式）。"""
    return {
        "name": rel.get("relation_name"),
        "from": object_names.get(int(rel.get("from_object_id") or 0), ""),
        "to": object_names.get(int(rel.get("to_object_id") or 0), ""),
        "multiplicity": rel.get("multiplicity"),
        "from_columns": _translate_to_physical_columns(
            rel.get("from_columns"),
            mappings_by_object.get(int(rel.get("from_object_id") or 0)),
            dimension_columns,
        ),
        "to_columns": _translate_to_physical_columns(
            rel.get("to_columns"),
            mappings_by_object.get(int(rel.get("to_object_id") or 0)),
            dimension_columns,
        ),
        "from_display_name": rel.get("from_display_name"),
        "to_display_name": rel.get("to_display_name"),
        "from_verbalize": rel.get("from_verbalize"),
        "to_verbalize": rel.get("to_verbalize"),
    }


def _render_catalog(
    bundle: dict[str, Any],
    config_names: dict[str, str],
    dimension_columns: dict[str, dict[str, str]] | None = None,
    domain_names: dict[int, str] | None = None,
    model_files: dict[str, str] | None = None,
) -> str:
    """渲染本体目录 YAML（对象 → 属性摘要/语义模型映射/关系邻接，给模型检索用）。"""
    properties_by_object = _group_by_object_id(bundle.get("properties"))
    mappings_by_object = _group_by_object_id(bundle.get("mappings"))
    object_names = {int(obj.get("id") or 0): str(obj.get("object_name") or "") for obj in bundle.get("objects") or []}
    payload = {
        "comment": (
            "本体目录（系统生成，只读）：业务对象 → 属性/语义模型映射/对象间关系；"
            "跨模型关联先查此目录。mappings.file 即取数模型快照文件名（semantic/ 下），"
            "本目录存在时不另发语义资产目录"
        ),
        "objects": [
            _render_object_entry(
                obj, properties_by_object, mappings_by_object, config_names, domain_names, model_files
            )
            for obj in bundle.get("objects") or []
        ],
        "relations": [
            _render_relation_entry(rel, object_names, mappings_by_object, dimension_columns)
            for rel in bundle.get("relations") or []
        ],
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)


async def materialize_ontology_catalog_async(
    workspace_runtime: Any,
    semantic_config_ids: list[str],
    configs: list[dict[str, Any]] | None = None,
    model_files: dict[str, str] | None = None,
) -> int:
    """物化本体目录到 workspace/semantic/，返回写入对象数（开关关或无对象返回 0）。

    model_files：semantic_config_id → 模型快照文件名，写入 mappings.file 供模型
    直接 file_read（本体目录与语义资产目录二选一的取数索引）。
    尽力而为：内部异常只告警不抛出，本体故障不阻塞语义模型预物化与会话启动。
    """
    if workspace_runtime is None or not ontology_injection_enabled():
        return 0
    try:
        bundle = await OntologyDao().load_injection_bundle_async(list(semantic_config_ids or []))
        objects = bundle.get("objects") or []
        if not objects:
            return 0
        try:
            domain_rows = await OntologyDao().list_domains_async()
        except Exception:  # noqa: BLE001 - 领域名是展示增强，取不到按未分域渲染
            domain_rows = []
        domain_names = {
            int(row.get("id") or 0): str(row.get("display_name") or row.get("domain_name") or "")
            for row in domain_rows
        }
        content = _render_catalog(
            bundle,
            _config_display_names(configs),
            _config_dimension_columns(configs),
            domain_names,
            model_files,
        )
        catalog_path = str(PurePosixPath(ONTOLOGY_SNAPSHOT_DIR) / ONTOLOGY_CATALOG_FILE_NAME)
        await workspace_runtime.write_internal_text_files([(catalog_path, content)])
        logger.info("本体目录物化完成: objects=%s relations=%s", len(objects), len(bundle.get("relations") or []))
        await _cleanup_legacy_ontology_dir(workspace_runtime)
        return len(objects)
    except Exception as exc:  # noqa: BLE001 - 目录属尽力而为，不阻塞会话启动
        logger.warning("本体目录物化失败（不影响会话启动）: %s", exc)
        return 0


async def _cleanup_legacy_ontology_dir(workspace_runtime: Any) -> None:
    """清理旧版独立 ontology/ 目录残留，避免模型读到过期目录文本。尽力而为。"""
    try:
        await workspace_runtime.delete_internal_text_files(path=ONTOLOGY_LEGACY_CATALOG_PATH)
        logger.info("已清理旧版本体目录残留: %s", ONTOLOGY_LEGACY_CATALOG_PATH)
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 - 清理属尽力而为
        logger.warning("清理旧版本体目录失败: %s", exc)
