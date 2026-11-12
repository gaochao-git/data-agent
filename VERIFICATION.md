# ChatData 存储分层改造 · 最终验证

> 分支：`feature/chat-data-storage-split`
> 结构：共享持久根 `CHAT_DATA_HOME`

## 最终目录

```text
CHAT_DATA_HOME/                         共享 JuiceFS/GlusterFS
├── threads/
│   └── {user_id}/
│       ├── {yyyy-MM-dd}/{conversation_id}/
│       │   ├── semantic-ref.json
│       │   └── user-data/
│       │       ├── uploads/
│       │       ├── workspace/
│       │       ├── outputs/
│       │       └── memory/
│       └── runtime/
│           ├── skills/
│           └── semantic/{datasource_id}/
├── skills/user/{user_id}/{skill}/versions/vN/
└── plugins/{plugin_name}/
```

## 关键设计

- 会话数据、技能运行快照、语义缓存、技能管理源、插件都在共享根。
- semantic ref 位于会话目录 `{cid}/semantic-ref.json`，只保存 `datasource_id`；user_id 由路径推导。
- runtime-skills 和 semantic 优先使用 Redis 租约串行写入；未配置 Redis 时降级本地锁。
- 共享 runtime 清理：`scripts/cleanup_shared_runtime.py`，保护 ref 指向的 semantic。

## 验证结果

```text
scripts/verify --all：通过
后端：4671 passed
行覆盖：98.37%
分支覆盖：97.41%
OpenAPI：无 diff
前端：637 passed，type-check / build 通过
共享目录写入：直接路径验证通过
Redis 租约降级：单测通过
共享 runtime GC：单测通过
```

## 未覆盖

- 真实 OpenSandbox 端到端
- 真实三节点并发 Run
- 主机房 MySQL/Redis/JuiceFS 切换演练
