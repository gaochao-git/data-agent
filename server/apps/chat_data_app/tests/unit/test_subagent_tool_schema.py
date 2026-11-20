"""单工具 ``subagent`` 的扁平 action 参数契约。"""

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


def _operation(payload: dict):
    return SubagentToolArgs.model_validate(payload).to_operation()


def test_dispatch_flat_arguments_normalize_to_dispatch_args():
    name, args = _operation({"action": "dispatch", "description": "盘点", "subagent_type": "explore"})

    assert name == "dispatch"
    assert isinstance(args, DispatchArgs)
    assert args.description == "盘点"
    assert args.background is True


def test_send_flat_arguments_normalize_to_send_args():
    name, args = _operation({"action": "send", "task_id": "t1", "message": "继续"})

    assert name == "send"
    assert isinstance(args, SendArgs)
    assert args.task_id == "t1"
    assert args.background is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"action": "list", "status": "running"}, {"action": "list", "status": "running"}),
        ({"action": "check", "task_id": "t1"}, {"action": "check", "task_id": "t1"}),
        (
            {"action": "wait", "task_ids": ["t1"], "wait_seconds": 5},
            {"action": "wait", "task_ids": ["t1"], "wait_seconds": 5},
        ),
        ({"action": "cancel", "task_id": "t1"}, {"action": "cancel", "task_id": "t1"}),
    ],
)
def test_manage_flat_actions_normalize_to_manage_args(payload, expected):
    name, args = _operation(payload)

    assert name == "manage"
    assert isinstance(args, ManageArgs)
    for key, value in expected.items():
        assert getattr(args, key) == value


def test_requires_action():
    with pytest.raises(ValidationError, match="action"):
        SubagentToolArgs.model_validate({"description": "盘点", "subagent_type": "explore"})


def test_rejects_legacy_nested_arguments():
    with pytest.raises(ValidationError, match="扁平 action"):
        SubagentToolArgs.model_validate({"dispatch": {"description": "盘点", "subagent_type": "explore"}})
    with pytest.raises(ValidationError, match="扁平 action"):
        SubagentToolArgs.model_validate({"send": {"task_id": "t1", "message": "继续"}})


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "dispatch", "description": "盘点", "subagent_type": "explore", "task_id": "t1"},
        {"action": "dispatch", "description": "盘点", "subagent_type": "explore", "message": "x"},
        {"action": "send", "task_id": "t1", "message": "继续", "description": "x"},
        {"action": "send", "task_id": "t1", "message": "继续", "status": "running"},
        {"action": "list", "task_id": "t1"},
        {"action": "check", "task_id": "t1", "message": "x"},
        {"action": "wait", "task_ids": ["t1"], "task_id": "t1"},
        {"action": "cancel", "task_id": "t1", "agent_id": "a1"},
    ],
)
def test_rejects_fields_not_allowed_for_action(payload):
    with pytest.raises(ValidationError, match="不支持参数"):
        SubagentToolArgs.model_validate(payload)


@pytest.mark.parametrize(
    ("payload", "missing"),
    [
        ({"action": "dispatch", "subagent_type": "explore"}, "description"),
        ({"action": "dispatch", "description": "盘点"}, "subagent_type"),
        ({"action": "send", "task_id": "t1"}, "message"),
        ({"action": "send", "message": "继续"}, "task_id"),
        ({"action": "check"}, "task_id"),
        ({"action": "cancel"}, "task_id"),
        ({"action": "wait", "task_ids": []}, "task_ids"),
    ],
)
def test_requires_action_specific_fields(payload, missing):
    with pytest.raises(ValidationError, match=missing):
        SubagentToolArgs.model_validate(payload)


def test_top_level_intent_is_optional_and_not_an_action_field():
    args = SubagentToolArgs.model_validate({"intent": "查看进行中的任务", "action": "list"})

    assert args.intent == "查看进行中的任务"
    assert SubagentToolArgs.model_validate({"action": "list"}).intent == ""


def test_timeout_seconds_is_not_a_tool_parameter_anywhere():
    for model in (SubagentToolArgs, DispatchArgs, SendArgs, ManageArgs):
        assert "timeout_seconds" not in model.model_fields
    with pytest.raises(ValidationError, match="timeout_seconds"):
        DispatchArgs(description="盘点", subagent_type="explore", timeout_seconds=1)
    with pytest.raises(ValidationError, match="timeout_seconds"):
        SubagentToolArgs.model_validate(
            {"action": "dispatch", "description": "盘点", "subagent_type": "explore", "timeout_seconds": 1}
        )


def test_legacy_decide_is_rejected_as_unknown_field():
    """审批入口已从模型可见契约移除；旧调用不能再通过校验。"""
    with pytest.raises(ValidationError, match="decide"):
        SubagentToolArgs.model_validate({"action": "list", "decide": {"task_id": "t1", "decision": "allow"}})


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
    assert set(tool.args_schema.model_json_schema()["properties"]) == {
        "intent",
        "action",
        "description",
        "subagent_type",
        "background",
        "label",
        "task_id",
        "message",
        "task_ids",
        "agent_id",
        "status",
        "wait_seconds",
    }
    assert "runtime" not in tool.get_input_schema().model_json_schema()["properties"]


def test_tool_description_explains_flat_argument_shape():
    """工具描述必须给出可直接照抄的扁平 JSON，避免小模型退回分组嵌套参数。"""
    description = str(build_task_tool([_spec()]).description or "")

    assert '"action":"list"' in description
    assert '"action":"dispatch"' in description
    assert '"action":"send"' in description
    assert '"dispatch":{' not in description
    assert '"send":{"task_id"' not in description
    assert '"intent":"盘点报告目录"' in description


def test_tool_description_and_system_prompt_split_contract_from_policy():
    """类型清单只在工具描述完整出现；策略只在系统提示，避免两处同义重复。"""
    description = str(build_task_tool([_spec()]).description or "")
    prompt = subagents_module._default_system_prompt()

    assert "只读盘点子智能体" in description
    assert "只读盘点子智能体" not in prompt
    assert "{available_agents}" not in prompt
    assert "可用类型、能力边界和参数见 subagent 工具说明" in prompt
    assert 'subagent({"action":"list"})' in prompt
    assert 'subagent({"action":"send"' in prompt

    for phrase in ("达到上限后 dispatch 会被拒绝", "present_artifacts", "file_write", "并发规则见系统提示"):
        assert phrase not in description

    assert "仅异步任务运行中可插话" in description
    assert "先 file_write 到 workspace" in prompt
    assert "目标、要求和交付格式直接写进任务书" in prompt


def test_parameter_descriptions_keep_field_semantics_only():
    """参数描述只讲字段含义，不重复系统提示里的并发与异步策略。"""
    assert "所有必要上下文" not in DispatchArgs.model_fields["description"].description
    assert "任务 ID 不阻塞" not in DispatchArgs.model_fields["background"].description


def test_write_scope_feature_is_removed():
    """并发写由系统提示约束，不再暴露 write_scopes 参数或账本字段。"""
    assert "write_scopes" not in DispatchArgs.model_fields
    assert "write_scopes" not in SubagentToolArgs.model_fields
    description = str(build_task_tool([_spec()]).description or "")
    prompt = subagents_module._default_system_prompt()
    assert "write_scopes" not in description
    assert "write_scopes" not in prompt
    assert "写目标不同的任务可以并行" in prompt


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
            "action": "dispatch",
            "description": "盘点工作区",
            "subagent_type": "explore",
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
                "action": "dispatch",
                "description": "盘点工作区",
                "subagent_type": "explore",
                "unknown": True,
                "runtime": object(),
            }
        )


@pytest.mark.asyncio
async def test_tool_invocation_rejects_cross_action_fields():
    tool = build_task_tool([_spec()])

    with pytest.raises(ValidationError, match="不支持参数"):
        await tool.ainvoke(
            {
                "action": "dispatch",
                "description": "盘点工作区",
                "subagent_type": "explore",
                "task_id": "t1",
                "runtime": object(),
            }
        )
