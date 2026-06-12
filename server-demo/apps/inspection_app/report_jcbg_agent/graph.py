"""
检查报告智能体图定义
"""

from langgraph.graph import StateGraph, START, END

from .state import JcbgState
from .nodes import (
    check_deps_node,
    retrieve_node,
    review_retrieve_node,
    generate_node,
    router_node,
    router_conditional,
    qa_node,
)


def state_update_node(state: JcbgState) -> JcbgState:
    """虚拟节点，用于外部状态更新（如文件上传）"""
    return state


def get_graph(checkpointer=None):
    """
    构建检查报告图

    流程:
    - router → (report) → check_deps → retrieve → review_retrieve → generate → END
    - router → (qa) → qa → END
    """
    builder = StateGraph(JcbgState)

    # 添加节点
    builder.add_node("state_update_node", state_update_node)  # 虚拟节点，用于外部状态更新
    builder.add_node("router", router_node)
    builder.add_node("qa", qa_node)
    builder.add_node("check_deps", check_deps_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("review_retrieve", review_retrieve_node)
    builder.add_node("generate", generate_node)

    # 添加边 - 路由分支
    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        router_conditional,
        {
            "check_deps": "check_deps",
            "qa": "qa"
        }
    )

    # 报告生成流程
    builder.add_edge("check_deps", "retrieve")
    builder.add_edge("retrieve", "review_retrieve")
    builder.add_edge("review_retrieve", "generate")
    builder.add_edge("generate", END)

    # QA 流程
    builder.add_edge("qa", END)

    return builder.compile(checkpointer=checkpointer)
