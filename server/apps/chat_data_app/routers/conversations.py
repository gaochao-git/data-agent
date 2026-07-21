"""
公共会话路由模块
提供统一的会话管理 API 接口
"""

import inspect
from typing import Callable, Optional, Dict, Any, List

from fastapi import APIRouter, Query, UploadFile, File, Request
from pydantic import BaseModel

from utils.error_codes import ErrorCode, ensure_defined_error_code
from utils import conversation
from utils import file_manager
from utils.response import business_response
from utils.logger import get_logger
from auth import authenticate_request, AuthenticationError

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


class AgentAvailabilityResult(BaseModel):
    """Agent 可用性检查结果。"""
    enabled: bool = True
    message: str = ""
    code: int = ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED


# ============ Agent 配置注册 ============

_agent_configs: Dict[str, Dict[str, Any]] = {}
_completion_handlers: Dict[str, Callable] = {}
_agent_availability_checkers: Dict[str, Callable[[], bool | AgentAvailabilityResult | Dict[str, Any]]] = {}
_default_db_path: Optional[str] = None  # 默认数据库路径（用于通过 conversation_id 查询 agent_id）


def register_agent(
    agent_id: str,
    get_graph: Callable = None,
    file_base_dir: str = None,
    allowed_extensions: List[str] = None
):
    """
    注册 agent 配置

    Args:
        agent_id: 代理标识
        get_graph: 获取graph的函数（可选，某些 agent 无 LangGraph 图）
        file_base_dir: 文件存储基础目录
        allowed_extensions: 允许上传的文件扩展名列表
    """
    global _default_db_path

    # 使用公共 checkpointer
    from utils.checkpoint import DB_PATH, get_checkpointer

    _agent_configs[agent_id] = {
        "db_path": DB_PATH,  # 保留用于兼容，实际已不使用
        "get_graph": get_graph,
        "get_checkpointer": get_checkpointer,
        "file_base_dir": file_base_dir,
        "allowed_extensions": allowed_extensions,
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


def register_agent_availability_checker(agent_id: str, checker: Callable[[], bool | AgentAvailabilityResult | Dict[str, Any]]):
    """注册 agent 可用性检查器。"""
    _agent_availability_checkers[agent_id] = checker


def _normalize_agent_availability_result(result: bool | AgentAvailabilityResult | Dict[str, Any] | None) -> AgentAvailabilityResult:
    """统一 agent 可用性检查结果。"""
    if isinstance(result, AgentAvailabilityResult):
        return result
    if isinstance(result, dict):
        return AgentAvailabilityResult(
            enabled=bool(result.get("enabled", True)),
            message=str(result.get("message") or ""),
            code=ensure_defined_error_code(result.get("code") or ErrorCode.CHATDATA_DATA_ANALYZE_DISABLED),
        )
    return AgentAvailabilityResult(enabled=bool(True if result is None else result))


def get_agent_availability(agent_id: str) -> AgentAvailabilityResult:
    """读取 agent 可用性，未注册检查器时默认可用。"""
    checker = _agent_availability_checkers.get(agent_id)
    if not checker:
        return AgentAvailabilityResult(enabled=True)
    return _normalize_agent_availability_result(checker())


def _agent_unavailable_response(agent_id: str):
    """构造 agent 不可用响应。"""
    availability = get_agent_availability(agent_id)
    if availability.enabled:
        return None
    return business_response(
        msg=availability.message or "功能暂未开放",
        code=availability.code,
        status="error",
    )


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
    requested_datasource_id: str,
    agent_id: str,
):
    """校验当前会话是否已绑定其他数据源组。"""
    if not requested_datasource_id:
        return None
    agent_config = get_agent_config(agent_id)
    if not agent_config:
        return None

    bound_datasource_id = await conversation.get_checkpoint_datasource_id(
        conversation_id,
        agent_config["get_graph"],
        agent_config["get_checkpointer"],
    )
    if not bound_datasource_id or bound_datasource_id == requested_datasource_id:
        return None
    return business_response(
        msg="当前会话已绑定其他数据源组，请新建会话后再切换数据源组",
        code=ErrorCode.CHATDATA_BAD_REQUEST,
        status="error",
    )


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
    """强制认证，未登录返回 401"""
    try:
        return authenticate_request(request)
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

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", status_code=201)
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
        unavailable_response = _agent_unavailable_response(request.agent_id)
        if unavailable_response:
            return unavailable_response

        result = await conversation.create(config["db_path"], request.agent_id, user_id)
        return business_response(data=result, msg="创建对话成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"创建对话失败: {e}", exc_info=True)
        return business_response(msg=f"创建对话失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_CREATE_FAILED, status="error")


@router.get("")
async def list_conversations(
    req: Request,
    agent_id: str = Query(default=None),
    agent_ids: str = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100)
):
    """
    获取会话列表

    GET /api/chatdata/conversations?agent_id=xxx&page=1&page_size=20

    参数：
    - agent_id: 可选，指定则只返回该智能体的会话，不指定则返回所有会话
    - agent_ids: 可选，逗号分隔，指定则返回这些智能体合并后的会话
    - page: 页码，从 1 开始
    - page_size: 每页数量

    返回：
    {
        "status": "success",
        "data": {"conversations": [...], "page": 1, "page_size": 20, "has_more": false}
    }
    """
    try:
        user_id, username = _get_current_user_identity(req)

        offset = (page - 1) * page_size
        if agent_ids:
            requested_agent_ids = [item.strip() for item in agent_ids.split(",") if item.strip()]
            unknown_agent_ids = [item for item in requested_agent_ids if not get_agent_config(item)]
            if unknown_agent_ids:
                return business_response(msg=f"未知的 agent_id: {','.join(unknown_agent_ids)}", code=ErrorCode.CHATDATA_UNKNOWN_AGENT, status="error")
            enabled_agent_ids = [
                item
                for item in requested_agent_ids
                if get_agent_availability(item).enabled
            ]
            rows = await conversation.list_by_agent_ids(enabled_agent_ids, user_id, page_size + 1, offset) if enabled_agent_ids else []
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
            if get_agent_availability(agent_id).enabled:
                rows = await conversation.list(config["db_path"], agent_id, user_id, page_size + 1, offset)
            else:
                rows = []
            result = {
                "conversations": rows[:page_size],
                "page": page,
                "page_size": page_size,
                "has_more": len(rows) > page_size,
            }
        else:
            # 不指定 agent_id，查询所有智能体的会话
            enabled_agent_configs = {
                item_agent_id: config
                for item_agent_id, config in _agent_configs.items()
                if get_agent_availability(item_agent_id).enabled
            }
            result = await conversation.list_all(enabled_agent_configs, user_id, page_size)
        return business_response(data=result, msg="获取对话列表成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
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
            config["db_path"],
            conversation_id,
            config["get_graph"],
            config["get_checkpointer"],
            user_id
        )
        if not result:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data=result, msg="获取对话详情成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"获取对话详情失败: {e}", exc_info=True)
        return business_response(msg=f"获取对话详情失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED, status="error")


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, req: Request):
    """
    删除会话

    DELETE /api/chatdata/conversations/{id}

    返回：
    {
        "status": "success",
        "data": {"id": "..."}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        success = await conversation.delete(config["db_path"], conversation_id, user_id)
        if not success:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data={"id": conversation_id}, msg="删除成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"删除对话失败: {e}", exc_info=True)
        return business_response(msg=f"删除对话失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_DELETE_FAILED, status="error")


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
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
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
            config["get_graph"],
            config["get_checkpointer"],
            user_id
        )
        if not title:
            return business_response(msg="无法生成标题，对话不存在或无消息", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data={"id": conversation_id, "title": title}, msg="标题生成成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
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
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"切换收藏状态失败: {e}", exc_info=True)
        return business_response(msg=f"切换收藏状态失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_FAVORITE_FAILED, status="error")


@router.post("/{conversation_id}/completion")
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
        unavailable_response = _agent_unavailable_response(request.agent_id)
        if unavailable_response:
            return unavailable_response

        options = dict(request.options or {})
        requested_datasource_id = str(options.get("datasource_id") or "").strip()
        datasource_binding_response = await _completion_datasource_binding_response(
            conversation_id,
            requested_datasource_id,
            request.agent_id,
        )
        if datasource_binding_response:
            return datasource_binding_response

        # 调用对应的 completion 处理器
        kwargs = _build_completion_handler_kwargs(handler, conversation_id, request, options, req, user_id, username)

        return await handler(**kwargs)
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"对话失败: {e}", exc_info=True)
        return business_response(msg=f"对话失败: {str(e)}", code=ErrorCode.CHATDATA_CONVERSATION_COMPLETION_FAILED, status="error")


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

        datasource_id = await conversation.get_checkpoint_datasource_id(
            conversation_id,
            config["get_graph"],
            config["get_checkpointer"],
        )
        return business_response(data={"conversation_id": conversation_id, "datasource_id": datasource_id}, msg="获取成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"获取对话上下文失败: {e}", exc_info=True)
        return business_response(msg="获取对话上下文失败，请稍后重试", code=ErrorCode.CHATDATA_CONVERSATION_DETAIL_FAILED, status="error")


@router.post("/{conversation_id}/files")
async def upload_file(
    conversation_id: str,
    req: Request,
    file: UploadFile = File(...)
):
    """
    上传文件

    POST /api/chatdata/conversations/{id}/files
    Form: file

    返回：
    {
        "status": "success",
        "data": {
            "file_id": "upload_xxx.docx",
            "file_name": "原始文件名.docx",
            "file_path": "/path/to/file",
            "file_size": 1234,
            "mime_type": "application/..."
        }
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        file_base_dir = config.get("file_base_dir")
        if not file_base_dir:
            return business_response(msg="该 agent 未配置文件存储目录", code=ErrorCode.CHATDATA_FILE_STORAGE_NOT_CONFIGURED, status="error")

        allowed_extensions = config.get("allowed_extensions")

        result = await file_manager.save_file(
            base_dir=file_base_dir,
            conversation_id=conversation_id,
            file=file,
            allowed_extensions=allowed_extensions
        )

        return business_response(data=result, msg="文件上传成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_REQUIRED_FIELD, status="error")
    except Exception as e:
        logger.error(f"文件上传失败: {e}", exc_info=True)
        return business_response(msg=f"文件上传失败: {str(e)}", code=ErrorCode.CHATDATA_UPLOAD_FAILED, status="error")


@router.get("/{conversation_id}/files/{file_id}")
async def download_file(
    conversation_id: str,
    file_id: str,
    req: Request,
    preview: bool = Query(False, description="是否预览模式")
):
    """
    下载或预览文件

    GET /api/chatdata/conversations/{id}/files/{file_id}?preview=false

    返回：文件内容
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        file_base_dir = config.get("file_base_dir")
        if not file_base_dir:
            return business_response(msg="该 agent 未配置文件存储目录", code=ErrorCode.CHATDATA_FILE_STORAGE_NOT_CONFIGURED, status="error")

        return await file_manager.read_file(
            base_dir=file_base_dir,
            conversation_id=conversation_id,
            file_id=file_id,
            preview=preview
        )
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except ValueError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_REQUIRED_FIELD, status="error")
    except FileNotFoundError as e:
        return business_response(msg=str(e), code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
    except Exception as e:
        logger.error(f"文件下载失败: {e}", exc_info=True)
        return business_response(msg=f"文件下载失败: {str(e)}", code=ErrorCode.CHATDATA_SERVICE_ERROR, status="error")


@router.get("/{conversation_id}/files")
async def list_files(conversation_id: str, req: Request):
    """
    列出会话的所有文件

    GET /api/chatdata/conversations/{id}/files

    返回：
    {
        "status": "success",
        "data": [{"file_id": "...", "file_size": 123, "mime_type": "..."}, ...]
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

        user_id = _get_user_id(req)
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="对话不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        file_base_dir = config.get("file_base_dir")
        if not file_base_dir:
            return business_response(msg="该 agent 未配置文件存储目录", code=ErrorCode.CHATDATA_FILE_STORAGE_NOT_CONFIGURED, status="error")

        files = file_manager.list_files(file_base_dir, conversation_id)
        return business_response(data=files, msg="获取文件列表成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}", exc_info=True)
        return business_response(msg=f"获取文件列表失败: {str(e)}", code=ErrorCode.CHATDATA_FILE_LIST_FAILED, status="error")


# ============ Artifact 接口 ============

@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, req: Request):
    """
    获取 Artifact 数据

    GET /api/chatdata/conversations/artifacts/{id}

    artifact_id 是全局唯一的 UUID，会自动查找对应的类型和数据

    返回：
    {
        "status": "success",
        "data": { ... }  // Artifact 数据内容
    }
    """
    from utils.artifact_store import load_artifact_meta_by_id

    if not artifact_id:
        return business_response(msg="artifact_id 不能为空", code=ErrorCode.CHATDATA_BAD_REQUEST, status="error")

    try:
        user_id = _get_user_id(req)
        artifact = load_artifact_meta_by_id(artifact_id)
        if artifact is None:
            return business_response(msg="Artifact 不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        conversation_id = str(artifact.get("conversation_id") or "").strip()
        if not conversation_id:
            return business_response(msg="Artifact 不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        config = await get_agent_config_by_conversation(conversation_id, user_id)
        if not config:
            return business_response(msg="Artifact 不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")
        data = artifact.get("data")
        if data is None:
            return business_response(msg="Artifact 不存在", code=ErrorCode.CHATDATA_NOT_FOUND, status="error")

        return business_response(data=data, msg="获取成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=ErrorCode.CHATDATA_AUTHENTICATION_FAILED, status="error")
    except Exception as e:
        logger.error(f"获取 Artifact 失败: {e}", exc_info=True)
        return business_response(msg=f"获取 Artifact 失败: {str(e)}", code=ErrorCode.CHATDATA_ARTIFACT_GET_FAILED, status="error")
