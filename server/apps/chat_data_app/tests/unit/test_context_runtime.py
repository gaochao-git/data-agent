"""ContextRuntime 统一入口测试。"""

from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from apps.chat_data_app.agent.context import context_budget as cb
from apps.chat_data_app.agent.context.runtime import ContextRuntime


def test_runtime_create_configures_budget_and_reuses_static_tokens(monkeypatch):
    monkeypatch.setattr(
        cb,
        "get_model_context_budget",
        lambda model_id, max_output_tokens=None: SimpleNamespace(
            compression_trigger_tokens=1000,
            context_window=8000,
            hard_input_limit_tokens=6000,
        ),
    )
    runtime = ContextRuntime.create(
        model_id="m",
        system_prompt="系统提示词",
        tools=(),
        static_input_tokens=321,
    )
    assert runtime.model_id == "m"
    assert runtime.system_prompt == "系统提示词"
    assert runtime.static_input_tokens == 321
    assert runtime.budget.compression_trigger_tokens == 1000


def test_runtime_prepare_messages_injects_summary_and_cleans_legacy():
    runtime = ContextRuntime.create(model_id=None, system_prompt="sys", tools=())
    messages = [
        SystemMessage(content="[历史对话摘要]旧摘要", additional_kwargs={"context_compression": True}),
        HumanMessage(content="当前问题"),
    ]
    prepared, summary, injected = runtime.prepare_messages(messages, "运行摘要")
    assert summary == "运行摘要\n\n旧摘要"
    assert injected is True
    assert prepared[0].additional_kwargs["context_summary"] is True
    assert prepared[1] == messages[1]
