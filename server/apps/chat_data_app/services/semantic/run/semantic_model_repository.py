"""统一语义模型加载适配层。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from ...datasource.manager import get_datasource_manager
from .semantic_config_service import SemanticConfigService, get_semantic_config_service

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
        )
    except ValueError:
        return None


async def list_runtime_semantic_config_directory(
    datasource_id: str | None,
    user_id: str | None = None,
) -> List[Dict[str, Any]]:
    """List the lightweight authorized model directory for a datasource."""
    normalized_datasource_id = str(datasource_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_datasource_id:
        return []
    directory = await get_semantic_config_service().get_directory_async(
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


async def load_semantic_model(
    datasource_id: str,
    semantic_config_id: Optional[str],
    *,
    user_id: Optional[str] = None,
    mode: SemanticModelLoadMode = "runtime",
    manager: Any = None,
    semantic_service: Any = None,
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
    config_service = semantic_service or (
        SemanticConfigService(ds_manager) if manager is not None else get_semantic_config_service()
    )
    datasource = await ds_manager.get_datasource_async(normalized_datasource_id)
    if not datasource:
        raise ValueError("数据源不存在")

    selected = await _load_selected_config(
        config_service,
        normalized_datasource_id,
        normalized_config_id,
        normalized_user_id,
        mode,
    )
    if not selected:
        raise ValueError("未找到可用语义配置")
    if not selected.get("enabled", True):
        raise ValueError("语义配置未启用")
    return await _finalize_semantic_model(
        selected, datasource, mode, normalized_config_id, normalized_datasource_id, ds_manager
    )


async def _finalize_semantic_model(
    selected: Dict[str, Any],
    datasource: Dict[str, Any],
    mode: SemanticModelLoadMode,
    normalized_config_id: str,
    normalized_datasource_id: str,
    ds_manager: Any,
) -> Dict[str, Any]:
    """按加载模式补齐模型元数据（ID 归一、joins 别名、来源与表结构）。"""
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
    semantic_service: Any,
    datasource_id: str,
    semantic_config_id: str,
    user_id: str,
    mode: SemanticModelLoadMode,
) -> Optional[Dict[str, Any]]:
    if mode == "runtime":
        return await semantic_service.get_runtime_config_async(
            datasource_id,
            semantic_config_id,
            user_id,
        )
    return await semantic_service.get_config_async(datasource_id, semantic_config_id, user_id)
