"""
事实认定分析智能体

功能：分析用户输入的违法事实，检索相关法律依据
输入：文本（违法事实描述）
输出：法律依据分析结果
"""

from utils.lang_util.conversation_router import register_agent, register_completion_handler

from .graph import get_graph
from .handlers import fact_analysis_completion_handler

AGENT_ID = "fact_analysis"

# 注册智能体
register_agent(agent_id=AGENT_ID, get_graph=get_graph)
register_completion_handler(AGENT_ID, fact_analysis_completion_handler)
