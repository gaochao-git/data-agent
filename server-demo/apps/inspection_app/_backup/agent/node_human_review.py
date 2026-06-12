"""
Human-in-the-Loop 人工审核节点
使用 LangGraph interrupt 机制实现暂停和恢复
"""

from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer

from .states import GraphState, SplitResult, ViolationItem
from .task_manager import TaskManager
from utils.logger import get_logger

logger = get_logger(__name__)

# 任务配置（与 subgraph_vio.py 保持一致）
VIOLATION_TASKS = [
    {"id": "split", "title": "分析事实认定"},
    {"id": "review_split", "title": "确认拆分结果"},
    {"id": "retrieve", "title": "检索法律法规"},
    {"id": "review_retrieve", "title": "确认检索结果"},
    {"id": "format", "title": "格式化输出"}
]


def human_review_split_node(state: GraphState) -> Command:
    """
    拆分结果人工审核节点

    暂停图执行，等待用户确认或修改拆分结果
    支持的操作：
    - approve: 确认当前结果，继续执行
    - edit: 修改后继续执行
    - reject: 拒绝，终止执行
    """
    split_result = state.get("vio_split_result")

    if not split_result:
        logger.error("拆分结果为空，无法进行审核")
        raise ValueError("拆分结果为空")

    # 设置任务进度为进行中
    task_manager = TaskManager(VIOLATION_TASKS)
    task_manager.set_in_progress("review_split")

    # 发送审核数据给前端
    writer = get_stream_writer()
    writer({
        "type": "non_standard",
        "value": {
            "type": "human_review",
            "data": {
                "review_type": "split",
                "message": "请确认违法行为拆分结果",
                "data": {
                    "violations": [
                        {
                            "violation_id": v.violation_id,
                            "original_fact": v.original_fact,
                            "ai_expansion": v.ai_expansion
                        }
                        for v in split_result.violations
                    ]
                }
            }
        }
    })

    logger.info(f"等待用户审核拆分结果，共 {len(split_result.violations)} 个违法行为...")

    # 中断执行，等待用户输入
    # interrupt 会暂停图执行，并将数据存入 checkpoint
    human_input = interrupt({
        "review_type": "split",
        "message": "请确认违法行为拆分结果",
        "current_data": {
            "violations": [
                {
                    "violation_id": v.violation_id,
                    "original_fact": v.original_fact,
                    "ai_expansion": v.ai_expansion
                }
                for v in split_result.violations
            ]
        }
    })

    logger.info(f"收到用户审核结果: {human_input}")

    # 处理用户输入
    action = human_input.get("action", "approve")

    if action == "reject":
        # 用户拒绝，终止执行
        logger.info("用户拒绝拆分结果，终止执行")
        raise ValueError("用户取消操作")

    elif action == "edit":
        # 用户编辑了数据，使用修改后的结果
        modified_violations = human_input.get("data", {}).get("violations", [])
        if modified_violations:
            # 重建 SplitResult
            new_violations = [
                ViolationItem(
                    violation_id=v["violation_id"],
                    original_fact=v["original_fact"],
                    ai_expansion=v["ai_expansion"]
                )
                for v in modified_violations
            ]
            new_split_result = SplitResult(violations=new_violations)
            logger.info(f"用户修改了拆分结果，新结果包含 {len(new_violations)} 个违法行为")
            # 标记任务完成，获取进度更新
            progress_update = task_manager.set_completed("review_split")
            return Command(update={
                "vio_split_result": new_split_result,
                "human_approved": True,
                **progress_update
            })

    # 默认：用户确认（approve）
    logger.info("用户确认拆分结果")
    progress_update = task_manager.set_completed("review_split")
    return Command(update={"human_approved": True, **progress_update})


def human_review_retrieve_node(state: GraphState) -> Command:
    """
    检索结果人工审核节点

    暂停图执行，等待用户确认或修改法律条款检索结果
    """
    split_result = state.get("vio_split_result")
    retrieval_results = state.get("vio_retrieval_results", [])

    if not split_result or not retrieval_results:
        logger.error("拆分结果或检索结果为空，无法进行审核")
        raise ValueError("数据不完整")

    # 设置任务进度为进行中
    task_manager = TaskManager(VIOLATION_TASKS)
    task_manager.set_in_progress("review_retrieve")

    # 按 violation_id 组织检索结果
    results_by_violation = {}
    for result in retrieval_results:
        vid = result["violation_id"]
        result_type = result["type"]
        if vid not in results_by_violation:
            results_by_violation[vid] = {}
        results_by_violation[vid][result_type] = result["data"]

    # 构建审核数据
    review_data = []
    for violation in split_result.violations:
        vid = violation.violation_id
        violation_data = results_by_violation.get(vid, {})
        review_data.append({
            "violation_id": vid,
            "original_fact": violation.original_fact,
            "ai_expansion": violation.ai_expansion,
            "legal_basis": violation_data.get("legal_basis", []),
            "penalty_clause": violation_data.get("penalty_clause", []),
            "discretion_standard": violation_data.get("discretion_standard", []),
            "rectification_measures": violation_data.get("rectification_measures", []),
            "penalty_suggestion": violation_data.get("penalty_suggestion", [])
        })

    # 发送审核数据给前端
    writer = get_stream_writer()
    writer({
        "type": "non_standard",
        "value": {
            "type": "human_review",
            "data": {
                "review_type": "retrieve",
                "message": "请确认法律条款检索结果",
                "data": {
                    "violations": review_data
                }
            }
        }
    })

    logger.info(f"等待用户审核检索结果...")

    # 中断执行，等待用户输入
    human_input = interrupt({
        "review_type": "retrieve",
        "message": "请确认法律条款检索结果",
        "current_data": {
            "violations": review_data
        }
    })

    logger.info(f"收到用户审核结果: {human_input}")

    # 处理用户输入
    action = human_input.get("action", "approve")

    if action == "reject":
        logger.info("用户拒绝检索结果，终止执行")
        raise ValueError("用户取消操作")

    elif action == "edit":
        # 用户编辑了数据，重建检索结果
        modified_violations = human_input.get("data", {}).get("violations", [])
        if modified_violations:
            new_retrieval_results = []
            for v in modified_violations:
                vid = v["violation_id"]
                # 重建各类型的检索结果
                for result_type in ["legal_basis", "penalty_clause", "discretion_standard",
                                   "rectification_measures", "penalty_suggestion"]:
                    if result_type in v:
                        new_retrieval_results.append({
                            "violation_id": vid,
                            "type": result_type,
                            "data": v[result_type]
                        })

            logger.info(f"用户修改了检索结果，新结果包含 {len(new_retrieval_results)} 条")
            # 标记任务完成，获取进度更新
            progress_update = task_manager.set_completed("review_retrieve")
            # 注意：这里直接替换而不是累加，所以需要清空原有结果
            return Command(update={
                "vio_retrieval_results": new_retrieval_results,
                "human_approved": True,
                **progress_update
            })

    # 默认：用户确认
    logger.info("用户确认检索结果")
    progress_update = task_manager.set_completed("review_retrieve")
    return Command(update={"human_approved": True, **progress_update})
