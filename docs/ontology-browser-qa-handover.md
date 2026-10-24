# 本体（Ontology）浏览器点击测试交接文档

> 交接对象：下一位负责「本体浏览器点击测试」的 Agent
> 目标：用真实浏览器把本体相关链路完整点一遍，**发现问题、记录证据、不要直接改代码或提交**；由主 Agent 统一评估优化。
> 时间基准：2026-10-23 23:50（`feat/ontology-rebuild` 分支）


> 全链路测试清单见 [ontology-full-chain-test-plan.md](ontology-full-chain-test-plan.md)（2026-10-24 版，覆盖导航/建模/派生/关系/Action/Dataset/对象实例/Agent/异常）。

---

## 1. 测试范围（只测这些，别扩散）

1. **本体管理后台**（Admin → 本体）：本体/对象类型/属性/关系类型/动作类型/状态机/一致性检查/图谱。
2. **对象实例工作台**：列表、机构切换、搜索、筛选、排序、分页、详情、关联跳转、图探索。
3. **语义指标关联（本分支新增）**：
   - 对象类型 Overview 的「语义指标」只读区；
   - 「管理关联」弹窗（Dataset ↔ 语义模型关联的列表/新建/编辑/删除）；
   - 智能问数（本体模式）按命名指标查询。
4. **稳定性**：console error、4xx/5xx、重复请求、loading 卡死、toast 错误、切机构旧数据残留、返回竞态。

不做：Pipeline DAG 编辑、DatasetAdminPanel 其他功能、认证/权限改造、后端 schema 变更。

---

## 2. 环境与运行时（先读，避免踩坑）

| 项 | 值 |
|---|---|
| 仓库 | `/Users/gaochao/work/git_repo/data-agent`，分支 `feat/ontology-rebuild` |
| 后端 | `http://127.0.0.1:5001`（gunicorn，`CHAT_DATA_ENV=dev`，进程由其他 Agent 启动；**不要随意重启**） |
| 前端 Vite | 8090 / 8091 / 8092 均可用，统一用 `http://127.0.0.1:8090` |
| 入口 | `http://127.0.0.1:8090/chatdata?parentComponentName=%E6%89%A7%E6%B3%95%E6%A3%80%E6%9F%A5`（`parentComponentName=执法检查`，**不带该参数数据源会被过滤为空，无法发消息**） |
| 登录 | 无登录页；开发用户内置 `agents:chatData:admin` 权限 |
| Python | `/Users/gaochao/work/miniconda3/envs/fxcjg/bin/python3.12`（后端/单测都用这个解释器） |
| 浏览器 | 系统 Chrome：`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`（pyppeteer 2.0 可用） |
| 数据库 | dev MySQL（配置在 `server/conf/.env-dev`，应用账号 `fxcjg_rw` **无 CREATE 权限**，不要尝试 DDL） |
| 工作产物 | `.deepseek/browser-profile/`（已 ignore，测试后删除）；截图放 `.deepseek/qa/` |

### 2.1 后端健康检查

```bash
curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5001/openapi.json   # 期望 200
```

### 2.2 如果后端挂了（尽量先找主 Agent，不要自己 HUP）

- **不要**对 gunicorn 发 `SIGHUP`：本机 macOS 会因 fork/ObjC 崩溃（实测会连续 SIGABRT）。
- 如需恢复，使用“杀掉进程 + 分离启动”（其他 Agent 的启动方式）：

```bash
cd /Users/gaochao/work/git_repo/data-agent/server
CHAT_DATA_ENV=dev nohup /Users/gaochao/work/miniconda3/envs/fxcjg/bin/gunicorn main:app -c gunicorn.conf.py > /tmp/backend_qa.log 2>&1 &
```

### 2.3 直接调后端 API 的公共头

```bash
-H 'X-Service-Token: local-dev-service-token'
-H 'X-User-Id: 10001'
-H 'X-Username: 5rWL6K+V55So5oi3'
```

前端页面内 fetch 无需手写这些头（Vite proxy 会注入），路径前缀为：

```
/dev-api/agents/api/chatdata/...
```

---

## 3. 浏览器自动化脚手架（仓库里已有，直接复用）

目录：`.deepseek/qa/`（已 ignore，可随意增删）

| 文件 | 作用 |
|---|---|
| `lib.py` | 启动 Chrome、console/网络监听、截图、等待文本 |
| `helpers2.py` | 打开后台→本体、展开树节点、按文本点树节点 |
| `helpers3.py` | 对象实例工作台作用域操作：读列表/详情、机构切换、筛选、排序、分页 |
| `s6`~`s33_*.py` | 历史验证脚本，可直接抄 |
| `54/58/61*.png` | 关联管理 UI 证据示例 |

最小启动代码：

```python
import asyncio, sys
sys.path.insert(0, '/Users/gaochao/work/git_repo/data-agent/.deepseek/qa')
from lib import *
from helpers2 import *

async def main():
    browser = await new_browser()          # headless Chrome + .deepseek/browser-profile
    page = await browser.newPage()
    attach(page)                            # console error/pageerror/4xx/5xx 自动收集
    await page.goto(BASE + '/chatdata?parentComponentName=%E6%89%A7%E6%B3%95%E6%A3%80%E6%9F%A5',
                    {'waitUntil': 'networkidle2', 'timeout': 60000})
    await asyncio.sleep(2)
    # ...测试...
    await screenshot(page, '70-xxx')
    print('console:', console_errors)
    print('bad:', bad_responses)
    await browser.close()

asyncio.run(main())
```

结束后必须清理：

```bash
pkill -f 'deepseek/browser-profile' 2>/dev/null; rm -rf .deepseek/browser-profile
```

---

## 4. 页面导航路径（点击顺序）

### 4.1 进入后台

1. 打开入口页，等 `networkidle2` + 2s。
2. 点左下角齿轮：`button[title="后台管理"]`（即使左下角显示“请先配置数据源组”也可点）。
3. 后台弹窗左侧导航点 `本体`。

### 4.2 本体树

结构：`本体（根）→ 本体现有 2 个（木星/火星）→ 对象类型/关系类型/动作类型/共享属性/接口/函数`。

- 「共享属性 / 接口 / 函数」是**占位灰节点**（规划中，点不进详情），不是 bug。
- 展开：点节点行内的 `.el-tree-node__expand-icon`；按文本匹配时建议先去掉空白（`helpers2.expand_icon` 已处理）。
- 火星电子商务有限公司 = 当前有对象类型的本体；展开 `对象类型 6` 后点 `customer / shipment / sales_order ...`。

### 4.3 对象类型 Overview

右侧默认 `Overview` 页签，可验证：

- 头部：对象类型名、展示名、状态标签、本体/更新时间/修改人/ID/描述；
- 属性清单（主键/标题/类型/描述、渲染提示）；
- 动作类型列表（`查看` 打开 Action 详情）；
- 关系类型（图形/列表切换）；
- 对象数据源（Dataset、机构数、属性映射数）；
- **语义指标**（本分支新增，见 §5.3）。

### 4.4 对象实例

点 `对象实例` 页签（`ObjectInstanceExplorer.vue`），左侧列表 + 右侧详情：

- 机构下拉：`ORG_CODE1`..`ORG_CODE6`（customer/shipment 有 6 个机构）；
- 搜索框 placeholder：`搜索标题键或主键`；
- 筛选 popover：「筛选 → 添加条件 → 属性/操作符/值 → 应用」；
- 排序下拉：`排序`（选中后出现升/降序，再点「应用」）；
- 分页：`上一页 / 下一页`，页尾 `共 N 个 · 第 P 页`；
- 详情：点行 → 右侧属性表 + 关联对象链接；点关联对象 → 下钻；「返回」回上一跳；
- 图探索：`属性 / 图探索` 切换。

### 4.5 对象类型右键菜单（写操作入口）

在树中**右键对象类型节点**：

- `编辑`、`状态迁移`、`删除`（deprecated 时置灰）；
- 右键「组织类型」分组 → 新建对象类型；右键本体节点 → 编辑/删除本体、新建关系类型等。

状态迁移弹窗：`draft / experimental / certified / deprecated`。**certified 降级（→ experimental/draft）会弹二次确认**，确认后才会携带 `confirm_destructive=true`。

### 4.6 语义模型关联管理（本分支新增）

路径：对象类型 Overview → 滚动到 **语义指标** → 点右侧 **管理关联**。

弹窗 `Dataset ↔ 语义模型关联`：

- 列表列：语义模型（名称+ID）、数据源组、语义表、指标范围数、状态（草稿/启用）、操作；
- `新建关联` / `编辑` / `删除`；
- 新建/编辑表单：数据源组、语义模型（显示指标数）、语义表名、指标范围（多选，空=全部）、列映射（JSON，`{}`=自动同名对照）、状态（草稿/启用，**默认启用**）。

### 4.7 智能问数（本体模式）

- 左侧「新建任务」或直接输入框输入问题；
- 模型选择器：输入框右下角 `title="模型设置"` → `模型` → `deepseek/deepseek-v4-flash`（**默认本地 Sage 模型可能因内存守卫 OOM，务必切 deepseek**）；
- 发送后等待 `复制` 按钮出现且停止按钮消失（最长约 3 分钟）；
- 通过会话详情 API 核对工具链（见 §8.2）。

---

## 5. 测试用例清单（按优先级）

> 每条都要记录：实际结果、console error、4xx/5xx、截图、复现步骤。发现异常**只记录**，不改代码。

### 5.1 P0：本体管理后台

| # | 用例 | 步骤 | 期望 |
|---|---|---|---|
| 1 | 后台/本体加载 | 齿轮 → 本体 | 树正常；console/网络无异常；无重复请求爆发 |
| 2 | 对象类型详情 | 展开火星 → 对象类型 6 → shipment | 属性 8 个、主键/标题正确、关系 1 条、动作 1 个、Dataset=shipments/6 机构/8 映射 |
| 3 | 状态过滤/搜索 | 顶部状态筛选、搜索对象名 | 结果正确，无残留旧结果 |
| 4 | 对象类型编辑（非破坏） | 右键 → 编辑 → 改描述 → 保存 | 保存成功、树/详情刷新、无 console 错误 |
| 5 | 破坏性变更确认 | 编辑时改主键/标题键 | 弹二次确认；取消不提交；确认才生效 |
| 6 | certified 冻结 | 对 certified 对象删除属性 / 改主键 | 被拒绝并给明确提示（40074 等） |
| 7 | certified 禁删 | 右键 certified 对象 → 删除 | 前端拦截或后端拒绝（不允许） |
| 8 | **certified 降级确认（本轮新增）** | 右键 certified 对象 → 状态迁移 → draft/experimental | 弹「certified 降级确认」；取消不提交；确认才生效；关系类型级联降级 |
| 9 | deprecated 终态 | 状态迁移 → deprecated（必填原因） | 成功后不可逆、节点置灰、不可编辑/删除 |
| 10 | 关系类型 | 新建/编辑关系（FK→对方标识属性、基数只允许 1:1/N:1） | 非法列/基数被拒绝；状态随两端较低者 |
| 11 | 动作类型 | 对象详情「动作类型」查看；列表/试运行/执行 | 需 env 开关；未开启时明确报错，不能静默失败 |
| 12 | 一致性检查/图谱 | 本体行「检查」「图谱」 | 弹窗正常、无报错、无卡死 |
| 13 | 占位节点 | 点 共享属性/接口/函数 | 灰节点不可点，**不是 bug** |
| 14 | 数据源绑定-一源一对象类型 | 给 customer 绑定 shipments 的 Dataset（可走 API） | 返回 40072「已被对象类型 shipment 绑定」，不写入 |
| 15 | 数据源绑定-列映射校验 | 绑定时选不存在的属性/列 | 明确拒绝；不产生半成功数据 |

### 5.2 P1：对象实例工作台

| # | 用例 | 步骤 | 期望 |
|---|---|---|---|
| 16 | customer 列表/机构 | 对象实例 → ORG_CODE1 / ORG_CODE6 | 71 / 76 条；姓氏数据不同；object_key 带 member 前缀 |
| 17 | shipment 列表/机构 | 同上 | 260 / 265 条；key 如 `ORG_CODE1:shipment:ORG1-SHIP000001` |
| 18 | 搜索 | customer 搜“赵”；shipment 搜 `ORG6-SHIP000010`（先切 ORG6） | 条数/payload 正确 |
| 19 | 分页 | 下一页/上一页 | offset 正确、页尾 `共 N 个 · 第 P 页` |
| 20 | 筛选 | 用户名 = 赵子轩（customer，ORG1） | 2 条；popover 选择属性/操作符**不应关闭** |
| 21 | 排序 | 用户名 降序 → 应用 | payload `sort` 正确、顺序正确 |
| 22 | 详情 | 点行 | 属性中文 label 正确、object_key 带 member、links 展示 |
| 23 | 关联跳转/返回 | shipment 详情点关联订单 → 返回 | 跳到 sales_order；返回回 shipment；member 不丢、object_key 不串机构 |
| 24 | 返回竞态 | 点关联后**立即**点返回（读接口约 3~4s） | 最终仍停在原对象，不被迟到响应覆盖 |
| 25 | 机构切换清残留 | 打开某对象详情 → 切 ORG6 | 右侧详情清空（显示“从左侧列表选择一个对象”），不残留旧机构数据 |
| 26 | 图探索 | 切图探索、点关联节点 | 图渲染、节点可点、无报错 |
| 27 | 加载/空态/报错 | 禁用/切机构/搜不存在关键词 | loading 不卡死；空态/错误提示正常 |
| 28 | 控制台/网络 | 全程 | 无 console error / 4xx/5xx；无重复请求风暴 |

### 5.3 P1：语义指标只读区（对象类型 Overview）

| # | 用例 | 步骤 | 期望 |
|---|---|---|---|
| 29 | 指标区展示 | shipment Overview 滚到「语义指标」 | 5 个指标：原子×2、派生×2、复合×1，均「可执行」+ 描述 |
| 30 | 无关联态 | 删掉关联后刷新对象类型 | 显示「未关联语义指标」/原因，不报错 |
| 31 | 不可执行态 | 把关联状态改草稿后刷新 | 指标显示「不可执行」，tooltip 给出原因 |
| 32 | 管理关联入口 | 点指标区右侧「管理关联」 | 打开弹窗，列出当前关联 |
| 33 | 管理关联-编辑 | 编辑 → 校验回显（数据源组/模型/语义表/指标范围/列映射/状态） → 保存 | 回显完整；保存成功；指标区自动刷新 |
| 34 | 管理关联-新建 | 新建 → 选 `shipments基础模型` → 保存（默认启用） | 保存成功；指标区恢复可执行 |
| 35 | 管理关联-删除 | 删除 → 二次确认 | 删除成功；指标区变未关联；**测完务必重建**（见 §7） |
| 36 | 管理关联-错误路径 | 故意填不存在的维度/指标/跨组表 | 后端友好报错，不产生脏数据 |
| 37 | 边界 | 未绑 Dataset 的对象类型（如木星下的空对象） | 「管理关联」置灰/提示，不报错 |

### 5.4 P1：智能问数（本体模式 + 命名指标）

| # | 用例 | 步骤 | 期望 |
|---|---|---|---|
| 38 | 派生指标 | 提问：`按承运商统计 ORG_CODE1 的 2026年发货量（用语义指标）` | 走 `ontology_tool`：`list_object_metrics` → `aggregate_objects(metric_names=[...])`；结果表带 **Ontology** 标签；执行对象=财付通；`query_mode=ontology_metric_aggregate` |
| 39 | 原子+复合指标 | 提问：`按承运商统计 ORG_CODE1 的发货单数量和平均每单发货单数（用语义指标）` | 260 张/6 行、复合=1.00；口径文字引用语义模型；**不得**走 `data_analyzing_tool`/semantic_config |
| 40 | 越界/不可执行 | 把关联改草稿后再问 | 明确报「关联未启用」，**不得**回退属性聚合或语义查询 |
| 41 | 派生混查限制 | 一次问「发货单数量 + 近7日发货量」 | 明确报「派生指标暂不支持与其他指标同时聚合」（已知 MVP 限制） |
| 42 | 对象模式路由 | 普通明细问题（如“ORG_CODE1 前 5 条发货单”） | 走 `find_objects`，不被错误路由到语义模型 |
| 43 | 目录校验 | 问数前读 `_ontology_catalog.yaml`（可在工作区查看） | `object_types[].metrics` 只读指标索引存在；不含指标表达式 |

### 5.5 P2：稳定性/体验

| # | 用例 | 期望 |
|---|---|---|
| 44 | 连续快速操作 | 快速切机构/搜索/翻页，最终数据与最后一次操作一致（请求序号保护） |
| 45 | 重复点击保存/删除 | 不产生重复数据、不出现双 toast 残留 |
| 46 | 弹窗层级 | 后台弹窗（z-index）、嵌套 dialog、popover、下拉不被其他容器盖住 |
| 47 | 中文/空数据/长文本 | 不溢出、不空白、不报错 |
| 48 | 刷新/A页面重进 | 状态一致（无内存脏缓存） |
| 49 | 多入口回归 | 对象实例/管理关联/问数三处对同一关联的“可执行”口径一致 |

---

## 6. 当前测试数据基线（2026-10-23 23:50）

### 6.1 机构与对象类型

- 机构：`ORG_CODE1`（财付通）…`ORG_CODE6`（货拉拉），同一数据源组 `ds_20260116115851_95505`（hive-localhost）。
- 对象类型（火星电子商务有限公司）：`customer`、`order_item`、`product`、`product_category`、`sales_order`、`shipment`。
- 计数基线：customer ORG1=71 / ORG6=76；shipment ORG1=260 / ORG6=265。

### 6.2 shipment 的指标与关联（本轮新增）

- Dataset：`shipments`（6 机构成员绑定）。
- 语义模型：`shipments基础模型`（`sc_20261023150328_fccec4469b34`），5 个指标：
  - `发货单数量`（原子，COUNT shipment_id）
  - `关联订单数`（原子，COUNT DISTINCT order_id）
  - `近7日发货量`（派生，last_n_days=7，shipped_at；当前数据较旧，可能 0 行）
  - `2026年发货量`（派生，between 2026-01-01~2027-01-01；基线 16）
  - `平均每单发货单数`（复合，{发货单数量}/{关联订单数}；基线 1.00）
- 关联：`Dataset shipments × shipments基础模型`，业务域 `ds_20260116115851_95505`，状态 **active**。
- 若关联被删/改草稿，按 §7 恢复。

### 6.3 部署开关（若出现「未启用」类报错先核对）

- `CHAT_DATA_ONTOLOGY_INJECTION_ENABLED=1`：本体模式总闸。
- `CHAT_DATA_ONTOLOGY_OBJECT_READ_ENABLED=1`：对象读取/聚合/指标执行。
- `CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED`：Action 执行（未开时报错属预期）。

---

## 7. 数据安全与恢复（务必遵守）

1. **只读优先**：能不写就不写。写操作只动“可恢复”的对象。
2. **不要删除既有对象/本体/关系/动作**；不要给 `customer/shipment` 等认证对象改主键、状态。
3. 新建测试对象统一命名：`qa_<日期>_<用途>`（如 `qa_1023_bind_test`），测完删除。
4. certified 对象**不要降级**；确需验证降级确认，可在测试对象（draft/experimental）上走，或只验证“取消”分支。
5. **语义模型关联可安全删除/重建**，但必须恢复：

   > 打开 shipment Overview → 语义指标 → 管理关联 → 新建关联：
   > 数据源组 `ds_20260116115851_95505`、语义模型 `shipments基础模型`、
   > 语义表 `shipments`、指标范围全选、列映射留 `{}`、状态 **启用** → 保存。

   恢复后用 `GET /admin/meta/ontology/object-types/shipment/metrics` 确认 5 个指标 `executable_in_scope=true`。
6. **不要改语义模型指标定义**（`shipments基础模型`），删除指标会让“可执行指标”变化。
7. 不要 `git commit` / `git add`；不要改其他正在开发中的模块（DatasetAdminPanel、Pipeline DAG 等）。
8. 应用 DB 账号无 DDL 权限；不要尝试建表/改表。

---

## 8. 取证与核对工具

### 8.1 截图

命名：`<序号>-<场景>.png`，放 `.deepseek/qa/`；每个失败用例至少 1 张（含 console/network 证据更好）。

### 8.2 核对 Agent 工具链（关键）

跑完问数后，从会话详情里看工具调用：

```bash
curl -s -H 'X-Service-Token: local-dev-service-token' -H 'X-User-Id: 10001' -H 'X-Username: 5rWL6K+V55So5oi3' \
  'http://127.0.0.1:5001/api/chatdata/conversations/<conversation_id>' \
| /Users/gaochao/work/miniconda3/envs/fxcjg/bin/python3.12 - <<'PY'
import json,sys
d=json.load(sys.stdin)['data']
for m in d['messages']:
    for tc in (m.get('tool_calls') or []):
        print(tc.get('tool_name') or tc.get('name'), json.dumps(tc.get('args') or {}, ensure_ascii=False)[:240])
    c=str(m.get('content') or '')
    if c.startswith('{"type": "query_result"'):
        meta=json.loads(c).get('meta') or {}
        print('QUERY_MODE', meta.get('query_mode'), '| members', meta.get('member_names'), '| total', meta.get('total_count'))
PY
```

期望：出现 `ontology_tool` 的 `list_object_metrics` / `aggregate_objects(metric_names)`，且 `query_mode` 为 `ontology_metric_aggregate`；不应出现 `data_analyzing_tool`/`execute_query`。

### 8.3 关键接口（页面内 fetch 或 curl）

```
GET  /api/chatdata/admin/meta/ontology/object-types/shipment/metrics?datasource_group_id=ds_20260116115851_95505
GET  /api/chatdata/admin/meta/datasets/shipments/semantic-links?datasource_group_id=ds_20260116115851_95505
PUT  /api/chatdata/admin/meta/datasets/{dataset}/semantic-links/{semantic_config_id}
DEL  /api/chatdata/admin/meta/datasets/{dataset}/semantic-links/{semantic_config_id}
GET  /api/chatdata/admin/meta/ontology/object-types/{name}
POST /api/chatdata/admin/meta/ontology/object-types/{name}/status      # body: {status, confirm_destructive?, deprecation_reason?}
```

### 8.4 日志

- 后端日志：`server/logs/data-agent_app.log`（按 ERROR 搜）；启动日志可能在 `/tmp/backend_*.log`。
- 浏览器侧：`lib.attach(page)` 已收集 `console_errors / bad_responses / requests_log`。

### 8.5 常用 curl 探测

```bash
for p in 8090 5001; do echo -n "$p:"; curl -s -m 3 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:$p/; done
curl -s http://127.0.0.1:5001/openapi.json | python3 -c "import json,sys;print([p for p in json.load(sys.stdin)['paths'] if 'metrics' in p or 'semantic-links' in p])"
```

---

## 9. 已知基线 / 不要误报

1. `共享属性 / 接口 / 函数` 是灰色占位节点（规划中），点击无反应属预期。
2. **一源一对象类型**：同一 Dataset 只能绑一个对象类型（Palantir 规则），复用会返回 40072，属预期。
3. **派生指标带时间窗时不能与其他指标混查**：会明确报错，属 MVP 限制。
4. 指标与对象 Dataset 必须**同表**；跨数据集/跨表指标标记不可执行。
5. `certified` 对象降级需要二次确认（新行为）；`certified→deprecated` 由必填原因兜底。
6. Action 仅支持 `set_property`；Action 执行受 env 开关控制，未开启时报错属预期。
7. 对象读取 MVP：单标识属性、单表、单 member；自关联/多列关系 links 跳过。
8. 默认本地模型 `Sage-A1.5-OptQ-4bit` 可能因 oMLX 内存守卫报错（环境问题，不是业务 bug），测试问数请选 `deepseek/deepseek-v4-flash`。
9. 后端不能 `SIGHUP`（macOS fork 崩溃），要重启用 §2.2 的方式。
10. 已知非阻塞项：打开对象实例时 `GET object-types` / `GET object-types/{type}` 各有一次重复请求（树+详情面板各一次），可记录但不要求修复。

---

## 10. 报告模板（交回主 Agent）

```markdown
# 本体浏览器点击测试报告（2026-10-23）

## 结论
- 通过 X / 失败 Y / 阻塞 Z
- 建议优先修复：P0 …；P1 …

## 场景结果表
| # | 场景 | 结果(通过/失败/阻塞) | 证据(截图/日志) | 复现步骤 | 严重级别 | 疑似文件 | 建议 |
|---|---|---|---|---|---|---|---|

## 问题详情（每个失败一条）
### Q1 <标题>
- 严重级别：P0/P1/P2
- 前置条件：...
- 复现步骤：
  1. ...
- 实际结果：
- 期望结果：
- 证据：console / network / 截图路径
- 疑似原因与建议修复点（只写建议，不直接改）

## 稳定性
- console error：N（附前 5 条）
- 4xx/5xx：N（附 URL）
- 重复请求：
- loading 卡死/残留数据：
- toast 异常：

## 已测范围 / 未测范围
- 已测：...
- 未测（及原因）：...

## 数据改动与恢复
- 新建/删除的关联：...
- 测试对象：...
- 是否已恢复 baseline（§6）：是/否
```

---

## 11. 建议执行顺序（约 2~3 小时）

1. 环境探活（§2）→ 打开后台/本体（§4.1-4.2）→ P0 用例 1-9。
2. 对象实例 P1 用例 16-28（customer/shipment 各一轮）。
3. 语义指标只读区 + 关联管理（用例 29-37），结束务必恢复 active 关联。
4. 智能问数（用例 38-43）——选 deepseek 模型；必要时用会话 API 核对工具链。
5. 稳定性 P2 + 补充边界；截图归档、按 §10 输出报告。
6. 清理：`pkill -f 'deepseek/browser-profile'; rm -rf .deepseek/browser-profile`。

---

## 12. 附：常用选择器速查

| 元素 | 选择器/文本 |
|---|---|
| 后台入口 | `button[title="后台管理"]` |
| 后台左侧导航 | `.el-dialog button` 文本 `本体` |
| 树节点 | `.el-tree-node__content`（文本去空白匹配） |
| 展开图标 | `.el-tree-node__expand-icon` |
| 对象类型右键菜单 | 树节点 `contextmenu` → `.ctx-menu`/文本 `编辑`/`状态迁移`/`删除` |
| 对象实例页签 | `button` 文本 `对象实例` |
| 机构下拉 | 对象实例左栏 `.el-select` 第一个 |
| 搜索框 | `input[placeholder*="搜索标题键"]` |
| 筛选 | 左栏按钮 `筛选`；popover `.el-popover` |
| 详情/返回 | 右侧 `返回` 按钮 |
| 语义指标区 | `span` 文本 `语义指标`；按钮 `管理关联` |
| 关联弹窗 | `.el-dialog` 文本 `Dataset ↔ 语义模型关联` |
| 确认框 | `.el-message-box` 按钮 `删除`/`确认降级` |
| 模型选择 | 输入框右下 `button[title="模型设置"]` → `模型` → `deepseek/deepseek-v4-flash` |

> 选择器优先用**可见文本**，避免依赖易变 class；点按钮用 `dispatchEvent(new MouseEvent('click',{bubbles:true}))` 更稳（Vue/EP 组件兼容）。
