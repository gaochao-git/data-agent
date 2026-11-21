# 今日全量变更浏览器回归（2026-11-21 15:12~15:50）

- 范围：今天全部 17 个提交（HEAD `2658d0f5`），工作区已干净、无未提交改动。
- 方式：真浏览器（agent-browser）冒烟回归 + 单测合入回归。各功能点的深度端到端验证见各自记录文件（`2026-11-21-*.md`），本轮聚焦「合入 HEAD 后仍生效」＋补验尚未覆盖的提交。
- 服务端：worker 15:14:02 启动，晚于所有改动 mtime（最晚 14:54:36），HEAD 代码已加载。

## 今日提交分类
| 类别 | 提交 | 浏览器可验证点 | 验证状态 |
|---|---|---|---|
| 工具/提示词优化（07:13-09:12） | b493d30f/fb0abd5e/ee068cb7/b8cb6734/2a91aad7/8b5590fc/6165ec7b/7c744002/dafbe660 | 子智能体六动作扁平、未装配工具名不弹审批 | 早间已深度验证（flat-params 6/6、AG-17 13 场景）；本轮单测回归确认未破坏 |
| data2image（10:59-11:54） | 9e86a414/55b72d01/74505b2d | 结果文件→折线图→read_image、安全限制 | 早间已端到端 PASS（data2image.md）；本轮单测回归确认未破坏 |
| supersteps 主/子（12:31,12:50） | 4c87653e/6af3d663 | 底栏 N/limit 步、失败步数保留 | 本轮真点 PASS（见下） |
| 失败文案专门化 + 真实引擎步（13:10-13:44） | 工作区改动→合入 2658d0f5 | 超步专门文案、7/7 真实步 | 本轮历史回放 PASS（见下） |
| 错误持久化 turn_error（14:00） | 合入 2658d0f5 | 刷新/历史会话错误可见 | 上一轮刚验证 PASS；本轮 HEAD 回放再确认 |
| **模型注册表刷新事务冲突（14:54，本轮新验）** | 2658d0f5 | 模型写操作后缓存刷新不报会话冲突 | **本轮真点 PASS（见下）** |

## 本轮新增验证：模型注册表刷新的事务会话冲突修复
- 改动（读 diff）：`utils/database.py` 抽出 `_session_scope()`，新增 `get_isolated_session()`（始终新开会话、忽略 `transaction_scope` 绑定）；`services/llm_config.py::load_registry_from_db()` 改用 `ModelRegistryDao(session_context=get_isolated_session)`。
- 背景 bug：注册表缓存后台刷新任务经 `create_task` 会继承调用方事务的 ContextVar，复用普通会话触发「事务内禁止并发使用同一数据库会话」守卫，刷新失败→冷缓存报错。
- 浏览器真点（后台管理 → 大模型管理）：
  - 页面加载 3 个模型正常；点「刷新」列表正常；无错误。
  - **写操作**：deepseek-v4-flash（原停用）点「启用」→ `PATCH .../admin/models/enabled` 200 → 列表重拉 200 → 状态变「启用」、时间戳 15:43:29；错误探针 0，**无会话冲突告警**。
  - **缓存生效实证**：`GET /config` 立即含 deepseek（计数 4），证明写操作后的注册表缓存刷新成功（独立会话生效）。
  - **恢复**：点「停用」改回原状 → PATCH 200、DB `enabled_flag=0` 复原、探针 0。
- 单测：`test_db_session_transaction.py` + `test_model_registry_service.py` + `test_llm_config.py` **144 passed**（含新增 `test_isolated_session_bypasses_transaction_binding` / `test_load_registry_from_db_uses_isolated_session`）。
- 诚实边界：事务冲突是竞态（需在 `transaction_scope` 内恰好触发 TTL 过期刷新），浏览器无法稳定复现那一瞬间；本轮验证的是「修复后注册表加载链路健康 + 写操作后缓存刷新成功」，根因正确性由单测保证。

## 其余变更 HEAD 冒烟回归
| 项 | 方式 | 结果 |
|---|---|---|
| 主对话 supersteps | 新会话发「你好」→ 底栏 | **7 / 1000 步**正常显示、回答正常、探针 0（本地 35B 用时 49s） |
| 错误持久化历史回放 | 打开失败会话 `14563bcb` | `turn_error` 事件仍在库（error/error_type/supersteps/recursion_limit/timestamp 完整）；页面错误文案 + `7 / 7 步`仍显示、探针 0 |
| 单测合入回归 | 7 个功能测试文件 | **357 passed**（data2image/workspace/supersteps/agent_stream/event_service/db_session/model_registry） |
| 前端 type-check | `vue-tsc --build --force` | exit 0 |

## 收尾
- 临时模型启停已恢复原状（仅 update_time 变化，属正常写痕迹）；DB 无配置覆盖残留（runtime=1000）；未改产品代码；浏览器已关；服务 5001/8089 正常。
- 错误探针全程 0（模型管理写操作、主对话、历史回放三段）。
