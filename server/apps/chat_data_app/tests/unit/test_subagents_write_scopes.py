"""子智能体并发写范围：归一化、冲突提示与账本写入的软控制测试。"""

from types import SimpleNamespace

import pytest

from apps.chat_data_app.agent.contracts.subagent_task import (
    WORKSPACE_WRITE_SCOPE,
    normalize_write_scopes,
    write_scopes_overlap,
)
from apps.chat_data_app.agent.middleware.subagent_async import (
    _apply_task_write_scopes,
    format_subagent_agent_list,
)
from apps.chat_data_app.agent.middleware.subagents import (
    SubagentMiddleware,
    SubagentSpec,
    _validate_tool_write_scopes,
)


def test_subagent_tool_exposes_write_scopes_and_dispatch_prompt():
    middleware = SubagentMiddleware(
        [
            SubagentSpec(
                name="general",
                description="通用执行子智能体",
                system_prompt="你是执行子智能体",
                tools=[],
                model=object(),
            )
        ]
    )

    assert "write_scopes" in middleware.tools[0].description
    schema = middleware.tools[0].args_schema.model_json_schema()
    assert set(schema["properties"]) == {"dispatch", "send", "manage"}
    assert "write_scopes" in schema["$defs"]["DispatchArgs"]["properties"]
    # 写范围只在 dispatch 声明；send 续写固定继承上一轮，不能再覆盖。
    assert "write_scopes" not in schema["$defs"]["SendArgs"]["properties"]
    assert "## 并发写与任务分发" in middleware.system_prompt


def test_write_scopes_normalize_and_overlap_by_path_prefix():
    assert normalize_write_scopes("outputs/word") == ["outputs/word"]
    assert normalize_write_scopes(
        [
            " ./outputs/word/ ",
            "outputs\\ppt",
            "outputs/word",
            "a/../b",
            "workspace",
        ]
    ) == ["outputs/word", "outputs/ppt", WORKSPACE_WRITE_SCOPE]
    assert normalize_write_scopes(["", "  ", "/", "."]) == []
    assert write_scopes_overlap("outputs", "outputs/word")
    assert not write_scopes_overlap("outputs/word", "outputs/ppt")
    assert write_scopes_overlap(WORKSPACE_WRITE_SCOPE, "outputs/ppt")


def test_validate_tool_write_scopes_rejects_unsafe_paths():
    assert _validate_tool_write_scopes(None) is None
    assert _validate_tool_write_scopes(["outputs\\word"]) == ["outputs/word"]
    with pytest.raises(ValueError, match="write_scopes"):
        _validate_tool_write_scopes(["../outside"])


@pytest.mark.asyncio
async def test_apply_task_write_scopes_is_best_effort():
    record = SimpleNamespace(task_id="task-1", owner_token="owner-1")

    class _NoSetter:
        pass

    class _BrokenSetter:
        async def set_task_write_scopes(self, *_args):
            raise RuntimeError("db down")

    class _Setter:
        async def set_task_write_scopes(self, *_args):
            return "updated"

    assert await _apply_task_write_scopes(_NoSetter(), record, ["outputs/word"], log_label="测试") is record
    assert await _apply_task_write_scopes(_BrokenSetter(), record, ["outputs/word"], log_label="测试") is record
    assert await _apply_task_write_scopes(_Setter(), record, ["outputs/word"], log_label="测试") == "updated"


def test_agent_list_warns_about_overlapping_write_scopes():
    records = [
        SimpleNamespace(
            task_id="task-word",
            agent_id="agent-word",
            agent_session_id="agent-word",
            status="running",
            subagent_type="general",
            label="生成 Word",
            turn_index=1,
            write_scopes=("outputs/word",),
        ),
        SimpleNamespace(
            task_id="task-ppt",
            agent_id="agent-ppt",
            agent_session_id="agent-ppt",
            status="running",
            subagent_type="general",
            label="生成 PPT",
            turn_index=1,
            write_scopes=("outputs/word/pptx",),
        ),
    ]

    output = format_subagent_agent_list(records)

    assert "写范围=outputs/word" in output
    assert "写范围提示" in output
    assert "写范围重叠" in output


def test_agent_list_does_not_warn_for_disjoint_write_scopes():
    records = [
        SimpleNamespace(
            task_id="task-word",
            agent_id="agent-word",
            agent_session_id="agent-word",
            status="running",
            subagent_type="general",
            label="生成 Word",
            turn_index=1,
            write_scopes=("outputs/word",),
        ),
        SimpleNamespace(
            task_id="task-ppt",
            agent_id="agent-ppt",
            agent_session_id="agent-ppt",
            status="running",
            subagent_type="general",
            label="生成 PPT",
            turn_index=1,
            write_scopes=("outputs/ppt",),
        ),
    ]

    output = format_subagent_agent_list(records)

    assert "写范围=outputs/word" in output
    assert "写范围=outputs/ppt" in output
    assert "写范围提示" not in output


def test_agent_list_reads_legacy_meta_scope_and_warns_when_other_is_missing():
    legacy = SimpleNamespace(
        task_id="task-legacy",
        agent_id="agent-legacy",
        agent_session_id="agent-legacy",
        status="running",
        subagent_type="general",
        label="旧任务",
        turn_index=1,
        write_scopes=None,
        meta={"write_scopes": "outputs/word"},
    )
    missing = SimpleNamespace(
        task_id="task-missing",
        agent_id="agent-missing",
        agent_session_id="agent-missing",
        status="running",
        subagent_type="general",
        label="未声明任务",
        turn_index=1,
    )

    output = format_subagent_agent_list([legacy, missing])

    assert "写范围=outputs/word" in output
    assert "未完整声明写范围" in output
