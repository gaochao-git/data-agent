"""Utilities for normalizing markdown-style text outputs."""

from __future__ import annotations

from typing import Optional


def cleanup_markdown_blank_lines(text: Optional[str]) -> str:
    """Condense redundant blank lines while keeping markdown spacing readable."""

    if not text:
        return ""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines = []
    pending_blank = False

    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            if cleaned_lines and not pending_blank:
                cleaned_lines.append("")
                pending_blank = True
            continue

        cleaned_lines.append(line)
        pending_blank = False

    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()

    return "\n".join(cleaned_lines)
