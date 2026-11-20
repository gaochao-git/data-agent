# 子智能体全场景 E2E 测试（2026-11-20 23:17-23:50，qwen3.6）

## 0. 结论（先看这里）

**12 个场景全部通过，1 个新 P2 UI 缺陷，1 个 P3 错误消息优化点，1 个有意设计变更确认（pause/resume 已移除）。**

环境：前端 8089 + 后端 5001（23:09:47 重启，含 99a94abf 缓存指纹修复）+ `local/Qwen3.6-35B-A3B-4bit`（主对话与子智能体同源）。
会话：`78cc347f-1f6b-41fa-8072-d3e3e7a1f4d1`（「派发后台子智能...」，5 个子任务）+ 同步派发测试会话。

## 1. 场景结果矩阵

| # | 场景 | 结果 | 关键证据 |
| --- | --- | --- | --- |
| A | 后台派发（background=true） | ✅ | task 工具 16ms-1.3s 返回 task_id，主对话不被阻塞；5 次派发全部成功 |
| B | 任务卡/管理面板/过程视图 | ✅ | 卡片含类型/task_id 短码/轮次/工具次数/上下文百分比/创建结束时间/状态/耗时；过程视图含任务书+工作过程+报告+tok/s |
| C | 通知唤醒-活跃 Run steering | ✅ | 6 文件任务在主对话 list 查询运行中完成，完成简报注入同一轮（23:22:11 轮内） |
| C2 | 通知唤醒-空闲唤醒 Run | ✅ | 父 Run 已收口时推送失效→pending notification Run 领取→主对话自动简报（dc3438 任务 23:36 完成→23:37 简报） |
| D | follow_up 追问 | ✅ | 32s 完成；子会话线性第 2 轮（不分叉）；第 2 轮正确引用第 1 轮 file_write/file_read 历史；面板上下文 10%→11% |
| E | 关闭子智能体会话 | ✅ | 确认框警示「不能再追问/历史可查看/释放额度」→toast「子智能体已关闭，额度已释放」；卡片状态加「已关闭」标记 |
| F | 取消运行中任务 | ✅ | 执行中→正在取消→已取消（1m47s/55 工具调用）；**取消后主对话收到部分产出报告**（前 24 文件已建+校验、续写建议）——比 11-15 的「取消无通知」体验升级 |
| G | 刷新恢复 | ✅ | 刷新后重进会话：消息/产物预览/任务面板 4 任务（cancelled/completed+closed/completed/2轮）全部恢复 |
| H | list 感知 | ✅ | manage list active 185ms 返回；任务卡数量动态展示 |
| I | ask 档 MCP 工具子图剔除 | ✅ | 子智能体报告「mcp_fault-db_db_query 不存在于当前可用工具列表，仅有 mcp_fault-ssh_ssh_exec」，日志零 db_query 执行；自适应用 allow 档完成并如实汇报（详见 §2） |
| J | 同步派发（background=false） | ✅ | 工具调用同步阻塞（5-25s）→结果内联回复（无 task_id/无账本登记）；同会话前序子任务产物可读（工作区共享）；跨会话工作区隔离亦验证（新会话读不到 78cc347f 的文件=正确） |
| K | closed 会话 send 拒绝 | ✅ | send 失败，模型如实报告「会话已关闭，需重新 dispatch」 |

## 2. 场景 I 的双重意义：今天上午「审批未生效」疑点闭环

上午记录（2026-11-20-subagent-approval-bubble.md）发现 ask 档 MCP 工具被子智能体直接执行。本轮定位根因：

- **时间线**：后端 22:39:42 启动 → 22:46:45 plugin.json 改 `approval: "ask"` → 旧代码 loader 缓存 manifest + MCP TTL 缓存工具元数据 → 装配仍按旧档位（allow）→ 未剔除 → 子图可执行。
- **修复**：99a94abf（原未提交改动，23:14 提交）= manifest 文件指纹（mtime_ns/ctime_ns/size）+ MCP 配置签名双缓存失效，无需重启。
- **本轮复测**：重启后（23:09）新代码生效，ask 工具被完整剔除（静态链路：metadata 省略 approval → catalog 回落默认 ask → 子图工具集剔除 + `mcp_tool_search`/`mcp_tool_call` 在 SUBAGENT_TOOL_DENYLISTS 封死检索绕过）。**上午的观察是缓存滞后，非设计缺陷；修复确认有效。**

## 3. 新发现缺陷

### P3（原报 P2，复验+评审后降级）：EP MessageBox 过渡死锁 → 隐形全屏遮罩拦截整页点击

**复验结论：现象与 DOM 证据成立（可复现 2 次），流式不是必要条件；但触发机制未定论——「rAF 阵发停滞」只是候选解释之一，插桩能证明过渡类名叠加这一"果"，证明不了唯一"因"（「取消落在入场动画未完成时」同样能产生这组类名）。影响降级 P3：随窗口重新获得焦点自愈，无数据损害。**

- 复现 1（流式窗口）：通知简报流式生成中打开确认框 → 点击后 `ElMessageBox.setupState.visible` 翻为 false（EP 的 handleAction 收到了点击、promise 正常 reject），但 DOM 不移除；数分钟后 rAF 恢复、过渡回调补跑才自愈。
- 复现 2（**空闲态也复现**）：页面完全空闲时打开确认框 → 点取消 → overlay 类名残留 `fade-in-linear-enter-from fade-in-linear-leave-from fade-in-linear-leave-active` 三类并存、computed opacity:0、**pointer-events:auto** → 全屏隐形遮罩拦截整页点击。
- 决定性数据：`document.hasFocus()` = false（窗口失焦期）；rAF 阵发停滞（同页 setTimeout 正常）；rAF 恢复后残留遮罩自动清理（两次复现均自愈，无需刷新——昨晚结论「唯一恢复=刷新」修正为「等待或刷新」）。
- **统一解释昨晚全部症状**：①「MessageBox 按钮全失活」= 过渡死锁（点击其实生效、状态已翻、DOM 不动）；②「后续整页点击全灭」= 残留隐形遮罩（pointer-events:auto）吃掉所有点击；③「右栏按钮 Playwright locator click 全超时但 CUA 能点」= Playwright 点击的 stability 检查依赖 rAF，rAF 停滞期必超时。
- **已落地修复（2026-11-21，走「不依赖动画完成」的路子）**：`web-vue/src/utils/overlayClickGuard.ts` + `App.vue` onMounted 安装——窗口 focus/visibilitychange 时扫描全部 `.el-overlay`，把 opacity<0.01 的遮罩强制 pointer-events:none，恢复可见时还原内联覆盖（不误伤正常弹窗淡入淡出）；一次覆盖全站 20+ 处 ElMessageBox。单测 5 过（overlayClickGuard.spec.ts）。**注：曾考虑的「把过渡时长压到毫秒级」方案机制上无效已弃**——卡住的是 Vue nextFrame 回调本身（等下一帧），时长再短也要等帧。
- 两次复现均无数据损害（未发 API，卡片状态正常）。

### P3（2026-11-21 复验修正定性）：qwen3.6 嵌套参数生成不稳定，非错误消息问题

- 静态确认：closed 会话 send 的友好消息路径存在且正确（subagent_async.py:362-363「该子智能体会话已关闭，不能继续追问；如需同类任务请重新派发新的子智能体」，单测覆盖）。
- 实测（prompt 明确给出嵌套格式示例）：qwen3.6 仍生成平铺畸形参数（`send` 值非字典 + `task_id` 顶层平铺）→ schema 校验拒绝 "Extra inputs are not permitted"；模型从未到达语义拒绝层，靠自身推理回答「会话已关闭需重新派发」。
- 对照：昨晚同场景 follow_up 成功过（23:20，32s）→ 非确定性弱点。**处置：不改代码**——工具描述已有可直接照抄的嵌套 JSON 示例，且 test_subagent_tool_schema.py:118 专门锁定该契约，模型拿到示例仍写错，再补一遍不提高成功率；换模型或重试才是有效手段。

## 4. 有意设计变更确认（更新过时认知）

- **pause/resume 已被移除**：后端无 `TASK_STATUS_PAUSED`、前端 SubagentTaskRow 只有 取消/关闭 两按钮（归因修正：6bf0e5a3「完善子智能体并发写控制并收敛审批生命周期」明确写「移除子智能体 pause/resume 状态与控制命令」，2026-11-18；此前误记为 b89368aa）。11-15 验证过的暂停恢复入口不复存在，测试矩阵相应缩窄为 取消/关闭。
- **取消语义升级**：取消路径追加部分产出报告（`_append_partial_report`），主对话获得「已完成部分+续写建议」简报。
- 工具描述已从 1104 压到 552 字符（f18010bf），qwen3.6 嵌套 JSON 格式偶有调用错误（收到 schema 拒绝后能自我修正），本轮未复现 11-15 的「幻觉 task_id」。
- 写目标并发协调为 prompt 级引导（「写目标不同可并行、全局写串行」），无代码级锁——与旧 write_scopes 软提示语义等价。

## 5. 性能数据（qwen3.6 本地模型）

| 指标 | 数值 |
| --- | --- |
| task 工具派发返回 | 16ms-1.3s（background=true 立即返回） |
| 子任务执行 | 12s（1 文件）/ 27s（6 文件 15 工具）/ 1m21s（12 文件 26 工具）/ 1m47s（30 文件取消时 55 工具）/ 1m09s（MCP 连通性 7 工具） |
| 通知唤醒 | 活跃 Run 内联注入即时；空闲唤醒 Run 秒级领取 |
| follow_up 全链路 | 32s |
| 同步派发阻塞 | 5-25s（简单任务） |
| 生成速率 | 25-93 tok/s（波动较大，本地负载相关） |

## 6. 门禁收尾（本轮完成）

verify --fast 曾有 3 项失败（complexity/file-lines/ruff-format，均为 11-20 新提交引入），已修复并全绿（45s）：

1. `loader.py:112 _scan_disk_plugins` 复杂度 18→合规：拆出 `_scan_plugin_entry` 单目录扫描 helper。
2. `agent_stream.py` 2004→1967：`_usage_aux_stats` 迁至其数据口径所属的 `context_usage.py`（agent_stream 保留同名导入，测试零改动；顺带清理迁移后失效的 `latest_model_name_from_usage` 导入）。
3. `test_subagents_middleware.py` 2012→1915：4 个纯列表格式测试拆至新文件 `test_subagents_list_format.py`（103 行，仅依赖 subagent_async 格式函数）。
4. `context_usage.py` ruff format（-3 行机械重排）。

**以上修复未提交**（工作区 4 改动+1 新文件），待用户确认后提交。受影响测试 259 个先行全绿，verify --fast 全量门禁通过。

## 7. 测试方法备注

- 右侧栏按钮/任务卡的 Playwright locator 点击在本轮频繁超时（元素存在、无遮挡），CUA 坐标点击可靠；坐标随主对话流式高度变化漂移，每次点击前必须重新截图定位（本轮 2 次误触关闭按钮均因此）。**2026-11-21 根因补充：locator click 的 stability 检查依赖 rAF，rAF 阵发停滞期（窗口失焦）必超时；CUA（裸 CDP input）不走该检查，故"locator 超时、CUA 可点"成对出现——机制推断，与 §3 实测的 rAF 停滞证据吻合。**
- 欢迎页与会话页的发送按钮坐标不同（(1157,648) vs (802,648)）；原生 setter+input 事件注入 textarea 后必须重新定位发送按钮。
- 后端日志 grep 须排除 pytest fixture 窗口（23:10:22+ 的 NODE_ID/db down 告警为本轮 verify 测试写入，非活进程）。

## 8. 2026-11-21 复验轮（用户要求对「是问题的」两项重复验证）

- P2：复现 2 次（流式窗口 1 次 + 空闲态 1 次），插桩数据见 §3；根因修正为 rAF 停滞 × EP 过渡死锁 × 隐形遮罩，与流式无必然关联，两次均自愈无数据损害。
- P3：静态（友好消息路径 subagent_async.py:362）+ 动态（prompt 给示例仍生成畸形参数）双路复验，定性修正为 qwen3.6 嵌套参数稳定性问题。
- 复验中顺带观察：fe779c「同步读取db_query_result.md文件」出现在任务面板（创建 2026-11-20 23:45）——**同步派发任务现在也进账本**，与 11-15「sync 路径不过账本」的旧观察不同，为后续架构演进结果，非缺陷。
- 复验轮新派发任务：ca251f/cf9bf4（P2 复现用）均已完成，卡片状态正常。
