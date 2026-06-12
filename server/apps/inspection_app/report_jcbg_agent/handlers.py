"""
检查报告智能体 - completion handler
"""

from ..shared.handlers import create_completion_handler
from .graph import get_graph
from .state import JcbgState

jcbg_completion_handler = create_completion_handler(
    agent_name="检查报告",
    get_graph=get_graph,
    state_class=JcbgState,
    default_report_type="jcbg"
)
