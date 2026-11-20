"""单工具 ``subagent`` 的四组主参数契约。"""

import pytest
from pydantic import ValidationError

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import (
    DecideArgs,
    DispatchArgs,
    ManageArgs,
    SendArgs,
    SubagentSpec,
    SubagentToolArgs,
    build_task_tool,
)


def test_requires_exactly_one_operation_group():
    with pytest.raises(ValidationError, match="必须且只能"):
        SubagentToolArgs()
    with pytest.raises(ValidationError, match="必须且只能"):
        SubagentToolArgs(
            dispatch={"description": "盘点", "subagent_type": "explore"},
            manage={"action": "list"},
        )


def test_group_nests_into_typed_model():
    args = SubagentToolArgs.model_validate({"send": {"task_id": "t1", "message": "继续"}})
    assert isinstance(args.send, SendArgs)
    assert args.dispatch is None
    assert args.manage is None
    assert args.decide is None


def test_rejects_legacy_flat_arguments():
    with pytest.raises(ValidationError):
        SubagentToolArgs.model_validate({"action": "start", "description": "盘点", "subagent_type": "explore"})
    with pytest.raises(ValidationError):
        SubagentToolArgs.model_validate({"description": "盘点", "subagent_type": "explore"})


def test_timeout_seconds_is_not_a_tool_parameter_anywhere():
    for model in (SubagentToolArgs, DispatchArgs, SendArgs, ManageArgs, DecideArgs):
        assert "timeout_seconds" not in model.model_fields
    with pytest.raises(ValidationError, match="timeout_seconds"):
        DispatchArgs(description="盘点", subagent_type="explore", timeout_seconds=1)
    with pytest.raises(ValidationError, match="timeout_seconds"):
        SubagentToolArgs.model_validate(
            {"dispatch": {"description": "盘点", "subagent_type": "explore", "timeout_seconds": 1}}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"dispatch": {"description": "盘点", "subagent_type": "explore", "unknown": 1}},
        {"send": {"task_id": "t1", "message": "继续", "unknown": 1}},
        {"manage": {"action": "list", "unknown": 1}},
        {"decide": {"task_id": "t1", "decision": "allow", "unknown": 1}},
        {"dispatch": {"description": "盘点", "subagent_type": "explore"}, "unknown": 1},
    ],
)
def test_unknown_fields_are_rejected_at_every_level(payload):
    with pytest.raises(ValidationError):
        SubagentToolArgs.model_validate(payload)


def test_manage_allows_only_action_specific_fields():
    assert ManageArgs(action="list", status="running").status == "running"
    with pytest.raises(ValidationError, match="不支持参数"):
        ManageArgs(action="list", task_id="t1")

    assert ManageArgs(action="check", task_id="t1").task_id == "t1"
    with pytest.raises(ValidationError, match="不支持参数"):
        ManageArgs(action="check", task_id="t1", wait_seconds=5)

    assert ManageArgs(action="wait", task_ids=["t1"], wait_seconds=5).wait_seconds == 5
    with pytest.raises(ValidationError, match="task_ids"):
        ManageArgs(action="wait")

    assert ManageArgs(action="cancel", task_id="t1").action == "cancel"
    with pytest.raises(ValidationError, match="不支持参数"):
        ManageArgs(action="cancel", task_id="t1", status="running")


def test_decide_requires_explicit_decision():
    assert DecideArgs(task_id="t1", decision="allow").decision == "allow"
    with pytest.raises(ValidationError):
        DecideArgs(task_id="t1")
    with pytest.raises(ValidationError):
        DecideArgs(task_id="t1", decision="maybe")


def _spec():
    return SubagentSpec(
        name="explore",
        description="只读盘点子智能体",
        system_prompt="你是只读盘点子智能体",
        tools=[],
        model=object(),
    )


def test_tool_schema_keeps_runtime_injected_and_hidden_from_model():
    tool = build_task_tool([_spec()])

    assert tool._injected_args_keys == frozenset({"runtime"})
    assert set(tool.args_schema.model_json_schema()["properties"]) == {"dispatch", "send", "manage", "decide"}
    assert "runtime" not in tool.get_input_schema().model_json_schema()["properties"]


def test_tool_description_explains_nested_argument_shape():
    """工具描述必须给出可直接照抄的嵌套 JSON，避免小模型退回旧平铺参数。"""
    description = str(build_task_tool([_spec()]).description or "")

    assert '{"manage":{"action":"list"}}' in description
    assert '{"dispatch":{"subagent_type":"explore"' in description
    assert '{"send":{"task_id":"任务 ID"' in description
    assert "错误示例（会因顶层未知字段被拒绝）" in description
    assert '{"action":"list"}' in description


@pytest.mark.asyncio
async def test_tool_invocation_accepts_langgraph_runtime_injection(monkeypatch):
    seen = {}

    async def fake_route(_ctx, runtime, dispatch=None, send=None, manage=None, decide=None):
        seen.update(runtime=runtime, dispatch=dispatch, send=send, manage=manage, decide=decide)
        return "ok"

    monkeypatch.setattr(subagents_module, "_route_subagent_tool", fake_route)
    tool = build_task_tool([_spec()])
    runtime = object()

    result = await tool.ainvoke(
        {
            "dispatch": {"description": "盘点工作区", "subagent_type": "explore"},
            "runtime": runtime,
        }
    )

    assert result == "ok"
    assert seen["runtime"] is runtime
    assert isinstance(seen["dispatch"], DispatchArgs)
    assert seen["dispatch"].description == "盘点工作区"


@pytest.mark.asyncio
async def test_tool_invocation_still_rejects_unknown_model_fields():
    tool = build_task_tool([_spec()])

    with pytest.raises(ValidationError, match="extra_forbidden"):
        await tool.ainvoke(
            {
                "dispatch": {"description": "盘点工作区", "subagent_type": "explore"},
                "unknown": True,
                "runtime": object(),
            }
        )
