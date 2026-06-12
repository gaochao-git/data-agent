"""
事实认定分析智能体 - completion handler
"""

from ..shared.handlers import create_completion_handler
from .graph import get_graph
from .state import FactAnalysisState

fact_analysis_completion_handler = create_completion_handler(
    agent_name="事实认定分析",
    get_graph=get_graph,
    state_class=FactAnalysisState,
    default_report_type=""
)
