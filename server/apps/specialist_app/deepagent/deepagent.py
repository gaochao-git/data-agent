import os

from deepagents import create_deep_agent

from langchain.chat_models import init_chat_model


kwargs = {
    "model": "qwen3-30b-a3b-instruct-2507",
    "model_provider": os.getenv("LLM_PROVIDER", "openai"),
    "api_key": "sk-k0RxkCPfCFDReahD",
    "base_url": "http://39.97.229.91:8081/v1"
}

model = init_chat_model(**kwargs)


def legal_runner(query):
    """法律检索子智能体，用于检索法律相关内容"""
    print("=====111===")
    return f"法律检索子智能体，用于检索法律相关内容，查询：{query}"


legal_research_subagent = {
    "name": "legal_research_subagent",
    "description": "从标准库里检索法律子智能体，用于检索法律相关内容",
    "system_prompt": "你是一个法律检索子智能体，用于检索法律相关内容",
    "tools": [legal_runner],
    "model": model,
}
subagents = [legal_research_subagent]


agent = create_deep_agent(
    system_prompt="回答用户问题时，一定要用子智能体",
    model=model,
    subagents=subagents
)

# result = agent.invoke({"messages":[{"role":"user","content":"你好，1+1等于几"}]})
# print(result["messages"][-1].content)
# result = agent.invoke({"messages":[{"role":"user","content":"你好，刚才问了什么问题"}]})
# print(result["messages"][-1].content)
result = agent.invoke({"messages":[{"role":"user","content":"你能否从标准库里检索一下有没有反洗钱法"}]})
print(result["messages"][-1].content)