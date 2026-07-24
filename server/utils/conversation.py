"""
公共会话管理模块
提供会话的创建、查询、更新、删除等通用功能

使用 SQLAlchemy 统一管理业务数据库操作
"""

import uuid
import builtins
from datetime import datetime
import re
from typing import Optional, List, Dict, Any, Callable

from langchain_core.prompts import ChatPromptTemplate

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from apps.chat_data_app.daos.conversation_dao import ConversationDao
from apps.chat_data_app.services.conversation_event_service import conversation_event_service
from apps.chat_data_app.services.context_budget import sanitize_context_usage_for_response
from apps.chat_data_app.services.llm_config import get_chat_data_llm
from .checkpoint import get_checkpointer
from .database import get_session
from .models import UserThread

logger = get_logger(__name__)
conversation_dao = ConversationDao(session_context=lambda: get_session())


# ============ 内部函数 ============

TITLE_GENERATION_PROMPT = """你是一个对话标题生成助手。根据用户的第一条消息，生成一个简短、精准的对话标题。

要求：
1. 标题长度：严格控制在10-15个汉字（不少于10个字，不超过15个字）
2. 直接概括用户问题的核心内容，不要创造性发挥或生成用户未提及的内容
3. 使用简洁、专业的语言
4. 只返回标题文本，不要任何其他内容
5. 不要加引号、书名号、标点符号等
6. 绝对不允许使用表情符号
7. 不要直接照抄用户原句作为标题，应概括为更短的主题短语

示例：
用户消息：某科技公司于2024年1月至6月期间，在未取得支付业务许可证的情况下...
标题：某科技公司无证支付业务案件

用户消息：请帮我分析这个案例的违反条款
标题：案例违反条款分析咨询

用户消息：小微商户使用信用卡收款余额超1000元，手续费超规定5元...
标题：小微商户信用卡收款超限问题

现在，请为以下用户消息生成标题："""


def _get_title_llm():
    """获取标题生成用的LLM"""
    return get_chat_data_llm(streaming=False, max_tokens=30)


def _format_thread(t: UserThread) -> Dict[str, Any]:
    """转换会话列表返回结构"""
    return {
        "id": t.thread_id,
        "title": t.thread_title,
        "agent_id": t.agent_id,
        "create_time": t.create_time.strftime('%Y-%m-%d %H:%M:%S') if t.create_time else "",
        "update_time": t.update_time.strftime('%Y-%m-%d %H:%M:%S') if t.update_time else "",
        "is_favorited": bool(t.is_favorited) if t.is_favorited is not None else False
    }


def _clean_title(title: str, fallback: str = "") -> str:
    """清理标题文本"""
    title = title.strip()
    title = title.strip('"').strip("'").strip('《》""''').strip()
    # 移除表情符号和特殊字符
    title = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', title)
    title = title[:15]  # 限制最大长度

    if not title or len(title) < 5:
        title = fallback[:15].strip() if fallback else "新对话"

    return title


def _get_first_user_message(messages: List[Any]) -> Optional[str]:
    """从消息列表中获取第一条用户消息"""
    for msg in messages:
        if hasattr(msg, 'type') and msg.type == 'human':
            return msg.content
    return None


def _extract_timestamp(msg) -> str:
    """
    从消息的 additional_kwargs 中提取时间戳

    时间戳存储在 additional_kwargs["timestamp"] 中，
    由发送消息时设置（流式处理的 handler 中）。
    """
    if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
        ts = msg.additional_kwargs.get('timestamp')
        if ts:
            return ts
    return ""


def _extract_reasoning_content(msg) -> str:
    """从消息元数据中提取模型原生思考内容。"""
    candidates = []
    for attr in ("additional_kwargs", "response_metadata"):
        value = getattr(msg, attr, None)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        for key in ("reasoning_content", "reasoning", "reasoning_text"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _serialize_messages(messages: List[Any]) -> List[Dict[str, Any]]:
    """
    序列化LangChain消息为前端格式

    处理逻辑：
    - HumanMessage (type='human') -> role='user'
      - file_ids 从 additional_kwargs 中提取
      - timestamp 从 additional_kwargs 中提取
    - AIMessage (type='ai') -> role='assistant'
      - tool_calls 保留用于前端展示工具卡片
      - timestamp 从 additional_kwargs 中提取
    - ToolMessage (type='tool') -> role='tool'，保留 tool_call_id 用于关联

    保留 LangGraph 原始消息顺序，前端按顺序渲染即可正确显示工具调用和响应的交错顺序。
    """
    serialized = []

    for msg in messages:
        if not hasattr(msg, 'type'):
            continue

        # 时间戳只使用 checkpoint 中真实保存的消息时间，避免历史刷新时显示推导出来的假时间。
        timestamp = _extract_timestamp(msg)

        if msg.type == 'human':
            msg_data = {
                "id": msg.id,  # LangGraph 自动生成
                "role": "user",
                "content": msg.content,
                "timestamp": timestamp
            }
            # 从 additional_kwargs 提取 file_ids
            if hasattr(msg, 'additional_kwargs') and msg.additional_kwargs:
                file_ids = msg.additional_kwargs.get('file_ids')
                if file_ids:
                    msg_data["file_ids"] = file_ids
                semantic_config_selection = _serialize_semantic_config_selection(msg.additional_kwargs.get("semantic_config_selection"))
                if semantic_config_selection:
                    msg_data["semantic_config_selection"] = semantic_config_selection
                reasoning_turn_id = str(msg.additional_kwargs.get("reasoning_turn_id") or "").strip()
                if reasoning_turn_id:
                    msg_data["reasoning_turn_id"] = reasoning_turn_id

            serialized.append(msg_data)

        elif msg.type == 'ai':
            ai_msg = {
                "id": msg.id,  # LangGraph 自动生成
                "role": "assistant",
                "content": msg.content,
                "timestamp": timestamp
            }
            # 保留 tool_calls 信息（用于前端展示工具调用卡片）
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                tool_calls_list = []
                for tc in msg.tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
                    tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
                    tool_calls_list.append({
                        "id": tc_id,
                        "name": tc_name,
                        "args": tc_args
                    })
                ai_msg["tool_calls"] = tool_calls_list
            reasoning_content = _extract_reasoning_content(msg)
            if reasoning_content:
                ai_msg["reasoning_content"] = reasoning_content
            serialized.append(ai_msg)

        elif msg.type == 'tool':
            tool_name = getattr(msg, 'name', '')
            tool_call_id = getattr(msg, 'tool_call_id', '')
            tool_content = msg.content

            # ToolMessage 作为独立消息，保留 tool_call_id 用于前端关联
            serialized.append({
                "id": msg.id,  # LangGraph 自动生成
                "role": "tool",
                "content": tool_content,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "timestamp": timestamp
            })

    return serialized


def _serialize_reasoning_segments(reasoning_segments: Any) -> List[Dict[str, Any]]:
    """序列化模型原生思考分段，供前端恢复展示。"""
    if not isinstance(reasoning_segments, builtins.list):
        return []
    serialized = []
    for index, item in enumerate(reasoning_segments):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content:
            continue
        tool_call_count = item.get("tool_call_count", item.get("toolCallCount", 0))
        try:
            normalized_tool_call_count = max(0, int(tool_call_count))
        except (TypeError, ValueError):
            normalized_tool_call_count = 0
        segment = {
            "id": str(item.get("id") or f"reasoning-history-{index}"),
            "content": content,
            "tool_call_count": normalized_tool_call_count,
        }
        reasoning_turn_id = str(item.get("reasoning_turn_id") or item.get("reasoningTurnId") or "").strip()
        if reasoning_turn_id:
            segment["reasoning_turn_id"] = reasoning_turn_id
        serialized.append(segment)
    return serialized


def _serialize_semantic_config_selection(selection: Any) -> Optional[Dict[str, Any]]:
    """序列化智能问数指标集选择提示，供前端恢复展示。"""
    if not isinstance(selection, dict):
        return None
    semantic_config_id = str(selection.get("semantic_config_id") or "").strip()
    if not semantic_config_id:
        return None
    matched_metrics = selection.get("matched_metrics")
    matched_dimensions = selection.get("matched_dimensions")
    return {
        "semantic_config_id": semantic_config_id,
        "semantic_config_name": selection.get("semantic_config_name") or semantic_config_id,
        "confidence": selection.get("confidence") or "",
        "reason": selection.get("reason") or "",
        "matched_metrics": matched_metrics if isinstance(matched_metrics, builtins.list) else [],
        "matched_dimensions": matched_dimensions if isinstance(matched_dimensions, builtins.list) else [],
        "auto_selected": bool(selection.get("auto_selected")),
        "user_selected": bool(selection.get("user_selected")),
    }


# 公开别名（向后兼容）
serialize_messages = _serialize_messages


# ============ 公共函数 ============

async def get_or_create_conversation(
    db_path: str,  # 保留参数兼容性，但不再使用
    agent_id: str,
    conversation_id: str,
    user_id: str = "system"
) -> str:
    """
    获取或创建会话（确保会话记录存在）

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        agent_id: 代理标识
        conversation_id: 会话ID
        user_id: 用户ID

    Returns:
        会话ID
    """
    existing = await conversation_dao.get_thread_by_id_any_status(conversation_id)
    if existing:
        if user_id and existing.user_id != user_id:
            raise PermissionError("无权访问该对话")
        return conversation_id

    await conversation_dao.create_thread(
        user_id=user_id,
        thread_id=conversation_id,
        agent_id=agent_id,
        now=datetime.now(),
    )

    logger.info(f"创建新对话: {conversation_id}, agent: {agent_id}")
    return conversation_id


async def create(db_path: str, agent_id: str, user_id: str = "system") -> Dict[str, Any]:
    """
    创建新会话

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        agent_id: 代理标识
        user_id: 用户ID

    Returns:
        {"id": thread_id, "create_time": create_time}
    """
    thread_id = str(uuid.uuid4())
    now = datetime.now()

    await conversation_dao.create_thread(user_id=user_id, thread_id=thread_id, agent_id=agent_id, now=now)

    logger.info(f"创建新对话: {thread_id}, agent: {agent_id}")
    return {"id": thread_id, "create_time": now.strftime('%Y-%m-%d %H:%M:%S')}


async def list(db_path: str, agent_id: str, user_id: str = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """
    获取历史会话列表

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        agent_id: 代理标识
        user_id: 用户ID（可选）
        limit: 返回数量限制
        offset: 跳过数量

    Returns:
        会话列表
    """
    threads = await conversation_dao.list_threads(agent_id=agent_id, user_id=user_id, limit=limit, offset=offset)

    return [_format_thread(t) for t in threads]


async def list_by_agent_ids(agent_ids: List[str], user_id: str = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """获取多个智能体的历史会话列表"""
    if not agent_ids:
        return []

    threads = await conversation_dao.list_threads_by_agent_ids(agent_ids=agent_ids, user_id=user_id, limit=limit, offset=offset)

    return [_format_thread(t) for t in threads]


async def list_all(agent_configs: Dict[str, Dict[str, Any]], user_id: str = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    获取所有智能体的历史会话列表

    Args:
        agent_configs: 所有智能体配置 {agent_id: {"db_path": "..."}}
        user_id: 用户ID（可选）
        limit: 每个智能体返回数量限制

    Returns:
        所有会话列表（按更新时间倒序）
    """
    all_conversations = []

    for agent_id, config in agent_configs.items():
        try:
            # 使用统一数据库，db_path 参数不再需要
            conversations = await list("", agent_id, user_id, limit)
            all_conversations.extend(conversations)
        except Exception as e:
            logger.warning(f"获取 {agent_id} 会话列表失败: {e}")
            continue

    # 按更新时间倒序排序
    all_conversations.sort(key=lambda x: x.get("update_time", ""), reverse=True)

    # 限制总数量
    return all_conversations[:limit]


async def sync_thread_to_db(
    user_id: str,
    thread_id: str,
    agent_id: str,
    title: Optional[str] = None,
    is_new: bool = False
) -> None:
    """异步同步会话到数据库"""
    try:
        await conversation_dao.sync_thread(
            user_id=user_id,
            thread_id=thread_id,
            agent_id=agent_id,
            title=title,
            is_new=is_new,
            now=datetime.now(),
        )
    except Exception as e:
        logger.warning(f"同步会话到 user_threads 失败: {e}")


def _get_checkpoint_datasource_id(values: Dict[str, Any] | None) -> Optional[str]:
    """从 LangGraph state 读取会话首次绑定的数据源组。"""
    datasource_id = str((values or {}).get("conversation_datasource_id") or "").strip()
    return datasource_id or None


async def get_checkpoint_datasource_id(
    conversation_id: str,
    get_graph: Callable,
    get_checkpointer_fn: Callable,
) -> Optional[str]:
    """读取会话 checkpoint 中绑定的数据源组。"""
    if get_checkpointer_fn is None:
        return None
    config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
    async with get_checkpointer_fn() as checkpointer:
        tuple_read, datasource_id = await _get_checkpoint_datasource_id_from_tuple(checkpointer, config)
        if tuple_read or get_graph is None:
            return datasource_id
        graph = get_graph(checkpointer)
        state = await graph.aget_state(config)
    return _get_checkpoint_datasource_id(state.values if state and state.values else None)


async def _get_checkpoint_datasource_id_from_tuple(checkpointer: Any, config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """直接读取 checkpoint channel_values，避免仅查绑定数据源时创建完整 agent。"""
    getter = getattr(checkpointer, "aget_tuple", None)
    if not callable(getter):
        return False, None
    try:
        checkpoint_tuple = await getter(config)
    except Exception as err:
        logger.warning("直接读取 checkpoint 绑定数据源失败，回退 graph state: %s", err)
        return False, None
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None) if checkpoint_tuple else None
    values = checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
    return True, _get_checkpoint_datasource_id(values if isinstance(values, dict) else None)


async def get_detail(
    db_path: str,
    conversation_id: str,
    get_graph: Callable,
    get_checkpointer_fn: Callable,
    user_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    获取会话详情（含历史消息）

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID
        get_graph: 获取graph的函数
        get_checkpointer_fn: 获取checkpointer的函数

    Returns:
        {"id": "...", "title": "...", "messages": [...], "is_favorited": bool} 或 None
    """
    # 从数据库获取会话元信息
    thread = await conversation_dao.get_thread(conversation_id, user_id)
    if not thread:
        return None
    title = thread.thread_title
    is_favorited = bool(thread.is_favorited) if thread.is_favorited is not None else False

    # 事件表是可见历史的权威来源；旧会话尚未迁出时兼容 checkpoint。
    event_history = await conversation_event_service.load_history(
        conversation_id=conversation_id,
        user_id=str(thread.user_id),
    )
    messages = builtins.list(event_history.get("messages") or [])
    history_events = builtins.list(event_history.get("history_events") or [])
    reasoning_segments = builtins.list(event_history.get("reasoning_segments") or [])
    token_usage = None
    state = None

    # 仅当 get_graph 存在时才从 checkpoint 获取消息历史
    # 部分 agent 不使用 LangGraph，有自己的消息存储
    if get_graph is not None:
        config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
        async with get_checkpointer_fn() as checkpointer:
            graph = get_graph(checkpointer)
            state = await graph.aget_state(config)
            if state and state.values:
                raw_messages = state.values.get("messages", [])
                if isinstance(raw_messages, builtins.list) and raw_messages:
                    await conversation_event_service.backfill_checkpoint_history(
                        conversation_id=conversation_id,
                        user_id=str(thread.user_id),
                        operator_name=str(thread.update_by_name or thread.user_id),
                        messages=raw_messages,
                        reasoning_segments=state.values.get("reasoning_segments") or [],
                    )
                    recovered_history = await conversation_event_service.load_history(
                        conversation_id=conversation_id,
                        user_id=str(thread.user_id),
                    )
                    if recovered_history.get("history_events"):
                        messages = builtins.list(recovered_history.get("messages") or [])
                        history_events = builtins.list(recovered_history.get("history_events") or [])
                        reasoning_segments = builtins.list(recovered_history.get("reasoning_segments") or [])
                if not messages:
                    messages = _serialize_messages(raw_messages)
                if not reasoning_segments:
                    reasoning_segments = _serialize_reasoning_segments(state.values.get("reasoning_segments"))
                raw_token_usage = state.values.get("token_usage")
                if isinstance(raw_token_usage, dict):
                    token_usage = sanitize_context_usage_for_response(raw_token_usage)

    return {
        "id": conversation_id,
        "title": title,
        "datasource_id": _get_checkpoint_datasource_id(state.values if state and state.values else None),
        "messages": messages,
        "history_events": history_events,
        "reasoning_segments": reasoning_segments,
        "token_usage": token_usage,
        "is_favorited": is_favorited,
    }


async def delete(db_path: str, conversation_id: str, user_id: Optional[str] = None) -> bool:
    """
    删除会话（逻辑删除，设置 delete_flag = 1）

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID

    Returns:
        是否成功
    """
    deleted = await conversation_dao.soft_delete(conversation_id=conversation_id, user_id=user_id)
    if not deleted:
        return False

    logger.info(f"逻辑删除对话: {conversation_id}")
    return True


async def update_title(db_path: str, conversation_id: str, title: str, user_id: Optional[str] = None) -> bool:
    """
    更新会话标题

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID
        title: 新标题

    Returns:
        是否成功
    """
    updated = await conversation_dao.update_title(conversation_id=conversation_id, title=title, user_id=user_id)
    if not updated:
        return False

    logger.info(f"更新对话标题: {conversation_id} -> {title}")
    return True


async def generate_title(
    db_path: str,
    conversation_id: str,
    get_graph: Callable,
    get_checkpointer_fn: Callable,
    user_id: Optional[str] = None
) -> Optional[str]:
    """
    自动生成会话标题（从第一条消息用LLM生成）

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID
        get_graph: 获取graph的函数
        get_checkpointer_fn: 获取checkpointer的函数

    Returns:
        生成的标题，失败返回None
    """
    if get_graph is None:
        thread = await conversation_dao.get_thread(conversation_id, user_id)
        if not thread:
            return None
        title = str(thread.thread_title or "").strip()
        if title and title != "未命名会话":
            return title

        title = "智能分析会话"
        await update_title(db_path, conversation_id, title, user_id)
        logger.info("无 graph agent 使用默认标题: %s -> %s", conversation_id, title)
        return title

    thread = await conversation_dao.get_thread(conversation_id, user_id)
    if not thread:
        return None

    event_history = await conversation_event_service.load_history(
        conversation_id=conversation_id,
        user_id=str(user_id or thread.user_id),
    )
    first_message = _get_first_user_message_from_history(event_history.get("messages") or [])
    if first_message:
        return await _generate_and_update_title(db_path, conversation_id, first_message, user_id)

    # 旧会话兼容从 checkpoint 获取消息
    config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
    async with get_checkpointer_fn() as checkpointer:
        graph = get_graph(checkpointer)
        state = await graph.aget_state(config)

    if not state or not state.values:
        logger.warning(f"生成标题失败: {conversation_id} - checkpoint 中没有状态")
        return None

    messages = state.values.get("messages", [])
    if not messages:
        logger.warning(f"生成标题失败: {conversation_id} - 没有消息记录")
        return None

    # 获取第一条用户消息
    first_message = _get_first_user_message(messages)
    if not first_message:
        logger.warning(f"生成标题失败: {conversation_id} - 没有用户消息")
        return None

    return await _generate_and_update_title(db_path, conversation_id, first_message, user_id)


def _get_first_user_message_from_history(messages: List[Dict[str, Any]]) -> Optional[str]:
    for message in messages:
        if str(message.get("role") or "") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return None


async def _generate_and_update_title(
    db_path: str,
    conversation_id: str,
    first_message: str,
    user_id: Optional[str],
) -> str:
    """根据首条用户消息生成并更新会话标题。"""
    query = first_message.strip()[:500]
    try:
        llm = _get_title_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", TITLE_GENERATION_PROMPT),
            ("human", "{query}")
        ])

        chain = prompt | llm
        result = await chain.ainvoke({"query": query})
        title = _clean_title(result.content, query)
    except Exception as e:
        emit_llm_connection_alarm_if_needed(
            e,
            ext1={"purpose": "conversation_title", "conversation_id": conversation_id},
        )
        logger.warning(f"LLM生成标题失败: {e}")
        title = _clean_title("", query)

    # 更新到数据库
    await update_title(db_path, conversation_id, title, user_id)

    return title


async def toggle_favorite(db_path: str, conversation_id: str, user_id: Optional[str] = None) -> Optional[bool]:
    """
    切换会话收藏状态

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID

    Returns:
        新的收藏状态（True=已收藏，False=未收藏），会话不存在返回None
    """
    new_favorited = await conversation_dao.toggle_favorite(conversation_id=conversation_id, user_id=user_id)
    if new_favorited is None:
        return None

    logger.info(f"切换收藏状态: {conversation_id} -> {new_favorited}")
    return new_favorited


async def get_agent_id(db_path: str, conversation_id: str, user_id: Optional[str] = None) -> Optional[str]:
    """
    通过会话ID获取对应的agent_id

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID

    Returns:
        agent_id，会话不存在返回None
    """
    return await conversation_dao.get_agent_id(conversation_id=conversation_id, user_id=user_id)
