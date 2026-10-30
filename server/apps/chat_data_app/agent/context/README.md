# Agent Context

上下文预算估算：`context_budget.py` 提供 token 估算与常量（`REQUEST_OVERHEAD_TOKENS` 等），
供 `middleware/context_usage.py`、`context_compression.py` 和语义检索服务共享。

## 运行态摘要统一入口

`context_budget.resolve_context_summary()` / `inject_runtime_context_summary()` 是主图压缩、
主图请求注入、子图压缩与子图预算提示共用的摘要处理入口：

- 剥离旧 checkpoint 中以 SystemMessage 内嵌的压缩摘要（`context_compression` 标记）；
- 合并 state 运行态摘要与遗留摘要，去重并保持顺序；
- 注入时固定前插隐藏 HumanMessage，保持唯一 system 消息。

不在各中间件里重复实现 legacy 迁移/摘要去重，避免主图与子图摘要口径漂移。

## ContextRuntime

`context/runtime.py` 提供一个 agent 运行内的统一上下文对象：

- 一次性解析模型预算并记录 system prompt + tools 的静态 token；
- 提供 `prepare_messages()` 统一剥离 legacy 摘要、合并运行态摘要、前插隐藏消息；
- 主图与子图分别创建自己的 runtime，压缩/计量中间件共享同一个 `ContextRequestBudget`。

后续压缩与计量的实现继续向 `runtime.py` 收拢，中间件只保留 LangChain 适配层。
