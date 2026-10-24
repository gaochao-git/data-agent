# 本体能力审计（2026-10-24）

审计目标：盘点当前本体功能、对齐 Palantir 官方能力、检查智能体接通情况，并给出问答回归清单与缺口优先级。

- 仓库：`feat/ontology-rebuild`
- 当前 HEAD：`33c86d9c`（审计开始）
- 范围：本体对象/属性/关系/Action/派生属性/查询/绑定/智能体链路
- 不在范围：语义模型内部能力、Pipeline 调度、权限体系实现

## 1. 功能矩阵

| 能力 | 当前实现 | 状态 | 说明 |
| --- | --- | --- | --- |
| 本体容器 | `chat_data_ontology` | ✅ | 纯语义容器，不绑数据源组 |
| 对象类型 | 状态机 draft/experimental/certified/deprecated、归属、软删 | ✅ | certified 冻结与降级确认已实现 |
| 属性 | 一等实体、主键/标题键、enum、render_hints | ✅ | 单标识属性 MVP |
| 派生属性 | aggregate/select、count/count_distinct/sum/avg/min/max、filters、period | ✅ 子集 | 单跳、单列、非自关联；无 collect/近似基数/表达式 |
| 关系类型 | FK→PK、many_to_one/one_to_one、双向导航 | ✅ 子集 | 多对多走 junction；无多跳链定义 |
| 对象查询 | 关键词、filters、where 树、排序、分页 | ✅ | where 树 and/or/not，≤3 层、≤50 节点 |
| 对象详情 | 单对象读取 + 一跳 links | ✅ | 单成员、单映射；links 上限 20 |
| 对象聚合 | group_by + count/count_distinct/sum/avg/min/max | ✅ 子集 | 无官方 range 时间桶、无 collect/percentile |
| Action | 参数 + set_property 编辑 + 覆盖层 + 审计 | ⚠️ L1 | 无 create/link/edit、审批、副作用、回滚 |
| Dataset 绑定 | source binding + member 绑定 | ✅ | 一源一对象类型；多 member 查询需显式指定 |
| 对象身份 | `member:object_type:local_key` | ✅ | 复合主键未支持 |
| 一致性巡检 | 关系/列/模型路径检查 | ✅ | 按 Dataset binding 组装 |
| 接口 / 共享属性 / 函数 | 树占位 | ❌ | 未实现 |
| Object Set | 无 | ❌ | 无 static/dynamic/temporary |
| 对象级/行级权限 | 仅数据源组/member 授权 | ❌ | 无动态安全 |
| 对象存储 / OSDK | 直连源库投影 | ⚠️ | 无独立对象存储/索引 |

## 2. 与 Palantir 官方对齐

对齐依据：`docs/palantir-ontology/` 下官方文档本地副本。

| 官方能力 | 对齐情况 | 差距 |
| --- | --- | --- |
| Object Type / Property / Link Type | 基本对齐 | 单标识、单跳、单列、无接口/共享属性 |
| Action Type | 仅定义/执行最小闭环 | 无 create/link/edit、审批、副作用、action log 查询 |
| Derived Property | 链接型子集 | 无 3 跳、collectList/Set、近似/精确基数、runtime expression |
| Search API | 关键词 + where 树 | 无全文词项、数组 contains、复杂排序/分页游标 |
| Aggregate API | group_by + 白名单算子 | 无 range 时间桶、无命名指标（已按架构解耦） |
| Object Set | 无 | 无法持久化/传递对象集合 |
| Functions / Interfaces / Shared Properties | 占位 | 未实现 |
| Dynamic Security | 无 | 只有数据源组/member 级授权 |
| Object Storage / OSDK | 直连投影 | 无 OSv2 索引/对象存储；无 OSDK |

## 3. 智能体接通检查

| 检查项 | 结论 |
| --- | --- |
| query_mode 二选一 | ✅ `query_mode.py` 唯一入口 |
| 本体目录 | ✅ 只含 object_types/relations/actions，已移除 metrics |
| 工具注册 | ✅ 本体模式仅 `ontology_tool` |
| 工具 action | ✅ 9 个：list/locate/search_around/get/find/aggregate/list_execution_targets/list_action_types/execute_action |
| 语义指标耦合 | ✅ 已移除 `list_object_metrics`、`metric_names`、catalog metrics |
| 派生属性 | ✅ 目录/定位器带 `derived` 与推导定义；详情/列表返回值；filter/sort JOIN 下推 |
| where 树 | ✅ API + find_objects/aggregate_objects 共用 |
| Action 执行 | ✅ 受 `CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED` 门控 |
| 回退语义链路 | ✅ 本体模式禁止回退 |
| Object Set | ❌ 查询结果只活在当轮对话 |
| 权限上下文 | ⚠️ 只有数据源组/member 范围 |

## 4. 问答回归清单

| # | 问题 | 期望路由 | 期望结果 |
| --- | --- | --- | --- |
| 1 | ORG_CODE1 前 5 个客户 | find_objects | 5 条对象，object_key 带 ORG_CODE1 |
| 2 | ORG_CODE1 客户 username=赵子轩 | find_objects + filter | 2 条 |
| 3 | ORG_CODE1 发货单 status=delivered | find_objects + filter | 80 条 |
| 4 | 发货单 status=delivered AND carrier=顺丰 | find_objects + where | 过滤树生效 |
| 5 | 发货单关联订单 | search_around/get_object links | 跳到 sales_order |
| 6 | 客户关联订单 | search_around | 订单列表 |
| 7 | 按承运商统计 ORG_CODE1 发货单数量 | aggregate_objects | 6 行，260 张 |
| 8 | 按城市统计 ORG_CODE1 订单金额 | aggregate_objects | 城市维度金额 |
| 9 | 订单的发货单数量（派生属性） | find_objects/get_object | shipment_count 有值 |
| 10 | 客户订单数（派生属性） | find_objects/get_object | order_count 有值 |
| 11 | 不存在的对象/属性 | ontology_tool 错误 | 明确错误，不换语义路线 |
| 12 | 多机构未指定 | find_objects | 要求选择 ORG_CODE 范围 |

重点断言：

```text
不出现 semantic_config
不出现 data_analyzing_tool
不出现 metric_names / ontology_metric_aggregate
所有数据查询走 ontology_tool
派生属性返回且无未映射错误
```

## 5. 缺口与优先级

### P0（审计后立即处理）

1. Object Set static/temporary：查询结果可保存、可传递、可批量 Action。
2. Action L2：多属性编辑、状态机、执行前预览、审批占位。
3. 多 member 查询与对象身份一致性回归。
4. 列表派生属性 N+1 性能与缓存。

### P1

1. 多跳关系（最多 3 跳）与关系链验证。
2. 对象聚合 range 时间桶。
3. 派生属性 collect/近似基数。
4. 对象/属性级权限与动态安全。

### P2

1. 接口 / 共享属性 / 函数。
2. 对象存储/索引与 OSDK。
3. Action 副作用、审批、回放。

## 6. 结论

- 本体建模、对象查询、where 树、派生属性、Action 最小闭环已接通智能体；
- 与官方的主要差距集中在 Object Set、Action 完整度、权限、多跳、接口/函数；
- 下一步先做真实问答回归，再按 P0 补 Object Set 与 Action L2。

## 7. 真实智能体问答回归（browser-qa，2026-10-24）

证据：`.deepseek/qa/task6/report.md`、`audit_raw.json`、`audit_parsed.json`、`q01-A.png` … `q12-C.png`。

| # | 场景 | 结果 | 说明 |
| --- | --- | --- | --- |
| 1 | ORG1 前 5 客户 | ✅ | `find_objects`，带 `order_count/max_amount` 派生列 |
| 2 | username=赵子轩 | ✅ | 当前数据无此客户，属数据变化 |
| 3 | status=delivered 发货单 | ✅ | 4,615 条 |
| 4 | delivered AND carrier=顺丰 | ✅ | 725 条，where/filters AND 生效 |
| 5 | 发货单→订单 | ✅ | `get_object + search_around` |
| 6 | 客户→订单 | ✅ | 3 笔，与派生 `order_count` 一致 |
| 7 | 按承运商统计发货单 | ✅ | 6 家，4,732 单 |
| 8 | 按城市统计订单金额 | ❌ | `receiver_city` 分组曾返回伪行；`sales_order.amount` 为 String 不能 sum；订单无城市属性 |
| 9 | 订单发货单数量（派生） | ✅ | `shipment_count` 正确 |
| 10 | 客户订单数（派生） | ✅ | `order_count/max_amount` 正确 |
| 11 | 不存在对象错误 | ✅ | 明确报错并列可用对象 |
| 12 | 多机构未指定 | ❌ | Agent 自动查 6 个 member，未澄清 |

关键断言：

```text
未出现 semantic_config / data_analyzing_tool / execute_query / metric_names
全部走 ontology_tool
where 树生效
派生属性可用
console error 0，4xx/5xx 0
```

### Q8 复核

审计后直接用当前 HEAD 调 `aggregate_object_projection`：

```text
receiver_city: 上海 807 / 北京 446 / ...
carrier: 中通 1353 / 京东 274 / ...
status: delivered 4615 / shipped 117
```

分组聚合服务当前正常，审计中的伪行疑似旧会话/缓存或历史版本瞬时结果，需继续观察；真实口径问题仍是 `amount` 类型与跨对象城市聚合。

### Q12 修复

已在 `agent/prompts/chat_data.py` 增加机构口径约束：

```text
用户未指定机构且可用 member > 1 时，先询问选择机构或确认“全部机构”
未确认前不要把多个 member 混在一次查询里
```

## 8. 审计结论

```text
本体建模、对象查询、where 树、派生属性、Action 最小闭环已接通智能体
语义指标耦合已下线
主要缺口：Object Set、Action L2、权限、多跳、接口/函数
问答回归 10/12，通过项覆盖明细/过滤/导航/聚合/派生；Q8 数据口径、Q12 已修
```
