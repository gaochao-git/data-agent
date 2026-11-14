"""后台子智能体（task 工具 background=true）测试：派生、完成上报、通知注入、check/cancel/list 与容量护栏。

注册表为模块级按 conversation_id 分桶：跨 Run 兜底注入依赖这一点，
测试用例各自用独立 conversation_id（或清桶）避免串扰。
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from apps.chat_data_app.agent.middleware import subagent_async
from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import SubagentMiddleware

CONV = "conv-bg-test"


@pytest.fixture(autouse=True)
def _clear_background_registry():
    """模块级注册表跨测试共享：每个用例前后清桶，避免串扰。"""
    subagent_async._BACKGROUND_TASKS.clear()
    yield
    subagent_async._BACKGROUND_TASKS.clear()


def _spec(name="explore", **overrides):
    kwargs = {
        "name": name,
        "description": "深度分析子智能体",
        "system_prompt": "你是分析子智能体",
        "tools": [],
        "model": object(),
    }
    kwargs.update(overrides)
    return subagents_module.SubagentSpec(**kwargs)


def _fake_runtime(state=None, tool_call_id="call_1"):
    base = {"conversation_id": CONV, "user_id": "u1"}
    base.update(state or {})
    return SimpleNamespace(state=base, tool_call_id=tool_call_id)


def _fake_request(state=None):
    return SimpleNamespace(
        state=state if state is not None else {"conversation_id": CONV},
        system_message=None,
        override=lambda **kwargs: SimpleNamespace(**kwargs),
    )


def _middleware_with_fake_subgraph(monkeypatch, *, report="盘点结论：一切正常", fail=False):
    """构建假子图中间件：astream 产出指定报告或抛错，返回 (middleware, registry)。"""

    from langchain_core.messages import AIMessage

    class FakeRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            if fail:
                raise RuntimeError("子图炸了")
            yield "updates", {"model": {"messages": [AIMessage(content=report)]}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: FakeRunnable())
    middleware = SubagentMiddleware([_spec()])
    return middleware, subagent_async._BACKGROUND_TASKS.setdefault(CONV, {})


async def _drain(entry):
    """等待后台任务收口（确定性：直接 await 任务本身）。"""
    if entry.asyncio_task is not None:
        await entry.asyncio_task


@pytest.mark.asyncio
async def test_spawn_returns_immediately_and_registers_running(monkeypatch):
    """background=true：立即返回确认文本（不等待子图），注册表按会话记 running。"""
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch)
    tool = middleware.tools[0]

    result = await tool.coroutine(
        description="长任务盘点",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_bg_1"),
        background=True,
    )

    assert isinstance(result, str)
    assert "task_id=call_bg_1" in result
    assert "后台启动" in result
    entry = bucket["call_bg_1"]
    assert entry.status == "running"
    assert entry.subagent_type == "explore"
    assert entry.conversation_id == CONV
    assert entry.asyncio_task is not None
    await _drain(entry)
    assert entry.status == "completed"
    assert "盘点结论" in entry.report


@pytest.mark.asyncio
async def test_background_task_failure_marks_failed(monkeypatch):
    """子图执行异常：条目转 failed，报告带异常摘要（供完成通知注入）。"""
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch, fail=True)
    tool = middleware.tools[0]

    await tool.coroutine(
        description="会失败的任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_bg_fail"),
        background=True,
    )
    await _drain(bucket["call_bg_fail"])

    entry = bucket["call_bg_fail"]
    assert entry.status == "failed"
    assert "子图炸了" in entry.report


@pytest.mark.asyncio
async def test_wrap_model_call_injects_notification_once_then_cleans_registry(monkeypatch):
    """完成通知只注入一次：注入后条目出册（下轮不再重复），通知进系统提示。"""
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch, report="近30天盘点结论")
    tool = middleware.tools[0]

    await tool.coroutine(
        description="后台盘点",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_bg_notify"),
        background=True,
    )
    await _drain(bucket["call_bg_notify"])
    assert bucket["call_bg_notify"].status == "completed"

    request = _fake_request()

    async def handler(req):
        return req

    merged = await middleware.awrap_model_call(request, handler)
    assert "后台子智能体完成通知" in merged.system_message.content
    assert "近30天盘点结论" in merged.system_message.content
    assert "call_bg_notify" in merged.system_message.content

    # 注入后条目出册：第二次模型调用不再重复注入，空桶也出册
    merged2 = await middleware.awrap_model_call(request, handler)
    assert "后台子智能体完成通知" not in merged2.system_message.content
    assert CONV not in subagent_async._BACKGROUND_TASKS


@pytest.mark.asyncio
async def test_notification_survives_run_boundary_for_next_run(monkeypatch):
    """跨 Run 兜底：父 Run 结束（不再注入）后完成的报告，由下一次 Run 注入。"""
    middleware1, bucket = _middleware_with_fake_subgraph(monkeypatch)
    tool = middleware1.tools[0]

    # 场景C同款：取消/结束后台任务在父 Run 收口后才完成——本 Run 不会再有模型调用
    await tool.coroutine(
        description="跨Run任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_cross"),
        background=True,
    )
    await _drain(bucket["call_cross"])
    assert bucket["call_cross"].status == "completed"

    # 下一次 Run：全新中间件实例（create_data_agent 按 Run 编译），同一会话
    middleware2 = SubagentMiddleware([_spec()])
    request = _fake_request()

    async def handler(req):
        return req

    merged = await middleware2.awrap_model_call(request, handler)
    assert "后台子智能体完成通知" in merged.system_message.content
    assert "盘点结论" in merged.system_message.content


@pytest.mark.asyncio
async def test_check_async_task_running_completed_and_missing(monkeypatch):
    """check_async_task：执行中/已完成/不存在三态。"""
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch)
    check = middleware.tools[1]

    # 未派发：不存在
    missing = await check.coroutine(task_id="call_none", runtime=_fake_runtime())
    assert "不存在" in missing

    # 派发后 running
    await middleware.tools[0].coroutine(
        description="查我",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_check"),
        background=True,
    )
    running = await check.coroutine(task_id="call_check", runtime=_fake_runtime())
    assert "[执行中]" in running
    assert "查我" in running

    # 完成后 completed + 报告
    await _drain(bucket["call_check"])
    done = await check.coroutine(task_id="call_check", runtime=_fake_runtime())
    assert "[completed]" in done
    assert "盘点结论" in done


@pytest.mark.asyncio
async def test_cancel_async_task_stops_running_task(monkeypatch):
    """cancel_async_task：running → cancelled；子任务被取消。"""
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch)

    class HangingRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            import asyncio

            await asyncio.sleep(3600)
            yield "updates", {"model": {"messages": []}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: HangingRunnable())
    tool = middleware.tools[0]
    await tool.coroutine(
        description="挂起任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_hang"),
        background=True,
    )
    entry = bucket["call_hang"]
    assert entry.status == "running"

    cancel = middleware.tools[2]
    result = await cancel.coroutine(task_id="call_hang", runtime=_fake_runtime())
    assert "已取消" in result
    assert entry.status == "cancelled"
    # 已取消条目保留在册（供任务列表展示），不重复取消
    again = await cancel.coroutine(task_id="call_hang", runtime=_fake_runtime())
    assert "无需取消" in again


@pytest.mark.asyncio
async def test_list_async_tasks_lists_all_states(monkeypatch):
    """list_async_tasks：空态/混合态列出全部任务。"""
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch)
    listing = middleware.tools[3]

    empty = await listing.coroutine(runtime=_fake_runtime())
    assert "当前会话没有后台子智能体任务" in empty

    # 一个执行中（Hanging）+ 跑完一个（completed）
    class HangingRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            import asyncio

            await asyncio.sleep(3600)
            yield "updates", {"model": {"messages": []}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: HangingRunnable())
    tool = middleware.tools[0]
    await tool.coroutine(
        description="挂起",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_h2"),
        background=True,
    )
    text = await listing.coroutine(runtime=_fake_runtime())
    assert "执行中" in text

    entry = bucket["call_h2"]
    entry.asyncio_task.cancel()
    entry.status = "cancelled"
    text2 = await listing.coroutine(runtime=_fake_runtime())
    assert "cancelled" in text2
    # 清理挂起协程
    entry.asyncio_task.cancel()


@pytest.mark.asyncio
async def test_background_capacity_guard(monkeypatch):
    """同时执行中的后台任务达上限后拒绝新派发。"""
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch)
    tool = middleware.tools[0]
    limit = subagent_async.MAX_BACKGROUND_TASKS

    class HangingRunnable:
        async def astream(self, _state, _config, *, stream_mode):
            import asyncio

            await asyncio.sleep(3600)
            yield "updates", {"model": {"messages": []}}

    monkeypatch.setattr(subagents_module, "_compile_subagent", lambda spec, run_controls=None: HangingRunnable())
    for k in range(limit):
        result = await tool.coroutine(
            description=f"任务{k}",
            subagent_type="explore",
            runtime=_fake_runtime(tool_call_id=f"call_h{k}"),
            background=True,
        )
        assert "后台启动" in result
    overflow = await tool.coroutine(
        description="超限任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_overflow"),
        background=True,
    )
    assert "已达上限" in overflow
    for entry in bucket.values():
        entry.asyncio_task.cancel()


@pytest.mark.asyncio
async def test_spawn_unknown_type_returns_error_without_registry_write(monkeypatch):
    middleware, bucket = _middleware_with_fake_subgraph(monkeypatch)
    tool = middleware.tools[0]
    result = await tool.coroutine(
        description="x",
        subagent_type="不存在",
        runtime=_fake_runtime(tool_call_id="call_x"),
        background=True,
    )
    assert "类型不存在" in result
    assert bucket == {}


@pytest.mark.asyncio
async def test_sync_path_unchanged_when_background_false(monkeypatch):
    """默认 background=false：行为与旧版完全一致（Command + ToolMessage）。"""
    middleware, _bucket = _middleware_with_fake_subgraph(monkeypatch, report="同步结论")
    tool = middleware.tools[0]

    result = await tool.coroutine(
        description="同步任务",
        subagent_type="explore",
        runtime=_fake_runtime(tool_call_id="call_sync"),
    )
    assert not isinstance(result, str)
    msg = result.update["messages"][0]
    assert isinstance(msg, ToolMessage)
    assert msg.content == "同步结论"


@pytest.mark.asyncio
async def test_system_prompt_documents_background_and_task_tools():
    middleware = SubagentMiddleware([_spec()])
    assert "background=true" in middleware.system_prompt
    assert "list_async_tasks" in middleware.system_prompt
    assert "cancel_async_task" in middleware.system_prompt
