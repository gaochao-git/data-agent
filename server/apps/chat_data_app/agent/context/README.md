# Agent Context

上下文预算估算：`context_budget.py` 提供 token 估算与常量（`REQUEST_OVERHEAD_TOKENS` 等），
供 `middleware/context_usage.py`、`context_compression.py` 和语义检索服务共享。
