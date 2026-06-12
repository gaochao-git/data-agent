"""
通用报告子图工厂 (Universal Report Subgraph Factory)

根据配置动态构建报告生成子图，支持：
- 检查报告 (jcbg)
- 整改通知书 (zgtzs)
- 信息简表 (xxjb)
- 证据目录 (zjml)

通过配置驱动，新增报告类型只需：
1. 在 node_config.py 添加配置
2. 在 generators/ 目录添加生成器
"""

from typing import Callable, Optional
from langgraph.graph import StateGraph, END
from langgraph.types import RunnableConfig

from .states import GraphState
from .node_common import (
    create_check_dependencies_node,
    create_retrieve_legal_basis_node
)
from .node_review import (
    create_human_review_retrieve_node,
    create_human_review_evidence_node
)
from .node_extract_from_report import create_extract_from_report_node
from .generators import (
    generate_jcbg_document,
    generate_zgtzs_document,
    generate_xxjb_document,
    extract_xxjb_data,
    generate_zjml_document,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def create_report_subgraph(config: dict):
    """
    通用报告子图工厂函数

    根据配置动态构建报告生成子图。

    Args:
        config: 报告配置，包含：
            - report_type: 报告类型 (jcbg/zgtzs/xxjb/zjml)
            - report_name: 报告名称
            - tasks: 任务列表
            - has_extract_step: 是否有额外数据提取步骤
            - has_evidence_review: 是否有证据审核步骤
            - extra_check: 额外依赖检查函数（可选）

    Returns:
        编译后的子图
    """
    report_type = config["report_type"]
    tasks = config["tasks"]

    logger.info(f"创建报告子图: {config['report_name']} (type={report_type})")

    subgraph = StateGraph(GraphState)

    # ============ 判断输入类型 ============
    has_extract_from_report = config.get("has_extract_from_report", False)

    # ============ 公共节点 ============

    # 依赖检查节点（可能有额外检查）
    extra_check = config.get("extra_check")
    check_deps_node = create_check_dependencies_node(config, extra_check=extra_check)

    # 添加依赖检查节点
    subgraph.add_node("check_deps", check_deps_node)

    if has_extract_from_report:
        # ============ 从检查报告提取的流程 ============
        # check_deps -> extract_from_report -> review_retrieve -> ...

        extract_from_report_node = create_extract_from_report_node(config)
        subgraph.add_node("extract_from_report", extract_from_report_node)

        review_retrieve_node = create_human_review_retrieve_node(config)
        subgraph.add_node("review_retrieve", review_retrieve_node)

        # 设置入口点和边
        subgraph.set_entry_point("check_deps")
        subgraph.add_edge("check_deps", "extract_from_report")
        subgraph.add_edge("extract_from_report", "review_retrieve")

    else:
        # ============ 从事实认定书的流程 ============
        # check_deps -> retrieve -> review_retrieve -> ...

        retrieve_node = create_retrieve_legal_basis_node(config)
        review_retrieve_node = create_human_review_retrieve_node(config)

        subgraph.add_node("retrieve", retrieve_node)
        subgraph.add_node("review_retrieve", review_retrieve_node)

        # 设置入口点和边
        subgraph.set_entry_point("check_deps")
        subgraph.add_edge("check_deps", "retrieve")
        subgraph.add_edge("retrieve", "review_retrieve")

    # ============ 可选节点（根据配置）============

    has_extract_step = config.get("has_extract_step", False)
    has_evidence_review = config.get("has_evidence_review", False)

    if has_extract_step:
        # 额外数据提取节点
        extract_data_func = _get_extract_data_func(report_type, tasks)
        subgraph.add_node("extract", extract_data_func)

    if has_evidence_review:
        # 证据审核节点
        review_evidence_node = create_human_review_evidence_node(config)
        subgraph.add_node("review_evidence", review_evidence_node)

    # ============ 生成节点（每个报告不同）============

    generate_func = _get_generate_func(report_type, tasks)
    subgraph.add_node("generate", generate_func)

    # ============ 构建后续工作流边 ============

    # 根据配置添加可选边
    if has_extract_step and has_evidence_review:
        # zjml: review_retrieve -> extract -> review_evidence -> generate
        subgraph.add_edge("review_retrieve", "extract")
        subgraph.add_edge("extract", "review_evidence")
        subgraph.add_edge("review_evidence", "generate")
    elif has_extract_step:
        # xxjb: review_retrieve -> extract -> generate
        subgraph.add_edge("review_retrieve", "extract")
        subgraph.add_edge("extract", "generate")
    else:
        # jcbg/zgtzs: review_retrieve -> generate
        subgraph.add_edge("review_retrieve", "generate")

    subgraph.add_edge("generate", END)

    logger.info(f"报告子图创建完成: {config['report_name']}")

    return subgraph.compile()


def _get_generate_func(report_type: str, tasks: list) -> Callable:
    """
    获取生成函数（包装为节点函数）

    Args:
        report_type: 报告类型
        tasks: 任务列表

    Returns:
        节点函数
    """
    def generate_node(state: GraphState, config: RunnableConfig) -> dict:
        if report_type == "jcbg":
            return generate_jcbg_document(state, config, tasks)
        elif report_type == "zgtzs":
            return generate_zgtzs_document(state, config, tasks)
        elif report_type == "xxjb":
            return generate_xxjb_document(state, config, tasks)
        elif report_type == "zjml":
            return generate_zjml_document(state, config, tasks)
        else:
            raise ValueError(f"未知的报告类型: {report_type}")

    return generate_node


def _get_extract_data_func(report_type: str, tasks: list) -> Callable:
    """
    获取数据提取函数（包装为节点函数）

    Args:
        report_type: 报告类型
        tasks: 任务列表

    Returns:
        节点函数
    """
    def extract_node(state: GraphState, config: RunnableConfig) -> dict:
        if report_type == "xxjb":
            return extract_xxjb_data(state, tasks)
        else:
            raise ValueError(f"报告类型 {report_type} 不支持数据提取步骤")

    return extract_node


# ============ 便捷工厂函数（兼容旧代码）============

def create_inspection_report_subgraph():
    """创建检查报告子图（兼容旧接口）"""
    from .node_config import CONFIG_JCBG
    return create_report_subgraph(CONFIG_JCBG)


def create_rectification_report_subgraph():
    """创建整改通知书子图（兼容旧接口）"""
    from .node_config import CONFIG_ZGTZS
    return create_report_subgraph(CONFIG_ZGTZS)


def create_info_summary_subgraph():
    """创建信息简表子图（兼容旧接口）"""
    from .node_config import CONFIG_XXJB
    return create_report_subgraph(CONFIG_XXJB)


def create_evidence_catalog_subgraph():
    """创建证据目录子图（兼容旧接口）"""
    from .node_config import CONFIG_ZJML
    return create_report_subgraph(CONFIG_ZJML)
