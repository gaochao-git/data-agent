"""agent_stream 流式链路测试共享 stub。

_FakeGraph / _FakeMessageChunk / 收集器由 test_agent_stream.py 与
test_quoted_snippets_stream.py 共用（pytest importlib 模式 + pytest.ini
pythonpath=.，经 apps.chat_data_app.tests.unit 命名空间包导入）。
非 test_* 命名，pytest 不会收集本文件。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
os.environ.setdefault("FXCJG_DB_HOST", "127.0.0.1:3306")
os.environ.setdefault("FXCJG_DB", "fxcjg_test")
os.environ.setdefault("FXCJG_DB_USER", "fxcjg_test")

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

from langchain_core.messages import AIMessageChunk, HumanMessageChunk, ToolMessageChunk

from apps.chat_data_app.agent_stream import AgentRunStreamFailedError
from apps.chat_data_app.services.semantic.run.semantic_conversation_context import SemanticConversationContext


def _FakeMessageChunk(
    *,
    content="",
    type_="ai",
    tool_calls=None,
    tool_call_chunks=None,
    tool_call_id="",
    name="",
):
    """测试工厂：返回真实 LangChain 消息 Chunk，不伪造 type 字段。"""
    if type_ == "human":
        return HumanMessageChunk(content=content)
    if type_ == "tool":
        return ToolMessageChunk(content=content, tool_call_id=tool_call_id, name=name or None)
    normalized_tool_calls = [{**tool_call, "args": tool_call.get("args") or {}} for tool_call in tool_calls or []]
    return AIMessageChunk(
        content=content,
        tool_calls=normalized_tool_calls,
        tool_call_chunks=tool_call_chunks or [],
    )


class _FakeGraph:
    def __init__(self, events=None, state=None, raise_on_stream=False):
        self.events = events or []
        self.state = state or SimpleNamespace(tasks=[], values={})
        self.raise_on_stream = raise_on_stream
        self.initial_state = None

    async def aget_state(self, config):
        return self.state

    async def astream(self, initial_state, config=None, stream_mode=None):
        self.initial_state = initial_state
        if self.raise_on_stream:
            raise RuntimeError("graph failed")
        for event in self.events:
            yield event


@asynccontextmanager
async def _fake_checkpointer():
    yield object()


async def _collect_async_iter(async_iterable):
    return [item async for item in async_iterable]


async def _collect_async_iter_until_failure(async_iterable):
    """收集事件并捕获 AgentRunStreamFailedError，返回 (events, failure)。

    failure 非 None 表示生成器按契约把失败异常抛给了调用方（Runtime）。
    """
    events = []
    failure = None
    try:
        async for item in async_iterable:
            events.append(item)
    except AgentRunStreamFailedError as exc:
        failure = exc
    return events, failure


def _semantic_context_from_request(**kwargs) -> SemanticConversationContext:
    datasource_id = str(kwargs.get("datasource_id") or "").strip() or None
    semantic_config_id = str(kwargs.get("semantic_config_id") or "").strip() or None
    selected_ids = list(
        dict.fromkeys(
            str(member_id).strip() for member_id in kwargs.get("member_datasource_ids") or [] if str(member_id).strip()
        )
    )
    semantic_config = (
        {"id": semantic_config_id, "name": semantic_config_id, "metrics": [], "dimensions": []}
        if semantic_config_id
        else None
    )
    targets = [
        {"member_datasource_id": member_id, "member_name": member_id, "org_code": member_id}
        for member_id in selected_ids
    ]
    query = str(kwargs.get("user_query") or "")
    matched_ids = selected_ids if "所有机构" in query or "全部机构" in query else []
    return SemanticConversationContext(
        datasource_id=datasource_id,
        datasource_meta={"query_context": ""} if datasource_id else None,
        semantic_config_id=semantic_config_id,
        semantic_config=semantic_config,
        execution_targets=targets,
        available_member_datasource_ids=selected_ids,
        matched_member_datasource_ids=matched_ids,
        selected_member_datasource_ids=selected_ids,
    )


def _override_semantic_context(monkeypatch, **overrides):
    async def prepare_context(**kwargs):
        context = _semantic_context_from_request(**kwargs)
        values = {key: value(context, kwargs) if callable(value) else value for key, value in overrides.items()}
        return replace(context, **values)

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prepare_semantic_conversation_context",
        prepare_context,
    )


@pytest.fixture(autouse=True)
def _stub_semantic_conversation_context(monkeypatch):
    async def noop_event_write(**_kwargs):
        return None

    async def prepare_context(**kwargs):
        return _semantic_context_from_request(**kwargs)

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prepare_semantic_conversation_context",
        prepare_context,
    )

    async def resolve_datasource_binding(_conversation_id, _user_id, requested_datasource_id):
        return requested_datasource_id

    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.conversation.resolve_datasource_binding",
        resolve_datasource_binding,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.conversation_event_service.persist_user_message",
        noop_event_write,
    )
    monkeypatch.setattr(
        "apps.chat_data_app.agent_stream.conversation_event_service.persist_turn",
        noop_event_write,
    )

    # 预物化不是这些用例的关注点：默认桩为“本轮无模型需物化”，
    # 需要验证物化行为的用例自行覆盖。
    async def noop_prematerialize(**_kwargs):
        return {"total": 0, "failed_ids": []}

    monkeypatch.setattr(
        "apps.chat_data_app.agent_run_support.prematerialize_enabled_semantic_models",
        noop_prematerialize,
    )
