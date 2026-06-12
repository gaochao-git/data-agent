"""Legal research sub-agent node that orchestrates the end-to-end workflow."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .search_node import SearchNodeRunner
from .summarization_node import SummarizationNodeRunner
from .references_node import ReferencesNodeRunner
from .stream_protocol import StreamProtocol

logger = logging.getLogger(__name__)


class LegalResearchSubAgentNodeRunner:
    """Run the planning → search → summarize → references workflow as a node."""

    NODE_ID = "legal_research_subagent"
    NODE_TYPE = "agent"
    NODE_TITLE = "法律检索子agent节点"

    def __init__(
        self,
        protocol: StreamProtocol,
        query: str,
    ) -> None:
        self.protocol = protocol
        self.query = query
        self.has_error = False

    async def stream(self) -> AsyncIterator[str]:
        try:
            search_runner = SearchNodeRunner(self.protocol, self.query)
            async for chunk in search_runner.stream():
                yield chunk
            search_payload = search_runner.search_payload
            reference_snippets = search_runner.reference_snippets
            summary_payload = search_runner.summary_payload

            summary_runner = SummarizationNodeRunner(
                self.protocol, self.query, summary_payload
            )
            async for chunk in summary_runner.stream():
                yield chunk

            references_runner = ReferencesNodeRunner(
                self.protocol, reference_snippets, search_payload
            )
            async for chunk in references_runner.stream():
                yield chunk

        except Exception as exc:  # pylint: disable=broad-except
            self.has_error = True
            logger.exception("legal research sub-agent pipeline failed")
            error_text = f"\n流程执行失败，请稍后重试。原因：{exc}\n"
            async for chunk in self.protocol.emit_text_delta(error_text, record_visible=False):
                if chunk:
                    yield chunk
            yield self.protocol.build_event(
                "error",
                include_message_id=False,
                code="workflow_error",
                message=str(exc),
                fatal=True,
            )
            yield self.protocol.build_event("done", include_message_id=False)


__all__ = ["LegalResearchSubAgentNodeRunner"]
