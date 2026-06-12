"""
通用助手应用模块
"""
from .agent import get_graph
from .handlers import general_completion_handler
from utils.lang_util.conversation_router import register_agent, register_completion_handler

# 代理标识
AGENT_ID = "general"

# 注册 agent 配置和 completion handler
register_agent(agent_id=AGENT_ID, get_graph=get_graph)
register_completion_handler(AGENT_ID, general_completion_handler)

__all__ = ['AGENT_ID']
