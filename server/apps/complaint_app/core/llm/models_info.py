from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parent / "models_info.json"
_MODELS_CACHE: List[Dict[str, Any]] = []
_MODELS_CACHE_EXPIRES_AT = 0.0


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    val = str(raw).strip().lower()
    if not val:
        return default
    return val in {"1", "true", "yes", "y", "on"}


def _is_local_mode() -> bool:
    return _env_bool("IS_LOCAL", False)


def _cache_ttl_seconds() -> int:
    raw = os.getenv("MODEL_INFO_CACHE_TTL_SECONDS", "600")
    try:
        return max(0, int(raw))
    except Exception:
        return 600


def _match_runtime(item: Dict[str, Any], is_local: bool) -> bool:
    marker = item.get("is_local")
    if marker is None:
        return not is_local
    return bool(marker) == is_local


def _filter_by_runtime(models: List[Dict[str, Any]], *, is_local: bool) -> List[Dict[str, Any]]:
    return [item for item in models if _match_runtime(item, is_local)]


def _merge_by_model_id(base: List[Dict[str, Any]], override: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    ordered: List[str] = []
    for item in base + override:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        if model_id not in merged:
            ordered.append(model_id)
        merged[model_id] = item
    return [merged[model_id] for model_id in ordered]


def _normalize_models(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _load_from_file(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_models(payload)
    except Exception as exc:
        logger.warning("models_info.json load failed: %s", exc)
        return []


def _load_from_url(url: str) -> List[Dict[str, Any]]:
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        payload = resp.json()
        return _normalize_models(payload)
    except Exception as exc:
        logger.warning("models info url fetch failed: %s", exc)
        return []


def load_models_info() -> List[Dict[str, Any]]:
    global _MODELS_CACHE, _MODELS_CACHE_EXPIRES_AT
    now = time.time()
    if _MODELS_CACHE and now < _MODELS_CACHE_EXPIRES_AT:
        return _MODELS_CACHE

    is_local = _is_local_mode()
    file_models = _load_from_file(_DEFAULT_PATH)
    if is_local:
        models = _filter_by_runtime(file_models, is_local=True)
        _MODELS_CACHE = models
        _MODELS_CACHE_EXPIRES_AT = now + _cache_ttl_seconds()
        return models

    models = _filter_by_runtime(file_models, is_local=False)
    url = os.getenv("LLM_MODELS_INFO_URL", "").strip()
    if url:
        remote_models = _load_from_url(url)
        if remote_models:
            models = _merge_by_model_id(models, remote_models)

    _MODELS_CACHE = models
    _MODELS_CACHE_EXPIRES_AT = now + _cache_ttl_seconds()
    return models


def get_model_max_tokens(model_id: Optional[str]) -> Optional[int]:
    if not model_id:
        return None
    for item in load_models_info():
        if item.get("id") != model_id:
            continue
        raw = item.get("max_model_len") or item.get("max_tokens")
        try:
            value = int(raw)
        except Exception as exc:
            logger.error("[models_info] parse max tokens failed model=%s raw=%s err=%s", model_id, raw, exc)
            value = None
        if value and value > 0:
            return value
    return None


__all__ = ["load_models_info", "get_model_max_tokens"]
