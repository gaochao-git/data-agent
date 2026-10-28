"""context_budget 分支补测。

覆盖目标：`agent/context/context_budget.py` 中未被触达的分支（原 11 条缺失分支弧）。

模块本身是纯函数 + 少量配置读取，因此只需要 monkeypatch 三个配置读取函数
（`get_chat_data_model_configs` / `get_chat_data_output_max_tokens` /
`get_context_compression_summary_output_max_tokens`）即可驱动启动校验的所有分支，
不需要真的写模型注册表文件。

注意 monkeypatch 目标必须是 **context_budget 自己的命名空间**：模块用
``from ... import xxx`` 把名字绑定进来了，patch 源头模块不会生效。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from apps.chat_data_app.agent.context import context_budget as cb

# ---------------------------------------------------------------------------
# extract_legacy_context_summary（108->110 无前缀 / 110->112 空摘要）
# ---------------------------------------------------------------------------


def test_extract_legacy_context_summary_merges_prefixed_entries():
    messages = [
        SystemMessage(content="[历史对话摘要]\n\n  第一段摘要", additional_kwargs={"context_compression": True}),
        SystemMessage(content="[历史对话摘要]第二段摘要", additional_kwargs={"context_compression": True}),
        # 正文带了前缀但没有压缩标记 → 属于普通系统提示词，不能被剥离
        SystemMessage(content="[历史对话摘要]无标记前缀"),
        HumanMessage(content="用户问题"),
    ]
    cleaned, summary = cb.extract_legacy_context_summary(messages)

    assert cleaned == [messages[2], messages[3]]
    assert summary == "第一段摘要\n\n第二段摘要"


def test_extract_legacy_context_summary_keeps_summary_without_prefix():
    """旧摘要不一定带 `[历史对话摘要]` 前缀（108->110 假分支）。"""
    messages = [
        SystemMessage(content="  裸摘要  ", additional_kwargs={"context_compression": True}),
        HumanMessage(content="用户问题"),
    ]
    cleaned, summary = cb.extract_legacy_context_summary(messages)

    assert summary == "裸摘要"
    assert cleaned == [messages[1]]


def test_extract_legacy_context_summary_skips_blank_summary():
    """带标记但正文为空的 SystemMessage 只被剥离、不产生摘要（110->112 假分支）。"""
    messages = [
        SystemMessage(content="   ", additional_kwargs={"context_compression": True}),
        # 没有压缩标记的 SystemMessage 属于正常系统提示词，必须保留
        SystemMessage(content="你是一个助手"),
        AIMessage(content="收到"),
    ]
    cleaned, summary = cb.extract_legacy_context_summary(messages)

    assert summary is None
    assert cleaned == messages[1:]


def test_extract_legacy_context_summary_handles_empty_input():
    assert cb.extract_legacy_context_summary(None) == ([], None)
    assert cb.extract_legacy_context_summary([]) == ([], None)


# ---------------------------------------------------------------------------
# _content_blocks_tokens（133->135 非 dict 块 / 137->138 图片块）
# ---------------------------------------------------------------------------


def test_content_blocks_tokens_serializes_non_dict_blocks():
    """非 dict 的 content block 退回字符串序列化（133->135 真分支）。"""
    blocks = ["纯文本块", 123, None]

    expected = sum(cb.estimate_tokens(json.dumps(block, ensure_ascii=False, default=str)) for block in blocks)
    assert cb._content_blocks_tokens(blocks) == expected


def test_content_blocks_tokens_counts_image_block_with_fixed_estimate():
    """图片块按固定常数计费，与图片体积无关（137->138 真分支）。"""
    image_block = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 5000}}
    text_block = {"type": "text", "text": "说明文字"}

    assert cb._content_blocks_tokens([image_block]) == cb.IMAGE_TOKEN_ESTIMATE
    assert cb._content_blocks_tokens([image_block, text_block]) == (
        cb.IMAGE_TOKEN_ESTIMATE + cb.estimate_tokens(json.dumps(text_block, ensure_ascii=False, default=str))
    )


# ---------------------------------------------------------------------------
# _message_tokens（150->153 非 str/list content / 160->161 兜底 tool_calls）
# ---------------------------------------------------------------------------


def test_message_tokens_serializes_unexpected_content_type():
    """content 既不是 str 也不是 list 时走 JSON 序列化兜底（150->153）。

    LangChain 的 BaseMessage 会在构造时拒绝 dict 类型的 content，因此这里用
    duck-typed 桩对象：被测函数只读 content / tool_calls / additional_kwargs。
    """
    content = {"key": "值", "nested": {"a": 1}}
    message = SimpleNamespace(content=content, tool_calls=None, additional_kwargs={})

    assert cb._message_tokens(message) == 4 + cb.estimate_tokens(json.dumps(content, ensure_ascii=False, default=str))


def test_message_tokens_falls_back_to_additional_kwargs_tool_calls():
    """未解析成结构化 tool_calls 时，从 additional_kwargs 取原始调用（160->161 真分支）。"""
    raw_tool_calls = [{"id": "call_1", "name": "search", "args": {"q": "关键词"}}]
    message = AIMessage(content="", additional_kwargs={"tool_calls": raw_tool_calls})

    assert message.tool_calls == []
    assert cb._message_tokens(message) == 4 + cb.estimate_tokens(
        json.dumps(raw_tool_calls, ensure_ascii=False, default=str)
    )


# ---------------------------------------------------------------------------
# validate_context_budget_startup_config（210->215 / 215->216 / 220->exit）
# ---------------------------------------------------------------------------


def _patch_startup_budget_sources(monkeypatch, configs, output_tokens, summary_output_tokens):
    monkeypatch.setattr(cb, "get_chat_data_model_configs", lambda: configs)
    monkeypatch.setattr(cb, "get_chat_data_output_max_tokens", lambda: output_tokens)
    monkeypatch.setattr(cb, "get_context_compression_summary_output_max_tokens", lambda: summary_output_tokens)
    # validate 内部会取压缩目标做自洽校验；按触发阈值 30% 的同口径桩掉，避免依赖真实模型注册表
    budgets = {}
    for config in configs:
        window = max(int(config.get("context_window") or 0), 1)
        trigger = max(int(window * 0.70), 1)
        budgets[str(config.get("id") or "")] = cb.ContextBudget(
            context_window=window,
            max_output_tokens=1,
            compression_trigger_ratio=0.70,
            hard_input_limit_tokens=1,
            compression_trigger_tokens=trigger,
            compression_target_tokens=max(int(trigger * 0.30), 1),
        )
    monkeypatch.setattr(cb, "get_model_context_budget", lambda model_id=None, **_kwargs: budgets[str(model_id or "")])


def test_startup_validation_passes_when_reserves_fit_context_window(monkeypatch):
    """两项预留都小于上下文窗口时不抛异常（210->215 + 220 正常退出）。"""
    _patch_startup_budget_sources(
        monkeypatch,
        configs=[{"id": "big-model", "context_window": 100_000}],
        output_tokens=8_000,
        summary_output_tokens=4_000,
    )

    cb.validate_context_budget_startup_config()


def test_startup_validation_rejects_summary_reserve_larger_than_window(monkeypatch):
    """只有摘要预留越界时报错（215->216 真分支）。"""
    _patch_startup_budget_sources(
        monkeypatch,
        configs=[{"id": "tiny", "context_window": 1_000}],
        output_tokens=100,
        summary_output_tokens=1_000,
    )

    with pytest.raises(RuntimeError, match="CHAT_DATA_CONTEXT_COMPRESSION_SUMMARY_OUTPUT_MAX_TOKENS=1000"):
        cb.validate_context_budget_startup_config()


def test_startup_validation_skips_models_with_inline_max_output_tokens(monkeypatch):
    """注册表内联 max_output_tokens 的模型跳过全局输出预留校验（解析时已校验）。"""
    _patch_startup_budget_sources(
        monkeypatch,
        configs=[{"id": "inline", "context_window": 1_000, "max_output_tokens": 512}],
        output_tokens=5_000,
        summary_output_tokens=100,
    )

    cb.validate_context_budget_startup_config()


def test_startup_validation_collects_errors_for_every_model(monkeypatch):
    """多个模型各自违规时错误信息用分号拼接；缺失 context_window 按 1 兜底。"""
    _patch_startup_budget_sources(
        monkeypatch,
        configs=[
            {"id": "model-a", "context_window": 2_000},
            {"id": "model-b"},  # 无 context_window → max(int(0), 1) == 1
            {"context_window": 2_000},  # 无 id → 空字符串
        ],
        output_tokens=5_000,
        summary_output_tokens=5_000,
    )

    with pytest.raises(RuntimeError) as excinfo:
        cb.validate_context_budget_startup_config()

    message = str(excinfo.value)
    for model_id in ("model-a", "model-b"):
        assert model_id in message
    # 每个模型 3 条违规（output + summary + 压缩目标），3 个模型共 9 条，用分号分隔
    assert message.count(";") == 8


def test_startup_validation_rejects_target_smaller_than_summary_budget(monkeypatch):
    """压缩目标（触发阈值 30%）不大于摘要输出预算时报配置自洽错误。"""
    _patch_startup_budget_sources(
        monkeypatch,
        configs=[{"id": "tiny", "context_window": 100_000}],
        output_tokens=8_000,
        summary_output_tokens=50_000,
    )
    monkeypatch.setattr(
        cb,
        "get_model_context_budget",
        lambda *_args, **_kwargs: cb.ContextBudget(
            context_window=100_000,
            max_output_tokens=20_000,
            compression_trigger_ratio=0.70,
            hard_input_limit_tokens=80_000,
            compression_trigger_tokens=56_000,
            compression_target_tokens=100,
        ),
    )

    with pytest.raises(RuntimeError, match="压缩目标 100"):
        cb.validate_context_budget_startup_config()


# ---------------------------------------------------------------------------
# sanitize_context_usage_for_response：breakdown 明细清洗
# （264->265 / 276->277 非 dict 项）
# ---------------------------------------------------------------------------


@pytest.fixture
def _system_prompt_hidden(monkeypatch):
    """确保走脱敏路径而非调试直通路径。"""
    monkeypatch.setattr(cb, "is_context_usage_system_prompt_visible", lambda: False)


def test_sanitize_breakdown_skips_non_dict_items(_system_prompt_hidden):
    """明细里混入非 dict 元素时两项清洗都要跳过而不是抛错。

    注意"跳过"只是 `continue`，元素本身仍然留在列表里（不改写结构）。
    """
    usage = {
        "breakdown": [
            "junk-string",
            None,
            {"role": "human", "content": "用户正文"},
        ]
    }

    sanitized = cb.sanitize_context_usage_for_response(usage)

    assert sanitized["breakdown"] == ["junk-string", None, {"role": "human"}]


def test_strip_breakdown_content_preserves_tool_schema_and_recurses(_system_prompt_hidden):
    """工具 schema 正文保留，其余角色正文剥离，并递归处理 children。"""
    usage = {
        "breakdown": [
            {
                "role": "human",
                "content": "用户正文",
                "children": [
                    {"role": "tool_schema", "content": '{"name": "search"}'},
                    {"role": "assistant", "content": "模型正文"},
                    "junk",
                ],
            }
        ]
    }

    sanitized = cb.sanitize_context_usage_for_response(usage)
    children = sanitized["breakdown"][0]["children"]

    assert "content" not in sanitized["breakdown"][0]
    assert children[0]["content"] == '{"name": "search"}'
    assert "content" not in children[1]
    assert children[2] == "junk"


def test_sanitize_breakdown_redacts_system_items():
    """`_sanitize_breakdown` 把 system_prompt / system 角色正文换成占位符并打标。"""
    items = [
        {"key": "system_prompt", "content": "机密提示词"},
        {"role": "system", "content": "机密提示词2", "children": [{"key": "system_prompt", "content": "x"}]},
        {"role": "human", "content": "用户正文"},
    ]

    cb._sanitize_breakdown(items)
    first, second, third = items

    for item in (first, second):
        assert item["content"] == cb.SYSTEM_PROMPT_PLACEHOLDER
        assert item["description"] == cb.SYSTEM_PROMPT_PLACEHOLDER
        assert item["redacted"] is True
    assert second["children"][0]["content"] == cb.SYSTEM_PROMPT_PLACEHOLDER
    # 非系统项在这一步不动（正文由后续 _strip_breakdown_content 剥离）
    assert third["content"] == "用户正文"


def test_sanitize_breakdown_skips_non_dict_and_non_list():
    cb._sanitize_breakdown(None)
    cb._sanitize_breakdown("not-a-list")
    items = ["junk", None, {"role": "human", "content": "x"}]
    cb._sanitize_breakdown(items)
    assert items == ["junk", None, {"role": "human", "content": "x"}]


def test_system_items_lose_content_after_full_sanitize_pipeline(_system_prompt_hidden):
    """完整链路下系统项先被替换成占位符，再被剥离正文，只留下描述与标记。"""
    usage = {
        "breakdown": [
            {"key": "system_prompt", "content": "机密提示词"},
            {"role": "tool_schema", "content": '{"name": "search"}'},
        ]
    }

    sanitized = cb.sanitize_context_usage_for_response(usage)
    system_item, schema_item = sanitized["breakdown"]

    assert "content" not in system_item
    assert system_item["description"] == cb.SYSTEM_PROMPT_PLACEHOLDER
    assert system_item["redacted"] is True
    # 工具 schema 是唯一保留正文的明细行
    assert schema_item["content"] == '{"name": "search"}'


def test_sanitize_context_usage_passthrough_and_llm_calls_removal(_system_prompt_hidden):
    """非 dict 原样返回；llm_calls 始终移除。"""
    assert cb.sanitize_context_usage_for_response("not a dict") == "not a dict"
    assert cb.sanitize_context_usage_for_response(None) is None

    usage = {"llm_calls": [{"tokens": 1}, {"tokens": 2}], "used": 120, "breakdown": []}
    sanitized = cb.sanitize_context_usage_for_response(usage)

    assert "llm_calls" not in sanitized
    assert sanitized["used"] == 120


def test_sanitize_context_usage_returns_full_body_in_debug_mode(monkeypatch):
    """调试模式下保留正文，不做明细剥离。"""
    monkeypatch.setattr(cb, "is_context_usage_system_prompt_visible", lambda: True)

    usage = {"breakdown": [{"key": "system_prompt", "content": "原文保留"}]}
    sanitized = cb.sanitize_context_usage_for_response(usage)

    assert sanitized["breakdown"][0]["content"] == "原文保留"
    # 原始对象未被就地修改
    assert usage["breakdown"][0]["content"] == "原文保留"


# ---------------------------------------------------------------------------
# 其余纯函数边界
# ---------------------------------------------------------------------------


def test_hard_input_guard_tokens_never_returns_zero():
    assert cb.hard_input_guard_tokens(0) == 1
    assert cb.hard_input_guard_tokens(-100) == 1
    assert cb.hard_input_guard_tokens(1) == 1
    assert cb.hard_input_guard_tokens(1000) == 950
    assert cb.hard_input_guard_tokens(10) == 9  # min(9, 10) 后仍要 >= 1


def test_estimate_text_tokens_returns_zero_for_blank():
    assert cb.estimate_text_tokens("") == 0
    assert cb.estimate_text_tokens(None) == 0
    assert cb.estimate_text_tokens("你好") > 0


def test_merge_context_summaries_dedupes_and_preserves_order():
    assert cb.merge_context_summaries("第一段", "  ", None, "第一段", "第二段") == "第一段\n\n第二段"
    assert cb.merge_context_summaries("", None) is None


def test_build_runtime_context_summary_message_returns_none_for_blank():
    assert cb.build_runtime_context_summary_message(None) is None
    assert cb.build_runtime_context_summary_message("   ") is None

    message = cb.build_runtime_context_summary_message("摘要")
    assert message.additional_kwargs["context_summary"] is True
    assert message.additional_kwargs["hide_from_ui"] is True
    assert "摘要" in message.content


def test_count_messages_tokens_handles_empty_and_none():
    assert cb.count_messages_tokens(None) == 0
    assert cb.count_messages_tokens([]) == 0
    assert cb.count_messages_tokens([HumanMessage(content="你好")]) == 4 + cb.estimate_tokens("你好")


def test_compact_context_usage_for_history_truncates_text_keeps_counts():
    long_text = "x" * (cb.HISTORY_BREAKDOWN_TEXT_LIMIT + 10)
    usage = {
        "used": 100,
        "breakdown": [
            {
                "key": "tool_schema",
                "tokens": 12,
                "content": long_text,
                "description": long_text,
                "children": [{"role": "tool_schema", "tokens": 3, "content": long_text}],
            }
        ],
    }

    compact = cb.compact_context_usage_for_history(usage)

    assert compact["used"] == 100
    row = compact["breakdown"][0]
    assert row["tokens"] == 12
    assert row["content"].endswith("…")
    assert len(row["content"]) == cb.HISTORY_BREAKDOWN_TEXT_LIMIT + 1
    assert row["children"][0]["content"].endswith("…")
    # 原始 usage 不能被污染：SSE 仍要发完整内容
    assert usage["breakdown"][0]["content"] == long_text


def test_compact_context_usage_for_history_keeps_short_text_and_unknown_shapes():
    usage = {"breakdown": ["junk", None, {"key": "system_prompt", "content": "short"}]}

    compact = cb.compact_context_usage_for_history(usage)

    assert compact["breakdown"][0] == "junk"
    assert compact["breakdown"][1] is None
    assert compact["breakdown"][2]["content"] == "short"
    assert cb.compact_context_usage_for_history(None) is None


def test_compact_context_usage_keeps_debug_payload(monkeypatch):
    long_text = "x" * (cb.HISTORY_BREAKDOWN_TEXT_LIMIT + 10)
    monkeypatch.setattr(cb, "is_context_usage_system_prompt_visible", lambda: True)

    compact = cb.compact_context_usage_for_history({"breakdown": [{"content": long_text}]})

    assert compact["breakdown"][0]["content"] == long_text


# ---------------------------------------------------------------------------
# 运行态摘要统一入口（主图/子图/压缩共用）
# ---------------------------------------------------------------------------


def test_resolve_context_summary_strips_legacy_and_merges_state_summary():
    messages = [
        SystemMessage(content="[历史对话摘要]旧摘要", additional_kwargs={"context_compression": True}),
        HumanMessage(content="用户问题"),
    ]
    cleaned, summary = cb.resolve_context_summary(messages, "运行态摘要")
    assert cleaned == [messages[1]]
    assert summary == "运行态摘要\n\n旧摘要"


def test_inject_runtime_context_summary_prepends_hidden_message():
    messages = [HumanMessage(content="用户问题")]
    injected, summary, injected_flag = cb.inject_runtime_context_summary(messages, "运行态摘要")
    assert summary == "运行态摘要"
    assert injected_flag is True
    assert len(injected) == 2
    assert injected[0].additional_kwargs["context_summary"] is True
    assert injected[0].additional_kwargs["hide_from_ui"] is True
    assert injected[1] is messages[0]


def test_inject_runtime_context_summary_dedupes_same_summary():
    messages = [
        SystemMessage(content="[历史对话摘要]同一段摘要", additional_kwargs={"context_compression": True}),
        HumanMessage(content="用户问题"),
    ]
    injected, summary, injected_flag = cb.inject_runtime_context_summary(messages, "同一段摘要")
    assert summary == "同一段摘要"
    assert injected_flag is True
    assert len(injected) == 2


def test_inject_runtime_context_summary_migrates_legacy_summary():
    messages = [
        SystemMessage(content="[历史对话摘要]旧摘要", additional_kwargs={"context_compression": True}),
        HumanMessage(content="用户问题"),
    ]
    injected, summary, injected_flag = cb.inject_runtime_context_summary(messages)
    assert summary == "旧摘要"
    assert injected_flag is True
    assert len(injected) == 2
    assert injected[0].additional_kwargs["context_summary"] is True
    assert injected[1] == messages[1]


def test_resolve_context_summary_with_legacy_returns_migrated_flag():
    messages = [
        SystemMessage(content="[历史对话摘要]旧摘要", additional_kwargs={"context_compression": True}),
        HumanMessage(content="用户问题"),
    ]
    cleaned, summary, legacy = cb.resolve_context_summary_with_legacy(messages, None)
    assert cleaned == [messages[1]]
    assert summary == "旧摘要"
    assert legacy == "旧摘要"
