# 举报件存储 Scope 改造 TODO

## 当前状态

已完成 V1：路径工具、版本工具、举报分析/列表/答复草稿/trace 的 scope 入参、读写策略、scope 内缓存与测试。

暂未内置组织权限判定与旧 `user_id/base_id` 产物自动迁移；当前只做路径段安全校验，权限由上游鉴权层保证。

## 目标

将举报件产物从当前 `user_id/base_id` 私有路径，调整为以 `base_id + scope` 为主的共享/覆盖模型。

建议 scope 语义：

```text
base_id/
  default/              # 全案共享基线
  org_id/               # 组织独有覆盖
  org_id/entity_id/     # 组织下某个被举报主体独有覆盖
```

`user_id` 不再作为主存储路径的一部分，只作为操作者写入 `meta`、日志和权限校验上下文。

## TODO

- [x] 定义 scope 规则
  - [x] 明确 `default`、`org_id`、`org_id/entity_id` 的读写语义。
  - [x] 明确哪些产物只能写 `default`，哪些产物允许写组织/主体覆盖层。
  - [x] 明确 `entity_id` 的稳定来源，避免使用展示名直接作为路径。

- [x] 调整路径工具
  - [x] 在 `server/utils/storage_paths.py` 增加 `scope` 路径能力。
  - [x] 保留路径段安全校验，新增 `org_id/entity_id` 的 normalize/validate。
  - [x] 避免业务代码直接拼接 `base_id/default/org_id/entity_id`。

- [x] 调整版本工具
  - [x] 修改 `load_manifest/save_manifest/stage_dir/write_meta` 支持 scope。
  - [x] 让 `manifest.json` 跟随 scope 独立维护。
  - [x] 在 `meta.json` 写入 `scope/org_id/entity_id/created_by/updated_by`。

- [x] 调整 API 入参
  - [x] `complaint-analysis` 增加 `org_id`，必要时增加 `scope` 或 `entity_id`。
  - [x] `complaint-analysis/list` 增加同样的 scope 入参。
  - [x] `reply-draft`、`reply-draft/save`、`reply-draft` 查询、`reply-draft/download` 增加同样的 scope 入参。
  - [x] `trace` 增加 `org_id/entity_id/scope` 查询维度。

- [x] 实现统一读路径策略
  - [x] 优先读取 `base_id/org_id/entity_id`。
  - [x] 不存在时回退 `base_id/org_id`。
  - [x] 仍不存在时回退 `base_id/default`。
  - [x] 对需要严格隔离的写入禁止隐式回退。

- [x] 实现统一写路径策略
  - [x] 全案共享的 `texts/summary` 默认写 `default`。
  - [x] 组织独有的 `issue_analysis/reply` 写 `org_id`。
  - [x] 被举报主体独有的 `issue_analysis/reply` 写 `org_id/entity_id`。
  - [x] 所有写入必须记录操作者 `user_id`。

- [x] 梳理产物归属
  - [x] `texts`：确认是否始终全案共享。
  - [x] `summary`：确认是否始终全案共享，或是否受 `reported_entities` 影响。
  - [x] `issue_analysis`：按组织/被举报主体支持独立版本。
  - [x] `reply`：按组织/被举报主体支持独立版本。
  - [x] `manifest`：按 scope 独立维护版本指针。

- [x] 调整缓存与版本复用
  - [x] `force=false` 缓存命中必须限定在当前 scope。
  - [x] `issue_analysis_version` 不同 scope 之间不能混用。
  - [x] `reply` 的 `source_versions` 需要包含 scope 信息。

- [x] 调整 trace
  - [x] 日志统一补充 `org_id/scope/entity_id`。
  - [x] trace 查询按 `base_id + org_id + entity_id + version` 聚合。
  - [x] trace 页面按 scope 分组展示，避免不同组织/主体日志混在一起。

- [ ] 调整权限校验
  - [ ] 明确用户是否可读 `base_id/default`。
  - [ ] 明确用户是否可读写 `base_id/org_id`。
  - [ ] 明确用户是否可读写 `base_id/org_id/entity_id`。
  - [ ] 防止用户通过伪造 `org_id/entity_id` 访问未授权产物。

- [x] 更新文档
  - [x] 更新 `complaint_api_analysis.md`。
  - [x] 更新 `complaint_api_analysis_list.md`。
  - [x] 更新 `complaint_api_reply_draft.md`。
  - [x] 更新 `README.md` 中的存储路径说明。

- [x] 增加测试
  - [x] 同一 `base_id` 下不同用户读取同一 `default` 产物。
  - [x] 同一 `base_id` 下不同 `org_id` 读取各自覆盖产物。
  - [x] `org_id/entity_id` 存在时优先读取主体独有产物。
  - [x] 无覆盖层时正确回退到 `default`。
  - [ ] 无权限时不能通过 scope 参数越权读取。
