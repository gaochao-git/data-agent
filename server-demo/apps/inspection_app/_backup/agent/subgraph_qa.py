"""
问答子图
处理用户问题，可访问当前违规分析结果和历史对话
符合 LangGraph 官方最佳实践：使用共享状态设计
"""


from dotenv import load_dotenv

from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage

from .states import GraphState
from ..inspection_utils import get_chat_model
from utils.logger import get_logger
from utils.exceptions import BusinessException

# 初始化日志
logger = get_logger(__name__)

# 加载环境变量
load_dotenv()


# ============ 辅助函数 ============

def build_qa_context(messages_history):
    """构建 QA 上下文（使用 LangChain BaseMessage 格式）

    直接从 messages 历史中提取上下文，无需单独传递 vio_result
    因为违规分析结果已经通过 aggregate_messages_node 添加到 messages 中了
    """
    context_parts = []

    # 提取最近 10 条消息（包含违规分析结果和对话历史）
    recent_messages = messages_history[-10:] if len(messages_history) > 10 else messages_history

    if recent_messages:
        context_parts.append("历史对话：")
        for msg in recent_messages:
            # 判断消息类型（HumanMessage 或 AIMessage）
            if isinstance(msg, HumanMessage):
                role = "用户"
                # 检查是否是违规分析调用（通过 name 属性）
                if getattr(msg, "name", None) == "violation_analysis":
                    context_parts.append(f"{role}: [请求违规分析]")
                else:
                    context_parts.append(f"{role}: {msg.content}")
            elif isinstance(msg, AIMessage):
                role = "助手"
                # 检查是否是违规分析结果
                if getattr(msg, "name", None) == "violation_analysis":
                    context_parts.append(f"{role}: [违规分析结果] {msg.content}")
                else:
                    context_parts.append(f"{role}: {msg.content}")

    return "\n".join(context_parts)


# ============ LangGraph 节点定义 ============

def qa_node(state: GraphState) -> dict:
    """问答节点 - 使用 LLM 回答问题

    只需要访问 messages 历史，违规分析结果已包含在其中
    使用 LangGraph 原生 messages 模式流式输出
    """
    logger.info("处理问答...")

    messages_history = state.get("messages", [])

    # 从最后一条用户消息中提取问题
    question = ""
    if messages_history:
        last_message = messages_history[-1]
        if isinstance(last_message, HumanMessage):
            question = last_message.content

    try:
        # 使用公共函数构建上下文
        context = build_qa_context(messages_history)

        # 使用 ChatPromptTemplate 构建结构化提示词（LangGraph 官方推荐）
        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是执法检查专家助手，负责回答用户关于违规分析的问题。

你可以访问当前的违规分析结果和历史对话记录。

回答要求：
1. 基于提供的分析结果回答问题
2. 如果问题涉及具体的法律条款，引用分析结果中的相关内容
3. 保持专业、客观的态度
4. 如果分析结果中没有相关信息，诚实告知用户

请直接回答用户的问题，不要重复分析结果的全部内容。"""),
            ("human", """上下文信息：
{context}

用户问题：{question}

请回答用户的问题：""")
        ])

        # 格式化消息
        formatted_messages = prompt.format_messages(
            context=context,
            question=question
        )

        # LLM 在节点内部创建（测试验证：不影响流式输出）
        # 关键是主图正确解包 stream 返回的格式
        logger.info(f"处理问题: {question[:50]}...")

        llm = get_chat_model(
            temperature=0,
            streaming=True,
            timeout=120
        )

        result = llm.invoke(formatted_messages)
        answer = result.content

        logger.info(f"完成，答案长度: {len(answer)} 字符")

        # 返回标准 AIMessage（符合 LangChain 官方规范）
        return {
            "messages": [
                AIMessage(
                    content=answer
                )
            ]
        }

    except Exception as e:
        error_msg = f"问答处理失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise BusinessException(msg=error_msg, code=50021)


# ============ 构建子图 ============

def create_qa_subgraph():
    """
    创建问答子图（符合 LangGraph 官方推荐的共享状态设计）

    使用 GraphState 共享状态。
    子图直接读写它需要的字段（messages, answer）。

    注意：问答子图不需要读取 vio_result，因为违规分析结果已经通过
    aggregate_messages_node 添加到 messages 历史中了。

    参考：https://docs.langchain.com/oss/python/langgraph/use-subgraphs
    """
    subgraph = StateGraph(GraphState)

    # 添加节点
    subgraph.add_node("qa", qa_node)

    # 定义工作流
    subgraph.set_entry_point("qa")
    subgraph.add_edge("qa", END)

    return subgraph.compile()
