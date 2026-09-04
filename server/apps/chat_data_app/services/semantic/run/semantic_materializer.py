"""Materialize selected semantic models into the current conversation workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from pathlib import PurePosixPath
from typing import Any

from utils.logger import get_logger

from .chat_data_ossie_converter import ChatDataToOssieConverter
from .semantic_config_service import get_semantic_config_service

logger = get_logger(__name__)

_CACHE_LIMIT = 256
_YAML_CACHE: OrderedDict[str, str] = OrderedDict()
# 语义模型 YAML 快照目录（受管只读区，仅供 file_grep/file_read）
SEMANTIC_SNAPSHOT_DIR = "/mnt/user-data/workspace/semantic"
# unicode 感知：保留中文，仅把空白/路径分隔等不安全字符折叠成下划线
_FILE_COMPONENT_RE = re.compile(r"[^\w.-]+")
_MODEL_NAME_MAX_CHARS = 40
_ID_SUFFIX_CHARS = 8


def _config_id(config: dict[str, Any]) -> str:
    return str(config.get("id") or config.get("semantic_config_id") or "").strip()


def _config_cache_key(config: dict[str, Any], source_type: str) -> str:
    payload = json.dumps(
        {"source_type": str(source_type or "").strip(), "config": config},
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
    if not isinstance(config, dict) or not config.get("enabled", True):
        return None
    result = dict(config)
    if result.get("relationships") and not result.get("joins"):
        result["joins"] = result["relationships"]
    if not result.get("id"):
        result["id"] = normalized_config_id
    return result


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
    runtime_configs = await get_semantic_config_service().get_runtime_configs_async(
        normalized_datasource_id, str(user_id or "")
    )
    materialized = await materialize_runtime_semantic_models(
        workspace_runtime=workspace_runtime,
        datasource_id=normalized_datasource_id,
        user_id=str(user_id or ""),
        source_type=str(source_type or ""),
        semantic_config_ids=enabled_candidate_ids(runtime_configs),
        prune_stale=True,
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


def _cache_put(key: str, value: str) -> None:
    _YAML_CACHE[key] = value
    _YAML_CACHE.move_to_end(key)
    while len(_YAML_CACHE) > _CACHE_LIMIT:
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
            await workspace_runtime.delete_path(path=path, recursive=False, expected_type="file")
            logger.info("清理语义目录残留文件: %s", path)
        except Exception as exc:  # noqa: BLE001 - 清理属尽力而为
            logger.warning("清理语义目录残留文件失败: path=%s error=%s", path, exc)


async def materialize_runtime_semantic_models(
    *,
    workspace_runtime: Any,
    datasource_id: str | None,
    user_id: str | None,
    source_type: str = "",
    semantic_config_ids: list[str] | None = None,
    prune_stale: bool = False,
) -> dict[str, Any]:
    """Reload and overwrite only the selected semantic model files.

    The database remains authoritative. The workspace YAML is a model-readable
    reference and is never used as the query execution source.

    ``prune_stale`` 仅应在传入“全部启用模型”完整范围时开启（预物化 /
    search_models），会删除目录里不属于本次物化清单的残留 YAML；
    单模型物化（list_fields）禁止开启，否则会误删其它模型文件。
    """
    normalized_ids = []
    for config_id in semantic_config_ids or []:
        value = str(config_id or "").strip()
        if value and value not in normalized_ids:
            normalized_ids.append(value)
    if not normalized_ids:
        return {"directory": [], "paths": {}, "configs": {}, "failed_ids": []}

    # 字段数据字典按数据源整体拉取一次，所有模型复用；
    # 逐模型拉取会让全量物化时 DB 压力随模型数线性放大。
    service = get_semantic_config_service()
    column_dicts = await service.get_column_dicts_async(str(datasource_id or ""))

    loaded = await asyncio.gather(*(
        _load_selected_semantic_config(datasource_id, config_id, user_id, column_dicts=column_dicts)
        for config_id in normalized_ids
    ), return_exceptions=True)
    configs: list[dict[str, Any]] = []
    failed_ids: list[str] = []
    for config_id, result in zip(normalized_ids, loaded):
        if isinstance(result, Exception):
            logger.error("加载选中语义模型失败: config_id=%s error=%s", config_id, result)
            failed_ids.append(config_id)
            continue
        if isinstance(result, dict) and _config_id(result) and bool(result.get("enabled", True)):
            configs.append(dict(result))

    root = PurePosixPath(SEMANTIC_SNAPSHOT_DIR)
    converter = ChatDataToOssieConverter()
    entries: list[tuple[str, str]] = []
    directory: list[dict[str, Any]] = []
    cache_hits = 0

    async def render(config: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        config_id = _config_id(config)
        effective_source_type = str(source_type or config.get("source_type") or "").strip()
        key = _config_cache_key(config, effective_source_type)
        cached = _cache_get(key)
        if cached is not None:
            return config, cached, True
        content = await asyncio.to_thread(converter.convert, config, source_type=effective_source_type)
        _cache_put(key, content)
        return config, content, False

    rendered = await asyncio.gather(*(render(config) for config in configs), return_exceptions=True)
    for config, result in zip(configs, rendered):
        if isinstance(result, Exception):
            logger.error("转换选中语义模型失败: config_id=%s error=%s", _config_id(config), result)
            failed_ids.append(_config_id(config))
            continue
        rendered_config, content, hit = result
        config_id = _config_id(rendered_config)
        path = str(root / _safe_file_name(rendered_config))
        entries.append((path, content))
        directory.append(_directory_item(rendered_config, path))
        cache_hits += int(hit)

    await workspace_runtime.write_internal_text_files(entries)
    if prune_stale:
        # 保留集以“加载成功的启用模型”为准而非 entries：
        # 转换失败的模型仍启用，不应因本轮转换异常丢掉原本有效的 YAML。
        keep_paths = {str(root / _safe_file_name(config)) for config in configs}
        await _prune_stale_semantic_files(workspace_runtime, keep_paths)
    logger.info(
        "语义模型按需物料化完成: datasource_id=%s selected_ids=%s files=%s cache_hits=%s backend=%s",
        datasource_id,
        normalized_ids,
        len(entries),
        cache_hits,
        workspace_runtime.backend_name,
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
        "configs": {_config_id(config): config for config in configs},
        "failed_ids": failed_ids,
    }


__all__ = [
    "materialize_runtime_semantic_models",
    "SEMANTIC_SNAPSHOT_DIR",
]
