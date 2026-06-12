"""
证据目录智能体图定义
"""

from langgraph.graph import StateGraph, START, END
from .state import ZjMlState
from .nodes import check_deps_node, review_retrieve_node, generate_node, router_node, router_conditional, qa_node


def state_update_node(state: ZjMlState) -> ZjMlState:
    """虚拟节点，用于外部状态更新（如文件上传）"""
    return state


def get_graph(checkpointer=None):
    builder = StateGraph(ZjMlState)
    builder.add_node("state_update_node", state_update_node)
    builder.add_node("router", router_node)
    builder.add_node("qa", qa_node)
    builder.add_node("check_deps", check_deps_node)
    builder.add_node("review_retrieve", review_retrieve_node)
    builder.add_node("generate", generate_node)

    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", router_conditional, {"check_deps": "check_deps", "qa": "qa"})
    builder.add_edge("check_deps", "review_retrieve")
    builder.add_edge("review_retrieve", "generate")
    builder.add_edge("generate", END)
    builder.add_edge("qa", END)

    return builder.compile(checkpointer=checkpointer)
