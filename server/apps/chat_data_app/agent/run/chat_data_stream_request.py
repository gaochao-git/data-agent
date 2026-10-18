"""ChatData Agent 流式执行的入参契约。

放在中立模块是为了打破环：``agent_stream`` 会导入 ``agent_run_runtime``（注册 stream
generator），而 runtime 在装配 stream 时又要构造这份请求对象——两边都从本模块取，
避免 ``agent_run_runtime`` 反向依赖 ``agent_stream``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from langchain_core.messages import HumanMessage


@dataclass(frozen=True)
class ChatDataStreamRequest:
    """一次 ChatData Agent 流式执行的完整入参描述。

    AgentRunRuntime 装配 与 定时任务/API 直调共用同一份描述：此前这些字段逐字铺在
    ``_generate_chat_data_stream`` / ``_run_chat_data_agent_stream`` 两个函数的签名上，
    形参数量远超 AGENTS.md §3 的阈值。收敛为一个冻结对象后：

      - 两个流式函数各自只声明 1~2 个形参；
      - 字段默认值集中在此（frozen 结构不可变），调用方无需重复写 ``None`` 兜底；
      - 单元测试可以直接构造一份请求并复用。

    ``request`` 字段是 FastAPI 的 Request（可能为 None：定时任务链路无 HTTP 请求）。
    """

    thread_id: str
    user_query: str
    user_id: str
    datasource_id: str | None = None
    username: str | None = None
    member_datasource_ids: list[str] | None = None
    semantic_config_id: str | None = None
    request: Request | None = None
    model_id: str | None = None
    file_selections: Any | None = None
    enable_thinking: bool | None = None
    thinking_effort: str | None = None
    skill_names: list[str] | None = None
    options: dict[str, Any] | None = None
    file_ids: list[str] | None = None
    run_id: str | None = None
    adopted_messages: list[HumanMessage] | None = None
    resume_answer: dict[str, Any] | None = None
    resume_started: bool = False
    resume_turn_id: str | None = None
    turn_id: str | None = None
    recovering: bool = False
    owner_token: str | None = None
    stream_event_id_provider: Callable[[], str | None] | None = None
    # 请求到达墙钟（毫秒）：completion 入口 / 问答恢复 claim 盖章写入 Run 行，
    # runtime 透传到本对象，作为 harness 首 token 与任务总耗时 total_duration_ms
    # 的起点（用户气泡对齐口径）；缺失时（定时任务/continuation）由流工厂按
    # 派发时刻兜底盖章。
    stream_started_wall_ms: int | None = None
    # 客户端点击发送时刻（epoch 毫秒）：前端 Date.now() 随请求体上报，handler
    # 校验钟偏后覆盖 stream_started_wall_ms，使 harness 计时与用户气泡显示同锚
    # 「点击时刻」；非法/缺失回退服务端接收章。
    client_timestamp_ms: int | None = None
