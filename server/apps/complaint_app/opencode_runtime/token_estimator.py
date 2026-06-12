from __future__ import annotations

import os
from typing import Optional

from utils.logger import get_logger

try:
    import tiktoken
except Exception:  # pragma: no cover - optional dependency
    tiktoken = None

_CHARS_PER_TOKEN = 4
logger = get_logger(__name__)


def _trace_enabled() -> bool:
    return os.getenv("OPENCODE_RUNTIME_TRACE", "").strip().lower() in {"1", "true", "yes", "y", "on"}


class TokenEstimator:
    """Token 估算器。

    优先使用 tiktoken 进行模型级别的 token 计算，失败时回退为字符数/4 的粗略估算。
    缓存 encoding，避免重复初始化开销。
    """
    def __init__(self) -> None:
        self._enc_cache = {}

    def _get_encoding(self, model: Optional[str]):
        if not tiktoken:
            if _trace_enabled():
                logger.info("[token_estimator] resolve_encoding model=%s source=none reason=tiktoken_unavailable", model)
            return None
        if model in self._enc_cache:
            enc = self._enc_cache[model]
            if _trace_enabled():
                logger.info("[token_estimator] resolve_encoding model=%s source=cache has_encoding=%s", model, bool(enc))
            return enc
        enc = None
        source = "none"
        if model:
            try:
                enc = tiktoken.encoding_for_model(model)
                source = "model"
            except Exception as exc:
                if _trace_enabled():
                    logger.warning("[token_estimator] resolve_encoding model=%s source=model err=%s", model, exc)
                enc = None
        if enc is None:
            try:
                enc = tiktoken.get_encoding("cl100k_base")
                source = "fallback_cl100k_base"
            except Exception as exc:
                if _trace_enabled():
                    logger.warning("[token_estimator] resolve_encoding model=%s source=fallback_cl100k_base err=%s", model, exc)
                enc = None
                source = "none"
        self._enc_cache[model] = enc
        if _trace_enabled():
            logger.info("[token_estimator] resolve_encoding model=%s source=%s has_encoding=%s", model, source, bool(enc))
        return enc

    def count_text(self, text: str, model: Optional[str] = None) -> int:
        if not text:
            return 0
        enc = self._get_encoding(model)
        if enc is None:
            count = max(0, round(len(text) / _CHARS_PER_TOKEN))
            if _trace_enabled():
                logger.info("[token_estimator] count_text model=%s method=chars_div_4 chars=%s tokens=%s", model, len(text), count)
            return count
        try:
            count = len(enc.encode(text))
            if _trace_enabled():
                logger.info("[token_estimator] count_text model=%s method=tiktoken encoding=%s chars=%s tokens=%s", model, getattr(enc, "name", "unknown"), len(text), count)
            return count
        except Exception as exc:
            count = max(0, round(len(text) / _CHARS_PER_TOKEN))
            if _trace_enabled():
                logger.warning("[token_estimator] count_text model=%s method=chars_div_4 reason=encode_failed err=%s chars=%s tokens=%s", model, exc, len(text), count)
            return count


__all__ = ["TokenEstimator"]
