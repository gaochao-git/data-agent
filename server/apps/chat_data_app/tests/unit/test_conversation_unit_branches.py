import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from utils import conversation as conversation_module
from utils.models import Base
from apps.chat_data_app.daos.conversation_event_dao import ConversationEventDao


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
    monkeypatch.setattr(
        conversation_module.conversation_event_service,
        "_dao",
        ConversationEventDao(session_context=lambda: fake_get_session()),
    )
    yield
    asyncio.run(engine.dispose())


def test_conversation_formatting_helpers():
    now = datetime(2026, 1, 2, 3, 4, 5)
    thread = SimpleNamespace(
        thread_id="conv_a",
        thread_title="标题",
        agent_id="agent_a",
        datasource_id="ds_a",
        create_time=now,
        update_time=now,
        is_favorited=1,
    )
    assert conversation_module._format_thread(thread) == {
        "id": "conv_a",
        "title": "标题",
        "agent_id": "agent_a",
        "datasource_id": "ds_a",
        "create_time": "2026-01-02 03:04:05",
        "update_time": "2026-01-02 03:04:05",
        "is_favorited": True,
        "run_status": None,
    }
    assert conversation_module._format_thread(SimpleNamespace(**{**thread.__dict__, "datasource_id": None}))["datasource_id"] == ""
    assert conversation_module._format_thread(SimpleNamespace(**{**thread.__dict__, "create_time": None, "update_time": None, "is_favorited": None}))["is_favorited"] is False
    assert conversation_module._clean_title("《销售额分析!!!》", fallback="fallback") == "销售额分析"
    assert conversation_module._clean_title("!!", fallback="fallback-title") == "fallback-title"


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
    assert await conversation_module.get_datasource_id(conv_id, "u1") is None
    assert await conversation_module.resolve_datasource_binding(conv_id, "u1", "ds_a") == "ds_a"
    assert await conversation_module.get_datasource_id(conv_id, "u1") == "ds_a"
    assert await conversation_module.resolve_datasource_binding(conv_id, "u1", "ds_b") == "ds_a"
    assert await conversation_module.resolve_datasource_binding("missing", "u1", "ds_a") is None


@pytest.mark.asyncio
async def test_grouped_conversations_return_every_group_first_page_without_counts(conversation_db):
    for conversation_id, datasource_id in [
        ("ds-a-new", "ds_a"),
        ("ds-a-old", "ds_a"),
        ("ds-b", "ds_b"),
        ("unclassified", ""),
        ("favorite", "ds_a"),
    ]:
        await conversation_module.sync_thread_to_db("u1", conversation_id, "agent_a", title=conversation_id, is_new=True)
        if datasource_id:
            await conversation_module.resolve_datasource_binding(conversation_id, "u1", datasource_id)
    await conversation_module.toggle_favorite("", "favorite", user_id="u1")
    await conversation_module.sync_thread_to_db("u2", "other-user", "agent_a", title="other", is_new=True)
    await conversation_module.resolve_datasource_binding("other-user", "u2", "ds_private")

    result = await conversation_module.list_grouped("agent_a", "u1", page_size=1)
    groups = {group["key"]: group for group in result["groups"]}

    assert set(groups) == {
        "system:pinned",
        "datasource:ds_a",
        "datasource:ds_b",
        "system:unclassified",
    }
    assert groups["system:pinned"]["conversations"][0]["id"] == "favorite"
    assert groups["datasource:ds_a"]["conversations"][0]["id"] != "favorite"
    assert groups["datasource:ds_a"]["has_more"] is True
    assert groups["datasource:ds_a"]["next_cursor"]
    assert groups["datasource:ds_b"]["has_more"] is False
    assert "total" not in groups["datasource:ds_a"]
    assert all(item["id"] != "other-user" for group in groups.values() for item in group["conversations"])

    await conversation_module.sync_thread_to_db("u1", "ds-a-latest", "agent_a", title="latest", is_new=True)
    await conversation_module.resolve_datasource_binding("ds-a-latest", "u1", "ds_a")
    next_page = await conversation_module.list_grouped(
        "agent_a",
        "u1",
        page_size=1,
        group_key="datasource:ds_a",
        page=2,
        cursor=groups["datasource:ds_a"]["next_cursor"],
    )
    assert next_page["groups"][0]["page"] == 2
    assert next_page["groups"][0]["has_more"] is False
    assert next_page["groups"][0]["conversations"][0]["id"] != "ds-a-latest"
    assert next_page["groups"][0]["conversations"][0]["id"] != groups["datasource:ds_a"]["conversations"][0]["id"]

    with pytest.raises(ValueError, match="无效的会话分组"):
        await conversation_module.list_grouped("agent_a", "u1", 1, group_key="datasource:")
    with pytest.raises(ValueError, match="缺少游标"):
        await conversation_module.list_grouped("agent_a", "u1", 1, group_key="datasource:ds_a", page=2)
    with pytest.raises(ValueError, match="无效的会话分页游标"):
        await conversation_module.list_grouped(
            "agent_a", "u1", 1, group_key="datasource:ds_a", page=2, cursor="invalid",
        )


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

    assert await conversation_module.update_title("", "conv_existing", "无用户过滤标题", user_id=None) is True
    assert await conversation_module.toggle_favorite("", "conv_existing", user_id=None) in {True, False}
    assert await conversation_module.get_agent_id("", "conv_existing", user_id=None) == "agent_a"


@pytest.mark.asyncio
async def test_conversation_sync_get_detail_and_generate_title(conversation_db, monkeypatch):
    await conversation_module.sync_thread_to_db("u1", "conv_sync", "agent_a", title="同步标题", is_new=True)
    await conversation_module.sync_thread_to_db("u1", "conv_sync", "agent_a", title="更新标题", is_new=False)
    await conversation_module.sync_thread_to_db("u1", "conv_sync", "agent_a", is_new=False)

    detail = await conversation_module.get_detail("conv_sync", "u1")
    assert detail["title"] == "更新标题"
    assert detail["messages"] == []

    assert await conversation_module.generate_title("", "missing", "u1") is None
    assert await conversation_module.generate_title("", "conv_sync", "u1") == "更新标题"

    await conversation_module.update_title("", "conv_sync", "未命名会话", user_id="u1")
    calls = []

    async def fake_update_title(db_path, conversation_id, title, user_id=None):
        calls.append((conversation_id, title, user_id))
        return True

    monkeypatch.setattr(conversation_module, "update_title", fake_update_title)
    assert await conversation_module.generate_title("", "conv_sync", "u1") is None
    assert calls == []


@pytest.mark.asyncio
async def test_conversation_get_detail_reads_event_store_without_checkpoint(conversation_db):
    await conversation_module.sync_thread_to_db("u1", "conv_detail", "agent_a", title="详情标题", is_new=True)
    await conversation_module.resolve_datasource_binding("conv_detail", "u1", "ds_a")
    user_message = HumanMessage(content="问题", id="h1", additional_kwargs={"timestamp": "t1"})
    token_usage = {
        "used": 12,
        "total": 100,
        "breakdown": [{
            "key": "system_prompt",
            "content": "[系统提示词已隐藏]",
        }],
    }
    await conversation_module.conversation_event_service.persist_turn(
        conversation_id="conv_detail",
        user_id="u1",
        operator_name="用户一",
        turn_id="h1",
        messages=[user_message, AIMessage(content="回答", id="a1")],
        reasoning_segments=[],
        token_usage=token_usage,
    )

    detail = await conversation_module.get_detail("conv_detail", "u1")

    assert detail["title"] == "详情标题"
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]
    assert [event["event_type"] for event in detail["history_events"]] == ["user_message", "assistant_message"]
    assert "task_stages" not in detail
    assert detail["reasoning_segments"] == []
    assert detail["token_usage"]["used"] == 12
    assert "breakdown" not in detail["token_usage"]
    assert detail["datasource_id"] == "ds_a"
    assert await conversation_module.get_detail("missing", "u1") is None


@pytest.mark.asyncio
async def test_conversation_get_detail_with_empty_event_history(conversation_db):
    await conversation_module.sync_thread_to_db("u1", "conv_empty_graph", "agent_a", title="空状态", is_new=True)
    detail = await conversation_module.get_detail("conv_empty_graph", "u1")

    assert detail["title"] == "空状态"
    assert detail["messages"] == []
    assert "task_stages" not in detail
    assert detail["reasoning_segments"] == []
    assert detail["token_usage"] is None


@pytest.mark.asyncio
async def test_conversation_generate_title_from_event_history(conversation_db, monkeypatch):
    await conversation_module.sync_thread_to_db("u1", "conv_graph", "agent_a", is_new=True)
    assert await conversation_module.generate_title("", "conv_graph", "u1") is None
    await conversation_module.conversation_event_service.persist_user_message(
        conversation_id="conv_graph",
        user_id="u1",
        operator_name="用户一",
        message=HumanMessage(content="请分析订单销售趋势", id="title-turn"),
    )

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
    assert await conversation_module.generate_title("", "conv_graph", "u1") == "订单销售趋势分析"

    class BadChain:
        async def ainvoke(self, _payload):
            raise RuntimeError("llm down")

    class BadPrompt:
        def __or__(self, _llm):
            return BadChain()

    monkeypatch.setattr(conversation_module.ChatPromptTemplate, "from_messages", lambda _messages: BadPrompt())
    await conversation_module.update_title("", "conv_graph", "未命名会话", "u1")
    assert await conversation_module.generate_title("", "conv_graph", "u1") == "请分析订单销售趋势"
