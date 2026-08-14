"""
公共会话管理模块
提供会话的创建、查询、更新、删除等通用功能

使用 SQLAlchemy 统一管理业务数据库操作
"""

import uuid
from datetime import datetime
import re
from typing import Optional, List, Dict, Any

from langchain_core.prompts import ChatPromptTemplate

from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.logger import get_logger
from apps.chat_data_app.daos.conversation_dao import ConversationDao
from apps.chat_data_app.services.agent_run.conversation_event_service import conversation_event_service
from apps.chat_data_app.services.llm_config import get_chat_data_llm
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
        "datasource_id": t.datasource_id or "",
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

    # 收藏会话置顶，其次按更新时间倒序排序
    all_conversations.sort(
        key=lambda x: (bool(x.get("is_favorited")), x.get("update_time", "")),
        reverse=True,
    )

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


async def get_datasource_id(conversation_id: str, user_id: str) -> Optional[str]:
    """从业务会话表读取绑定的数据源组。"""
    return await conversation_dao.get_datasource_id(
        conversation_id=conversation_id,
        user_id=user_id,
    )


async def resolve_datasource_binding(
    conversation_id: str,
    user_id: str,
    requested_datasource_id: str,
) -> Optional[str]:
    """读取会话数据源绑定，首次请求时在行锁内完成绑定。"""
    return await conversation_dao.resolve_datasource_binding(
        conversation_id=conversation_id,
        user_id=user_id,
        requested_datasource_id=str(requested_datasource_id or "").strip(),
    )


async def get_detail(
    conversation_id: str,
    user_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """从业务表和事件表获取会话详情。"""
    # 从数据库获取会话元信息
    thread = await conversation_dao.get_thread(conversation_id, user_id)
    if not thread:
        return None
    title = thread.thread_title
    is_favorited = bool(thread.is_favorited) if thread.is_favorited is not None else False

    event_history = await conversation_event_service.load_history(
        conversation_id=conversation_id,
        user_id=str(thread.user_id),
    )
    messages = [*(event_history.get("messages") or [])]
    history_events = [*(event_history.get("history_events") or [])]
    reasoning_segments = [*(event_history.get("reasoning_segments") or [])]
    return {
        "id": conversation_id,
        "title": title,
        "datasource_id": str(thread.datasource_id or "").strip() or None,
        "messages": messages,
        "history_events": history_events,
        "reasoning_segments": reasoning_segments,
        "token_usage": event_history.get("token_usage"),
        "is_favorited": is_favorited,
    }


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
    user_id: Optional[str] = None
) -> Optional[str]:
    """根据事件表中的第一条用户消息生成会话标题。"""
    thread = await conversation_dao.get_thread(conversation_id, user_id)
    if not thread:
        return None

    existing_title = str(thread.thread_title or "").strip()
    if existing_title not in {"", "新会话", "未命名会话"}:
        return existing_title

    first_message = await conversation_event_service.get_first_user_message(
        conversation_id=conversation_id,
        user_id=str(user_id or thread.user_id),
    )
    if first_message:
        return await _generate_and_update_title(db_path, conversation_id, first_message, user_id)
    logger.warning("生成标题失败: %s - 事件表中没有用户消息", conversation_id)
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
