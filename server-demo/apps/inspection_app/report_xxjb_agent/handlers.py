"""
信息简表智能体 - completion handler
"""

from ..shared.handlers import create_completion_handler
from .graph import get_graph
from .state import XxJbState

xxjb_completion_handler = create_completion_handler(
    agent_name="信息简表",
    get_graph=get_graph,
    state_class=XxJbState,
    default_report_type="xxjb"
)
