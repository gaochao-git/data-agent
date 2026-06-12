"""
违规分析子图
包含拆分、并行检索、聚合、格式化等节点
符合 LangGraph 官方最佳实践：使用共享状态设计
"""

import time
from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langgraph.config import get_stream_writer
from langchain_core.messages import AIMessage

from .states import GraphState
from ..inspection_utils import parse_file_groups_to_violations, create_task_progress_block, create_analysis_data_block
from .task_manager import TaskManager
from .node_human_review import human_review_split_node, human_review_retrieve_node
from .retrieval_service import retrieval_service
from utils.logger import get_logger
from utils.exceptions import BusinessException

# 初始化日志
logger = get_logger(__name__)


# 加载环境变量
load_dotenv()

# ============ 任务配置 ============

VIOLATION_TASKS = [
    {"id": "parse", "title": "解析事实认定"},
    {"id": "review_parse", "title": "确认解析结果"},
    {"id": "retrieve", "title": "检索法律法规"},
    {"id": "review_retrieve", "title": "确认检索结果"},
    {"id": "format", "title": "格式化输出"}
]


# ============ LangGraph 节点定义 ============

def parse_violations_node(state: GraphState) -> dict:
    """解析节点 - 从用户输入构造违法行为（事实认定分析专用）"""
    logger.info("步骤1: 解析事实认定内容...")

    # 创建任务管理器
    task_manager = TaskManager(VIOLATION_TASKS)
    task_manager.set_in_progress("parse")

    try:
        # 从 state 中获取用户查询内容
        user_query = state.get("user_query", "")
        if not user_query:
            # 尝试从 messages 中提取
            messages = state.get("messages", [])
            if messages:
                user_query = messages[-1].content

        if not user_query:
            raise BusinessException(msg="请输入事实认定内容", code=40052)

        # 构造违法行为对象
        from .states import ViolationItem, SplitResult
        violation = ViolationItem(
            violation_id=1,
            original_fact="事实认定分析",
            ai_expansion=user_query
        )
        result = SplitResult(violations=[violation])
        logger.info("成功解析事实认定内容")

        # 标记任务完成，获取进度更新
        progress_update = task_manager.set_completed("parse")

        # 返回解析结果
        return_dict = {
            "vio_split_result": result,
            **progress_update  # 持久化任务进度
        }

        return return_dict

    except Exception as e:
        error_msg = f"解析失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        # 发送错误事件
        writer = get_stream_writer()
        writer({
            "type": "error",
            "error": error_msg,
            "step": "parse"
        })
        raise BusinessException(msg=error_msg, code=50002)


def legal_basis_retrieval_node(state: GraphState) -> dict:
    """检索节点1 - 检索违反条款（处理单个违法行为）"""
    violation_id = state.get("current_violation_id")
    violation_summary = state.get("current_violation_summary")

    # 只在第一个检索任务时发送进度更新（避免并行任务重复发送）
    # 注：并行检索节点不负责持久化进度，由 aggregate 节点统一处理
    split_result = state.get("vio_split_result")
    if split_result and split_result.violations and violation_id == split_result.violations[0].violation_id:
        task_manager = TaskManager(VIOLATION_TASKS)
        task_manager.set_in_progress("retrieve")

    try:
        data = retrieval_service.retrieve_legal_basis(violation_summary)
        return {
            "vio_retrieval_results": [{
                "violation_id": violation_id,
                "type": "legal_basis",
                "data": data
            }]
        }
    except Exception as e:
        error_msg = f"违反条款检索失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50011)


def penalty_clause_retrieval_node(state: GraphState) -> dict:
    """检索节点2 - 检索处罚条款（处理单个违法行为）"""
    violation_id = state.get("current_violation_id")
    violation_summary = state.get("current_violation_summary")

    try:
        data = retrieval_service.retrieve_penalty_clause(violation_summary)
        return {
            "vio_retrieval_results": [{
                "violation_id": violation_id,
                "type": "penalty_clause",
                "data": data
            }]
        }
    except Exception as e:
        error_msg = f"处罚条款检索失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50012)


def discretion_standard_retrieval_node(state: GraphState) -> dict:
    """检索节点3 - 检索裁量基准（处理单个违法行为）"""
    violation_id = state.get("current_violation_id")
    violation_summary = state.get("current_violation_summary")

    try:
        data = retrieval_service.retrieve_discretion_standard(violation_summary)
        return {
            "vio_retrieval_results": [{
                "violation_id": violation_id,
                "type": "discretion_standard",
                "data": data
            }]
        }
    except Exception as e:
        error_msg = f"裁量基准检索失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50013)


def rectification_measures_retrieval_node(state: GraphState) -> dict:
    """检索节点4 - 检索整改措施（处理单个违法行为）"""
    violation_id = state.get("current_violation_id")
    violation_summary = state.get("current_violation_summary")

    try:
        data = retrieval_service.retrieve_rectification_measures(violation_summary)
        return {
            "vio_retrieval_results": [{
                "violation_id": violation_id,
                "type": "rectification_measures",
                "data": data
            }]
        }
    except Exception as e:
        error_msg = f"整改措施检索失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50014)


def penalty_suggestion_retrieval_node(state: GraphState) -> dict:
    """检索节点5 - 检索处罚建议（处理单个违法行为）"""
    violation_id = state.get("current_violation_id")
    violation_summary = state.get("current_violation_summary")

    try:
        data = retrieval_service.retrieve_penalty_suggestion(violation_summary)
        return {
            "vio_retrieval_results": [{
                "violation_id": violation_id,
                "type": "penalty_suggestion",
                "data": data
            }]
        }
    except Exception as e:
        error_msg = f"处罚建议检索失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50015)


def continue_to_retrievals(state: GraphState):
    """
    分发函数 - 为每个违法行为创建并行检索任务
    返回 Send 对象列表，实现动态并行
    """
    split_data = state["vio_split_result"]  # 直接使用 SplitResult 对象，不需要 JSON 解析

    logger.info(f"为 {len(split_data.violations)} 个违法行为创建 {len(split_data.violations) * 5} 个并行检索任务...")

    sends = []
    for violation in split_data.violations:
        # 为每个违法行为创建5个并行检索任务
        # 每个 Send 传递包含违法行为信息的状态
        violation_state = {
            **state,
            "current_violation_id": violation.violation_id,
            "current_violation_summary": violation.ai_expansion,  # 使用完整的扩充表述用于检索
            "current_original_fact": violation.original_fact  # 保留核心认定用于溯源
        }

        sends.append(Send("legal_basis", violation_state))
        sends.append(Send("penalty_clause", violation_state))
        sends.append(Send("discretion_standard", violation_state))
        sends.append(Send("rectification", violation_state))
        sends.append(Send("penalty_suggestion", violation_state))

    return sends


def aggregate_results_node(state: GraphState) -> dict:
    """聚合节点 - 等待所有并行检索完成"""
    logger.info(f"聚合所有检索结果，共 {len(state.get('vio_retrieval_results', []))} 条...")

    task_manager = TaskManager(VIOLATION_TASKS)
    progress_update = task_manager.set_completed("retrieve")

    # 这个节点只是作为同步点，不需要做任何处理
    # 所有并行任务的结果已经通过 operator.add 自动合并到 vio_retrieval_results 中
    # 持久化进度状态
    return progress_update


def format_output_node(state: GraphState) -> dict:
    """格式化输出节点 - 从并行检索结果组装输出"""
    logger.info("步骤3: 整理输出结果...")

    task_manager = TaskManager(VIOLATION_TASKS)
    task_manager.set_in_progress("format")

    try:
        # 直接使用 SplitResult 对象
        split_data = state["vio_split_result"]

        # 按 violation_id 和 type 重新组织检索结果
        results_by_violation = {}
        for result in state.get("vio_retrieval_results", []):
            vid = result["violation_id"]
            result_type = result["type"]

            if vid not in results_by_violation:
                results_by_violation[vid] = {}

            results_by_violation[vid][result_type] = result["data"]

        # 为每个违法行为组装结果
        violations_results = []

        for violation in split_data.violations:
            vid = violation.violation_id
            violation_data = results_by_violation.get(vid, {})

            # 格式化违反条款
            legal_basis = []
            for item in violation_data.get("legal_basis", []):
                legal_basis.append({
                    "law_name": item.get("law_name", ""),
                    "law_item_name": item.get("law_item_name", ""),
                    "law_item_content": item.get("law_item_content", "")
                })

            # 格式化处罚条款
            penalty_clauses = []
            for item in violation_data.get("penalty_clause", []):
                penalty_clauses.append({
                    "law_name": item.get("law_name", ""),
                    "law_item_name": item.get("law_item_name", ""),
                    "law_item_content": item.get("law_item_content", "")
                })

            violation_result = {
                '违法行为编号': violation.violation_id,
                '原始事实认定': violation.original_fact,
                'AI扩充': violation.ai_expansion,
                '违反条款': legal_basis,
                '处罚条款': penalty_clauses,
                '裁量基准': violation_data.get("discretion_standard", []),
                '处罚建议': violation_data.get("penalty_suggestion", []),
                '整改措施': violation_data.get("rectification_measures", [])
            }
            violations_results.append(violation_result)

        final_data = {
            '违法行为数量': len(violations_results),
            '违法行为分析': violations_results
        }

        # 标记任务完成，获取进度更新
        progress_update = task_manager.set_completed("format")

        # 构建最终任务进度列表（所有任务已完成）
        final_tasks = [{**task, "status": "completed"} for task in VIOLATION_TASKS]

        # 返回标准 AIMessage（符合 LangChain 官方规范）
        # 使用 NonStandardContentBlock 存放自定义数据
        return {
            "messages": [
                AIMessage(
                    content=[
                        # 任务进度（NonStandardContentBlock）
                        create_task_progress_block(final_tasks),
                        # 分析数据（NonStandardContentBlock）
                        create_analysis_data_block(final_data),
                    ]
                )
            ],
            **progress_update  # 持久化任务进度到 state
        }

    except Exception as e:
        error_msg = f"格式化失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50003)


# ============ 构建子图（用于主图集成） ============

def create_violation_subgraph():
    """
    创建违规分析子图（符合 LangGraph 官方推荐的共享状态设计）

    使用 GraphState 共享状态，无需适配器。
    子图直接读写它需要的字段（user_query, split_result, retrieval_results, final_result）。

    工作流程（包含 Human-in-the-Loop）：
    split → review_split（人工确认）→ [并行检索] → aggregate → review_retrieve（人工确认）→ format

    参考：https://docs.langchain.com/oss/python/langgraph/use-subgraphs
    """
    # 构建子图（直接使用 GraphState，无需适配）
    subgraph = StateGraph(GraphState)

    # 添加节点（直接使用原始节点函数，无需适配）
    subgraph.add_node("parse", parse_violations_node)
    subgraph.add_node("review_parse", human_review_split_node)  # 人工审核：解析结果
    subgraph.add_node("legal_basis", legal_basis_retrieval_node)
    subgraph.add_node("penalty_clause", penalty_clause_retrieval_node)
    subgraph.add_node("discretion_standard", discretion_standard_retrieval_node)
    subgraph.add_node("rectification", rectification_measures_retrieval_node)
    subgraph.add_node("penalty_suggestion", penalty_suggestion_retrieval_node)
    subgraph.add_node("aggregate", aggregate_results_node)
    subgraph.add_node("review_retrieve", human_review_retrieve_node)  # 人工审核：检索结果
    subgraph.add_node("format", format_output_node)

    # 定义工作流
    subgraph.set_entry_point("parse")

    # 解析后进入人工审核
    subgraph.add_edge("parse", "review_parse")

    # 审核通过后分发到并行检索
    subgraph.add_conditional_edges(
        "review_parse",
        continue_to_retrievals,
        ["legal_basis", "penalty_clause", "discretion_standard", "rectification", "penalty_suggestion"]
    )

    # 所有检索节点汇聚到聚合节点
    subgraph.add_edge("legal_basis", "aggregate")
    subgraph.add_edge("penalty_clause", "aggregate")
    subgraph.add_edge("discretion_standard", "aggregate")
    subgraph.add_edge("rectification", "aggregate")
    subgraph.add_edge("penalty_suggestion", "aggregate")

    # 聚合后进入人工审核
    subgraph.add_edge("aggregate", "review_retrieve")

    # 审核通过后格式化输出
    subgraph.add_edge("review_retrieve", "format")
    subgraph.add_edge("format", END)

    return subgraph.compile()
