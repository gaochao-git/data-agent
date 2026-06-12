"""
整改通知书智能体 - completion handler
"""

from ..shared.handlers import create_completion_handler
from .graph import get_graph
from .state import ZgTzsState

zgtzs_completion_handler = create_completion_handler(
    agent_name="整改通知书",
    get_graph=get_graph,
    state_class=ZgTzsState,
    default_report_type="zgtzs"
)
