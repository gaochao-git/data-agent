"""
事实认定分析智能体图定义
"""

from langgraph.graph import StateGraph, START, END

from .state import FactAnalysisState
from .nodes import (
    prepare_node,
    retrieve_node,
    format_node,
    router_node,
    router_conditional,
    qa_node,
)


def get_graph(checkpointer=None):
    """
    构建事实认定分析图

    流程:
    START → router → (violation_analysis) → prepare → retrieve → format → END
                   → (qa) → qa → END
    """
    builder = StateGraph(FactAnalysisState)

    # 添加节点
    builder.add_node("router", router_node)
    builder.add_node("qa", qa_node)
    builder.add_node("prepare", prepare_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("format", format_node)

    # 添加边
    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        router_conditional,
        {"prepare": "prepare", "qa": "qa"}
    )
    builder.add_edge("prepare", "retrieve")
    builder.add_edge("retrieve", "format")
    builder.add_edge("format", END)
    builder.add_edge("qa", END)

    return builder.compile(checkpointer=checkpointer)
