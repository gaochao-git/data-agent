import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.chat_data_app.daos.conversation_dao import ConversationDao
from apps.chat_data_app.daos.conversation_event_dao import ConversationEventDao
from apps.chat_data_app.services import conversation_service as conversation_module
from utils.models import Base, UserThread


async def seed_conversation(
    conversation_id: str, *, title: str | None = None, user_id: str = "u1", agent_id: str = "agent_a"
) -> None:
    """造数工具：走生产 create_thread 链路创建会话记录。"""
    await conversation_module.conversation_dao.create_thread(
        user_id=user_id or "system",
        thread_id=conversation_id,
        agent_id=agent_id,
        title=title,
        now=datetime.now(),
    )


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
    assert (
        conversation_module._format_thread(SimpleNamespace(**{**thread.__dict__, "datasource_id": None}))[
            "datasource_id"
        ]
        == ""
    )
    assert (
        conversation_module._format_thread(
            SimpleNamespace(**{**thread.__dict__, "create_time": None, "update_time": None, "is_favorited": None})
        )["is_favorited"]
        is False
    )
    assert conversation_module._clean_title("《销售额分析!!!》") == "销售额分析"
    assert conversation_module._clean_title("!!") == ""
    assert conversation_module._clean_title("Here's a thinking process:") == ""


@pytest.mark.asyncio
async def test_conversation_crud_and_list_branches(conversation_db):
    created = await conversation_module.create("agent_a", user_id="u1")
    conv_id = created["id"]

    assert await conversation_module.get_or_create_conversation("agent_a", conv_id, user_id="u1") == conv_id
    with pytest.raises(PermissionError):
        await conversation_module.get_or_create_conversation("agent_a", conv_id, user_id="u2")
    assert await conversation_module.get_or_create_conversation("agent_b", "conv_b", user_id="u1") == "conv_b"

    listed = await conversation_module.list_conversations("agent_a", user_id="u1", limit=10, offset=-1)
    assert [item["id"] for item in listed] == [conv_id]
    assert await conversation_module.list_by_agent_ids([], user_id="u1") == []
    listed_many = await conversation_module.list_by_agent_ids(["agent_a", "agent_b"], user_id="u1")
    assert {item["agent_id"] for item in listed_many} == {"agent_a", "agent_b"}

    all_rows = await conversation_module.list_all({"agent_a": {}, "agent_b": {}}, user_id="u1", limit=5)
    assert len(all_rows) == 2

    assert await conversation_module.get_agent_id(conv_id, user_id="u1") == "agent_a"
    assert await conversation_module.update_title(conv_id, "新标题", user_id="u1") is True
    assert await conversation_module.update_title(conv_id, "无权", user_id="u2") is False
    assert await conversation_module.toggle_favorite(conv_id, user_id="u1") is True
    assert await conversation_module.toggle_favorite(conv_id, user_id="u1") is False
    assert await conversation_module.toggle_favorite("missing", user_id="u1") is None
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
        await seed_conversation(conversation_id, title=conversation_id)
        if datasource_id:
            await conversation_module.resolve_datasource_binding(conversation_id, "u1", datasource_id)
    await conversation_module.toggle_favorite("favorite", user_id="u1")
    await seed_conversation("other-user", title="other", user_id="u2")
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

    await seed_conversation("ds-a-latest", title="latest")
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
            "agent_a",
            "u1",
            1,
            group_key="datasource:ds_a",
            page=2,
            cursor="invalid",
        )


@pytest.mark.asyncio
async def test_conversation_additional_db_branches(conversation_db, monkeypatch):
    await seed_conversation("conv_existing", title="原始标题")
    await seed_conversation("conv_system", user_id="")

    listed_without_user = await conversation_module.list_conversations("agent_a", user_id=None, limit=10, offset=0)
    assert {item["id"] for item in listed_without_user} >= {"conv_existing", "conv_system"}

    listed_by_agents_without_user = await conversation_module.list_by_agent_ids(
        ["agent_a"], user_id=None, limit=10, offset=-10
    )
    assert {item["id"] for item in listed_by_agents_without_user} >= {"conv_existing", "conv_system"}

    original_list = conversation_module.list_conversations

    async def sometimes_fails(agent_id, user_id=None, limit=50, offset=0):
        if agent_id == "bad_agent":
            raise RuntimeError("list failed")
        return await original_list(agent_id, user_id, limit, offset)

    monkeypatch.setattr(conversation_module, "list_conversations", sometimes_fails)
    rows = await conversation_module.list_all({"bad_agent": {}, "agent_a": {}}, user_id=None, limit=10)
    assert any(item["id"] == "conv_existing" for item in rows)

    assert await conversation_module.update_title("conv_existing", "无用户过滤标题", user_id=None) is True
    assert await conversation_module.toggle_favorite("conv_existing", user_id=None) in {True, False}
    assert await conversation_module.get_agent_id("conv_existing", user_id=None) == "agent_a"


@pytest.mark.asyncio
async def test_conversation_sync_get_detail_and_generate_title(conversation_db, monkeypatch):
    await seed_conversation("conv_sync", title="同步标题")
    await conversation_module.update_title("conv_sync", "更新标题", user_id="u1")

    detail = await conversation_module.get_detail("conv_sync", "u1")
    assert detail["title"] == "更新标题"
    assert detail["messages"] == []

    assert await conversation_module.generate_title("missing", "u1") is None
    assert await conversation_module.generate_title("conv_sync", "u1") == "更新标题"

    await conversation_module.update_title("conv_sync", "未命名会话", user_id="u1")
    calls = []

    async def fake_update_title(conversation_id, title, user_id=None):
        calls.append((conversation_id, title, user_id))
        return True

    monkeypatch.setattr(conversation_module, "update_title", fake_update_title)
    assert await conversation_module.generate_title("conv_sync", "u1") is None
    assert calls == []


@pytest.mark.asyncio
async def test_conversation_get_detail_reads_event_store_without_checkpoint(conversation_db):
    await seed_conversation("conv_detail", title="详情标题")
    await conversation_module.resolve_datasource_binding("conv_detail", "u1", "ds_a")
    user_message = HumanMessage(content="问题", id="h1", additional_kwargs={"timestamp": "t1"})
    token_usage = {
        "used": 12,
        "total": 100,
        "breakdown": [
            {
                "key": "system_prompt",
                "content": "[系统提示词已隐藏]",
            }
        ],
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
    assert detail["token_usage"]["breakdown"] == token_usage["breakdown"]
    assert detail["datasource_id"] == "ds_a"
    assert await conversation_module.get_detail("missing", "u1") is None


@pytest.mark.asyncio
async def test_conversation_get_detail_with_empty_event_history(conversation_db):
    await seed_conversation("conv_empty_graph", title="空状态")
    detail = await conversation_module.get_detail("conv_empty_graph", "u1")

    assert detail["title"] == "空状态"
    assert detail["messages"] == []
    assert "task_stages" not in detail
    assert detail["reasoning_segments"] == []
    assert detail["token_usage"] is None


@pytest.mark.asyncio
async def test_conversation_generate_title_from_event_history(conversation_db, monkeypatch):
    await seed_conversation("conv_graph")
    assert await conversation_module.generate_title("conv_graph", "u1") is None
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
    assert await conversation_module.generate_title("conv_graph", "u1") == "订单销售趋势分析"

    class BadChain:
        async def ainvoke(self, _payload):
            raise RuntimeError("llm down")

    class BadPrompt:
        def __or__(self, _llm):
            return BadChain()

    monkeypatch.setattr(conversation_module.ChatPromptTemplate, "from_messages", lambda _messages: BadPrompt())
    await conversation_module.update_title("conv_graph", "未命名会话", "u1")
    # 命名失败不写降级标题：异常上抛且标题保持占位符，下一轮可重试
    with pytest.raises(RuntimeError, match="llm down"):
        await conversation_module.generate_title("conv_graph", "u1")
    thread = await conversation_module.conversation_dao.get_thread("conv_graph", "u1")
    assert thread.thread_title == "未命名会话"


@pytest.mark.asyncio
async def test_dao_group_first_pages_without_window_functions():
    """list_thread_group_first_pages 在无窗口函数（MySQL 5.7 兼容）实现下保持组序/组内序/每组截断。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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

    dao = ConversationDao(session_context=lambda: fake_get_session())

    def thread(conversation_id, update_time, *, datasource_id=None, favorited=0):
        return UserThread(
            user_id="u1",
            thread_id=conversation_id,
            thread_title=conversation_id,
            agent_id="agent_a",
            datasource_id=datasource_id,
            is_favorited=favorited,
            delete_flag=0,
            create_time=update_time,
            update_time=update_time,
        )

    t1 = datetime(2026, 8, 1, 10, 0, 0)
    t2 = datetime(2026, 8, 2, 10, 0, 0)
    t3 = datetime(2026, 8, 3, 10, 0, 0)
    t4 = datetime(2026, 8, 4, 10, 0, 0)
    t5 = datetime(2026, 8, 5, 10, 0, 0)
    t6 = datetime(2026, 8, 6, 10, 0, 0)
    t7 = datetime(2026, 8, 7, 10, 0, 0)
    t8 = datetime(2026, 8, 8, 10, 0, 0)
    rows = [
        thread("pin_new", t5, datasource_id="ds_a", favorited=1),
        thread("pin_old", t1, datasource_id="ds_b", favorited=1),
        # ds_a 有 3 条，limit_per_group=2 应截掉最旧的 a_old
        thread("a_new", t6, datasource_id="ds_a"),
        thread("a_mid", t3, datasource_id="ds_a"),
        thread("a_old", t2, datasource_id="ds_a"),
        thread("b_new", t7, datasource_id="ds_b"),
        thread("b_old", t4, datasource_id="ds_b"),
        thread("none_new", t8, datasource_id=""),
        thread("none_old", t2, datasource_id=""),
        # 带首尾空格的数据源应独立成组（trim 语义，与 ds_c 等价）
        thread("c_padded", t1, datasource_id=" ds_c "),
    ]
    async with session_factory() as session:
        session.add_all(rows)
        await session.commit()

    result = await dao.list_thread_group_first_pages(agent_id="agent_a", user_id="u1", limit_per_group=2)

    # 置顶组在前（组内新在前）；其余各组按组内最新会话时间倒序交错：
    # unclassified(none_new t8) > ds_b(b_new t7) > ds_a(a_new t6，a_old 被截断) > ds_c(c_padded t1)
    assert [row.thread_id for row in result] == [
        "pin_new",
        "pin_old",
        "none_new",
        "b_new",
        "a_new",
        "b_old",
        "a_mid",
        "none_old",
        "c_padded",
    ]

    await engine.dispose()


def test_llm_model_id_helper_falls_back():
    """_llm_model_id 按 model_name > model > unknown 取值，容忍构造失败的 None。"""
    assert conversation_module._llm_model_id(None) == "unknown"
    assert conversation_module._llm_model_id(SimpleNamespace(model_name="m1")) == "m1"
    assert conversation_module._llm_model_id(SimpleNamespace(model="m2")) == "m2"
    assert conversation_module._llm_model_id(SimpleNamespace()) == "unknown"


@pytest.mark.asyncio
async def test_conversation_title_failure_alarm_carries_model_id(conversation_db, monkeypatch):
    """标题生成连接失败时，告警 ext1 须带 model_id，与主链路告警字段对齐。"""
    await seed_conversation("conv_title_alarm")
    await conversation_module.conversation_event_service.persist_user_message(
        conversation_id="conv_title_alarm",
        user_id="u1",
        operator_name="用户一",
        message=HumanMessage(content="请分析订单销售趋势", id="title-alarm-turn"),
    )

    class TitleLLM:
        model_name = "qwen3.6-35b-a3b"

    class BadChain:
        async def ainvoke(self, _payload):
            raise RuntimeError("Connection error.")

    class BadPrompt:
        def __or__(self, _llm):
            return BadChain()

    captured: dict = {}

    def fake_alarm(_exc, *, ext1=None, ext2=None):
        captured["ext1"] = ext1
        return True

    monkeypatch.setattr(conversation_module, "_get_title_llm", lambda: TitleLLM())
    monkeypatch.setattr(conversation_module.ChatPromptTemplate, "from_messages", lambda _messages: BadPrompt())
    monkeypatch.setattr(conversation_module, "emit_llm_connection_alarm_if_needed", fake_alarm)

    # 告警之后异常上抛，不落降级标题
    with pytest.raises(RuntimeError, match="Connection error."):
        await conversation_module.generate_title("conv_title_alarm", "u1")
    assert captured["ext1"] == {
        "purpose": "conversation_title",
        "conversation_id": "conv_title_alarm",
        "model_id": "qwen3.6-35b-a3b",
    }


def test_title_llm_disables_thinking_and_skips_reasoning_blocks(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        conversation_module,
        "get_chat_data_llm",
        lambda **kwargs: calls.update(kwargs) or object(),
    )
    conversation_module._get_title_llm()
    assert calls["enable_thinking"] is False
    assert calls["max_tokens"] == 2048

    response = AIMessage(
        content=[
            {"type": "thinking", "thinking": "Here's a thinking process:"},
            {"type": "text", "text": "订单经营趋势分析"},
        ]
    )
    assert conversation_module._title_response_text(response) == "订单经营趋势分析"


def test_title_response_strips_inline_think_tags():
    """网关未配 reasoning parser 时思考以内联 <think> 混在 content 字符串里，须剥除。"""
    inline = "<think>用户想查攻击记录，先查表。</think>IP攻击来源分析"
    response = AIMessage(content=inline)
    assert conversation_module._title_response_text(response) == "IP攻击来源分析"
    # 无闭合标签 = 思考被截断，标签起的后续内容全部丢弃
    truncated = AIMessage(content="标题前的正文<think>被截断的思考")
    assert conversation_module._title_response_text(truncated) == "标题前的正文"


@pytest.mark.asyncio
async def test_conversation_generate_title_unusable_output_keeps_placeholder(conversation_db, monkeypatch):
    """LLM 调用成功但产物不可用（思考泄漏/过短）：不写降级标题，保持占位符待重试。"""
    await seed_conversation("conv_title_retry")
    await conversation_module.conversation_event_service.persist_user_message(
        conversation_id="conv_title_retry",
        user_id="u1",
        operator_name="用户一",
        message=HumanMessage(content="62.234.152.217这个IP最近有攻击吗", id="title-turn"),
    )

    class ShortResult:
        content = "!!"

    contents = {"value": type("R", (), {"content": "Here's a thinking process: user asks about IP"})()}

    class Chain:
        async def ainvoke(self, _payload):
            return contents["value"]

    class Prompt:
        def __or__(self, _llm):
            return Chain()

    monkeypatch.setattr(conversation_module, "_get_title_llm", lambda: object())
    monkeypatch.setattr(conversation_module.ChatPromptTemplate, "from_messages", lambda _messages: Prompt())

    # 推理泄漏 → 返回空串且不落库，占位符保持（下一轮 Run 终态可重试）
    assert await conversation_module.generate_title("conv_title_retry", "u1") == ""
    thread = await conversation_module.conversation_dao.get_thread("conv_title_retry", "u1")
    assert thread.thread_title == "未命名会话"

    # 过短产物同样不落库
    contents["value"] = ShortResult()
    assert await conversation_module.generate_title("conv_title_retry", "u1") == ""
    thread = await conversation_module.conversation_dao.get_thread("conv_title_retry", "u1")
    assert thread.thread_title == "未命名会话"

    # 重试成功后正常写入
    contents["value"] = type("R", (), {"content": "IP攻击来源分析咨询"})()
    assert await conversation_module.generate_title("conv_title_retry", "u1") == "IP攻击来源分析咨询"
    thread = await conversation_module.conversation_dao.get_thread("conv_title_retry", "u1")
    assert thread.thread_title == "IP攻击来源分析咨询"
