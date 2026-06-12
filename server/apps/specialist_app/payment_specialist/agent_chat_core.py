import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterable
from typing import Any, Optional

import openai

from ..conversation_history import (
    load_history,
    save_history,
    update_conversation_title,
)
from ..message_utils import create_message
from ..openai_client import get_openai_client, _OPENAI_BASE_URL, _OPENAI_VERIFY_TLS, _OPENAI_TRUST_ENV
from ..tool_register import dispatch_tool, _TOOL_DESC_DICT, ToolStreamChunk
from ..trace_utils import write_trace

logger = logging.getLogger(__name__)

TITLE_SYSTEM_PROMPT = """
你是一个AI专家系统-会话标题生成助手，负责根据用户的问题生成 30 字内的简洁标题。
要求有总结性，又与用户的问题有区分度。不带引号或编号，避免通用描述。
"""


async def generate_title(query: str, model_name: str | None = None) -> str:
    """生成会话标题"""
    cleaned = (query or "").strip()
    if not cleaned:
        return "新会话"

    # 如果问题很短，直接使用问题作为标题
    if len(cleaned) <= 30:
        return cleaned

    try:
        client = get_openai_client()
        target_model = model_name or os.getenv("OPENAI_SUGGEST_MODEL", "qwen3-30b-a3b-instruct-2507")

        response = await client.chat.completions.create(
            model=target_model,
            messages=[
                {"role": "system", "content": TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": f"请为这个问题生成标题：{cleaned}"},
            ],
            temperature=0.2,
            max_tokens=100,
            stream=False,
        )

        if response.choices and response.choices[0].message.content:
            title = response.choices[0].message.content.strip()
            # 限制标题长度
            if len(title) > 30:
                title = title[:28] + "..."
            return title
    except Exception as e:
        logger.warning("标题生成失败，使用默认标题: %s", e)

    # 失败时使用问题的前28个字符作为标题
    return cleaned[:28] + "..." if len(cleaned) > 28 else cleaned


SYSTEM_PROMPT = """
你是一名法律专家助手，擅长根据用户提出的问题检索、定位并返回相关的法律法规条款。在必要时，你可以调用预置的工具来完成查询任务，并基于查询结果进行分析回答。

【工具定义】
1、工具名称：search_laws  
   功能描述：根据用户问题检索相关法律法规。  
   输入参数：  
     - "query"：用户的自然语言问题。

【执行规则】
1、当用户的问题需要借助工具（如需要检索法律法规）时，你必须返回 **严格符合 Markdown 代码块格式 的 JSON 字符串**（不可包含任何额外解释性文字）。  
   JSON 返回格式示例（如工具无参数，arguments 允许为空对象）：  
   ```json
   {
       "tool_call": {
           "name": "",
           "arguments": {
               "参数1": "值1",
               "参数2": ["",""]
           }
       }
   }
   ```

2、在所有 JSON 中：  
   - 字段名必须使用双引号  
   - 字符串内容必须使用双引号  
   - 不能包含多余字段  
   - 格式必须完整可解析

3、不要自行编造信息或进行主观推断。  
   - 如果工具调用失败，你 **不要改为用你的知识回答**；  
   - 你必须明确告诉用户发生了什么错误，而不能输出法律内容。

4、引用条款输出格式：  
   - 在正文引用处紧跟 `【引用1】` 这样的标记（标号可以是数字或字母，但需唯一且按顺序递增）。  
   - 每个标记后必须紧跟一个 Markdown 代码块，语言标识为 `link`，代码块中的每一行都必须符合：  
     ```link
     [law][《title》][第n条][条款简介或摘要][文档ID][引用1]
     ```  
     其中第 5 段用于 `/api/clause?id=` 的 `id`，第 6 段必须与正文标记保持一致。  
   - 除上述代码块外，不要再输出任何额外的引用说明、脚注或“链接：...”文字。
   
5、最终回答必须以自然语言段落或条列方式展开分析，在相关句子或要点后附上对应的引用标记。严禁仅输出引用编号列表或纯粹的条款堆叠。

请严格遵守以上规则，以确保系统能稳定解析你的输出。
            """

async def chat_with_react_as_function_call(
    query,
    conversation_id,
    model_name,
    user_id: str | None = None,
):
    print(f"query:{query}")
    messages = load_history(conversation_id)

    # 检测是否为首次对话（用于决定是否生成标题）
    is_first_chat = len(messages) == 0

    # 确保 system prompt 只添加一次
    if not messages or not isinstance(messages[0], dict) or messages[0].get("role") != "system":
        messages.insert(
            0,
            create_message(
                "system",
                SYSTEM_PROMPT,
                visible_for_display=False,
                display_type="system_prompt",
            ),
        )
    # 添加当前用户的提问
    user_message_entry = create_message(
        "user",
        query,
        display_type="user",
    )
    messages.append(user_message_entry)
    try:
        max_retry = 3
        message_id = f"m_{uuid.uuid4().hex}"
        seq = 0
        message_started = False
        display_thinking: list[dict[str, Any]] = []
        references_buffer: list[dict[str, Any]] = []

        def record_display_event(event_payload: dict[str, Any]) -> None:
            event_name = event_payload.get("event")
            if event_name not in {"tool_call_start", "tool_call_delta", "tool_result_delta", "tool_call_end"}:
                return
            desc = event_payload.get("desc")
            if not isinstance(desc, str) or not desc.strip():
                return
            record: dict[str, Any] = {
                "event": event_name,
                "desc": desc.strip(),
            }
            seq_value = event_payload.get("seq")
            if isinstance(seq_value, int):
                record["seq"] = seq_value
            created_at = event_payload.get("created")
            if isinstance(created_at, (int, float)):
                record["created"] = int(created_at)
            tool_call_id = event_payload.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id:
                record["tool_call_id"] = tool_call_id
            status = event_payload.get("status")
            if isinstance(status, str) and status:
                record["status"] = status
            display_thinking.append(record)

        def build_event(event_name, *, chunk=None, include_message_id=True, **payload):
            nonlocal seq
            seq += 1
            if event_name == "references":
                refs = payload.get("references")
                if isinstance(refs, list):
                    references_buffer.clear()
                    references_buffer.extend(refs)
            event_payload = {
                "event": event_name,
                "seq": seq,
                "created": int(time.time() * 1000),
            }
            if include_message_id:
                event_payload["message_id"] = message_id
            if conversation_id:
                event_payload["conversation_id"] = conversation_id
            for key, value in list(payload.items()):
                if value is None:
                    payload.pop(key)
            event_payload.update(payload)
            record_display_event(event_payload)
            return f"data: {json.dumps(event_payload, ensure_ascii=False)}\n\n"

        def ensure_message_start(chunk):
            nonlocal message_started
            if not message_started:
                message_started = True
                return build_event(
                    "message_start",
                    chunk=chunk,
                    role="assistant",
                    model=model_name,
                )
            return None

        def normalize_output(value):
            if value is None:
                return None
            try:
                json.dumps(value, ensure_ascii=False)
                return value
            except TypeError:
                return str(value)

        def _extract_description(value):
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
            return None

        def _extract_desc_from_payload(payload):
            if isinstance(payload, dict):
                return _extract_description(payload.get("desc"))
            if isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    return None
                if isinstance(parsed, dict):
                    return _extract_description(parsed.get("desc"))
            return None

        def _resolve_desc(explicit_desc=None, *, delta=None, payload=None):
            explicit = _extract_description(explicit_desc)
            if explicit:
                return explicit
            payload_desc = _extract_desc_from_payload(payload)
            if payload_desc:
                return payload_desc
            if isinstance(delta, str):
                trimmed = delta.strip()
                if not trimmed:
                    return None
                try:
                    parsed = json.loads(trimmed)
                except json.JSONDecodeError:
                    return None
                if isinstance(parsed, dict):
                    return _extract_description(parsed.get("desc"))
            return None

        for attempt in range(1, max_retry + 1):
            print(f"messages:{messages}")
            try:
                write_trace(
                    conversation_id,
                    "model_request",
                    {
                        "attempt": attempt,
                        "model": model_name,
                        "messages": messages,
                    },
                )
                client = get_openai_client()
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=0.7,
                    stream=True,
                )
            except openai.APIConnectionError as conn_err:
                proxy_env_detected = any(
                    os.getenv(proxy_key)
                    for proxy_key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
                )
                logger.exception(
                    "OpenAI connection error attempt=%s/%s conversation_id=%s model=%s base_url=%s "
                    "verify_tls=%s trust_env=%s proxy_env=%s",
                    attempt,
                    max_retry,
                    conversation_id,
                    model_name,
                    _OPENAI_BASE_URL,
                    _OPENAI_VERIFY_TLS,
                    _OPENAI_TRUST_ENV,
                    proxy_env_detected,
                )
                if attempt == max_retry:
                    raise
                backoff_seconds = min(2 ** (attempt - 1), 10)
                await asyncio.sleep(backoff_seconds)
                continue

            probe_buffer = []  # 缓冲前若干 token 用于判定是否为工具调用
            function_check_done = False  # 是否已经完成开头探测
            function_mode = False  # 标记是否进入函数调用处理模式
            sum_content = ""
            usage_data = None
            async for chunk in response:
                # print(f"chunk:{chunk}")
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if hasattr(delta, "content"):
                    content = delta.content or ""
                    if content:
                        sum_content += content

                        if function_mode:
                            probe_buffer.append(content)
                            code_block = "".join(probe_buffer)
                            # 简单判断：代码块完整标志为出现两次 ``` 分隔符
                            if code_block.count("```") >= 2:
                                try:
                                    # 提取第一个 ``` 和第二个 ``` 中间的内容
                                    function_json_str = code_block.split("```", 2)[1]
                                    if function_json_str.startswith("json"):
                                        function_json_str = function_json_str.split("json", 1)[1]
                                except IndexError as e:
                                    function_json_str = ""
                                    print(f"======函数处理异常，conversation_id:{conversation_id}, e:{e}")
                                if isinstance(function_json_str, str):
                                    try:
                                        tool_info = json.loads(function_json_str)
                                        maybe_start = ensure_message_start(chunk)
                                        if maybe_start:
                                            yield maybe_start

                                        tool_call = tool_info.get("tool_call", {})
                                        tool_name = tool_call.get("name")
                                        raw_arguments = tool_call.get("arguments")
                                        tool_call_id = tool_call.get("id") or f"tc_{uuid.uuid4().hex}"
                                        write_trace(
                                            conversation_id,
                                            "tool_invocation",
                                            {
                                                "tool_call_id": tool_call_id,
                                                "tool_name": tool_name,
                                                "arguments": raw_arguments,
                                            },
                                        )
                                        args_delta = raw_arguments
                                        if raw_arguments is None:
                                            args_delta = ""
                                        elif not isinstance(raw_arguments, str):
                                            try:
                                                args_delta = json.dumps(raw_arguments, ensure_ascii=False)
                                            except TypeError:
                                                args_delta = str(raw_arguments)

                                        yield build_event(
                                            "tool_call_start",
                                            chunk=chunk,
                                            tool_call_id=tool_call_id,
                                            name=tool_name,
                                            desc=_TOOL_DESC_DICT['regulationassistant'][tool_name]['desc']
                                        )

                                        if args_delta:
                                            yield build_event(
                                                "tool_call_delta",
                                                chunk=chunk,
                                                tool_call_id=tool_call_id,
                                                args_delta=args_delta,
                                                desc=str(_TOOL_DESC_DICT['regulationassistant'][tool_name]['ing_desc']).format(**raw_arguments)
                                            )

                                        start_ts = time.time()
                                        messages.append(
                                            create_message(
                                                "assistant",
                                                function_json_str,
                                                visible_for_display=False,
                                                display_type="tool_invocation",
                                                extra={
                                                    "tool_call_id": tool_call_id,
                                                    "tool_name": tool_name,
                                                    "message_type": "tool_invocation",
                                                },
                                            )
                                        )
                                        tool_status = "ok"
                                        tool_response = ""
                                        tool_output_payload = None
                                        if tool_name:
                                            try:
                                                tool_execution_result = await dispatch_tool(tool_name, raw_arguments)
                                                if isinstance(tool_execution_result, AsyncIterable):
                                                    streamed_parts = []

                                                    async def _iter_stream():
                                                        nonlocal streamed_parts
                                                        try:
                                                            async for stream_chunk in tool_execution_result:  # type: ignore[arg-type]
                                                                event_name = "tool_result_delta"
                                                                delta_text = None
                                                                payload_value = None
                                                                explicit_desc = None

                                                                if isinstance(stream_chunk, ToolStreamChunk):
                                                                    if stream_chunk.event:
                                                                        event_name = stream_chunk.event
                                                                    payload_value = stream_chunk.payload
                                                                    explicit_desc = stream_chunk.desc
                                                                    if stream_chunk.delta is not None:
                                                                        delta_text = str(stream_chunk.delta)
                                                                    elif stream_chunk.payload is not None:
                                                                        try:
                                                                            delta_text = json.dumps(stream_chunk.payload, ensure_ascii=False)
                                                                        except TypeError:
                                                                            delta_text = str(stream_chunk.payload)
                                                                elif isinstance(stream_chunk, dict):
                                                                    maybe_event = stream_chunk.get("event")
                                                                    if maybe_event:
                                                                        event_name = maybe_event
                                                                    payload_value = stream_chunk.get("payload")
                                                                    explicit_desc = stream_chunk.get("desc")
                                                                    if stream_chunk.get("delta") is not None:
                                                                        delta_text = str(stream_chunk.get("delta"))
                                                                    elif stream_chunk.get("payload") is not None:
                                                                        try:
                                                                            delta_text = json.dumps(stream_chunk.get("payload"), ensure_ascii=False)
                                                                        except TypeError:
                                                                            delta_text = str(stream_chunk.get("payload"))
                                                                elif stream_chunk is not None:
                                                                    delta_text = str(stream_chunk)

                                                                desc_text = _resolve_desc(
                                                                    explicit_desc,
                                                                    delta=delta_text,
                                                                    payload=payload_value,
                                                                )

                                                                if delta_text is None and desc_text is None:
                                                                    continue

                                                                if delta_text is not None:
                                                                    streamed_parts.append(delta_text)
                                                                yield build_event(
                                                                    event_name,
                                                                    chunk=chunk,
                                                                    tool_call_id=tool_call_id,
                                                                    delta=delta_text,
                                                                    desc=desc_text,
                                                                )
                                                        except Exception as stream_exc:  # pylint: disable=broad-except
                                                            error_message = f"工具流式执行失败：{stream_exc}"
                                                            yield build_event(
                                                                "tool_result_delta",
                                                                chunk=chunk,
                                                                tool_call_id=tool_call_id,
                                                                delta=error_message,
                                                                desc=error_message,
                                                            )
                                                            raise

                                                    async for stream_event in _iter_stream():
                                                        yield stream_event

                                                    aggregated_text = "".join(streamed_parts)
                                                    if aggregated_text.strip():
                                                        try:
                                                            parsed_payload = json.loads(aggregated_text)
                                                        except json.JSONDecodeError:
                                                            parsed_payload = aggregated_text
                                                    else:
                                                        parsed_payload = ""
                                                    tool_output_payload = parsed_payload
                                                    tool_response = parsed_payload
                                                else:
                                                    tool_response = tool_execution_result
                                                    tool_output_payload = tool_execution_result
                                            except Exception as tool_error:  # pylint: disable=broad-except
                                                tool_status = "error"
                                                tool_output_payload = {"error": str(tool_error)}
                                                tool_response = tool_output_payload
                                                print(
                                                    f"=====工具调用失败，conversation_id:{conversation_id}, "
                                                    f"name:{tool_name}, e:{tool_error}"
                                                )
                                        else:
                                            tool_status = "error"
                                            tool_output_payload = {"error": "tool name missing"}
                                            tool_response = tool_output_payload
                                            print(
                                                f"=====工具名称缺失，conversation_id:{conversation_id}, "
                                                f"payload:{tool_info}"
                                            )
                                        latency_ms = int((time.time() - start_ts) * 1000)
                                        sanitized_output = normalize_output(tool_output_payload)
                                        yield build_event(
                                            "tool_call_end",
                                            chunk=chunk,
                                            tool_call_id=tool_call_id,
                                            status=tool_status,
                                            output=sanitized_output,
                                            latency_ms=latency_ms,
                                            desc=str(_TOOL_DESC_DICT['regulationassistant'][tool_name]['end_desc'])
                                        )
                                        write_trace(
                                            conversation_id,
                                            "tool_result",
                                            {
                                                "tool_call_id": tool_call_id,
                                                "tool_name": tool_name,
                                                "status": tool_status,
                                                "latency_ms": latency_ms,
                                                "output": sanitized_output,
                                            },
                                        )

                                        follow_up_content = (
                                            f"我调用了工具{tool_name or 'unknown_tool'}，返回信息如下：{tool_response}，请继续回答用户的问题"
                                            if tool_status == "ok"
                                            else f"我调用了工具{tool_name or 'unknown_tool'}，但发生错误：{tool_response}，请协助处理该错误或继续回答用户的问题"
                                        )
                                        messages.append(
                                            create_message(
                                                "assistant",
                                                follow_up_content,
                                                visible_for_display=False,
                                                display_type="tool_follow_up",
                                                extra={
                                                    "tool_call_id": tool_call_id,
                                                    "tool_name": tool_name,
                                                    "tool_status": tool_status,
                                                    "message_type": "tool_follow_up",
                                                },
                                            )
                                        )
                                    except json.JSONDecodeError as e:
                                        print(f"=====JSONDecodeError，conversation_id:{conversation_id}, e:{e}")
                                break
                            continue

                        if not function_check_done:
                            probe_buffer.append(content)
                            combined = "".join(probe_buffer)
                            stripped = combined.lstrip()
                            if not stripped:
                                continue

                            looks_like_tool_block = False
                            wait_more = False

                            if stripped.startswith("```"):
                                if "tool" in stripped or '"tool_call"' in stripped:
                                    looks_like_tool_block = True
                                elif stripped.count("```") < 2 and len(stripped) < 200:
                                    wait_more = True
                            elif stripped.startswith("{"):
                                if '"tool_call"' in stripped:
                                    looks_like_tool_block = True
                                elif stripped.count("}") == 0 and len(stripped) < 200:
                                    wait_more = True

                            if looks_like_tool_block:
                                function_mode = True
                                continue
                            if wait_more:
                                continue

                            function_check_done = True
                            maybe_start = ensure_message_start(chunk)
                            if maybe_start:
                                yield maybe_start
                            for token in probe_buffer:
                                yield build_event(
                                    "content_delta",
                                    chunk=chunk,
                                    index=0,
                                    delta=token,
                                )
                            probe_buffer = []
                            continue

                        maybe_start = ensure_message_start(chunk)
                        if maybe_start:
                            yield maybe_start
                        yield build_event(
                            "content_delta",
                            chunk=chunk,
                            index=0,
                            delta=content,
                        )

                if choice.finish_reason == "stop" and not function_mode:
                    if probe_buffer:
                        maybe_start = ensure_message_start(chunk)
                        if maybe_start:
                            yield maybe_start
                        for token in probe_buffer:
                            yield build_event(
                                "content_delta",
                                chunk=chunk,
                                index=0,
                                delta=token,
                            )
                        probe_buffer = []

                    raw_usage = getattr(chunk, "usage", None)
                    if raw_usage is not None:
                        if hasattr(raw_usage, "model_dump"):
                            usage_data = raw_usage.model_dump()
                        else:
                            usage_data = raw_usage

                    maybe_start = ensure_message_start(chunk)
                    if maybe_start:
                        yield maybe_start

                    usage_payload = normalize_output(usage_data)
                    final_thinking_trace = [dict(step) for step in display_thinking]
                    final_display_payload = {
                        "visible": True,
                        "type": "assistant_answer",
                        "content": sum_content,
                        "thinking": final_thinking_trace,
                    }

                    yield build_event(
                        "message_end",
                        chunk=chunk,
                        finish_reason=choice.finish_reason or "stop",
                        usage=usage_payload,
                        display=final_display_payload,
                    )
                    write_trace(
                        conversation_id,
                        "assistant_answer",
                        {
                            "message_id": message_id,
                            "content": sum_content,
                            "usage": usage_payload,
                            "thinking": final_thinking_trace,
                            "references": references_buffer,
                        },
                    )

                    # 首次对话时生成标题
                    if is_first_chat:
                        try:
                            title = await generate_title(query, model_name)
                            yield build_event(
                                "title_generation",
                                include_message_id=False,
                                title=title,
                            )
                            yield build_event(
                                "title_generation_done",
                                include_message_id=False,
                                title=title,
                            )
                            # 更新标题到历史记录
                            normalized_user_id = str(user_id or "").strip()
                            update_conversation_title(normalized_user_id, conversation_id, title)
                            print(f"[专家系统] 生成标题: {title}")
                        except Exception as title_err:
                            logger.warning("标题生成失败: %s", title_err)

                    yield build_event(
                        "done",
                        chunk=chunk,
                        include_message_id=False,
                    )
                    messages.append(
                        create_message(
                            "assistant",
                            sum_content,
                            message_id=message_id,
                            display_type="assistant_answer",
                            thinking=final_thinking_trace,
                            extra={
                                **({"usage": usage_payload} if usage_payload is not None else {}),
                                **({"references": references_buffer} if references_buffer else {}),
                            } or None,
                        )
                    )
                    save_history()
                    print(f"sum_content:{sum_content}")
                    return  # 退出循环，避免重复处理

    except Exception as e:
        print(f"调用失败，conversation_id:{conversation_id}, e:{e}")
        raise
    finally:
        print(f"调用完成，conversation_id:{conversation_id}")
