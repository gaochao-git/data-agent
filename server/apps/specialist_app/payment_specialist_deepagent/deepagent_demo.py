import os
from typing import Literal
from deepagents import create_deep_agent


def legal_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search via remote A2A agent"""
    payload = {
        "input": {
            "query": query,
            "max_results": max_results,
            "topic": topic,
            "include_raw_content": include_raw_content,
        }
    }
    # 这里换成你自己的 A2A Agent URL
    resp = httpx.post("http://localhost:10000/agent", json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    # 你按自己的格式挑一个合适的字段返回
    return json.dumps(data, ensure_ascii=False)

# System prompt to steer the agent to be an expert researcher
research_instructions = """You are an expert researcher. Your job is to conduct thorough research and then write a polished report.

You have access to an internet search tool as your primary means of gathering information.

## `internet_search`

Use this to run an internet search for a given query. You can specify the max number of results to return, the topic, and whether raw content should be included.
"""

agent = create_deep_agent(
    tools=[legal_search],
    system_prompt=research_instructions
)

result = agent.invoke({"messages": [{"role": "user", "content": "What is langgraph?"}]})

# Print the agent's response
print(result["messages"][-1].content)
