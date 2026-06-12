"""
公共节点函数
被多个报告子图复用的节点逻辑
"""
import os
from typing import Callable, Optional

from langgraph.types import RunnableConfig
from .states import GraphState
from .task_manager import TaskManager
from utils.logger import get_logger
from utils.exceptions import BusinessException
from ..inspection_utils import parse_fact_determination_doc, get_violation_uploads_dir, create_progress_message
from .retrieval_service import retrieval_service
from .report_utils import build_violations_analysis_result

logger = get_logger(__name__)


def create_check_dependencies_node(config: dict, extra_check: Optional[Callable] = None):
    """
    创建依赖检查节点（工厂函数）

    Args:
        config: 节点配置 (包含 tasks, report_name 等)
        extra_check: 额外检查函数 (state) -> None，抛出异常表示检查失败
    """
    def check_dependencies_node(state: GraphState) -> dict:
        """检查依赖数据是否完整"""
        logger.info(f"检查{config['report_name']}所需数据...")

        task_mgr = TaskManager(config['tasks'])
        task_mgr.set_in_progress(config['tasks'][0]['id'])  # 第一个任务

        # 判断输入类型
        input_type = config.get("input_type", "fact_determination")

        if input_type == "inspection_report":
            # ============ 检查上传的检查报告 ============
            # 从 violations_analysis_result.source_files 中查找检查报告
            analysis_result = state.get("violations_analysis_result")

            logger.info(f"[依赖检查] 检查报告输入模式")
            logger.info(f"[依赖检查] violations_analysis_result: {analysis_result is not None}")
            logger.info(f"[依赖检查] state 所有 keys: {list(state.keys())}")

            if not analysis_result:
                logger.error("[依赖检查] violations_analysis_result 为空，请上传检查报告")
                raise BusinessException(msg="请上传检查报告", code=40001)

            # 提取 source_files
            if isinstance(analysis_result, dict):
                source_files = analysis_result.get("source_files", [])
            else:
                source_files = analysis_result.source_files if hasattr(analysis_result, "source_files") else []

            # 查找检查报告
            uploaded_report = None
            for file_info in source_files:
                if file_info.get("file_type") == "检查报告":
                    uploaded_report = file_info
                    break

            if not uploaded_report:
                logger.error("[依赖检查] source_files 中未找到检查报告")
                raise BusinessException(msg="请上传检查报告", code=40001)

            file_path = uploaded_report.get("file_path")
            if not file_path or not os.path.exists(file_path):
                logger.error(f"[依赖检查] 检查报告文件不存在: {file_path}")
                raise BusinessException(msg="检查报告文件不存在", code=40002)

            logger.info(f"检测到检查报告: {uploaded_report.get('filename', file_path)}")

        else:
            # ============ 检查违法行为文件分组（事实认定书） ============
            file_groups = state.get("violation_file_groups") or []

            logger.info(f"[依赖检查] 事实认定书输入模式")
            logger.info(f"[依赖检查] violation_file_groups: {len(file_groups)} 个分组")
            logger.info(f"[依赖检查] state 所有 keys: {list(state.keys())}")

            if not file_groups:
                logger.error("[依赖检查] violation_file_groups 为空，请检查文件上传流程")
                raise BusinessException(msg="请上传事实认定书", code=40001)

            logger.info(f"检测到 {len(file_groups)} 个违法行为文件分组")

        # 额外检查（如证据目录需要检查取证记录表）
        if extra_check:
            extra_check(state)

        task_mgr.set_completed(config['tasks'][0]['id'])
        logger.info("依赖数据检查通过")

        # 保存进度到 messages，以便刷新页面后恢复
        progress_msg = create_progress_message(task_mgr._build_task_list(config['tasks'][0]['id'], 'completed'))
        return {"messages": [progress_msg]}

    return check_dependencies_node


def create_retrieve_legal_basis_node(config: dict):
    """
    创建检索法律依据节点（工厂函数）

    Args:
        config: 节点配置
    """
    def retrieve_legal_basis_node(state: GraphState) -> dict:
        """检索法律依据 - 直接从 violation_file_groups 检索并构建标准结构"""

        task_mgr = TaskManager(config['tasks'])
        retrieve_task_id = 'retrieve'

        # 检查是否已有标准化结果（幂等性检查）
        if state.get("violations_analysis_result"):
            logger.info("检测到已有标准化分析结果，跳过检索步骤")
            task_mgr.set_completed(retrieve_task_id)
            progress_msg = create_progress_message(task_mgr._build_task_list(retrieve_task_id, 'completed'))
            return {"messages": [progress_msg]}

        # 获取文件分组
        violation_file_groups = state.get("violation_file_groups")
        if not violation_file_groups:
            logger.error("未找到文件分组数据")
            raise BusinessException(msg="请上传事实认定书", code=40001)

        logger.info(f"开始检索法律依据 - 共 {len(violation_file_groups)} 个违法行为")
        task_mgr.set_in_progress(retrieve_task_id)

        # 直接从文件分组遍历检索
        retrieval_results = []
        for idx, file_group in enumerate(violation_file_groups, start=1):
            violation_id = idx  # 业务规则：文件分组顺序 = violation_id

            # 提取违法事实用于检索
            fact_det = file_group.get("fact_determination", {})
            parsed_data = fact_det.get("parsed_data", {})
            violation_facts = parsed_data.get("violation_facts", "")

            if not violation_facts:
                logger.warning(f"违法行为 {violation_id} 的事实认定为空，跳过检索")
                continue

            # 检索6项内容（增加历史案例）⭐
            retrieval_results.append({
                "violation_id": violation_id,
                "type": "legal_basis",
                "data": retrieval_service.retrieve_legal_basis(violation_facts)
            })
            retrieval_results.append({
                "violation_id": violation_id,
                "type": "penalty_clause",
                "data": retrieval_service.retrieve_penalty_clause(violation_facts)
            })
            retrieval_results.append({
                "violation_id": violation_id,
                "type": "discretion_standards",
                "data": retrieval_service.retrieve_discretion_standard(violation_facts)
            })
            retrieval_results.append({
                "violation_id": violation_id,
                "type": "rectification_measures",
                "data": retrieval_service.retrieve_rectification_measures(violation_facts)
            })
            retrieval_results.append({
                "violation_id": violation_id,
                "type": "penalty_suggestions",
                "data": retrieval_service.retrieve_penalty_suggestion(violation_facts)
            })
            retrieval_results.append({
                "violation_id": violation_id,
                "type": "historical_cases",
                "data": retrieval_service.retrieve_historical_cases(violation_facts)
            })

        logger.info(f"检索完成 - 共 {len(retrieval_results)} 项检索结果")
        task_mgr.set_completed(retrieve_task_id)

        # 构建标准化数据结构 ⭐（唯一数据源）
        violations_analysis_result = build_violations_analysis_result(
            violation_file_groups=violation_file_groups,
            retrieval_results=retrieval_results
        )
        logger.info("标准化数据结构构建完成")

        # 保存进度到 messages
        progress_msg = create_progress_message(task_mgr._build_task_list(retrieve_task_id, 'completed'))

        # ⭐ 清空 violation_file_groups（数据已转换到 violations_analysis_result，释放内存）
        return {
            "violations_analysis_result": violations_analysis_result,  # ⭐ 唯一数据源
            "messages": [progress_msg],
            # 注：不返回 violation_file_groups，让它保持原值（供幂等性重试使用）
        }

    return retrieve_legal_basis_node


# ========== 工具函数 ==========

def _find_uploaded_file(file_id: str, extensions: list[str], thread_id: str = None) -> str:
    """
    查找上传的文件路径

    Args:
        file_id: 文件ID
        extensions: 支持的扩展名列表，如 ['.docx', '.doc', '.pdf']
        thread_id: 会话ID，用于定位文件目录

    Returns:
        文件路径

    Raises:
        BusinessException: 文件不存在
    """
    if thread_id:
        uploads_dir = get_violation_uploads_dir(thread_id)
        for ext in extensions:
            file_path = os.path.join(uploads_dir, f"upload_{file_id}{ext}")
            if os.path.exists(file_path):
                return file_path

    logger.error(f"文件不存在: file_id={file_id}, thread_id={thread_id}")
    raise BusinessException(msg=f"文件不存在: {file_id}", code=40003)


