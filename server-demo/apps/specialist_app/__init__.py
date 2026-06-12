"""Specialist app package."""

from utils.lang_util.conversation_router import register_agent

# 代理标识
AGENT_ID = "expert_agent"

# 注册 agent 配置（仅用于创建会话，发送消息仍使用 /api/ai_chat）
# 专家系统不使用 LangGraph 图
register_agent(agent_id=AGENT_ID)

__all__ = ['AGENT_ID']
