"""Demonstrate the create_agent API with subagents."""

from __future__ import annotations

import asyncio

from .streaming_mode_agents import create_agent


async def main() -> None:
    legal_research_agent = create_agent(
        name="legal",
        description="法律检索子代理",
        system_prompt="负责处理检索结果的汇总与返回。",
        tools=["search_laws", "summarize_refs"],
    )
    agent = create_agent(
        name="main",
        description="支付纠纷主代理",
        system_prompt="判断是否需要检索并编排回复。",
        subagents=[legal_research_agent],
    )

    async for chunk in agent.stream():
        print(chunk)


if __name__ == "__main__":
    asyncio.run(main())
