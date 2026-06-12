"""
公共会话管理模块
提供会话的创建、查询、更新、删除等通用功能

使用 SQLAlchemy 统一管理数据库操作，支持 SQLite 和 MySQL
"""

import uuid
from datetime import datetime
import re
from typing import Optional, List, Dict, Any, Callable

from sqlalchemy import select, delete as sa_delete, update as sa_update, text
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from langchain_core.prompts import ChatPromptTemplate

from utils.logger import get_logger
from .checkpoint import get_checkpointer
from .database import get_session, get_engine, get_database_url
from .models import UserThread
from .llm import get_llm

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
    return get_llm(temperature=0.3, max_tokens=20)


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

        # 提取时间戳
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
        if result.scalar_one_or_none():
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


async def list(db_path: str, agent_id: str, user_id: str = None, limit: int = 50) -> List[Dict[str, Any]]:
    """
    获取历史会话列表

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        agent_id: 代理标识
        user_id: 用户ID（可选）
        limit: 返回数量限制

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
        query = query.order_by(UserThread.update_time.desc()).limit(limit)

        result = await session.execute(query)
        threads = result.scalars().all()

    return [{
        "id": t.thread_id,
        "title": t.thread_title,
        "agent_id": t.agent_id,
        "create_time": t.create_time.strftime('%Y-%m-%d %H:%M:%S') if t.create_time else "",
        "update_time": t.update_time.strftime('%Y-%m-%d %H:%M:%S') if t.update_time else "",
        "is_favorited": bool(t.is_favorited) if t.is_favorited is not None else False
    } for t in threads]


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


# ============ 公共同步方法（供各模块调用） ============

def sync_thread_to_db(
    user_id: str,
    thread_id: str,
    agent_id: str,
    title: Optional[str] = None,
    is_new: bool = False
) -> None:
    """
    同步会话到 user_threads 表（同步方法，供各模块调用）

    使用同步 SQLite 操作，避免异步事件循环关闭时的问题。

    Args:
        user_id: 用户ID
        thread_id: 会话ID
        agent_id: 智能体ID
        title: 会话标题（可选）
        is_new: 是否是新建会话
    """
    import sqlite3
    from .database import SQLITE_DB_PATH, CHECKPOINTER_TYPE

    # 仅支持 SQLite 同步写入，MySQL 需要异步方式
    if CHECKPOINTER_TYPE != "sqlite":
        logger.warning("sync_thread_to_db 仅支持 SQLite，MySQL 请使用异步方式")
        return

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(SQLITE_DB_PATH, timeout=5)
        try:
            cursor = conn.cursor()
            if is_new:
                # 检查是否已存在
                cursor.execute("SELECT 1 FROM user_threads WHERE thread_id = ?", (thread_id,))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO user_threads (
                            user_id,
                            thread_id,
                            thread_title,
                            agent_id,
                            is_favorited,
                            delete_flag,
                            create_time,
                            update_time,
                            create_by
                        )
                        VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)
                    """, (user_id or "system", thread_id, title or "新会话", agent_id, now, now, user_id or "system"))
            else:
                # 更新会话
                if title:
                    cursor.execute("""
                        UPDATE user_threads SET thread_title = ?, update_time = ?, update_by = ? WHERE thread_id = ?
                    """, (title, now, user_id, thread_id))
                else:
                    cursor.execute("""
                        UPDATE user_threads SET update_time = ?, update_by = ? WHERE thread_id = ?
                    """, (now, user_id, thread_id))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"同步会话到 user_threads 失败: {e}")


async def _async_sync_thread_to_db(
    user_id: str,
    thread_id: str,
    agent_id: str,
    title: Optional[str] = None,
    is_new: bool = False
) -> None:
    """异步同步会话到数据库"""
    engine = None
    try:
        now = datetime.now()
        engine = create_async_engine(get_database_url(), poolclass=NullPool, echo=False)
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with session_factory() as session:
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
            await session.commit()
    except Exception as e:
        logger.warning(f"同步会话到 user_threads 失败: {e}")
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:
                pass


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
    get_checkpointer_fn: Callable
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
        result = await session.execute(
            select(UserThread).where(
                UserThread.thread_id == conversation_id,
                UserThread.delete_flag == 0  # 只查询未删除的
            )
        )
        thread = result.scalar_one_or_none()
        if not thread:
            return None
        title = thread.thread_title
        is_favorited = bool(thread.is_favorited) if thread.is_favorited is not None else False

    # 从 checkpoint 获取消息和中断状态
    messages = []
    pending_tool = None

    # 仅当 get_graph 存在时才从 checkpoint 获取消息历史
    # 某些 agent（如 specialist_app）不使用 LangGraph，有自己的消息存储
    if get_graph is not None:
        config = {"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}}
        async with get_checkpointer_fn() as checkpointer:
            graph = get_graph(checkpointer)
            state = await graph.aget_state(config)
            if state and state.values:
                raw_messages = state.values.get("messages", [])
                messages = _serialize_messages(raw_messages)
                # 检查是否有待确认的工具
                pending_tool = _get_interrupt_info(state)

    return {
        "id": conversation_id,
        "title": title,
        "messages": messages,
        "is_favorited": is_favorited,
        "pending_tool": pending_tool
    }


async def delete(db_path: str, conversation_id: str) -> bool:
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
        result = await session.execute(
            select(UserThread).where(
                UserThread.thread_id == conversation_id,
                UserThread.delete_flag == 0
            )
        )
        if not result.scalar_one_or_none():
            return False

        # 逻辑删除：设置 delete_flag = 1
        await session.execute(
            sa_update(UserThread)
            .where(UserThread.thread_id == conversation_id)
            .values(delete_flag=1, update_time=datetime.now())
        )

    logger.info(f"逻辑删除对话: {conversation_id}")
    return True


async def update_title(db_path: str, conversation_id: str, title: str) -> bool:
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
        result = await session.execute(
            select(UserThread).where(
                UserThread.thread_id == conversation_id,
                UserThread.delete_flag == 0
            )
        )
        if not result.scalar_one_or_none():
            return False

        await session.execute(
            sa_update(UserThread)
            .where(UserThread.thread_id == conversation_id)
            .values(thread_title=title, update_time=datetime.now())
        )

    logger.info(f"更新对话标题: {conversation_id} -> {title}")
    return True


async def generate_title(
    db_path: str,
    conversation_id: str,
    get_graph: Callable,
    get_checkpointer_fn: Callable
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
    await update_title(db_path, conversation_id, title)

    return title


async def toggle_favorite(db_path: str, conversation_id: str) -> Optional[bool]:
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
        result = await session.execute(
            select(UserThread).where(
                UserThread.thread_id == conversation_id,
                UserThread.delete_flag == 0
            )
        )
        thread = result.scalar_one_or_none()
        if not thread:
            return None

        # 切换收藏状态
        current_favorited = thread.is_favorited if thread.is_favorited is not None else 0
        new_favorited = 0 if current_favorited else 1

        # 更新收藏状态
        await session.execute(
            sa_update(UserThread)
            .where(UserThread.thread_id == conversation_id)
            .values(is_favorited=new_favorited)
        )

    logger.info(f"切换收藏状态: {conversation_id} -> {bool(new_favorited)}")
    return bool(new_favorited)


async def get_agent_id(db_path: str, conversation_id: str) -> Optional[str]:
    """
    通过会话ID获取对应的agent_id

    Args:
        db_path: 数据库路径（已废弃，保留兼容）
        conversation_id: 会话ID

    Returns:
        agent_id，会话不存在返回None
    """
    async with get_session() as session:
        result = await session.execute(
            select(UserThread.agent_id).where(
                UserThread.thread_id == conversation_id,
                UserThread.delete_flag == 0  # 只查询未删除的
            )
        )
        row = result.scalar_one_or_none()
        return row
