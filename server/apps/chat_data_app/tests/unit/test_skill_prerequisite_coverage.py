"""skill_prerequisite 覆盖率补充：同步门禁、状态/请求多形态与 Command 合并。"""

from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from apps.chat_data_app.agent.middleware import skill_prerequisite
from apps.chat_data_app.agent.middleware.skill_prerequisite import (
    SkillPrerequisiteMiddleware,
    _skill_name_from_request,
    _state_value,
    _with_loaded_skill,
    loaded_skill_names,
)
from apps.chat_data_app.agent.tools.skill import SKILL_TOOL_NAME


def test_state_value_supports_objects_and_missing_fields():
    assert _state_value({"loaded_skills": ["a"]}, "loaded_skills") == ["a"]
    assert _state_value(SimpleNamespace(loaded_skills=["b"]), "loaded_skills") == ["b"]
    assert _state_value(SimpleNamespace(), "loaded_skills") is None


def test_loaded_skill_names_ignores_non_sequence_state():
    assert loaded_skill_names({"loaded_skills": "officecli"}) == set()
    assert loaded_skill_names({"loaded_skills": [" officecli ", "", None]}) == {"officecli"}


def test_skill_name_from_request_invalid_shapes():
    assert _skill_name_from_request(SimpleNamespace(tool_call="not-a-dict")) == ""
    assert _skill_name_from_request(SimpleNamespace(tool_call={"args": ["x"]})) == ""
    assert _skill_name_from_request(SimpleNamespace(tool_call={"args": {"name": " demo "}})) == "demo"


def test_with_loaded_skill_merges_command_update():
    base = Command(update={"messages": ["m"]}, goto="next")
    merged = _with_loaded_skill(base, "officecli")
    assert merged.update == {"messages": ["m"], "loaded_skills": ["officecli"]}
    assert merged.goto == "next"
    assert _with_loaded_skill(base, "") is base


def test_missing_skills_ignores_non_dict_tool_call():
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})
    assert middleware._missing_skills(SimpleNamespace(tool_call=None)) is None


def test_sync_wrap_tool_call_rejects_missing_skill():
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})
    request = SimpleNamespace(tool_call={"id": "c1", "name": "officecli_run"}, state={"loaded_skills": []})
    handled = []
    result = middleware.wrap_tool_call(request, lambda _request: handled.append(True))
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert 'load_skill("officecli")' in result.content
    assert handled == []


def test_sync_wrap_tool_call_records_loaded_skill():
    middleware = SkillPrerequisiteMiddleware({})
    request = SimpleNamespace(
        tool_call={"id": "c1", "name": SKILL_TOOL_NAME, "args": {"name": "officecli"}},
        state={"loaded_skills": []},
    )
    message = ToolMessage(content="body", name=SKILL_TOOL_NAME, tool_call_id="c1")
    result = middleware.wrap_tool_call(request, lambda _request: message)
    assert isinstance(result, Command)
    assert result.update["loaded_skills"] == ["officecli"]


def test_sync_wrap_tool_call_passes_through_regular_result():
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})
    request = SimpleNamespace(
        tool_call={"id": "c1", "name": "officecli_run"},
        state={"loaded_skills": ["officecli"]},
    )
    message = ToolMessage(content="ok", name="officecli_run", tool_call_id="c1")
    assert middleware.wrap_tool_call(request, lambda _request: message) is message


def test_with_loaded_skill_leaves_error_message_untouched():
    error_message = ToolMessage(content="failed", name=SKILL_TOOL_NAME, tool_call_id="c1", status="error")
    assert _with_loaded_skill(error_message, "officecli") is error_message


def test_async_wrap_tool_call_passes_through_regular_result():
    middleware = SkillPrerequisiteMiddleware({})
    request = SimpleNamespace(
        tool_call={"id": "c1", "name": "list_dir"},
        state={"loaded_skills": []},
    )
    message = ToolMessage(content="ok", name="list_dir", tool_call_id="c1")

    async def _handler(_request):
        return message

    import asyncio

    assert asyncio.run(middleware.awrap_tool_call(request, _handler)) is message


def test_async_reject_includes_tool_call_id():
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})
    request = SimpleNamespace(tool_call={"id": "call_9", "name": "officecli_run"}, state={})

    async def _handler(_request):
        raise AssertionError("缺技能时不得执行工具")

    import asyncio

    result = asyncio.run(middleware.awrap_tool_call(request, _handler))
    assert result.tool_call_id == "call_9"
    assert result.name == "officecli_run"
    assert skill_prerequisite.SKILL_TOOL_NAME == SKILL_TOOL_NAME
