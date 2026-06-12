from __future__ import annotations

from typing import Any, Dict

from apps.complaint_app.opencode_runtime.tools.base_tool import Tool
from apps.complaint_app.services.complaint_analysis_v2.search_law_runner import search_law


async def _execute_search_law_tool(payload: Dict[str, Any], context) -> dict[str, Any]:
    sentence = str((payload or {}).get("sentence") or "").strip()
    trace_context = {}
    if context and isinstance(getattr(context, "metadata", None), dict):
        trace_context = dict(getattr(context, "metadata", None) or {})
    return await search_law(sentence, trace_context=trace_context)


def search_law_tool() -> Tool:
    return Tool(
        name="search_law",
        description="Search related laws and article content for a complaint issue sentence.",
        _executor=_execute_search_law_tool,
        metadata={
            "parallel_safe": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "sentence": {"type": "string", "description": "One complaint issue sentence to search related laws for."}
                },
                "required": ["sentence"],
            }
        },
    )
