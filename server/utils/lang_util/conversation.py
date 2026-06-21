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

from sqlalchemy import select, update as sa_update
from langchain_core.prompts import ChatPromptTemplate

from utils.logger import get_logger
from apps.chat_data_app.services.llm_config import get_chat_data_llm
from .checkpoint import get_checkpointer
from .database import get_session
from .models import UserThread

logger = get_logger(__name__)


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
    return get_chat_data_llm(streaming=False, temperature=0.3, max_tokens=30)


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


def _serialize_task_stages(task_stages: Any) -> List[Dict[str, Any]]:
    """序列化任务阶段状态，供前端恢复阶段流展示。"""
    if not isinstance(task_stages, builtins.list):
        return []
    latest_run_id = ""
    for item in task_stages:
        if isinstance(item, dict) and str(item.get("run_id") or "").strip():
            latest_run_id = str(item.get("run_id") or "").strip()
    serialized = []
    for item in task_stages:
        if not isinstance(item, dict):
            continue
        run_id = str(item.get("run_id") or "").strip()
        if latest_run_id and run_id != latest_run_id:
            continue
        stage = str(item.get("stage") or "").strip()
        status = str(item.get("status") or "").strip()
        if not stage or not status:
            continue
        stage_data = {
            "stage": stage,
            "status": status,
        }
        for key in ("title", "content"):
            value = item.get(key)
            if isinstance(value, str):
                stage_data[key] = value
        source = item.get("source")
        if source in {"reasoning", "task_stage"}:
            stage_data["source"] = source
        steps = item.get("steps")
        if isinstance(steps, builtins.list):
            stage_data["steps"] = [str(step) for step in steps if str(step).strip()]
        serialized.append(stage_data)
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
        serialized.append({
            "id": str(item.get("id") or f"reasoning-history-{index}"),
            "content": content,
            "tool_call_count": normalized_tool_call_count,
        })
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
    async with get_session() as session:
        # 检查是否已存在
        result = await session.execute(
            select(UserThread).where(UserThread.thread_id == conversation_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            if user_id and existing.user_id != user_id:
                raise PermissionError("无权访问该对话")
            return conversation_id

        # 不存在则创建
        now = datetime.now()
        thread = UserThread(
            user_id=user_id,
            thread_id=conversation_id,
            agent_id=agent_id,
            create_time=now,
            update_time=now,
            create_by=user_id,
        )
        session.add(thread)

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

    async with get_session() as session:
        thread = UserThread(
            user_id=user_id,
            thread_id=thread_id,
            agent_id=agent_id,
            create_time=now,
            update_time=now,
            create_by=user_id,
        )
        session.add(thread)

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
    async with get_session() as session:
        query = select(UserThread).where(
            UserThread.agent_id == agent_id,
            UserThread.delete_flag == 0  # 只查询未删除的
        )
        if user_id:
            query = query.where(UserThread.user_id == user_id)
        query = query.order_by(UserThread.update_time.desc(), UserThread.id.desc()).offset(max(offset, 0)).limit(limit)

        result = await session.execute(query)
        threads = result.scalars().all()

    return [_format_thread(t) for t in threads]


async def list_by_agent_ids(agent_ids: List[str], user_id: str = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """获取多个智能体的历史会话列表"""
    if not agent_ids:
        return []

    async with get_session() as session:
        query = select(UserThread).where(
            UserThread.agent_id.in_(agent_ids),
            UserThread.delete_flag == 0
        )
        if user_id:
            query = query.where(UserThread.user_id == user_id)
        query = query.order_by(UserThread.update_time.desc(), UserThread.id.desc()).offset(max(offset, 0)).limit(limit)

        result = await session.execute(query)
        threads = result.scalars().all()

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
        now = datetime.now()
        async with get_session() as session:
            if is_new:
                # 检查是否已存在
                result = await session.execute(
                    select(UserThread).where(UserThread.thread_id == thread_id)
                )
                if not result.scalar_one_or_none():
                    thread = UserThread(
                        user_id=user_id or "system",
                        thread_id=thread_id,
                        thread_title=title or "新会话",
                        agent_id=agent_id,
                        delete_flag=0,
                        create_time=now,
                        update_time=now,
                        create_by=user_id or "system",
                    )
                    session.add(thread)
            else:
                # 更新会话
                if title:
                    await session.execute(
                        sa_update(UserThread)
                        .where(UserThread.thread_id == thread_id)
                        .values(thread_title=title, update_time=now, update_by=user_id)
                    )
                else:
                    await session.execute(
                        sa_update(UserThread)
                        .where(UserThread.thread_id == thread_id)
                        .values(update_time=now, update_by=user_id)
                    )
    except Exception as e:
        logger.warning(f"同步会话到 user_threads 失败: {e}")


def _get_interrupt_info(state) -> Optional[Dict[str, Any]]:
    """
    从状态中提取中断信息（由工具内部的 interrupt() 触发）

    Returns:
        如果有中断: {"tool_name", "tool_call_id", "args", "message", ...}
        否则: None
    """
    if not state or not state.tasks:
        return None

    # 从 tasks 中找到第一个 interrupt
    for task in state.tasks:
        if not (hasattr(task, 'interrupts') and task.interrupts):
            continue

        interrupt = task.interrupts[0]  # 取第一个中断
        if not hasattr(interrupt, 'value'):
            continue

        value = interrupt.value
        if not isinstance(value, dict):
            continue

        # 从最后一条消息的 tool_calls 中获取 tool_call_id 和 tool_name
        tool_call_id = None
        tool_name = None
        messages = state.values.get("messages", []) if state.values else []
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                tc = last_msg.tool_calls[0]
                tool_call_id = tc.get("id")
                tool_name = tc.get("name")

        # 返回中断信息
        return {
            "tool_name": tool_name or "unknown_tool",
            "tool_call_id": tool_call_id,
            "args": value.get("args", {}),
            "message": value.get("message"),
        }

    return None


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
        {"id": "...", "title": "...", "messages": [...], "is_favorited": bool, "pending_tool": {...} | null} 或 None
    """
    # 从数据库获取会话元信息
    async with get_session() as session:
        conditions = [
            UserThread.thread_id == conversation_id,
            UserThread.delete_flag == 0,  # 只查询未删除的
        ]
        if user_id:
            conditions.append(UserThread.user_id == user_id)
        result = await session.execute(select(UserThread).where(*conditions))
        thread = result.scalar_one_or_none()
        if not thread:
            return None
        title = thread.thread_title
        is_favorited = bool(thread.is_favorited) if thread.is_favorited is not None else False

    # 从 checkpoint 获取消息和中断状态
    messages = []
    task_stages = []
    reasoning_segments = []
    pending_tool = None

    # 仅当 get_graph 存在时才从 checkpoint 获取消息历史
    # 部分 agent 不使用 LangGraph，有自己的消息存储
    if get_graph is not None:
        config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
        async with get_checkpointer_fn() as checkpointer:
            graph = get_graph(checkpointer)
            state = await graph.aget_state(config)
            if state and state.values:
                raw_messages = state.values.get("messages", [])
                messages = _serialize_messages(raw_messages)
                task_stages = _serialize_task_stages(state.values.get("task_stages"))
                reasoning_segments = _serialize_reasoning_segments(state.values.get("reasoning_segments"))
                # 检查是否有待确认的工具
                pending_tool = _get_interrupt_info(state)

    return {
        "id": conversation_id,
        "title": title,
        "messages": messages,
        "task_stages": task_stages,
        "reasoning_segments": reasoning_segments,
        "is_favorited": is_favorited,
        "pending_tool": pending_tool
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
    async with get_session() as session:
        # 检查会话是否存在且未删除
        conditions = [
            UserThread.thread_id == conversation_id,
            UserThread.delete_flag == 0,
        ]
        if user_id:
            conditions.append(UserThread.user_id == user_id)
        result = await session.execute(select(UserThread).where(*conditions))
        if not result.scalar_one_or_none():
            return False

        # 逻辑删除：设置 delete_flag = 1
        update_conditions = [UserThread.thread_id == conversation_id]
        if user_id:
            update_conditions.append(UserThread.user_id == user_id)
        await session.execute(
            sa_update(UserThread)
            .where(*update_conditions)
            .values(delete_flag=1, update_time=datetime.now(), update_by=user_id or "")
        )

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
    async with get_session() as session:
        # 检查会话是否存在且未删除
        conditions = [
            UserThread.thread_id == conversation_id,
            UserThread.delete_flag == 0,
        ]
        if user_id:
            conditions.append(UserThread.user_id == user_id)
        result = await session.execute(select(UserThread).where(*conditions))
        if not result.scalar_one_or_none():
            return False

        update_conditions = [UserThread.thread_id == conversation_id]
        if user_id:
            update_conditions.append(UserThread.user_id == user_id)
        await session.execute(
            sa_update(UserThread)
            .where(*update_conditions)
            .values(thread_title=title, update_time=datetime.now(), update_by=user_id or "")
        )

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
        async with get_session() as session:
            conditions = [
                UserThread.thread_id == conversation_id,
                UserThread.delete_flag == 0,
            ]
            if user_id:
                conditions.append(UserThread.user_id == user_id)
            result = await session.execute(select(UserThread).where(*conditions))
            thread = result.scalar_one_or_none()
            if not thread:
                return None
            title = str(thread.thread_title or "").strip()
            if title and title != "未命名会话":
                return title

        title = "智能分析会话"
        await update_title(db_path, conversation_id, title, user_id)
        logger.info("无 graph agent 使用默认标题: %s -> %s", conversation_id, title)
        return title

    # 从 checkpoint 获取消息
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

    # 使用 LLM 生成标题
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
    async with get_session() as session:
        # 检查会话是否存在且未删除
        conditions = [
            UserThread.thread_id == conversation_id,
            UserThread.delete_flag == 0,
        ]
        if user_id:
            conditions.append(UserThread.user_id == user_id)
        result = await session.execute(select(UserThread).where(*conditions))
        thread = result.scalar_one_or_none()
        if not thread:
            return None

        # 切换收藏状态
        current_favorited = thread.is_favorited if thread.is_favorited is not None else 0
        new_favorited = 0 if current_favorited else 1

        # 更新收藏状态
        update_conditions = [UserThread.thread_id == conversation_id]
        if user_id:
            update_conditions.append(UserThread.user_id == user_id)
        await session.execute(
            sa_update(UserThread)
            .where(*update_conditions)
            .values(is_favorited=new_favorited)
        )

    logger.info(f"切换收藏状态: {conversation_id} -> {bool(new_favorited)}")
    return bool(new_favorited)


async def get_agent_id(db_path: str, conversation_id: str, user_id: Optional[str] = None) -> Optional[str]:
    """
    通过会话ID获取对应的agent_id

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID

    Returns:
        agent_id，会话不存在返回None
    """
    async with get_session() as session:
        conditions = [
            UserThread.thread_id == conversation_id,
            UserThread.delete_flag == 0,  # 只查询未删除的
        ]
        if user_id:
            conditions.append(UserThread.user_id == user_id)
        result = await session.execute(select(UserThread.agent_id).where(*conditions))
        row = result.scalar_one_or_none()
        return row
