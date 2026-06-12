"""
信息简表智能体
"""

from utils.lang_util.conversation_router import register_agent, register_completion_handler
from .graph import get_graph
from .handlers import xxjb_completion_handler
from .nodes import TASKS

AGENT_ID = "report_xxjb"

register_agent(agent_id=AGENT_ID, get_graph=get_graph)
register_completion_handler(AGENT_ID, xxjb_completion_handler)
