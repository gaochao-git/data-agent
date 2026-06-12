from __future__ import annotations

import asyncio
from typing import Any, Dict

from apps.complaint_app.services.analysis_agent.agent import complaint_analysis

from ..base_tool import Tool, ToolContext


def _maybe_run(coro):
    """在没有事件循环时直接运行协程，否则返回协程交给调用方处理。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    return coro


def registry() -> Tool:
    """返回内置举报事项分析工具实例。"""
    description = (
        "举报事项分析工具（基于专家经验库）：识别举报事项并给出综合分析结论。"
        "source_text 应为摘要或用户选中内容；若无摘要需先生成摘要。"
        "不适用于“仅使用本地 laws/本地法律法规”类任务。"
    )

    def _handler(payload: Dict[str, Any], context: ToolContext):
        """执行举报事项分析。"""
        data = payload or {}
        query = str(data.get("query") or "").strip()
        source_text = str(data.get("source_text") or "").strip()
        if not query:
            raise ValueError("query is required")
        if not source_text:
            raise ValueError("source_text is required")

        result = _maybe_run(complaint_analysis(query, source_text))
        if asyncio.iscoroutine(result):
            return result

        return {
            "title": "complaint_analysis",
            "output": str(result),
            "metadata": {
                "query": query,
            },
        }

    return Tool(
        name="complaint_analysis",
        description=description,
        _executor=_handler,
        metadata={
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户问题",
                    },
                    "source_text": {
                        "type": "string",
                        "description": "摘要内容或用户选中的内容",
                    },
                },
                "required": ["query", "source_text"],
            }
        },
    )
