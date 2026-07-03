import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from utils.lang_util import conversation as conversation_module
from utils.lang_util.models import Base


@pytest.fixture()
def conversation_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def create_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(create_tables())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def fake_get_session():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(conversation_module, "get_session", fake_get_session)
    yield
    asyncio.run(engine.dispose())


def test_conversation_serializers_and_helpers():
    now = datetime(2026, 1, 2, 3, 4, 5)
    thread = SimpleNamespace(
        thread_id="conv_a",
        thread_title="标题",
        agent_id="agent_a",
        create_time=now,
        update_time=now,
        is_favorited=1,
    )
    assert conversation_module._format_thread(thread) == {
        "id": "conv_a",
        "title": "标题",
        "agent_id": "agent_a",
        "create_time": "2026-01-02 03:04:05",
        "update_time": "2026-01-02 03:04:05",
        "is_favorited": True,
    }
    assert conversation_module._format_thread(SimpleNamespace(**{**thread.__dict__, "create_time": None, "update_time": None, "is_favorited": None}))["is_favorited"] is False
    assert conversation_module._clean_title("《销售额分析!!!》", fallback="fallback") == "销售额分析"
    assert conversation_module._clean_title("!!", fallback="fallback-title") == "fallback-title"
    assert conversation_module._get_first_user_message([AIMessage(content="a"), HumanMessage(content="hello")]) == "hello"
    assert conversation_module._get_first_user_message([AIMessage(content="a")]) is None

    human = HumanMessage(
        content="用户问题",
        id="h1",
        additional_kwargs={
            "timestamp": "t1",
            "file_ids": ["f1"],
            "semantic_config_selection": {
                "semantic_config_id": "cfg",
                "semantic_config_name": "指标集",
                "confidence": "high",
                "reason": "match",
                "matched_metrics": ["销售额"],
                "matched_dimensions": ["城市"],
                "auto_selected": True,
            },
        },
    )
    ai = AIMessage(
        content="回答",
        id="a1",
        additional_kwargs={"timestamp": "t2"},
        tool_calls=[{"id": "tc1", "name": "tool_a", "args": {"x": 1}}],
    )
    tool = ToolMessage(content="工具结果", tool_call_id="tc1", name="tool_a", id="tool1", additional_kwargs={"timestamp": "t3"})
    serialized = conversation_module._serialize_messages([object(), human, ai, tool])

    assert serialized[0]["role"] == "user"
    assert serialized[0]["file_ids"] == ["f1"]
    assert serialized[0]["semantic_config_selection"]["semantic_config_id"] == "cfg"
    assert serialized[1]["tool_calls"][0]["name"] == "tool_a"
    assert serialized[2]["role"] == "tool"
    assert serialized[2]["tool_call_id"] == "tc1"
    assert serialized[1]["timestamp"] == "t2"
    assert serialized[2]["timestamp"] == "t3"

    stages = conversation_module._serialize_task_stages([
        {"run_id": "old", "stage": "s1", "status": "done"},
        {"run_id": "new", "stage": "s2", "status": "running", "title": "标题", "content": "内容", "steps": ["a", "", "b"]},
        {"run_id": "new", "stage": "", "status": "bad"},
        "ignored",
    ])
    assert stages == [{"stage": "s2", "status": "running", "title": "标题", "content": "内容", "steps": ["a", "b"]}]
    assert conversation_module._serialize_task_stages("bad") == []
    assert conversation_module._serialize_semantic_config_selection(None) is None
    assert conversation_module._serialize_semantic_config_selection({"semantic_config_id": ""}) is None


def test_serialize_messages_does_not_infer_missing_assistant_timestamps():
    human = HumanMessage(content="问题", id="h1", additional_kwargs={"timestamp": "2026-06-21 07:14:24"})
    ai = AIMessage(content="回答", id="a1")
    tool = ToolMessage(content="工具结果", tool_call_id="tc1", name="tool_a", id="tool1")
    followup_ai = AIMessage(content="总结", id="a2")

    serialized = conversation_module._serialize_messages([human, ai, tool, followup_ai])

    assert serialized[0]["timestamp"] == "2026-06-21 07:14:24"
    assert serialized[1]["timestamp"] == ""
    assert serialized[2]["timestamp"] == ""
    assert serialized[3]["timestamp"] == ""


def test_serialize_messages_uses_message_own_timestamp():
    human = HumanMessage(content="问题", id="h1", additional_kwargs={"timestamp": "2026-06-21 07:30:27"})
    ai = AIMessage(content="回答", id="a1", additional_kwargs={"timestamp": "2026-06-21 07:30:59"})
    tool = ToolMessage(
        content="工具结果",
        tool_call_id="tc1",
        name="tool_a",
        id="tool1",
        additional_kwargs={"timestamp": "2026-06-21 07:30:50"},
    )

    serialized = conversation_module._serialize_messages([human, ai, tool])

    assert serialized[0]["timestamp"] == "2026-06-21 07:30:27"
    assert serialized[1]["timestamp"] == "2026-06-21 07:30:59"
    assert serialized[2]["timestamp"] == "2026-06-21 07:30:50"


def test_conversation_serializer_object_tool_calls_and_stage_edges():
    class ToolCall:
        id = "obj_tc"
        name = "object_tool"
        args = {"x": 2}

    ai = AIMessage(content="回答", id="ai_obj")
    ai.tool_calls = [ToolCall()]
    semantic_selection = conversation_module._serialize_semantic_config_selection({
        "semantic_config_id": "cfg",
        "matched_metrics": "bad",
        "matched_dimensions": "bad",
    })
    stages = conversation_module._serialize_task_stages([
        {"run_id": "r1", "stage": "thinking", "status": "done", "title": 1, "content": None, "steps": "bad"},
        {"run_id": "r1", "stage": "plan", "status": "done", "steps": [1, " two ", ""]},
        {"run_id": "r1", "stage": "execute", "status": "done", "source": "reasoning"},
    ])

    serialized = conversation_module._serialize_messages([SimpleNamespace(type="unknown")])
    human_without_files = HumanMessage(content="无附件", id="h_no_file", additional_kwargs={"file_ids": []})
    human_without_file_payload = conversation_module._serialize_messages([human_without_files])[0]

    assert conversation_module._extract_timestamp(SimpleNamespace(additional_kwargs={})) == ""
    assert conversation_module._extract_timestamp(SimpleNamespace(additional_kwargs={"timestamp": ""})) == ""
    assert conversation_module._serialize_messages([ai])[0]["tool_calls"][0] == {"id": "obj_tc", "name": "object_tool", "args": {"x": 2}}
    assert "file_ids" not in human_without_file_payload
    assert semantic_selection["semantic_config_name"] == "cfg"
    assert semantic_selection["matched_metrics"] == []
    assert semantic_selection["matched_dimensions"] == []
    assert stages[0] == {"stage": "thinking", "status": "done"}
    assert stages[1]["steps"] == ["1", " two "]
    assert stages[2]["source"] == "reasoning"
    assert serialized == []


def test_conversation_reasoning_serialization_and_timestamp_branches():
    ai = AIMessage(
        content="回答",
        id="ai_reasoning",
        additional_kwargs={"reasoning_content": "思考过程", "timestamp": "t1"},
    )
    serialized_ai = conversation_module._serialize_messages([ai])[0]
    assert serialized_ai["reasoning_content"] == "思考过程"
    assert serialized_ai["timestamp"] == "t1"

    assert conversation_module._extract_reasoning_content(SimpleNamespace(additional_kwargs={}, response_metadata={})) == ""
    assert conversation_module._extract_reasoning_content(SimpleNamespace(response_metadata={"reasoning": "元数据思考"})) == "元数据思考"
    assert conversation_module._serialize_reasoning_segments("bad") == []
    assert conversation_module._serialize_reasoning_segments([
        "skip",
        {"content": ""},
        {"id": "", "content": "第一段", "toolCallCount": "2"},
        {"content": "第二段", "tool_call_count": "bad"},
        {"content": "第三段", "tool_call_count": -3},
    ]) == [
        {"id": "reasoning-history-2", "content": "第一段", "tool_call_count": 2},
        {"id": "reasoning-history-3", "content": "第二段", "tool_call_count": 0},
        {"id": "reasoning-history-4", "content": "第三段", "tool_call_count": 0},
    ]


def test_conversation_interrupt_info_branches():
    assert conversation_module._get_interrupt_info(None) is None
    assert conversation_module._get_interrupt_info(SimpleNamespace(tasks=[])) is None
    assert conversation_module._get_interrupt_info(SimpleNamespace(tasks=[SimpleNamespace(interrupts=[])])) is None
    assert conversation_module._get_interrupt_info(SimpleNamespace(tasks=[SimpleNamespace(interrupts=[object()])])) is None
    assert conversation_module._get_interrupt_info(SimpleNamespace(tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value="bad")])])) is None
    assert conversation_module._get_interrupt_info(SimpleNamespace(
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"x": 1}})])],
        values={"messages": []},
    )) == {
        "tool_name": "unknown_tool",
        "tool_call_id": None,
        "args": {"x": 1},
        "message": None,
    }

    state = SimpleNamespace(
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"sql": "select 1"}, "message": "确认执行"})])],
        values={"messages": [AIMessage(content="", tool_calls=[{"id": "tc1", "name": "semantic_query_tool", "args": {}}])]},
    )
    assert conversation_module._get_interrupt_info(state) == {
        "tool_name": "semantic_query_tool",
        "tool_call_id": "tc1",
        "args": {"sql": "select 1"},
        "message": "确认执行",
    }


@pytest.mark.asyncio
async def test_conversation_crud_and_list_branches(conversation_db):
    created = await conversation_module.create("", "agent_a", user_id="u1")
    conv_id = created["id"]

    assert await conversation_module.get_or_create_conversation("", "agent_a", conv_id, user_id="u1") == conv_id
    with pytest.raises(PermissionError):
        await conversation_module.get_or_create_conversation("", "agent_a", conv_id, user_id="u2")
    assert await conversation_module.get_or_create_conversation("", "agent_b", "conv_b", user_id="u1") == "conv_b"

    listed = await conversation_module.list("", "agent_a", user_id="u1", limit=10, offset=-1)
    assert [item["id"] for item in listed] == [conv_id]
    assert await conversation_module.list_by_agent_ids([], user_id="u1") == []
    listed_many = await conversation_module.list_by_agent_ids(["agent_a", "agent_b"], user_id="u1")
    assert {item["agent_id"] for item in listed_many} == {"agent_a", "agent_b"}

    all_rows = await conversation_module.list_all({"agent_a": {}, "agent_b": {}}, user_id="u1", limit=5)
    assert len(all_rows) == 2

    assert await conversation_module.get_agent_id("", conv_id, user_id="u1") == "agent_a"
    assert await conversation_module.update_title("", conv_id, "新标题", user_id="u1") is True
    assert await conversation_module.update_title("", conv_id, "无权", user_id="u2") is False
    assert await conversation_module.toggle_favorite("", conv_id, user_id="u1") is True
    assert await conversation_module.toggle_favorite("", conv_id, user_id="u1") is False
    assert await conversation_module.toggle_favorite("", "missing", user_id="u1") is None
    assert await conversation_module.delete("", conv_id, user_id="u2") is False
    assert await conversation_module.delete("", conv_id, user_id="u1") is True
    assert await conversation_module.delete("", conv_id, user_id="u1") is False


@pytest.mark.asyncio
async def test_conversation_additional_db_branches(conversation_db, monkeypatch):
    await conversation_module.sync_thread_to_db("u1", "conv_existing", "agent_a", title="原始标题", is_new=True)
    await conversation_module.sync_thread_to_db("u1", "conv_existing", "agent_a", title="不会重复创建", is_new=True)
    await conversation_module.sync_thread_to_db("", "conv_system", "agent_a", is_new=True)

    listed_without_user = await conversation_module.list("", "agent_a", user_id=None, limit=10, offset=0)
    assert {item["id"] for item in listed_without_user} >= {"conv_existing", "conv_system"}

    listed_by_agents_without_user = await conversation_module.list_by_agent_ids(["agent_a"], user_id=None, limit=10, offset=-10)
    assert {item["id"] for item in listed_by_agents_without_user} >= {"conv_existing", "conv_system"}

    original_list = conversation_module.list

    async def sometimes_fails(db_path, agent_id, user_id=None, limit=50, offset=0):
        if agent_id == "bad_agent":
            raise RuntimeError("list failed")
        return await original_list(db_path, agent_id, user_id, limit, offset)

    monkeypatch.setattr(conversation_module, "list", sometimes_fails)
    rows = await conversation_module.list_all({"bad_agent": {}, "agent_a": {}}, user_id=None, limit=10)
    assert any(item["id"] == "conv_existing" for item in rows)

    assert await conversation_module.delete("", "conv_system", user_id=None) is True
    assert await conversation_module.update_title("", "conv_existing", "无用户过滤标题", user_id=None) is True
    assert await conversation_module.toggle_favorite("", "conv_existing", user_id=None) in {True, False}
    assert await conversation_module.get_agent_id("", "conv_existing", user_id=None) == "agent_a"


@pytest.mark.asyncio
async def test_conversation_sync_get_detail_and_generate_title(conversation_db, monkeypatch):
    await conversation_module.sync_thread_to_db("u1", "conv_sync", "agent_a", title="同步标题", is_new=True)
    await conversation_module.sync_thread_to_db("u1", "conv_sync", "agent_a", title="更新标题", is_new=False)
    await conversation_module.sync_thread_to_db("u1", "conv_sync", "agent_a", is_new=False)

    detail = await conversation_module.get_detail("", "conv_sync", None, None, user_id="u1")
    assert detail["title"] == "更新标题"
    assert detail["messages"] == []
    assert detail["pending_tool"] is None

    assert await conversation_module.generate_title("", "missing", None, None, user_id="u1") is None
    assert await conversation_module.generate_title("", "conv_sync", None, None, user_id="u1") == "更新标题"

    await conversation_module.update_title("", "conv_sync", "未命名会话", user_id="u1")
    calls = []

    async def fake_update_title(db_path, conversation_id, title, user_id=None):
        calls.append((conversation_id, title, user_id))
        return True

    monkeypatch.setattr(conversation_module, "update_title", fake_update_title)
    assert await conversation_module.generate_title("", "conv_sync", None, None, user_id="u1") == "智能分析会话"
    assert calls == [("conv_sync", "智能分析会话", "u1")]


@pytest.mark.asyncio
async def test_conversation_get_detail_with_graph_state_and_missing_thread(conversation_db):
    await conversation_module.sync_thread_to_db("u1", "conv_detail", "agent_a", title="详情标题", is_new=True)

    class CheckpointerContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        HumanMessage(content="问题", id="h1", additional_kwargs={"timestamp": "t1"}),
                        AIMessage(content="回答", id="a1"),
                    ],
                    "task_stages": [
                        {"run_id": "run1", "stage": "thinking", "status": "completed", "content": "理解"}
                    ],
                    "token_usage": {"used": 12, "total": 100},
                },
                tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value={"args": {"sql": "select 1"}, "message": "确认"})])],
            )

    detail = await conversation_module.get_detail("", "conv_detail", lambda _cp: Graph(), lambda: CheckpointerContext(), user_id="u1")

    assert detail["title"] == "详情标题"
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]
    assert detail["task_stages"][0]["stage"] == "thinking"
    assert detail["reasoning_segments"] == []
    assert detail["token_usage"] == {"used": 12, "total": 100}
    assert detail["pending_tool"]["args"] == {"sql": "select 1"}
    assert await conversation_module.get_detail("", "missing", lambda _cp: Graph(), lambda: CheckpointerContext(), user_id="u1") is None


@pytest.mark.asyncio
async def test_conversation_get_detail_with_empty_graph_state(conversation_db):
    await conversation_module.sync_thread_to_db("u1", "conv_empty_graph", "agent_a", title="空状态", is_new=True)

    class CheckpointerContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={})

    detail = await conversation_module.get_detail("", "conv_empty_graph", lambda _cp: Graph(), lambda: CheckpointerContext(), user_id="u1")

    assert detail["title"] == "空状态"
    assert detail["messages"] == []
    assert detail["task_stages"] == []
    assert detail["reasoning_segments"] == []
    assert detail["token_usage"] is None
    assert detail["pending_tool"] is None


@pytest.mark.asyncio
async def test_conversation_generate_title_with_graph_branches(conversation_db, monkeypatch):
    await conversation_module.sync_thread_to_db("u1", "conv_graph", "agent_a", is_new=True)

    class CheckpointerContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class Graph:
        def __init__(self, state):
            self.state = state

        async def aget_state(self, _config):
            return self.state

    assert await conversation_module.generate_title("", "conv_graph", lambda _cp: Graph(None), lambda: CheckpointerContext(), user_id="u1") is None
    empty_state = SimpleNamespace(values={"messages": []})
    assert await conversation_module.generate_title("", "conv_graph", lambda _cp: Graph(empty_state), lambda: CheckpointerContext(), user_id="u1") is None
    no_human_state = SimpleNamespace(values={"messages": [AIMessage(content="hi")]})
    assert await conversation_module.generate_title("", "conv_graph", lambda _cp: Graph(no_human_state), lambda: CheckpointerContext(), user_id="u1") is None

    class FakeLLMResult:
        content = "《订单销售趋势分析!!!》"

    class FakeChain:
        async def ainvoke(self, payload):
            assert payload["query"] == "请分析订单销售趋势"
            return FakeLLMResult()

    class FakePrompt:
        def __or__(self, _llm):
            return FakeChain()

    monkeypatch.setattr(conversation_module, "_get_title_llm", lambda: object())
    monkeypatch.setattr(conversation_module.ChatPromptTemplate, "from_messages", lambda _messages: FakePrompt())
    human_state = SimpleNamespace(values={"messages": [HumanMessage(content="请分析订单销售趋势")]})
    assert await conversation_module.generate_title("", "conv_graph", lambda _cp: Graph(human_state), lambda: CheckpointerContext(), user_id="u1") == "订单销售趋势分析"

    class BadChain:
        async def ainvoke(self, _payload):
            raise RuntimeError("llm down")

    class BadPrompt:
        def __or__(self, _llm):
            return BadChain()

    monkeypatch.setattr(conversation_module.ChatPromptTemplate, "from_messages", lambda _messages: BadPrompt())
    assert await conversation_module.generate_title("", "conv_graph", lambda _cp: Graph(human_state), lambda: CheckpointerContext(), user_id="u1") == "请分析订单销售趋势"
