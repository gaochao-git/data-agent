"""子智能体运行期进度快照：压缩次数与「正在压缩」状态窗口。

从 ``test_subagents_middleware.py`` 拆出，避免单文件超过 2000 行红线；
被测符号由 ``subagents`` 模块统一 re-export，行为与原用例完全一致。
"""

import asyncio
from types import SimpleNamespace

from apps.chat_data_app.agent.middleware import subagents as subagents_module


def test_progress_snapshot_carries_running_compaction_count():
    """运行期压缩次数随心跳落 progress，任务卡不必等任务收口。"""
    state = subagents_module._SubagentStreamState("call-1")
    state.compression_events.extend([{"id": "c1"}, {"id": "c2"}])
    assert subagents_module._subagent_progress_snapshot(state)["compaction_count"] == 2


def test_progress_snapshot_marks_compressing_only_between_started_and_completed():
    """任务卡「正在压缩」只在 started 已到、completed 未到的窗口内为 True。"""
    state = subagents_module._SubagentStreamState("call-1")
    assert subagents_module._subagent_progress_snapshot(state)["compressing"] is False

    subagents_module._process_subagent_chunk(
        (
            "custom",
            {
                "type": "context_compression",
                "event": {"id": "started-1", "phase": "started", "timestamp": "2026-11-15 22:30:00"},
            },
        ),
        state,
        None,
        "call-1",
    )
    snapshot = subagents_module._subagent_progress_snapshot(state)
    assert snapshot["compressing"] is True
    # 占位不计入完成次数，否则任务卡会提前显示「压缩 1 次」。
    assert snapshot["compaction_count"] == 0

    subagents_module._process_subagent_chunk(
        (
            "updates",
            {
                "compress": {
                    "context_compression_events": [
                        {
                            "id": "done-1",
                            "phase": "completed",
                            "estimated_tokens_before": 900,
                            "estimated_tokens_after": 300,
                            "compressed_messages": 4,
                        }
                    ]
                }
            },
        ),
        state,
        None,
        "call-1",
    )
    snapshot = subagents_module._subagent_progress_snapshot(state)
    assert snapshot["compressing"] is False
    assert snapshot["compaction_count"] == 1


def test_progress_snapshot_ignores_invalid_started_chunk():
    """无 id 的 started 帧不转发，也不能把任务卡钉在「正在压缩」。"""
    state = subagents_module._SubagentStreamState("call-1")
    subagents_module._process_subagent_chunk(
        ("custom", {"type": "context_compression", "event": {"phase": "started"}}),
        state,
        None,
        "call-1",
    )
    assert subagents_module._subagent_progress_snapshot(state)["compressing"] is False


def test_grouped_tool_schemas_keep_status_and_decision(monkeypatch):
    """manage 保留 status，decide 保留 decision，并各自透传到执行分支。"""
    spec = subagents_module.SubagentSpec(name="explore", description="d", system_prompt="s", tools=[], model=object())
    tool = subagents_module.build_task_tool([spec])
    assert "status" in subagents_module.ManageArgs.model_fields
    assert "decision" in subagents_module.DecideArgs.model_fields

    captured: dict = {}

    async def fake_manage(action, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(subagents_module, "manage_async_task", fake_manage)
    result = asyncio.run(
        tool.coroutine(
            manage=subagents_module.ManageArgs(action="list", status="completed"),
            runtime=SimpleNamespace(state={}, tool_call_id="call_1"),
        )
    )
    assert result == "ok"
    assert captured["status"] == "completed"


def test_subagent_tool_schema_does_not_expose_timeout_seconds():
    """子任务超时只认全局配置，工具参数不向模型暴露 timeout_seconds。"""
    spec = subagents_module.SubagentSpec(name="explore", description="d", system_prompt="s", tools=[], model=object())
    tool = subagents_module.build_task_tool([spec])

    assert "timeout_seconds" not in repr(tool.args_schema.model_json_schema())
    assert "timeout_seconds" not in tool.description
