"""
事实认定分析智能体节点定义
"""

from langchain_core.messages import AIMessage, HumanMessage

from .state import FactAnalysisState
from ..shared.task_manager import TaskManager
from ..shared.retrieval_service import retrieval_service
from ..shared.report_utils import build_violations_analysis_result
from ..inspection_utils import (
    create_task_progress_block,
    create_analysis_data_block,
    create_progress_message,
    get_chat_model,
)
from utils.logger import get_logger
from utils.exceptions import BusinessException

logger = get_logger(__name__)


# 任务列表定义
TASKS = [
    {"id": "prepare", "title": "分析数据"},
    {"id": "retrieve", "title": "检索法律依据"},
    {"id": "format", "title": "生成分析结果"},
]


def prepare_node(state: FactAnalysisState) -> dict:
    """
    准备节点 - 从用户输入构造临时的 violation_file_groups
    """
    logger.info("准备事实认定分析...")

    task_mgr = TaskManager(TASKS)
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
                    "violation_facts": user_query
                }
            },
            "evidence_records": None,
            "created_at": None,
            "order": 1
        }
    ]

    task_mgr.set_completed("prepare")
    logger.info("数据准备完成")

    progress_msg = create_progress_message(task_mgr._build_task_list("prepare", 'completed'))
    return {
        "violation_file_groups": temp_file_groups,
        "messages": [progress_msg]
    }


async def retrieve_node(state: FactAnalysisState) -> dict:
    """
    检索节点 - 执行6项法律检索
    """
    import asyncio
    logger.info("开始检索法律依据...")

    task_mgr = TaskManager(TASKS)
    task_mgr.set_in_progress("retrieve")

    # Mock: 模拟搜索延迟
    await asyncio.sleep(3)

    # 获取文件分组
    violation_file_groups = state.get("violation_file_groups", [])
    if not violation_file_groups:
        raise BusinessException(msg="未找到分析数据", code=40001)

    # 从文件分组提取违法事实用于检索
    retrieval_results = []
    for idx, file_group in enumerate(violation_file_groups, start=1):
        violation_id = idx
        fact_det = file_group.get("fact_determination", {})
        parsed_data = fact_det.get("parsed_data", {})
        violation_facts = parsed_data.get("violation_facts", "")

        if not violation_facts:
            continue

        # 执行6项检索
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
    task_mgr.set_completed("retrieve")

    # 构建标准化数据结构
    violations_analysis_result = build_violations_analysis_result(
        violation_file_groups=violation_file_groups,
        retrieval_results=retrieval_results
    )

    progress_msg = create_progress_message(task_mgr._build_task_list("retrieve", 'completed'))
    return {
        "violations_analysis_result": violations_analysis_result,
        "messages": [progress_msg]
    }


INTENT_RECOGNITION_PROMPT = """你是一个意图识别助手，专门用于执法检查事实认定分析系统。请根据对话历史和当前用户输入，判断用户意图属于以下哪种：

1. **violation_analysis** - 违法事实分析请求
   - 用户输入了需要分析的违法事实内容（通常较长，描述了具体的违法行为）
   - 用户明确要求分析某段事实认定内容
   - 输入包含具体的违法行为描述、被检查人、检查内容等
   - 这是用户首次提交需要分析的内容

2. **qa** - 普通问答/对话
   - 问候语（你好、在吗等）
   - 关于功能的询问（这个系统能做什么、怎么使用等）
   - 对之前分析结果的追问或补充问题
   - 一般性的法律咨询问题
   - 简短的确认、感谢等
   - 基于已有分析结果的进一步讨论

{conversation_history}

当前用户输入：
{user_input}

请只回复一个词：violation_analysis 或 qa"""


def _extract_text_content(content) -> str:
    """从消息内容中提取纯文本"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            elif isinstance(block, str):
                text_parts.append(block)
        return " ".join(text_parts)
    return str(content) if content else ""


def _build_conversation_history(messages: list, max_turns: int = 15) -> str:
    """构建对话历史摘要（最近N轮）"""
    if not messages or len(messages) <= 1:
        return "对话历史：无（这是新对话的第一条消息）"

    # 取最近的消息（不包括当前消息）
    recent_messages = messages[:-1][-max_turns * 2:]  # 每轮包含用户+助手，所以乘2

    if not recent_messages:
        return "对话历史：无（这是新对话的第一条消息）"

    history_lines = ["对话历史："]
    for msg in recent_messages:
        role = "用户" if msg.type == "human" else "助手"
        content = _extract_text_content(msg.content)
        # 截断过长的内容
        if len(content) > 200:
            content = content[:200] + "..."
        history_lines.append(f"[{role}]: {content}")

    return "\n".join(history_lines)


def router_node(state: FactAnalysisState) -> dict:
    """
    路由节点 - 使用 LLM 进行意图识别，结合对话历史判断走分析流程还是QA流程
    """
    messages = state.get("messages", [])
    if not messages:
        return {"task_type": "qa"}

    last_message = messages[-1]

    # 获取用户输入内容
    user_input = _extract_text_content(last_message.content).strip()

    if not user_input:
        return {"task_type": "qa"}

    # 构建对话历史
    conversation_history = _build_conversation_history(messages, max_turns=15)

    # 使用 LLM 进行意图识别
    try:
        llm = get_chat_model(temperature=0, streaming=False, timeout=30)
        prompt = INTENT_RECOGNITION_PROMPT.format(
            conversation_history=conversation_history,
            user_input=user_input[:1000]  # 限制长度
        )
        result = llm.invoke([HumanMessage(content=prompt)])
        intent = result.content.strip().lower()

        # 解析结果
        if "violation_analysis" in intent:
            task_type = "violation_analysis"
        else:
            task_type = "qa"

        logger.info(f"意图识别结果: {intent} -> task_type={task_type} (历史消息数: {len(messages)-1})")

    except Exception as e:
        # LLM 调用失败，使用简单规则兜底
        logger.warning(f"意图识别 LLM 调用失败: {e}，使用规则兜底")
        # 简单规则：
        # 1. 如果已有对话历史，新输入大概率是追问
        # 2. 如果是新对话且长度>100且不以问号结尾，视为分析请求
        has_history = len(messages) > 1
        if has_history:
            task_type = "qa"
        elif len(user_input) > 100 and not user_input.endswith(("?", "？")):
            task_type = "violation_analysis"
        else:
            task_type = "qa"

    return {"task_type": task_type}


def router_conditional(state: FactAnalysisState) -> str:
    """路由条件函数"""
    task_type = state.get("task_type", "qa")
    return "prepare" if task_type == "violation_analysis" else "qa"


def _filter_messages_for_llm(messages: list) -> list:
    """过滤消息，只保留 LLM 可处理的内容，非文本内容生成描述"""
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


def qa_node(state: FactAnalysisState) -> dict:
    """问答节点 - 处理追问和普通对话"""
    logger.info("处理问答...")
    messages = state.get("messages", [])
    try:
        filtered_messages = _filter_messages_for_llm(messages[-10:])
        system_msg = HumanMessage(content="你是执法检查专家助手，专门负责违法事实分析相关的问答。基于对话历史回答用户问题，保持专业、客观。")
        llm = get_chat_model(temperature=0, streaming=True, timeout=120)
        result = llm.invoke([system_msg] + filtered_messages)
        logger.info(f"问答完成，答案长度: {len(result.content)} 字符")
        return {"messages": [AIMessage(content=result.content)]}
    except Exception as e:
        error_msg = f"问答处理失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50021)


def format_node(state: FactAnalysisState) -> dict:
    """
    格式化输出节点 - 将分析结果格式化为用户友好的输出
    """
    logger.info("格式化事实认定分析结果...")

    task_mgr = TaskManager(TASKS)
    task_mgr.set_in_progress("format")

    violations_analysis_result = state.get("violations_analysis_result")
    if not violations_analysis_result or not violations_analysis_result.violations:
        raise BusinessException(msg="分析结果为空", code=50001)

    violation = violations_analysis_result.violations[0]

    # 构建分析数据（供前端使用）
    analysis_data = {
        "inspected_person": "用户输入",
        "violations": [
            {
                "violation_id": 1,
                "facts": violation.title,
                "title": violation.facts,
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

    final_tasks = [{**task, "status": "completed"} for task in TASKS]

    # 构建用户友好的文本输出
    content_parts = ["# 违法事实分析结果\n\n"]
    content_parts.append(f"## 违法事实\n{violation.facts}\n\n")

    content_parts.append("## 违反的法律条款\n\n")
    for i, provision in enumerate(violation.violated_provisions, 1):
        content_parts.append(f"### {i}. {provision.law_name} {provision.law_item_name}\n")
        content_parts.append(f"{provision.law_item_content}\n\n")

    content_parts.append("## 处罚法律条款\n\n")
    for i, provision in enumerate(violation.penalty_provisions, 1):
        content_parts.append(f"### {i}. {provision.law_name} {provision.law_item_name}\n")
        content_parts.append(f"{provision.law_item_content}\n\n")

    if violation.discretionary_benchmarks:
        content_parts.append("## 裁量基准\n\n")
        for i, benchmark in enumerate(violation.discretionary_benchmarks, 1):
            content_parts.append(f"{i}. {benchmark}\n")
        content_parts.append("\n")

    if violation.rectification_measures:
        content_parts.append("## 整改措施建议\n\n")
        for i, measure in enumerate(violation.rectification_measures, 1):
            content_parts.append(f"{i}. {measure}\n")
        content_parts.append("\n")

    if violation.penalty_recommendations:
        content_parts.append("## 处罚建议\n\n")
        for i, suggestion in enumerate(violation.penalty_recommendations, 1):
            content_parts.append(f"{i}. {suggestion}\n")
        content_parts.append("\n")

    if violation.historical_cases:
        content_parts.append("## 历史案例参考\n\n")
        for i, case in enumerate(violation.historical_cases, 1):
            content_parts.append(f"### 案例 {i}: {case.case_title}\n")
            content_parts.append(f"**案例编号**: {case.case_id}\n\n")
            content_parts.append(f"**案例摘要**: {case.case_summary}\n\n")
            content_parts.append(f"**处罚结果**: {case.penalty_result}\n\n")
            content_parts.append(f"**参考价值**: {case.reference_value}\n\n")
            content_parts.append("---\n\n")

    final_content = "".join(content_parts)

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
