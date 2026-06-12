"""Shared OpenAI async client for complaint services."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os

import httpx
import openai

logger = logging.getLogger(__name__)


def _env_flag(key: str, default: bool = True) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    stripped = val.strip()
    if not stripped:
        return default
    return stripped.lower() in {"1", "true", "yes", "y", "on"}


_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
_OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
_OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_HTTP_TIMEOUT", "600"))
_OPENAI_VERIFY_TLS = _env_flag("OPENAI_VERIFY_TLS", False)
_OPENAI_TRUST_ENV = _env_flag("OPENAI_HTTP_TRUST_ENV", True)

_httpx_client: httpx.AsyncClient | None = None
_openai_client: openai.AsyncOpenAI | None = None


def get_async_openai_client() -> openai.AsyncOpenAI:
    """Lazily construct the shared OpenAI async client."""
    global _httpx_client, _openai_client
    if _openai_client is not None:
        return _openai_client

    api_key = os.getenv("OPENAI_API_KEY") or _OPENAI_API_KEY
    if not api_key:
        logger.error(f"OPENAI_API_KEY is not configured, api_key={api_key}")
        raise RuntimeError("OPENAI_API_KEY is not configured")

    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(
            timeout=_OPENAI_TIMEOUT_SECONDS,
            verify=_OPENAI_VERIFY_TLS,
            trust_env=_OPENAI_TRUST_ENV,
            limits=httpx.Limits(keepalive_expiry=30.0),
        )
    _openai_client = openai.AsyncOpenAI(
        api_key=api_key,
        base_url=_OPENAI_BASE_URL,
        http_client=_httpx_client,
    )
    logger.info(
        "Initialized complaint OpenAI client base_url=%s timeout=%ss verify_tls=%s trust_env=%s",
        _OPENAI_BASE_URL,
        _OPENAI_TIMEOUT_SECONDS,
        _OPENAI_VERIFY_TLS,
        _OPENAI_TRUST_ENV,
    )
    return _openai_client


async def _close_httpx_client() -> None:
    global _httpx_client, _openai_client
    if _httpx_client is None:
        return
    client = _httpx_client
    _httpx_client = None
    _openai_client = None
    await client.aclose()


def _close_httpx_client_sync() -> None:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        try:
            loop.create_task(_close_httpx_client())
            return
        except Exception as exc:
            logger.warning("schedule httpx close failed: %s", exc)
            return
    try:
        asyncio.run(_close_httpx_client())
    except RuntimeError:
        if loop and not loop.is_running():
            loop.run_until_complete(_close_httpx_client())


atexit.register(_close_httpx_client_sync)


__all__ = ["get_async_openai_client"]
