"""统一语义模型加载适配层。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from .datasource_manager import get_datasource_manager

SemanticModelLoadMode = Literal["runtime", "full"]


async def load_runtime_semantic_config(
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_id: str | None = None,
) -> Dict[str, Any] | None:
    """Load an authorized runtime model for query and conversation services."""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_config_id = str(semantic_config_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_datasource_id or not normalized_config_id:
        return None
    try:
        return await load_semantic_model(
            normalized_datasource_id,
            normalized_config_id,
            user_id=normalized_user_id,
            mode="runtime",
            manager=get_datasource_manager(),
        )
    except ValueError:
        return None


def _directory_item(config: Dict[str, Any]) -> Dict[str, Any]:
    config_id = str(config.get("id") or config.get("semantic_config_id") or "").strip()
    metrics = config.get("metrics") if isinstance(config.get("metrics"), list) else []
    dimensions = config.get("dimensions") if isinstance(config.get("dimensions"), list) else []
    return {
        "id": config_id,
        "name": str(config.get("name") or config.get("semantic_config_name") or config_id).strip(),
        "description": str(config.get("description") or config.get("semantic_config_desc") or "").strip(),
        "metrics_total": len(metrics),
        "dimensions_total": len(dimensions),
    }


async def list_runtime_semantic_config_directory(
    datasource_id: str | None,
    user_id: str | None = None,
) -> List[Dict[str, Any]]:
    """List the lightweight authorized model directory for a datasource."""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_datasource_id:
        return []
    manager = get_datasource_manager()
    if hasattr(manager, "get_semantic_config_directory_async"):
        directory = await manager.get_semantic_config_directory_async(
            normalized_datasource_id,
            normalized_user_id,
        )
        return [
            {key: value for key, value in item.items() if key != "path"}
            for item in directory or []
            if isinstance(item, dict)
            and str(item.get("id") or item.get("semantic_config_id") or "").strip()
            and bool(item.get("enabled", True))
        ]
    if hasattr(manager, "get_runtime_semantic_configs_async"):
        configs = await manager.get_runtime_semantic_configs_async(normalized_datasource_id, normalized_user_id)
    else:
        configs = await manager.get_semantic_configs_async(normalized_datasource_id, normalized_user_id)
    return [
        _directory_item(config)
        for config in configs or []
        if str((config or {}).get("id") or (config or {}).get("semantic_config_id") or "").strip()
        and bool((config or {}).get("enabled", True))
    ]


async def load_semantic_model(
    datasource_id: str,
    semantic_config_id: Optional[str],
    *,
    user_id: Optional[str] = None,
    mode: SemanticModelLoadMode = "runtime",
    manager: Any = None,
) -> Dict[str, Any]:
    """按明确用途加载当前用户可用的语义模型。"""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_config_id = str(semantic_config_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_config_id:
        raise ValueError("缺少语义模型ID")
    if mode not in {"runtime", "full"}:
        raise ValueError(f"不支持的语义模型加载模式: {mode}")

    ds_manager = manager or get_datasource_manager()
    datasource = await ds_manager.get_datasource_async(normalized_datasource_id)
    if not datasource:
        raise ValueError("数据源不存在")

    selected = await _load_selected_config(
        ds_manager,
        datasource,
        normalized_datasource_id,
        normalized_config_id,
        normalized_user_id,
        mode,
    )
    if not selected:
        raise ValueError("未找到可用语义配置")
    if not selected.get("enabled", True):
        raise ValueError("语义配置未启用")

    result = dict(selected)
    result["id"] = str(result.get("id") or normalized_config_id).strip()
    if result.get("relationships") and not result.get("joins"):
        result["joins"] = result["relationships"]
    if datasource.get("source_type"):
        result["source_type"] = datasource["source_type"]
    if mode == "full":
        cached_tables = await ds_manager.get_cached_schema_async(normalized_datasource_id)
        result["tables"] = cached_tables or result.get("tables") or []
    return result


async def _load_selected_config(
    manager: Any,
    datasource: Dict[str, Any],
    datasource_id: str,
    semantic_config_id: str,
    user_id: str,
    mode: SemanticModelLoadMode,
) -> Optional[Dict[str, Any]]:
    if mode == "runtime":
        loader = getattr(manager, "get_runtime_semantic_config_async", None)
        if loader is None:
            loader = manager.get_semantic_config_async
        return await loader(datasource_id, semantic_config_id, user_id) if user_id else await loader(datasource_id, semantic_config_id)

    if user_id:
        configs = await manager.get_semantic_configs_async(datasource_id, user_id)
    else:
        configs = datasource.get("semantic_configs")
        if configs is None:
            configs = await manager.get_semantic_configs_async(datasource_id)
    return next(
        (
            config
            for config in configs or []
            if str(config.get("id") or config.get("semantic_config_id") or "").strip()
            == semantic_config_id
        ),
        None,
    )
