"""
通用助手节点定义

文件问答的业内标准做法（ChatGPT/Claude Web 方式）：
1. 文件与消息一一关联 - 每条消息通过 file_ids 关联自己的文件
2. 消息历史保存原始内容 - 只存 file_ids，不存文件内容
3. 调用 LLM 时临时展开 - 将 file_ids 替换为实际文件内容
4. 前端显示文件名 - 用户看到的是文件图标，不是文件内容
"""

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig
from utils.lang_util.llm import get_llm
from utils.file_service.service import get_file_info, get_parsed_content
from .state import GeneralAssistantState


async def _expand_message_with_files(msg: HumanMessage) -> HumanMessage:
    """
    将消息中的 file_ids 替换为实际文件内容

    原始消息: HumanMessage(content="请分析", additional_kwargs={"file_ids": ["uuid-1"]})
    展开后:   HumanMessage(content="[文件: report.pdf]\n{内容}\n\n请分析")

    Args:
        msg: 原始用户消息

    Returns:
        展开后的消息（如果没有文件则返回原消息）
    """
    file_ids = msg.additional_kwargs.get("file_ids", [])
    if not file_ids:
        return msg

    # 加载每个文件的内容
    file_contents = []
    for file_id in file_ids:
        info = await get_file_info(file_id)
        if not info:
            continue

        # 检查解析状态
        if info["status"] != "completed":
            file_contents.append(f"[文件: {info['original_filename']}]\n（文件解析中或解析失败）")
            continue

        content = await get_parsed_content(file_id)
        if content:
            file_contents.append(f"[文件: {info['original_filename']}]\n{content}")
        else:
            file_contents.append(f"[文件: {info['original_filename']}]\n（无法读取文件内容）")

    if not file_contents:
        return msg

    # 拼接：文件内容 + 原始问题
    expanded_content = "\n\n".join(file_contents) + "\n\n" + msg.content

    # 返回新消息（保留原消息的其他属性，但不包含 file_ids 避免重复处理）
    return HumanMessage(content=expanded_content)


async def _expand_messages_with_files(messages: list[BaseMessage]) -> list[BaseMessage]:
    """
    展开消息列表中所有带文件的 HumanMessage

    Args:
        messages: 原始消息列表

    Returns:
        展开后的消息列表（用于 LLM 调用，不保存）
    """
    expanded = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            expanded.append(await _expand_message_with_files(msg))
        else:
            expanded.append(msg)
    return expanded


async def chat_node(state: GeneralAssistantState, config: RunnableConfig) -> dict:
    """
    对话节点 - 调用 LLM 生成回复

    文件处理逻辑（业内标准做法）：
    1. 获取消息历史（包含 file_ids 但不含文件内容）
    2. 临时展开所有带 file_ids 的消息（替换为文件内容）
    3. 调用 LLM 生成回复
    4. 只返回 AI 回复，原始消息不变

    这样：
    - 消息历史保持干净（只存 file_ids）
    - 每条消息的文件一一对应
    - 前端可以根据 file_ids 显示文件图标
    """
    llm = get_llm(streaming=True)
    messages = list(state["messages"])

    # 临时展开所有带文件的消息（仅用于 LLM 调用）
    messages_for_llm = await _expand_messages_with_files(messages)

    # 调用 LLM
    response = await llm.ainvoke(messages_for_llm)

    # 只返回 AI 回复，不修改原始消息
    return {"messages": [response]}
