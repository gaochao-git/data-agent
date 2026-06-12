"""
执法检查 FastAPI 路由（完全异步版本）
包含：
- 文件上传和提取接口（支持 docx 格式）
- 会话恢复接口
- 报告生成接口
- 其他 violation 特有接口

注意：会话的创建、列表、详情、删除、标题、收藏、对话等通用接口
已迁移到公共路由 utils/conversation_router.py
"""
import json
import time
import os
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import select

from utils.storage_paths import VIOLATION_BASE_DIR
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, FileResponse
from dotenv import load_dotenv
from utils.lang_util.checkpoint import get_checkpointer
from utils.lang_util.database import get_session
from utils.lang_util.models import UserThread
from utils.lang_util.conversation_router import get_agent_config
from utils.logger import get_logger
from utils.response import business_response
from .inspection_utils import get_chat_model, get_violation_upload_path
from .inspection_utils import get_file_type, parse_fact_determination_doc, parse_evidence_records
from .schema import ResumeRequest

# 初始化日志
logger = get_logger(__name__)

# 创建路由器 - 只包含 violation 特有的接口
router = APIRouter(prefix="/api", tags=["violation"])

# 加载环境变量
load_dotenv()

# 代理标识（默认使用检查报告智能体）
DEFAULT_AGENT_ID = "report_jcbg"


def get_graph_for_agent(agent_id: str, checkpointer=None):
    """
    根据 agent_id 获取对应的图
    """
    config = get_agent_config(agent_id)
    if not config:
        # 尝试默认智能体
        config = get_agent_config(DEFAULT_AGENT_ID)
    if not config:
        raise ValueError(f"未找到智能体配置: {agent_id}")
    get_graph_fn = config.get("get_graph")
    if not get_graph_fn:
        raise ValueError(f"智能体配置缺少 get_graph: {agent_id}")
    return get_graph_fn(checkpointer)


async def get_graph_for_conversation(conversation_id: str, checkpointer=None):
    """
    根据 conversation_id 获取对应智能体的图
    """
    # 从数据库查询会话对应的 agent_id
    async with get_session() as session:
        result = await session.execute(
            select(UserThread).where(UserThread.thread_id == conversation_id)
        )
        thread = result.scalar_one_or_none()
        if thread:
            agent_id = thread.agent_id or DEFAULT_AGENT_ID
        else:
            agent_id = DEFAULT_AGENT_ID

    return get_graph_for_agent(agent_id, checkpointer)


# ============ 路由处理函数 ============
# 注意：创建会话、获取列表、获取详情、删除、标题、收藏、对话、恢复等通用接口
# 已迁移到公共路由 /api/conversations
# resume 接口已迁移到公共接口，violation_agent 的 resume_handler 在 __init__.py 中注册


@router.get("/violation/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """
    获取对话详情（含历史消息和中断状态）

    路径参数：
    - conversation_id: 对话 ID

    返回：JSON
    {
        "messages": [...],
        "title": "...",
        "interrupt": {  // 如果对话处于中断状态
            "next_nodes": [...],
            "interrupt_data": {...}
        }
    }
    """
    try:
        # 使用 async with checkpointer 模式获取状态
        config = {"configurable": {"thread_id": conversation_id}}
        async with get_checkpointer() as checkpointer:
            graph = await get_graph_for_conversation(conversation_id, checkpointer)
            state = await graph.aget_state(config)

            # 如果没有状态，返回空消息列表
            if not state or not state.values:
                logger.info(f"对话 {conversation_id} 没有找到状态")
                return business_response(
                    data={"messages": [], "title": None, "interrupt": None},
                    msg="对话不存在或无消息记录"
                )

            # 提取消息历史
            # 关键：如果存在子图任务（中断状态），需要从子图的 checkpoint 读取完整消息
            messages = state.values.get("messages", [])

            if state.tasks:
                # 存在子图任务，尝试从子图获取更完整的消息
                for task in state.tasks:
                    if hasattr(task, 'state') and task.state:
                        subgraph_config = task.state
                        try:
                            subgraph_state = await graph.aget_state(subgraph_config)
                            subgraph_messages = subgraph_state.values.get("messages", [])
                            # 如果子图消息更多，使用子图的消息
                            if len(subgraph_messages) > len(messages):
                                logger.info(f"使用子图消息: 主图 {len(messages)} 条 -> 子图 {len(subgraph_messages)} 条")
                                messages = subgraph_messages
                                break
                        except Exception as e:
                            logger.warning(f"获取子图状态失败: {e}")

            logger.info(f"对话 {conversation_id} 消息数量: {len(messages)}")

        # 序列化消息（转换为前端格式）
        serialized_messages = []
        for msg in messages:
            if hasattr(msg, 'type'):
                role = 'user' if msg.type == 'human' else 'assistant'
                content = msg.content

                # 获取 additional_kwargs（如果存在）
                additional_kwargs = None
                if hasattr(msg, 'additional_kwargs'):
                    additional_kwargs = msg.additional_kwargs

                formatted_msg = {
                    "role": role,
                    "content": content,
                    "timestamp": ""
                }

                # 只有当 additional_kwargs 不为空时才添加
                if additional_kwargs:
                    formatted_msg["additional_kwargs"] = additional_kwargs

                serialized_messages.append(formatted_msg)

        # 检查是否存在中断状态
        interrupt_info = None
        if state.next:
            # 对话处于中断状态，提取中断数据
            logger.info(f"对话 {conversation_id} 处于中断状态，等待节点: {state.next}")
            interrupt_data = None
            if state.tasks:
                for task in state.tasks:
                    if hasattr(task, 'interrupts') and task.interrupts:
                        interrupt_data = task.interrupts[0].value if task.interrupts else None
                        break

            interrupt_info = {
                "next_nodes": list(state.next) if state.next else [],
                "interrupt_data": interrupt_data
            }

        # 读取任务进度（从 state 中持久化的 task_progress）
        task_progress = state.values.get("task_progress")
        if task_progress:
            logger.info(f"对话 {conversation_id} 任务进度: {len(task_progress)} 个任务")

        # 读取工具类型（从 state 中持久化的 tool_type，用于前端恢复引导语）
        tool_type = state.values.get("tool_type")

        # 获取对话标题
        title = None
        try:
            async with get_session() as session:
                result = await session.execute(
                    select(UserThread.thread_title).where(UserThread.thread_id == conversation_id)
                )
                title = result.scalar_one_or_none()
        except Exception as e:
            logger.warning(f"获取对话标题失败: {str(e)}")

        logger.info(f"获取对话 {conversation_id} 的 {len(serialized_messages)} 条消息, 中断状态: {interrupt_info is not None}, 任务进度: {task_progress is not None}, tool_type: {tool_type}")

        return business_response(
            data={
                "messages": serialized_messages,
                "title": title,
                "interrupt": interrupt_info,
                "task_progress": task_progress,  # 返回持久化的任务进度
                "tool_type": tool_type  # 返回工具类型（用于前端恢复引导语）
            },
            msg="获取对话详情成功"
        )

    except HTTPException as e:
        return business_response(msg=str(e.detail), code=40000 + e.status_code, status="error")
    except Exception as e:
        error_msg = f"获取对话详情失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return business_response(msg=error_msg, code=50006, status="error")


@router.get("/conversations/{conversation_id}/files/{file_id}")
async def download_file(
    conversation_id: str,
    file_id: str,
    preview: bool = Query(False, description="是否预览模式（true=内联显示，false=下载）")
):
    """
    下载或预览文件

    路径参数：
    - conversation_id: 对话 ID
    - file_id: 文件 ID（文件名）

    查询参数：
    - preview: 是否预览模式（默认 false）

    返回：
    - 文件
    """
    try:
        # 安全检查：防止路径遍历攻击
        if '..' in conversation_id or '/' in conversation_id:
            logger.warning(f"检测到潜在的路径遍历攻击 (conversation_id): {conversation_id}")
            return business_response(msg="无效的对话ID", code=40400, status="error")

        if '..' in file_id or '/' in file_id:
            logger.warning(f"检测到潜在的路径遍历攻击 (file_id): {file_id}")
            return business_response(msg="无效的文件ID", code=40400, status="error")

        # 精确定位文件路径
        # 判断是生成的报告还是上传的文件
        if file_id.startswith('upload_'):
            # 上传的文件：在根目录查找（文件名已包含 upload_ 前缀和扩展名）
            file_path = get_violation_upload_path(conversation_id, file_id)
        else:
            # 生成的报告：在 generated 目录查找
            file_path = os.path.join(VIOLATION_BASE_DIR, conversation_id, "generated", file_id)

        if not os.path.exists(file_path):
            logger.error(f"文件不存在: {file_path}")
            return business_response(msg=f"文件不存在: {file_id}", code=40404, status="error")

        logger.info(f"下载文件: {file_id} (路径: {file_path})")

        # 根据文件扩展名确定 MIME 类型
        if file_id.endswith('.xlsx'):
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        elif file_id.endswith('.docx') or file_id.endswith('.wps'):
            mimetype = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        else:
            mimetype = 'application/octet-stream'

        # 返回文件
        from fastapi.responses import Response
        import aiofiles

        # 读取文件内容
        async with aiofiles.open(file_path, 'rb') as f:
            content = await f.read()

        # 设置响应头
        headers = {}
        # 对文件名进行 URL 编码以支持中文
        from urllib.parse import quote
        encoded_filename = quote(file_id)

        if preview:
            # 预览模式：内联显示
            headers['Content-Disposition'] = f'inline; filename*=UTF-8\'\'{encoded_filename}'
        else:
            # 下载模式：使用 RFC 5987 格式支持中文文件名
            headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{encoded_filename}'

        return Response(
            content=content,
            media_type=mimetype,
            headers=headers
        )

    except Exception as e:
        error_msg = f"文件下载失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return business_response(msg=error_msg, code=50008, status="error")


@router.post("/conversations/{conversation_id}/files")
async def upload_file(
    conversation_id: str,
    file: UploadFile = File(...),
    file_type_hint: Optional[str] = Form(None),
    group_id: Optional[str] = Form(None),
    violation_id: Optional[int] = Form(None)
):
    """
    上传文件（支持分组管理 + 违法行为关联）

    路径参数：
    - conversation_id: 对话 ID
    - violation_id: 违法行为ID（上传取证记录表时指定）

    表单参数：
    - file: 文件
    - file_type_hint: 文件类型提示（"事实认定书" 或 "取证记录表"）
    - group_id: 分组ID（可选）
      - 上传"事实认定书"时：忽略此参数，会自动创建新分组
      - 上传"取证记录表"时：必须指定要配对的分组ID

    返回：
    {
        "file_id": "uuid",
        "file_name": "原始文件名",
        "file_type": "detected_type",
        "group_id": "分组ID"
    }
    """
    try:
        if not file.filename:
            return business_response(msg="文件名为空", code=40001, status="error")

        if not conversation_id:
            return business_response(msg="对话ID不能为空", code=40004, status="error")

        # 生成文件ID
        file_id = str(uuid.uuid4())

        # 获取文件扩展名
        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()

        # 校验文件格式：只接受 .docx 和 .wps
        if ext not in ['.docx', '.wps']:
            return business_response(msg="只能上传 .docx 或 .wps 格式的 Word 文档", code=40003, status="error")

        # 保存文件到持久化目录
        file_path = get_violation_upload_path(conversation_id, f"upload_{file_id}{ext}")

        # 异步写入文件
        content = await file.read()
        with open(file_path, 'wb') as f:
            f.write(content)

        # 检测文件类型
        raw_mime_type = file.content_type or 'application/octet-stream'
        file_type = get_file_type(mime_type=raw_mime_type, extension=ext)

        # 初始化解析数据（后续状态更新需要使用）
        parsed_data = None

        # 根据文件类型提示进行内容校验
        if file_type_hint == "事实认定书":
            try:
                # 读取文件内容并解析
                parsed_data = parse_fact_determination_doc(content)

                # 校验必需字段
                if not parsed_data.get('inspection_content') or not parsed_data.get('violation_facts'):
                    os.unlink(file_path)  # 删除无效文件
                    return business_response(
                        msg='事实认定书格式不正确：缺少"检查内容"或"事实认定"字段，请检查文档格式',
                        code=40010,
                        status='error'
                    )

                logger.info(f"事实认定书校验通过 - 检查内容: {len(parsed_data['inspection_content'])} 字符, 事实认定: {len(parsed_data['violation_facts'])} 字符")
            except Exception as e:
                os.unlink(file_path)  # 删除无效文件
                return business_response(
                    msg=f'事实认定书解析失败：{str(e)}，请检查文档格式是否正确',
                    code=40011,
                    status='error'
                )

        elif file_type_hint == "检查报告":
            # 检查报告不需要解析，直接保存即可
            # 后续在 extract_from_report 节点中用 LLM 提取数据
            logger.info(f"检查报告上传成功: {filename}")

        elif file_type_hint == "取证记录表":
            try:
                # 读取文件内容并解析
                parsed_data = parse_evidence_records(content)

                # 校验必需字段
                evidence_list = parsed_data.get('evidence_list', [])
                if not evidence_list or len(evidence_list) == 0:
                    os.unlink(file_path)  # 删除无效文件
                    return business_response(
                        msg='取证记录表格式不正确：未找到证据清单数据，请检查文档格式',
                        code=40020,
                        status='error'
                    )

                # 校验证据清单中的关键字段（至少要有证据名称及编号）
                missing_fields = []
                for idx, evidence in enumerate(evidence_list):
                    if not evidence.get('evidence_name_code'):
                        missing_fields.append(f'第 {idx + 1} 条证据缺少"证据名称及编号"')

                if missing_fields:
                    os.unlink(file_path)  # 删除无效文件
                    return business_response(
                        msg=f'取证记录表格式不正确：{", ".join(missing_fields[:3])}{"等" if len(missing_fields) > 3 else ""}，请检查文档格式',
                        code=40021,
                        status='error'
                    )

                logger.info(f"取证记录表校验通过 - 证据数量: {len(evidence_list)}, 基本信息: {parsed_data.get('basic_info', {})}")
            except Exception as e:
                os.unlink(file_path)  # 删除无效文件
                return business_response(
                    msg=f'取证记录表解析失败：{str(e)}，请检查文档格式是否正确',
                    code=40022,
                    status='error'
                )

        logger.info(f"文件上传成功: {filename}, file_id: {file_id}, 路径: {file_path}, 类型: {file_type}, 类型提示: {file_type_hint}")

        # 更新状态：实现分组管理
        new_group_id = None
        state_update = {}

        try:
            async with get_checkpointer() as checkpointer:
                graph = await get_graph_for_conversation(conversation_id, checkpointer)
                config = {"configurable": {"thread_id": conversation_id}}

                # 获取当前状态
                state_snapshot = await graph.aget_state(config)

                if file_type_hint == "检查报告":
                    # ============ 处理检查报告上传 ============
                    # 检查报告信息保存到 violations_analysis_result.source_files 中
                    uploaded_report_info = {
                        "file_id": f"upload_{file_id}{ext}",
                        "filename": filename,
                        "file_path": file_path,
                        "upload_time": datetime.now().isoformat(),
                        "file_type": "检查报告"  # 标记文件类型
                    }

                    # 获取或创建 violations_analysis_result
                    current_result = state_snapshot.values.get("violations_analysis_result") if state_snapshot else None

                    if current_result:
                        # 已有结果，更新 source_files
                        if isinstance(current_result, dict):
                            source_files = current_result.get("source_files", [])
                        else:
                            source_files = current_result.source_files if hasattr(current_result, "source_files") else []

                        # 移除旧的检查报告，添加新的
                        source_files = [f for f in source_files if f.get("file_type") != "检查报告"]
                        source_files.append(uploaded_report_info)

                        # 更新 violations_analysis_result
                        if isinstance(current_result, dict):
                            current_result["source_files"] = source_files
                            state_update = {"violations_analysis_result": current_result}
                        else:
                            # Pydantic 模型
                            current_result.source_files = source_files
                            state_update = {"violations_analysis_result": current_result}
                    else:
                        # 创建新的 violations_analysis_result（最小结构）
                        from .shared.models import ViolationsAnalysisResult
                        state_update = {
                            "violations_analysis_result": ViolationsAnalysisResult(
                                inspected_person="",  # 占位，后续从报告解析
                                violations=[],
                                source_files=[uploaded_report_info]
                            )
                        }

                    logger.info(f"检查报告保存到 violations_analysis_result.source_files: {filename}")

                elif file_type_hint in ["事实认定书", "取证记录表"]:
                    # ============ 处理事实认定书/取证记录表上传 ============
                    current_groups = state_snapshot.values.get("violation_file_groups") if state_snapshot else None
                    current_groups = current_groups or []
                    logger.info(f"当前分组数量: {len(current_groups)}, conversation_id={conversation_id}")

                    if file_type_hint == "事实认定书":
                        # 检查分组数量限制
                        if len(current_groups) >= 10:
                            logger.warning(f"已达上限: 当前{len(current_groups)}个分组，拒绝上传")
                            os.unlink(file_path)  # 删除已上传的文件
                            return business_response(
                                msg="已达到最大上传数量限制（最多10个违法行为），请删除部分文件后再上传",
                                code=40030,
                                status="error"
                            )

                        # 创建新分组
                        new_group_id = str(uuid.uuid4())
                        new_group = {
                            "group_id": new_group_id,
                            "fact_determination": {
                                "file_id": f"upload_{file_id}{ext}",  # 完整文件名
                                "filename": filename,
                                "upload_time": datetime.now().isoformat(),
                                "parsed_data": parsed_data
                            },
                            "evidence_records": None,
                            "created_at": datetime.now().isoformat(),
                            "order": len(current_groups) + 1
                        }

                        # 使用 operator.add 追加新分组
                        state_update = {"violation_file_groups": [new_group]}
                        logger.info(f"创建新分组: group_id={new_group_id}, 文件={filename}")

                    elif file_type_hint == "取证记录表":
                        # 检查是事实认定书流程还是检查报告流程
                        # 事实认定书流程：需要 group_id，将取证记录表关联到分组
                        # 检查报告流程：不需要 group_id，将取证记录表添加到 violations_analysis_result.source_files

                        if not group_id:
                            # ============ 检查报告流程 ============
                            logger.info(f"检查报告流程：上传取证记录表，violation_id={violation_id}")

                            # 必须指定 violation_id
                            if not violation_id:
                                os.unlink(file_path)
                                return business_response(
                                    msg="检查报告流程上传取证记录表必须指定 violation_id",
                                    code=40034,
                                    status="error"
                                )

                            # ⭐ 在中断期间，不尝试更新图状态（因为 violations 数据在节点函数内部，未持久化）
                            # 文件已保存到磁盘，前端会在 resume 时将文件信息包含在 human_input.data.violations 中
                            logger.info(f"取证记录表已保存到磁盘: {filename}，violation_id={violation_id}")
                            logger.info(f"文件路径: {file_path}")

                            # 不更新图状态，避免清除中断状态
                            state_update = None

                        else:
                            # ============ 事实认定书流程 ============
                            # 查找并更新指定分组
                            found = False
                            updated_groups = []
                            for group in current_groups:
                                if group["group_id"] == group_id:
                                    # 更新该分组的取证记录表
                                    group["evidence_records"] = {
                                        "file_id": f"upload_{file_id}{ext}",  # 完整文件名
                                        "filename": filename,
                                        "upload_time": datetime.now().isoformat(),
                                        "parsed_data": parsed_data
                                    }
                                    found = True
                                    logger.info(f"更新分组 {group_id} 的取证记录表: {filename}")
                                updated_groups.append(group)

                            if not found:
                                os.unlink(file_path)
                                return business_response(
                                    msg=f"未找到指定的分组ID: {group_id}",
                                    code=40032,
                                    status="error"
                                )

                            # 检查图是否处于中断状态
                            current_graph_state = await graph.aget_state(config)
                            is_interrupted = bool(current_graph_state.next)

                            if is_interrupted:
                                # 图处于中断状态，不更新状态（避免清除中断）
                                # 文件已保存到磁盘，前端会在 resume 时将文件信息包含在 human_input 中
                                logger.info(f"图处于中断状态，跳过状态更新（文件已保存到磁盘）: {filename}")
                                state_update = None
                            else:
                                # 图未中断，正常更新状态
                                # 整体替换分组列表（不能用 operator.add）
                                # 需要先清空再设置
                                await graph.aupdate_state(
                                    config=config,
                                    values={"violation_file_groups": []},
                                    as_node="state_update_node"
                                )
                                state_update = {"violation_file_groups": updated_groups}

                            new_group_id = group_id  # 返回更新的分组ID

                    else:
                        logger.warning(f"未知的文件类型提示: {file_type_hint}，不更新状态")
                        return business_response(
                            data={
                                "file_id": file_id,
                                "file_name": filename,
                                "file_type": file_type
                            },
                            msg="文件上传成功（未关联到分组）"
                        )

                # 更新状态（使用专门的虚拟节点）
                if state_update:
                    await graph.aupdate_state(
                        config=config,
                        values=state_update,
                        as_node="state_update_node"
                    )
                    logger.info(f"状态更新成功: conversation_id={conversation_id}, group_id={new_group_id}")
                else:
                    logger.info(f"跳过状态更新（图处于中断状态）: conversation_id={conversation_id}, group_id={new_group_id}")

        except Exception as e:
            logger.error(f"状态更新失败: {str(e)}", exc_info=True)
            # 不删除文件，允许重试
            return business_response(
                msg=f"文件已上传但状态更新失败: {str(e)}",
                code=50010,
                status="error"
            )

        # 构建返回数据
        response_data = {
            "file_id": f"upload_{file_id}{ext}",  # 返回完整文件名
            "file_name": filename,
            "file_type": file_type
        }

        # 根据文件类型返回不同数据
        if file_type_hint == "检查报告":
            # 检查报告返回文件路径
            response_data["file_path"] = file_path
        else:
            # 事实认定书/取证记录表返回 group_id
            response_data["group_id"] = new_group_id

        # 如果是取证记录表且有解析数据，返回证据列表
        if file_type_hint == "取证记录表" and parsed_data:
            response_data["parsed_data"] = {
                "evidence_list": parsed_data.get("evidence_list", []),
                "basic_info": parsed_data.get("basic_info", {})
            }

        return business_response(
            data=response_data,
            msg="文件上传成功"
        )

    except HTTPException as e:
        return business_response(msg=str(e.detail), code=40000 + e.status_code, status="error")
    except Exception as e:
        error_msg = f"文件上传失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return business_response(msg=error_msg, code=50009, status="error")


@router.delete("/conversations/{conversation_id}/file-groups/{group_id}")
async def delete_file_group(
    conversation_id: str,
    group_id: str,
    delete_evidence_only: bool = Query(False, description="仅删除取证记录表，保留事实认定书")
):
    """
    删除文件分组或取证记录表

    路径参数：
    - conversation_id: 对话 ID
    - group_id: 分组 ID

    查询参数：
    - delete_evidence_only: 是否仅删除取证记录表
      - False（默认）：删除整个分组（包括事实认定书和取证记录表）
      - True：仅删除取证记录表，保留事实认定书

    返回：
    {
        "success": true,
        "message": "删除成功"
    }
    """
    try:
        async with get_checkpointer() as checkpointer:
            graph = await get_graph_for_conversation(conversation_id, checkpointer)
            config = {"configurable": {"thread_id": conversation_id}}

            # 获取当前状态
            state_snapshot = await graph.aget_state(config)
            if not state_snapshot:
                return business_response(
                    msg=f"对话不存在: {conversation_id}",
                    code=40040,
                    status="error"
                )

            current_groups = state_snapshot.values.get("violation_file_groups") or []

            # 查找目标分组
            found = False
            updated_groups = []

            for group in current_groups:
                if group["group_id"] == group_id:
                    found = True
                    if delete_evidence_only:
                        # 仅删除取证记录表
                        if group.get("evidence_records"):
                            group["evidence_records"] = None
                            logger.info(f"删除分组 {group_id} 的取证记录表")
                            updated_groups.append(group)
                        else:
                            return business_response(
                                msg="该分组没有取证记录表",
                                code=40041,
                                status="error"
                            )
                    else:
                        # 删除整个分组（跳过不添加到updated_groups）
                        logger.info(f"删除整个分组: {group_id}")
                        continue  # 不添加到updated_groups，实现删除
                else:
                    updated_groups.append(group)

            if not found:
                return business_response(
                    msg=f"未找到指定的分组ID: {group_id}",
                    code=40042,
                    status="error"
                )

            # 更新状态：先清空再设置
            await graph.aupdate_state(
                config=config,
                values={"violation_file_groups": []},
                as_node="state_update_node"
            )

            await graph.aupdate_state(
                config=config,
                values={"violation_file_groups": updated_groups},
                as_node="state_update_node"
            )

            # 重新编号（保持order连续）
            if not delete_evidence_only:
                for idx, group in enumerate(updated_groups, start=1):
                    group["order"] = idx

                await graph.aupdate_state(
                    config=config,
                    values={"violation_file_groups": []},
                    as_node="state_update_node"
                )

                await graph.aupdate_state(
                    config=config,
                    values={"violation_file_groups": updated_groups},
                    as_node="state_update_node"
                )

            logger.info(f"删除成功: conversation_id={conversation_id}, group_id={group_id}, evidence_only={delete_evidence_only}")

            return business_response(
                data={
                    "group_id": group_id,
                    "delete_evidence_only": delete_evidence_only
                },
                msg="删除成功"
            )

    except Exception as e:
        error_msg = f"删除失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return business_response(msg=error_msg, code=50011, status="error")


@router.delete("/conversations/{conversation_id}/file-groups")
async def clear_all_file_groups(conversation_id: str):
    """
    清空所有文件分组

    路径参数：
    - conversation_id: 对话 ID

    返回：
    {
        "success": true,
        "message": "清空成功",
        "data": {
            "deleted_count": 56
        }
    }
    """
    try:
        async with get_checkpointer() as checkpointer:
            graph = await get_graph_for_conversation(conversation_id, checkpointer)
            config = {"configurable": {"thread_id": conversation_id}}

            # 获取当前状态
            state_snapshot = await graph.aget_state(config)
            if not state_snapshot:
                return business_response(
                    msg=f"对话不存在: {conversation_id}",
                    code=40040,
                    status="error"
                )

            current_groups = state_snapshot.values.get("violation_file_groups") or []
            deleted_count = len(current_groups)

            # 清空所有分组
            await graph.aupdate_state(
                config=config,
                values={"violation_file_groups": []},
                as_node="state_update_node"
            )

            logger.info(f"清空所有文件分组成功: conversation_id={conversation_id}, deleted_count={deleted_count}")

            return business_response(
                data={"deleted_count": deleted_count},
                msg=f"成功清空 {deleted_count} 个违法行为"
            )

    except Exception as e:
        error_msg = f"清空失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return business_response(msg=error_msg, code=50012, status="error")


@router.get("/conversations/{conversation_id}/state")
async def get_conversation_state(conversation_id: str):
    """
    获取对话状态（用于查询文件分组和已上传的文件信息）

    路径参数：
    - conversation_id: 对话 ID

    返回：JSON
    {
        "violation_file_groups": [...],  // 文件分组列表
        "has_analysis_result": true/false
    }
    """
    try:
        # 使用 async with 模式获取状态
        async with get_checkpointer() as checkpointer:
            graph = await get_graph_for_conversation(conversation_id, checkpointer)
            state_snapshot = await graph.aget_state(
                config={"configurable": {"thread_id": conversation_id}}
            )

        state_values = state_snapshot.values if state_snapshot else {}

        # 获取文件分组列表
        file_groups_raw = state_values.get("violation_file_groups", [])

        # 去重：由于 operator.add 会累积数据，需要按 group_id 去重，保留最后一个
        seen_group_ids = {}
        for group in file_groups_raw:
            group_id = group.get("group_id")
            if group_id:
                seen_group_ids[group_id] = group

        file_groups = list(seen_group_ids.values())
        # 按 order 排序
        file_groups.sort(key=lambda x: x.get("order", 0))

        has_analysis = bool(state_values.get("vio_split_result"))

        logger.info(f"[查询状态] conversation_id={conversation_id}, "
                   f"file_groups_raw_count={len(file_groups_raw)}, "
                   f"file_groups_unique_count={len(file_groups)}, "
                   f"analysis={has_analysis}")

        return business_response(
            data={
                "violation_file_groups": file_groups,
                "has_analysis_result": has_analysis
            },
            msg="获取对话状态成功"
        )

    except Exception as e:
        error_msg = f"获取对话状态失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        # 如果状态不存在，返回空状态
        return business_response(
            data={
                "available_files": {
                    "事实认定书": False,
                    "取证记录表": False
                },
                "has_analysis_result": False
            },
            msg="获取对话状态成功"
        )


# ===== 重新生成报告接口 =====
@router.post("/conversations/{conversation_id}/regenerate-report")
async def regenerate_report(conversation_id: str, request: dict):
    """
    重新生成报告（使用修改后的数据）

    请求体：
    {
        "report_type": "jcbg",  # 报告类型
        "source_data": {        # 修改后的源数据
            "inspection_content": "...",
            "violation_facts": ["...", "..."],
            "legal_basis": ["...", "..."],
            "penalty_clauses": ["...", "..."]
        }
    }
    """
    from docx import Document
    from .inspection_utils import set_chinese_font, get_violation_generated_path

    report_type = request.get("report_type")
    source_data = request.get("source_data", {})

    if not report_type:
        return business_response(msg="report_type 不能为空", code=40001, status="error")

    if report_type != "jcbg":
        return business_response(msg=f"暂不支持重新生成 {report_type} 类型的报告", code=40002, status="error")

    try:
        # 提取数据
        inspection_content = source_data.get("inspection_content", "")
        violation_facts = source_data.get("violation_facts", [])
        legal_basis = source_data.get("legal_basis", [])
        penalty_clauses = source_data.get("penalty_clauses", [])

        # 加载模板文件
        template_path = os.path.join(
            os.path.dirname(__file__),
            "report_template",
            "检查报告.docx"
        )

        if not os.path.exists(template_path):
            return business_response(msg=f"模板文件不存在", code=50041, status="error")

        doc = Document(template_path)

        # 查找"四、支付结算方面的主要问题"段落的索引
        target_index = -1
        for i, para in enumerate(doc.paragraphs):
            if "四、支付结算方面的主要问题" in para.text:
                target_index = i
                break

        if target_index == -1:
            return business_response(msg="未找到目标段落", code=50042, status="error")

        # 找到占位符段落并替换内容
        if target_index + 1 < len(doc.paragraphs):
            placeholder_para = doc.paragraphs[target_index + 1]
            placeholder_para.clear()

            # 构建主要问题内容
            problems_text = []

            if inspection_content:
                problems_text.append(f"（一）未按照规定履行{inspection_content}义务")
                problems_text.append("")

            for fact in violation_facts:
                problems_text.append(fact)

            if legal_basis:
                problems_text.append("")
                problems_text.append(f"以上行为违反了{'、'.join(legal_basis)}的规定。")

            if penalty_clauses:
                problems_text.append("")
                problems_text.append(f"根据{'、'.join(penalty_clauses)}的规定，应当给予行政处罚。")

            if problems_text:
                content_to_fill = '\n'.join(problems_text)
                run = placeholder_para.add_run(content_to_fill)
                set_chinese_font(run, '仿宋_GB2312', 16)

        # 保存文档
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"检查报告_{timestamp}.docx"
        file_path = get_violation_generated_path(conversation_id, filename)
        doc.save(file_path)

        logger.info(f"检查报告重新生成成功: {file_path}")

        return business_response(
            data={
                "filename": filename,
                "title": "检查报告",
                "source_data": source_data,
            },
            msg="报告重新生成成功"
        )

    except Exception as e:
        error_msg = f"报告重新生成失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return business_response(msg=error_msg, code=50043, status="error")


@router.get("/conversations/{conversation_id}/files/{file_id}/preview")
async def preview_file(conversation_id: str, file_id: str):
    """
    预览 Word 文档（转换为 HTML）

    路径参数：
    - conversation_id: 对话 ID
    - file_id: 文件 ID（文件名）

    返回：
    - HTML 格式的文档内容
    """
    try:
        # 安全检查
        if '..' in conversation_id or '/' in conversation_id:
            return business_response(msg="无效的对话ID", code=40400, status="error")
        if '..' in file_id or '/' in file_id:
            return business_response(msg="无效的文件ID", code=40400, status="error")

        # 定位文件路径
        if file_id.startswith('upload_'):
            file_path = get_violation_upload_path(conversation_id, file_id)
        else:
            file_path = os.path.join(VIOLATION_BASE_DIR, conversation_id, "generated", file_id)

        if not os.path.exists(file_path):
            return business_response(msg=f"文件不存在: {file_id}", code=40404, status="error")

        # 使用 mammoth 转换 Word 文档为 HTML
        import mammoth

        with open(file_path, "rb") as docx_file:
            result = mammoth.convert_to_html(docx_file)
            html_body = result.value  # 转换后的 HTML

        # 构建完整的 HTML 页面
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{
                    font-family: 'Microsoft YaHei', 'SimSun', Arial, sans-serif;
                    padding: 20px;
                    line-height: 1.8;
                    max-width: 900px;
                    margin: 0 auto;
                    background: #fff;
                    color: #333;
                }}
                p {{
                    margin: 10px 0;
                    text-align: justify;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin: 20px 0;
                }}
                table td, table th {{
                    border: 1px solid #ddd;
                    padding: 8px;
                    text-align: left;
                }}
                table th {{
                    background-color: #f2f2f2;
                    font-weight: bold;
                }}
                h1, h2, h3, h4, h5, h6 {{
                    margin-top: 20px;
                    margin-bottom: 10px;
                    font-weight: bold;
                }}
                h1 {{ font-size: 24px; }}
                h2 {{ font-size: 20px; }}
                h3 {{ font-size: 18px; }}
                ul, ol {{
                    margin: 10px 0;
                    padding-left: 30px;
                }}
                li {{
                    margin: 5px 0;
                }}
                strong {{
                    font-weight: bold;
                }}
                em {{
                    font-style: italic;
                }}
                img {{
                    max-width: 100%;
                    height: auto;
                }}
            </style>
        </head>
        <body>
        {html_body}
        </body>
        </html>
        """

        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html_content)

    except Exception as e:
        logger.error(f"文档预览失败: {str(e)}", exc_info=True)
        return business_response(msg=f"文档预览失败: {str(e)}", code=50045, status="error")


@router.post("/parse-evidence")
async def parse_evidence_file(
    file: UploadFile = File(...)
):
    """
    解析取证记录表文件（用于审核阶段）

    表单参数：
    - file: 取证记录表文件（.docx 或 .wps 格式）

    返回：
    {
        "evidence_list": [
            {
                "evidence_name_code": str,
                "evidence_source": str,
                "evidence_form": str,
                "evidence_truth": str,
                "provider_sign": str,
                "remark": str
            }
        ],
        "basic_info": {
            "inspected_unit": str,
            "inspection_project": str,
            "evidence_date": str,
            "inspectors": str
        },
        "evidence_process": str
    }
    """
    try:
        if not file.filename:
            return business_response(msg="文件名为空", code=40001, status="error")

        # 获取文件扩展名
        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()

        # 校验文件格式
        if ext not in ['.docx', '.wps']:
            return business_response(msg="只能上传 .docx 或 .wps 格式的 Word 文档", code=40003, status="error")

        # 读取文件内容
        content = await file.read()

        # 解析取证记录表
        try:
            parsed_data = parse_evidence_records(content)

            # 校验必需字段
            evidence_list = parsed_data.get('evidence_list', [])
            if not evidence_list or len(evidence_list) == 0:
                return business_response(
                    msg='取证记录表格式不正确：未找到证据清单数据，请检查文档格式',
                    code=40020,
                    status='error'
                )

            # 校验证据清单中的关键字段
            missing_fields = []
            for idx, evidence in enumerate(evidence_list):
                if not evidence.get('evidence_name_code'):
                    missing_fields.append(f'第 {idx + 1} 条证据缺少"证据名称及编号"')

            if missing_fields:
                return business_response(
                    msg=f'取证记录表格式不正确：{", ".join(missing_fields[:3])}{"等" if len(missing_fields) > 3 else ""}',
                    code=40021,
                    status='error'
                )

            logger.info(f"取证记录表解析成功 - 证据数量: {len(evidence_list)}, 文件名: {filename}")

            return business_response(
                data=parsed_data,
                msg="解析成功"
            )

        except Exception as e:
            logger.error(f"取证记录表解析失败: {str(e)}", exc_info=True)
            return business_response(
                msg=f'取证记录表解析失败：{str(e)}',
                code=40022,
                status='error'
            )

    except Exception as e:
        error_msg = f"文件处理失败: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return business_response(msg=error_msg, code=50044, status="error")
