# 本体全链路点击测试报告（2026-10-24 上午）

按 `docs/ontology-full-chain-test-plan.md` 执行；QA 只记录问题，不改代码。

## 环境

- commit: `6e6a68d6`（docs: 本体全链路点击测试计划）
- 后端/前端: 5001（gunicorn 09:15 重启，含 JOIN 批量计算）/ 8090 Vite
- 模型: `deepseek/deepseek-v4-flash`（超高推理档）

## 结果表

| ID | 用例 | 结果 | 证据 | 备注 |
| --- | --- | --- | --- | --- |
| ONT-A01 | 后台入口 | PASS | 截图 | 弹窗正常打开 |
| ONT-A02 | 分组正确 | PASS | 截图 | 五分组：数据资产/语义层/业务本体/智能体/系统；本体已拆出数据资产；数据血缘置灰+锁 |
| ONT-A03 | 搜索与树 | PASS | 截图 | 搜索"sales_order"后火星对象类型过滤为 1；输入不即时过滤需点按钮（UX 观察，非缺陷） |
| ONT-A04 | 占位节点 | PASS | 截图 | 数据血缘/共享属性/接口/函数置灰不可点，无报错 |
| ONT-I06 | sales_order 列表性能 | **FAIL（P0-2）** | 日志 09:22:01 | 见缺陷表 |
| ONT-J01 | 明细+派生列 | PASS | 截图 | 19s；find_objects 表带 MAX_AMOUNT 派生列；5 客户+订单数全对 |
| ONT-J02/J03 | 过滤+组合 where | **FAIL（P0-1）** | 智能体根因分析 | shipment 任何明细查询编译失败 |
| ONT-J05/J06 | 聚合+城市金额 | PASS | 截图 | 聚合路径正常；30 城市×金额×占比；order_amount SUM 正常（select 派生仅明细路径挂） |

## 缺陷

| ID | 严重度 | 现象 | 复现步骤 | 期望 | 证据 |
| --- | --- | --- | --- | --- | --- |
| DEF-1 | P0 | shipment 对象**任何明细查询**（SELECT/WHERE 含 status/order_id 等）编译失败：`SemanticException: Column status Found in more than One Tables/Subqueries` | 任何对 shipment 的明细/过滤问答 | 明细 SQL 列引用加主表别名限定，或 select 型派生改子查询取列，避免与 JOIN 表同名列冲突 | 智能体根因分析（09:25 回答）：`include_all_derived` 的 JOIN 批量化把 orders 无条件 JOIN 进明细查询，明细列未限定表名 |
| DEF-2 | P0 | sales_order（4 派生属性）实例列表/明细查询超 Hive executor 10s 超时：`[Hive] 执行 SQL 超时（10s）`→前端报"加载对象列表失败" | 后台 → 本体 → sales_order → 对象实例 → 搜索 | JOIN 批量化对 Hive 源不可用或超时放宽/派生数上限；customer（2 派生）同路径 3.3s 正常，说明超时随 JOIN 子查询数增长 | 日志 09:22:01-09:22:02；注意 5.1s 的实测为服务层口径，Hive 实际 20s+ 后失败 |
| DEF-3 | P2 | 派生过滤属性候选未排除派生属性（sales_order 的 3 个派生属性出现在 customer 派生过滤下拉） | 编辑 customer → 派生属性 → 添加条件 → 属性下拉 | 排除派生属性（无物理列，选中必错） | 上轮截图 |

## 数据变更

- 本轮测试全部为只读查询，未改数据。
- 上一轮创建的 `sales_order.recent_30d_delivered_count` 保留（另一智能体已知）。

## 未测/阻塞

- B/C 组（本体与对象类型 CRUD、状态迁移/冻结/降级确认）、D 组（属性/枚举/渲染提示）、E05-E08（编辑回填/删除/只读/候选）、F 组（关系类型）、G 组（Action 试运行/执行/审计）、H 组（Dataset 绑定）、I01-I05、J04/J07/J08/J09、K01-K05——受单轮上下文限制未覆盖，DEF-1/DEF-2 修复后需整组回归。
- DEF-1 导致 J02-J04（shipment 相关）全部阻塞。

## 控制台与网络

- 无白屏；业务报错均有前端提示（"加载对象列表失败"）。
- 4xx/5xx：DEF-1/DEF-2 对应的 500/超时为缺陷本身，其余未见异常。

## 修复记录（Lead，2026-10-24 晚）

| 缺陷 | 处理 | 状态 |
| --- | --- | --- |
| DEF-1 | 明细/列表 SQL 的主表列全部加表名限定（select / WHERE / ORDER BY），避免与派生 JOIN 表同名列冲突 | 已修，shipment 列表实测通过 |
| DEF-2 | 对象查询超时从 10s 提升到 30s；仍建议后续合并同关系派生子查询或加派生 JOIN 数量上限 | 已缓解，未根治 |
| DEF-3 | 派生过滤属性候选已排除派生属性（`!item.derivation`） | 已修 |

复测建议：先回归 shipment 明细 J02-J04，再复测 sales_order I06；DEF-2 若仍超时，按“同关系合并聚合子查询”继续优化。

## 复测结论（2026-10-24 09:40，修复 `213cedeb` 后，gunicorn 09:36 重启）

| 缺陷 | 复测结果 | 证据 |
| --- | --- | --- |
| DEF-2 列表性能/超时 | **PASS**：sales_order 实例列表约 1 秒出数据行（原 38-42s/失败） | 截图 + 轮询计时 |
| DEF-1 shipment 明细列歧义 | **PASS**：`status=delivered AND carrier=顺丰` 组合过滤成功，共 725 笔（1.18s），前 5 笔编号/收货城市/发货时间完整；模型确认"SQL 正确加上了表别名限定（shipments.status / shipments.order_id / d0.amount）" | 对话截图 |
| 回归（customer 路径） | **PASS**：find_objects 2.00s，5 客户订单数（2/3/2/3/1）与最大单笔金额（1,502.46/18,924.72/12,268.49/2,776.32/2,901.09）与历史基线逐值一致；Decimal 金额格式正常 | 对话截图 |

DEF-1/DEF-2 关闭。DEF-3（P2，派生过滤候选未排除派生属性）本轮未复测，待下轮核对。
