"""agent_state messages DeltaChannel 测试——fold 结合律与恢复重放语义。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.channels import DeltaChannel
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from apps.chat_data_app.agent.agent_state import delta_messages_reducer

# 消息一律显式携带 id：add_messages 对无 id 消息随机补 id，会干扰相等断言。


def _human(msg_id: str, content: str) -> HumanMessage:
    return HumanMessage(id=msg_id, content=content)


def _ai(msg_id: str, content: str) -> AIMessage:
    return AIMessage(id=msg_id, content=content)


def test_reducer_fold_matches_sequential_batches():
    """分批 fold 与合并一批 fold 结果一致（DeltaChannel 批量结合律）。"""
    state = [_human("h1", "q")]
    w1 = [_ai("a1", "a")]
    w2 = [_human("h2", "q2"), _ai("a2", "a2")]

    sequential = delta_messages_reducer(delta_messages_reducer(state, [w1]), [w2])
    combined = delta_messages_reducer(state, [w1, w2])

    assert [(m.id, m.content) for m in sequential] == [
        ("h1", "q"),
        ("a1", "a"),
        ("h2", "q2"),
        ("a2", "a2"),
    ]
    assert sequential == combined


def test_reducer_remove_all_clears_then_appends():
    """REMOVE_ALL 哨兵按哨兵清空、不依赖消息 id。"""
    state = [_human("h1", "q"), _ai("a1", "a")]
    compaction = [RemoveMessage(id=REMOVE_ALL_MESSAGES), _human("h2", "compacted")]

    result = delta_messages_reducer(state, [compaction])

    assert [(m.id, m.content) for m in result] == [("h2", "compacted")]


def test_reducer_accepts_single_message_write():
    """单条消息（非列表）作为一次写入可直接追加。"""
    result = delta_messages_reducer([_human("h1", "q")], [_ai("a1", "a")])

    assert [(m.id, m.content) for m in result] == [("h1", "q"), ("a1", "a")]


def test_reducer_handles_none_state():
    """空 state（None）起始可正常折叠。"""
    result = delta_messages_reducer(None, [[_human("h1", "q")]])

    assert [(m.id, m.content) for m in result] == [("h1", "q")]


def test_delta_channel_replay_from_seed_matches_live():
    """恢复重放（seed + 祖先 writes 单次 reducer 调用）与 live 逐超步 update 一致。"""
    seed = [_human("h1", "q")]
    w1 = [_ai("a1", "a")]
    w2 = [RemoveMessage(id=REMOVE_ALL_MESSAGES), _human("h2", "compacted")]
    w3 = [_ai("a2", "a2")]

    live = DeltaChannel(reducer=delta_messages_reducer, snapshot_frequency=25)
    live = live.from_checkpoint(list(seed))
    for batch in (w1, w2, w3):
        live.update([batch])

    replayed = DeltaChannel(reducer=delta_messages_reducer, snapshot_frequency=25)
    replayed = replayed.from_checkpoint(list(seed))
    replayed.replay_writes(
        [
            ("task-1", "messages", w1),
            ("task-1", "messages", w2),
            ("task-1", "messages", w3),
        ]
    )

    assert replayed.get() == live.get()


def test_delta_channel_accepts_legacy_plain_value_checkpoint():
    """旧 LastValue 全量 checkpoint（plain value）可直接作种子并继续增量写入。"""
    legacy = [_human("h1", "q"), _ai("a1", "a")]

    ch = DeltaChannel(reducer=delta_messages_reducer, snapshot_frequency=25)
    ch = ch.from_checkpoint(list(legacy))
    ch.update([[_human("h2", "q2")]])

    assert [(m.id, m.content) for m in ch.get()] == [
        ("h1", "q"),
        ("a1", "a"),
        ("h2", "q2"),
    ]
