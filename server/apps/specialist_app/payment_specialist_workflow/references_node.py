"""Node for rendering references to the stream."""

from __future__ import annotations

import logging
from typing import Iterable

from .references import ReferenceSnippet
from .stream_protocol import StreamProtocol

logger = logging.getLogger(__name__)


class ReferencesNodeRunner:
    """Stream law references (or fallback payload) to the client."""

    def __init__(
        self,
        protocol: StreamProtocol,
        reference_snippets: list[ReferenceSnippet] | None,
        search_payload: str,
    ) -> None:
        self.protocol = protocol
        self.reference_snippets = reference_snippets or []
        self.search_payload = search_payload

    async def stream(self) -> Iterable[str]:
        protocol = self.protocol

        if self.reference_snippets:
            valid_snippets: list[ReferenceSnippet] = []
            for snippet in self.reference_snippets:
                label = (snippet.label or "").strip()
                title = (snippet.title or "").strip()
                clause = (snippet.clause or "").strip()
                doc_id = (snippet.doc_id or "").strip()
                content = (snippet.content or "").strip()
                if not label or not title or not clause or not doc_id:
                    logger.warning(
                        "Skipping incomplete reference in stream: label=%r title=%r clause=%r doc_id=%r",
                        snippet.label,
                        snippet.title,
                        snippet.clause,
                        snippet.doc_id,
                    )
                    continue
                valid_snippets.append(
                    ReferenceSnippet(
                        label=label,
                        title=title,
                        clause=clause,
                        content=content or clause,
                        doc_id=doc_id,
                    )
                )

            if valid_snippets:
                references_payload = [
                    {
                        "id": snippet.doc_id,
                        "title": f"{snippet.title} · {snippet.clause}",
                        "summary": snippet.content,
                        "sourceId": snippet.doc_id,
                        "anchorId": snippet.doc_id,
                        "clause": snippet.clause,
                        "description": snippet.content,
                        "displayLabel": f"引用{snippet.label}",
                    }
                    for snippet in valid_snippets
                ]
                yield protocol.build_event(
                    "references",
                    references=references_payload,
                )
        elif self.search_payload:
            # Fallback: emit raw search payload as a single reference note.
            fallback_payload = [
                {
                    "id": "raw_search_payload",
                    "title": "检索到的法律条款",
                    "summary": (self.search_payload or "").strip(),
                }
            ]
            yield protocol.build_event(
                "references",
                references=fallback_payload,
            )


__all__ = ["ReferencesNodeRunner"]
