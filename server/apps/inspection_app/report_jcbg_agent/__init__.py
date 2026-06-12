"""
检查报告智能体

功能：根据事实认定书生成检查报告 Word 文档
输入：事实认定书文件（violation_file_groups）
输出：检查报告 Word 文档
"""

from utils.lang_util.conversation_router import register_agent, register_completion_handler

from .graph import get_graph
from .handlers import jcbg_completion_handler
from .nodes import TASKS

AGENT_ID = "report_jcbg"

# 注册智能体
register_agent(agent_id=AGENT_ID, get_graph=get_graph)
register_completion_handler(AGENT_ID, jcbg_completion_handler)
