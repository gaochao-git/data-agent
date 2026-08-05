"""Materialize request-scoped Ossie semantic-model snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import OrderedDict
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from utils.logger import get_logger

from .chat_data_ossie_converter import ChatDataToOssieConverter
from .datasource_manager import get_datasource_manager

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


def _directory_item(config: dict[str, Any], path: str) -> dict[str, Any]:
    metrics = config.get("metrics") if isinstance(config.get("metrics"), list) else []
    dimensions = config.get("dimensions") if isinstance(config.get("dimensions"), list) else []
    config_id = _config_id(config)
    return {
        "id": config_id,
        "name": str(config.get("name") or config.get("semantic_config_name") or config_id).strip(),
        "description": str(config.get("description") or config.get("semantic_config_desc") or "").strip(),
        "metrics_total": len(metrics),
        "dimensions_total": len(dimensions),
        "path": path,
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


async def load_runtime_semantic_config_catalog(
    datasource_id: str | None,
    user_id: str | None,
) -> list[dict[str, Any]]:
    """Load the complete authorized runtime catalog once for one request."""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_datasource_id:
        return []
    manager = get_datasource_manager()
    loader = getattr(manager, "get_runtime_semantic_configs_async", None)
    if callable(loader):
        configs = await loader(normalized_datasource_id, normalized_user_id)
    else:
        configs = await manager.get_semantic_configs_async(normalized_datasource_id, normalized_user_id)
    return [
        dict(config)
        for config in configs or []
        if isinstance(config, dict) and _config_id(config) and bool(config.get("enabled", True))
    ]


async def materialize_runtime_semantic_models(
    *,
    workspace_runtime: Any,
    datasource_id: str | None,
    user_id: str | None,
    source_type: str = "",
) -> dict[str, Any]:
    """Write an immutable Ossie snapshot for the current request.

    The request directory is unique, while converted YAML text is cached by
    the full normalized config content. This preserves request isolation and
    avoids repeating conversion work when a model has not changed.
    """
    configs = await load_runtime_semantic_config_catalog(datasource_id, user_id)
    request_id = uuid4().hex
    root = PurePosixPath("workspace") / "semantic" / request_id
    converter = ChatDataToOssieConverter()
    entries: list[tuple[str, str]] = []
    directory: list[dict[str, Any]] = []
    cache_hits = 0

    async def render(config: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        config_id = _config_id(config)
        key = _config_cache_key(config, source_type)
        cached = _cache_get(key)
        if cached is not None:
            return config, cached, True
        content = await asyncio.to_thread(converter.convert, config, source_type=source_type)
        _cache_put(key, content)
        return config, content, False

    rendered = await asyncio.gather(*(render(config) for config in configs))
    for config, content, hit in rendered:
        config_id = _config_id(config)
        path = str(root / _safe_file_name(config_id))
        entries.append((path, content))
        directory.append(_directory_item(config, f"/mnt/user-data/{path}"))
        cache_hits += int(hit)

    if entries:
        await workspace_runtime.write_text_files(entries)
    logger.info(
        "语义模型 Ossie 请求快照完成: datasource_id=%s configs=%s files=%s cache_hits=%s request_id=%s backend=%s",
        datasource_id,
        len(configs),
        len(entries),
        cache_hits,
        request_id,
        workspace_runtime.backend_name,
    )
    return {
        "request_id": request_id,
        "directory": directory,
        "paths": {item["id"]: item["path"] for item in directory},
        "configs": { _config_id(config): config for config in configs },
    }


__all__ = [
    "load_runtime_semantic_config_catalog",
    "materialize_runtime_semantic_models",
]
