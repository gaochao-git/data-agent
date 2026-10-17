"""本体消费注入：把绑定到当前会话启用语义模型的本体对象物化成 workspace 目录。

消费链路（对齐语义层预物化的形状）：run 启动前语义模型预物化的同一步，把
certified/experimental、绑定到本轮启用语义模型的对象
连同属性摘要与关系子图，物化成 `workspace/semantic/_ontology_catalog.yaml`
只读目录。目录二选一：本体目录物化成功时语义资产目录（_semantic_catalog.yaml）
不写——本体目录的 mappings.file 直接给出模型快照文件名，覆盖语义目录的索引职责；
同时目录顶层 `models` 列出全部启用模型，本体对象覆盖不到的模型（未建本体对象）
不会因二选一丢失索引。提示词只声明「存在时先读」，实体识别与关系导航由模型
运行时完成。目录为尽力而为：任何异常只告警，不阻塞语义模型物化与会话启动。

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

# 目录 objects 段的字符预算：40k 渲染字符 ≈ 14-20k tokens（中文约 1 字 1 token，
# YAML 键名/缩进按英文估算），在 48k 上下文窗口里还要留给模型快照精读与工具往返，
# 超过这个体积「先整读目录」就不可行。按对象名升序装入，剩余对象交给 file_grep。
_OBJECT_BUDGET_CHARS = 40_000

_TRUTHY = {"1", "true", "yes", "on"}

# 旧目录清理被沙箱拒绝（sandbox 只放行 workspace/semantic/）只提示一次，
# 避免每次物化都刷 warning 噪音。
_legacy_cleanup_denied_logged = False


def ontology_injection_enabled() -> bool:
    """本体注入开关（env 控制，默认关闭）。"""
    return str(os.getenv("CHAT_DATA_ONTOLOGY_INJECTION_ENABLED") or "").strip().lower() in _TRUTHY


def _config_id(config: dict[str, Any] | None) -> str:
    config = config or {}
    return str(config.get("id") or config.get("semantic_config_id") or "").strip()


def _config_display_names(configs: list[dict[str, Any]] | None) -> dict[str, str]:
    """从 runtime configs 提取 {semantic_config_id: 模型名}，供目录回显模型名。"""
    names: dict[str, str] = {}
    for config in configs or []:
        config = config or {}
        config_id = _config_id(config)
        if config_id:
            names[config_id] = str(config.get("name") or config.get("semantic_config_name") or "").strip()
    return names


def _dimension_column_map(dimensions: list[dict[str, Any]] | None) -> dict[str, str]:
    """单个模型的 {维度名: 物理列名} 索引（expression 维度没有物理列，不入索引）。"""
    columns: dict[str, str] = {}
    for dimension in dimensions or []:
        dimension = dimension or {}
        name = str(dimension.get("name") or "").strip()
        column = str(dimension.get("column") or "").strip()
        if name and column:
            columns[name] = column
    return columns


def _config_dimension_columns(configs: list[dict[str, Any]] | None) -> dict[str, dict[str, str]]:
    """从 runtime configs 提取 {semantic_config_id: {维度名: 物理列名}}。

    关系列翻译用：本体映射右值是模型维度名（概念层不重复定义物理列），
    目录渲染时经此索引把「属性 → 维度 → 物理列」翻译到物理列供 join 直接使用。
    """
    index: dict[str, dict[str, str]] = {}
    for config in configs or []:
        config = config or {}
        config_id = _config_id(config)
        if not config_id:
            continue
        index[config_id] = _dimension_column_map(config.get("dimensions"))
    return index


def _group_by_object_id(items: list[dict[str, Any]] | None) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in items or []:
        grouped.setdefault(int(item.get("object_id") or 0), []).append(item)
    return grouped


def _mapping_config_id(mapping: dict[str, Any] | None) -> str:
    return str((mapping or {}).get("semantic_config_id") or "").strip()


def _mapping_column_map(mapping: dict[str, Any] | None) -> dict[str, str]:
    """映射行的「属性名 → 模型维度名」字典（空/非法一律按空映射处理）。"""
    raw = (mapping or {}).get("column_mappings") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _usable_mappings(mappings: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """排序后的可用映射列表（带非空 column_mappings，按 semantic_config_id 升序）。

    排序是确定性的关键：DAO 查询没有 ORDER BY，行序不稳定；同时翻译必须用
    同一条映射自己的 semantic_config_id 查维度列，不能拿 mappings[0] 的 id 去
    配另一条映射的 column_mappings（多模型绑定时会串模型）。
    """
    usable: list[dict[str, Any]] = []
    for mapping in mappings or []:
        column_map = _mapping_column_map(mapping)
        if not column_map:
            continue
        usable.append(
            {
                "semantic_config_id": _mapping_config_id(mapping),
                "column_mappings": column_map,
            }
        )
    usable.sort(key=lambda item: item["semantic_config_id"])
    return usable


def _translate_with_mapping(
    columns: list[Any] | None,
    mapping: dict[str, Any],
    dimension_columns: dict[str, dict[str, str]] | None,
) -> tuple[list[str], bool]:
    """用指定映射把关系列翻译为物理列，返回 (物理列, 是否有列未解析)。

    两级翻译：属性 → 维度名（该映射的 column_mappings）→ 物理列（该映射
    semantic_config_id 下的维度列索引）。任一级缺失时保留原文，并标记未解析：
    - 属性未映射：保留原始属性名（可能是物理列名，但未经验证）；
    - 维度无物理列（expression 维度）：保留维度名。
    """
    column_map = mapping.get("column_mappings") or {}
    dimension_map = (dimension_columns or {}).get(mapping.get("semantic_config_id") or "", {})
    translated: list[str] = []
    unresolved = False
    for column in columns or []:
        raw_name = str(column)
        dimension_name = column_map.get(raw_name)
        if dimension_name is None:
            translated.append(raw_name)
            unresolved = True
            continue
        physical_column = dimension_map.get(dimension_name)
        if physical_column is None:
            translated.append(dimension_name)
            unresolved = True
            continue
        translated.append(physical_column)
    return translated, unresolved


def _translate_to_physical_columns(
    columns: list[Any] | None,
    mappings: list[dict[str, Any]] | None,
    dimension_columns: dict[str, dict[str, str]] | None,
) -> tuple[list[str], bool]:
    """向后兼容的单映射翻译：取排序后第一个可用映射，返回 (物理列, 是否未解析)。

    同一模型单映射时输出与旧实现一致；多模型绑定时只作为兜底视图，
    精确视图见 `_translate_columns_by_model`。
    """
    usable = _usable_mappings(mappings)
    raw_columns = [str(column) for column in columns or []]
    if not usable:
        return raw_columns, bool(raw_columns)
    return _translate_with_mapping(columns, usable[0], dimension_columns)


def _translate_columns_by_model(
    columns: list[Any] | None,
    mappings: list[dict[str, Any]] | None,
    dimension_columns: dict[str, dict[str, str]] | None,
) -> dict[str, list[str]]:
    """每个可用端点映射各自翻译：{semantic_config_id: [物理列]}。"""
    by_model: dict[str, list[str]] = {}
    for mapping in _usable_mappings(mappings):
        config_id = mapping["semantic_config_id"]
        if not config_id:
            continue
        translated, _ = _translate_with_mapping(columns, mapping, dimension_columns)
        by_model[config_id] = translated
    return by_model


def _render_model_entry(config: dict[str, Any], model_files: dict[str, str]) -> dict[str, Any]:
    config_id = _config_id(config)
    return {
        "semantic_config_id": config_id,
        "name": str(config.get("name") or config.get("semantic_config_name") or "").strip(),
        "file": str((model_files or {}).get(config_id) or ""),
        # description UI 已并入业务说明：description 为空时回退 business_notes，模型路由信息不缺
        "description": str(config.get("description") or config.get("semantic_config_desc") or config.get("business_notes") or "").strip(),
        "business_notes": str(config.get("business_notes") or "").strip(),
    }


def _render_models_section(
    configs: list[dict[str, Any]] | None,
    model_files: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """全部启用模型索引（按 semantic_config_id 排序去重）。

    objects 只覆盖已绑定本体对象的模型；本段覆盖全部启用模型，
    使「本体目录接管语义资产目录」后未建对象的模型仍有 description/business_notes
    索引与 file 取数入口（二选一不丢信息）。
    """
    models: dict[str, dict[str, Any]] = {}
    for config in configs or []:
        config = config or {}
        config_id = _config_id(config)
        if not config_id or config_id in models or not bool(config.get("enabled", True)):
            continue
        models[config_id] = _render_model_entry(config, model_files or {})
    return [models[config_id] for config_id in sorted(models)]


def _render_property_entry(prop: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": prop.get("property_name"),
        "display_name": prop.get("display_name"),
        "type": prop.get("value_type"),
        "identifier": bool(prop.get("is_identifier")),
        "title": bool(prop.get("is_title")),
    }
    description = str(prop.get("description") or "").strip()
    if description:
        entry["description"] = description
    enum_ref = str(prop.get("enum_ref") or "").strip()
    if enum_ref:
        entry["enum_ref"] = enum_ref
    return entry


def _render_mapping_entry(
    mapping: dict[str, Any],
    config_names: dict[str, str],
    model_files: dict[str, str],
) -> dict[str, Any]:
    config_id = _mapping_config_id(mapping)
    return {
        "semantic_config_id": config_id,
        "semantic_config_name": config_names.get(config_id, ""),
        "file": model_files.get(config_id, ""),
        "column_mappings": _mapping_column_map(mapping),
    }


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
    空 column_mappings 的映射行对模型没有信息量（渲染成空字典只会造成歧义），
    因此从对象条目中剔除；对象本身即使没有映射也保留。
    """
    domain_names = domain_names or {}
    model_files = model_files or {}
    object_id = int(obj.get("id") or 0)
    properties = [
        _render_property_entry(prop)
        for prop in sorted(
            properties_by_object.get(object_id, []),
            key=lambda p: str(p.get("property_name") or ""),
        )
    ]
    mappings = [
        _render_mapping_entry(mapping, config_names, model_files)
        for mapping in sorted(mappings_by_object.get(object_id, []), key=_mapping_config_id)
        if _mapping_column_map(mapping)
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


def _render_relation_entry(
    rel: dict[str, Any],
    object_names: dict[int, str],
    mappings_by_object: dict[int, list[dict[str, Any]]],
    dimension_columns: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """单条关系 → 目录条目（两端对象名 + 尽力翻译的关联列 + 自然语言模式）。

    列翻译是尽力而为：`from_columns`/`to_columns` 是第一个可用端点映射的翻译结果
    （单映射时与旧输出一致）；`from_columns_by_model`/`to_columns_by_model` 给出
    每个绑定模型各自的翻译结果，多模型绑定时以它为准。任一列回退到原始属性/维度名
    时置 `unresolved: true`——模型必须回读对应模型 YAML 确认物理列后再写 join。
    """
    from_mappings = mappings_by_object.get(int(rel.get("from_object_id") or 0))
    to_mappings = mappings_by_object.get(int(rel.get("to_object_id") or 0))
    from_columns, from_unresolved = _translate_to_physical_columns(
        rel.get("from_columns"), from_mappings, dimension_columns
    )
    to_columns, to_unresolved = _translate_to_physical_columns(rel.get("to_columns"), to_mappings, dimension_columns)
    entry: dict[str, Any] = {
        "name": rel.get("relation_name"),
        "from": object_names.get(int(rel.get("from_object_id") or 0), ""),
        "to": object_names.get(int(rel.get("to_object_id") or 0), ""),
        "multiplicity": rel.get("multiplicity"),
        "from_columns": from_columns,
        "to_columns": to_columns,
        "from_columns_by_model": _translate_columns_by_model(rel.get("from_columns"), from_mappings, dimension_columns),
        "to_columns_by_model": _translate_columns_by_model(rel.get("to_columns"), to_mappings, dimension_columns),
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


def _yaml_render_size(value: Any) -> int:
    """单条对象条目的 YAML 渲染字符数（截断预算度量）。"""
    return len(yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120))


def _select_objects_within_budget(
    entries: list[dict[str, Any]],
    budget: int,
) -> tuple[list[dict[str, Any]], int]:
    """按对象名升序装入对象直到字符预算，返回 (保留对象, 省略对象数)。

    预算按单条对象 YAML 的渲染长度累加（YAML 头部字段与 relations 段不计入，
    实际文件略大于预算，量级足够）。首条对象即使超预算也保留，避免空目录。
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


_CATALOG_COMMENT = (
    "本体目录（系统生成，只读）：models 是全部启用模型索引（file 即取数模型快照文件名，"
    "含未建本体对象的模型）；objects 是已建本体对象的导航子集（属性/语义模型映射/对象间关系），"
    "模型可能不在 objects 中。relations 的 from/to 列是尽力翻译的物理列，"
    "unresolved: true 表示存在未解析列（回退为原始属性/维度名），必须回读模型 YAML 确认；"
    "truncated: true 时 objects 仅为按对象名排序的前一部分，剩余对象用 file_grep 检索。"
)


def _render_catalog(
    bundle: dict[str, Any],
    config_names: dict[str, str],
    dimension_columns: dict[str, dict[str, str]] | None = None,
    domain_names: dict[int, str] | None = None,
    model_files: dict[str, str] | None = None,
    configs: list[dict[str, Any]] | None = None,
) -> str:
    """渲染本体目录 YAML（模型索引 + 对象导航 + 关系邻接，给模型检索用）。"""
    properties_by_object = _group_by_object_id(bundle.get("properties"))
    mappings_by_object = _group_by_object_id(bundle.get("mappings"))
    objects = sorted(bundle.get("objects") or [], key=lambda obj: str(obj.get("object_name") or ""))
    rendered_objects = [
        _render_object_entry(obj, properties_by_object, mappings_by_object, config_names, domain_names, model_files)
        for obj in objects
    ]
    kept_objects, omitted_objects = _select_objects_within_budget(rendered_objects, _OBJECT_BUDGET_CHARS)
    kept_count = len(kept_objects)
    kept_ids = {int(obj.get("id") or 0) for obj in objects[:kept_count]}
    kept_names = {int(obj.get("id") or 0): str(obj.get("object_name") or "") for obj in objects[:kept_count]}
    relations = sorted(bundle.get("relations") or [], key=lambda rel: str(rel.get("relation_name") or ""))
    payload: dict[str, Any] = {
        "comment": _CATALOG_COMMENT,
        "models": _render_models_section(configs, model_files),
        "objects": kept_objects,
        "relations": [
            _render_relation_entry(rel, kept_names, mappings_by_object, dimension_columns)
            for rel in relations
            if int(rel.get("from_object_id") or 0) in kept_ids and int(rel.get("to_object_id") or 0) in kept_ids
        ],
    }
    if omitted_objects:
        payload["truncated"] = True
        payload["omitted_objects"] = omitted_objects
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
    configs：runtime 全量配置，用于渲染顶层 models（全部启用模型索引）。
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
            int(row.get("id") or 0): str(row.get("display_name") or row.get("domain_name") or "") for row in domain_rows
        }
        content = _render_catalog(
            bundle,
            _config_display_names(configs),
            _config_dimension_columns(configs),
            domain_names,
            model_files,
            configs,
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
