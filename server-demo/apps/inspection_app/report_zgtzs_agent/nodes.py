"""
整改通知书智能体节点定义
"""

from langgraph.types import RunnableConfig
from langchain_core.messages import HumanMessage, AIMessage

from .state import ZgTzsState
from ..shared.task_manager import TaskManager
from ..shared.generators.zgtzs_generator import generate_zgtzs_document
from ..shared.extract_from_report import extract_violations_from_report
from ..inspection_utils import create_progress_message, get_chat_model, get_violation_upload_path
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)

TASKS = [
    {"id": "check_deps", "title": "提取所需数据"},
    {"id": "generate", "title": "生成Word文档"},
]


def check_deps_node(state: ZgTzsState, config: RunnableConfig) -> dict:
    logger.info("检查整改通知书所需数据...")
    task_mgr = TaskManager(TASKS)
    task_mgr.set_in_progress("check_deps")

    thread_id = config.get("configurable", {}).get("thread_id")

    # 检查是否已有分析结果
    analysis_result = state.get("violations_analysis_result")

    # 检查 violations 是否为空
    has_violations = analysis_result and hasattr(analysis_result, 'violations') and len(analysis_result.violations) > 0

    # 如果没有分析结果或 violations 为空，尝试从上传的检查报告中提取
    if not has_violations:
        logger.info("未找到有效的分析结果（violations 为空），尝试从上传的检查报告提取...")

        # 优先从 analysis_result.source_files 中获取文件路径
        file_path = None
        if analysis_result and hasattr(analysis_result, 'source_files') and analysis_result.source_files:
            for source_file in analysis_result.source_files:
                if source_file.get("file_type") == "检查报告":
                    file_path = source_file.get("file_path")
                    break
            if not file_path and analysis_result.source_files:
                file_path = analysis_result.source_files[0].get("file_path")

        # 如果 source_files 中没有，尝试从消息的 uploaded_files 中获取
        if not file_path:
            messages = state.get("messages", [])
            uploaded_files = []
            for msg in reversed(messages):
                if hasattr(msg, 'additional_kwargs'):
                    task = msg.additional_kwargs.get("task", {})
                    uploaded_files = task.get("uploaded_files", [])
                    if uploaded_files:
                        break

            if uploaded_files:
                report_file = None
                for f in uploaded_files:
                    file_type = f.get("file_type", "")
                    if file_type == "检查报告" or "报告" in f.get("file_name", ""):
                        report_file = f
                        break
                if not report_file:
                    report_file = uploaded_files[0]

                file_name = report_file.get("file_name") or report_file.get("filename")
                if file_name:
                    file_path = get_violation_upload_path(thread_id, file_name)

        if not file_path:
            raise BusinessException(msg="请上传检查报告文件", code=40001)

        logger.info(f"从检查报告提取数据: {file_path}")
        analysis_result = extract_violations_from_report(file_path)
        logger.info(f"从检查报告提取到 {len(analysis_result.violations)} 个违法行为")

    violations_count = len(analysis_result.violations) if hasattr(analysis_result, 'violations') else 0
    logger.info(f"检测到分析结果，包含 {violations_count} 个违法行为")

    task_mgr.set_completed("check_deps")
    progress_msg = create_progress_message(task_mgr._build_task_list("check_deps", 'completed'))
    return {
        "violations_analysis_result": analysis_result,
        "messages": [progress_msg]
    }


def generate_node(state: ZgTzsState, config: RunnableConfig) -> dict:
    logger.info("开始生成整改通知书 Word 文档...")
    return generate_zgtzs_document(state, config, TASKS)


def router_node(state: ZgTzsState) -> dict:
    """路由节点"""
    messages = state.get("messages", [])
    if not messages:
        return {"task_type": "qa"}
    last_message = messages[-1]
    task_dict = last_message.additional_kwargs.get("task", {}) if hasattr(last_message, 'additional_kwargs') else {}
    task_type = task_dict.get("task_type", "qa")
    logger.info(f"路由决策: task_type={task_type}")
    return {"task_type": task_type}


def router_conditional(state: ZgTzsState) -> str:
    """路由条件函数"""
    task_type = state.get("task_type", "qa")
    return "check_deps" if task_type == "report" else "qa"


def _filter_messages_for_llm(messages: list) -> list:
    """过滤消息，只保留 LLM 可处理的内容，非文本内容生成描述"""
    from langchain_core.messages import SystemMessage
    filtered = []
    for msg in messages:
        content = msg.content
        if isinstance(content, str):
            if isinstance(msg, HumanMessage):
                filtered.append(HumanMessage(content=content))
            elif isinstance(msg, AIMessage):
                filtered.append(AIMessage(content=content))
        elif isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "file":
                        text_parts.append(f"[已生成文件: {block.get('filename', '文件')}]")
                    elif block_type == "non_standard" and block.get("value", {}).get("type") == "file":
                        text_parts.append(f"[已生成文件: {block.get('value', {}).get('data', {}).get('filename', '文件')}]")
                elif isinstance(block, str):
                    text_parts.append(block)
            if text_parts:
                combined = "\n".join(text_parts)
                if isinstance(msg, HumanMessage):
                    filtered.append(HumanMessage(content=combined))
                elif isinstance(msg, AIMessage):
                    filtered.append(AIMessage(content=combined))
    return filtered


def qa_node(state: ZgTzsState) -> dict:
    """问答节点"""
    logger.info("处理问答...")
    messages = state.get("messages", [])
    try:
        filtered_messages = _filter_messages_for_llm(messages[-10:])
        system_msg = HumanMessage(content="你是执法检查专家助手，专门负责整改通知书相关的问答。基于对话历史回答用户问题，保持专业、客观。")
        llm = get_chat_model(temperature=0, streaming=True, timeout=120)
        result = llm.invoke([system_msg] + filtered_messages)
        logger.info(f"问答完成，答案长度: {len(result.content)} 字符")
        return {"messages": [AIMessage(content=result.content)]}
    except Exception as e:
        error_msg = f"问答处理失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50021)
