"""单工具 ``subagent`` 的三组主参数契约。"""

import pytest
from pydantic import ValidationError

from apps.chat_data_app.agent.middleware import subagents as subagents_module
from apps.chat_data_app.agent.middleware.subagents import (
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


def test_top_level_intent_is_optional_and_not_an_operation_group():
    """intent 与其它工具同口径：顶层可选、面向用户展示，不参与三组互斥校验。"""
    args = SubagentToolArgs.model_validate({"intent": "查看进行中的任务", "manage": {"action": "list"}})
    assert args.intent == "查看进行中的任务"
    assert SubagentToolArgs.model_validate({"manage": {"action": "list"}}).intent == ""
    with pytest.raises(ValidationError, match="必须且只能"):
        SubagentToolArgs.model_validate({"intent": "只有目的，没有操作"})


def test_rejects_legacy_flat_arguments():
    with pytest.raises(ValidationError):
        SubagentToolArgs.model_validate({"action": "start", "description": "盘点", "subagent_type": "explore"})
    with pytest.raises(ValidationError):
        SubagentToolArgs.model_validate({"description": "盘点", "subagent_type": "explore"})


def test_timeout_seconds_is_not_a_tool_parameter_anywhere():
    for model in (SubagentToolArgs, DispatchArgs, SendArgs, ManageArgs):
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


def test_legacy_decide_is_rejected_as_unknown_group():
    """审批入口已从模型可见契约移除；旧调用不能再通过校验。"""
    with pytest.raises(ValidationError, match="decide"):
        SubagentToolArgs.model_validate({"decide": {"task_id": "t1", "decision": "allow"}})


def test_send_has_no_write_scopes_and_rejects_legacy_field():
    """写范围只在 dispatch 声明；send 固定继承上一轮范围。"""
    assert "write_scopes" in DispatchArgs.model_fields
    assert "write_scopes" not in SendArgs.model_fields
    with pytest.raises(ValidationError, match="write_scopes"):
        SendArgs(task_id="t1", message="继续", write_scopes=["outputs/a.md"])


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
    assert set(tool.args_schema.model_json_schema()["properties"]) == {"intent", "dispatch", "send", "manage"}
    assert "runtime" not in tool.get_input_schema().model_json_schema()["properties"]


def test_tool_description_explains_nested_argument_shape():
    """工具描述必须给出可直接照抄的嵌套 JSON，避免小模型退回旧平铺参数。"""
    description = str(build_task_tool([_spec()]).description or "")

    assert '"manage":{"action":"list"}}' in description
    assert '"dispatch":{"subagent_type":"explore"' in description
    assert '"send":{"task_id":"任务 ID"' in description
    assert "错误示例（会因顶层未知字段被拒绝）" in description
    assert '{"action":"list"}' in description
    assert '"intent":"盘点报告目录"' in description


def test_tool_description_and_system_prompt_split_contract_from_policy():
    """类型清单只在工具描述完整出现；策略只在系统提示，避免两处同义重复。"""
    description = str(build_task_tool([_spec()]).description or "")
    prompt = subagents_module._default_system_prompt()

    assert "只读盘点子智能体" in description
    assert "只读盘点子智能体" not in prompt
    assert "{available_agents}" not in prompt
    assert "可用类型、能力边界和参数见 subagent 工具说明" in prompt

    for phrase in ("达到上限后 dispatch 会被拒绝", "present_artifacts", "file_write", "并发规则见系统提示"):
        assert phrase not in description

    assert "仅异步任务运行中可插话" in description
    assert "事实文件路径" in description
    assert "先 file_write 到 workspace" in prompt
    assert "目标、要求和交付格式直接写进任务书" in prompt


def test_parameter_descriptions_keep_field_semantics_only():
    """参数描述只讲字段含义，不重复系统提示里的并发与异步策略。"""
    assert "所有必要上下文" not in DispatchArgs.model_fields["description"].description
    assert "并发分发规则见系统提示" not in DispatchArgs.model_fields["write_scopes"].description
    assert "任务 ID 不阻塞" not in DispatchArgs.model_fields["background"].description


@pytest.mark.asyncio
async def test_tool_invocation_accepts_langgraph_runtime_injection(monkeypatch):
    seen = {}

    async def fake_route(_ctx, runtime, dispatch=None, send=None, manage=None):
        seen.update(runtime=runtime, dispatch=dispatch, send=send, manage=manage)
        return "ok"

    monkeypatch.setattr(subagents_module, "_route_subagent_tool", fake_route)
    tool = build_task_tool([_spec()])
    runtime = object()

    result = await tool.ainvoke(
        {
            "intent": "盘点工作区",
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
