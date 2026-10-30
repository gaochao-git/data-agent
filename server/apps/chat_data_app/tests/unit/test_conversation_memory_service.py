"""conversation_memory_service 测试：路径、flock 合并、调度防抖、抽取降级与注入渲染。

跨进程合并用 subprocess 双进程验证 flock 临界区不丢条目；LLM 抽取一律打桩，
不触网、不读真实模型注册表。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from apps.chat_data_app.services.memory import conversation_memory_service as svc
from apps.chat_data_app.services.memory.memory_parser import (
    MAX_MEMORY_ITEMS,
    MemoryItem,
    render_memory_document,
)

# tests/unit -> tests -> chat_data_app -> apps -> server
SERVER_ROOT = str(Path(__file__).resolve().parents[4])


@pytest.fixture
def memory_env(monkeypatch):
    """配置隔离：显式打开开关并清掉覆盖项，保证用例间不串环境。"""
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_ENABLED", "true")
    monkeypatch.delenv("CHAT_DATA_SESSION_MEMORY_MAX_ITEMS", raising=False)
    monkeypatch.delenv("CHAT_DATA_SESSION_MEMORY_INJECTION_MAX_TOKENS", raising=False)


@pytest.fixture
def workspace_root(tmp_path):
    return tmp_path / "user-data"


# ---------------------------------------------------------------------------
# 配置与路径
# ---------------------------------------------------------------------------


def test_session_memory_config_defaults(monkeypatch):
    monkeypatch.delenv("CHAT_DATA_SESSION_MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("CHAT_DATA_SESSION_MEMORY_MAX_ITEMS", raising=False)
    monkeypatch.delenv("CHAT_DATA_SESSION_MEMORY_INJECTION_MAX_TOKENS", raising=False)
    assert svc.session_memory_enabled() is True
    assert svc.session_memory_max_items() == MAX_MEMORY_ITEMS
    assert svc.session_memory_injection_max_tokens() == 1500


def test_session_memory_config_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_MAX_ITEMS", "0")
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_INJECTION_MAX_TOKENS", "abc")
    assert svc.session_memory_max_items() == MAX_MEMORY_ITEMS
    assert svc.session_memory_injection_max_tokens() == 1500


def test_memory_paths_under_user_data(workspace_root):
    assert svc.memory_file_path(str(workspace_root)) == workspace_root / "memory" / "memory.md"
    assert svc.memory_lock_path(str(workspace_root)) == workspace_root / ".memory.lock"


def test_load_memory_items_missing_file_returns_empty(workspace_root):
    assert svc.load_memory_items(str(workspace_root)) == []


# ---------------------------------------------------------------------------
# 合并（含跨进程 flock）
# ---------------------------------------------------------------------------


def test_merge_memory_items_appends_dedups_and_evicts_oldest(workspace_root, memory_env):
    root = str(workspace_root)
    merged = svc.merge_memory_items(root, [MemoryItem(category="口径", content="A")])
    assert [item.content for item in merged] == ["A"]

    # 空白折叠键去重：同一条目不重复追加；新条目排在已有条目之后
    merged = svc.merge_memory_items(
        root, [MemoryItem(category="口径", content="A"), MemoryItem(category="偏好", content="B")]
    )
    assert [item.content for item in merged] == ["A", "B"]

    # 超出上限淘汰最早条目，文件内容与内存一致，无 tmp 残留
    bulk = [MemoryItem(category=f"c{i:02d}", content=f"内容{i}") for i in range(MAX_MEMORY_ITEMS + 5)]
    merged = svc.merge_memory_items(root, bulk)
    assert len(merged) == MAX_MEMORY_ITEMS
    assert merged[0].content == "内容5"
    assert (workspace_root / ".memory.lock").exists()
    assert not list(workspace_root.glob(".memory-*.tmp"))
    assert (workspace_root / "memory" / "memory.md").read_text(encoding="utf-8") == (render_memory_document(merged))


def test_merge_memory_items_two_processes_no_loss(tmp_path, workspace_root, memory_env):
    """双进程并发合并：flock 临界区保证两个进程的不同条目都落盘。"""
    script = tmp_path / "memory_writer.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys
            from apps.chat_data_app.services.memory import conversation_memory_service as svc
            from apps.chat_data_app.services.memory.memory_parser import MemoryItem

            root, category, content = sys.argv[1], sys.argv[2], sys.argv[3]
            svc.merge_memory_items(root, [MemoryItem(category=category, content=content)])
            """
        ).strip(),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": SERVER_ROOT,
        "CHAT_DATA_HOME": str(tmp_path / "chat-data-home"),
    }
    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(workspace_root), "口径", f"进程{i}的结论"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for i in range(2)
    ]
    for index, proc in enumerate(procs):
        _, stderr = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"writer {index} failed: {stderr.decode('utf-8', 'replace')}"

    items = svc.load_memory_items(str(workspace_root))
    assert sorted(item.content for item in items) == ["进程0的结论", "进程1的结论"]


# ---------------------------------------------------------------------------
# 调度（防抖 / 立即 / 守卫）
# ---------------------------------------------------------------------------


def test_snapshot_messages_for_extraction_limits_and_coerces():
    messages = [HumanMessage(content=str(i)) for i in range(45)]
    snapshot = svc.snapshot_messages_for_extraction(messages)
    assert len(snapshot) == svc.EXTRACTION_MAX_TURNS
    assert snapshot[-1].content == "44"
    assert svc.snapshot_messages_for_extraction(None) == []
    assert svc.snapshot_messages_for_extraction(object()) == []


def test_schedule_after_turn_guards(monkeypatch, tmp_path, memory_env):
    async def unused(**kwargs):  # pragma: no cover - 守卫命中时不应被调用
        raise AssertionError("不应触发抽取")

    monkeypatch.setattr(svc, "extract_and_save_memory", unused)
    common = {"workspace_root": str(tmp_path), "user_id": "u", "username": "n", "model_id": "m"}

    # 开关关闭不调度
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_ENABLED", "false")
    svc.schedule_memory_extraction_after_turn(conversation_id="c-guard", messages=[HumanMessage("q")], **common)
    assert not svc._pending_extraction_tasks

    # 开关恢复后，空会话标识 / 空消息快照仍不调度
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_ENABLED", "true")
    svc.schedule_memory_extraction_after_turn(conversation_id="  ", messages=[HumanMessage("q")], **common)
    svc.schedule_memory_extraction_after_turn(conversation_id="c-guard", messages=[], **common)
    assert not svc._pending_extraction_tasks


@pytest.mark.asyncio
async def test_schedule_after_turn_debounce_keeps_latest(monkeypatch, tmp_path, memory_env):
    monkeypatch.setattr(svc, "EXTRACTION_DEBOUNCE_SECONDS", 0.01)
    captured: list[dict] = []

    async def fake_extract(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(svc, "extract_and_save_memory", fake_extract)
    common = {"workspace_root": str(tmp_path), "user_id": "u", "username": "n", "model_id": "m"}

    svc.schedule_memory_extraction_after_turn(conversation_id="c-debounce", messages=[HumanMessage("第一轮")], **common)
    svc.schedule_memory_extraction_after_turn(conversation_id="c-debounce", messages=[HumanMessage("第二轮")], **common)
    await asyncio.sleep(0.05)

    assert len(captured) == 1
    assert [message.content for message in captured[0]["messages"]] == ["第二轮"]
    assert captured[0]["conversation_id"] == "c-debounce"
    # 任务完成后防抖表清理
    assert not svc._pending_extraction_tasks


@pytest.mark.asyncio
async def test_schedule_extraction_now_runs_immediately(monkeypatch, tmp_path, memory_env):
    captured: list[dict] = []

    async def fake_extract(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(svc, "extract_and_save_memory", fake_extract)
    svc.schedule_memory_extraction_now(
        conversation_id="c-now",
        workspace_root=str(tmp_path),
        model_id="m",
        messages=[HumanMessage("旧消息")],
    )
    await asyncio.sleep(0.01)
    assert len(captured) == 1
    assert [message.content for message in captured[0]["messages"]] == ["旧消息"]


@pytest.mark.asyncio
async def test_after_agent_turn_full_chain_writes_memory(monkeypatch, tmp_path, memory_env):
    """after_turn 钩子全链路：先打点，再从最终 state 取消息调度防抖抽取落盘。"""
    monkeypatch.setattr(svc, "EXTRACTION_DEBOUNCE_SECONDS", 0.01)

    async def fake_extract_items(messages, *, model_id, existing_items=()):
        return svc.MemoryExtraction(items=(MemoryItem(category="口径", content="A"),))

    monkeypatch.setattr(svc, "extract_memory_items", fake_extract_items)
    log_calls: list[bool] = []

    async def log_done():
        log_calls.append(True)

    adapter = SimpleNamespace(state=SimpleNamespace(values={"messages": [HumanMessage("q"), AIMessage("a")]}))
    await svc.after_agent_turn_with_memory(
        log_done,
        adapter,
        conversation_id="c-full",
        workspace_root=str(tmp_path / "user-data"),
        user_id="u",
        username="n",
        model_id="m",
    )
    assert log_calls == [True]
    await asyncio.sleep(0.05)
    items = svc.load_memory_items(str(tmp_path / "user-data"))
    assert [item.content for item in items] == ["A"]


@pytest.mark.asyncio
async def test_after_agent_turn_skips_when_state_unavailable(monkeypatch, tmp_path, memory_env):
    monkeypatch.setattr(svc, "EXTRACTION_DEBOUNCE_SECONDS", 0.01)
    captured: list[dict] = []

    async def fake_extract(**kwargs):
        captured.append(kwargs)  # pragma: no cover - 守卫生效时不应触发

    monkeypatch.setattr(svc, "extract_and_save_memory", fake_extract)

    async def log_done():
        return None

    # state 缺失：messages=None 走守卫拒绝，不调度
    adapter = SimpleNamespace(state=None)
    await svc.after_agent_turn_with_memory(
        log_done,
        adapter,
        conversation_id="c-skip",
        workspace_root=str(tmp_path),
        user_id=None,
        username=None,
        model_id=None,
    )

    # state.values 读取异常：静默跳过
    class _BoomState:
        @property
        def values(self):
            raise RuntimeError("no state")

    await svc.after_agent_turn_with_memory(
        log_done,
        SimpleNamespace(state=_BoomState()),
        conversation_id="c-skip",
        workspace_root=str(tmp_path),
        user_id=None,
        username=None,
        model_id=None,
    )
    await asyncio.sleep(0.02)
    assert captured == []


# ---------------------------------------------------------------------------
# 抽取（LLM 打桩）
# ---------------------------------------------------------------------------


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        return AIMessage(content=self._content)


@pytest.mark.asyncio
async def test_extract_memory_items_builds_prompt_and_llm_kwargs(monkeypatch):
    captured: dict = {}

    def fake_llm(**kwargs):
        captured.update(kwargs)
        return _FakeLLM('{"items": [{"category": "口径", "content": "A", "scope": ""}]}')

    monkeypatch.setattr(svc, "get_chat_data_llm", fake_llm)
    result = await svc.extract_memory_items([HumanMessage("q")], model_id="m1")
    assert [item.to_line() for item in result.items] == ["- [口径] A"]
    assert captured["model"] == "m1"
    assert captured["streaming"] is False
    assert captured["max_tokens"] == svc.EXTRACTION_LLM_MAX_TOKENS


@pytest.mark.asyncio
async def test_extract_memory_items_normalizes_and_skips_invalid(monkeypatch):
    payload = (
        '{"items": ['
        '{"category": "口径", "content": "销售额含税", "scope": "订单分析"},'
        '{"category": "", "content": "缺类别"},'
        '"不是字典的条目"'
        "]}"
    )
    monkeypatch.setattr(svc, "get_chat_data_llm", lambda **kwargs: _FakeLLM(payload))
    result = await svc.extract_memory_items([HumanMessage("q"), AIMessage("a")], model_id="m")
    assert result.items == (MemoryItem(category="口径", content="销售额含税", scope="订单分析"),)


@pytest.mark.asyncio
async def test_extract_memory_items_joins_chunked_content(monkeypatch):
    class _ChunkLLM:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=[{"text": '{"items":['}, {"text": '{"category":"口径","content":"A"}]}'}])

    monkeypatch.setattr(svc, "get_chat_data_llm", lambda **kwargs: _ChunkLLM())
    result = await svc.extract_memory_items([HumanMessage("q")], model_id="m")
    assert [item.content for item in result.items] == ["A"]


@pytest.mark.asyncio
async def test_extract_memory_items_llm_failure_returns_empty(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(svc, "get_chat_data_llm", boom)
    assert await svc.extract_memory_items([HumanMessage("q")], model_id="m") == svc.MemoryExtraction()


@pytest.mark.asyncio
async def test_extract_memory_items_unparsable_or_non_list_returns_empty(monkeypatch):
    monkeypatch.setattr(svc, "get_chat_data_llm", lambda **kwargs: _FakeLLM("我说不出 JSON"))
    assert await svc.extract_memory_items([HumanMessage("q")], model_id="m") == svc.MemoryExtraction()
    monkeypatch.setattr(svc, "get_chat_data_llm", lambda **kwargs: _FakeLLM('{"items": "not-a-list"}'))
    assert await svc.extract_memory_items([HumanMessage("q")], model_id="m") == svc.MemoryExtraction()


# ---------------------------------------------------------------------------
# 抽取落盘与降级
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_and_save_memory_writes_via_merge(monkeypatch, workspace_root, memory_env):
    async def fake_extract_items(messages, *, model_id, existing_items=()):
        return svc.MemoryExtraction(items=(MemoryItem(category="口径", content="A"),))

    monkeypatch.setattr(svc, "extract_memory_items", fake_extract_items)
    await svc.extract_and_save_memory(
        conversation_id="c1", workspace_root=str(workspace_root), model_id="m", messages=[]
    )
    items = svc.load_memory_items(str(workspace_root))
    assert [item.content for item in items] == ["A"]


@pytest.mark.asyncio
async def test_extract_and_save_memory_empty_items_skips_write(monkeypatch, workspace_root, memory_env):
    async def fake_extract_items(messages, *, model_id, existing_items=()):
        return svc.MemoryExtraction()

    monkeypatch.setattr(svc, "extract_memory_items", fake_extract_items)
    await svc.extract_and_save_memory(
        conversation_id="c1", workspace_root=str(workspace_root), model_id="m", messages=[]
    )
    assert not (workspace_root / "memory" / "memory.md").exists()


@pytest.mark.asyncio
async def test_extract_and_save_memory_merge_failure_degrades(monkeypatch, tmp_path, memory_env):
    async def fake_extract_items(messages, *, model_id, existing_items=()):
        return svc.MemoryExtraction(items=(MemoryItem(category="口径", content="A"),))

    monkeypatch.setattr(svc, "extract_memory_items", fake_extract_items)

    def broken_merge(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(svc, "merge_memory_items", broken_merge)
    # 不抛出即通过（失败只告警降级）
    await svc.extract_and_save_memory(conversation_id="c1", workspace_root=str(tmp_path), model_id="m", messages=[])


# ---------------------------------------------------------------------------
# 注入渲染
# ---------------------------------------------------------------------------


def test_render_session_memory_block_guards(monkeypatch, workspace_root, memory_env):
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_ENABLED", "false")
    assert svc.render_session_memory_block(str(workspace_root), conversation_id="c1") == ""
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_ENABLED", "true")
    # 空会话标识与文件缺失均返回空串
    assert svc.render_session_memory_block(str(workspace_root), conversation_id="  ") == ""
    assert svc.render_session_memory_block(str(workspace_root), conversation_id="c1") == ""


def test_render_session_memory_block_escapes_entries(monkeypatch, workspace_root, memory_env):
    items = [MemoryItem(category="口径", content="销售额<含税>与`引用`", scope="订单分析")]
    memory_path = workspace_root / "memory" / "memory.md"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(render_memory_document(items), encoding="utf-8")

    block = svc.render_session_memory_block(str(workspace_root), conversation_id="c1")
    assert block == "- [口径] 销售额＜含税＞与ˋ引用ˋ（订单分析）"
    assert "<" not in block and ">" not in block and "`" not in block


def test_render_session_memory_block_trims_by_budget_from_newest(monkeypatch, workspace_root, memory_env):
    items = [
        MemoryItem(category="口径", content="旧口径"),
        MemoryItem(category="偏好", content="b" * 2000),
    ]
    memory_path = workspace_root / "memory" / "memory.md"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(render_memory_document(items), encoding="utf-8")

    # 小预算：最新的长条目保底保留，旧条目被裁掉
    monkeypatch.setenv("CHAT_DATA_SESSION_MEMORY_INJECTION_MAX_TOKENS", "100")
    block = svc.render_session_memory_block(str(workspace_root), conversation_id="c1")
    assert "口径" not in block
    assert "偏好" in block


def test_extraction_prompt_prioritizes_user_text_and_trims_tool_results():
    from langchain_core.messages import ToolMessage

    from apps.chat_data_app.agent.prompts.memory_extraction import (
        MEMORY_TOOL_RESULT_MAX_CHARS,
        build_memory_extraction_prompt,
    )

    prompt = build_memory_extraction_prompt(
        [
            HumanMessage("以后销售额按含税口径"),
            ToolMessage(content="工" * 5000, name="data_analyzing_tool", tool_call_id="call-1"),
            AIMessage("好的，已记录"),
        ],
        existing_items=[MemoryItem(category="偏好", content="报告默认用表格")],
    )

    assert "以后销售额按含税口径" in prompt
    assert "报告默认用表格" in prompt
    assert "工" * (MEMORY_TOOL_RESULT_MAX_CHARS + 1) not in prompt
    assert "工具(data_analyzing_tool)结果摘要" in prompt


@pytest.mark.asyncio
async def test_extract_memory_items_parses_supersedes_and_remove(monkeypatch):
    payload = (
        '{"items":[{"category":"口径","content":"销售额未税","scope":"本次分析",'
        '"supersedes":["销售额含税"]}],"remove":["旧偏好"]}'
    )
    monkeypatch.setattr(svc, "get_chat_data_llm", lambda **kwargs: _FakeLLM(payload))

    result = await svc.extract_memory_items([HumanMessage("纠正口径")], model_id="m")

    assert result.items[0].supersedes == ("销售额含税",)
    assert result.removals == ("旧偏好",)


def test_merge_memory_items_applies_supersedes_and_removals(workspace_root, memory_env):
    root = str(workspace_root)
    svc.merge_memory_items(root, [MemoryItem(category="口径", content="销售额含税")])
    svc.merge_memory_items(root, [MemoryItem(category="偏好", content="旧偏好")])

    merged = svc.merge_memory_items(
        root,
        [MemoryItem(category="口径", content="销售额未税", supersedes=("销售额含税",))],
        removals=("旧偏好",),
    )

    assert [item.content for item in merged] == ["销售额未税"]
