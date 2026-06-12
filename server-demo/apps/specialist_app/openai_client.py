"""Shared OpenAI client factory for agents."""

from __future__ import annotations

import logging
import os

import httpx
import openai

logger = logging.getLogger(__name__)


def _env_flag(key: str, default: bool = True) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    trimmed = value.strip()
    if not trimmed:
        return default
    return trimmed.lower() in {"1", "true", "yes", "y", "on"}


_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
_OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_HTTP_TIMEOUT", "60"))
_OPENAI_VERIFY_TLS = _env_flag("OPENAI_VERIFY_TLS", False)
_OPENAI_TRUST_ENV = _env_flag("OPENAI_HTTP_TRUST_ENV", True)

_httpx_client: httpx.AsyncClient | None = None
_openai_client: openai.AsyncOpenAI | None = None


def get_openai_client() -> openai.AsyncOpenAI:
    """Lazily construct the shared OpenAI async client."""
    global _httpx_client, _openai_client
    if _openai_client is not None:
        return _openai_client

    api_key = os.getenv("OPENAI_API_KEY") or _OPENAI_API_KEY
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(
            timeout=_OPENAI_TIMEOUT_SECONDS,
            verify=_OPENAI_VERIFY_TLS,
            trust_env=_OPENAI_TRUST_ENV,
        )
    _openai_client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=_OPENAI_BASE_URL,
        http_client=_httpx_client,
    )
    logger.info(
        "Initialized OpenAI client base_url=%s timeout=%ss verify_tls=%s trust_env=%s",
        _OPENAI_BASE_URL,
        _OPENAI_TIMEOUT_SECONDS,
        _OPENAI_VERIFY_TLS,
        _OPENAI_TRUST_ENV,
    )
    return _openai_client


__all__ = [
    "get_openai_client",
    "_OPENAI_BASE_URL",
    "_OPENAI_VERIFY_TLS",
    "_OPENAI_TRUST_ENV",
]
