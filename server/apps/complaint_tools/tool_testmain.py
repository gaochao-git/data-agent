from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, List, Optional

from langchain.agents import create_agent

from .case_analysis_llm import get_llm_for_case_analysis
from .case_analysis_tool_analyze_v2 import analyze_case_items_tool
from .case_analysis_tool_identify_v2 import identify_case_items_tool


def get_case_analysis_agent():
    """
    构建案例分析主 Agent。
    - 使用 create_agent 创建统一的调用入口；
    - tools 使用 identify_case_items_tool / analyze_case_items_tool。
    """
    llm = get_llm_for_case_analysis(streaming=True)
    system_prompt = """
你是一名金融投诉与案例分析助手，擅长：
- 从长文本中识别清晰的“案例事项”列表；
- 在需要时调用工具完成结构化抽取与进一步分析；
- 回答时保持中文、简洁、专业。
    """.strip()

    agent = create_agent(
        llm,
        tools=[identify_case_items_tool, analyze_case_items_tool],
        system_prompt=system_prompt,
    )
    return agent


async def astream_case_analysis(
    query: str,
    *,
    stream_mode: Optional[List[str]] = None,
) -> AsyncIterator[Any]:
    """
    案例分析 Agent 的简单流式封装。
    - 默认同时输出 messages 和 custom（SSE）两类流事件；
    - 上层可直接在 FastAPI StreamingResponse 中消费。
    """
    if stream_mode is None:
        stream_mode = ["messages", "custom"]

    agent = get_case_analysis_agent()
    async for chunk in agent.astream(
        {"messages": [{"role": "user", "content": query}]},
        stream_mode=stream_mode,
    ):
        yield chunk


def _sample_text() -> str:
    return """
投诉人反映：2023 年 5 月，其通过我行手机银行向外行转账 5 万元，
界面提示“交易失败”，但对方账户实际收到款项，且投诉人账户流水显示扣款成功。
投诉人认为系统提示存在误导，要求退还手续费并说明原因。
    """.strip()


def test_case_analysis_sync() -> None:
    """非流式测试：直接调用案例分析主 Agent，一次性拿完整结果。"""
    agent = get_case_analysis_agent()
    text = _sample_text()

    result: Any = agent.invoke({"messages": [{"role": "user", "content": text}]})

    print("=== 非流式结果 ===")
    print(result)


async def _test_case_analysis_stream_async() -> None:
    """流式测试：同时打印 LLM 消息流和工具的 SSE 自定义事件。"""
    text = _sample_text()

    print("=== 流式输出开始 ===")
    async for chunk in astream_case_analysis(
        query=text,
        stream_mode=["messages", "custom"],
    ):
        print("---- chunk ----")
        print(chunk)


def test_case_analysis_stream() -> None:
    """同步入口，用于在命令行中运行流式测试。"""
    asyncio.run(_test_case_analysis_stream_async())


__all__ = [
    "identify_case_items",
    "analyze_case_items",
    "get_case_analysis_agent",
    "astream_case_analysis",
    "test_case_analysis_sync",
    "test_case_analysis_stream",
]


if __name__ == "__main__":
    # 默认先跑一次非流式，再跑一次流式，方便本地调试。
    test_case_analysis_sync()
    print("\n\n")
    # test_case_analysis_stream()

