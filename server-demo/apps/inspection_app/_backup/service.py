"""
业务逻辑层（异步版本）
负责参数验证、业务规则判断、执行图工作流
"""

import time
from datetime import datetime
from typing import Optional, AsyncGenerator
from langchain_core.messages import HumanMessage, AIMessageChunk, AIMessage
from langgraph.types import Command
from sqlalchemy import update as sa_update

from .agent.states import GraphState
from .agent.graph import get_graph, get_checkpointer
from utils.logger import get_logger
from utils.exceptions import BusinessException
from utils.lang_util.database import get_session
from utils.lang_util.models import UserThread

logger = get_logger(__name__)


async def process_conversation(
    thread_id: str,
    query: str,
    task: Optional[dict] = None,
    uploaded_file: Optional[bytes] = None,
    tool_type: Optional[str] = None
) -> AsyncGenerator[dict, None]:
    """
    处理对话 - 统一入口（异步版本）

    Args:
        thread_id: 线程 ID（用于 checkpoint 持久化）
        query: 用户查询内容
        task: 任务对象（{"task_type": "violation_analysis", ...} 或 None）
        uploaded_file: 上传的文件内容（字节数组，用于报告生成）
        tool_type: 工具类型（用于前端恢复引导语，如 "violation-analysis", "report-jcbg" 等）

    Yields:
        dict: SSE 事件数据
    """
    logger.info(f"开始处理对话，thread_id: {thread_id}, 任务: {task}")
    logger.debug(f"查询内容: {query[:100]}...")

    # 参数验证
    if not query or not query.strip():
        yield {
            'event': 'error',
            'data': {
                'message': '消息内容不能为空',
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }
        }
        return

    if not thread_id:
        yield {
            'event': 'error',
            'data': {
                'message': '线程ID不能为空',
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }
        }
        return

    # 准备执行参数
    task_data = task or {"task_type": "qa"}
    task_type = task_data.get("task_type", "qa")

    user_message = HumanMessage(content=query, additional_kwargs={"task": task_data})
    initial_state: GraphState = {"messages": [user_message]}

    # 如果传入了 tool_type，保存到 state（仅首次有效，后续会被 checkpoint 保留）
    if tool_type:
        initial_state["tool_type"] = tool_type

    if uploaded_file:
        initial_state["uploaded_file"] = uploaded_file

    config = {"configurable": {"thread_id": thread_id}}

    try:
        # 发送 start 事件
        yield {
            'event': 'start',
            'data': {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }
        }

        # 执行图
        async with get_checkpointer() as checkpointer:
            graph = get_graph(checkpointer)

            async for event in graph.astream(
                initial_state,
                config=config,
                stream_mode=["custom", "messages"],
                subgraphs=True
            ):
                if isinstance(event, tuple) and len(event) == 3:
                    namespace, event_type, event_data = event

                    if event_type == "custom":
                        if isinstance(event_data, dict):
                            # 统一使用 message 事件，保持 NonStandardContentBlock 原始格式
                            yield {
                                'event': 'message',
                                'data': {
                                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                                    **event_data
                                }
                            }

                    elif event_type == "messages":
                        # 只有 qa 类型才输出流式文本
                        if task_type != "qa":
                            continue

                        if isinstance(event_data, tuple) and len(event_data) == 2:
                            message_chunk, metadata = event_data
                            if isinstance(message_chunk, AIMessageChunk):
                                content = message_chunk.content if hasattr(message_chunk, "content") else ""
                                if content:
                                    yield {
                                        'event': 'message',
                                        'data': {
                                            'type': 'AIMessageChunk',
                                            'id': getattr(message_chunk, 'id', None),
                                            'content': content,
                                            'additional_kwargs': getattr(message_chunk, 'additional_kwargs', {}),
                                            'response_metadata': getattr(message_chunk, 'response_metadata', {}),
                                            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                                        }
                                    }

            # 获取最终状态
            final_snapshot = await graph.aget_state(config)
            if not final_snapshot or not final_snapshot.values:
                raise BusinessException(msg="未获取到最终状态", code=50031)

            final_state = final_snapshot.values

            if final_state.get("error"):
                raise BusinessException(msg=final_state["error"], code=50032)

            # 检查是否被中断（Human-in-the-Loop）
            if final_snapshot.next:
                # 图被中断，等待用户输入
                # next 包含下一个要执行的节点，说明图还没有结束
                logger.info(f"图被中断，等待节点: {final_snapshot.next}")

                # 从 tasks 中获取中断信息
                interrupt_data = None
                if final_snapshot.tasks:
                    logger.info(f"检查 tasks: {len(final_snapshot.tasks)} 个任务")
                    for idx, task in enumerate(final_snapshot.tasks):
                        logger.info(f"任务 {idx}: name={getattr(task, 'name', 'N/A')}, has_interrupts={hasattr(task, 'interrupts')}")
                        if hasattr(task, 'interrupts') and task.interrupts:
                            # 获取第一个中断的数据
                            interrupt_data = task.interrupts[0].value if task.interrupts else None
                            break

                yield {
                    'event': 'interrupt',
                    'data': {
                        'next_node': list(final_snapshot.next) if final_snapshot.next else [],
                        'interrupt_data': interrupt_data,
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                    }
                }
                return  # 中断时不继续执行

            # 获取最后一条 AIMessage
            messages = final_state.get("messages", [])
            logger.info(f"[resume] 消息历史数量: {len(messages)}")
            if messages:
                for i, msg in enumerate(messages[-5:]):  # 只打印最后5条
                    logger.info(f"[resume] 消息 {i}: type={type(msg).__name__}")

            if not messages:
                raise BusinessException(msg="未找到消息记录", code=50033)

            # 查找最后一条 AIMessage（可能有状态更新消息在后面）
            last_message = None
            for msg in reversed(messages):
                if isinstance(msg, AIMessage):
                    last_message = msg
                    logger.info(f"[resume] 找到 AIMessage")
                    break

            if not last_message:
                logger.warning(f"[resume] 未找到 AIMessage，可能是审核节点确认后继续执行。最近10条消息类型: {[type(msg).__name__ for msg in messages[-10:]]}")
                # 审核确认后继续执行，返回成功完成事件（不抛出异常）
                yield {
                    'event': 'completed',
                    'data': {
                        'message': '操作成功',
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                    }
                }
                return

            # 返回完整的 AIMessage
            yield {
                'event': 'completed',
                'data': {
                    'messages': [{
                        'id': getattr(last_message, 'id', None),
                        'role': 'assistant',
                        'content': last_message.content,
                        'additional_kwargs': last_message.additional_kwargs
                    }],
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                }
            }

        # 更新时间戳
        try:
            async with get_session() as session:
                await session.execute(
                    sa_update(UserThread)
                    .where(UserThread.thread_id == thread_id)
                    .values(update_time=datetime.now())
                )
        except Exception as e:
            logger.error(f"更新线程时间戳失败: {str(e)}")

    except Exception as e:
        error_message = e.msg if isinstance(e, BusinessException) else str(e)
        logger.error(f"工作流执行异常: {error_message}", exc_info=True)
        yield {
            'event': 'error',
            'data': {'message': error_message, 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')}
        }


async def resume_conversation(
    thread_id: str,
    human_input: dict
) -> AsyncGenerator[dict, None]:
    """
    恢复被中断的对话（Human-in-the-Loop）

    Args:
        thread_id: 线程 ID
        human_input: 用户输入，格式：
            {
                "action": "approve" | "edit" | "reject",
                "data": { ... }  # 如果是 edit，包含修改后的数据
            }

    Yields:
        dict: SSE 事件数据
    """
    logger.info(f"恢复对话，thread_id: {thread_id}, action: {human_input.get('action')}")

    if not thread_id:
        yield {
            'event': 'error',
            'data': {
                'message': '线程ID不能为空',
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }
        }
        return

    config = {"configurable": {"thread_id": thread_id}}

    try:
        # 发送 resume 开始事件
        yield {
            'event': 'resume',
            'data': {
                'action': human_input.get('action', 'approve'),
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
            }
        }

        # 使用 Command(resume=...) 恢复执行
        async with get_checkpointer() as checkpointer:
            graph = get_graph(checkpointer)

            # 检查当前图状态
            current_state = await graph.aget_state(config)
            logger.info(f"[resume] 当前图状态 - next: {current_state.next}, tasks数量: {len(current_state.tasks) if current_state.tasks else 0}")
            if current_state.tasks:
                for idx, task in enumerate(current_state.tasks):
                    logger.info(f"[resume] 任务 {idx}: name={getattr(task, 'name', 'N/A')}, interrupts={len(task.interrupts) if hasattr(task, 'interrupts') and task.interrupts else 0}")

            # 恢复图执行
            event_count = 0
            async for event in graph.astream(
                Command(resume=human_input),
                config=config,
                stream_mode=["custom", "messages"],
                subgraphs=True
            ):
                event_count += 1
                if isinstance(event, tuple) and len(event) == 3:
                    namespace, event_type, event_data = event
                    if event_type == "custom":
                        if isinstance(event_data, dict):
                            yield {
                                'event': 'message',
                                'data': {
                                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                                    **event_data
                                }
                            }

            logger.info(f"[resume] astream 循环结束，共收到 {event_count} 个事件")

            # 获取最终状态
            final_snapshot = await graph.aget_state(config)
            if not final_snapshot or not final_snapshot.values:
                raise BusinessException(msg="未获取到最终状态", code=50031)

            final_state = final_snapshot.values

            if final_state.get("error"):
                raise BusinessException(msg=final_state["error"], code=50032)

            # 检查是否再次被中断
            if final_snapshot.next:
                logger.info(f"图再次被中断，等待节点: {final_snapshot.next}")

                interrupt_data = None
                if final_snapshot.tasks:
                    logger.info(f"[resume] 检查 tasks: {len(final_snapshot.tasks)} 个任务")
                    for idx, task in enumerate(final_snapshot.tasks):
                        logger.info(f"[resume] 任务 {idx}: name={getattr(task, 'name', 'N/A')}, has_interrupts={hasattr(task, 'interrupts')}")
                        if hasattr(task, 'interrupts') and task.interrupts:
                            interrupt_data = task.interrupts[0].value if task.interrupts else None
                            break

                yield {
                    'event': 'interrupt',
                    'data': {
                        'next_node': list(final_snapshot.next) if final_snapshot.next else [],
                        'interrupt_data': interrupt_data,
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                    }
                }
                return

            # 获取最后一条 AIMessage
            messages = final_state.get("messages", [])
            logger.info(f"[resume] 消息历史数量: {len(messages)}")
            if messages:
                for i, msg in enumerate(messages[-5:]):  # 只打印最后5条
                    logger.info(f"[resume] 消息 {i}: type={type(msg).__name__}")

            if not messages:
                raise BusinessException(msg="未找到消息记录", code=50033)

            # 查找最后一条 AIMessage（可能有状态更新消息在后面）
            last_message = None
            for msg in reversed(messages):
                if isinstance(msg, AIMessage):
                    last_message = msg
                    logger.info(f"[resume] 找到 AIMessage")
                    break

            if not last_message:
                logger.warning(f"[resume] 未找到 AIMessage，可能是审核节点确认后继续执行。最近10条消息类型: {[type(msg).__name__ for msg in messages[-10:]]}")
                # 审核确认后继续执行，返回成功完成事件（不抛出异常）
                yield {
                    'event': 'completed',
                    'data': {
                        'message': '操作成功',
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                    }
                }
                return

            # 返回完整的 AIMessage
            yield {
                'event': 'completed',
                'data': {
                    'messages': [{
                        'id': getattr(last_message, 'id', None),
                        'role': 'assistant',
                        'content': last_message.content,
                        'additional_kwargs': last_message.additional_kwargs
                    }],
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                }
            }

        # 更新时间戳
        try:
            async with get_session() as session:
                await session.execute(
                    sa_update(UserThread)
                    .where(UserThread.thread_id == thread_id)
                    .values(update_time=datetime.now())
                )
        except Exception as e:
            logger.error(f"更新线程时间戳失败: {str(e)}")

    except Exception as e:
        error_message = e.msg if isinstance(e, BusinessException) else str(e)
        logger.error(f"恢复执行异常: {error_message}", exc_info=True)
        yield {
            'event': 'error',
            'data': {'message': error_message, 'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')}
        }
