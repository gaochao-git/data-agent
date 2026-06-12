from __future__ import annotations

from typing import Any, Dict, List

from .agent import complaint_analysis


TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "complaint_analysis",
            "description": "举报事项分析工具：识别举报事项并给出综合分析结论。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "用户问题"},
                    "source_text": {"type": "string", "description": "摘要内容或用户选中的内容"},
                },
                "required": ["query", "source_text"],
            },
        },
    }
]


def complaint_analysis_tool(arguments: Dict[str, Any]):
    return complaint_analysis(str(arguments.get("query") or ""), str(arguments.get("source_text") or ""))


FUNCTION_MAPPER = {"complaint_analysis": complaint_analysis_tool}
