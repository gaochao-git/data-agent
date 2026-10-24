# 本体全链路点击测试计划

- 日期：2026-10-24
- 适用分支：`feat/ontology-rebuild`
- 目标：用真实浏览器覆盖“后台建模 → 数据绑定 → 对象实例 → 智能体问答 → 异常/性能”的完整链路
- 执行角色：浏览器 QA Agent（只记录问题，不直接改代码/数据）
- 结果回传：Lead 统一排优先级后再修

## 1. 环境

| 项 | 值 |
| --- | --- |
| 仓库 | `/Users/gaochao/work/git_repo/data-agent` |
| 后端 | `http://127.0.0.1:5001`，`CHAT_DATA_ENV=dev` |
| 前端 | `http://127.0.0.1:8090`（8090/8091/8092 先探测） |
| 入口 | `/chatdata?parentComponentName=执法检查` |
| 后台入口 | 左下齿轮 `button[title="后台管理"]` |
| 模型 | `deepseek/deepseek-v4-flash`，不要用默认本地 Sage |
| 数据库 | dev MySQL，应用账号无 DDL |
| 数据域 | `ds_20260116115851_95505`，member `ORG_CODE1..ORG_CODE6` |

## 2. 测试数据基线

```text
本体：
  木星电子商务有限公司（空）
  火星电子商务有限公司（6 个对象类型）

对象类型：
  customer / order_item / product / product_category / sales_order / shipment
  均为 certified
关系：
  order_belongs_to_customer
  order_item_belongs_to_order
  order_item_references_product
  product_belongs_to_category
  shipment_belongs_to_order
Action：
  mark_shipment_delivered
  update_order_status（以实际为准）
Dataset：
  users / orders / shipments / products / categories / order_items
  另有 Pipeline 输出 shipments_delivered_clean
```

当前已知基线：

```text
sales_order 有 shipment_count / recent_7d_shipment_count /
  year_2026_shipment_count / recent_30d_delivered_count 等派生属性
customer 有 order_count / max_amount 派生属性
shipment 有 order_amount 派生属性
```

## 3. 测试总表

| 组 | 覆盖 |
| --- | --- |
| A 导航与权限 | 后台入口、分组、搜索、树状态 |
| B 本体管理 | 新建/编辑/删除/归属 |
| C 对象类型 | 新建/编辑/状态迁移/冻结 |
| D 属性 | 普通属性、主键/标题、枚举、渲染提示 |
| E 派生属性 | 新建、编辑、过滤、时间窗、删除、只读 |
| F 关系类型 | 新建/编辑/状态/删除/级联 |
| G Action | 定义、试运行、执行、审计、开关 |
| H Dataset 绑定 | 选择、映射、一源一对象、刷新 |
| I 对象实例 UI | 列表、筛选、排序、分页、详情、派生值 |
| J 智能体问答 | 明细、过滤、跳转、聚合、派生、错误 |
| K 异常/性能/一致性 | 超时、空结果、非法输入、巡检 |

## 4. 详细用例

### A 导航与权限

**ONT-A01 后台入口**
- 打开入口页，等 networkidle
- 点左下齿轮 `button[title="后台管理"]`
- 期望：后台弹窗打开，左侧导航显示 `数据资产 / 语义层 / 业务本体 / 智能体 / 系统`

**ONT-A02 分组正确**
- 检查：
  - 数据资产：数据源管理、数据集、数据血缘
  - 语义层：语义模型管理、枚举值
  - 业务本体：本体
  - 智能体：大模型管理、定时任务、插件管理
  - 系统：运行配置、历史 SQL
- 期望：本体不再位于数据资产组下

**ONT-A03 搜索与树**
- 在搜索框输对象名，检查筛选
- 展开/收起本体、对象类型、关系类型、动作类型
- 期望：不丢选中态，不重复请求

**ONT-A04 占位节点**
- 点击共享属性/接口/函数
- 期望：灰色不可点，不弹错误

### B 本体管理

**ONT-B01 新建本体**
- 右键根或“本体”分组 → 新建本体
- 输入 API 名/展示名/描述
- 期望：创建成功，树出现新本体

**ONT-B02 编辑本体**
- 右键本体 → 编辑 → 修改展示名/描述
- 期望：保存成功，树和详情同步

**ONT-B03 删除本体**
- 对空本体右键删除
- 对挂对象类型的本体右键删除
- 期望：空本体可删；挂载本体拒绝并提示原因

**ONT-B04 对象归属**
- 编辑对象类型，切换所属本体 → 保存
- 再编辑只改属性、不传归属 → 保存
- 期望：第二次保存不会把对象移出本体

### C 对象类型

**ONT-C01 新建对象类型**
- 右键对象类型分组 → 新建
- 填 API 名/展示名/描述/所属本体
- 添加一个标识属性
- 期望：创建成功，状态 draft

**ONT-C02 新建无标识属性**
- 不勾主键保存
- 期望：明确报错“至少一个标识属性”

**ONT-C03 编辑对象类型**
- 改名/展示名/描述
- 期望：API 名创建后不可改；展示名可改

**ONT-C04 状态迁移**
- draft → experimental → certified
- 期望：certified 前置校验（标识属性 + Dataset 绑定）

**ONT-C05 certified 降级**
- certified → draft
- 期望：弹出二次确认；取消不提交；确认携带 `confirm_destructive=true`

**ONT-C06 删除**
- draft/experimental 删除
- certified 删除
- 期望：certified 禁止删除，只能 deprecated

**ONT-C07 废弃**
- 状态迁移 → deprecated
- 填原因，期限/替代可选
- 期望：废弃后终态，关系级联废弃

### D 属性

**ONT-D01 添加普通属性**
- 添加属性，配置名称/展示名/类型/描述
- 期望：入表，可保存

**ONT-D02 主键/标题**
- 勾选主键、标题
- 期望：至少一个主键；至多一个标题

**ONT-D03 枚举**
- 类型选 enum，选择全局枚举
- 期望：enum_ref 必填校验

**ONT-D04 渲染提示**
- 配置 searchable/sortable/low_cardinality
- 期望：保存后详情正确展示

**ONT-D05 删除/改名属性**
- 删除被关系引用的属性
- 期望：拒绝并说明悬空引用

**ONT-D06 冻结**
- certified 对象编辑既有属性
- 期望：主键/标题/删除受冻结限制

### E 派生属性

**ONT-E01 新建 aggregate 派生**
- 对象属性区 → 添加派生属性
- kind=aggregate、选关系、operator=count
- 期望：保存成功，类型 Integer

**ONT-E02 count_distinct**
- operator=count_distinct，选目标属性
- 期望：保存成功，类型 Integer

**ONT-E03 filters**
- 添加对端过滤：属性/操作符/值
- 保存后查看 summary
- 期望：summary 显示过滤条件；值合法

**ONT-E04 时间窗**
- 时间窗 N 天 + 时间属性
- 期望：只列 Date/DateTime 属性；summary 显示 `近 N 天(...)`

**ONT-E05 编辑派生**
- 点派生行“编辑”，修改 operator/filters/period
- 期望：弹窗回填完整；保存后不新增重复行

**ONT-E06 删除派生**
- 删除派生属性
- 期望：行消失；保存后后端不再返回

**ONT-E07 只读约束**
- 派生属性不能做主键/标题/枚举/渲染提示
- 期望：UI 禁用或后端拒绝

**ONT-E08 派生过滤候选**
- 打开派生过滤属性下拉
- 期望：不出现对端派生属性（无物理列）

### F 关系类型

**ONT-F01 新建**
- 选择 from/to 对象、关联列、基数、展示名
- 期望：保存成功；状态不高于两侧对象

**ONT-F02 FK→PK**
- to 侧关联列必须是标识属性
- 期望：非法列拒绝

**ONT-F03 one_to_one**
- 两侧都选标识属性
- 期望：保存成功

**ONT-F04 状态级联**
- 降级对象类型
- 期望：关系自动降级；升级不自动

**ONT-F05 删除/引用保护**
- 删除被派生属性引用的关系
- 期望：拒绝并提示

### G Action

**ONT-G01 定义**
- 新建 Action，目标对象、参数、set_property 编辑
- 期望：保存成功

**ONT-G02 试运行**
- test-run 填参数
- 期望：返回解析后的编辑，不写库

**ONT-G03 执行**
- 开关 `CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED=true` 时执行
- 期望：写覆盖层 + 审计；对象读取合并新值

**ONT-G04 只读属性**
- Action 编辑派生属性/标识属性
- 期望：拒绝

**ONT-G05 审计**
- 查看 executions
- 期望：who/when/参数/前后值可查

### H Dataset 绑定

**ONT-H01 选择 Dataset**
- 新建/编辑对象，打开 Dataset 下拉
- 期望：显示 layer/origin/机构数；已绑定其他对象的 Dataset 置灰

**ONT-H02 属性映射**
- 为普通属性选择 Dataset 列
- 期望：同名列自动映射；派生属性不参与映射

**ONT-H03 一源一对象**
- 选择已绑定其他对象类型的 Dataset
- 期望：禁用或保存时拒绝

**ONT-H04 刷新**
- 点刷新数据集
- 期望：列表刷新，不丢当前选择

### I 对象实例 UI

**ONT-I01 列表**
- 对象类型 → 对象实例 → 选机构
- 期望：列表、总数、分页正常

**ONT-I02 机构切换**
- ORG_CODE1 → ORG_CODE6
- 期望：数据变化，旧详情清空

**ONT-I03 搜索/筛选/排序**
- 关键词、属性筛选、排序
- 期望：条件生效，分页一致

**ONT-I04 详情**
- 点击行
- 期望：属性/派生值/关系/links 正确

**ONT-I05 派生值**
- 在 detail 与 list 检查派生列
- 期望：有值；不出现“未映射”错误

**ONT-I06 性能**
- sales_order（4 个派生属性）列表
- 期望：< 10s；记录实际耗时

### J 智能体问答

统一检查：

```text
query_mode = ontology
工具 = ontology_tool
不出现 semantic_config/data_analyzing_tool/metric_names
```

**ONT-J01 明细**
- “ORG_CODE1 前 5 个客户”
- 期望：5 条，带派生列

**ONT-J02 过滤**
- “ORG_CODE1 发货单 status=delivered”
- 期望：过滤生效

**ONT-J03 组合 where**
- “status=delivered AND carrier=顺丰”
- 期望：AND 生效

**ONT-J04 跳转**
- “发货单关联订单”
- 期望：get_object/search_around 链路正确

**ONT-J05 聚合**
- “按承运商统计 ORG_CODE1 发货单数量”
- 期望：6 组

**ONT-J06 城市金额**
- “按城市统计 ORG_CODE1 订单金额”
- 期望：shipment group_by receiver_city sum(order_amount)

**ONT-J07 派生**
- “订单的发货单数量”
- 期望：shipment_count

**ONT-J08 错误**
- “查询 not_exist_object”
- 期望：明确不存在，列可用对象

**ONT-J09 多机构**
- “查询前 5 个客户”（不指定机构）
- 期望：先询问机构或确认全部

### K 异常 / 性能 / 一致性

**ONT-K01 空结果**
- 不存在的值过滤
- 期望：空结果 + 说明，不报 500

**ONT-K02 非法属性**
- 过滤/排序不存在属性
- 期望：明确校验错误

**ONT-K03 一致性巡检**
- 点“检查”
- 期望：findings 可读，不误报

**ONT-K04 超时**
- 大量派生属性列表
- 期望：前端提示可控，不白屏

**ONT-K05 控制台**
- 全程 console error = 0，4xx/5xx = 0（预期业务错误除外）

## 5. 清理与恢复

测试结束后必须：

```text
1. 删除测试新建的本体/对象类型/属性/关系/Action
2. 恢复 sales_order / customer / shipment 到火星本体
3. 恢复测试改动的派生属性（如 recent_30d_delivered_count 可保留或删除，记录）
4. 关闭浏览器 profile 并清理 .tmp-chrome-profile
5. 不改生产数据；dev 数据变更需在报告中列明
```

## 6. 报告模板

```text
## 环境
- commit:
- 后端/前端:
- 模型:

## 结果表
| ID | 用例 | 结果 | 证据 | 备注 |

## 缺陷
| ID | 严重度 | 现象 | 复现步骤 | 期望 | 证据 |

## 数据变更
## 未测/阻塞
## 控制台与网络
```

## 7. 已知基线（不要误报）

```text
派生列表 4 个属性约 5-8s，属当前 Hive 查询成本
sales_order.amount 已改 Decimal；城市金额按发货单粒度，可能重复计入
一源一对象类型是 MVP 约束，官方同样要求单 datasource 只属一个对象类型
共享属性/接口/函数为占位
Action 仅 set_property
Object Set 未实现
派生属性排序受对象查询 limit 100 限制
```
