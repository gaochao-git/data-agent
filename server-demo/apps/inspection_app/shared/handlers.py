"""
共享的 completion handler 工厂
"""

import json
import time
from typing import Dict, Any, Optional, Callable
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk

from utils.logger import get_logger
from utils.lang_util.checkpoint import get_checkpointer

logger = get_logger(__name__)


def create_completion_handler(
    agent_name: str,
    get_graph: Callable,
    state_class: type,
    default_report_type: str = ""
):
    """
    创建 completion handler 的工厂函数

    Args:
        agent_name: 智能体名称（用于日志）
        get_graph: 获取图的函数
        state_class: 状态类型
        default_report_type: 默认报告类型

    Returns:
        completion handler 函数
    """

    async def completion_handler(
        conversation_id: str,
        query: str,
        options: Optional[Dict[str, Any]] = None
    ) -> StreamingResponse:
        """
        Completion 处理器

        Args:
            conversation_id: 会话ID
            query: 用户查询
            options: 扩展参数
        """
        async def generate():
            try:
                query_text = query.strip()
                options_data = options or {}

                if not query_text:
                    yield f"data: {{\"error\": \"消息内容不能为空\"}}\n\n"
                    return

                if not conversation_id:
                    yield f"data: {{\"error\": \"conversation_id 不能为空\"}}\n\n"
                    return

                # 构建 task 对象
                task = None
                task_type = options_data.get("task_type")
                if task_type:
                    task = {
                        "task_type": task_type,
                        "report_type": options_data.get("report_type", default_report_type),
                        "uploaded_files": options_data.get("uploaded_files", [])
                    }

                # 构建初始消息
                user_message = HumanMessage(content=query_text, additional_kwargs={"task": task} if task else {})
                initial_state = {"messages": [user_message]}

                config = {"configurable": {"thread_id": conversation_id}}

                # 发送 start 事件
                yield f"event: start\ndata: {{\"timestamp\": \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"}}\n\n"

                # 执行图
                async with get_checkpointer() as checkpointer:
                    graph = get_graph(checkpointer)

                    async for event in graph.astream(
                        initial_state,
                        config=config,
                        stream_mode=["custom", "messages"],
                        subgraphs=True
                    ):
                        # 处理 (namespace, event_type, event_data) 三元组
                        if isinstance(event, tuple) and len(event) == 3:
                            namespace, event_type, event_data = event

                            if event_type == "custom":
                                # 处理 get_stream_writer() 发送的自定义数据
                                if isinstance(event_data, dict):
                                    for sse in _process_custom_event(event_data):
                                        yield sse

                            elif event_type == "messages":
                                # 处理流式消息输出
                                if isinstance(event_data, tuple) and len(event_data) == 2:
                                    msg, metadata = event_data
                                    # 过滤掉 router 节点的输出（意图识别的 LLM 调用）
                                    langgraph_node = metadata.get("langgraph_node", "") if metadata else ""
                                    if langgraph_node == "router":
                                        continue
                                    # 只处理 AIMessageChunk（增量 token），过滤掉完整的 AIMessage（避免重复）
                                    if isinstance(msg, AIMessageChunk) and msg.content:
                                        content = msg.content
                                        if isinstance(content, str):
                                            # 前端期望 type: "AIMessageChunk"
                                            yield f"event: message\ndata: {json.dumps({'type': 'AIMessageChunk', 'content': content}, ensure_ascii=False)}\n\n"
                                        elif isinstance(content, list):
                                            for block in content:
                                                if isinstance(block, dict):
                                                    for sse in _process_content_block(block):
                                                        yield sse

                    # 检查是否有中断
                    state = await graph.aget_state(config)
                    if state.next:
                        # 图被中断，发送 interrupt 事件
                        interrupt_data = None
                        if hasattr(state, 'tasks') and state.tasks:
                            for task_obj in state.tasks:
                                if hasattr(task_obj, 'interrupts') and task_obj.interrupts:
                                    interrupt_data = task_obj.interrupts[0].value
                                    break

                        if interrupt_data:
                            yield f"event: interrupt\ndata: {json.dumps(interrupt_data, ensure_ascii=False, default=str)}\n\n"
                    else:
                        # 图正常完成，发送 completed 事件
                        final_values = state.values
                        messages = final_values.get("messages", [])
                        completed_data = {}
                        if messages:
                            last_msg = messages[-1]
                            if isinstance(last_msg, AIMessage):
                                content = last_msg.content
                                # 结构化数据（数组）需要完整发送，纯文本只发送空 completed 事件
                                if isinstance(content, list):
                                    completed_data = {
                                        "messages": [{
                                            "role": "assistant",
                                            "content": content
                                        }]
                                    }
                        # 总是发送 completed 事件，让前端知道流程已完成
                        yield f"event: completed\ndata: {json.dumps(completed_data, ensure_ascii=False, default=str)}\n\n"

                # 发送 end 事件
                yield f"event: end\ndata: {{\"timestamp\": \"{time.strftime('%Y-%m-%d %H:%M:%S')}\"}}\n\n"

            except Exception as e:
                error_msg = f"{agent_name} 对话处理失败: {str(e)}"
                logger.error(error_msg, exc_info=True)
                yield f"event: error\ndata: {json.dumps({'message': error_msg}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            generate(),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
            }
        )

    return completion_handler


def _process_custom_event(event_data: dict):
    """
    处理 get_stream_writer() 发送的自定义事件
    保持 non_standard 格式，让前端根据 type 判断处理方式
    """
    # 直接作为 message 事件发送，保持原始格式
    # 前端会根据 eventData.type === "non_standard" && eventData.value?.type 来处理
    yield f"event: message\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"


def _process_content_block(block: dict):
    """处理内容块，生成 SSE 事件"""
    block_type = block.get("type")

    if block_type == "text":
        yield f"event: message\ndata: {json.dumps({'type': 'text', 'content': block.get('text', '')}, ensure_ascii=False)}\n\n"
    elif block_type == "task_progress":
        yield f"event: task_progress\ndata: {json.dumps(block.get('tasks', []), ensure_ascii=False)}\n\n"
    elif block_type == "file":
        yield f"event: file\ndata: {json.dumps(block, ensure_ascii=False)}\n\n"
    elif block_type == "file_title":
        yield f"event: file_title\ndata: {json.dumps({'title': block.get('title', '')}, ensure_ascii=False)}\n\n"
    elif block_type == "report_source_data":
        yield f"event: report_source_data\ndata: {json.dumps(block.get('data', {}), ensure_ascii=False)}\n\n"
    elif block_type == "excel_data":
        yield f"event: excel_data\ndata: {json.dumps(block.get('data', {}), ensure_ascii=False)}\n\n"
