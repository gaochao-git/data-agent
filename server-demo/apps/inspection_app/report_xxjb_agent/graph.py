"""
信息简表智能体图定义
"""

from langgraph.graph import StateGraph, START, END
from .state import XxJbState
from .nodes import check_deps_node, generate_node, router_node, router_conditional, qa_node


def state_update_node(state: XxJbState) -> XxJbState:
    """虚拟节点，用于外部状态更新（如文件上传）"""
    return state


def get_graph(checkpointer=None):
    builder = StateGraph(XxJbState)
    builder.add_node("state_update_node", state_update_node)
    builder.add_node("router", router_node)
    builder.add_node("qa", qa_node)
    builder.add_node("check_deps", check_deps_node)
    builder.add_node("generate", generate_node)

    builder.add_edge(START, "router")
    builder.add_conditional_edges("router", router_conditional, {"check_deps": "check_deps", "qa": "qa"})
    builder.add_edge("check_deps", "generate")
    builder.add_edge("generate", END)
    builder.add_edge("qa", END)

    return builder.compile(checkpointer=checkpointer)
