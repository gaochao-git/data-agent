"""
证据目录智能体 - completion handler
"""

from ..shared.handlers import create_completion_handler
from .graph import get_graph
from .state import ZjMlState

zjml_completion_handler = create_completion_handler(
    agent_name="证据目录",
    get_graph=get_graph,
    state_class=ZjMlState,
    default_report_type="zjml"
)
