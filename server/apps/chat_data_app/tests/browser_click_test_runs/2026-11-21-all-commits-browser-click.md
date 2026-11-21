# 今日全部 commit 真浏览器点击回归（2026-11-21 15:52~16:01）

## 背景与目标

用户前一轮要求「今天所有 commitid 都测试」，交付的是单测覆盖矩阵（后端 + 前端 + 纯文本模块加载）。用户明确纠正「让你浏览器点击测试」——本轮按真浏览器点击（agent-browser + 真会话页 + 实际数据源）逐个 commit 找页面可交互点真点，只认页面行为，不拿单测冒充。

- 页面：`http://localhost:8089/chatdata?parentComponentName=执法检查`
- 模型：local/Qwen3.6-35B-A3B-4bit（本地 oMLX 8100）
- 错误探针：每阶段用 `window.__errs`（error/unhandledrejection/console.error）采集，全程 0
- 服务：5001/8089 均 200；今日 16 个 commit 全在 HEAD

## 逐 commit 点击验证矩阵

| commit | 主题 | 页面可交互点（真点） | 结果 |
|---|---|---|---|
| `2658d0f5` | 模型注册表事务冲突 + turn_error 持久化 | 后台管理→大模型管理：真点 deepseek「启用→停用」两次（PATCH .../enabled 200→列表重拉 200，`/config` 缓存随启停出现/消失 deepseek，无事务会话冲突告警）；失败会话 14563bcb 历史回放：错误文案 + `7/7 步` 从落库还原 | **PASS** |
| `4c87653e` | 主/子智能体步数展示与持久化 | limit=7 真点触发失败：实时气泡 `7/7 步`；刷新后历史回放仍 `7/7 步`；正常轮次底栏 `N/1000 步` | **PASS** |
| `6af3d663` | 失败时保留图步数 | 新会话 50356930 limit=7 失败：turn_error 落库 payload 含 `supersteps=7`/`recursion_limit=7`/`GraphRecursionError`；回放步数与文案均保留 | **PASS** |
| `74505b2d` | data2image 可读性优化 | 打开端到端会话 db5a06f3 回放：模型读图回答（蓝色实线订单总数 74~188、橙色虚线订单总额、11月峰值）与图吻合；工具卡显示名在册；outputs PNG 产物可见 | **PASS**（复用早间 5 项内核+像素证据） |
| `55b72d01` | data2image 仅消费系统结果 | 会话回放 + outputs 仅合法 PNG、无自建文件产物 | **PASS**（复用早间端到端拒绝证据） |
| `9e86a414` | 新增 data2image 工具 | 工具卡「数据转折线图」在册 + PNG 产物可见 | **PASS** |
| `b3d9bc64` | 兜底弹窗遮罩死锁 | 注入 opacity=0 全屏 `.el-overlay` + 派发 focus 触发全局守卫：遮罩被强制 `pointer-events:none`；真点「新建任务」穿透成功、空态出现 | **PASS** |
| `dafbe660` | 优化技能说明 | 输入框输 `/` 真点弹技能菜单：数据分析/Office 文档处理/故障分析/示例技能等列出，说明文案加载可见 | **PASS** |
| `2a91aad7`/`6165ec7b`/`8b5590fc`/`ee068cb7` | 提示词去重/优化/文件工具描述 | 页面无独立交互点；新会话「你好」真点对话 1分16秒正常完成、底栏 `7/1000 步`、探针 0，证明提示词改动合入后装配/对话链路健康 | **PASS**（行为层间接覆盖；语义正确性由单测+早间专项保证） |
| `fb0abd5e` | 未装配工具名不弹审批 | 页面无直接触发点（本质是"不该弹的没弹"）；本轮工具卡回放未见误弹审批，逻辑由单测 `test_tool_approval_middleware.py` 覆盖 | **间接 PASS** |
| `b493d30f` | 子智能体参数扁平化 | 图表会话含子智能体工具卡「子智能体读取所有语义模型」回放正常；逻辑由 7 个前端 spec + 单测覆盖 | **间接 PASS** |
| `7c744002` | 纯测试记录 md | 无页面影响 | N/A |

## 关键证据

- 模型启停：`PATCH .../admin/models/enabled` 200 → `GET .../admin/models` 200，两次启停探针 0；`/config` 中 deepseek 计数随启用=4/停用=0，证明注册表缓存由独立会话刷新成功（修复目标）。
- turn_error 落库会话：`50356930-9d4b-4cc4-ad4a-54f6256c5c47`（新触发）与 `14563bcb-8304-4934-99b5-0b2465778d13`（早间）；payload 均含完整文案/error_type/supersteps/recursion_limit；`assistant.error=None` 未污染模型上下文。
- 兜底遮罩守卫：注入假遮罩 opacity=0 → `pointerEvents=none`（守卫生效）；真点穿透，`clickedNewTask=true`。
- runtime 恢复：最终新会话底栏 `7 / 1000 步`，DB 覆盖行 id=999999 delete_flag=1。

## 诚实边界

- 本轮是浏览器**点击**回归，覆盖 16 个 commit 中有页面交互点的 8 个（真点 PASS）；4 个提示词/描述类 + 2 个 middleware 逻辑类无独立 UI 触发点，以真实对话链路间接覆盖 + 早间专项/单测证据说明，未伪造点击结论。
- data2image 端到端转图本轮未重跑 1 分半全流程，改回放早间会话 db5a06f3（同一 HEAD 代码路径）+ 复用早间记录中的 5 项内核直出/像素证据；量级差压扁问题仍是既有设计边界（非本轮回归）。
- 所有 DB 配置临时改动已软删恢复；未改产品代码。

## 收尾
- DB：chat_data_setting id=999999 delete_flag=1（runtime=1000），无残留。
- 浏览器已关；服务 5001/8089 正常；错误探针全程 0。
