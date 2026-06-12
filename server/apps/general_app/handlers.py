"""
general_app 的 completion handler

SSE 消息格式遵循 LangChain 官方规范：
- data: {"content": "...", "id": "...", "type": "AIMessageChunk", ...}
- data: [DONE]

文件处理说明（业内标准做法）：
1. 用户消息只包含原始问题（会被保存到消息历史）
2. 文件与会话关联，存储在 uploaded_files 表
3. 在 chat_node 中按需从数据库加载文件内容，作为 SystemMessage 注入
4. 文件内容不会被持久化到消息历史，但整个会话期间可用
"""

import json
from typing import Dict, Any, Optional, AsyncGenerator
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from .agent import get_graph, get_checkpointer
from utils.logger import get_logger

logger = get_logger(__name__)


async def general_completion_handler(
    conversation_id: str,
    query: str,
    options: Optional[Dict[str, Any]] = None,
    file_ids: Optional[list] = None
) -> StreamingResponse:
    """
    general_assistant 的 completion 处理器

    SSE 格式（LangChain 官方规范）：
        data: {"content": "...", "type": "AIMessageChunk", ...}
        data: [DONE]

    文件处理逻辑：
        - 用户消息使用 LangChain 标准多模态格式：content 为列表
        - 文件已通过上传接口关联到 conversation_id
        - chat_node 会自动从数据库加载该会话的所有文件作为上下文
    """
    async def generate() -> AsyncGenerator[str, None]:
        try:
            query_text = query.strip()

            if not query_text:
                yield f"data: {json.dumps({'error': '消息内容不能为空'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            config = {"configurable": {"thread_id": conversation_id}}

            # 构建消息（文件引用存入 additional_kwargs，不影响 LLM 调用）
            additional_kwargs = {}
            if file_ids:
                additional_kwargs["file_ids"] = file_ids

            input_state = {
                "messages": [HumanMessage(content=query_text, additional_kwargs=additional_kwargs)],
            }

            async with get_checkpointer() as checkpointer:
                graph = get_graph(checkpointer)

                async for chunk, metadata in graph.astream(
                    input_state,
                    config,
                    stream_mode="messages"
                ):
                    if hasattr(chunk, 'content') and chunk.content:
                        yield f"data: {json.dumps(chunk.model_dump(), ensure_ascii=False)}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"对话处理失败: {str(e)}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )
