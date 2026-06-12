"""
Word Fit App Handlers
用于处理通用 Conversation Router 的请求
"""
import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Dict, Any, AsyncGenerator
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from utils.lang_util.conversation import get_checkpointer
from utils.logger import get_logger
from utils.timing import log_phase_timing, log_route_timing, timing_elapsed_ms, timing_now
from .agent.graph import get_graph
from .agent.nodes import build_batch_file_contents_for_extract, extract_info_node, resolve_wordfit_extract_model
from utils.wordfit_trace_storage import build_wordfit_trace_context
from utils.storage_paths import resolve_complaint_scope

logger = get_logger(__name__)

_OCR_METRICS_DIR = os.getenv("WORDFIT_OCR_METRICS_DIR") or os.path.join("uploads", "ocr_metrics")
# TODO: 您可以在此处自定义各节点开始和结束时的提示内容
NODE_MESSAGES = {
    "fetch_file": {
        "name": "获取文件",
        "start": "正在获取文件...",
        "end": "文件获取完成。"
    },
    "resolve_file": {
        "name": "解析文件路径",
        "start": "",
        "end": ""
    },
    "read_text_node": {
        "name": "文本读取",
        "start": "正在读取文件内容...",
        "end": "文件内容读取完成。"
    },
    "ocr_node": {
        "name": "OCR识别",
        "start": "正在识别文件内容...",
        "end": "图像识别完成。"
    },
    "doc_node": {
        "name": "文档解析",
        "start": "正在解析文档内容...",
        "end": "文档内容解析完成。"
    },
    "extract_info": {
        "name": "信息提取",
        "start": "正在进行智能信息提取...",
        "end": ""
    },
    "locate_info": {
        "name": "信息定位",
        "start": "",
        "end": "智能信息提取完成。"
    }
}


def _safe_metric_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in str(value or "").strip())


def _write_extract_metric(metrics_batch_id: str | None, request_id: str, payload: dict[str, Any]) -> None:
    batch = _safe_metric_segment(str(metrics_batch_id or ""))
    safe_request_id = _safe_metric_segment(str(request_id or ""))
    if not batch or not safe_request_id:
        return
    target_dir = Path(_OCR_METRICS_DIR).resolve() / batch
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{safe_request_id}.jsonl"
    with open(target_path, "a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps({"request_id": safe_request_id, **payload}, ensure_ascii=False) + "\n")


def _sse_line(payload: dict[str, Any], base_id: str) -> str:
    event = dict(payload)
    if base_id and "base_id" not in event:
        event["base_id"] = base_id
    return json.dumps(event, ensure_ascii=False) + "\n"


# 解析模型返回的 JSON，兼容 ```json fenced code block。
def parse_extract_content(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.splitlines() if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    try:
        return json.loads(stripped)
    except Exception:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            return content
        try:
            return json.loads(stripped[start:end + 1])
        except Exception:
            return content


def normalize_handler_file_ids(options: dict[str, Any], file_ids: list[str] | None) -> list[str]:
    """统一解析 WordFit handler 的单/多文件输入。"""
    option_file_ids = options.get("file_ids")
    values = option_file_ids if isinstance(option_file_ids, list) else file_ids or []
    normalized = sorted({str(value or "").strip() for value in values if str(value or "").strip()})
    option_file_id = str(options.get("file_id") or "").strip()
    if normalized:
        if option_file_id and option_file_id not in normalized:
            raise ValueError("file_id and file_ids point to different files")
        return normalized
    if option_file_id:
        return [option_file_id]
    return []


def resolve_wordfit_scope_meta(options: dict[str, Any]) -> dict[str, str]:
    """解析 WordFit 透传的举报件 scope 日志字段。"""
    scope_context = resolve_complaint_scope(options.get("scope"), options.get("org_id"), options.get("entity_id"))
    return scope_context.to_meta()


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
    
    request_file_ids = normalize_handler_file_ids(options, file_ids)
    file_id = request_file_ids[0] if request_file_ids else ""
        
    extract_fields = options.get("extract_fields", [])
    
    if not file_id:
        raise ValueError("Missing 'file_id' in options")
    
    # 构造初始状态
    locate_enabled = options.get("locate_enabled", False)
    analysis_scene = str(options.get("analysis_scene") or "").strip()
    analysis_user_id = str(options.get("analysis_user_id") or "").strip() or None
    analysis_match_strategy = str(options.get("analysis_match_strategy") or "").strip() or None
    scope_meta = resolve_wordfit_scope_meta(options)
    scope = scope_meta["scope"]
    org_id = scope_meta["org_id"]
    entity_id = scope_meta["entity_id"]
    scope_path = scope_meta["scope_path"]
    force = bool(options.get("force"))
    extract_model = str(options.get("extract_model") or "").strip() or None
    resolved_extract_model = resolve_wordfit_extract_model(extract_model) or ""
    ocr_model = str(options.get("ocr_model") or "").strip() or None
    ocr_metrics_batch_id = str(options.get("ocr_metrics_batch_id") or "").strip() or None
    trace_context = build_wordfit_trace_context(request_file_ids, conversation_id, analysis_user_id)
    base_id = trace_context.get("base_id") or ""
    logger.info("[WordFit] locate_enabled=%s force=%s extract_model=%s ocr_model=%s user_id=%s base_id=%s scope=%s org_id=%s entity_id=%s file_ids=%s", locate_enabled, force, extract_model or "", ocr_model or "", analysis_user_id or "", base_id, scope, org_id, entity_id, request_file_ids)
    initial_state = {
        "file_id": file_id,
        "file_ids": request_file_ids,
        "extract_fields": extract_fields,
        "locate_enabled": locate_enabled,
        "analysis_scene": analysis_scene,
        "analysis_user_id": analysis_user_id,
        "analysis_match_strategy": analysis_match_strategy,
        "scope": scope,
        "org_id": org_id,
        "entity_id": entity_id,
        "scope_path": scope_path,
        "force": force,
        "extract_model": extract_model,
        "ocr_model": ocr_model,
        "ocr_metrics_batch_id": ocr_metrics_batch_id,
        "conversation_id": conversation_id,
        "base_id": base_id,
        "messages": [HumanMessage(content=query or "Start extraction")]
    }
    
    config = {"configurable": {"thread_id": conversation_id}}
    timing_extra = {"conversation_id": conversation_id, "file_id": ",".join(request_file_ids), "base_id": base_id, "user_id": analysis_user_id or "", "scope": scope, "org_id": org_id, "entity_id": entity_id, "scope_path": scope_path}
    
    async def event_generator() -> AsyncGenerator[str, None]:
        route_started_at = timing_now()
        route_success = False
        node_started_at: Dict[str, float] = {}
        extract_first_token_ms: int | None = None
        extract_output_chars = 0
        final_result: dict[str, Any] | None = None
        error_text = ""
        if len(request_file_ids) > 1:
            try:
                yield _sse_line({"type": "node", "status": "start", "node": "fetch_file", "node_name": "获取文件", "content": "正在获取文件..."}, base_id)
                merged_parts: list[str] = []
                ocr_event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

                async def put_ocr_event(event_data: dict[str, Any]) -> None:
                    await ocr_event_queue.put(event_data)

                content_task = asyncio.create_task(build_batch_file_contents_for_extract(request_file_ids, initial_state, put_ocr_event))
                while True:
                    if content_task.done() and ocr_event_queue.empty():
                        break
                    try:
                        event_data = await asyncio.wait_for(ocr_event_queue.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        continue
                    yield _sse_line(event_data, base_id)
                contents = await content_task
                for index, item_file_id in enumerate(request_file_ids, start=1):
                    content = contents[index - 1] if index - 1 < len(contents) else ""
                    merged_parts.append(f"【材料{index}：{item_file_id}】\n{content}")
                yield _sse_line({"type": "node", "status": "end", "node": "fetch_file", "node_name": "获取文件", "content": "文件获取完成。"}, base_id)

                yield _sse_line({"type": "node", "status": "start", "node": "extract_info", "node_name": "信息提取", "content": "正在进行智能信息提取..."}, base_id)
                extract_started_at = timing_now()
                output = await extract_info_node({**initial_state, "file_id": ",".join(request_file_ids), "file_ids": request_file_ids, "file_content": "\n\n".join(merged_parts).strip(), "locate_enabled": False})
                log_phase_timing(logger, "word_fit_app", "POST /api/word-fit/extract", "extract_info", timing_elapsed_ms(extract_started_at), True, {**timing_extra, "file_count": len(request_file_ids)})
                messages = output.get("messages", [])
                if messages:
                    last_msg = messages[-1]
                    content = last_msg.content if hasattr(last_msg, "content") else last_msg.get("content", "")
                    extract_result = parse_extract_content(content)
                else:
                    extract_result = {}
                route_success = True
                final_result = {"extract": extract_result, "locate": {}}
                yield _sse_line({"type": "result", "node": "extract_info", "data": final_result}, base_id)
            except Exception as exc:
                error_text = str(exc)
                logger.error("[WordFit] multi-file extract failed user_id=%s base_id=%s file_ids=%s err=%s", analysis_user_id or "", base_id, request_file_ids, exc)
                yield _sse_line({"type": "error", "content": str(exc)}, base_id)
            finally:
                log_route_timing(logger, "word_fit_app", "POST /api/word-fit/extract", timing_elapsed_ms(route_started_at), route_success, {**timing_extra, "file_count": len(request_file_ids)})
            return

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
                            yield _sse_line(event_data, base_id)

                    # 节点结束事件
                    elif kind == "on_chain_end" and name in NODE_MESSAGES:
                        msg_config = NODE_MESSAGES[name]
                        started_at = node_started_at.pop(name, None)
                        output = event.get("data", {}).get("output", {})
                        if started_at is not None:
                            duration_ms = timing_elapsed_ms(started_at)
                            log_phase_timing(logger, "word_fit_app", "POST /api/word-fit/extract", name, duration_ms, True, timing_extra)
                        event_data = {
                            "type": "node",
                            "status": "end",
                            "node": name,
                            "node_name": msg_config["name"],
                            "content": msg_config["end"]
                        }
                        if msg_config["end"]:
                            yield _sse_line(event_data, base_id)

                        # 处理 extract_info 节点结果
                        if name == "extract_info":
                            try:
                                extract_prompt_chars = int(output.get("extract_prompt_chars") or 0) if isinstance(output, dict) else 0
                                extract_started_at = started_at
                                # extract_info_node 返回的是 {"messages": [response]}
                                messages = output.get("messages", [])
                                if messages:
                                    last_msg = messages[-1]
                                    # 兼容对象或字典形式
                                    content = last_msg.content if hasattr(last_msg, "content") else last_msg.get("content", "")
                                    if not extract_output_chars:
                                        extract_output_chars = len(str(content or ""))
                                    
                                    extract_result = parse_extract_content(content)
                                if extract_started_at is not None:
                                    _write_extract_metric(
                                        ocr_metrics_batch_id,
                                        conversation_id,
                                        {
                                            "type": "extract_llm",
                                            "model": resolved_extract_model,
                                            "prompt_chars": extract_prompt_chars,
                                            "output_chars": extract_output_chars,
                                            "first_token_ms": extract_first_token_ms or 0,
                                            "llm_duration_ms": timing_elapsed_ms(extract_started_at),
                                        },
                                    )
                                
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
                                    final_result = combined_result
                                    yield _sse_line(result_event, base_id)
                            except Exception as e:
                                logger.error("[WordFit] Error processing extract_info result user_id=%s base_id=%s file_id=%s err=%s", analysis_user_id or "", base_id, file_id, e)
                                error_text = str(e)
                    
                        # 处理 locate_info 节点结果
                        elif name == "locate_info":
                            try:
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
                                final_result = combined_result
                                yield _sse_line(result_event, base_id)
                            except Exception as e:
                                logger.error("[WordFit] Error processing locate_info result user_id=%s base_id=%s file_id=%s err=%s", analysis_user_id or "", base_id, file_id, e)
                                error_text = str(e)

                    # LLM 流式输出事件
                    elif kind == "on_chat_model_stream":
                        chunk = event["data"]["chunk"]
                        if chunk.content:
                            if "extract_info" in node_started_at:
                                if extract_first_token_ms is None:
                                    extract_first_token_ms = timing_elapsed_ms(node_started_at["extract_info"])
                                extract_output_chars += len(str(chunk.content))
                            event_data = {
                                "type": "llm_chunk",
                                "content": chunk.content
                            }
                            yield _sse_line(event_data, base_id)
                    elif kind == "on_custom_event" and name == "word_fit_ocr_progress":
                        event_data = event.get("data", {})
                        if event_data:
                            yield _sse_line(event_data, base_id)
            finally:
                for node_name, started_at in list(node_started_at.items()):
                    duration_ms = timing_elapsed_ms(started_at)
                    log_phase_timing(logger, "word_fit_app", "POST /api/word-fit/extract", node_name, duration_ms, False, timing_extra)
                    with suppress(KeyError):
                        node_started_at.pop(node_name)
                log_route_timing(logger, "word_fit_app", "POST /api/word-fit/extract", timing_elapsed_ms(route_started_at), route_success, {**timing_extra, "locate_enabled": locate_enabled, "force": force, "extract_model": extract_model or "", "ocr_model": ocr_model or ""})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
