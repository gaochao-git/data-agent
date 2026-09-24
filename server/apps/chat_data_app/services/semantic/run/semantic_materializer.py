"""Materialize selected semantic models into the current conversation workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import OrderedDict
from pathlib import PurePosixPath
from typing import Any

from utils.logger import get_logger

from .chat_data_ossie_converter import ChatDataToOssieConverter
from .semantic_config_service import SemanticConfigService, get_semantic_config_service

logger = get_logger(__name__)

_CACHE_LIMIT = 256
# 单批模型数超过默认上限时按批扩容的上界，防止超大数据源组把缓存撑爆
_CACHE_HARD_LIMIT = 4096
_YAML_CACHE: OrderedDict[str, str] = OrderedDict()
# 语义模型 YAML 快照目录（受管只读区，仅供 file_grep/file_read）
SEMANTIC_SNAPSHOT_DIR = "/mnt/user-data/workspace/semantic"
# unicode 感知：保留中文，仅把空白/路径分隔等不安全字符折叠成下划线
_FILE_COMPONENT_RE = re.compile(r"[^\w.-]+")
_MODEL_NAME_MAX_CHARS = 40
_ID_SUFFIX_CHARS = 8


def _config_id(config: dict[str, Any]) -> str:
    return str(config.get("id") or config.get("semantic_config_id") or "").strip()


def _config_cache_key(config: dict[str, Any], source_type: str, salt: str = "") -> str:
    """计算模型 YAML 的缓存键。

    库表模型带 ``update_time``（由 ``update_time`` ON UPDATE 维护），命中时只用
    ``group_id|id|update_time|source_type|salt`` 这类轻量键，避免为查缓存先把
    整份配置（含数据源全量表结构）序列化一遍——模型数量上百时，这一步本身
    就是主要开销。表结构与字段字典不随模型行更新，统一由批次级 ``salt`` 兜住。
    缺少 ``update_time``（内存构造/测试替身）时回落到全量内容哈希，
    保证「内容变则缓存失效」不被破坏。
    """
    update_time = str((config or {}).get("update_time") or "").strip()
    if update_time:
        payload = "|".join((
            str((config or {}).get("group_id") or "").strip(),
            _config_id(config),
            update_time,
            str(source_type or "").strip(),
            salt,
        ))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    payload = json.dumps(
        {"source_type": str(source_type or "").strip(), "config": config, "salt": salt},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _batch_cache_salt(
    configs: list[dict[str, Any]],
    column_dicts: dict[tuple[str, str], dict[str, Any]] | None,
) -> str:
    """整批共享的缓存盐：字段字典版本 + 表结构版本，每批只算一次。

    ``config["tables"]`` 由数据源整体注入，绝大多数模型共享同一 list 对象；
    按对象身份去重后只序列化一次，避免把同一份表结构重复序列化 N 遍。
    """
    unique_tables: list[Any] = []
    seen_tables: set[int] = set()
    for config in configs:
        tables = config.get("tables")
        if tables is None:
            continue
        marker = id(tables)
        if marker in seen_tables:
            continue
        seen_tables.add(marker)
        unique_tables.append(tables)
    dict_entries = [
        [list(key), value]
        for key, value in sorted(
            (column_dicts or {}).items(),
            key=lambda item: (str(item[0][0]), str(item[0][1])),
        )
    ]
    payload = json.dumps(
        {"column_dicts": dict_entries, "tables": unique_tables},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_file_name(config: dict[str, Any]) -> str:
    """生成 `{清洗后的模型名}__{id后缀}.yaml`，让 grep/list_dir 输出自带模型语义。

    id 后缀保证清洗后碰撞（如 `模型A/B` 与 `模型A B` 同折成 `模型A_B`）、
    跨数据源组同名时文件名仍互不相同；模型名整体唯一由
    `uniq_group_id_config_name_delete_flag` 唯一索引背书。
    """
    config_id = _config_id(config)
    name = str(config.get("name") or config.get("semantic_config_name") or "").strip()
    cleaned_name = _FILE_COMPONENT_RE.sub("_", name).strip("._")[:_MODEL_NAME_MAX_CHARS]
    cleaned_id = _FILE_COMPONENT_RE.sub("_", config_id).strip("._")
    # 取末段（config_id 形如 sc_<时间戳>_<uuid12>）再截后 8 位，避免把分隔符带进后缀
    id_tail = cleaned_id.rsplit("_", 1)[-1] if cleaned_id else ""
    id_suffix = id_tail[-_ID_SUFFIX_CHARS:]
    if not cleaned_name and not id_suffix:
        raise ValueError("语义配置缺少可用于快照文件名的名称与 ID")
    if not id_suffix:
        return f"{cleaned_name}.yaml"
    if not cleaned_name:
        return f"{id_suffix}.yaml"
    return f"{cleaned_name}__{id_suffix}.yaml"


def _directory_item(config: dict[str, Any], path: str = "") -> dict[str, Any]:
    metrics = config.get("metrics") if isinstance(config.get("metrics"), list) else []
    dimensions = config.get("dimensions") if isinstance(config.get("dimensions"), list) else []
    config_id = _config_id(config)
    item = {
        "id": config_id,
        "name": str(config.get("name") or config.get("semantic_config_name") or config_id).strip(),
        "description": str(config.get("description") or config.get("semantic_config_desc") or "").strip(),
        "metrics_total": len(metrics),
        "dimensions_total": len(dimensions),
    }
    if path:
        item["path"] = path
    return item


def _normalize_loaded_config(config: Any, fallback_id: str) -> dict[str, Any] | None:
    """校验并补齐一条已加载的模型配置；未启用/无效返回 None（不计入 failed_ids）。"""
    if not isinstance(config, dict) or not config.get("enabled", True):
        return None
    result = dict(config)
    if result.get("relationships") and not result.get("joins"):
        result["joins"] = result["relationships"]
    if not result.get("id"):
        result["id"] = fallback_id
    return result


async def _load_selected_semantic_config(
    datasource_id: str | None,
    semantic_config_id: str,
    user_id: str | None,
    column_dicts: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Reload one selected model with the current user's authorization."""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_config_id = str(semantic_config_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_datasource_id or not normalized_config_id:
        return None
    config = await get_semantic_config_service().get_runtime_config_async(
        normalized_datasource_id,
        normalized_config_id,
        normalized_user_id,
        column_dicts=column_dicts,
    )
    return _normalize_loaded_config(config, normalized_config_id)


def enabled_candidate_ids(configs: list[dict[str, Any]] | None) -> list[str]:
    """取全量可见且启用的候选模型 id（保序去重）。"""
    ids: list[str] = []
    for config in configs or []:
        config_id = str((config or {}).get("id") or "").strip()
        if config_id and bool(config.get("enabled", True)) and config_id not in ids:
            ids.append(config_id)
    return ids


async def prematerialize_enabled_semantic_models(
    *,
    workspace_runtime: Any,
    datasource_id: str | None,
    user_id: str | None,
    source_type: str = "",
) -> dict[str, Any]:
    """Run 启动前把当前数据源组全部启用模型物化到 workspace/semantic/。

    让模型无需先触发 semantic_search 即可用 dir_list/file_grep/file_read
    检索语义目录；YAML 内容有 sha256 缓存，只有首次或配置变更才真正写盘。
    返回 {"total": 物化文件数, "failed_ids": 物化失败并跳过的模型 ID}；
    未绑定数据源组或无启用模型时 total 为 0。整体异常由调用方 fail-fast，
    部分失败（failed_ids）由调用方决定是否提示用户与注入上下文。
    """
    normalized_datasource_id = str(datasource_id or "").strip()
    if not normalized_datasource_id or workspace_runtime is None:
        return {"total": 0, "failed_ids": []}
    service = get_semantic_config_service()
    started = time.perf_counter()
    # 一次拿到「全量配置 + 字段字典」并透传给物化：否则物化会按 ID 逐个重载，
    # 每个模型都重新拉一遍数据源全量表结构，模型数上百时退化为 N+1 查库。
    runtime_configs, column_dicts = await service.get_runtime_bundle_async(
        normalized_datasource_id, str(user_id or "")
    )
    loaded_ms = (time.perf_counter() - started) * 1000
    materialized = await materialize_runtime_semantic_models(
        workspace_runtime=workspace_runtime,
        datasource_id=normalized_datasource_id,
        user_id=str(user_id or ""),
        source_type=str(source_type or ""),
        semantic_config_ids=enabled_candidate_ids(runtime_configs),
        configs=runtime_configs,
        column_dicts=column_dicts,
        prune_stale=True,
    )
    logger.info(
        "语义模型预物化耗时: datasource_id=%s configs=%s 加载=%.0fms 总计=%.0fms",
        normalized_datasource_id,
        len(runtime_configs),
        loaded_ms,
        (time.perf_counter() - started) * 1000,
    )
    return {
        "total": len(materialized.get("directory") or []),
        "failed_ids": list(materialized.get("failed_ids") or []),
    }


def _cache_get(key: str) -> str | None:
    value = _YAML_CACHE.get(key)
    if value is not None:
        _YAML_CACHE.move_to_end(key)
    return value


def _cache_put(key: str, value: str, limit: int = _CACHE_LIMIT) -> None:
    _YAML_CACHE[key] = value
    _YAML_CACHE.move_to_end(key)
    while len(_YAML_CACHE) > limit:
        _YAML_CACHE.popitem(last=False)


async def _prune_stale_semantic_files(workspace_runtime: Any, keep_paths: set[str]) -> None:
    """删除 semantic 目录中不在保留集内的残留 YAML（停用/删除模型遗留）。

    清理失败只告警不抛错：残留文件最多浪费一次检索轮次，不影响正确性。
    """
    try:
        existing = await workspace_runtime.list_dir(path=SEMANTIC_SNAPSHOT_DIR, max_depth=1)
    except FileNotFoundError:
        return
    except Exception as exc:  # noqa: BLE001 - 清理属尽力而为
        logger.warning("列举语义目录失败，跳过残留清理: %s", exc)
        return
    for item in existing or []:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        path = str(item.get("path") or "").strip()
        if not path.endswith(".yaml") or path in keep_paths:
            continue
        try:
            # 走系统内部删除通道：semantic 目录对模型工具是只读快照，
            # 普通 delete_path 会被路径策略挡住（见 runtime.resolve_workspace_path）。
            await workspace_runtime.delete_internal_text_files(path=path)
            logger.info("清理语义目录残留文件: %s", path)
        except Exception as exc:  # noqa: BLE001 - 清理属尽力而为
            logger.warning("清理语义目录残留文件失败: path=%s error=%s", path, exc)


def _normalize_config_ids(semantic_config_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for config_id in semantic_config_ids or []:
        value = str(config_id or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def _partition_loaded_configs(
    normalized_ids: list[str],
    loaded: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """分区加载结果：启用且有效的进 configs，异常进 failed_ids。"""
    configs: list[dict[str, Any]] = []
    failed_ids: list[str] = []
    for config_id, result in zip(normalized_ids, loaded):
        if isinstance(result, Exception):
            logger.error("加载选中语义模型失败: config_id=%s error=%s", config_id, result)
            failed_ids.append(config_id)
            continue
        if isinstance(result, dict) and _config_id(result) and bool(result.get("enabled", True)):
            configs.append(dict(result))
    return configs, failed_ids


async def _render_selected_configs(
    configs: list[dict[str, Any]],
    source_type: str,
    column_dicts: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]], int, list[str]]:
    """批量转换并缓存语义模型，返回 (entries, directory, cache_hits, failed_ids)。

    渲染前把命中的字段字典标注到配置副本（annotate_export_column_dicts），
    供 converter 把字典四项写入 Ossie 扩展；标注参与缓存键，字典变更后自动失效。
    """
    root = PurePosixPath(SEMANTIC_SNAPSHOT_DIR)
    converter = ChatDataToOssieConverter()
    # 单批模型数超过默认上限时按批扩容：否则整批互相淘汰，缓存永远命中不了，
    # 大模型量数据源组会退化成每轮全量重转。
    cache_limit = min(max(_CACHE_LIMIT, len(configs)), _CACHE_HARD_LIMIT)
    salt = _batch_cache_salt(configs, column_dicts)

    async def render(config: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        config_id = _config_id(config)
        effective_source_type = str(source_type or config.get("source_type") or "").strip()
        # 纯类方法、无实例状态：直接走 SemanticConfigService，避免被注入的服务替身影响
        annotated = SemanticConfigService.annotate_export_column_dicts(config, column_dicts or {})
        key = _config_cache_key(annotated, effective_source_type, salt)
        cached = _cache_get(key)
        if cached is not None:
            return config, cached, True
        content = await asyncio.to_thread(converter.convert, annotated, source_type=effective_source_type)
        _cache_put(key, content, cache_limit)
        return config, content, False

    rendered = await asyncio.gather(*(render(config) for config in configs), return_exceptions=True)
    entries: list[tuple[str, str]] = []
    directory: list[dict[str, Any]] = []
    cache_hits = 0
    failed_ids: list[str] = []
    for config, result in zip(configs, rendered):
        if isinstance(result, Exception):
            logger.error("转换选中语义模型失败: config_id=%s error=%s", _config_id(config), result)
            failed_ids.append(_config_id(config))
            continue
        rendered_config, content, hit = result
        path = str(root / _safe_file_name(rendered_config))
        entries.append((path, content))
        directory.append(_directory_item(rendered_config, path))
        cache_hits += int(hit)
    return entries, directory, cache_hits, failed_ids


async def materialize_runtime_semantic_models(
    *,
    workspace_runtime: Any,
    datasource_id: str | None,
    user_id: str | None,
    source_type: str = "",
    semantic_config_ids: list[str] | None = None,
    prune_stale: bool = False,
    configs: list[dict[str, Any]] | None = None,
    column_dicts: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reload and overwrite only the selected semantic model files.

    The database remains authoritative. The workspace YAML is a model-readable
    reference and is never used as the query execution source.

    ``configs`` / ``column_dicts`` 供调用方复用「已在手」的批量加载结果：
    传入后不再逐模型回源，整批物化只有 O(1) 次查库。不传时走逐条重载
    （单模型物化等小范围场景，保持原有授权语义）。
    ``prune_stale`` 仅应在传入“全部启用模型”完整范围时开启（预物化 /
    search_models），会删除目录里不属于本次物化清单的残留 YAML；
    单模型物化（list_fields）禁止开启，否则会误删其它模型文件。
    """
    normalized_ids = _normalize_config_ids(semantic_config_ids)
    if not normalized_ids:
        return {"directory": [], "paths": {}, "configs": {}, "failed_ids": []}

    # 字段数据字典按数据源整体拉取一次，所有模型复用；
    # 逐模型拉取会让全量物化时 DB 压力随模型数线性放大。
    service = get_semantic_config_service()
    if column_dicts is None:
        column_dicts = await service.get_column_dicts_async(str(datasource_id or ""))

    if configs is None:
        loaded = await asyncio.gather(*(
            _load_selected_semantic_config(datasource_id, config_id, user_id, column_dicts=column_dicts)
            for config_id in normalized_ids
        ), return_exceptions=True)
    else:
        # 复用调用方已加载的全量配置：同一次请求里这些数据刚查过，再按 ID
        # 逐个回源等于把整份数据源表结构重复加载 N 遍。
        pool = {_config_id(config): config for config in configs if _config_id(config)}
        loaded = [_normalize_loaded_config(pool.get(config_id), config_id) for config_id in normalized_ids]
    selected_configs, failed_ids = _partition_loaded_configs(normalized_ids, loaded)

    rendered_at = time.perf_counter()
    entries, directory, cache_hits, render_failed_ids = await _render_selected_configs(
        selected_configs, source_type, column_dicts,
    )
    render_ms = (time.perf_counter() - rendered_at) * 1000
    failed_ids.extend(render_failed_ids)

    written_at = time.perf_counter()
    await workspace_runtime.write_internal_text_files(entries)
    write_ms = (time.perf_counter() - written_at) * 1000
    if prune_stale:
        # 保留集以“加载成功的启用模型”为准而非 entries：
        # 转换失败的模型仍启用，不应因本轮转换异常丢掉原本有效的 YAML。
        keep_paths = {
            str(PurePosixPath(SEMANTIC_SNAPSHOT_DIR) / _safe_file_name(config)) for config in selected_configs
        }
        await _prune_stale_semantic_files(workspace_runtime, keep_paths)
    logger.info(
        "语义模型按需物料化完成: datasource_id=%s files=%s cache_hits=%s backend=%s 渲染=%.0fms 写盘=%.0fms",
        datasource_id,
        len(entries),
        cache_hits,
        workspace_runtime.backend_name,
        render_ms,
        write_ms,
    )
    if failed_ids:
        # 目录缺模型会让模型误判为“不存在该模型”，必须留下可排查的失败清单。
        logger.error(
            "语义模型物化不完整: datasource_id=%s 期望=%s 成功=%s 失败=%s",
            datasource_id,
            len(normalized_ids),
            len(entries),
            failed_ids,
        )
    return {
        "directory": directory,
        "paths": {item["id"]: item["path"] for item in directory},
        "configs": {_config_id(config): config for config in selected_configs},
        "failed_ids": failed_ids,
    }


__all__ = [
    "materialize_runtime_semantic_models",
    "SEMANTIC_SNAPSHOT_DIR",
]
