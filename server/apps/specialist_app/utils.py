from __future__ import annotations

import time
from typing import Iterable


def chunk_text(text: str, chunk_size: int = 200) -> Iterable[str]:
    if not text:
        return []
    start = 0
    while start < len(text):
        yield text[start : start + chunk_size]
        start += chunk_size


def utc_now_iso() -> str:
    """Return UTC timestamp in ISO format with Z suffix."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = ["chunk_text", "utc_now_iso"]
