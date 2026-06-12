"""
inspection_app 的 completion handler
"""

import json
import time
from typing import Dict, Any, Optional
from fastapi.responses import StreamingResponse
from .service import process_conversation, resume_conversation
from utils.logger import get_logger

logger = get_logger(__name__)


async def violation_completion_handler(
    conversation_id: str,
    query: str,
    options: Optional[Dict[str, Any]] = None
) -> StreamingResponse:
    """
    violation_agent 的 completion 处理器

    Args:
        conversation_id: 会话ID
        query: 用户查询
        options: 扩展参数
            - task_type: 任务类型 (如 "report")
            - uploaded_files: 上传的文件列表
    """
    async def generate():
        try:
            query_text = query.strip()
            options_data = options or {}

            # 参数验证
            if not query_text:
                yield f"data: {{\"error\": \"消息内容不能为空\"}}\n\n"
                return

            if not conversation_id:
                yield f"data: {{\"error\": \"conversation_id 不能为空\"}}\n\n"
                return

            # 获取 tool_type（用于前端恢复引导语）
            tool_type = options_data.get("tool_type")

            # 构建 task 对象（兼容原有逻辑）
            task = None
            task_type = options_data.get("task_type")
            if task_type:
                task = {
                    "task_type": task_type,
                    "report_type": options_data.get("report_type", ""),  # 报告类型
                    "uploaded_files": options_data.get("uploaded_files", [])
                }

            # 如果是报告生成任务，且有上传的检查报告文件
            if task and task.get("task_type") == "report":
                uploaded_files = task.get("uploaded_files", [])
                logger.info(f"[报告生成-DEBUG] task_type: {task_type}")
                logger.info(f"[报告生成-DEBUG] uploaded_files: {uploaded_files}")

                if uploaded_files and len(uploaded_files) > 0:
                    logger.info(f"[报告生成] 进入检查报告文件更新逻辑")
                    try:
                        uploaded_file_info = uploaded_files[0]
                        logger.info(f"[报告生成] uploaded_file_info: {uploaded_file_info}")

                        if uploaded_file_info and uploaded_file_info.get("file_path"):
                            logger.info(f"[报告生成] 检测到检查报告文件: {uploaded_file_info.get('file_name')}, 路径: {uploaded_file_info.get('file_path')}")
                        else:
                            logger.warning(f"[报告生成] uploaded_file_info 为空或缺少 file_path: {uploaded_file_info}")
                    except Exception as e:
                        logger.error(f"[报告生成] 更新检查报告文件信息失败: {str(e)}", exc_info=True)
                        yield f"data: {{\"error\": \"更新文件信息失败: {str(e)}\"}}\n\n"
                        return
                else:
                    logger.info(f"[报告生成] 不满足更新检查报告的条件，跳过")

            # 调用异步统一对话处理函数
            event_count = 0
            async for progress_data in process_conversation(
                thread_id=conversation_id,
                query=query_text,
                task=task,
                tool_type=tool_type
            ):
                event_count += 1

                # 标准化 SSE 格式：event: xxx\ndata: {...}\n\n
                event_name = progress_data.get('event', 'message')
                event_data = progress_data.get('data', progress_data)

                sse_data = f"event: {event_name}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                logger.debug(f"SSE 事件 #{event_count}: event={event_name}, 数据长度={len(sse_data)}")

                yield sse_data

            logger.info(f"补全完成，总共发送了 {event_count} 个 SSE 事件")

        except Exception as e:
            error_msg = f"对话处理失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            try:
                yield f"data: {{\"error\": \"{error_msg}\", \"timestamp\": \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"}}\n\n"
            except:
                pass

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        }
    )


async def violation_resume_handler(
    conversation_id: str,
    action: str = "approve",
    data: Optional[Dict[str, Any]] = None
) -> StreamingResponse:
    """
    恢复被中断的执法检查流程（Human-in-the-Loop 审核确认）

    符合公共 resume 接口签名:
        async def handler(conversation_id, action, data) -> StreamingResponse

    Args:
        conversation_id: 会话ID
        action: 用户操作 "approve" | "edit" | "reject"
        data: 修改后的数据（action=edit 时使用）
    """
    async def generate():
        try:
            # 构建 human_input 对象
            human_input = {
                "action": action,
                "data": data or {}
            }

            event_count = 0
            async for progress_data in resume_conversation(
                thread_id=conversation_id,
                human_input=human_input
            ):
                event_count += 1

                # 标准化 SSE 格式：event: xxx\ndata: {...}\n\n
                event_name = progress_data.get('event', 'message')
                event_data = progress_data.get('data', progress_data)

                sse_data = f"event: {event_name}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                logger.debug(f"Resume SSE 事件 #{event_count}: event={event_name}, 数据长度={len(sse_data)}")

                yield sse_data

            logger.info(f"Resume 完成，总共发送了 {event_count} 个 SSE 事件")

        except Exception as e:
            error_msg = f"恢复对话失败: {str(e)}"
            logger.error(error_msg, exc_info=True)
            try:
                yield f"event: error\ndata: {{\"message\": \"{error_msg}\", \"timestamp\": \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"}}\n\n"
            except:
                pass

    return StreamingResponse(
        generate(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
        }
    )
