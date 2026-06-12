"""
chat_data_app 的 completion handler

使用 LangGraph interrupt() 函数实现 Human-in-the-Loop:
- execute_sql_tool 内部调用 interrupt() 暂停等待用户确认
- generate_chart_tool 不调用 interrupt()，自动执行
- 用户确认后通过 Command(resume=...) 恢复执行

语义模式：
- 当用户开启语义模式时，传入 semantic_config_id
- 系统加载对应语义配置，动态添加 semantic_to_sql_tool 工具
- LLM 根据语义配置中的指标/维度，自动调用工具生成 SQL
"""

import json
import time
from typing import Dict, Any, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.types import Command


def _get_timestamp() -> str:
    """获取当前时间戳字符串，格式：YYYY-MM-DD HH:MM:SS"""
    return time.strftime('%Y-%m-%d %H:%M:%S')

from utils.logger import get_logger
from utils.lang_util import conversation
from .agent import (
    get_graph,
    get_checkpointer,
    get_all_tables,
    get_tables_create_format,
    llm_select_tables,
)
from .services.datasource_manager import get_datasource_manager

logger = get_logger(__name__)

AGENT_ID = "chat_data"


def _format_sse(event: str, data: dict | str) -> str:
    """
    格式化 SSE 事件

    Args:
        event: 事件类型 (message, tool, error, done)
        data: 事件数据（字典会被 JSON 序列化，字符串直接使用）

    Returns:
        符合 SSE 规范的字符串: "event: {event}\ndata: {data}\n\n"
    """
    if isinstance(data, dict):
        data_str = json.dumps(data, ensure_ascii=False)
    else:
        data_str = data
    return f"event: {event}\ndata: {data_str}\n\n"


def _format_tool_event(
    tool_name: str,
    tool_call_id: str | None,
    status: str,  # "pending" | "running" | "completed"
    args: dict | None = None,
    result: str | None = None,
    message: str | None = None,
    timestamp: str | None = None
) -> str:
    """
    格式化统一的 tool 事件

    Args:
        tool_name: 工具名称
        tool_call_id: 工具调用 ID
        status: 状态 (pending, running, completed)
        args: 工具参数（pending 状态时使用）
        result: 工具执行结果（completed 状态时使用）
        message: 提示信息（pending 状态时使用）
        timestamp: 时间戳（格式：YYYY-MM-DD HH:MM:SS）
    """
    data = {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "status": status,
        "timestamp": timestamp or _get_timestamp(),
    }
    if args is not None:
        data["args"] = args
    if result is not None:
        data["result"] = result
    if message is not None:
        data["message"] = message
    return _format_sse("tool", data)


def _register_running_tool(running_tool_ids: list[str], tool_call_id: str) -> None:
    """登记运行中的工具，保持插入顺序。"""
    if tool_call_id and tool_call_id not in running_tool_ids:
        running_tool_ids.append(tool_call_id)


def _resolve_active_tool_call_id(
    chunk_id: str | None,
    active_tool_call_id: str | None,
    running_tool_ids: list[str],
) -> str | None:
    """解析当前 chunk 应归属的 tool_call_id。"""
    normalized_chunk_id = str(chunk_id or "").strip() or None
    if normalized_chunk_id:
        return normalized_chunk_id
    if active_tool_call_id:
        return active_tool_call_id
    if running_tool_ids:
        return running_tool_ids[-1]
    return None


def _finish_running_tool(
    running_tool_ids: list[str],
    active_tool_call_id: str | None,
    tool_call_id: str | None,
) -> str | None:
    """工具完成后清理运行态，并返回新的活跃 tool_call_id。"""
    normalized_tool_call_id = str(tool_call_id or "").strip()
    if normalized_tool_call_id and normalized_tool_call_id in running_tool_ids:
        running_tool_ids.remove(normalized_tool_call_id)
    if active_tool_call_id == normalized_tool_call_id:
        return running_tool_ids[-1] if running_tool_ids else None
    return active_tool_call_id


def _get_interrupt_info(state) -> dict | None:
    """
    从状态中提取中断信息（由工具内部的 interrupt() 触发）

    LangGraph interrupt 结构：
    - state.tasks[].interrupts[].value: interrupt() 传入的数据
    - state.values.messages[-1].tool_calls: 最后一条 AI 消息的工具调用

    Returns:
        如果有中断: {"tool_name", "tool_call_id", "sql", "message", ...}
        否则: None
    """
    if not state or not state.tasks:
        return None

    # 从 tasks 中找到第一个 interrupt
    for task in state.tasks:
        if not (hasattr(task, 'interrupts') and task.interrupts):
            continue

        interrupt = task.interrupts[0]  # 取第一个中断
        if not hasattr(interrupt, 'value'):
            continue

        value = interrupt.value
        if not isinstance(value, dict):
            continue

        # 从最后一条消息的 tool_calls 中获取 tool_call_id 和 tool_name
        tool_call_id = None
        tool_name = None
        messages = state.values.get("messages", []) if state.values else []
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                tc = last_msg.tool_calls[0]
                tool_call_id = tc.get("id")
                tool_name = tc.get("name")

        # 直接透传 interrupt.value，补充 tool 信息
        return {
            **value,  # 包含 sql, message 等
            "tool_name": tool_name or "unknown_tool",
            "tool_call_id": tool_call_id,
        }

    return None


def _build_execution_target_info(datasource_id: str | None, member_datasource_ids: list[str] | None = None) -> str:
    """构造执行对象说明，注入给大模型理解执行范围。"""
    if not datasource_id:
        return ""

    try:
        ds_manager = get_datasource_manager()
        execution_targets = ds_manager.get_execution_datasources(datasource_id, member_datasource_ids)
    except Exception as err:
        logger.warning(f"构造执行对象说明失败: {err}")
        return ""

    if not execution_targets:
        return ""

    lines = ["## 当前执行对象"]
    if len(execution_targets) == 1:
        lines.append("本次查询仅在 1 个执行对象上运行：")
    else:
        lines.append(f"本次查询会在 {len(execution_targets)} 个执行对象上分别运行，同一条 SQL 会返回多份结果：")

    for index, target in enumerate(execution_targets, start=1):
        member_name = target.get("member_name") or target.get("member_datasource_id") or f"执行对象{index}"
        lines.append(f"{index}. {member_name}")

    return "\n".join(lines)


def _get_semantic_config(
    datasource_id: str | None,
    semantic_config_id: str | None,
    user_query: str | None = None
) -> dict | None:
    """
    获取语义配置

    Args:
        datasource_id: 数据源 ID
        semantic_config_id: 语义配置 ID
        user_query: 用户问题（保留参数以兼容现有调用）

    Returns:
        语义配置字典，或 None（未找到或未启用）
    """
    if not semantic_config_id:
        return None

    # 指定了配置 ID，查找指定配置
    if not datasource_id:
        return None

    try:
        ds_manager = get_datasource_manager()
        datasource = ds_manager.get_datasource(datasource_id)
        if not datasource:
            return None

        semantic_configs = datasource.get("semantic_configs", [])
        source_type = datasource.get("source_type", "mysql")

        # 从 semantic_configs 数组中查找指定的配置
        for cfg in semantic_configs:
            if cfg.get("id") == semantic_config_id:
                if not cfg.get("enabled"):
                    logger.warning(f"指定的语义配置未启用: {semantic_config_id}")
                    return None

                result = cfg.copy()
                if 'relationships' in result and 'joins' not in result:
                    result['joins'] = result['relationships']
                if source_type:
                    result['source_type'] = source_type
                if semantic_config_id and not result.get("id"):
                    result["id"] = semantic_config_id
                logger.info(f"加载语义配置: {result.get('name', semantic_config_id)}")
                return result

        logger.warning(f"未找到指定的语义配置: {semantic_config_id}")
        return None
    except Exception as e:
        logger.warning(f"获取语义配置异常: {e}")
        return None


async def _stream_sql_generation(
    thread_id: str,
    user_query: str,
    datasource_id: str | None = None,
    user_id: str | None = None,
    member_datasource_ids: list[str] | None = None,
    mode: str = "generate",
    semantic_config_id: str | None = None,
    temp_semantic_config: dict | None = None,
    request: Request | None = None,
    model_id: str | None = None
):
    """流式执行 SQL 生成"""
    logger.info(f"开始执行，thread_id: {thread_id}, mode: {mode}, semantic_config_id: {semantic_config_id}, temp_config: {bool(temp_semantic_config)}")

    # 获取语义配置
    # 优先级：temp_semantic_config > semantic_config_id
    semantic_config = None
    if temp_semantic_config:
        # 使用临时配置（用于测试，不保存到数据库）
        semantic_config = temp_semantic_config
        logger.info(f"使用临时语义配置（测试模式）")
    elif semantic_config_id is not None:
        # 从数据库获取配置
        semantic_config = _get_semantic_config(datasource_id, semantic_config_id, user_query)
        if semantic_config:
            logger.info(f"使用语义模式: {semantic_config.get('name', semantic_config.get('id'))}")
        else:
            logger.warning("query 模式未找到可用语义配置")
    elif mode == "query":
        logger.warning("query 模式未传 semantic_config_id")

    if mode == "query" and semantic_config_id is None:
        yield _format_sse("error", {"error": "query 模式必须传入 semantic_config_id"})
        yield _format_sse("done", "[DONE]")
        return

    if mode == "query" and not semantic_config:
        yield _format_sse("error", {"error": f"query 模式未找到可用语义配置: {semantic_config_id}"})
        yield _format_sse("done", "[DONE]")
        return

    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": ""
        }
    }

    try:
        async with get_checkpointer() as checkpointer:
            # 构建 graph，传入语义配置（如果有）
            # 语义模式时，graph 会添加 semantic_to_sql_tool 工具
            graph = get_graph(checkpointer=checkpointer, semantic_config=semantic_config, model_id=model_id)

            # 语义模式不需要表结构，semantic_to_sql_tool 内部已封装
            # NL2SQL 模式需要加载表结构给 LLM
            tables_ddl_info = ""
            new_loaded_names = []

            if not semantic_config:
                # NL2SQL 模式：加载表结构
                all_tables = get_all_tables(datasource_id)

                # 获取历史状态，检查已加载的表
                loaded_table_names: set[str] = set()
                try:
                    state = await graph.aget_state(config)
                    if state and state.values:
                        loaded_table_names = set(state.values.get("loaded_table_names") or [])
                        logger.info(f"历史已加载的表: {loaded_table_names}")
                except Exception as e:
                    logger.debug(f"获取历史状态失败（可能是新会话）: {e}")

                # 根据用户问题选择需要的表
                selected_table_names = await llm_select_tables(user_query, all_tables)
                logger.info(f"本次需要的表: {selected_table_names}")

                # 计算需要新加载的表
                new_table_names = set(selected_table_names) - loaded_table_names
                logger.info(f"需要新加载的表: {new_table_names}")

                # 构建本次传入的状态
                if new_table_names:
                    tables_ddl_info = get_tables_create_format(all_tables, list(new_table_names))
                    new_loaded_names = list(new_table_names)
                elif selected_table_names:
                    logger.info("复用历史表结构信息")

            initial_state = {
                "messages": [HumanMessage(
                    content=user_query,
                    additional_kwargs={"timestamp": _get_timestamp()}
                )],
                "tables_ddl_info": tables_ddl_info,
                "loaded_table_names": new_loaded_names,
                "mode": mode,
                "datasource_id": datasource_id,  # 传递数据源ID，用于真实SQL执行
                "user_id": user_id,
                "member_datasource_ids": member_datasource_ids or [],
                "execution_target_info": _build_execution_target_info(datasource_id, member_datasource_ids),
            }

            # 用于跟踪已发送 running 状态的工具
            running_tool_ids: list[str] = []
            active_tool_call_id: str | None = None
            # 用于累积 tool_call_chunks 的 args（JSON 字符串拼接）
            tool_call_args_buffer: dict[str, str] = {}
            # 用于存储工具名称
            tool_call_names: dict[str, str] = {}

            # 流式执行
            # 使用 stream_mode="messages" 捕获 LLM 输出
            # 通过 metadata["langgraph_node"] 区分来源，过滤工具内部的 LLM 调用
            async for event in graph.astream(
                initial_state,
                config=config,
                stream_mode="messages"
            ):
                # 检测客户端断连，停止消费事件
                if request and await request.is_disconnected():
                    logger.info(f"客户端已断开，停止流式输出: {thread_id}")
                    return

                message_chunk, metadata = event

                # 过滤：只保留来自 agent 节点的消息（排除 tools 节点内部的 LLM 调用）
                node_name = metadata.get("langgraph_node", "")
                msg_type = getattr(message_chunk, 'type', 'ai')
                # AI 消息：只接受来自 agent 节点的
                # Tool 消息：来自 tools 节点，需要保留
                if msg_type == 'ai' and node_name != "agent":
                    logger.debug(f"[过滤] 跳过来自 {node_name} 节点的 AI 消息")
                    continue

                # 检查是否有内容或工具调用
                has_content = hasattr(message_chunk, 'content') and message_chunk.content
                has_tool_calls = hasattr(message_chunk, 'tool_calls') and message_chunk.tool_calls
                has_tool_call_chunks = hasattr(message_chunk, 'tool_call_chunks') and message_chunk.tool_call_chunks
                if not has_content and not has_tool_calls and not has_tool_call_chunks:
                    continue

                # 调试：打印 AI 消息内容
                if msg_type == 'ai' and has_content:
                    logger.debug(f"[AI Content from {node_name}] {message_chunk.content[:200]}")

                # 根据消息类型选择事件名
                msg_type = getattr(message_chunk, 'type', 'ai')
                if msg_type == 'tool':
                    event_name = "tool"
                else:
                    event_name = "message"

                # 检测 tool_calls 开始，发送 running 状态，并初始化 args buffer
                if has_tool_calls:
                    for tc in message_chunk.tool_calls:
                        tc_id = tc.get("id")
                        tc_name = tc.get("name")
                        # 只有当 id 和 name 都存在且未发送过时，发送 running 状态
                        if tc_id and tc_name and tc_id not in running_tool_ids:
                            _register_running_tool(running_tool_ids, tc_id)
                            tool_call_names[tc_id] = tc_name
                            tool_call_args_buffer[tc_id] = ""  # 初始化 args buffer
                            yield _format_tool_event(
                                tool_name=tc_name,
                                tool_call_id=tc_id,
                                status="running",
                            )

                # 累积 tool_call_chunks 的 args（JSON 字符串）
                # 注意：LangChain 流式模式下，只有第一个 chunk 有 id，后续 chunks 的 id 为 None
                # 需要使用 index 来关联，或者记住当前活跃的 tool_call_id
                if has_tool_call_chunks:
                    for chunk in message_chunk.tool_call_chunks:
                        chunk_id = chunk.get("id")
                        chunk_args = chunk.get("args", "")
                        chunk_name = chunk.get("name")

                        # 如果有 id，记录为当前活跃的 tool_call（只在首次出现时初始化 buffer）
                        if chunk_id:
                            tool_call_names[chunk_id] = chunk_name or tool_call_names.get(chunk_id, "")
                            # 只有新的 tool_call_id 才初始化 buffer
                            if chunk_id not in tool_call_args_buffer:
                                tool_call_args_buffer[chunk_id] = ""
                            _register_running_tool(running_tool_ids, chunk_id)

                        # 优先使用 chunk 自带 id，其次使用最近一次明确记录的活跃 tool_call_id
                        current_tool_call_id = _resolve_active_tool_call_id(
                            chunk_id,
                            active_tool_call_id,
                            running_tool_ids,
                        )
                        if chunk_id:
                            active_tool_call_id = current_tool_call_id

                        # 累积 args
                        if current_tool_call_id and chunk_args:
                            if current_tool_call_id not in tool_call_args_buffer:
                                tool_call_args_buffer[current_tool_call_id] = ""
                            tool_call_args_buffer[current_tool_call_id] += chunk_args

                # ToolMessage: 工具执行完成，发送带有完整 args 的消息
                if msg_type == 'tool':
                    tool_call_id = getattr(message_chunk, 'tool_call_id', '')
                    # 解析累积的 args JSON 字符串
                    accumulated_args = {}
                    if tool_call_id and tool_call_id in tool_call_args_buffer:
                        args_str = tool_call_args_buffer[tool_call_id]
                        if args_str:
                            try:
                                accumulated_args = json.loads(args_str)
                            except json.JSONDecodeError:
                                logger.warning(f"无法解析工具参数: {args_str[:100]}")
                        # 清理 buffer
                        del tool_call_args_buffer[tool_call_id]
                    active_tool_call_id = _finish_running_tool(running_tool_ids, active_tool_call_id, tool_call_id)

                    # 构造增强的消息，包含完整的 args 和时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    if accumulated_args:
                        # 添加累积的 args 到消息中（前端可以从这里获取）
                        msg_data["accumulated_args"] = accumulated_args
                        msg_data["tool_name"] = tool_call_names.get(tool_call_id, "")
                    yield _format_sse(event_name, msg_data)
                else:
                    # AI 消息添加时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    yield _format_sse(event_name, msg_data)

            # 检查是否被 interrupt() 中断
            state = await graph.aget_state(config)
            interrupt_info = _get_interrupt_info(state)
            if interrupt_info:
                # 返回待确认的工具信息（status: pending）
                sql_preview = interrupt_info.get('args', {}).get('sql', '')[:50]
                logger.info(f"检测到工具中断，等待用户确认: {sql_preview}...")
                yield _format_tool_event(
                    tool_name=interrupt_info.get("tool_name", "unknown_tool"),
                    tool_call_id=interrupt_info.get("tool_call_id"),
                    status="pending",
                    args=interrupt_info.get("args"),
                    message=interrupt_info.get("message"),
                )
                yield _format_sse("done", "[DONE]")
                return

        # 正常结束
        yield _format_sse("done", "[DONE]")

    except Exception as e:
        logger.error(f"执行失败: {e}", exc_info=True)
        yield _format_sse("error", {"error": str(e)})
        yield _format_sse("done", "[DONE]")


async def _continue_execution(
    thread_id: str,
    modified_sql: str | None = None,
    action: str = "approve",
    semantic_config: dict | None = None,
    request: Request | None = None,
    model_id: str | None = None
):
    """
    继续执行被 interrupt() 暂停的图

    Args:
        thread_id: 会话ID
        modified_sql: 修改后的 SQL（如果用户编辑了）
        action: 用户操作 "approve" 或 "reject"
        semantic_config: 语义配置（语义模式时需要，保持工具列表一致）
    """
    logger.info(f"继续执行，thread_id: {thread_id}, action: {action}, semantic_mode: {semantic_config is not None}")

    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": ""
        }
    }

    try:
        async with get_checkpointer() as checkpointer:
            # 传入语义配置，保持工具列表与原始执行一致
            graph = get_graph(checkpointer=checkpointer, semantic_config=semantic_config, model_id=model_id)

            # 构建恢复数据
            resume_data = {"action": action}
            if modified_sql is not None:
                resume_data["sql"] = modified_sql

            # 用于跟踪已发送 running 状态的工具
            running_tool_ids: list[str] = []
            active_tool_call_id: str | None = None
            # 用于累积 tool_call_chunks 的 args（JSON 字符串拼接）
            tool_call_args_buffer: dict[str, str] = {}
            # 用于存储工具名称
            tool_call_names: dict[str, str] = {}

            # 使用 Command(resume=...) 恢复执行
            # 通过 metadata["langgraph_node"] 过滤工具内部的 LLM 调用
            async for event in graph.astream(
                Command(resume=resume_data),
                config=config,
                stream_mode="messages"
            ):
                # 检测客户端断连
                if request and await request.is_disconnected():
                    logger.info(f"客户端已断开，停止流式输出: {thread_id}")
                    return

                message_chunk, metadata = event

                # 过滤：只保留来自 agent 节点的 AI 消息（排除 tools 节点内部的 LLM 调用）
                node_name = metadata.get("langgraph_node", "")
                msg_type = getattr(message_chunk, 'type', 'ai')
                if msg_type == 'ai' and node_name != "agent":
                    logger.debug(f"[过滤] 跳过来自 {node_name} 节点的 AI 消息")
                    continue

                has_content = hasattr(message_chunk, 'content') and message_chunk.content
                has_tool_calls = hasattr(message_chunk, 'tool_calls') and message_chunk.tool_calls
                has_tool_call_chunks = hasattr(message_chunk, 'tool_call_chunks') and message_chunk.tool_call_chunks
                if not has_content and not has_tool_calls and not has_tool_call_chunks:
                    continue

                # 根据消息类型选择事件名
                if msg_type == 'tool':
                    event_name = "tool"
                else:
                    event_name = "message"

                # 检测 tool_calls 开始，发送 running 状态，并初始化 args buffer
                if has_tool_calls:
                    for tc in message_chunk.tool_calls:
                        tc_id = tc.get("id")
                        tc_name = tc.get("name")
                        if tc_id and tc_name and tc_id not in running_tool_ids:
                            _register_running_tool(running_tool_ids, tc_id)
                            tool_call_names[tc_id] = tc_name
                            tool_call_args_buffer[tc_id] = ""
                            yield _format_tool_event(
                                tool_name=tc_name,
                                tool_call_id=tc_id,
                                status="running",
                            )

                # 累积 tool_call_chunks 的 args（JSON 字符串）
                # 注意：LangChain 流式模式下，只有第一个 chunk 有 id，后续 chunks 的 id 为 None
                if has_tool_call_chunks:
                    for chunk in message_chunk.tool_call_chunks:
                        chunk_id = chunk.get("id")
                        chunk_args = chunk.get("args", "")
                        chunk_name = chunk.get("name")

                        if chunk_id:
                            tool_call_names[chunk_id] = chunk_name or tool_call_names.get(chunk_id, "")
                            # 只有新的 tool_call_id 才初始化 buffer
                            if chunk_id not in tool_call_args_buffer:
                                tool_call_args_buffer[chunk_id] = ""
                            _register_running_tool(running_tool_ids, chunk_id)

                        current_tool_call_id = _resolve_active_tool_call_id(
                            chunk_id,
                            active_tool_call_id,
                            running_tool_ids,
                        )
                        if chunk_id:
                            active_tool_call_id = current_tool_call_id

                        if current_tool_call_id and chunk_args:
                            if current_tool_call_id not in tool_call_args_buffer:
                                tool_call_args_buffer[current_tool_call_id] = ""
                            tool_call_args_buffer[current_tool_call_id] += chunk_args

                # ToolMessage: 工具执行完成，发送带有完整 args 的消息
                if msg_type == 'tool':
                    tool_call_id = getattr(message_chunk, 'tool_call_id', '')
                    accumulated_args = {}
                    if tool_call_id and tool_call_id in tool_call_args_buffer:
                        args_str = tool_call_args_buffer[tool_call_id]
                        if args_str:
                            try:
                                accumulated_args = json.loads(args_str)
                            except json.JSONDecodeError:
                                logger.warning(f"无法解析工具参数: {args_str[:100]}")
                        del tool_call_args_buffer[tool_call_id]
                    active_tool_call_id = _finish_running_tool(running_tool_ids, active_tool_call_id, tool_call_id)

                    # 构造增强的消息，包含完整的 args 和时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    if accumulated_args:
                        msg_data["accumulated_args"] = accumulated_args
                        msg_data["tool_name"] = tool_call_names.get(tool_call_id, "")
                    yield _format_sse(event_name, msg_data)
                else:
                    # AI 消息添加时间戳
                    msg_data = message_chunk.model_dump()
                    msg_data["timestamp"] = _get_timestamp()
                    yield _format_sse(event_name, msg_data)

            # 检查是否又被中断（可能有多个工具需要确认）
            state = await graph.aget_state(config)
            interrupt_info = _get_interrupt_info(state)
            if interrupt_info:
                sql_preview = interrupt_info.get('args', {}).get('sql', '')[:50]
                logger.info(f"检测到新的工具中断: {sql_preview}...")
                yield _format_tool_event(
                    tool_name=interrupt_info.get("tool_name", "unknown_tool"),
                    tool_call_id=interrupt_info.get("tool_call_id"),
                    status="pending",
                    args=interrupt_info.get("args"),
                    message=interrupt_info.get("message"),
                )
                yield _format_sse("done", "[DONE]")
                return

        yield _format_sse("done", "[DONE]")

    except Exception as e:
        logger.error(f"继续执行失败: {e}", exc_info=True)
        yield _format_sse("error", {"error": str(e)})
        yield _format_sse("done", "[DONE]")


async def sql_completion_handler(
    conversation_id: str,
    query: str,
    options: Optional[Dict[str, Any]] = None,
    user_id: str = "system",
    request: Request | None = None
) -> StreamingResponse:
    """
    sql_generator 的 completion 处理器
    """
    options_data = options or {}
    datasource_id = options_data.get("datasource_id")
    mode = options_data.get("mode", "generate")
    semantic_config_id = options_data.get("semantic_config_id")  # 语义配置 ID（可选）
    model_id = options_data.get("model_id")  # 模型选择（可选）
    member_datasource_ids = options_data.get("member_datasource_ids") or []

    # 确保 thread 记录存在
    await conversation.get_or_create_conversation("", AGENT_ID, conversation_id, user_id)

    return StreamingResponse(
        _stream_sql_generation(
            thread_id=conversation_id,
            user_query=query,
            datasource_id=datasource_id,
            user_id=user_id,
            member_datasource_ids=member_datasource_ids,
            mode=mode,
            semantic_config_id=semantic_config_id,
            request=request,
            model_id=model_id
        ),
        media_type="text/event-stream",
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )


async def sql_resume_handler(
    conversation_id: str,
    action: str = "approve",
    data: Optional[Dict[str, Any]] = None
) -> StreamingResponse:
    """
    恢复被中断的 SQL 生成流程（Human-in-the-Loop 工具确认）

    符合公共 resume 接口签名:
        async def handler(conversation_id, action, data) -> StreamingResponse

    Args:
        conversation_id: 会话ID
        action: 用户操作 "approve" | "edit" | "reject"
        data: 修改后的数据，如：
            - {"sql": "SELECT ..."}（action=edit 时使用）
            - {"datasource_id": "...", "semantic_config_id": "..."}（语义模式时需要）
    """
    data = data or {}

    # 从 data 中提取参数
    modified_sql = data.get("sql")
    datasource_id = data.get("datasource_id")
    semantic_config_id = data.get("semantic_config_id")

    # 获取语义配置（如果有）
    semantic_config = _get_semantic_config(datasource_id, semantic_config_id)

    # edit 和 approve 都会执行，只是 edit 时使用修改后的 SQL
    # 将 edit 转换为 approve（底层逻辑一致）
    effective_action = "approve" if action == "edit" else action

    return StreamingResponse(
        _continue_execution(
            thread_id=conversation_id,
            modified_sql=modified_sql,
            action=effective_action,
            semantic_config=semantic_config
        ),
        media_type="text/event-stream",
        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'}
    )
