"""子智能体中间件测试：subagent 工具派发、状态透传白名单与系统提示合并。"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagent_async import (
    format_active_subagent_summary,
    format_subagent_task_list_summary,
)
from apps.chat_data_app.agent.middleware.subagents import (
    SUBAGENT_STATE_PASSTHROUGH_KEYS,
    SubagentMiddleware,
    SubagentSpec,
    build_subagent_invocation_config,
    build_task_tool,
    validate_subagent_specs,
)


def _spec(name="explore", **overrides):
    kwargs = {
        "name": name,
        "description": "深度分析子智能体",
        "system_prompt": "你是分析子智能体",
        "tools": [],
        "model": object(),
    }
    kwargs.update(overrides)
    return SubagentSpec(**kwargs)


def _fake_runtime(state=None, tool_call_id="call_1"):
    return SimpleNamespace(state=state or {}, tool_call_id=tool_call_id)


def test_validate_subagent_specs_rejects_empty_and_duplicates():
    with pytest.raises(ValueError, match="至少需要定义一个子智能体"):
        validate_subagent_specs([])
    with pytest.raises(ValueError, match="名称不能为空"):
        validate_subagent_specs([_spec(name="  ")])
    with pytest.raises(ValueError, match="名称重复"):
        validate_subagent_specs([_spec(name="a"), _spec(name="a")])


def test_subagent_system_prompt_injects_skills_catalog():
    """一致继承：spec 带技能清单时，子图 prompt 追加目录段。"""
    xml = (
        "<available_skills>\n  <skill>\n    <name>data-analyzing</name>\n"
        "    <skill_file>/mnt/skills/data-analyzing/SKILL.md</skill_file>\n  </skill>\n</available_skills>"
    )
    spec = _spec(available_skills_xml=xml)
    prompt = subagents_module._subagent_system_prompt(spec)
    assert prompt.startswith("你是分析子智能体")
    assert "## 可用技能" in prompt
    assert "/mnt/skills/data-analyzing/SKILL.md" in prompt


def test_subagent_system_prompt_never_injects_datasource_section():
    """静态注入下线固化：数据上下文段（执行对象清单/方言）不再进子图 prompt。

    执行对象清单与方言改为 data_analyzing_tool 的 list_execution_targets 按需
    实时获取，子智能体与主智能体共用同一工具通道，无需提示词镜像。
    """
    xml = (
        "<available_skills>\n  <skill>\n    <name>data-analyzing</name>\n"
        "    <skill_file>/mnt/skills/data-analyzing/SKILL.md</skill_file>\n  </skill>\n</available_skills>"
    )
    spec = _spec(available_skills_xml=xml)
    prompt = subagents_module._subagent_system_prompt(spec)
    assert "## 可用技能" in prompt
    assert "## 数据上下文" not in prompt
    assert "member_datasource_ids" not in prompt


def test_subagent_system_prompt_keeps_original_when_catalog_empty():
    """未传目录或空目录块（无 <skill> 条目）时不注入，prompt 原样。"""
    spec = _spec()
    assert subagents_module._subagent_system_prompt(spec) == spec.system_prompt
    empty_catalog = _spec(available_skills_xml="<available_skills>\n</available_skills>")
    assert subagents_module._subagent_system_prompt(empty_catalog) == spec.system_prompt


def test_build_subagent_middleware_passes_skills_catalog_to_specs():
    """data_agent.build_subagent_middleware 把技能清单透传给每个子智能体 spec。"""
    from apps.chat_data_app.agent.data_agent import AgentRuntimeOptions, build_subagent_middleware

    options = AgentRuntimeOptions(
        explore_subagent_enabled=True,
        general_subagent_enabled=True,
    )
    xml = "<available_skills>\n  <skill>\n    <name>x</name>\n  </skill>\n</available_skills>"
    mw = build_subagent_middleware(
        options,
        tools=[],
        model=object(),
        model_id="m1",
        available_skills_xml=xml,
    )
    assert mw is not None
    assert {spec.name for spec in mw.specs} == {"explore", "general"}
    assert all(spec.available_skills_xml == xml for spec in mw.specs)
    mw_default = build_subagent_middleware(options, tools=[], model=object(), model_id="m1")
    assert all(spec.available_skills_xml == "" for spec in mw_default.specs)


def test_build_subagent_state_only_passes_whitelisted_keys():
    runtime = _fake_runtime(
        {
            "user_id": "u1",
            "workspace": {"root": "/mnt/user-data"},
            "token_usage": {"input": 1},
            "context_summary": "should-not-leak",
        }
    )

    state = subagents_module._build_subagent_state(runtime, "分析入金趋势")

    assert state["user_id"] == "u1"
    assert state["workspace"] == {"root": "/mnt/user-data"}
    assert "token_usage" not in state
    assert "context_summary" not in state
    assert [message.content for message in state["messages"]] == ["分析入金趋势"]


def test_build_subagent_invocation_config_cuts_callbacks():
    config = build_subagent_invocation_config(_spec(recursion_limit=12))

    assert config["callbacks"] == []
    assert config["recursion_limit"] == 12
    assert config["configurable"] == {"chatdata_subagent": "explore"}


def test_background_invocation_config_drops_parent_checkpoint_state():
    """后台任务只带自己的 thread_id，不继承父 Run 的 checkpointer/namespace。"""
    from langchain_core.runnables.config import var_child_runnable_config
    from langgraph._internal._config import ensure_config

    sentinel = object()
    token = var_child_runnable_config.set(
        {
            "configurable": {
                "__pregel_checkpointer": sentinel,
                "thread_id": "parent-run",
                "checkpoint_ns": "parent:task",
            },
            "callbacks": [],
        }
    )
    try:
        config = build_subagent_invocation_config(_spec(), background_task_id="task-1")
        effective = ensure_config(config)
    finally:
        var_child_runnable_config.reset(token)

    assert config["configurable"]["thread_id"] == "chatdata-subagent-task-task-1"
    assert effective["configurable"] == {
        "chatdata_subagent": "explore",
        "thread_id": "chatdata-subagent-task-task-1",
    }
    assert "__pregel_checkpointer" not in effective["configurable"]
    assert "checkpoint_ns" not in effective["configurable"]


def test_background_invocation_config_injects_own_checkpointer():
    """独立检查点：线程按智能体会话、命名空间按任务，显式注入 saver。"""
    sentinel = object()
    config = build_subagent_invocation_config(
        _spec(),
        background_task_id="task-1",
        agent_session_id="session-1",
        checkpointer=sentinel,
    )

    configurable = config["configurable"]
    assert configurable["thread_id"] == "chatdata-agent-session-session-1"
    assert configurable["checkpoint_ns"] == "task-task-1"
    assert configurable["__pregel_checkpointer"] is sentinel


@pytest.mark.asyncio
async def test_control_middleware_pause_interrupts_and_resumes(monkeypatch):
    """暂停落在模型调用前的安全点，恢复后继续同一检查点。"""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from apps.chat_data_app.agent.middleware import subagent_control as control_module
    from apps.chat_data_app.agent.middleware.subagent_control import SubagentControlMiddleware
    from apps.chat_data_app.agent.middleware.subagents import ChatDataSubAgentState

    class _ControlService:
        def __init__(self) -> None:
            self.paused = True

        async def is_pause_requested(self, _task_id: str, _owner: str) -> bool:
            return self.paused

        async def claim_steer_messages(self, _task_id: str, _owner: str) -> list[str]:
            return []

    service = _ControlService()
    monkeypatch.setattr(control_module, "get_subagent_task_service", lambda: service)
    model = FakeMessagesListChatModel(responses=[AIMessage(content="恢复后完成")])
    graph = create_agent(
        model=model,
        tools=[],
        system_prompt="test",
        middleware=[SubagentControlMiddleware()],
        state_schema=ChatDataSubAgentState,
        checkpointer=MemorySaver(),
    )
    config = {"configurable": {"thread_id": "control-pause"}}
    state = {
        "messages": [HumanMessage(content="分析")],
        "subagent_task_id": "task-1",
        "subagent_owner_token": "owner-1",
    }

    interrupted = await graph.ainvoke(state, config)
    assert interrupted["__interrupt__"][0].value == {"kind": "subagent_pause", "task_id": "task-1"}
    assert all(message.content != "恢复后完成" for message in interrupted["messages"])

    service.paused = False
    resumed = await graph.ainvoke(Command(resume={"kind": "subagent_pause"}), config)
    assert resumed["messages"][-1].content == "恢复后完成"


def test_process_subagent_chunk_captures_interrupt_payload():
    tracker = subagents_module._SubagentStreamState("call-1")

    class _Interrupt:
        value = {"kind": "approval", "approval_items": [{"call_id": "c1"}]}

    handled = subagents_module._process_subagent_chunk(
        ("updates", {"__interrupt__": (_Interrupt(),)}), tracker, None, None
    )

    assert handled is True
    assert tracker.interrupt_payload == {"kind": "approval", "approval_items": [{"call_id": "c1"}]}


@pytest.mark.asyncio
async def test_subgraph_approval_interrupt_and_resume_with_injected_checkpointer():
    """子图审批冒泡：独立 saver + Command(resume) 后工具才真正执行。"""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool as lc_tool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from apps.chat_data_app.agent.middleware.subagents import ChatDataSubAgentState
    from apps.chat_data_app.agent.middleware.tool_approval import create_tool_approval_middleware

    executed: list[str] = []

    @lc_tool
    def file_delete(path: str) -> str:
        """删除文件（测试用）。"""
        executed.append(path)
        return f"deleted {path}"

    class _ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "del-1", "type": "tool_call", "name": "file_delete", "args": {"path": "/a"}}],
            ),
            AIMessage(content="删除完成"),
        ]
    )
    graph = create_agent(
        model=model,
        tools=[file_delete],
        system_prompt="test",
        middleware=[
            create_tool_approval_middleware([file_delete], approval_policies={"file_delete": "ask"}),
        ],
        state_schema=ChatDataSubAgentState,
    )
    config = build_subagent_invocation_config(
        _spec(),
        background_task_id="task-approval",
        agent_session_id="session-approval",
        checkpointer=MemorySaver(),
        default_recursion_limit=20,
    )
    writer = lambda _event: None  # noqa: E731

    with pytest.raises(subagents_module.SubagentSuspended) as suspended:
        await subagents_module._run_subagent_with_events(
            graph,
            {"messages": [HumanMessage(content="删除 /a")]},
            config,
            writer,
        )
    assert suspended.value.payload["kind"] == "approval"
    assert executed == []

    await subagents_module._run_subagent_with_events(
        graph,
        Command(resume={"answers": [{"id": "approval", "selected": ["允许"]}]}),
        config,
        writer,
        initial_messages=[],
    )
    assert executed == ["/a"]


@pytest.mark.asyncio
async def test_subagent_tool_returns_final_report_as_tool_message(monkeypatch):
    captured = {}

    class FakeRunnable:
        async def astream(self, state, config, stream_mode):
            captured["state"] = state
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            yield "updates", {"model": {"messages": [AIMessage(content="近30天入金呈上升趋势")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False,
        description="分析入金趋势",
        subagent_type="explore",
        runtime=_fake_runtime({"user_id": "u1"}),
    )

    assert isinstance(result.update["messages"][0], ToolMessage)
    assert result.update["messages"][0].content == "近30天入金呈上升趋势"
    assert captured["state"]["messages"][0].content == "分析入金趋势"
    assert captured["state"]["user_id"] == "u1"
    assert captured["config"]["callbacks"] == []
    assert captured["stream_mode"] == ["updates", "messages"]


@pytest.mark.asyncio
async def test_subagent_tool_falls_back_when_subagent_returns_empty_report(monkeypatch):
    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": []}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析", subagent_type="explore", runtime=_fake_runtime()
    )

    assert "未产出有效结论" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_subagent_tool_forwards_inner_tool_events_to_parent_stream(monkeypatch):
    """内层工具生命周期事件经 custom 通道转发，带 parent_tool_call_id。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            message = AIMessage.model_construct(
                content="",
                tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": '{"expression": "1 + 1"}'}],
            )
            yield "updates", {"model": {"messages": [message]}}
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="2",
                                tool_call_id="inner-1",
                                name="calculator_tool",
                            )
                        ]
                    }
                },
            )
            yield "updates", {"model": {"messages": [AIMessage(content="1 + 1 = 2")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="算一下 1+1", subagent_type="explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "1 + 1 = 2"
    # 用量事件（每个 updates 后一条）与过程事件同通道，序列断言先滤掉
    written_filtered = [
        payload for payload in written if payload["event"].get("event_type") != "subagent_context_usage"
    ]
    # 工具开始 → 工具结束 → 子智能体最终叙述
    assert [payload["event"]["event_type"] for payload in written_filtered] == [
        "tool_started",
        "tool_completed",
        "subagent_message",
    ]
    started = written_filtered[0]
    completed = written_filtered[1]
    assert written_filtered[2]["event"]["content"] == "1 + 1 = 2"
    assert started["type"] == "subagent_tool"
    started_event = started["event"]
    assert started_event["event_type"] == "tool_started"
    assert started_event["tool_call_id"] == "inner-1"
    assert started_event["tool_name"] == "calculator_tool"
    assert started_event["args"] == {"expression": "1 + 1"}
    assert started_event["parent_tool_call_id"] == "call_1"
    assert completed["type"] == "subagent_tool"
    event = completed["event"]
    assert event["event_type"] == "tool_completed"
    assert event["tool_call_id"] == "inner-1"
    assert event["content"] == "2"
    assert event["truncated"] is False
    assert event["parent_tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_subagent_tool_survives_missing_stream_writer(monkeypatch):
    """无 custom 流上下文（如离线批处理）时事件转发静默降级，不影响结论回传。"""

    def _raise():
        raise RuntimeError("outside graph context")

    monkeypatch.setattr(subagents_module, "get_stream_writer", _raise)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="结论")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析", subagent_type="explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "结论"


def test_iter_subagent_stream_events_truncates_long_results():
    long_text = "x" * (subagents_module.SUBAGENT_RESULT_PREVIEW_MAX_CHARS + 100)

    events = subagents_module._iter_subagent_stream_events(
        [
            ToolMessage(content=long_text, tool_call_id="inner-1", name="file_read"),
            ToolMessage(content="失败原因", tool_call_id="inner-2", name="data_analyzing_tool", status="error"),
        ],
        parent_tool_call_id="call_1",
    )

    assert len(events) == 2
    truncated = events[0]
    assert truncated["event_type"] == "tool_completed"
    assert truncated["truncated"] is True
    assert len(truncated["content"]) < len(long_text)
    assert "已截断" in truncated["content"]
    failed = events[1]
    assert failed["event_type"] == "tool_failed"
    assert failed["content"] == "失败原因"
    assert failed["truncated"] is False


def test_iter_subagent_stream_events_fills_missing_tool_timing():
    """内层工具被前置校验拒绝时，完成事件用 tool_started 时刻兜底，保证有耗时。"""
    tool_started_at: dict[str, int] = {}
    events = subagents_module._iter_subagent_stream_events(
        [
            AIMessage(content="", tool_calls=[{"id": "inner-1", "name": "data_analyzing_tool", "args": {}}]),
            ToolMessage(content="请先加载技能", tool_call_id="inner-1", name="data_analyzing_tool", status="error"),
        ],
        parent_tool_call_id="call_1",
        tool_started_at=tool_started_at,
    )

    started = next(event for event in events if event["event_type"] == "tool_started")
    failed = next(event for event in events if event["event_type"] == "tool_failed")
    assert isinstance(started["started_at_ms"], int)
    assert failed["started_at_ms"] == started["started_at_ms"]
    assert failed["ended_at_ms"] >= failed["started_at_ms"]
    # 完成后回收计时记录，避免长会话累积
    assert tool_started_at == {}


@pytest.mark.asyncio
async def test_subagent_tool_forwards_and_stamps_subagent_compactions(monkeypatch):
    """子图压缩：自定义事件转发 + 压缩记录随 subagent 结果持久化。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="结论", id="m1")]}}
            yield (
                "updates",
                {
                    "compress": {
                        "context_compression_events": [
                            {
                                "id": "context-compression-abc",
                                "phase": "completed",
                                "timestamp": "2026-11-01 10:00:00",
                                "estimated_tokens_before": 900,
                                "estimated_tokens_after": 300,
                                "estimated_cleared_tokens": 600,
                                "compressed_messages": 4,
                            }
                        ]
                    }
                },
            )

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="盘点", subagent_type="explore", runtime=_fake_runtime()
    )

    compaction_events = [
        payload["event"]
        for payload in written
        if isinstance(payload, dict)
        and payload.get("type") == "subagent_tool"
        and payload.get("event", {}).get("event_type") == "subagent_context_compression"
    ]
    assert len(compaction_events) == 1
    assert compaction_events[0]["estimated_tokens_before"] == 900
    assert result.update["messages"][0].additional_kwargs["subagent_compactions"] == [
        {
            "id": "context-compression-abc",
            "timestamp": "2026-11-01 10:00:00",
            "anchor_message_id": "",
            "estimated_tokens_before": 900,
            "estimated_tokens_after": 300,
            "estimated_cleared_tokens": 600,
            "compressed_messages": 4,
        }
    ]


def test_aggregate_subagent_token_usage_sums_model_calls():
    """子图 token 用量按每个模型调用的 usage_metadata 累加（含缓存/推理明细）。"""
    usage = subagents_module._aggregate_subagent_token_usage(
        [
            AIMessage(
                content="一步",
                usage_metadata={
                    "input_tokens": 1_000,
                    "output_tokens": 100,
                    "total_tokens": 1_100,
                    "input_token_details": {"cache_read": 400, "cache_creation": 20},
                    "output_token_details": {"reasoning": 30},
                },
            ),
            AIMessage(
                content="二步",
                usage_metadata={
                    "input_tokens": 2_000,
                    "output_tokens": 200,
                    "total_tokens": 2_200,
                    "input_token_details": {"cache_read": 600},
                },
            ),
            ToolMessage(content="工具结果", tool_call_id="inner-1", name="file_read"),
            AIMessage(content="无 usage"),
        ]
    )
    assert usage == {
        "input_tokens": 3_000,
        "output_tokens": 300,
        "total_tokens": 3_300,
        "cache_read_input_tokens": 1_000,
        "cache_creation_input_tokens": 20,
        "reasoning_tokens": 30,
    }
    assert subagents_module._aggregate_subagent_token_usage([]) == {}


@pytest.mark.asyncio
async def test_subagent_tool_stamps_subagent_token_usage(monkeypatch):
    """subagent 结果附带子图 token 用量，供前端任务卡/过程面板展示。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="结论",
                                usage_metadata={"input_tokens": 500, "output_tokens": 50, "total_tokens": 550},
                            )
                        ]
                    }
                },
            )

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="盘点", subagent_type="explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    assert tool_message.additional_kwargs["subagent_token_usage"] == {
        "input_tokens": 500,
        "output_tokens": 50,
        "total_tokens": 550,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 0,
    }


def test_tool_result_event_and_trail_carry_execution_timing():
    """内层工具耗时与主图同源：ToolMessage 上的起止时间随事件转发并落 trail。"""

    message = ToolMessage(
        content="2",
        tool_call_id="inner-1",
        name="calculator_tool",
        additional_kwargs={"started_at_ms": 1_000, "ended_at_ms": 1_250},
    )
    event = subagents_module._subagent_tool_result_event(message, "call_1")
    assert event["started_at_ms"] == 1_000
    assert event["ended_at_ms"] == 1_250

    # 非法计时（布尔/负数）不落事件
    invalid = subagents_module._subagent_tool_result_event(
        ToolMessage(
            content="x",
            tool_call_id="inner-2",
            name="file_read",
            additional_kwargs={"started_at_ms": True, "ended_at_ms": -1},
        ),
        "call_1",
    )
    assert "started_at_ms" not in invalid
    assert "ended_at_ms" not in invalid

    results = subagents_module._collect_subagent_tool_results([message])
    assert results["inner-1"]["started_at_ms"] == 1_000
    assert results["inner-1"]["ended_at_ms"] == 1_250
    trail_entry = subagents_module._trail_tool_entry(
        {"id": "inner-1", "name": "calculator_tool", "args": {}},
        results,
    )
    assert trail_entry["started_at_ms"] == 1_000
    assert trail_entry["ended_at_ms"] == 1_250

    # 历史孤儿工具条目同样保留计时
    orphan = subagents_module._trail_orphan_tool_entry(message, results)
    assert orphan["started_at_ms"] == 1_000
    assert orphan["ended_at_ms"] == 1_250


def test_iter_subagent_stream_events_emits_text_before_tool_calls():
    """同一条 AIMessage 既有文本又有工具调用时按「先说后做」输出。"""
    message = AIMessage(
        content="先算一下 1+1",
        tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": {"expression": "1 + 1"}}],
    )

    events = subagents_module._iter_subagent_stream_events([message], parent_tool_call_id="call_1")

    assert [event["event_type"] for event in events] == ["subagent_message", "tool_started"]
    assert events[0]["content"] == "先算一下 1+1"
    assert events[0]["truncated"] is False
    assert events[0]["parent_tool_call_id"] == "call_1"
    assert events[0]["message_id"] == message.id
    assert events[1]["tool_call_id"] == "inner-1"


def test_progress_snapshot_counts_completed_tools_and_running_tools_separately():
    """工具完成后仍计入累计 tool_calls；running_tools 只表示当前执行中的工具。"""
    message = AIMessage(
        id="assistant-1",
        content="",
        tool_calls=[
            {"id": "inner-1", "name": "file_read", "args": {"path": "/a"}},
            {"id": "inner-2", "name": "query_data", "args": {}},
        ],
    )
    state = subagents_module._SubagentStreamState("call-1")
    subagents_module._process_subagent_chunk(
        ("updates", {"agent": {"messages": [message]}}),
        state,
        None,
        "call-1",
    )
    subagents_module._process_subagent_chunk(
        ("updates", {"tools": {"messages": [ToolMessage(content="ok", tool_call_id="inner-1")]}}),
        state,
        None,
        "call-1",
    )

    snapshot = subagents_module._subagent_progress_snapshot(state)
    assert snapshot["tool_calls"] == 2
    assert snapshot["running_tools"] == 1
    assert snapshot["last_tool"] == "query_data"
    assert snapshot["message_count"] == 2


@pytest.mark.asyncio
async def test_offload_subagent_report_returns_recovery_for_long_report(monkeypatch):
    """超长报告复用统一落盘通道，返回 recovery 供后台任务记录路径。"""

    async def fake_externalize(content, *, source_tool, state, suffix, preview_chars):
        return "offloaded-envelope", {
            "source_tool": source_tool,
            "result_path": "/mnt/user-data/workspace/.system/tool-results/report.md",
            "result_size_chars": len(content),
        }

    monkeypatch.setattr(subagents_module, "externalize_tool_text", fake_externalize)
    runtime = SimpleNamespace(state={"token_usage": {"hard_input_limit_tokens": 1_000}})
    report = "x" * (subagents_module.TOOL_RESULT_OFFLOAD_SIZE + 1)

    delivered, recovery = await subagents_module._offload_subagent_report(report, runtime)

    assert delivered == "offloaded-envelope"
    assert recovery["result_size_chars"] == len(report)
    assert recovery["result_path"].endswith("report.md")


def test_iter_subagent_stream_events_skips_blank_text_and_truncates_long_text():
    long_text = "分析" * (subagents_module.SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS + 10)
    events = subagents_module._iter_subagent_stream_events(
        [
            AIMessage(content="   "),
            AIMessage(content=""),
            AIMessage(content=long_text),
        ],
        parent_tool_call_id="call_1",
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "subagent_message"
    assert events[0]["truncated"] is True
    assert len(events[0]["content"]) < len(long_text)
    assert "已截断" in events[0]["content"]


def test_iter_subagent_stream_events_coerces_string_args():
    import json

    # langchain_core 1.5.4 构造 AIMessage 时强校验 args 必须 dict，
    # 服务端版本宽松会落出字符串形态，用 model_construct 模拟
    message = AIMessage.model_construct(
        content="",
        tool_calls=[
            {"id": "a", "name": "calculator_tool", "args": json.dumps({"expression": "1+1"})},
            {"id": "b", "name": "file_read", "args": json.dumps(json.dumps({"path": "/a"}))},
        ],
    )

    events = subagents_module._iter_subagent_stream_events([message], parent_tool_call_id="call_1")

    assert events[0]["args"] == {"expression": "1+1"}
    assert events[1]["args"] == {"path": "/a"}


@pytest.mark.asyncio
async def test_subagent_tool_rejects_unknown_subagent_type():
    tool = build_task_tool([_spec()])

    with pytest.raises(ValueError) as exc_info:
        await tool.coroutine(
            background=False, description="分析", subagent_type="no-such-agent", runtime=_fake_runtime()
        )

    assert "类型不存在" in str(exc_info.value)
    assert "explore" in str(exc_info.value)


def test_middleware_appends_system_prompt_as_system_message():
    request = SimpleNamespace(
        system_message=SystemMessage(content="主提示词"),
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    merged = subagents_module._append_system_prompt(request, "## 子智能体委派")

    system_message = merged.system_message
    assert isinstance(system_message, SystemMessage)
    assert system_message.content.startswith("主提示词")
    assert "## 子智能体委派" in system_message.content


def test_middleware_creates_system_message_when_request_has_none():
    request = SimpleNamespace(
        system_message=None,
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    merged = subagents_module._append_system_prompt(request, "## 子智能体委派")

    assert isinstance(merged.system_message, SystemMessage)
    assert "## 子智能体委派" in merged.system_message.content


def test_active_subagent_summary_counts_sessions_and_statuses():
    records = [
        SimpleNamespace(task_id="t1", agent_session_id="s1", status="running"),
        SimpleNamespace(task_id="t2", agent_session_id="s2", status="waiting_approval"),
        SimpleNamespace(task_id="t3", agent_session_id="s3", status="paused"),
    ]

    summary = format_active_subagent_summary(records)

    assert summary == "当前会话有 3 个活跃子智能体（执行中 1、等待审批 1、已暂停 1）。"
    assert format_active_subagent_summary([]) == "当前会话没有活跃的子智能体。"


def test_task_list_summary_reports_tasks_sessions_and_active_count():
    records = [
        SimpleNamespace(task_id="t1", agent_session_id="s1", status="completed"),
        SimpleNamespace(task_id="t2", agent_session_id="s1", status="running"),
    ]

    assert format_subagent_task_list_summary(records) == ("子智能体任务（共 2 条，涉及 1 个子智能体，活跃 1 条）：")


@pytest.mark.asyncio
async def test_subagent_middleware_injects_current_active_activity_summary(monkeypatch):
    middleware = SubagentMiddleware([_spec()])
    records = [
        SimpleNamespace(task_id="t1", agent_session_id="s1", status="running"),
        SimpleNamespace(task_id="t2", agent_session_id="s2", status="waiting_approval"),
    ]
    calls = {}

    class _ActivityService:
        async def list_tasks(self, conversation_id, user_id, *, statuses, limit):
            calls.update(
                conversation_id=conversation_id,
                user_id=user_id,
                statuses=statuses,
                limit=limit,
            )
            return records

    monkeypatch.setattr(subagents_module, "get_subagent_task_service", lambda: _ActivityService())
    request = SimpleNamespace(
        system_message=SystemMessage(content="主提示词"),
        state={"conversation_id": "conv-1", "user_id": "u1"},
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    seen = {}

    async def handler(current):
        seen["request"] = current
        return "handled"

    assert await middleware.awrap_model_call(request, handler) == "handled"
    content = seen["request"].system_message.content
    assert "## 子智能体委派" in content
    assert "## 当前子智能体状态" in content
    assert "当前会话有 2 个活跃子智能体（执行中 1、等待审批 1）" in content
    assert calls["conversation_id"] == "conv-1"
    assert calls["user_id"] == "u1"
    assert "running" in calls["statuses"]


@pytest.mark.asyncio
async def test_subagent_middleware_activity_query_failure_does_not_block_prompt(monkeypatch):
    middleware = SubagentMiddleware([_spec()])

    class _BrokenService:
        async def list_tasks(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    monkeypatch.setattr(subagents_module, "get_subagent_task_service", lambda: _BrokenService())
    request = SimpleNamespace(
        system_message=SystemMessage(content="主提示词"),
        state={"conversation_id": "conv-1", "user_id": "u1"},
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    seen = {}

    async def handler(current):
        seen["request"] = current
        return "handled"

    assert await middleware.awrap_model_call(request, handler) == "handled"
    content = seen["request"].system_message.content
    assert "## 子智能体委派" in content
    assert "## 当前子智能体状态" not in content


def test_middleware_registers_single_subagent_tool_with_agent_catalog():
    middleware = SubagentMiddleware([_spec(), _spec(name="report-writer")])

    # 单一 subagent 工具同时承担派发与后台任务管理
    assert [tool.name for tool in middleware.tools] == ["subagent"]
    assert middleware.subagent_names == {"explore", "report-writer"}
    assert "explore" in middleware.tools[0].description
    assert "report-writer" in middleware.tools[0].description
    assert 'action="start"' in middleware.tools[0].description
    assert 'action="check"' in middleware.tools[0].description


def test_passthrough_keys_cover_subagent_state_schema_fields():
    """白名单必须覆盖子智能体状态 schema 的全部透传业务字段。

    不含基类框架字段；压缩三键（context_summary / context_compression /
    context_compression_events）与 token_usage 是子图自有状态（子图压缩/用量
    中间件写入，生命周期属于本次派发），有意不从主图透传，也不得出现在白名单里。
    """
    from apps.chat_data_app.agent.middleware.subagents import ChatDataSubAgentState

    framework_keys = {"messages", "jump_to", "structured_response"}
    subgraph_owned_keys = {
        "context_summary",
        "context_compression",
        "context_compression_events",
        "loaded_skills",
        "token_usage",
        # 控制/审批通道：子图内部使用，不来自主图业务状态。
        "approval_decisions",
        "subagent_task_id",
        "subagent_owner_token",
    }
    schema_keys = {
        key
        for key in ChatDataSubAgentState.__annotations__
        if key not in framework_keys and key not in subgraph_owned_keys
    }
    assert schema_keys and schema_keys <= set(SUBAGENT_STATE_PASSTHROUGH_KEYS)
    assert not (set(SUBAGENT_STATE_PASSTHROUGH_KEYS) & subgraph_owned_keys)


def test_subagent_state_declares_token_usage_channel():
    """子图必须声明 token_usage channel，用量中间件的明细更新才不会被丢弃。"""
    from apps.chat_data_app.agent.middleware.subagents import ChatDataSubAgentState

    assert "token_usage" in ChatDataSubAgentState.__annotations__


@pytest.mark.asyncio
async def test_subagent_tool_streams_text_and_thinking_deltas(monkeypatch):
    """正文/思考以 token 级增量经 custom 流转发；完整消息只补收敛标记不重复带内容。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            assert stream_mode == ["updates", "messages"]
            yield (
                "messages",
                (AIMessageChunk(content="", additional_kwargs={"reasoning_content": "先查数据"}, id="m1"), {}),
            )
            yield "messages", (AIMessageChunk(content="结论是", id="m1"), {})
            yield "messages", (AIMessageChunk(content="上升", id="m1"), {})
            yield "updates", {"model": {"messages": [AIMessage(content="结论是上升", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析", subagent_type="explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "结论是上升"
    # 用量事件（每个 updates 后一条）与过程事件同通道，序列断言先滤掉
    written = [payload for payload in written if payload["event"].get("event_type") != "subagent_context_usage"]
    assert [payload["event"]["event_type"] for payload in written] == [
        "subagent_thinking_delta",
        "subagent_message_delta",
        "subagent_message_delta",
        "subagent_message",
    ]
    thinking_delta = written[0]["event"]
    assert thinking_delta["delta"] == "先查数据"
    assert thinking_delta["message_id"] == "m1"
    assert thinking_delta["parent_tool_call_id"] == "call_1"
    assert [payload["event"]["delta"] for payload in written[1:3]] == ["结论是", "上升"]
    # 收敛：完整消息事件不再带内容，仅标记已流出
    final_event = written[3]["event"]
    assert final_event["streamed"] is True
    assert final_event["content"] == ""
    assert final_event["truncated"] is False


@pytest.mark.asyncio
async def test_subagent_tool_delta_streaming_caps_cumulative_text(monkeypatch):
    """正文增量累计超过上限后停止转发，完整消息事件带截断标记。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    cap = subagents_module.SUBAGENT_MESSAGE_PREVIEW_MAX_CHARS

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "messages", (AIMessageChunk(content="x" * (cap - 5), id="m1"), {})
            # 最后一小段触发封顶：只外发剩余 5 字符 + 截断标记
            yield "messages", (AIMessageChunk(content="yyyyyyyy", id="m1"), {})
            yield "messages", (AIMessageChunk(content="不会外发", id="m1"), {})
            yield "updates", {"model": {"messages": [AIMessage(content="x" * cap + "yyyyyyyy", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析", subagent_type="explore", runtime=_fake_runtime()
    )

    assert result.update["messages"][0].content == "x" * cap + "yyyyyyyy"
    # 用量事件（每个 updates 后一条）与过程事件同通道，先滤掉再断言增量序列
    written = [payload for payload in written if payload["event"].get("event_type") != "subagent_context_usage"]
    deltas = [payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_message_delta"]
    assert len(deltas) == 2
    assert deltas[0]["truncated"] is False
    assert len(deltas[1]["delta"]) == 5 + len("\n…[内容过长已截断]")
    assert deltas[1]["truncated"] is True
    # 封顶后的增量不再外发，完整消息仅补截断标记
    final_event = written[-1]["event"]
    assert final_event["streamed"] is True
    assert final_event["truncated"] is True


def test_iter_chunk_stream_events_keeps_text_alongside_tool_call_chunks():
    """正文与工具调用增量同 chunk 时正文必须转发（丢内容回归）。

    OpenAI 兼容实现里 ``content`` 与 ``tool_calls`` 是独立字段，同一 chunk 完全
    可以「文本 + 工具增量」同时到达。若整块跳过，这段文本永远补不回来——该消息
    已因先前的 delta 进入 ``streamed_text_ids``，完整消息事件按收敛策略只补标记、
    不再携带 content。工具参数碎片也不会漏进来：``_chunk_text_delta`` 只取
    ``str`` 与 ``type == "text"`` 的内容块。
    """
    text_caps: dict = {}

    tool_chunk = AIMessageChunk(
        content="配置文件",
        tool_call_chunks=[{"name": "file_read", "args": '{"path":', "id": "c1", "index": 0, "type": "tool_call_chunk"}],
        id="m1",
    )
    events = subagents_module._iter_chunk_stream_events(tool_chunk, "call_1", text_caps, {})
    assert [event["event_type"] for event in events] == ["subagent_message_delta"]
    assert events[0]["delta"] == "配置文件"
    assert events[0]["message_id"] == "m1"

    # content blocks 形式：非 text 块（工具调用等）不参与正文增量
    block_chunk = AIMessageChunk(
        content=[{"type": "text", "text": "正文片段"}, {"type": "tool_use", "name": "file_read", "input": {}}],
        id="m2",
    )
    events = subagents_module._iter_chunk_stream_events(block_chunk, "call_1", text_caps, {})
    assert [event["delta"] for event in events] == ["正文片段"]

    # 无 message_id 的 chunk 不增量转发（由完整消息事件兜底）
    anonymous = AIMessageChunk(content="无id文本")
    assert subagents_module._iter_chunk_stream_events(anonymous, "call_1", {}, {}) == []

    # 非 AIMessageChunk（如 ToolMessageChunk）不产生文本增量
    assert (
        subagents_module._iter_chunk_stream_events(ToolMessage(content="x", tool_call_id="c"), "call_1", {}, {}) == []
    )


@pytest.mark.asyncio
async def test_subagent_tool_keeps_text_when_chunk_carries_tool_call(monkeypatch):
    """正文与工具增量同 chunk：前端拼出的全文必须等于完整消息（丢内容回归）。"""
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "messages", (AIMessageChunk(content="我需要先读取", id="m1"), {})
            # 文本与工具增量同时到达（OpenAI 兼容实现常见）
            yield (
                "messages",
                (
                    AIMessageChunk(
                        content="配置文件",
                        tool_call_chunks=[
                            {"name": "file_read", "args": '{"path":', "id": "c1", "index": 0, "type": "tool_call_chunk"}
                        ],
                        id="m1",
                    ),
                    {},
                ),
            )
            yield "updates", {"model": {"messages": [AIMessage(content="我需要先读取配置文件", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    await tool.coroutine(background=False, description="分析", subagent_type="explore", runtime=_fake_runtime())

    events = [payload["event"] for payload in written]
    deltas = [event["delta"] for event in events if event["event_type"] == "subagent_message_delta"]
    final = next(event for event in events if event["event_type"] == "subagent_message")
    # 复刻前端收敛口径：streamed 为真时展示增量拼出的全文
    shown = "".join(deltas) if final["streamed"] else final["content"]
    assert shown == "我需要先读取配置文件"


@pytest.mark.asyncio
async def test_subagent_tool_assigns_fallback_id_to_messages_without_id(monkeypatch):
    """provider 不返回 chunk id 时，完整消息事件仍带可用 message_id。

    前端以 message_id 作为 trail 条目主键，空 id 会被整条丢弃——不能把「中间文本
    是否可见」押在 provider 一定返回 id 上。
    """
    written = []
    monkeypatch.setattr(subagents_module, "get_stream_writer", lambda: written.append)
    shared_message = AIMessage(content="无id完整")

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            # 无 id 的增量不转发
            yield "messages", (AIMessageChunk(content="无id增量"), {})
            # 同一条无 id 消息出现在两个节点的 updates 里
            yield "updates", {"model": {"messages": [shared_message]}}
            yield "updates", {"tools": {"messages": [shared_message]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    await tool.coroutine(background=False, description="分析", subagent_type="explore", runtime=_fake_runtime())

    finals = [payload["event"] for payload in written if payload["event"]["event_type"] == "subagent_message"]
    # 同一对象收敛到同一个兜底 id，去重后只发一次
    assert len(finals) == 1
    assert finals[0]["message_id"] == "call_1:anon-1"
    assert finals[0]["content"] == "无id完整"
    assert finals[0]["streamed"] is False


def test_stream_cap_state_returns_none_after_cap():
    cap = subagents_module._StreamCapState()
    assert cap.take("abc", 5) == ("abc", False)
    assert cap.take("d", 5) == ("d", False)
    # 超出剩余额度：外发剩余 1 字符 + 截断标记
    assert cap.take("efg", 5) == ("e" + "\n…[内容过长已截断]", True)
    assert cap.capped is True
    assert cap.take("more", 5) == (None, False)

    # 恰好填满后再来的增量：无内容可带，封顶标记由完整消息事件的 truncated 兜底
    exact = subagents_module._StreamCapState()
    assert exact.take("abcde", 5) == ("abcde", False)
    assert exact.take("f", 5) == (None, False)
    assert exact.capped is True


# ---- 子智能体过程快照（历史回放持久化） ----


def test_build_trail_snapshot_mirrors_sse_shape():
    """快照与前端 SubagentTrailItem 同构：先说后做、工具回填终态。"""
    messages = [
        AIMessage(
            content="先算一下",
            additional_kwargs={"reasoning_content": "需要精确计算"},
            tool_calls=[{"id": "inner-1", "name": "calculator_tool", "args": {"expression": "1 + 1"}}],
        ),
        ToolMessage(content="2", tool_call_id="inner-1", name="calculator_tool"),
        AIMessage(content="1 + 1 = 2", id="m-final"),
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    assert [item["kind"] for item in trail] == ["message", "tool", "message"]
    first = trail[0]
    assert first["id"].startswith("trail-anon-")  # 无 id 的 AIMessage 也要有稳定 key
    assert first["content"] == "先算一下"
    assert first["thinking"] == "需要精确计算"
    assert first["truncated"] is False and first["thinkingTruncated"] is False
    tool_item = trail[1]
    assert tool_item == {
        "kind": "tool",
        "id": "inner-1",
        "tool_name": "calculator_tool",
        "args": {"expression": "1 + 1"},
        "result": "2",
        "status": "completed",
    }
    assert trail[2] == {
        "kind": "message",
        "id": "m-final",
        "content": "1 + 1 = 2",
        "truncated": False,
        "thinking": "",
        "thinkingTruncated": False,
    }


def test_build_trail_snapshot_marks_error_and_truncates():
    long_text = "x" * (subagents_module.SUBAGENT_RESULT_PREVIEW_MAX_CHARS + 100)
    messages = [
        AIMessage.model_construct(
            content="",
            tool_calls=[{"id": "inner-1", "name": "file_read", "args": {"path": "/a"}}],
        ),
        ToolMessage(content=long_text, tool_call_id="inner-1", name="file_read", status="error"),
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    # 纯工具调用消息（无文本无思考）不产生 message 条目
    assert [item["kind"] for item in trail] == ["tool"]
    tool_item = trail[0]
    assert tool_item["status"] == "error"
    assert tool_item["result"].startswith("x" * 10)
    assert "已截断" in tool_item["result"]
    assert len(tool_item["result"]) < len(long_text)


def test_build_trail_snapshot_skips_blank_and_deduplicates():
    messages = [
        AIMessage(content="   "),
        AIMessage(content="结论", id="m1"),
        AIMessage(content="结论", id="m1"),  # 同 id 重复出现（多节点 updates）
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    assert len(trail) == 1
    assert trail[0]["id"] == "m1"
    assert trail[0]["content"] == "结论"


@pytest.mark.asyncio
async def test_subagent_tool_stamps_trail_snapshot_on_tool_message(monkeypatch):
    """task ToolMessage 携带过程快照，随消息持久化供历史回放还原。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="开始计算",
                                tool_calls=[
                                    {"id": "inner-1", "name": "calculator_tool", "args": {"expression": "1 + 1"}}
                                ],
                            )
                        ]
                    }
                },
            )
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="2",
                                tool_call_id="inner-1",
                                name="calculator_tool",
                            )
                        ]
                    }
                },
            )
            yield "updates", {"model": {"messages": [AIMessage(content="1 + 1 = 2", id="m-final")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="算一下 1+1", subagent_type="explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    trail = tool_message.additional_kwargs["subagent_trail"]
    assert [item["kind"] for item in trail] == ["message", "tool", "message"]
    assert trail[1]["result"] == "2"
    assert trail[2]["content"] == "1 + 1 = 2"


@pytest.mark.asyncio
async def test_subagent_tool_omits_trail_when_no_inner_activity(monkeypatch):
    """子图完全无可见输出时不塞空快照；仅有最终结论时快照与直播一致（含该消息）。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": []}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析", subagent_type="explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    assert "subagent_trail" not in tool_message.additional_kwargs


@pytest.mark.asyncio
async def test_subagent_tool_trail_includes_final_message_like_live_stream(monkeypatch):
    """最终结论消息在直播中本就作为 subagent_message 出现在 trail 尾部，快照保持一致。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield "updates", {"model": {"messages": [AIMessage(content="直接给出结论", id="m1")]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析", subagent_type="explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    trail = tool_message.additional_kwargs["subagent_trail"]
    assert trail == [
        {
            "kind": "message",
            "id": "m1",
            "content": "直接给出结论",
            "truncated": False,
            "thinking": "",
            "thinkingTruncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_subagent_tool_stamps_trail_on_failure(monkeypatch):
    """子图中途抛异常时返回 error ToolMessage 并盖章已收集的 trail 快照。

    真实场景：子图耗尽 recursion_limit —— 直播时内层过程已通过 SSE 转发，
    若失败走 tool_error 中间件（无 additional_kwargs），历史里 trail 会丢失。
    """

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            yield (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="开始查询",
                                tool_calls=[{"id": "inner-1", "name": "query_data", "args": {}}],
                            )
                        ]
                    }
                },
            )
            yield (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="查询成功",
                                tool_call_id="inner-1",
                                name="query_data",
                            )
                        ]
                    }
                },
            )
            raise RuntimeError("Recursion limit of 40 reached without hitting a stop condition.")

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析订单", subagent_type="explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.status == "error"
    assert "子智能体执行失败" in tool_message.content
    assert "Recursion limit" in tool_message.content
    trail = tool_message.additional_kwargs["subagent_trail"]
    assert [item["kind"] for item in trail] == ["message", "tool"]
    assert trail[1]["result"] == "查询成功"


@pytest.mark.asyncio
async def test_subagent_tool_failure_without_inner_activity_omits_trail(monkeypatch):
    """异常发生在任何可见输出之前时不塞空快照，与成功路径口径一致。"""

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            raise RuntimeError("模型连接失败")
            yield  # pragma: no cover

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    tool = build_task_tool([_spec()])

    result = await tool.coroutine(
        background=False, description="分析", subagent_type="explore", runtime=_fake_runtime()
    )

    tool_message = result.update["messages"][0]
    assert tool_message.status == "error"
    assert "子智能体执行失败" in tool_message.content
    assert "subagent_trail" not in tool_message.additional_kwargs


def test_build_trail_snapshot_tolerates_malformed_inner_tool_calls():
    """LLM 畸形 tool_calls 与乱序 ToolMessage 不炸快照、不产生脏条目。"""
    messages = [
        AIMessage.model_construct(
            content="",
            tool_calls=[
                "not-a-dict",  # 非 dict：跳过
                {"id": "", "name": "x", "args": {}},  # 空 id：跳过
                {"id": "inner-1", "name": "file_read", "args": {}},
                {"id": "inner-1", "name": "file_read", "args": {}},  # 重复 id：跳过
            ],
        ),
        # 孤儿 ToolMessage：先于对应 tool_calls 到达（乱序输入容忍）
        ToolMessage(content="结果", tool_call_id="inner-2", name="file_read"),
        # 空 tool_call_id 的 ToolMessage：直接跳过
        ToolMessage(content="无id", tool_call_id="", name="file_read"),
    ]

    trail = subagents_module._build_trail_snapshot(messages)

    ids = [item["id"] for item in trail]
    assert ids == ["inner-1", "inner-2"]
    normal = trail[0]
    assert normal["tool_name"] == "file_read"
    assert normal["status"] == "completed"  # 无 ToolMessage 回填 → 默认完成
    assert normal["result"] == ""
    orphan = trail[1]
    assert orphan["result"] == "结果"
    assert orphan["tool_name"] == "file_read"


@pytest.mark.asyncio
async def test_compiled_subagent_mounts_tool_result_middleware(monkeypatch, tmp_path):
    """真实编译的子图必须挂 ToolResultMiddleware：内层查询工具返回的内部协议
    JSON 在出口被外置成小信封（content_in_context=false），全量数据不进子图
    上下文——与主图口径一致（MIDDLEWARE_CANONICAL_ORDER 中紧跟归一化器）。"""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool as lc_tool

    from apps.chat_data_app.agent import tool_results
    from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module
    from apps.chat_data_app.services.agent_run import conversation_workspace

    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    paths = conversation_workspace.ensure_conversation_workspace("conv_sub", "user_sub")

    class _ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    @lc_tool
    def query_tool(query: str) -> str:
        """查询数据（测试用：返回内部协议结果）。"""
        return tool_results.build_internal_query_result(
            {
                "status": "succeeded",
                "sql": "select 1",
                "results": [{"status": "succeeded", "data": [{"value": 1}]}],
                "total_count": 1,
            },
            query_mode="dsl2sql",
        )

    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "inner-q1",
                        "type": "tool_call",
                        "name": "query_tool",
                        "args": {"query": "总量"},
                    }
                ],
            ),
            AIMessage(content="查询完成，共 1 行"),
        ]
    )

    spec = _spec(tools=[query_tool], model=model)
    tool = build_task_tool([spec])

    result = await tool.coroutine(
        background=False,
        description="统计总量",
        subagent_type="explore",
        runtime=_fake_runtime(
            {
                "user_id": "user_sub",
                "conversation_id": "conv_sub",
                "workspace": {"workspace_root": paths["root"], "workspace_backend": "local"},
            }
        ),
    )

    tool_message = result.update["messages"][0]
    assert tool_message.content == "查询完成，共 1 行"
    trail = tool_message.additional_kwargs["subagent_trail"]
    tool_entries = [item for item in trail if item["kind"] == "tool"]
    assert len(tool_entries) == 1

    inner_result = tool_entries[0]["result"]
    assert "content_in_context" in inner_result
    assert "result_path" in inner_result
    # 全量数据被外置，不内联进子图上下文
    assert '[{"value": 1}]' not in inner_result
    # 完整结果落到会话工作区私有目录
    saved = list((Path(paths["workspace"]) / ".system" / "query_result").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8"))["results"][0]["data"] == [{"value": 1}]


@pytest.mark.asyncio
async def test_compiled_subagent_survives_raising_tool(monkeypatch, tmp_path):
    """工具 raise 后子图不得崩溃：ToolErrorMiddleware 把异常转成 error ToolMessage，
    子智能体读到错误详情后继续产出结论（回归：工具失败显式化改造后，
    子图未挂 ToolErrorMiddleware 会让整个 task 失败）。"""
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool as lc_tool

    from apps.chat_data_app.agent.workspace import runtime as workspace_runtime_module
    from apps.chat_data_app.services.agent_run import conversation_workspace

    monkeypatch.setattr(workspace_runtime_module, "CHAT_DATA_HOME", str(tmp_path))
    monkeypatch.setattr("utils.storage_paths.THREADS_DIR", tmp_path / "threads")
    conversation_workspace.ensure_conversation_workspace("conv_raise", "user_raise")

    class _ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    @lc_tool
    def broken_tool(query: str) -> str:
        """查询数据（测试用：直接抛异常）。"""
        raise ValueError("当前会话未绑定数据源")

    model = _ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "inner-e1",
                        "type": "tool_call",
                        "name": "broken_tool",
                        "args": {"query": "总量"},
                    }
                ],
            ),
            AIMessage(content="工具报错未绑定数据源，无法继续分析"),
        ]
    )

    spec = _spec(tools=[broken_tool], model=model)
    tool = build_task_tool([spec])

    result = await tool.coroutine(
        background=False,
        description="统计总量",
        subagent_type="explore",
        runtime=_fake_runtime(
            {
                "user_id": "user_raise",
                "conversation_id": "conv_raise",
                "workspace": {"workspace_root": str(tmp_path), "workspace_backend": "local"},
            }
        ),
    )

    tool_message = result.update["messages"][0]
    # 子图正常跑完，回传的是模型最终结论而非"子智能体执行失败"
    assert tool_message.content == "工具报错未绑定数据源，无法继续分析"
    assert tool_message.status != "error"
    trail = tool_message.additional_kwargs["subagent_trail"]
    tool_entries = [item for item in trail if item["kind"] == "tool"]
    assert len(tool_entries) == 1
    assert tool_entries[0]["status"] == "error"
    assert "未绑定数据源" in tool_entries[0]["result"]


def test_budget_alert_middleware_silent_below_threshold(monkeypatch):
    """上下文未达告警阈值时原样放行请求，不追加任何提示。"""
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=10**9),
    )
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    request = SimpleNamespace(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="短任务结论")],
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    assert middleware.wrap_model_call(request, handler) == "done"
    assert seen["request"] is request


class _FakeModelRequest:
    """保留属性的假 ModelRequest：override 合并字段，供跨轮次断言 tools/system_message。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def override(self, **kwargs):
        merged = dict(self.__dict__)
        merged.update(kwargs)
        return _FakeModelRequest(**merged)


def _over_threshold_request():
    return _FakeModelRequest(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="事实" * 1000)],
        tools=["file_read"],
    )


def test_budget_alert_middleware_appends_warning_over_threshold(monkeypatch):
    """上下文估算达到阈值时，本轮请求尾部追加预算收尾提示 + tool_choice="none"（双层收尾）。"""
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(compression_trigger_tokens=100),
    )
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    request = _over_threshold_request()

    assert middleware.wrap_model_call(request, handler) == "done"
    # 注入通道=隐藏 HumanMessage 追加在消息尾部，system prompt 保持原样
    request_messages = seen["request"].messages
    assert len(request_messages) == len(request.messages) + 1
    alert_message = request_messages[-1]
    assert isinstance(alert_message, HumanMessage)
    assert alert_message.additional_kwargs.get("budget_alert") is True
    assert alert_message.additional_kwargs.get("hide_from_ui") is True
    content = alert_message.content
    assert "上下文预算提示" in content
    assert "当前已完成任务为" in content
    assert "待完成任务为" in content
    # 只汇报事实，不替主智能体决定后续安排
    assert "续接任务书为" not in content
    assert seen["request"].system_message.content == "主提示词"
    assert "建议" not in content
    # 工具 schema 保留（与已废弃的 override(tools=[]) 的本质区别：schema 消失才是
    # Qwen 裸 <tool_call> 正文退化的诱因），仅 API 参数层禁止本轮调用
    assert seen["request"].tools == ["file_read"]
    assert seen["request"].tool_choice == "none"

    # 低于阈值零注入，tool_choice 不设置（模型正常自主决策）
    fresh = _FakeModelRequest(
        system_message=SystemMessage(content="主提示词"),
        messages=[AIMessage(content="短")],
        tools=["file_read"],
    )
    assert middleware.wrap_model_call(fresh, handler) == "done"
    assert seen["request"].system_message.content == "主提示词"
    assert getattr(seen["request"], "tool_choice", None) is None


def test_budget_alert_middleware_env_override_takes_precedence(monkeypatch):
    """SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO 覆盖主图同源阈值（压测/调试用）。

    主图预算取超大值（正常情况下永不提示）；覆盖比例 0.5 × 窗口 200 = 100，
    同样体量的消息即触发提示，且提示文案里的阈值是换算后的覆盖值而非主图值；
    覆盖线被硬限 clamp（150）兜住；软约束不摘工具。
    """
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(
            compression_trigger_tokens=10**9,
            context_window=200,
            hard_input_limit_tokens=150,
        ),
    )
    monkeypatch.setattr(subagents_module, "SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO", 0.5)
    middleware = subagents_module.create_subagent_budget_alert_middleware(_spec(), [])
    seen = {}

    def handler(req):
        seen["request"] = req
        return "done"

    request = _over_threshold_request()

    assert middleware.wrap_model_call(request, handler) == "done"
    # 注入通道=隐藏 HumanMessage 追加在消息尾部（非 system prompt 追加）
    request_messages = seen["request"].messages
    assert len(request_messages) == len(request.messages) + 1
    alert_message = request_messages[-1]
    assert isinstance(alert_message, HumanMessage)
    assert alert_message.additional_kwargs.get("budget_alert") is True
    assert alert_message.additional_kwargs.get("hide_from_ui") is True
    assert "上下文预算提示" in alert_message.content
    assert "100 tokens" in alert_message.content
    assert seen["request"].system_message.content == "主提示词"
    assert seen["request"].tools == ["file_read"]


def test_budget_alert_ratio_override_clamped_by_hard_limit(monkeypatch):
    """比例换算出的告警线超过硬限时被 clamp 到硬限，不会出现告警线越过硬限。"""
    monkeypatch.setattr(
        subagents_module,
        "get_model_context_budget",
        lambda model_id: SimpleNamespace(
            compression_trigger_tokens=10**9,
            context_window=1000,
            hard_input_limit_tokens=60,
        ),
    )
    monkeypatch.setattr(subagents_module, "SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO", 0.9)
    meta = subagents_module._subagent_budget_meta(_spec(), [])
    assert meta["threshold_tokens"] == 60
