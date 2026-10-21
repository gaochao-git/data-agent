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
    skill_dir.mkdir(parents=True)
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


def test_loaded_skill_names_reads_successful_tool_history():
    state = {
        "messages": [
            ToolMessage(
                content='<skill_content name="officecli">\nbody\n</skill_content>',
                name=SKILL_TOOL_NAME,
                tool_call_id="c1",
            ),
            ToolMessage(
                content='<skill_content name="broken">',
                name=SKILL_TOOL_NAME,
                tool_call_id="c2",
                status="error",
            ),
        ]
    }
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})
    request = SimpleNamespace(tool_call={"id": "c3", "name": "officecli_run"}, state=state)
    assert middleware._missing_skills(request) is None


@pytest.mark.asyncio
async def test_skill_prerequisite_blocks_unloaded_tool():
    middleware = SkillPrerequisiteMiddleware(
        {"officecli_run": ("officecli",), "data_analyzing_tool": ("data-analyzing",)}
    )
    request = SimpleNamespace(tool_call={"id": "c1", "name": "officecli_run"}, state={"messages": []})

    async def _handler(_request):
        raise AssertionError("未加载技能时不应执行工具")

    result = await middleware.awrap_tool_call(request, _handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "load_skill" in str(result.content)
    assert "officecli" in str(result.content)


@pytest.mark.asyncio
async def test_skill_prerequisite_allows_loaded_tool_and_ignores_other_tools():
    middleware = SkillPrerequisiteMiddleware({"officecli_run": ("officecli",)})
    loaded = ToolMessage(
        content='<skill_content name="officecli">body</skill_content>',
        name=SKILL_TOOL_NAME,
        tool_call_id="c1",
    )

    async def _handler(_request):
        return ToolMessage(content="ok", tool_call_id="c2", name="officecli_run")

    request = SimpleNamespace(
        tool_call={"id": "c2", "name": "officecli_run"},
        state={"messages": [loaded]},
    )
    result = await middleware.awrap_tool_call(request, _handler)
    assert result.content == "ok"

    plain = SimpleNamespace(tool_call={"id": "c3", "name": "file_read"}, state={})
    assert await middleware.awrap_tool_call(plain, _handler) is not None
