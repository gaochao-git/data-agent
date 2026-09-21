"""工具 intent 元数据契约：所有模型可见工具的入参 schema 必须声明 intent 参数。

intent 是面向用户的调用目的说明（前端时间线默认展示，技术细节悬浮展示），
由前端 ``getToolCompactDescription`` 通用读取。新增工具漏加 intent 时本测试失败，
作为清单防止口径漂移。``task``/``write_todos`` 为 langchain 框架内置工具，不受本契约约束。
"""

from types import SimpleNamespace

from apps.chat_data_app.agent.tools.ask_user_question import ask_user_question_tool
from apps.chat_data_app.agent.tools.bash_run import create_bash_run_tool
from apps.chat_data_app.agent.tools.calculator import calculator_tool
from apps.chat_data_app.agent.tools.current_time import get_current_time_tool
from apps.chat_data_app.agent.tools.data_analyzing import create_data_analyzing_tool
from apps.chat_data_app.agent.tools.filesystem import create_filesystem_tools
from apps.chat_data_app.agent.tools.officecli_run import create_officecli_run_tool
from apps.chat_data_app.agent.tools.parse_document import create_parse_document_tool
from apps.chat_data_app.agent.tools.present_artifacts import create_present_artifacts_tool
from apps.chat_data_app.agent.tools.read_image import create_read_image_tool


def _collect_model_visible_tools() -> list:
    runtime = SimpleNamespace()
    return [
        *create_filesystem_tools(runtime),
        *create_parse_document_tool(runtime),
        *create_read_image_tool(runtime),
        create_present_artifacts_tool(runtime),
        get_current_time_tool,
        create_data_analyzing_tool(),
        calculator_tool,
        ask_user_question_tool,
        create_officecli_run_tool(runtime),
        create_bash_run_tool(runtime),
    ]


def test_every_model_visible_tool_declares_intent_metadata():
    tools = _collect_model_visible_tools()
    assert len(tools) == 21
    missing = sorted(
        tool.name for tool in tools if "intent" not in (tool.args or {})
    )
    assert missing == [], f"以下工具缺少 intent 参数: {missing}"


def test_intent_description_mentions_user_facing_purpose():
    """intent 的用途说明必须进入参数 schema description。

    函数式工具通过 ``parse_docstring=True`` + Google 风格 Args 段提供，
    pydantic 工具通过 Field description 提供；两种形态统一后不允许再出现
    "说明只在 docstring 正文、schema description 为空"的旧写法。
    """
    for tool in _collect_model_visible_tools():
        description = str((tool.args or {}).get("intent", {}).get("description") or "")
        assert "面向用户展示" in description, (
            f"{tool.name} 的 intent 缺少 schema 参数描述（函数式工具需 parse_docstring=True 且 Args 段完整）"
        )
