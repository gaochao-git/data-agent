"""
公共会话路由模块
提供统一的会话管理 API 接口
"""

import json
import time
import inspect
from typing import Callable, Optional, Dict, Any, List, AsyncGenerator
from fastapi import APIRouter, Query, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langgraph.types import Command
from langchain_core.messages import AIMessage
from utils.lang_util import conversation
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


class ResumeRequest(BaseModel):
    """恢复中断请求（Human-in-the-Loop 工具确认）"""
    action: str = "approve"  # approve | edit | reject
    data: Optional[Dict[str, Any]] = None  # 修改后的数据（action=edit 时使用）


# ============ Agent 配置注册 ============

_agent_configs: Dict[str, Dict[str, Any]] = {}
_completion_handlers: Dict[str, Callable] = {}
_resume_handlers: Dict[str, Callable] = {}  # resume 处理器（Human-in-the-Loop）
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
    from utils.lang_util.checkpoint import DB_PATH, get_checkpointer

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
    logger.info(f"注册 agent 配置: {agent_id}")


def register_completion_handler(agent_id: str, handler: Callable):
    """
    注册 completion 处理器

    Args:
        agent_id: 代理标识
        handler: 处理函数，签名为 async def handler(conversation_id, query, options) -> StreamingResponse
    """
    _completion_handlers[agent_id] = handler
    logger.info(f"注册 completion 处理器: {agent_id}")


def register_resume_handler(agent_id: str, handler: Callable):
    """
    注册 resume 处理器（Human-in-the-Loop 工具确认）

    注意：通常不需要注册，通用 resume 实现会自动使用 get_graph 和 get_checkpointer。
    只有当 agent 需要特殊的 resume 逻辑时才需要注册。

    Args:
        agent_id: 代理标识
        handler: 处理函数，签名为 async def handler(conversation_id, action, data) -> StreamingResponse
    """
    _resume_handlers[agent_id] = handler
    logger.info(f"注册 resume 处理器: {agent_id}")


async def _generic_resume_stream(
    conversation_id: str,
    action: str,
    data: Optional[Dict[str, Any]],
    get_graph: Callable,
    get_checkpointer: Callable
) -> AsyncGenerator[str, None]:
    """
    通用 resume 流式处理

    根据 LangGraph 官方规范，使用 Command(resume=...) 恢复被中断的图执行。
    """
    try:
        config = {
            "configurable": {
                "thread_id": conversation_id,
                "checkpoint_ns": ""
            }
        }

        human_input = {
            "action": action,
            "data": data or {}
        }

        async with get_checkpointer() as checkpointer:
            graph = get_graph(checkpointer)

            event_count = 0
            async for event in graph.astream(
                Command(resume=human_input),
                config=config,
                stream_mode=["custom", "messages"],
            ):
                event_count += 1
                # 处理 custom 事件（task_progress 等）
                if isinstance(event, tuple) and len(event) == 2:
                    event_type, event_data = event
                    if event_type == "custom" and isinstance(event_data, dict):
                        yield f"event: {event_data.get('event', 'message')}\ndata: {json.dumps(event_data.get('data', event_data), ensure_ascii=False)}\n\n"

            logger.info(f"[通用resume] astream 循环结束，共收到 {event_count} 个事件")

            # 获取最终状态
            final_snapshot = await graph.aget_state(config)
            if not final_snapshot or not final_snapshot.values:
                yield f"event: error\ndata: {{\"message\": \"未获取到最终状态\"}}\n\n"
                return

            final_state = final_snapshot.values

            # 检查是否再次被中断
            if final_snapshot.next:
                logger.info(f"图再次被中断，等待节点: {final_snapshot.next}")

                interrupt_data = None
                if final_snapshot.tasks:
                    for task in final_snapshot.tasks:
                        if hasattr(task, 'interrupts') and task.interrupts:
                            interrupt_data = task.interrupts[0].value if task.interrupts else None
                            break

                yield f"event: interrupt\ndata: {json.dumps({'next_node': list(final_snapshot.next), 'interrupt_data': interrupt_data, 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False)}\n\n"
                return

            # 获取最后一条 AIMessage
            messages = final_state.get("messages", [])
            last_message = None
            for msg in reversed(messages):
                if isinstance(msg, AIMessage):
                    last_message = msg
                    break

            if last_message:
                yield f"event: completed\ndata: {json.dumps({'messages': [{'id': getattr(last_message, 'id', None), 'role': 'assistant', 'content': last_message.content, 'additional_kwargs': last_message.additional_kwargs}], 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False)}\n\n"
            else:
                yield f"event: completed\ndata: {json.dumps({'message': '操作成功', 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logger.error(f"[通用resume] 恢复对话失败: {e}", exc_info=True)
        yield f"event: error\ndata: {{\"message\": \"恢复对话失败: {str(e)}\", \"timestamp\": \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"}}\n\n"


def get_agent_config(agent_id: str) -> Optional[Dict[str, Any]]:
    """获取 agent 配置"""
    return _agent_configs.get(agent_id)


async def get_agent_config_by_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    通过 conversation_id 获取 agent 配置

    Args:
        conversation_id: 会话ID

    Returns:
        agent 配置，找不到返回 None
    """
    if not _default_db_path:
        return None

    agent_id = await conversation.get_agent_id(_default_db_path, conversation_id)
    if not agent_id:
        return None

    return _agent_configs.get(agent_id)


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


# ============ 路由 ============

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("", status_code=201)
async def create_conversation(request: CreateConversationRequest, req: Request):
    """
    创建新会话

    POST /api/conversations
    Body: {"agent_id": "..."}

    返回：
    {
        "status": "success",
        "data": {"id": "...", "create_time": "..."}
    }
    """
    try:
        user_id = _get_user_id(req)

        config = get_agent_config(request.agent_id)
        if not config:
            return business_response(msg=f"未知的 agent_id: {request.agent_id}", code=40003, status="error")

        result = await conversation.create(config["db_path"], request.agent_id, user_id)
        return business_response(data=result, msg="创建对话成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=40101, status="error")
    except Exception as e:
        logger.error(f"创建对话失败: {e}", exc_info=True)
        return business_response(msg=f"创建对话失败: {str(e)}", code=50001, status="error")


@router.get("")
async def list_conversations(req: Request, agent_id: str = Query(default=None)):
    """
    获取会话列表

    GET /api/conversations?agent_id=xxx

    参数：
    - agent_id: 可选，指定则只返回该智能体的会话，不指定则返回所有会话

    返回：
    {
        "status": "success",
        "data": [{"id": "...", "title": "...", "agent_id": "...", ...}]
    }
    """
    try:
        user_id = _get_user_id(req)

        if agent_id:
            # 指定 agent_id，使用该智能体的数据库
            config = get_agent_config(agent_id)
            if not config:
                return business_response(msg=f"未知的 agent_id: {agent_id}", code=40003, status="error")
            result = await conversation.list(config["db_path"], agent_id, user_id)
        else:
            # 不指定 agent_id，查询所有智能体的会话
            result = await conversation.list_all(_agent_configs, user_id)
        return business_response(data=result, msg="获取对话列表成功")
    except AuthenticationError as e:
        return business_response(msg=e.message, code=40101, status="error")
    except Exception as e:
        logger.error(f"获取对话列表失败: {e}", exc_info=True)
        return business_response(msg=f"获取对话列表失败: {str(e)}", code=50002, status="error")


@router.get("/{conversation_id}")
async def get_conversation_detail(conversation_id: str):
    """
    获取会话详情

    GET /api/conversations/{id}

    返回：
    {
        "status": "success",
        "data": {"id": "...", "title": "...", "messages": [...], "is_favorited": bool}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        result = await conversation.get_detail(
            config["db_path"],
            conversation_id,
            config["get_graph"],
            config["get_checkpointer"]
        )
        if not result:
            return business_response(msg="对话不存在", code=40404, status="error")

        return business_response(data=result, msg="获取对话详情成功")
    except Exception as e:
        logger.error(f"获取对话详情失败: {e}", exc_info=True)
        return business_response(msg=f"获取对话详情失败: {str(e)}", code=50003, status="error")


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """
    删除会话

    DELETE /api/conversations/{id}

    返回：
    {
        "status": "success",
        "data": {"id": "..."}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        success = await conversation.delete(config["db_path"], conversation_id)
        if not success:
            return business_response(msg="对话不存在", code=40404, status="error")

        return business_response(data={"id": conversation_id}, msg="删除成功")
    except Exception as e:
        logger.error(f"删除对话失败: {e}", exc_info=True)
        return business_response(msg=f"删除对话失败: {str(e)}", code=50004, status="error")


@router.put("/{conversation_id}/title")
async def update_conversation_title(conversation_id: str, request: UpdateTitleRequest):
    """
    修改会话标题

    PUT /api/conversations/{id}/title
    Body: {"title": "新标题"}

    返回：
    {
        "status": "success",
        "data": {"id": "...", "title": "..."}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        title = request.title.strip()[:20]
        if not title:
            return business_response(msg="标题不能为空", code=40002, status="error")

        success = await conversation.update_title(config["db_path"], conversation_id, title)
        if not success:
            return business_response(msg="对话不存在", code=40404, status="error")

        return business_response(data={"id": conversation_id, "title": title}, msg="标题更新成功")
    except Exception as e:
        logger.error(f"更新标题失败: {e}", exc_info=True)
        return business_response(msg=f"更新标题失败: {str(e)}", code=50005, status="error")


@router.post("/{conversation_id}/title")
async def generate_conversation_title(conversation_id: str):
    """
    自动生成会话标题

    POST /api/conversations/{id}/title

    返回：
    {
        "status": "success",
        "data": {"id": "...", "title": "..."}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        title = await conversation.generate_title(
            config["db_path"],
            conversation_id,
            config["get_graph"],
            config["get_checkpointer"]
        )
        if not title:
            return business_response(msg="无法生成标题，对话不存在或无消息", code=40404, status="error")

        return business_response(data={"id": conversation_id, "title": title}, msg="标题生成成功")
    except Exception as e:
        logger.error(f"生成标题失败: {e}", exc_info=True)
        return business_response(msg=f"生成标题失败: {str(e)}", code=50006, status="error")


@router.post("/{conversation_id}/favorite")
async def toggle_conversation_favorite(conversation_id: str):
    """
    切换会话收藏状态

    POST /api/conversations/{id}/favorite

    返回：
    {
        "status": "success",
        "data": {"id": "...", "is_favorited": true/false}
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        is_favorited = await conversation.toggle_favorite(config["db_path"], conversation_id)
        if is_favorited is None:
            return business_response(msg="对话不存在", code=40404, status="error")

        return business_response(
            data={"id": conversation_id, "is_favorited": is_favorited},
            msg="收藏成功" if is_favorited else "取消收藏成功"
        )
    except Exception as e:
        logger.error(f"切换收藏状态失败: {e}", exc_info=True)
        return business_response(msg=f"切换收藏状态失败: {str(e)}", code=50007, status="error")


@router.post("/{conversation_id}/completion")
async def completion(conversation_id: str, request: CompletionRequest, req: Request):
    """
    对话（发送消息）

    POST /api/conversations/{id}/completion
    Body: {"query": "...", "agent_id": "...", "options": {...}}

    返回：StreamingResponse (SSE)
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        if not request.query:
            return business_response(msg="消息不能为空", code=40002, status="error")

        user_id = _get_user_id(req)

        handler = _completion_handlers.get(request.agent_id)
        if not handler:
            return business_response(msg=f"未知的 agent_id: {request.agent_id}", code=40003, status="error")

        # 调用对应的 completion 处理器
        # 使用 inspect 检查 handler 支持哪些参数
        sig = inspect.signature(handler)
        kwargs = {
            "conversation_id": conversation_id,
            "query": request.query,
            "options": request.options,
        }
        if 'file_ids' in sig.parameters:
            kwargs['file_ids'] = request.file_ids
        if 'user_id' in sig.parameters:
            kwargs['user_id'] = user_id
        if 'request' in sig.parameters:
            kwargs['request'] = req

        return await handler(**kwargs)
    except AuthenticationError as e:
        return business_response(msg=e.message, code=40101, status="error")
    except Exception as e:
        logger.error(f"对话失败: {e}", exc_info=True)
        return business_response(msg=f"对话失败: {str(e)}", code=50008, status="error")


@router.post("/{conversation_id}/resume")
async def resume_conversation(conversation_id: str, request: ResumeRequest):
    """
    恢复被中断的会话（Human-in-the-Loop 工具确认）

    POST /api/conversations/{id}/resume
    Body: {"action": "approve" | "edit" | "reject", "data": {...}}

    返回：StreamingResponse (SSE)

    通用实现：根据 conversation_id 自动获取对应 agent 的 graph 和 checkpointer，
    无需每个 agent 单独注册 resume handler。
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        # 通过 conversation_id 获取 agent 配置
        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        # 获取 agent_id
        agent_id = await conversation.get_agent_id(_default_db_path, conversation_id)
        if not agent_id:
            return business_response(msg="无法确定对话的 agent_id", code=40404, status="error")

        # 优先使用自定义的 resume handler（如果有特殊逻辑）
        handler = _resume_handlers.get(agent_id)
        if handler:
            return await handler(
                conversation_id=conversation_id,
                action=request.action,
                data=request.data
            )

        # 通用 resume 实现：使用已注册的 get_graph 和 get_checkpointer
        get_graph = config.get("get_graph")
        get_checkpointer = config.get("get_checkpointer")
        if not get_graph or not get_checkpointer:
            return business_response(msg=f"agent {agent_id} 缺少 graph 或 checkpointer 配置", code=40003, status="error")

        return StreamingResponse(
            _generic_resume_stream(
                conversation_id=conversation_id,
                action=request.action,
                data=request.data,
                get_graph=get_graph,
                get_checkpointer=get_checkpointer
            ),
            media_type="text/event-stream",
            headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
        )
    except Exception as e:
        logger.error(f"恢复对话失败: {e}", exc_info=True)
        return business_response(msg=f"恢复对话失败: {str(e)}", code=50012, status="error")


@router.post("/{conversation_id}/files")
async def upload_file(
    conversation_id: str,
    file: UploadFile = File(...)
):
    """
    上传文件

    POST /api/conversations/{id}/files
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
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        file_base_dir = config.get("file_base_dir")
        if not file_base_dir:
            return business_response(msg="该 agent 未配置文件存储目录", code=40004, status="error")

        allowed_extensions = config.get("allowed_extensions")

        result = await file_manager.save_file(
            base_dir=file_base_dir,
            conversation_id=conversation_id,
            file=file,
            allowed_extensions=allowed_extensions
        )

        return business_response(data=result, msg="文件上传成功")
    except ValueError as e:
        return business_response(msg=str(e), code=40002, status="error")
    except Exception as e:
        logger.error(f"文件上传失败: {e}", exc_info=True)
        return business_response(msg=f"文件上传失败: {str(e)}", code=50009, status="error")


@router.get("/{conversation_id}/files/{file_id}")
async def download_file(
    conversation_id: str,
    file_id: str,
    preview: bool = Query(False, description="是否预览模式")
):
    """
    下载或预览文件

    GET /api/conversations/{id}/files/{file_id}?preview=false

    返回：文件内容
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        file_base_dir = config.get("file_base_dir")
        if not file_base_dir:
            return business_response(msg="该 agent 未配置文件存储目录", code=40004, status="error")

        return await file_manager.read_file(
            base_dir=file_base_dir,
            conversation_id=conversation_id,
            file_id=file_id,
            preview=preview
        )
    except ValueError as e:
        return business_response(msg=str(e), code=40002, status="error")
    except FileNotFoundError as e:
        return business_response(msg=str(e), code=40404, status="error")
    except Exception as e:
        logger.error(f"文件下载失败: {e}", exc_info=True)
        return business_response(msg=f"文件下载失败: {str(e)}", code=50010, status="error")


@router.get("/{conversation_id}/files")
async def list_files(conversation_id: str):
    """
    列出会话的所有文件

    GET /api/conversations/{id}/files

    返回：
    {
        "status": "success",
        "data": [{"file_id": "...", "file_size": 123, "mime_type": "..."}, ...]
    }
    """
    try:
        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40001, status="error")

        config = await get_agent_config_by_conversation(conversation_id)
        if not config:
            return business_response(msg="对话不存在", code=40404, status="error")

        file_base_dir = config.get("file_base_dir")
        if not file_base_dir:
            return business_response(msg="该 agent 未配置文件存储目录", code=40004, status="error")

        files = file_manager.list_files(file_base_dir, conversation_id)
        return business_response(data=files, msg="获取文件列表成功")
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}", exc_info=True)
        return business_response(msg=f"获取文件列表失败: {str(e)}", code=50011, status="error")


# ============ Artifact 接口 ============

@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    """
    获取 Artifact 数据

    GET /api/conversations/artifacts/{id}

    artifact_id 是全局唯一的 UUID，会自动查找对应的类型和数据

    返回：
    {
        "status": "success",
        "data": { ... }  // Artifact 数据内容
    }
    """
    from utils.lang_util.artifact_store import load_artifact_by_id

    if not artifact_id:
        return business_response(msg="artifact_id 不能为空", code=40001, status="error")

    try:
        data = load_artifact_by_id(artifact_id)
        if data is None:
            return business_response(msg="Artifact 不存在", code=40404, status="error")

        return business_response(data=data, msg="获取成功")
    except Exception as e:
        logger.error(f"获取 Artifact 失败: {e}", exc_info=True)
        return business_response(msg=f"获取 Artifact 失败: {str(e)}", code=50013, status="error")
