"""工具调用参数规整工具。

部分模型（如内网 Qwen3.6 经 vLLM）返回的 tool call args 不是 JSON 对象，而是
JSON 字符串甚至双层编码字符串（``"{\\"a\\": 1}"``）。下游三处消费方都要求 dict：

1. 工具执行：langgraph ToolNode 把 ``tool_calls[].args`` 当 kwargs 传给工具；
2. 直播 SSE：``_ToolStreamState`` 的 args_buffer / args_fallback；
3. 回放落库：conversation_event 的 assistant_message payload。

本模块提供唯一的规整入口，调用方包括 agent 中间件（tool_call_normalizer）与
直播流处理（handlers），注意二者不能互相 import，故收口在 utils L1 底座。
"""

import json
from typing import Any, Optional


def coerce_tool_call_args(value: Any, max_unwrap_layers: int = 2) -> Optional[dict]:
    """尽力把工具调用参数规整为 dict；无法规整时返回 None。

    规整规则：
    - dict 原样返回（同一对象，调用方可据此判断是否发生变化）；
    - 字符串逐层 ``json.loads``，最多剥 ``max_unwrap_layers`` 层，最终须为 dict；
    - 解析失败 / 非 dict 结果（如列表、数字）/ 空串返回 None。

    返回 None 时调用方应保留原始值，让下游自然报错，不做静默篡改。
    """
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    for _ in range(max_unwrap_layers):
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str):
            text = parsed
            continue
        return None
    return None
