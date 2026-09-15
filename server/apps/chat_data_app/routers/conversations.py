"""
公共会话路由模块
提供统一的会话管理 API 接口
"""

import inspect
from typing import Callable, Literal, Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Query, Request, status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from utils.error_codes import ErrorCode
from utils.response import business_response
from utils.logger import get_logger
from auth import get_forwarded_user, AuthenticationError

from ..agent.workspace.runtime_skill_snapshot import build_runtime_skill_snapshot_async
from ..services import conversation_service as conversation
from ..agent.run.agent_inbox_service import (
    AgentQuestionNotFoundError,
    AgentQuestionNotWaitingError,
    AgentRunConflictError,
    AgentRunNotActiveError,
    SteerMessageNotFoundError,
    SteerMessageNotPendingError,
    agent_inbox_service,
)
from ..agent.run.agent_run_event_service import agent_run_event_service
from ..agent.run.agent_run_runtime import agent_run_runtime
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
    query: str = ""
    agent_id: str
    options: Optional[Dict[str, Any]] = None  # 通用扩展参数
    file_ids: Optional[List[str]] = None  # 本次消息关联的文件ID列表
    queued_message_id: Optional[str] = None  # 停止后从队列中明确执行的消息
    preserve_queued_messages: bool = False  # 新输入作为当前任务，不提前接管旧队列


SteerQueueType = Literal["next-step", "next-turn"]


class SteerRequest(BaseModel):
    """运行中追加纯文本消息。"""
    # 与主链路 USER_INPUT_OFFLOAD_THRESHOLD 对齐：steering 语义是运行中追加短指令，
    # 超长输入会原样进 state/checkpoint 每步重复落库，必须在入口拦下
    query: str = Field(..., min_length=1, max_length=4000)
    queue_type: Optional[SteerQueueType] = None  # 缺省 next-turn：本轮结束后作为下一轮处理


class SteerMessageUpdateRequest(BaseModel):
    """编辑排队中的追加消息；content 和 queue_type 至少提供一个。"""
    # 编辑后的内容同样经 inbox 进 state/checkpoint，与 SteerRequest 同限
    content: Optional[str] = Field(default=None, min_length=1, max_length=4000)
    queue_type: Optional[SteerQueueType] = None


class QuestionAnswerItem(BaseModel):
    id: str
    selected: List[str] = Field(default_factory=list)
    custom: str = ""
    skipped: bool = False


class QuestionAnswerRequest(BaseModel):
    answers: List[QuestionAnswerItem]


# ============ Agent 配置注册 ============

_agent_configs: Dict[str, Dict[str, Any]] = {}
_completion_handlers: Dict[str, Callable] = {}


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
    # 使用公共 checkpointer
    from utils.checkpoint import get_checkpointer

    _agent_configs[agent_id] = {
        "get_graph": get_graph,
        "get_checkpointer": get_checkpointer,
    }

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
    if not _agent_configs:
        return None

    agent_id = await conversation.get_agent_id(conversation_id, user_id)
    if not agent_id:
        return None

    return _agent_configs.get(agent_id)


async def _get_existing_agent_for_completion(conversation_id: str, request_agent_id: str, user_id: str) -> Optional[str]:
    """校验会话归属并读取本用户会话的 agent_id。"""
    existing_agent_id = await conversation.get_agent_id(conversation_id, user_id)
    if existing_agent_id and existing_agent_id != request_agent_id:
        return None
    if existing_agent_id:
        return existing_agent_id

    unowned_agent_id = await conversation.get_agent_id(conversation_id)
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
    if 'queued_message_id' in sig.parameters:
        kwargs['queued_message_id'] = request.queued_message_id
    if 'preserve_queued_messages' in sig.parameters:
        kwargs['preserve_queued_messages'] = request.preserve_queued_messages
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
        result = await conversation.create(request.agent_id, user_id)
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
        return business_response(msg="创建对话失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_CREATE_FAILED, status="error")


def _flat_conversation_page_result(rows: List[Dict[str, Any]], page: int, page_size: int) -> Dict[str, Any]:
    """构造平铺会话列表分页结果。"""
    return {
        "conversations": rows[:page_size],
        "page": page,
        "page_size": page_size,
        "has_more": len(rows) > page_size,
    }


def _parse_requested_agent_ids(agent_ids: str) -> List[str]:
    """解析逗号分隔的 agent_id 列表。"""
    return [item.strip() for item in agent_ids.split(",") if item.strip()]


def _unknown_agent_ids(requested_agent_ids: List[str]) -> List[str]:
    """筛选配置中不存在的 agent_id。"""
    return [item for item in requested_agent_ids if not get_agent_config(item)]


async def _list_grouped_conversations_response(
    agent_id: Optional[str],
    agent_ids: Optional[str],
    user_id: str,
    page: int,
    page_size: int,
    group_key: Optional[str],
    cursor: Optional[str],
):
    """分组视图会话查询响应。"""
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
            return await _list_grouped_conversations_response(agent_id, agent_ids, user_id, page, page_size, group_key, cursor)

        offset = (page - 1) * page_size
        if agent_ids:
            requested_agent_ids = _parse_requested_agent_ids(agent_ids)
            unknown_agent_ids = _unknown_agent_ids(requested_agent_ids)
            if unknown_agent_ids:
                return business_response(msg=f"未知的 agent_id: {','.join(unknown_agent_ids)}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
            rows = await conversation.list_by_agent_ids(requested_agent_ids, user_id, page_size + 1, offset)
            result = _flat_conversation_page_result(rows, page, page_size)
        elif agent_id:
            # 指定 agent_id，使用该智能体的数据库
            config = get_agent_config(agent_id)
            if not config:
                return business_response(msg=f"未知的 agent_id: {agent_id}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
            rows = await conversation.list(agent_id, user_id, page_size + 1, offset)
            result = _flat_conversation_page_result(rows, page, page_size)
        else:
            # 不指定 agent_id，查询所有智能体的会话
            result = await conversation.list_all(_agent_configs, user_id, page_size)
        return business_response(data=result, msg="获取对话列表成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"获取对话列表失败: {e}", exc_info=True)
        return business_response(msg="获取对话列表失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_LIST_FAILED, status="error")


@router.get("/{conversation_id}")
async def get_conversation_detail(
    conversation_id: str,
    req: Request,
    view: Literal["full", "timeline"] = "full",
    limit: Optional[int] = Query(default=None, ge=1, le=600, description="仅返回最近 limit 条历史事件，用于长会话分页"),
    before_sequence: Optional[int] = Query(default=None, ge=0, description="翻页游标：只返回 sequence_no 小于该值的历史事件"),
):
    """
    获取会话详情

    GET /api/chatdata/conversations/{id}

    返回：
    {
        "status": "success",
        "data": {"id": "...", "title": "...", "messages": [...], "is_favorited": bool}
    }

    limit 省略时返回全量历史（兼容旧行为）；指定后返回尾部窗口：以「轮次闭包」起点收尾的
    连续 sequence_no 区间（并发 run 会让同一轮次的事件在序号上不连续，窗口起点会向下收敛，
    保证页内不出现被切断的轮次，首屏不会渲染出没有提问的回答），
    并附带 has_more / earliest_sequence_no 供向上翻页。
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
            user_id,
            include_derived_history=view == "full",
            limit=limit,
            before_sequence=before_sequence,
        )
        if not result:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        if view == "timeline":
            result.pop("messages", None)
            result.pop("reasoning_segments", None)
        return business_response(data=result, msg="获取对话详情成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"获取对话详情失败: {e}", exc_info=True)
        return business_response(msg="获取对话详情失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED, status="error")


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

        success = await conversation.update_title(conversation_id, title, user_id)
        if not success:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data={"id": conversation_id, "title": title}, msg="标题更新成功")
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"更新标题失败: {e}", exc_info=True)
        return business_response(msg="更新标题失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_TITLE_UPDATE_FAILED, status="error")


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
        return business_response(msg="生成标题失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_TITLE_GENERATE_FAILED, status="error")


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

        is_favorited = await conversation.toggle_favorite(conversation_id, user_id)
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
        return business_response(msg="切换收藏状态失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_FAVORITE_FAILED, status="error")


def _validate_completion_request(conversation_id: str, request: CompletionRequest):
    """校验对话请求基础参数；非法时返回错误响应。"""
    if not conversation_id:
        return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")
    if not request.query and not request.queued_message_id:
        return business_response(msg="消息不能为空", code=ErrorCode.CHATDATA_REQUIRED_FIELD, status="error")
    return None


async def _apply_completion_datasource_binding(conversation_id: str, user_id: str, options: Dict[str, Any]):
    """绑定会话数据源；需要拦截时返回错误响应，否则原地回填 options。"""
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
    return None


@router.post("/{conversation_id}/completion", responses=SSE_RESPONSES)
async def completion(conversation_id: str, request: CompletionRequest, req: Request):
    """
    对话（发送消息）

    POST /api/chatdata/conversations/{id}/completion
    Body: {"query": "...", "agent_id": "...", "options": {...}}

    返回：StreamingResponse (SSE)
    """
    try:
        invalid_request_response = _validate_completion_request(conversation_id, request)
        if invalid_request_response:
            return invalid_request_response

        user_id, username = _get_current_user_identity(req)
        existing_agent_id = await _get_existing_agent_for_completion(conversation_id, request.agent_id, user_id)
        if existing_agent_id is None:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        handler = _completion_handlers.get(request.agent_id)
        if not handler:
            return business_response(msg=f"未知的 agent_id: {request.agent_id}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
        if not existing_agent_id:
            await conversation.get_or_create_conversation(request.agent_id, conversation_id, user_id)
        options = dict(request.options or {})
        binding_response = await _apply_completion_datasource_binding(conversation_id, user_id, options)
        if binding_response:
            return binding_response

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
    except SteerMessageNotFoundError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_STEER_MESSAGE_NOT_FOUND,
            status="error",
            http_status_code=http_status.HTTP_404_NOT_FOUND,
        )
    except SteerMessageNotPendingError as e:
        return business_response(
            msg=str(e),
            code=ErrorCode.CHATDATA_STEER_MESSAGE_NOT_PENDING,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message) from e
    except Exception as e:
        logger.error(f"对话失败: {e}", exc_info=True)
        return business_response(msg="对话失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_COMPLETION_FAILED, status="error")


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


@router.delete("/{conversation_id}/run")
async def cancel_active_run(conversation_id: str, req: Request, run_id: str = Query(..., min_length=1)):
    """停止当前 Agent 执行；未消费的队列消息保留给下一次 completion。"""
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
        cancelled = await agent_run_runtime.cancel_run(
            run_id=run_id,
            conversation_id=conversation_id,
            user_id=user_id,
            operator_name=username,
        )
        return business_response(data={"cancelled": cancelled}, msg="已停止" if cancelled else "当前没有运行中的任务")
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("停止 Agent 运行失败: conversation_id=%s", conversation_id, exc_info=True)
        return business_response(
            msg="停止运行失败，请稍后重试",
            code=ErrorCode.CHATDATA_AGENT_RUN_CANCEL_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{conversation_id}/pending-question")
async def get_pending_question(conversation_id: str, req: Request):
    """读取 checkpoint 中等待当前用户回答的问题。"""
    try:
        user_id, _username = _get_current_user_identity(req)
        question = await agent_inbox_service.get_pending_question(conversation_id, user_id)
        return business_response(data=question, msg="ok")
    except AgentRunNotActiveError:
        return business_response(
            msg="对话不存在",
            code=ErrorCode.CHATDATA_NOT_FOUND,
            status="error",
            http_status_code=http_status.HTTP_404_NOT_FOUND,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("读取待回答问题失败: conversation_id=%s", conversation_id, exc_info=True)
        return business_response(
            msg="读取待回答问题失败，请稍后重试",
            code=ErrorCode.CHATDATA_QUESTION_ANSWER_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


async def _resume_question(
    conversation_id: str,
    question_id: str,
    answer: dict[str, Any],
    req: Request,
):
    user_id, username = _get_current_user_identity(req)
    existing_agent_id = await _get_existing_agent_for_completion(conversation_id, AGENT_ID, user_id)
    if existing_agent_id != AGENT_ID:
        raise AgentQuestionNotFoundError("待回答问题不存在")
    pending = await agent_inbox_service.get_pending_question(conversation_id, user_id)
    turn_id = str((pending or {}).get("turn_id") or "")
    pending_run_id = str((pending or {}).get("run_id") or "")
    if not pending_run_id:
        raise AgentQuestionNotFoundError("待回答问题不存在")
    # The interrupt status is written before question_requested/done are
    # published. Wait for the old segment's done event so a different worker
    # cannot start the resume subscription from inside the old segment.
    if not await agent_run_event_service.wait_for_done(pending_run_id):
        raise AgentQuestionNotWaitingError("当前回答仍在收口，请稍后重试")
    last_event_id = await agent_run_event_service.latest_event_id(pending_run_id)
    run_id, runtime_context = await agent_inbox_service.claim_question_answer(
        conversation_id,
        user_id,
        username,
        question_id,
        answer,
    )
    # 延迟导入用于打破 agent_stream -> routers 注册链的循环依赖。
    from ..agent_stream import resume_chat_data_question_handler
    return await resume_chat_data_question_handler(
        conversation_id=conversation_id,
        user_id=user_id,
        username=username,
        run_id=run_id,
        runtime_context=runtime_context,
        answer=answer,
        turn_id=turn_id,
        last_event_id=last_event_id,
        request=req,
    )


@router.post("/{conversation_id}/questions/{question_id}/answer", responses=SSE_RESPONSES)
async def answer_question(
    conversation_id: str,
    question_id: str,
    request: QuestionAnswerRequest,
    req: Request,
):
    """原子领取待回答问题并恢复原 LangGraph 运行。"""
    try:
        return await _resume_question(
            conversation_id,
            question_id,
            {"answers": [item.model_dump() for item in request.answers]},
            req,
        )
    except ValueError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_BAD_REQUEST,
            status="error",
            http_status_code=http_status.HTTP_400_BAD_REQUEST,
        )
    except AgentQuestionNotFoundError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_QUESTION_NOT_FOUND,
            status="error",
            http_status_code=http_status.HTTP_404_NOT_FOUND,
        )
    except AgentQuestionNotWaitingError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_QUESTION_NOT_WAITING,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("提交问题回答失败: conversation_id=%s", conversation_id, exc_info=True)
        return business_response(
            msg="提交回答失败，请稍后重试",
            code=ErrorCode.CHATDATA_QUESTION_ANSWER_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.post("/{conversation_id}/questions/{question_id}/cancel", responses=SSE_RESPONSES)
async def cancel_question(conversation_id: str, question_id: str, req: Request):
    """取消当前问题并让 Agent 自行决定如何收尾。"""
    try:
        return await _resume_question(conversation_id, question_id, {"cancelled": True, "answers": []}, req)
    except AgentQuestionNotFoundError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_QUESTION_NOT_FOUND,
            status="error",
            http_status_code=http_status.HTTP_404_NOT_FOUND,
        )
    except AgentQuestionNotWaitingError as exc:
        return business_response(
            msg=str(exc),
            code=ErrorCode.CHATDATA_QUESTION_NOT_WAITING,
            status="error",
            http_status_code=http_status.HTTP_409_CONFLICT,
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("取消待回答问题失败: conversation_id=%s", conversation_id, exc_info=True)
        return business_response(
            msg="取消问题失败，请稍后重试",
            code=ErrorCode.CHATDATA_QUESTION_ANSWER_FAILED,
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
        logger.error("读取队列状态失败: conversation_id=%s", conversation_id, exc_info=True)
        return business_response(
            msg="读取队列状态失败，请稍后重试",
            code=ErrorCode.CHATDATA_STEER_LIST_FAILED,
            status="error",
            http_status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@router.get("/{conversation_id}/runs/{run_id}/events", responses=SSE_RESPONSES)
async def stream_run_events(
    conversation_id: str,
    run_id: str,
    req: Request,
):
    """重新订阅后台 Run 的可重放 SSE 事件。"""
    try:
        user_id, _username = _get_current_user_identity(req)
        snapshot = await agent_inbox_service.get_run_snapshot(conversation_id, user_id, run_id)
        if snapshot is None:
            return business_response(
                msg="运行不存在",
                code=ErrorCode.CHATDATA_NOT_FOUND,
                status="error",
                http_status_code=http_status.HTTP_404_NOT_FOUND,
            )

        last_event_id = req.headers.get("Last-Event-ID") or "0-0"

        async def event_stream():
            async for event in agent_run_event_service.stream(
                run_id=run_id,
                last_event_id=last_event_id,
                is_disconnected=req.is_disconnected,
            ):
                yield f"id: {event['stream_id']}\n{event['payload']}"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-ChatData-Run-Id": run_id,
            },
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception:
        logger.error("订阅 Run 事件失败: conversation_id=%s run_id=%s", conversation_id, run_id, exc_info=True)
        return business_response(
            msg="订阅运行事件失败，请稍后重试",
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
