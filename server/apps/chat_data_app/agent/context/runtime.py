"""上下文运行时：预算、摘要准备与压缩/计量中间件的共享入口。

目标是把“同一个 agent 的上下文状态”收敛成一个对象：

- 一次性解析模型预算并记录 system prompt + tools 的静态 token；
- 统一提供运行态摘要的 legacy 清洗、去重与前插；
- 供 ``ContextCompressionMiddleware`` 与 ``ContextUsageMiddleware`` 共享，
  避免两边各自解析预算、各自注入摘要。

当前实现仍复用 ``context_budget`` 的纯函数与既有中间件报告构造；后续可以把
压缩/计量的实现继续搬进本模块，中间件只保留 LangChain 适配层。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage

from .context_budget import (
    ContextBudget,
    ContextRequestBudget,
    inject_runtime_context_summary,
)


@dataclass(frozen=True)
class ContextRuntime:
    """单个 agent 的上下文预算与摘要准备统一入口。"""

    model_id: str | None
    system_prompt: str
    tools: tuple[Any, ...]
    request_budget: ContextRequestBudget

    @classmethod
    def create(
        cls,
        *,
        model_id: str | None,
        system_prompt: str,
        tools: list[Any] | tuple[Any, ...] | None = None,
        static_input_tokens: int | None = None,
    ) -> ContextRuntime:
        """解析预算并立即配置静态输入，返回可共享的运行时对象。"""
        tool_list = list(tools or ())
        request_budget = ContextRequestBudget(model_id)
        request_budget.configure(
            system_prompt,
            tool_list,
            model_id=model_id,
            static_input_tokens=static_input_tokens,
        )
        return cls(
            model_id=model_id,
            system_prompt=system_prompt,
            tools=tuple(tool_list),
            request_budget=request_budget,
        )

    @property
    def budget(self) -> ContextBudget:
        return self.request_budget.budget

    @property
    def static_input_tokens(self) -> int:
        return self.request_budget.static_input_tokens

    def prepare_messages(
        self,
        messages: list[BaseMessage],
        context_summary: str | None = None,
    ) -> tuple[list[BaseMessage], str | None, bool]:
        """剥离 legacy 摘要、合并运行态摘要并按需前插隐藏消息。"""
        return inject_runtime_context_summary(messages, context_summary)


__all__ = ["ContextRuntime"]
