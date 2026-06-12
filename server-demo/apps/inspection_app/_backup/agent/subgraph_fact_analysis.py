"""
事实认定分析子图（新架构）

用户输入违法事实文本 → 6项法律检索 → 返回分析结果

特点：
- 复用现有的检索节点和数据结构
- 包含历史案例参考
- 轻量级实现（3个节点）
"""

from langgraph.graph import StateGraph, END
from langgraph.types import RunnableConfig
from langchain_core.messages import AIMessage

from .states import GraphState
from .node_common import create_retrieve_legal_basis_node
from .task_manager import TaskManager
from ..inspection_utils import create_task_progress_block, create_analysis_data_block, create_progress_message
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


# ============ 任务配置 ============

FACT_ANALYSIS_TASKS = [
    {"id": "prepare", "title": "准备分析数据"},
    {"id": "retrieve", "title": "检索法律依据"},
    {"id": "format", "title": "生成分析结果"}
]


# ============ 节点定义 ============

def prepare_fact_analysis_node(state: GraphState) -> dict:
    """
    准备节点 - 从用户输入构造临时的 violation_file_groups

    将用户输入的文本包装成标准的 violation_file_groups 格式，
    以便复用现有的检索节点
    """
    logger.info("准备事实认定分析...")

    task_mgr = TaskManager(FACT_ANALYSIS_TASKS)
    task_mgr.set_in_progress("prepare")

    # 从 user_query 获取用户输入的违法事实
    user_query = state.get("user_query", "")
    if not user_query:
        # 尝试从 messages 中提取
        messages = state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            user_query = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)

    if not user_query or not user_query.strip():
        raise BusinessException(msg="请输入违法事实内容", code=40001)

    logger.info(f"接收到用户输入，长度: {len(user_query)} 字符")

    # 构造临时的 violation_file_groups（模拟文件上传的数据结构）
    # 这样可以复用现有的检索节点，无需修改
    temp_file_groups = [
        {
            "group_id": "temp_fact_analysis",
            "fact_determination": {
                "file_id": "temp_input",
                "filename": "用户输入.txt",
                "upload_time": None,
                "parsed_data": {
                    "inspected_person": "待分析",
                    "inspection_content": "违法事实分析",
                    "violation_facts": user_query  # ⭐ 核心：用户输入的文本
                }
            },
            "evidence_records": None,
            "created_at": None,
            "order": 1
        }
    ]

    task_mgr.set_completed("prepare")
    logger.info("数据准备完成，已构造临时文件分组")

    progress_msg = create_progress_message(task_mgr._build_task_list("prepare", 'completed'))
    return {
        "violation_file_groups": temp_file_groups,
        "messages": [progress_msg]
    }


def format_fact_analysis_node(state: GraphState, config: RunnableConfig) -> dict:
    """
    格式化输出节点 - 将 violations_analysis_result 格式化为用户友好的输出
    """
    logger.info("格式化事实认定分析结果...")

    task_mgr = TaskManager(FACT_ANALYSIS_TASKS)
    task_mgr.set_in_progress("format")

    # 从标准数据结构获取结果
    violations_analysis_result = state.get("violations_analysis_result")

    if not violations_analysis_result or not violations_analysis_result.violations:
        raise BusinessException(msg="分析结果为空", code=50001)

    # 只取第一个违法行为（事实认定分析只有1个）
    violation = violations_analysis_result.violations[0]

    # 构建符合前端数据映射器期望的分析数据（英文字段）
    # 前端的 mapViolationsAnalysisResult 会将英文字段转换为中文字段
    analysis_data = {
        "inspected_person": "用户输入",  # 事实认定分析没有被检查人信息
        "violations": [
            {
                "violation_id": 1,
                "facts": violation.title,  # 原始事实认定（映射器期望从 facts 读取）
                "title": violation.facts,  # AI扩充内容（映射器期望从 title 读取）
                "violated_provisions": [
                    {
                        "law_name": p.law_name,
                        "law_item_name": p.law_item_name,
                        "law_item_content": p.law_item_content,
                    }
                    for p in violation.violated_provisions
                ],
                "penalty_provisions": [
                    {
                        "law_name": p.law_name,
                        "law_item_name": p.law_item_name,
                        "law_item_content": p.law_item_content,
                    }
                    for p in violation.penalty_provisions
                ],
                "discretionary_benchmarks": violation.discretionary_benchmarks,
                "rectification_measures": violation.rectification_measures,
                "penalty_recommendations": violation.penalty_recommendations,
                "historical_cases": [
                    {
                        "case_id": case.case_id,
                        "case_title": case.case_title,
                        "case_summary": case.case_summary,
                        "violation_facts": case.violation_facts,
                        "penalty_result": case.penalty_result,
                        "reference_value": case.reference_value,
                        "similarity_score": case.similarity_score
                    }
                    for case in violation.historical_cases
                ]
            }
        ]
    }

    task_mgr.set_completed("format")
    logger.info("事实认定分析完成")

    # 构建最终任务进度列表
    final_tasks = [{**task, "status": "completed"} for task in FACT_ANALYSIS_TASKS]

    # 构建用户友好的文本输出
    content_parts = ["# 违法事实分析结果\n\n"]

    # 1. 违法事实
    content_parts.append(f"## 📋 违法事实\n{violation.facts}\n\n")

    # 2. 违反条款
    content_parts.append("## ⚖️ 违反的法律条款\n\n")
    for i, provision in enumerate(violation.violated_provisions, 1):
        content_parts.append(f"### {i}. {provision.law_name} {provision.law_item_name}\n")
        content_parts.append(f"{provision.law_item_content}\n\n")

    # 3. 处罚条款
    content_parts.append("## 🔨 处罚法律条款\n\n")
    for i, provision in enumerate(violation.penalty_provisions, 1):
        content_parts.append(f"### {i}. {provision.law_name} {provision.law_item_name}\n")
        content_parts.append(f"{provision.law_item_content}\n\n")

    # 4. 裁量基准
    if violation.discretionary_benchmarks:
        content_parts.append("## 📊 裁量基准\n\n")
        for i, benchmark in enumerate(violation.discretionary_benchmarks, 1):
            content_parts.append(f"{i}. {benchmark}\n")
        content_parts.append("\n")

    # 5. 整改措施
    if violation.rectification_measures:
        content_parts.append("## 🔧 整改措施建议\n\n")
        for i, measure in enumerate(violation.rectification_measures, 1):
            content_parts.append(f"{i}. {measure}\n")
        content_parts.append("\n")

    # 6. 处罚建议
    if violation.penalty_recommendations:
        content_parts.append("## 💡 处罚建议\n\n")
        for i, suggestion in enumerate(violation.penalty_recommendations, 1):
            content_parts.append(f"{i}. {suggestion}\n")
        content_parts.append("\n")

    # 7. 历史案例参考 ⭐ 新增
    if violation.historical_cases:
        content_parts.append("## 📚 历史案例参考\n\n")
        for i, case in enumerate(violation.historical_cases, 1):
            content_parts.append(f"### 案例 {i}: {case.case_title}\n")
            content_parts.append(f"**案例编号**: {case.case_id}\n\n")
            content_parts.append(f"**案例摘要**: {case.case_summary}\n\n")
            content_parts.append(f"**违法事实**: {case.violation_facts}\n\n")
            content_parts.append(f"**处罚结果**: {case.penalty_result}\n\n")
            content_parts.append(f"**参考价值**: {case.reference_value}\n\n")
            content_parts.append(f"**相似度**: {case.similarity_score:.2%}\n\n")
            content_parts.append("---\n\n")

    final_content = "".join(content_parts)

    # 返回标准 AIMessage（包含文本 + 结构化数据）
    return {
        "messages": [
            AIMessage(
                content=[
                    {"type": "text", "text": final_content},
                    create_task_progress_block(final_tasks),
                    create_analysis_data_block(analysis_data),
                ]
            )
        ]
    }


# ============ 子图构建 ============

def create_fact_analysis_subgraph():
    """
    创建事实认定分析子图

    流程：用户输入 → 准备数据 → 检索法律依据 → 格式化输出

    特点：
    - 复用 create_retrieve_legal_basis_node（已支持6项检索）
    - 使用统一的 violations_analysis_result 数据结构
    - 包含历史案例参考
    """
    logger.info("创建事实认定分析子图...")

    subgraph = StateGraph(GraphState)

    # 创建检索节点的配置
    retrieve_config = {
        "tasks": FACT_ANALYSIS_TASKS,
        "report_name": "事实认定分析"
    }

    # 添加节点
    subgraph.add_node("prepare", prepare_fact_analysis_node)
    subgraph.add_node("retrieve", create_retrieve_legal_basis_node(retrieve_config))
    subgraph.add_node("format", format_fact_analysis_node)

    # 定义工作流
    subgraph.set_entry_point("prepare")
    subgraph.add_edge("prepare", "retrieve")
    subgraph.add_edge("retrieve", "format")
    subgraph.add_edge("format", END)

    logger.info("事实认定分析子图创建完成")

    return subgraph.compile()
