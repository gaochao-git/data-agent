"""
Word Fit App Handlers
用于处理通用 Conversation Router 的请求
"""
import json
from contextlib import suppress
from typing import Dict, Any, AsyncGenerator
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from utils.lang_util.conversation import get_checkpointer
from utils.logger import get_logger
from utils.timing import log_phase_timing, log_route_timing, timing_elapsed_ms, timing_now
from .agent.graph import get_graph

logger = get_logger(__name__)

# TODO: 您可以在此处自定义各节点开始和结束时的提示内容
NODE_MESSAGES = {
    "fetch_file": {
        "name": "获取文件",
        "start": "1/4 正在获取文件...",
        "end": "1/4 文件获取完成。"
    },
    "resolve_file": {
        "name": "解析文件路径",
        "start": "",
        "end": ""
    },
    "read_text_node": {
        "name": "文本读取",
        "start": "3/4 正在读取文件内容...",
        "end": "3/4 文件内容读取完成。"
    },
    "ocr_node": {
        "name": "OCR识别",
        "start": "2/4 正在图像识别文件内容...",
        "end": "2/4 图像识别完成。"
    },
    "doc_node": {
        "name": "文档解析",
        "start": "2/4 正在解析文档内容...",
        "end": "2/4 文档内容解析完成。"
    },
    "extract_info": {
        "name": "信息提取",
        "start": "4/4 正在进行智能信息提取...",
        "end": ""
    },
    "locate_info": {
        "name": "信息定位",
        "start": "",
        "end": "4/4 智能信息提取完成。"
    }
}

async def word_fit_completion_handler(
    conversation_id: str,
    query: str,
    options: Dict[str, Any] = None,
    file_ids: list[str] = None
) -> StreamingResponse:
    """
    处理 Word Fit 提取请求
    
    Args:
        conversation_id: 会话 ID
        query: 用户查询 (在本场景下可能不重要，主要是 options 里的参数)
        options: 包含 file_id 和 extract_fields
        file_ids: 关联文件列表
    """
    options = options or {}
    
    # 优先从 options 获取 file_id，如果没传，尝试取 file_ids 的第一个
    file_id = options.get("file_id")
    if not file_id and file_ids:
        file_id = file_ids[0]
        
    extract_fields = options.get("extract_fields", [])
    
    if not file_id:
        raise ValueError("Missing 'file_id' in options")
    
    # 构造初始状态
    locate_enabled = options.get("locate_enabled", False)
    analysis_scene = str(options.get("analysis_scene") or "").strip()
    analysis_user_id = str(options.get("analysis_user_id") or "").strip() or None
    analysis_match_strategy = str(options.get("analysis_match_strategy") or "").strip() or None
    logger.info(f"[WordFit] locate_enabled: {locate_enabled}")
    initial_state = {
        "file_id": file_id,
        "extract_fields": extract_fields,
        "locate_enabled": locate_enabled,
        "analysis_scene": analysis_scene,
        "analysis_user_id": analysis_user_id,
        "analysis_match_strategy": analysis_match_strategy,
        "messages": [HumanMessage(content=query or "Start extraction")]
    }
    
    config = {"configurable": {"thread_id": conversation_id}}
    
    async def event_generator() -> AsyncGenerator[str, None]:
        route_started_at = timing_now()
        route_success = False
        node_started_at: Dict[str, float] = {}
        async with get_checkpointer() as checkpointer:
            graph = get_graph(checkpointer)
            
            # 存储提取和定位结果
            extract_result = {}
            locate_result = {}
            
            # 运行图
            try:
                async for event in graph.astream_events(initial_state, config, version="v2"):
                    kind = event["event"]
                    name = event.get("name", "")

                    # 节点开始事件
                    if kind == "on_chain_start" and name in NODE_MESSAGES:
                        node_started_at[name] = timing_now()
                        msg_config = NODE_MESSAGES[name]
                        event_data = {
                            "type": "node",
                            "status": "start",
                            "node": name,
                            "node_name": msg_config["name"],
                            "content": msg_config["start"]
                        }
                        if msg_config["start"]:
                            yield json.dumps(event_data, ensure_ascii=False) + "\n"

                    # 节点结束事件
                    elif kind == "on_chain_end" and name in NODE_MESSAGES:
                        msg_config = NODE_MESSAGES[name]
                        started_at = node_started_at.pop(name, None)
                        if started_at is not None:
                            log_phase_timing(logger, "word_fit_app", "POST /api/word-fit/extract", name, timing_elapsed_ms(started_at), True, {"conversation_id": conversation_id, "file_id": file_id})
                        event_data = {
                            "type": "node",
                            "status": "end",
                            "node": name,
                            "node_name": msg_config["name"],
                            "content": msg_config["end"]
                        }
                        if msg_config["end"]:
                            yield json.dumps(event_data, ensure_ascii=False) + "\n"

                        # 处理 extract_info 节点结果
                        if name == "extract_info":
                            try:
                                output = event.get("data", {}).get("output", {})
                                # extract_info_node 返回的是 {"messages": [response]}
                                messages = output.get("messages", [])
                                if messages:
                                    last_msg = messages[-1]
                                    # 兼容对象或字典形式
                                    content = last_msg.content if hasattr(last_msg, "content") else last_msg.get("content", "")
                                    
                                    # 尝试解析 JSON
                                    try:
                                        extract_result = json.loads(content)
                                    except Exception:
                                        extract_result = content
                                
                                # 检查是否需要执行定位节点
                                if not initial_state.get("locate_enabled", False):
                                    # 如果不需要执行定位，直接返回结果
                                    combined_result = {
                                        "extract": extract_result,
                                        "locate": {}
                                    }
                                    
                                    result_event = {
                                        "type": "result",
                                        "node": "extract_info",
                                        "data": combined_result
                                    }
                                    route_success = True
                                    yield json.dumps(result_event, ensure_ascii=False) + "\n"
                            except Exception as e:
                                logger.error(f"[WordFit] Error processing extract_info result: {e}")
                    
                        # 处理 locate_info 节点结果
                        elif name == "locate_info":
                            try:
                                output = event.get("data", {}).get("output", {})
                                # locate_info_node 返回的是 {"info_locations": {...}}
                                locate_result = output.get("info_locations", {})
                                
                                # 整合输出完整结果
                                combined_result = {
                                    "extract": extract_result,
                                    "locate": locate_result
                                }
                                
                                result_event = {
                                    "type": "result",
                                    "node": "extract_info",
                                    "data": combined_result
                                }
                                route_success = True
                                yield json.dumps(result_event, ensure_ascii=False) + "\n"
                            except Exception as e:
                                logger.error(f"[WordFit] Error processing locate_info result: {e}")

                    # LLM 流式输出事件
                    elif kind == "on_chat_model_stream":
                        chunk = event["data"]["chunk"]
                        if chunk.content:
                            event_data = {
                                "type": "llm_chunk",
                                "content": chunk.content
                            }
                            yield json.dumps(event_data, ensure_ascii=False) + "\n"
                    elif kind == "on_custom_event" and name == "word_fit_ocr_progress":
                        event_data = event.get("data", {})
                        if event_data:
                            yield json.dumps(event_data, ensure_ascii=False) + "\n"
            finally:
                for node_name, started_at in list(node_started_at.items()):
                    log_phase_timing(logger, "word_fit_app", "POST /api/word-fit/extract", node_name, timing_elapsed_ms(started_at), False, {"conversation_id": conversation_id, "file_id": file_id})
                    with suppress(KeyError):
                        node_started_at.pop(node_name)
                log_route_timing(logger, "word_fit_app", "POST /api/word-fit/extract", timing_elapsed_ms(route_started_at), route_success, {"conversation_id": conversation_id, "file_id": file_id, "locate_enabled": locate_enabled})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
