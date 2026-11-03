"""
会话管理服务
提供会话的创建、查询、更新、删除等业务编排

使用 SQLAlchemy 统一管理业务数据库操作
"""

import base64
import binascii
import builtins
import json
import re
import uuid
from datetime import datetime
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate

from apps.chat_data_app.agent.prompts.conversation_title import TITLE_GENERATION_PROMPT
from apps.chat_data_app.agent.run.conversation_event_service import conversation_event_service
from apps.chat_data_app.daos.conversation_dao import ConversationDao
from apps.chat_data_app.services.llm_config import get_chat_data_llm
from utils.alarm import emit_llm_connection_alarm_if_needed
from utils.database import get_session
from utils.logger import get_logger
from utils.models import UserThread

logger = get_logger(__name__)
# 实例建在本模块：单测通过 monkeypatch 本模块 get_session 打桩，
# 若收敛为 daos 模块级单例会在导入期绑定真实会话工厂、破坏测试缝
conversation_dao = ConversationDao(session_context=lambda: get_session())

PINNED_GROUP_KEY = "system:pinned"
UNCLASSIFIED_GROUP_KEY = "system:unclassified"
DATASOURCE_GROUP_PREFIX = "datasource:"


# ============ 内部函数 ============


def _get_title_llm(model_id: str | None = None) -> BaseChatModel:
    """获取标题生成用的 LLM；源头关闭思考（forced 模型关不掉则由后处理剥离）。

    max_tokens 不能压得太低：forced thinking 模型会先消耗思考 token，
    预算过小时正文为空，表现为「LLM标题产物不可用」。
    """
    return get_chat_data_llm(model=model_id, streaming=False, max_tokens=2048, enable_thinking=False)


def _llm_model_id(llm: Any) -> str:
    """告警用：尽力从 LLM 实例解析模型标识，与主链路告警的 model_id 字段对齐。"""
    return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown")


def _format_thread(t: UserThread, run_status: str | None = None) -> dict[str, Any]:
    """转换会话列表返回结构"""
    result = {
        "id": t.thread_id,
        "title": t.thread_title,
        "agent_id": t.agent_id,
        "datasource_id": t.datasource_id or "",
        "create_time": t.create_time.strftime("%Y-%m-%d %H:%M:%S") if t.create_time else "",
        "update_time": t.update_time.strftime("%Y-%m-%d %H:%M:%S") if t.update_time else "",
        "is_favorited": bool(t.is_favorited) if t.is_favorited is not None else False,
    }
    result["run_status"] = run_status
    return result


async def _format_threads(rows: list[UserThread], user_id: str | None) -> list[dict[str, Any]]:
    statuses = await conversation_dao.list_active_run_statuses(
        [thread.thread_id for thread in rows],
        user_id,
    )
    return [_format_thread(thread, statuses.get(thread.thread_id)) for thread in rows]


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)

# 推理模型有时会把 "Here's a thinking process" 等推理叙述当正文返回；这不是标题。
_REASONING_LEAK_MARKERS = ("heresathinking", "thinkingprocess", "reasoningprocess")


def _strip_inline_think(text: str) -> str:
    """剥内联 <think>...</think>（网关未配 reasoning parser 时思考混在 content 字符串里）。

    无闭合标签视为思考截断，丢弃标签起的全部后续内容。
    """
    text = _THINK_TAG_RE.sub("", text)
    lowered = text.lower()
    if "<think>" in lowered:
        text = text[: lowered.index("<think>")]
    return text.strip()


def _title_response_text(response: Any) -> str:
    """从 AIMessage 响应中提取标题正文：跳过 thinking/reasoning 内容块，剥内联 <think> 标签。"""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return _strip_inline_think(content)
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").lower()
        if "thinking" in block_type or "reasoning" in block_type:
            continue
        text = block.get("text") or block.get("content")
        if isinstance(text, str):
            parts.append(text)
    return _strip_inline_think("\n".join(parts))


def _clean_title(title: str) -> str:
    """清洗标题文本；不可用产出（思考泄漏/过短）返回空串，由调用方决定占位与重试。"""
    title = title.strip()
    # 这里的意图是「去掉其中任意字符」，strip 的字符集语义正合用（非去除整串），故豁免 B005
    title = title.strip('"').strip("'").strip('《》""').strip()  # noqa: B005
    # 移除表情符号和特殊字符
    title = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", title)

    # 泄漏标记对全文检测（截断后 "Heresathinkingp" 之外的英文推理前缀同样要拦）
    lowered = title.lower()
    if any(marker in lowered for marker in _REASONING_LEAK_MARKERS):
        return ""

    title = title[:15]  # 限制最大长度
    if len(title) < 5:
        return ""

    return title


# ============ 公共函数 ============


async def get_or_create_conversation(agent_id: str, conversation_id: str, user_id: str = "system") -> str:
    """
    获取或创建会话（确保会话记录存在）

    Args:
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


async def create(agent_id: str, user_id: str = "system", datasource_id: str | None = None) -> dict[str, Any]:
    """
    创建新会话

    Args:
        agent_id: 代理标识
        user_id: 用户ID
        datasource_id: 建会话即绑定的数据源组（空串/None 表示暂不绑定）

    Returns:
        {"id": thread_id, "create_time": create_time}
    """
    thread_id = str(uuid.uuid4())
    now = datetime.now()

    await conversation_dao.create_thread(
        user_id=user_id,
        thread_id=thread_id,
        agent_id=agent_id,
        datasource_id=str(datasource_id or "").strip(),
        now=now,
    )

    logger.info(f"创建新对话: {thread_id}, agent: {agent_id}")
    return {"id": thread_id, "create_time": now.strftime("%Y-%m-%d %H:%M:%S")}


async def list_conversations(
    agent_id: str, user_id: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict[str, Any]]:
    """
    获取历史会话列表

    Args:
        agent_id: 代理标识
        user_id: 用户ID（可选）
        limit: 返回数量限制
        offset: 跳过数量

    Returns:
        会话列表
    """
    threads = await conversation_dao.list_threads(agent_id=agent_id, user_id=user_id, limit=limit, offset=offset)

    return await _format_threads(threads, user_id)


async def list_by_agent_ids(
    agent_ids: builtins.list[str], user_id: str | None = None, limit: int = 50, offset: int = 0
) -> builtins.list[dict[str, Any]]:
    """获取多个智能体的历史会话列表"""
    if not agent_ids:
        return []

    threads = await conversation_dao.list_threads_by_agent_ids(
        agent_ids=agent_ids, user_id=user_id, limit=limit, offset=offset
    )

    return await _format_threads(threads, user_id)


def _thread_group_key(thread: UserThread) -> str:
    if bool(thread.is_favorited):
        return PINNED_GROUP_KEY
    datasource_id = str(thread.datasource_id or "").strip()
    return f"{DATASOURCE_GROUP_PREFIX}{datasource_id}" if datasource_id else UNCLASSIFIED_GROUP_KEY


def _parse_group_key(group_key: str) -> tuple[str, str | None]:
    normalized = str(group_key or "").strip()
    if normalized == PINNED_GROUP_KEY:
        return "pinned", None
    if normalized == UNCLASSIFIED_GROUP_KEY:
        return "unclassified", None
    if normalized.startswith(DATASOURCE_GROUP_PREFIX):
        datasource_id = normalized[len(DATASOURCE_GROUP_PREFIX) :].strip()
        if datasource_id:
            return "datasource", datasource_id
    raise ValueError("无效的会话分组")


def _encode_group_cursor(thread: UserThread) -> str:
    payload = json.dumps(
        {"update_time": thread.update_time.isoformat() if thread.update_time else "", "id": thread.id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_group_cursor(cursor: str | None) -> tuple[datetime | None, int | None]:
    normalized = str(cursor or "").strip()
    if not normalized:
        return None, None
    try:
        padded = normalized + "=" * (-len(normalized) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        return datetime.fromisoformat(str(payload["update_time"])), int(payload["id"])
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("无效的会话分页游标") from exc


def _format_conversation_group(
    group_key: str,
    rows: builtins.list[UserThread],
    page: int,
    page_size: int,
    statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    datasource_id = group_key[len(DATASOURCE_GROUP_PREFIX) :] if group_key.startswith(DATASOURCE_GROUP_PREFIX) else None
    visible_rows = rows[:page_size]
    has_more = len(rows) > page_size
    return {
        "key": group_key,
        "datasource_id": datasource_id,
        "conversations": [_format_thread(thread, (statuses or {}).get(thread.thread_id)) for thread in visible_rows],
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "next_cursor": _encode_group_cursor(visible_rows[-1]) if has_more and visible_rows else None,
    }


async def list_grouped(
    agent_id: str,
    user_id: str,
    page_size: int,
    *,
    group_key: str | None = None,
    page: int = 1,
    cursor: str | None = None,
) -> dict[str, Any]:
    """返回全部分组首屏，或返回指定分组的一页，不执行总量统计。"""
    if group_key:
        if page > 1 and not cursor:
            raise ValueError("会话分组翻页缺少游标")
        group_type, datasource_id = _parse_group_key(group_key)
        cursor_update_time, cursor_id = _decode_group_cursor(cursor)
        rows = await conversation_dao.list_threads_in_group(
            agent_id=agent_id,
            user_id=user_id,
            group_type=group_type,
            datasource_id=datasource_id,
            limit=page_size + 1,
            cursor_update_time=cursor_update_time,
            cursor_id=cursor_id,
        )
        statuses = await conversation_dao.list_active_run_statuses(
            [thread.thread_id for thread in rows[:page_size]],
            user_id,
        )
        return {"groups": [_format_conversation_group(group_key, rows, page, page_size, statuses)]}

    rows = await conversation_dao.list_thread_group_first_pages(
        agent_id=agent_id,
        user_id=user_id,
        limit_per_group=page_size + 1,
    )
    grouped_rows: dict[str, builtins.list[UserThread]] = {}
    for thread in rows:
        grouped_rows.setdefault(_thread_group_key(thread), []).append(thread)
    statuses = await conversation_dao.list_active_run_statuses(
        [thread.thread_id for thread in rows],
        user_id,
    )
    return {
        "groups": [
            _format_conversation_group(key, group_rows, 1, page_size, statuses)
            for key, group_rows in grouped_rows.items()
        ]
    }


async def list_all(
    agent_configs: dict[str, dict[str, Any]], user_id: str | None = None, limit: int = 50
) -> builtins.list[dict[str, Any]]:
    """
    获取所有智能体的历史会话列表

    Args:
        agent_configs: 所有智能体配置 {agent_id: config}
        user_id: 用户ID（可选）
        limit: 每个智能体返回数量限制

    Returns:
        所有会话列表（按更新时间倒序）
    """
    all_conversations = []

    for agent_id in agent_configs:
        try:
            conversations = await list_conversations(agent_id, user_id, limit)
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


async def get_datasource_id(conversation_id: str, user_id: str) -> str | None:
    """从业务会话表读取绑定的数据源组。"""
    return await conversation_dao.get_datasource_id(
        conversation_id=conversation_id,
        user_id=user_id,
    )


async def resolve_datasource_binding(
    conversation_id: str,
    user_id: str,
    requested_datasource_id: str,
) -> str | None:
    """读取会话数据源绑定，首次请求时在行锁内完成绑定。"""
    return await conversation_dao.resolve_datasource_binding(
        conversation_id=conversation_id,
        user_id=user_id,
        requested_datasource_id=str(requested_datasource_id or "").strip(),
    )


async def get_detail(
    conversation_id: str,
    user_id: str | None = None,
    *,
    include_derived_history: bool = True,
    limit: int | None = None,
    before_sequence: int | None = None,
    strict_limit: bool = False,
) -> dict[str, Any] | None:
    """从业务表和事件表获取会话详情。

    limit 指定时只返回历史事件的尾部窗口，配合 has_more 支持向上翻页。
    """
    # 从数据库获取会话元信息
    thread = await conversation_dao.get_thread(conversation_id, user_id)
    if not thread:
        return None
    title = thread.thread_title
    is_favorited = bool(thread.is_favorited) if thread.is_favorited is not None else False

    event_history = await conversation_event_service.load_history_with_recovery(
        conversation_id=conversation_id,
        user_id=str(thread.user_id),
        include_derived=include_derived_history,
        limit=limit,
        before_sequence=before_sequence,
        strict_limit=strict_limit,
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
        "active_run": event_history.get("active_run"),
        "has_more": bool(event_history.get("has_more")),
        "earliest_sequence_no": event_history.get("earliest_sequence_no"),
        "is_favorited": is_favorited,
    }


async def update_title(conversation_id: str, title: str, user_id: str | None = None) -> bool:
    """
    更新会话标题

    Args:
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


async def generate_title(conversation_id: str, user_id: str | None = None, model_id: str | None = None) -> str | None:
    """根据事件表中的第一条用户消息生成会话标题。

    标题仍是占位符（""/新会话/未命名会话）时才生成，已命名直接返回；
    LLM 生成失败时抛异常且不落库（保持占位符，调用方可下一轮重试）。
    """
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
        return await _generate_and_update_title(conversation_id, first_message, user_id, model_id)
    logger.warning("生成标题失败: %s - 事件表中没有用户消息", conversation_id)
    return None


async def _generate_and_update_title(
    conversation_id: str,
    first_message: str,
    user_id: str | None,
    model_id: str | None = None,
) -> str:
    """根据首条用户消息生成并更新会话标题；产物不可用返回空串（不落库，保持占位符待重试）。"""
    query = first_message.strip()[:500]
    llm = None
    try:
        llm = _get_title_llm(model_id) if model_id else _get_title_llm()
        prompt = ChatPromptTemplate.from_messages([("system", TITLE_GENERATION_PROMPT), ("human", "{query}")])

        chain = prompt | llm
        result = await chain.ainvoke({"query": query})
        # content 可能是分块列表（多模态/工具混合输出），标题场景只取纯文本形态
        title = _clean_title(_title_response_text(result))
        if not title:
            # LLM 调用成功但产物不可用（思考挤占/泄漏等）：与连接失败同策略——
            # 不写降级标题（原始 query 会被原样占用标题位且永不重试），保持
            # 占位符，下一轮 Run 终态时前端会再次触发命名；区别是不发连接告警。
            logger.warning(f"LLM标题产物不可用，保持占位符待重试: {conversation_id}")
            return ""
    except Exception as e:
        emit_llm_connection_alarm_if_needed(
            e,
            ext1={
                "purpose": "conversation_title",
                "conversation_id": conversation_id,
                "model_id": _llm_model_id(llm),
            },
        )
        logger.warning(f"LLM生成标题失败: {e}")
        # 命名失败不写降级标题：保持「未命名会话」占位符状态，下一轮 Run 终态时
        # 前端会再次触发命名，直到成功自停。写降级标题会占用标题位，永久放弃重试。
        raise

    # 更新到数据库
    await update_title(conversation_id, title, user_id)

    return title


async def toggle_favorite(conversation_id: str, user_id: str | None = None) -> bool | None:
    """
    切换会话收藏状态

    Args:
        conversation_id: 会话ID

    Returns:
        新的收藏状态（True=已收藏，False=未收藏），会话不存在返回None
    """
    new_favorited = await conversation_dao.toggle_favorite(conversation_id=conversation_id, user_id=user_id)
    if new_favorited is None:
        return None

    logger.info(f"切换收藏状态: {conversation_id} -> {new_favorited}")
    return new_favorited


async def get_agent_id(conversation_id: str, user_id: str | None = None) -> str | None:
    """
    通过会话ID获取对应的agent_id

    Args:
        conversation_id: 会话ID

    Returns:
        agent_id，会话不存在返回None
    """
    return await conversation_dao.get_agent_id(conversation_id=conversation_id, user_id=user_id)
