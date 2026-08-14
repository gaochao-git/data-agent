"""
公共会话路由模块
提供统一的会话管理 API 接口
"""

import inspect
from typing import Callable, Literal, Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Query, Request, status as http_status
from pydantic import BaseModel

from utils.error_codes import ErrorCode
from utils import conversation
from utils.response import business_response
from utils.logger import get_logger
from auth import get_forwarded_user, AuthenticationError

from ..services.agent_run.runtime_skill_snapshot import build_runtime_skill_snapshot_async
from ..services.agent_run.agent_inbox_service import (
    AgentRunConflictError,
    AgentRunNotActiveError,
    SteerMessageNotFoundError,
    SteerMessageNotPendingError,
    agent_inbox_service,
)
from .. import AGENT_ID

logger = get_logger(__name__)


# ============ 请求模型 ============

class CreateConversationRequest(BaseModel):
    """创建会话请求"""
    agent_id: str


class UpdateTitleRequest(BaseModel):
    """更新标题请求"""
    title: str


class CompletionRequest(BaseModel):
    """对话请求"""
    query: str
    agent_id: str
    options: Optional[Dict[str, Any]] = None  # 通用扩展参数
    file_ids: Optional[List[str]] = None  # 本次消息关联的文件ID列表


SteerQueueType = Literal["next-step", "next-turn"]


class SteerRequest(BaseModel):
    """运行中追加纯文本消息。"""
    query: str
    queue_type: Optional[SteerQueueType] = None  # 缺省 next-turn：本轮结束后作为下一轮处理


class SteerMessageUpdateRequest(BaseModel):
    """编辑排队中的追加消息；content 和 queue_type 至少提供一个。"""
    content: Optional[str] = None
    queue_type: Optional[SteerQueueType] = None


# ============ Agent 配置注册 ============

_agent_configs: Dict[str, Dict[str, Any]] = {}
_completion_handlers: Dict[str, Callable] = {}
_default_db_path: Optional[str] = None  # 默认数据库路径（用于通过 conversation_id 查询 agent_id）


def register_agent(
    agent_id: str,
    get_graph: Callable = None,
):
    """
    注册 agent 配置

    Args:
        agent_id: 代理标识
        get_graph: 获取graph的函数（可选，某些 agent 无 LangGraph 图）
    """
    global _default_db_path

    # 使用公共 checkpointer
    from utils.checkpoint import DB_PATH, get_checkpointer

    _agent_configs[agent_id] = {
        "db_path": DB_PATH,  # 保留用于兼容，实际已不使用
        "get_graph": get_graph,
        "get_checkpointer": get_checkpointer,
    }
    # 设置默认数据库路径（用于通过 conversation_id 查询 agent_id）
    if _default_db_path is None:
        _default_db_path = DB_PATH

def register_completion_handler(agent_id: str, handler: Callable):
    """
    注册 completion 处理器

    Args:
        agent_id: 代理标识
        handler: 处理函数，签名为 async def handler(conversation_id, query, options) -> StreamingResponse
    """
    _completion_handlers[agent_id] = handler


def get_agent_config(agent_id: str) -> Optional[Dict[str, Any]]:
    """获取 agent 配置"""
    return _agent_configs.get(agent_id)


async def get_agent_config_by_conversation(conversation_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    通过 conversation_id 获取 agent 配置

    Args:
        conversation_id: 会话ID

    Returns:
        agent 配置，找不到返回 None
    """
    if not _default_db_path:
        return None

    agent_id = await conversation.get_agent_id(_default_db_path, conversation_id, user_id)
    if not agent_id:
        return None

    return _agent_configs.get(agent_id)


async def _get_existing_agent_for_completion(conversation_id: str, request_agent_id: str, user_id: str) -> Optional[str]:
    """校验会话归属并读取本用户会话的 agent_id。"""
    existing_agent_id = await conversation.get_agent_id(_default_db_path, conversation_id, user_id)
    if existing_agent_id and existing_agent_id != request_agent_id:
        return None
    if existing_agent_id:
        return existing_agent_id

    unowned_agent_id = await conversation.get_agent_id(_default_db_path, conversation_id)
    return None if unowned_agent_id else ""


async def _completion_datasource_binding_response(
    conversation_id: str,
    user_id: str,
    requested_datasource_id: str,
):
    """恢复会话已有绑定，并拒绝与它冲突的请求。"""
    bound_datasource_id = await conversation.get_datasource_id(conversation_id, user_id)
    if requested_datasource_id and bound_datasource_id and bound_datasource_id != requested_datasource_id:
        return None, business_response(
            msg="当前会话已绑定其他数据源组，请新建会话后再切换数据源组",
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            status="error",
        )
    return bound_datasource_id or requested_datasource_id or None, None


def _build_completion_handler_kwargs(
    handler: Callable,
    conversation_id: str,
    request: CompletionRequest,
    options: Dict[str, Any],
    req: Request,
    user_id: str,
    username: str,
) -> Dict[str, Any]:
    """按 handler 签名构造 completion 入参。"""
    sig = inspect.signature(handler)
    kwargs = {
        "conversation_id": conversation_id,
        "query": request.query,
        "options": options,
    }
    if 'file_ids' in sig.parameters:
        kwargs['file_ids'] = request.file_ids
    if 'user_id' in sig.parameters:
        kwargs['user_id'] = user_id
    if 'username' in sig.parameters:
        kwargs['username'] = username
    if 'request' in sig.parameters:
        kwargs['request'] = req
    return kwargs


# ============ 认证辅助 ============

def _get_current_user(request: Request):
    """解析可信代理转发的当前用户，不负责 service token 校验。"""
    try:
        return get_forwarded_user(request)
    except AuthenticationError as exc:
        logger.warning(f"认证失败: {exc.message}, path={request.url.path}")
        raise exc


def _get_user_id(request: Request) -> str:
    """获取当前用户ID，未登录抛出 AuthenticationError"""
    user = _get_current_user(request)
    return getattr(user, "user_id", None) or getattr(user, "username", None) or "system"


def _get_current_user_identity(request: Request) -> tuple[str, str]:
    """获取当前用户ID和展示名称。"""
    user = _get_current_user(request)
    user_id = getattr(user, "user_id", None) or getattr(user, "username", None) or "system"
    username = getattr(user, "username", None) or user_id
    return str(user_id), str(username)


# ============ 路由 ============

from . import JSON_API_RESPONSES, SSE_RESPONSES

router = APIRouter(prefix="/conversations", tags=["ChatData 会话"], responses=JSON_API_RESPONSES)


@router.post("")
async def create_conversation(request: CreateConversationRequest, req: Request):
    """
    创建新会话

    POST /api/chatdata/conversations
    Body: {"agent_id": "..."}

    返回：
    {
        "status": "success",
        "data": {"id": "...", "create_time": "..."}
    }
    """
    try:
        user_id, username = _get_current_user_identity(req)

        config = get_agent_config(request.agent_id)
        if not config:
            return business_response(msg=f"未知的 agent_id: {request.agent_id}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
        result = await conversation.create(config["db_path"], request.agent_id, user_id)
        if request.agent_id == AGENT_ID:
            # 创建会话即构建本会话全部可见技能的快照；构建失败不阻断会话创建，
            # 首次运行时 build_runtime_skill_snapshot_async 会幂等重试。
            try:
                await build_runtime_skill_snapshot_async(user_id, str(result.get("id") or ""))
            except Exception as err:
                logger.warning("创建会话时技能快照构建失败，首次运行将重试: %s", err)
        return business_response(data=result, msg="创建对话成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"创建对话失败: {e}", exc_info=True)
        return business_response(msg=f"创建对话失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_CREATE_FAILED, status="error")


@router.get("")
async def list_conversations(
    req: Request,
    agent_id: str = Query(default=None),
    agent_ids: str = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    view: Literal["flat", "grouped"] = Query(default="flat"),
    group_key: Optional[str] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
):
    """
    获取会话列表

    GET /api/chatdata/conversations?agent_id=xxx&page=1&page_size=20

    参数：
    - agent_id: 可选，指定则只返回该智能体的会话，不指定则返回所有会话
    - agent_ids: 可选，逗号分隔，指定则返回这些智能体合并后的会话
    - page: 平铺模式页码；分组模式仅作为前端展示页码
    - page_size: 每页数量
    - view: flat 为平铺列表，grouped 为按置顶/数据源分组
    - group_key: grouped 模式下指定分组时，仅查询该分组下一页
    - cursor: grouped 分组翻页游标，使用上一页返回的 next_cursor

    返回：
    {
        "status": "success",
        "data": {"conversations": [...], "page": 1, "page_size": 20, "has_more": false}
    }

    grouped 首屏返回全部分组的第一页；每个分组包含 has_more 和 next_cursor。
    """
    try:
        user_id, username = _get_current_user_identity(req)

        if view == "grouped":
            if agent_ids or not agent_id:
                return business_response(msg="分组会话查询必须指定单个 agent_id", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
            config = get_agent_config(agent_id)
            if not config:
                return business_response(msg=f"未知的 agent_id: {agent_id}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
            try:
                result = await conversation.list_grouped(
                    agent_id,
                    user_id,
                    page_size,
                    group_key=group_key,
                    page=page,
                    cursor=cursor,
                )
            except ValueError as exc:
                return business_response(msg=str(exc), code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
            return business_response(data=result, msg="获取分组对话列表成功")

        offset = (page - 1) * page_size
        if agent_ids:
            requested_agent_ids = [item.strip() for item in agent_ids.split(",") if item.strip()]
            unknown_agent_ids = [item for item in requested_agent_ids if not get_agent_config(item)]
            if unknown_agent_ids:
                return business_response(msg=f"未知的 agent_id: {','.join(unknown_agent_ids)}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
            rows = await conversation.list_by_agent_ids(requested_agent_ids, user_id, page_size + 1, offset)
            result = {
                "conversations": rows[:page_size],
                "page": page,
                "page_size": page_size,
                "has_more": len(rows) > page_size,
            }
        elif agent_id:
            # 指定 agent_id，使用该智能体的数据库
            config = get_agent_config(agent_id)
            if not config:
                return business_response(msg=f"未知的 agent_id: {agent_id}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
            rows = await conversation.list(config["db_path"], agent_id, user_id, page_size + 1, offset)
            result = {
                "conversations": rows[:page_size],
                "page": page,
                "page_size": page_size,
                "has_more": len(rows) > page_size,
            }
        else:
            # 不指定 agent_id，查询所有智能体的会话
            result = await conversation.list_all(_agent_configs, user_id, page_size)
        return business_response(data=result, msg="获取对话列表成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"获取对话列表失败: {e}", exc_info=True)
        return business_response(msg=f"获取对话列表失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_LIST_FAILED, status="error")


@router.get("/{conversation_id}")
async def get_conversation_detail(conversation_id: str, req: Request):
    """
    获取会话详情

    GET /api/chatdata/conversations/{id}

    返回：
    {
        "status": "success",
        "data": {"id": "...", "title": "...", "messages": [...], "is_favorited": bool}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        result = await conversation.get_detail(
            conversation_id,
            user_id
        )
        if not result:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data=result, msg="获取对话详情成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"获取对话详情失败: {e}", exc_info=True)
        return business_response(msg=f"获取对话详情失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED, status="error")


@router.put("/{conversation_id}/title")
async def update_conversation_title(conversation_id: str, request: UpdateTitleRequest, req: Request):
    """
    修改会话标题

    PUT /api/chatdata/conversations/{id}/title
    Body: {"title": "新标题"}

    返回：
    {
        "status": "success",
        "data": {"id": "...", "title": "..."}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        title = request.title.strip()[:20]
        if not title:
            return business_response(msg="标题不能为空", code=ErrorCode.CHATDATA_REQUIRED_FIELD, status="error")

        success = await conversation.update_title(config["db_path"], conversation_id, title, user_id)
        if not success:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data={"id": conversation_id, "title": title}, msg="标题更新成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"更新标题失败: {e}", exc_info=True)
        return business_response(msg=f"更新标题失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_TITLE_UPDATE_FAILED, status="error")


@router.post("/{conversation_id}/title")
async def generate_conversation_title(conversation_id: str, req: Request):
    """
    自动生成会话标题

    POST /api/chatdata/conversations/{id}/title

    返回：
    {
        "status": "success",
        "data": {"id": "...", "title": "..."}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        title = await conversation.generate_title(
            config["db_path"],
            conversation_id,
            user_id
        )
        if not title:
            return business_response(msg="无法生成标题，对话不存在或无消息", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data={"id": conversation_id, "title": title}, msg="标题生成成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"生成标题失败: {e}", exc_info=True)
        return business_response(msg=f"生成标题失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_TITLE_GENERATE_FAILED, status="error")


@router.post("/{conversation_id}/favorite")
async def toggle_conversation_favorite(conversation_id: str, req: Request):
    """
    切换会话收藏状态

    POST /api/chatdata/conversations/{id}/favorite

    返回：
    {
        "status": "success",
        "data": {"id": "...", "is_favorited": true/false}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        is_favorited = await conversation.toggle_favorite(config["db_path"], conversation_id, user_id)
        if is_favorited is None:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(
            data={"id": conversation_id, "is_favorited": is_favorited},
            msg="收藏成功" if is_favorited else "取消收藏成功"
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"切换收藏状态失败: {e}", exc_info=True)
        return business_response(msg=f"切换收藏状态失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_FAVORITE_FAILED, status="error")


@router.post("/{conversation_id}/completion", responses=SSE_RESPONSES)
async def completion(conversation_id: str, request: CompletionRequest, req: Request):
    """
    对话（发送消息）

    POST /api/chatdata/conversations/{id}/completion
    Body: {"query": "...", "agent_id": "...", "options": {...}}

    返回：StreamingResponse (SSE)
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        if not request.query:
            return business_response(msg="消息不能为空", code=ErrorCode.CHATDATA_REQUIRED_FIELD, status="error")

        user_id, username = _get_current_user_identity(req)
        existing_agent_id = await _get_existing_agent_for_completion(conversation_id, request.agent_id, user_id)
        if existing_agent_id is None:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        handler = _completion_handlers.get(request.agent_id)
        if not handler:
            return business_response(msg=f"未知的 agent_id: {request.agent_id}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
        if not existing_agent_id:
            await conversation.get_or_create_conversation("", request.agent_id, conversation_id, user_id)
        options = dict(request.options or {})
        requested_datasource_id = str(options.get("datasource_id") or "").strip()
        bound_datasource_id, datasource_binding_response = await _completion_datasource_binding_response(
            conversation_id,
            user_id,
            requested_datasource_id,
        )
        if datasource_binding_response:
            return datasource_binding_response
        if bound_datasource_id:
            options["datasource_id"] = bound_datasource_id

        # 调用对应的 completion 处理器
        kwargs = _build_completion_handler_kwargs(handler, conversation_id, request, options, req, user_id, username)

        return await handler(**kwargs)
    except AgentRunConflictError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_AGENT_RUN_ACTIVE,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"对话失败: {e}", exc_info=True)
        return business_response(msg=f"对话失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_COMPLETION_FAILED, status="error")


@router.post("/{conversation_id}/steer")
async def steer(conversation_id: str, request: SteerRequest, req: Request):
    """向当前用户正在运行的 ChatData Agent 追加一条纯文本消息。"""
    content = request.query.strip()
    if not content:
        return business_response(
            msg="消息不能为空",
            code=ErrorCode.CHATDATA_REQUIRED_FIELD,
            status="error",
            http_status_code=http_status.HTTP_400_BAD_REQUEST,
        )
    try:
        user_id, username = _get_current_user_identity(req)
        existing_agent_id = await _get_existing_agent_for_completion(conversation_id, AGENT_ID, user_id)
        if existing_agent_id != AGENT_ID:
            return business_response(
                msg="对话不存在",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error",
                http_status_code=http_status.HTTP_404_NOT_FOUND,
            )
        message_id = await agent_inbox_service.enqueue_message(
            conversation_id,
            user_id,
            username,
            content,
            queue_type=request.queue_type or "next-turn",
        )
        return business_response(data={"message_id": message_id}, msg="消息已发送")
    except AgentRunNotActiveError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_AGENT_RUN_NOT_ACTIVE,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("运行中追加消息失败: conversation_id=%s", conversation_id, exc_info=True)
        return business_response(
            msg="运行中追加消息失败，请稍后重试",
            code=ErrorCode.CHATDATA_STEER_ENQUEUE_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{conversation_id}/steer")
async def list_steer_messages(conversation_id: str, req: Request):
    """读取当前会话仍在排队的追加消息与运行状态，供前端刷新后恢复展示。"""
    try:
        user_id, _username = _get_current_user_identity(req)
        queue = await agent_inbox_service.list_message_states(conversation_id, user_id)
        return business_response(data=queue, msg="ok")
    except AgentRunNotActiveError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_AGENT_RUN_NOT_ACTIVE,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("读取队列消息失败: conversation_id=%s", conversation_id, exc_info=True)
        return business_response(
            msg="读取队列消息失败，请稍后重试",
            code=ErrorCode.CHATDATA_STEER_LIST_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.put("/{conversation_id}/steer/{message_id}")
async def update_steer_message(
    conversation_id: str,
    message_id: str,
    request: SteerMessageUpdateRequest,
    req: Request,
):
    """编辑排队中的追加消息内容或切换队列类型。"""
    if request.content is None and request.queue_type is None:
        return business_response(
            msg="请提供要修改的内容或队列类型",
            code=ErrorCode.CHATDATA_REQUIRED_FIELD,
            status="error",
            http_status_code=http_status.HTTP_400_BAD_REQUEST,
        )
    if request.content is not None and not request.content.strip():
        return business_response(
            msg="消息不能为空",
            code=ErrorCode.CHATDATA_REQUIRED_FIELD,
            status="error",
            http_status_code=http_status.HTTP_400_BAD_REQUEST,
        )
    try:
        user_id, username = _get_current_user_identity(req)
        updated = await agent_inbox_service.update_pending_message(
            conversation_id,
            user_id,
            username,
            message_id,
            content=request.content,
            queue_type=request.queue_type,
        )
        return business_response(data=updated, msg="消息已更新")
    except SteerMessageNotFoundError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_STEER_MESSAGE_NOT_FOUND,
            status="error",
            http_status_code=http_status.HTTP_404_NOT_FOUND,
        )
    except SteerMessageNotPendingError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_STEER_MESSAGE_NOT_PENDING,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AgentRunNotActiveError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_AGENT_RUN_NOT_ACTIVE,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("编辑队列消息失败: conversation_id=%s message_id=%s", conversation_id, message_id, exc_info=True)
        return business_response(
            msg="编辑队列消息失败，请稍后重试",
            code=ErrorCode.CHATDATA_STEER_UPDATE_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.delete("/{conversation_id}/steer/{message_id}")
async def cancel_steer_message(conversation_id: str, message_id: str, req: Request):
    """删除仍在排队等待注入的追加消息。"""
    try:
        user_id, username = _get_current_user_identity(req)
        await agent_inbox_service.cancel_pending_message(
            conversation_id,
            user_id,
            username,
            message_id,
        )
        return business_response(data={"message_id": message_id}, msg="消息已移除")
    except SteerMessageNotFoundError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_STEER_MESSAGE_NOT_FOUND,
            status="error",
            http_status_code=http_status.HTTP_404_NOT_FOUND,
        )
    except SteerMessageNotPendingError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_STEER_MESSAGE_NOT_PENDING,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AgentRunNotActiveError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_AGENT_RUN_NOT_ACTIVE,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("删除队列消息失败: conversation_id=%s message_id=%s", conversation_id, message_id, exc_info=True)
        return business_response(
            msg="删除队列消息失败，请稍后重试",
            code=ErrorCode.CHATDATA_STEER_CANCEL_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{conversation_id}/context")
async def get_conversation_context(conversation_id: str, req: Request):
    """读取历史会话的轻量上下文。"""
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        datasource_id = await conversation.get_datasource_id(conversation_id, user_id)
        return business_response(data={"conversation_id": conversation_id, "datasource_id": datasource_id}, msg="获取成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"获取对话上下文失败: {e}", exc_info=True)
        return business_response(msg="获取对话上下文失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED, status="error")
