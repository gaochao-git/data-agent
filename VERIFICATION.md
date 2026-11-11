# 缓存根拆分改造 · 验证报告

> 分支：`feature/chat-data-storage-split`（HEAD `10f22830`，工作树干净）
> 改造目标：把「本地派生缓存」（`runtime-skills`、语义模型缓存）从共享持久根 `CHAT_DATA_HOME` 移出，落到独立的本机派生缓存根 `CHAT_DATA_CACHE_HOME`，二者职责彻底分离；同时收敛历史会话兼容代码，杜绝跨用户/跨会话的隐私窗口与旧布局读取链。

---

## 1. 改造要点

### 1.1 双根拆分（`server/utils/storage_paths.py`）
- 新增 `CHAT_DATA_CACHE_HOME` 解析（`storage_paths.py:30-42`）：显式配置优先；未配置时回退到 `<CHAT_DATA_HOME 名>-cache`（与共享根同级的兄弟目录），仍是绝对路径且必须存在。
- 派生缓存常量全部改挂缓存根（`storage_paths.py:44-51`）：
  - `CACHE_THREADS_DIR` = `<CACHE_HOME>/threads`
  - `CACHE_RUNTIME_SKILLS_DIR` = `<CACHE_HOME>/runtime-skills`
  - `CACHE_SEMANTIC_DIR` = `<CACHE_HOME>/semantic`，ref 真值 `CACHE_SEMANTIC_REF_DIR` = `<CACHE_HOME>/semantic/.refs`
  - `NO_SEMANTIC_CACHE_DIR` = `<CACHE_HOME>/semantic/.no-ref`：占位目录，**永不创建**，仅用于「会话未绑定数据源组」时给出恒不存在路径，调用方统一用 `is_dir()` 判断，不再回退任何旧布局。
- 会话磁盘目录仍唯一拼装于 `conversation_thread_dir`（共享根），派生缓存目录由 `conversation_cache_dir` / `conversation_cache_dir_from_workspace` 拼装（缓存根）。

### 1.2 runtime-skills 跨进程提交锁（`runtime_skill_snapshot.py`）
- 常量：`SNAPSHOT_LOCK_TIMEOUT_SECONDS = 30.0`、`SNAPSHOT_LOCK_POLL_SECONDS = 0.05`（`runtime_skill_snapshot.py:229-230`）。
- `_acquire_snapshot_lock(fd, *, timeout)`（`:263-281`）：`fcntl.flock(fd, LOCK_EX | LOCK_NB)` 带 deadline 轮询，**只重试** `EAGAIN/EWOULDBLOCK/EACCES`，其余 errno 直接抛出（避免把磁盘/权限故障误判成锁竞争）；超时返回 `False` 交给调用方决定降级。
- 锁文件 `_snapshot_lock_path`（`:252-260`）：普通隐藏文件 `.<sha256(target)[:32]>.lock`，放在**共享快照根的父目录**（非目标目录内），**永不删除**——GC 只递归删目录不会碰它，避免删锁换 inode 导致互斥静默失效。
- `_commit_snapshot`（`:284-329`）：在 `asyncio.to_thread` 内完成「指纹复核 → 原子替换」，`target → old → tmp → target`；`fd` 在临界区内 `LOCK_UN` 后关闭。超时分支：
  - 已有可用快照 → `logger.warning` 降级复用，本轮不阻断；
  - 无快照 → 抛 `SnapshotLockTimeoutError` fail-fast。
  - 指纹条件语义不变（先提交者赢），但「填充期并发不同指纹」记 `warning` 并复用对方视图、下轮 `was_ready=True` 自动重建收敛；「无指纹存量」记 `info` 兼容复用。

### 1.3 语义缓存 ref 新格式（`storage_paths.py:196-238`）
- ref 唯一真值源 `semantic/.refs`，格式 `{"user_id": ..., "datasource_id": ...}`（**无 `scope_hash`/`config_hash`**）。
- `conversation_semantic_dir_from_workspace`（`:158-168`）：有 ref → 按 `user_id/datasource_id` 定位；无 ref → 返回 `NO_SEMANTIC_CACHE_DIR` 下的恒不存在路径。
- `semantic_cache_dir`（`:176-180`）：`CACHE_SEMANTIC_DIR/<user>/<datasource>`，按用户+数据源隔离。

---

## 2. 评审 4 问的回应与修复

| # | 评审提出的问题 | 处置 |
|---|---------------|------|
| 1 | 提交无锁超时，卡住的持锁者会让其它 worker 永久阻塞 | 已加 `SNAPSHOT_LOCK_TIMEOUT_SECONDS=30s` + `LOCK_NB` 轮询（`_acquire_snapshot_lock`），超时降级复用或 fail-fast，不再无限阻塞线程池。 |
| 2 | 可能复用错误指纹（不同内容被当成命中） | `_commit_snapshot` 内强制指纹复核（`_snapshot_matches`），绝不覆盖正在被读的不同指纹目录；并发不同指纹只复用对方视图、下轮重建收敛。无指纹存量显式 `info`/`warning`，不再静默命中。 |
| 3 | 读侧存在并发窗口（读旧目录时另一进程 rename） | 保留写保护边界：`runtime.py` 的 `legacy_root` 仅作可见路径映射边界，旧 `workspace/semantic/` 不再被读取；原子替换保证读侧要么看到旧完整目录、要么新完整目录，无中间态。 |
| 4 | GC 不获取锁，会清扫正在构建的目录 | `cache_cleanup.py` 只认新布局，且 `_sweep_stale_siblings` 仅删超过 `SNAPSHOT_TMP_TTL_SECONDS=3600s` 的 `.building-*`/`.old-*`，新建临时目录属别的会话绝不按前缀误删。 |

---

## 3. 历史会话兼容代码砍除清单（共 9 处）

用户明确「历史会话不要」——砍兼容代码、数据不动（保留写保护边界避免旧目录变可写）。

| 文件 | 移除内容 |
|------|----------|
| `server/utils/storage_paths.py` | 删 `CACHE_CONTEXT_DIR`/`CACHE_LEGACY_*` 三常量、`conversation_semantic_dir()`、两个 `legacy_*_semantic_cache_dir()`、`scope_hash`/`config_hash` 分支、旧 ref 根、无 ref 回退旧路径；新增 `NO_SEMANTIC_CACHE_DIR` 占位；`__all__` 同步。 |
| `server/apps/chat_data_app/agent/workspace/runtime.py` | 删 `:576-580` 共享根 `workspace/semantic/` 历史 YAML 兜底读取；保留 `is_managed_semantic_model_workspace_path`/`to_visible_path` 里 `legacy_root` 仅作写保护边界。 |
| `server/apps/chat_data_app/agent/workspace/workspace_sync.py` | 改为**无条件** `exclude_local_root=legacy_semantic_root`（原 `if semantic_root.is_dir() else None`）。 |
| `server/apps/chat_data_app/agent/workspace/workspace_local_backend.py` | 删 `_is_stale_legacy_semantic`；list_dir/写入排除旧 semantic 改为无条件。 |
| `server/apps/chat_data_app/services/agent_run/conversation_workspace.py` | 删 `_is_stale_legacy_semantic`；`get_workspace_total_size` 无条件忽略历史 semantic。 |
| `server/utils/cache_cleanup.py` | 删 legacy 分类/日期正则/`threads/*/runtime-skills` 扫描，只认新布局。 |
| `server/conftest.py` | 删死 patch `CACHE_CONTEXT_DIR` 等。 |
| 测试 `test_cache_cleanup.py` / `test_conversation_workspace.py` / `test_workspace_execution.py` | 旧兼容用例改写为**反证用例**（历史 YAML 不可读/不上传/空间统计忽略历史 semantic）。 |

---

## 4. 测试与门禁结果

- **定向回归**：9 个相关测试文件 **406 passed**（含 `test_runtime_skill_snapshot.py` 新增 7 个 flock 超时/并发用例、`test_cache_cleanup.py` 155 行新用例、`test_conversation_workspace.py` 148 行改写）。
- **`scripts/verify --fast` 全绿**：`pytest -n auto`、mypy（528 文件 0 issues）、ruff + ruff-format、前端 `npm run type-check` 均通过。
- **复杂度**：3772 函数，S3776 无超标。
- **`git diff --check`**：无空白/冲突标记残留。
- 注：`verify --all`（全量门禁）与真实 LLM 端到端 run 本轮未触发，属后续按需执行项。

---

## 5. 浏览器端到端验证（用户实测 `localhost:8089/chatdata?parentComponentName=执法检查`）

实测目录结构**符合新设计**，无异常：

- 会话派生缓存顶层仅 `runtime-skills` + `semantic` 两个子目录，无 `context/`。
- 语义 ref 为**新格式**无 hash：`{"datasource_id":"ds_20260116115442_907669","user_id":"10001"}`（实时写入 16:28 铁证）。
- `.snapshot-ready` 标记含 **5 个技能指纹**，锁文件落在 `runtime-skills` 顶层（`.<sha256[:32]>.lock`）。
- 上下文占用 **146k / 256k**，语义模型正常载入。

---

## 6. 部署层遗留 TODO（非代码阻断项）

1. **生产显式配置 `CHAT_DATA_CACHE_HOME`** 并在 rsync/备份排除清单中加入该派生缓存根（派生缓存可丢弃重建，不应随共享根同步）。属部署配置项，需运维侧落地。
2. **共享根历史语义数据未物理清理**：只读扫描显示共享根 `user-data/workspace/semantic/` 仍有 1451 目录 / 11312 YAML / 56MB 历史数据。按用户「数据不动」原则保留，待单独清理窗口处理；代码侧已不再读取，不构成功能回归。

---

## 7. 结论

缓存根拆分、跨进程 flock 提交锁（含超时降级与指纹复核）、语义 ref 新格式、历史会话兼容代码全砍（9 处）均已落地并通过定向测试与 `--fast` 门禁；浏览器端到端验证目录结构符合预期。代码改动停在「已修改 + 已验证」，待用户提交（本文件 `VERIFICATION.md` 亦由用户决定是否纳入提交）。
