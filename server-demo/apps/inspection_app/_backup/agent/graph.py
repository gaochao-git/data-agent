"""
主图编排（异步版本）
整合违规分析和问答功能，使用子图实现不同的工作流
支持共享状态，便于追问和上下文管理
"""

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langchain_core.messages import AIMessage
from .states import GraphState
from .subgraph_fact_analysis import create_fact_analysis_subgraph  # ⭐ 新的事实认定分析子图
from .subgraph_qa import create_qa_subgraph
from .subgraph_report import (
    create_rectification_report_subgraph,
    create_info_summary_subgraph,
    create_inspection_report_subgraph,
    create_evidence_catalog_subgraph,
)
from ..inspection_utils import Task
from utils.logger import get_logger
from utils.exceptions import BusinessException
from utils.lang_util.checkpoint import DB_PATH, get_checkpointer

logger = get_logger(__name__)
load_dotenv()


# ============ 主图节点 ============

def prepare_state_node(state: GraphState) -> dict:
    logger.debug("准备状态...")

    if not state.get("messages"):
        raise BusinessException(msg="状态中没有消息", code=40002)

    last_message = state["messages"][-1]
    message_content = last_message.content

    task_dict = last_message.additional_kwargs.get("task", {})
    task = Task.from_dict(task_dict)

    if task.task_type == "violation_analysis":
        return {"user_query": message_content}
    elif task.task_type == "report":
        return {"user_query": message_content}
    else:
        return {}


def state_update_node(state: GraphState) -> dict:
    """
    专门用于外部状态更新的虚拟节点
    不做任何处理，直接返回空字典，让传入的状态直接写入
    """
    logger.debug("执行状态更新...")
    return {}


def router_node(state: GraphState) -> str:
    if not state.get("messages"):
        return "qa_subgraph"

    last_message = state["messages"][-1]
    task_dict = last_message.additional_kwargs.get("task", {})
    report_type = task_dict.get("report_type", "")
    task = Task.from_dict(task_dict)

    logger.info(f"路由决策: 任务类型={task.task_type}, report_type={report_type}")

    if task.task_type == "violation_analysis":
        # ⭐ 事实认定分析功能（用户输入文本 → 6项检索 → 返回分析结果）
        return "fact_analysis_subgraph"
    elif task.task_type == "report":
        if report_type == "zgtzs":
            return "report_zgtzs_subgraph"
        elif report_type == "xxjb":
            return "report_jb_subgraph"
        elif report_type == "jcbg":
            return "report_jcbg_subgraph"
        elif report_type == "zjml":
            return "report_zjml_subgraph"
        else:
            return "qa_subgraph"
    else:
        return "qa_subgraph"


# aggregate_messages_node 已删除
# 按照 LangChain 官方规范，子图直接返回 AIMessage，不需要额外的聚合节点


# ============ 主图构建 ============

def _build_workflow():
    """构建工作流（模块加载时执行一次）"""
    workflow = StateGraph(GraphState)

    # 添加节点
    workflow.add_node("prepare_state_node", prepare_state_node)
    workflow.add_node("state_update_node", state_update_node)  # 虚拟节点，用于外部状态更新
    workflow.add_node("fact_analysis_subgraph", create_fact_analysis_subgraph())  # ⭐ 新的事实认定分析
    workflow.add_node("qa_subgraph", create_qa_subgraph())
    workflow.add_node("report_zgtzs_subgraph", create_rectification_report_subgraph())
    workflow.add_node("report_jb_subgraph", create_info_summary_subgraph())
    workflow.add_node("report_jcbg_subgraph", create_inspection_report_subgraph())
    workflow.add_node("report_zjml_subgraph", create_evidence_catalog_subgraph())

    # 设置入口点和路由
    workflow.set_entry_point("prepare_state_node")
    workflow.add_conditional_edges(
        "prepare_state_node",
        router_node,
        {
            "fact_analysis_subgraph": "fact_analysis_subgraph",  # ⭐ 事实认定分析
            "qa_subgraph": "qa_subgraph",
            "report_zgtzs_subgraph": "report_zgtzs_subgraph",
            "report_jb_subgraph": "report_jb_subgraph",
            "report_jcbg_subgraph": "report_jcbg_subgraph",
            "report_zjml_subgraph": "report_zjml_subgraph"
        }
    )

    # 按照 LangChain 官方规范：子图直接连到 END
    workflow.add_edge("fact_analysis_subgraph", END)  # ⭐ 事实认定分析
    workflow.add_edge("qa_subgraph", END)
    workflow.add_edge("report_zgtzs_subgraph", END)
    workflow.add_edge("report_jb_subgraph", END)
    workflow.add_edge("report_jcbg_subgraph", END)
    workflow.add_edge("report_zjml_subgraph", END)

    return workflow


# 模块加载时构建一次
_workflow = _build_workflow()


def get_graph(checkpointer=None):
    """
    获取编译好的图

    Args:
        checkpointer: 可选的 checkpointer，如果不传则不使用持久化
    """
    return _workflow.compile(checkpointer=checkpointer)


# get_checkpointer 和 DB_PATH 从 utils.conversation 导入并重新导出
__all__ = ['get_graph', 'get_checkpointer', 'DB_PATH']
