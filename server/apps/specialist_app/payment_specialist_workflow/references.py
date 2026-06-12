from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Tuple

logger = logging.getLogger(__name__)

_REFERENCE_LINE_PATTERN = re.compile(
    r"\[law\]\[(?P<title>[^\]]+)\]\[(?P<clause>[^\]]+)\]\[(?P<summary>[^\]]+)\]\[(?P<doc_id>[^\]]+)\]\[(?P<label>[^\]]+)\]"
)
_LINK_BLOCK_PATTERN = re.compile(r"```link\s*(?P<body>.*?)```", re.S)
_CLAUSE_LABEL_PATTERN = re.compile(r"(第[\d零〇一二三四五六七八九十百千万两]+条)")
_BULLET_LABEL_PATTERN = re.compile(r"^\s*([一二三四五六七八九十百千万两]+)([、.．])")


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


@dataclass
class ReferenceSnippet:
    label: str
    title: str
    clause: str
    content: str
    doc_id: str


def structured_search_payload(
    raw_text: str,
    *,
    snippets: Iterable[ReferenceSnippet] | None = None,
) -> str | None:
    """Convert various search payload formats into `引用n《title》...` lines."""

    entries = list(snippets or extract_reference_snippets(raw_text))
    if not entries:
        return None

    formatted_entries: list[str] = []
    for entry in entries:
        label = (entry.label or "").strip()
        title = (entry.title or "").strip()
        clause = (entry.clause or "").strip()
        content_raw = entry.content or ""
        content = _normalize_whitespace(str(content_raw)).strip()
        if not label or not title or not clause:
            logger.warning(
                "Skipping reference snippet missing required fields: label=%r title=%r clause=%r doc_id=%r",
                entry.label,
                entry.title,
                entry.clause,
                entry.doc_id,
            )
            continue
        line = f"引用{label}{title}{clause}"
        if content:
            line = f"{line}  {content}"
        formatted_entries.append(line)

    return "\n".join(formatted_entries)


def extract_reference_snippets(raw_text: str) -> list[ReferenceSnippet]:
    if not raw_text:
        return []

    snippets: list[ReferenceSnippet] = []

    # Preferred: already-formatted link blocks
    for block in _LINK_BLOCK_PATTERN.finditer(raw_text):
        body = block.group("body") or ""
        line_match = _REFERENCE_LINE_PATTERN.search(body)
        if not line_match:
            continue
        snippets.append(
            ReferenceSnippet(
                label=_clean_label(line_match.group("label")),
                title=line_match.group("title").strip(),
                clause=line_match.group("clause").strip(),
                content=_normalize_whitespace(line_match.group("summary").strip()),
                doc_id=line_match.group("doc_id").strip(),
            )
        )

    if snippets:
        return snippets

    # JSON payloads
    json_snippets = _extract_snippets_from_json(raw_text)
    if json_snippets:
        return json_snippets

    return snippets


def _extract_snippets_from_json(raw_text: str) -> list[ReferenceSnippet]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return []

    def _walk(obj: Any) -> Iterable[dict[str, Any]]:
        if isinstance(obj, dict):
            has_clause = any(
                key in obj
                for key in ("clause", "content", "text", "summary", "reason", "desc")
            )
            has_title = any(key in obj for key in ("title", "law"))
            if has_clause and has_title:
                yield obj
            for value in obj.values():
                yield from _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                yield from _walk(item)

    snippets: list[ReferenceSnippet] = []
    for index, item in enumerate(_walk(parsed), start=1):
        title = _ensure_title_format(str(item.get("title") or item.get("law") or ""))
        clause_text = (
            item.get("clause")
            or item.get("content")
            or item.get("text")
            or item.get("summary")
            or item.get("reason")
            or item.get("desc")
            or ""
        )
        clause_text = _normalize_whitespace(str(clause_text).strip())
        if not title or not clause_text:
            continue
        clause_label, remainder = _split_clause_text(clause_text)
        doc_id = str(
            item.get("doc_id")
            or item.get("document_id")
            or item.get("id")
            or item.get("documentId")
            or ""
        ).strip()
        snippets.append(
            ReferenceSnippet(
                label=str(index),
                title=title,
                clause=clause_label or "相关条款",
                content=remainder or clause_text,
                doc_id=doc_id,
            )
        )

    return snippets


def _split_clause_text(clause_text: str) -> Tuple[str, str]:
    if not clause_text:
        return "", ""
    match = _CLAUSE_LABEL_PATTERN.search(clause_text)
    if not match:
        bullet_match = _BULLET_LABEL_PATTERN.match(clause_text)
        if not bullet_match:
            return "", clause_text
        clause_label = (bullet_match.group(1) + bullet_match.group(2)).strip()
        remainder = clause_text[bullet_match.end() :].strip()
        return clause_label, remainder
    clause_label = match.group(1).strip()
    remainder = clause_text[match.end() :].strip()
    return clause_label, remainder


def _ensure_title_format(title: str) -> str:
    stripped = title.strip()
    if not stripped:
        return ""
    if stripped.startswith("《") and "》" in stripped:
        return stripped
    return f"《{stripped}》"


def _clean_label(label: str) -> str:
    stripped = label.strip()
    if stripped.startswith("引用"):
        stripped = stripped.replace("引用", "", 1).strip()
    return stripped or "1"


__all__ = [
    "ReferenceSnippet",
    "structured_search_payload",
    "extract_reference_snippets",
]
