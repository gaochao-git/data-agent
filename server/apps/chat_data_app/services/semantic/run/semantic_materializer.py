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
_FILE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


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


def _safe_file_name(config_id: str) -> str:
    value = _FILE_COMPONENT_RE.sub("_", str(config_id or "").strip()).strip("._")
    if not value:
        raise ValueError("语义配置 ID 不能用于快照文件名")
    return f"{value}.yaml"


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
    )
    if not isinstance(config, dict) or not config.get("enabled", True):
        return None
    result = dict(config)
    if result.get("relationships") and not result.get("joins"):
        result["joins"] = result["relationships"]
    if not result.get("id"):
        result["id"] = normalized_config_id
    return result


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


async def materialize_runtime_semantic_models(
    *,
    workspace_runtime: Any,
    datasource_id: str | None,
    user_id: str | None,
    source_type: str = "",
    semantic_config_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Reload and overwrite only the selected semantic model files.

    The database remains authoritative. The workspace YAML is a model-readable
    reference and is never used as the query execution source.
    """
    normalized_ids = []
    for config_id in semantic_config_ids or []:
        value = str(config_id or "").strip()
        if value and value not in normalized_ids:
            normalized_ids.append(value)
    if not normalized_ids:
        return {"directory": [], "paths": {}, "configs": {}}

    loaded = await asyncio.gather(*(
        _load_selected_semantic_config(datasource_id, config_id, user_id)
        for config_id in normalized_ids
    ), return_exceptions=True)
    configs: list[dict[str, Any]] = []
    for config_id, result in zip(normalized_ids, loaded):
        if isinstance(result, Exception):
            logger.warning("加载选中语义模型失败: config_id=%s error=%s", config_id, result)
            continue
        if isinstance(result, dict) and _config_id(result) and bool(result.get("enabled", True)):
            configs.append(dict(result))

    root = PurePosixPath("/mnt/user-data") / "workspace" / "semantic"
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
            logger.warning("转换选中语义模型失败: config_id=%s error=%s", _config_id(config), result)
            continue
        rendered_config, content, hit = result
        config_id = _config_id(rendered_config)
        path = str(root / _safe_file_name(config_id))
        entries.append((path, content))
        directory.append(_directory_item(rendered_config, path))
        cache_hits += int(hit)

    await workspace_runtime.write_internal_text_files(entries)
    logger.info(
        "语义模型按需物料化完成: datasource_id=%s selected_ids=%s files=%s cache_hits=%s backend=%s",
        datasource_id,
        normalized_ids,
        len(entries),
        cache_hits,
        workspace_runtime.backend_name,
    )
    return {
        "directory": directory,
        "paths": {item["id"]: item["path"] for item in directory},
        "configs": {_config_id(config): config for config in configs},
    }


__all__ = [
    "materialize_runtime_semantic_models",
]
