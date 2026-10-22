"""load_skill 工具与 required_skills 前置门禁。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from apps.chat_data_app.agent.middleware.skill_prerequisite import SkillPrerequisiteMiddleware
from apps.chat_data_app.agent.tools.skill import SKILL_TOOL_NAME, create_skill_tool
from apps.chat_data_app.agent.workspace.runtime import ConversationWorkspaceRuntime


def _runtime(tmp_path) -> ConversationWorkspaceRuntime:
    runtime = ConversationWorkspaceRuntime(workspace_root=str(tmp_path), user_id="u1", backend="local")
    skill_dir = runtime.runtime_skills_dir / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ntitle: Demo\n---\n# Demo\n正文内容\n",
        encoding="utf-8",
    )
    runtime.available_skill_names = ["demo"]
    return runtime


@pytest.mark.asyncio
async def test_load_skill_returns_body_and_strips_frontmatter(tmp_path):
    tool = create_skill_tool(_runtime(tmp_path))
    result = await tool.coroutine(name="demo")
    assert result.startswith('<skill_content name="demo">')
    assert "# Demo" in result
    assert "正文内容" in result
    assert "name: demo" not in result
    assert "/mnt/skills/demo" in result


def test_loaded_skill_names_reads_explicit_state_only():
    """门禁只认显式 loaded_skills；历史文本里的 skill_content 不能当状态。"""
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})
    textual_history = {
        "messages": [
            ToolMessage(
                content='<skill_content name="officecli">\nbody\n</skill_content>',
                name=SKILL_TOOL_NAME,
                tool_call_id="c1",
            )
        ]
    }
    text_request = SimpleNamespace(tool_call={"id": "c2", "name": "officecli_run"}, state=textual_history)
    assert middleware._missing_skills(text_request) == ("officecli_run", ["officecli"])

    state_request = SimpleNamespace(
        tool_call={"id": "c3", "name": "officecli_run"},
        state={"loaded_skills": ["officecli"]},
    )
    assert middleware._missing_skills(state_request) is None


@pytest.mark.asyncio
async def test_load_skill_success_writes_loaded_skills_state():
    middleware = SkillPrerequisiteMiddleware({})
    request = SimpleNamespace(
        tool_call={"id": "c1", "name": SKILL_TOOL_NAME, "args": {"name": "officecli"}},
        state={"loaded_skills": []},
    )

    async def _handler(_request):
        return ToolMessage(
            content='<skill_content name="officecli">body</skill_content>',
            name=SKILL_TOOL_NAME,
            tool_call_id="c1",
        )

    result = await middleware.awrap_tool_call(request, _handler)
    assert result.update["loaded_skills"] == ["officecli"]
    assert result.update["messages"][0].name == SKILL_TOOL_NAME


@pytest.mark.asyncio
async def test_load_skill_error_does_not_write_loaded_skills_state():
    middleware = SkillPrerequisiteMiddleware({})
    request = SimpleNamespace(
        tool_call={"id": "c1", "name": SKILL_TOOL_NAME, "args": {"name": "missing"}},
        state={"loaded_skills": []},
    )

    async def _handler(_request):
        return ToolMessage(content="技能不存在", name=SKILL_TOOL_NAME, tool_call_id="c1", status="error")

    result = await middleware.awrap_tool_call(request, _handler)
    assert isinstance(result, ToolMessage)


@pytest.mark.asyncio
async def test_skill_prerequisite_blocks_unloaded_tool():
    middleware = SkillPrerequisiteMiddleware(
        {"officecli_run": ("officecli",), "data_analyzing_tool": ("data-analyzing",)}
    )
    request = SimpleNamespace(tool_call={"id": "c1", "name": "officecli_run"}, state={"loaded_skills": []})

    async def _handler(_request):
        raise AssertionError("未加载技能时不应执行工具")

    result = await middleware.awrap_tool_call(request, _handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "load_skill" in str(result.content)
    assert "officecli" in str(result.content)


@pytest.mark.asyncio
async def test_data_analyzing_tool_requires_its_own_skill_only():
    """真实注册表口径：data_analyzing_tool 只认 data-analyzing。

    加载别的技能不应放行——否则任一技能加载后全部工具都开了口子。
    """
    from apps.chat_data_app.agent.tools.registry import TOOL_REQUIRED_SKILLS

    middleware = SkillPrerequisiteMiddleware(TOOL_REQUIRED_SKILLS)
    blocked_request = SimpleNamespace(
        tool_call={"id": "c2", "name": "data_analyzing_tool"},
        state={"loaded_skills": ["officecli"]},
    )

    async def _must_not_run(_request):
        raise AssertionError("技能不匹配时不应执行工具")

    blocked = await middleware.awrap_tool_call(blocked_request, _must_not_run)
    assert blocked.status == "error"
    assert "data-analyzing" in str(blocked.content)

    allowed_request = SimpleNamespace(
        tool_call={"id": "c4", "name": "data_analyzing_tool"},
        state={"loaded_skills": ["officecli", "data-analyzing"]},
    )

    async def _ok(_request):
        return ToolMessage(content="ok", tool_call_id="c4", name="data_analyzing_tool")

    assert (await middleware.awrap_tool_call(allowed_request, _ok)).content == "ok"


@pytest.mark.asyncio
async def test_skill_prerequisite_allows_loaded_tool_and_ignores_other_tools():
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})

    async def _handler(_request):
        return ToolMessage(content="ok", tool_call_id="c2", name="officecli_run")

    request = SimpleNamespace(
        tool_call={"id": "c2", "name": "officecli_run"},
        state={"loaded_skills": ["officecli"]},
    )
    result = await middleware.awrap_tool_call(request, _handler)
    assert result.content == "ok"

    plain = SimpleNamespace(tool_call={"id": "c3", "name": "file_read"}, state={})
    assert await middleware.awrap_tool_call(plain, _handler) is not None


@pytest.mark.asyncio
async def test_load_skill_rejects_oversized_skill_md(tmp_path):
    from utils.skill_paths import SKILL_MD_MAX_BYTES

    runtime = _runtime(tmp_path)
    skill_md = runtime.runtime_skills_dir / "demo" / "SKILL.md"
    skill_md.write_text("# Demo\n" + "x" * (SKILL_MD_MAX_BYTES + 1), encoding="utf-8")
    tool = create_skill_tool(runtime)
    with pytest.raises(ValueError, match="超过 32KB"):
        await tool.coroutine(name="demo")


@pytest.mark.asyncio
async def test_skill_gate_uses_state_through_real_agent_tool_node():
    """真实 ToolNode：load_skill 成功返回 Command，下一轮门禁即可放行。"""
    from typing import Annotated

    from langchain.agents import create_agent
    from langchain.agents.middleware import AgentState
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import tool as lc_tool

    from apps.chat_data_app.agent.agent_state import merge_loaded_skill_names

    class _State(AgentState, total=False):
        loaded_skills: Annotated[list[str], merge_loaded_skill_names]

    @lc_tool
    def load_skill(name: str) -> str:
        """load"""
        return f'<skill_content name="{name}">body</skill_content>'

    @lc_tool
    def data_analyzing_tool() -> str:
        """exec"""
        return "EXECUTED"

    class _Model(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    model = _Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "load_skill", "args": {"name": "demo"}, "id": "c1", "type": "tool_call"}],
            ),
            AIMessage(
                content="",
                tool_calls=[{"name": "data_analyzing_tool", "args": {}, "id": "c2", "type": "tool_call"}],
            ),
            AIMessage(content="done"),
        ]
    )
    agent = create_agent(
        model,
        tools=[load_skill, data_analyzing_tool],
        system_prompt="s",
        middleware=[SkillPrerequisiteMiddleware({"data_analyzing_tool": ("demo",)})],
        state_schema=_State,
    )
    out = await agent.ainvoke({"messages": [HumanMessage("go")]})
    assert out["loaded_skills"] == ["demo"]
    results = [m for m in out["messages"] if getattr(m, "name", "") == "data_analyzing_tool"]
    assert results and results[-1].status == "success"
    assert results[-1].content == "EXECUTED"
