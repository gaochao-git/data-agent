import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("DATA_ROOT", "/tmp/fxcjg_test_data")
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1")
os.environ.setdefault("FXCJG_DB_PORT", "3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_PASSWORD", "9xoGNhhQOhCKkg30NF/dPA==:teAdsWfDfAutVqg7GTFJHw==")

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from apps.chat_data_app.agent import data_analyze_graph


def test_data_analyze_tool_metadata_and_dialect_sections():
    assert [item["name"] for item in data_analyze_graph.get_data_analyze_tool_chain()] == [
        "skill_load",
        "file_read",
        "file_edit",
        "present_artifacts",
        "sandbox_run",
        "select_semantic_config_tool",
        "dsl2sql_tool",
        "execute_sql_tool",
    ]
    assert "`skill_load`" in data_analyze_graph.get_data_analyze_allowed_tool_names_text()
    assert "Hive" in data_analyze_graph.build_dialect_prompt_sections({"datasource": {"source_type": "hive"}})[0]
    assert "MySQL" in data_analyze_graph.build_dialect_prompt_sections({"semantic_configs": [{"source_type": "mysql"}]})[0]
    assert "当前数据库方言未明确" in data_analyze_graph.build_dialect_prompt_sections({})[0]


def test_data_analyze_agent_state_exposes_datasource_context():
    annotations = data_analyze_graph.DataAnalyzeAgentState.__annotations__

    assert "messages" in annotations
    assert "datasource_id" in annotations
    assert "semantic_config_id" in annotations
    assert "conversation_id" in annotations


def test_stage_context_truncates_and_merges_stages():
    messages = [HumanMessage(content="用户问题" * 300), AIMessage(content="助手回答")]
    prompt = data_analyze_graph._build_stage_user_prompt(messages, "当前问题", "补充信息")
    merged = data_analyze_graph.merge_task_stages(
        [{"stage": "thinking", "run_id": "r1", "status": "running"}],
        [{"stage": "thinking", "run_id": "r1", "status": "completed"}, {"stage": "", "status": "free"}],
    )

    assert len(prompt) < 4600
    assert "当前用户目标：\n当前问题" in prompt
    assert "补充信息" in prompt
    assert merged[0]["status"] == "completed"
    assert merged[1]["status"] == "free"


def test_create_data_analyze_tools_wraps_expected_tool_names(monkeypatch, tmp_path):
    monkeypatch.setattr(data_analyze_graph, "create_tool_retry_guard", lambda tools: tools)
    tools = data_analyze_graph.create_data_analyze_tools(
        str(tmp_path),
        datasource_id="ds_a",
        semantic_config_id="cfg_a",
        conversation_id="conv_a",
        member_datasource_ids=["m1"],
        user_id="u1",
        operator_name="测试用户",
    )

    assert [tool.name for tool in tools] == [
        "skill_load",
        "file_read",
        "file_edit",
        "present_artifacts",
        "sandbox_run",
        "select_semantic_config_tool",
        "dsl2sql_tool",
        "execute_sql_tool",
    ]


@pytest.mark.asyncio
async def test_create_data_analyze_flow_graph_runs_all_stages(monkeypatch, tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(content="理解用户要分析销售额")
            if self.calls == 2:
                return SimpleNamespace(content="1. 查询数据\n2. 生成图表")
            return SimpleNamespace(content="已完成分析")

    class FakeAgent:
        async def ainvoke(self, state, config=None):
            return {"messages": [AIMessage(content="执行完成")]}

    fake_llm = FakeLLM()
    monkeypatch.setattr(data_analyze_graph, "get_chat_data_llm", lambda **_kwargs: fake_llm)
    monkeypatch.setattr(data_analyze_graph, "create_data_analyze_agent", lambda **_kwargs: FakeAgent())
    monkeypatch.setattr(data_analyze_graph, "invoke_with_token_usage", lambda **_kwargs: asyncio.sleep(0, result=({"messages": [AIMessage(content="执行完成")]}, {"total_tokens": 12})))
    monkeypatch.setattr(data_analyze_graph, "get_stream_writer", lambda: lambda payload: None)
    monkeypatch.setattr(data_analyze_graph, "get_workspace_execution_session", lambda workspace_root, user_id: asyncio.sleep(0, result=SimpleNamespace(sandbox_id="sbx_1")))
    monkeypatch.setattr(data_analyze_graph, "attach_workspace_execution_session", lambda session, user_id: asyncio.sleep(0, result=None))
    monkeypatch.setattr(data_analyze_graph, "cleanup_workspace_execution_session", lambda session: asyncio.sleep(0, result=None))

    graph = data_analyze_graph.create_data_analyze_flow_graph(
        workspace_root=str(tmp_path),
        system_prompt="system",
        user_id="u1",
    )
    result = await graph.ainvoke({
        "messages": [HumanMessage(content="分析销售额")],
        "task_stages": [],
        "task_run_id": "run_1",
    }, config={"configurable": {"thread_id": "conv_graph"}})

    stages = result["task_stages"]
    assert [stage["stage"] for stage in stages] == ["thinking", "plan", "execute", "done"]
    assert stages[0]["content"] == "理解用户要分析销售额"
    assert stages[1]["steps"] == ["查询数据", "生成图表"]
    assert stages[-1]["content"] == "已完成分析"


@pytest.mark.asyncio
async def test_execute_data_analyze_agent_returns_message_for_non_dict(monkeypatch):
    monkeypatch.setattr(data_analyze_graph, "invoke_with_token_usage", lambda **_kwargs: asyncio.sleep(0, result=(AIMessage(content="完成"), {"total_tokens": 3})))
    emitted = []
    monkeypatch.setattr(data_analyze_graph, "get_stream_writer", lambda: emitted.append)

    result = await data_analyze_graph.execute_data_analyze_agent(agent=object(), state={"messages": []}, system_prompt="system", model_id=None)

    assert result["messages"][0].content == "完成"
    assert emitted == [{"type": "token_usage", "usage": {"total_tokens": 3}}]
