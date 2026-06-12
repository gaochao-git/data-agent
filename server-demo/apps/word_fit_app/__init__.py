"""
Word Fit App Module
"""
from utils.lang_util.conversation_router import register_agent, register_completion_handler
from .agent.graph import get_graph
from .handlers import word_fit_completion_handler
from .routes import router

AGENT_ID = "word_fit"

# 注册 Agent
register_agent(agent_id=AGENT_ID, get_graph=get_graph)
register_completion_handler(AGENT_ID, word_fit_completion_handler)

__all__ = ["AGENT_ID", "router"]
