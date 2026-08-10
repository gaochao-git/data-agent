# SQL Run

本目录负责 SQL 解析、安全校验、执行器、查询结果、缓存、并发限制、异步任务、查询历史和查询收藏。

语义 DSL 的编译由 `services/semantic/run/` 编排并调用 `services/semantic/core/`，本目录只负责实际 SQL 运行生命周期。
