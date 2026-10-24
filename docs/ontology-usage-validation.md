# 本体使用验证记录

- 日期：2026-10-24
- 范围：只读、多机构
- 分支：`feat/ontology-rebuild`
- 环境：后端 5001 / Hive 已重启 / 6 个 member
- 原则：不执行写操作，不改代码

## 1. 验证范围

```text
customer / sales_order / shipment
ORG_CODE1..ORG_CODE6
对象列表
对象详情
派生属性
一跳关系
多机构隔离
聚合口径
异常成员
```

## 2. 对象数量

| 机构 | customer | sales_order | shipment |
| --- | ---: | ---: | ---: |
| ORG_CODE1 | 1,200 | 5,000 | 4,732 |
| ORG_CODE2 | 800 | 3,000 | 2,804 |
| ORG_CODE3 | 600 | 2,200 | 2,049 |
| ORG_CODE4 | 300 | 1,200 | 1,082 |
| ORG_CODE5 | 120 | 600 | 540 |
| ORG_CODE6 | 900 | 4,000 | 3,742 |
| 合计 | 3,920 | 16,000 | 14,949 |

结论：每个机构返回独立数量，未出现跨机构串数。

## 3. 对象身份

每个机构的首个对象 key：

```text
ORG_CODE1:customer:1000001
ORG_CODE2:customer:2000001
...
ORG_CODE6:customer:6000001
```

结论：

```text
object_key 带 member 前缀
同本地主键在不同机构不会混淆
```

## 4. 对象详情与派生属性

| 机构 | 客户 | 订单数 | 最大单笔金额 | 关系 |
| --- | --- | ---: | ---: | --- |
| ORG_CODE1 | 杜佑 | 2 | 1,502.46 | order_belongs_to_customer |
| ORG_CODE2 | 罗熙丽 | 3 | 8,437.07 | order_belongs_to_customer |
| ORG_CODE3 | 钱佑华 | 5 | 9,051.98 | order_belongs_to_customer |
| ORG_CODE4 | 杜雨 | 2 | 589.72 | order_belongs_to_customer |
| ORG_CODE5 | 嘉兴鼎盛贸易有限公司 | 2 | 28,820.88 | order_belongs_to_customer |
| ORG_CODE6 | 贾语桂 | 8 | 701.55 | order_belongs_to_customer |

结论：

```text
派生属性按机构正确计算
一跳关系正常
Decimal 金额格式正常
```

## 5. 聚合口径

按机构统计发货单：

```text
ORG_CODE1  4,732
ORG_CODE2  2,804
ORG_CODE3  2,049
ORG_CODE4  1,082
ORG_CODE5    540
ORG_CODE6  3,742
合计      14,949
```

与对象列表数量完全一致。

## 6. 多机构隔离与异常

| 场景 | 结果 | 判定 |
| --- | --- | --- |
| 用 ORG_CODE1 读 ORG_CODE2 的主键 | `对象不存在` | PASS |
| 列表传两个 member | `Dataset 有多个 member，请指定 member_datasource_ids` | PASS（文案可优化） |
| 列表传未知 member | `Dataset 没有可用的 member binding` | PASS |
| 列表不传 member | 要求指定 member | PASS |

## 7. 性能观察

```text
列表查询：1.7s ~ 4.6s
详情查询：5.2s ~ 7.8s
聚合查询：0.65s ~ 1.5s/机构
```

详情偏慢，原因是详情页每个派生属性各发一次对端查询；当前缓存只减少元数据，不减少 SQL 次数。

## 8. 发现的问题

### P2 多 member 文案容易误导

用户传了两个 member，提示：

```text
Dataset 有多个 member，请指定 member_datasource_ids
```

用户已经指定了两个，应该提示：

```text
当前对象查询一次只能指定一个 member
```

### P2 详情派生属性仍逐属性查询

```text
N 个派生属性 = N 次 SQL
```

已做元数据缓存，但查询次数仍是 N。

### P3 多机构聚合没有统一入口

当前需要按 member 逐个执行后再汇总，智能体侧由工具循环，管理端没有统一“跨机构汇总”API。

## 9. 未覆盖

```text
多机构智能体澄清行为
Action 写操作
对象类型编辑
Dataset 绑定变更
权限用户
```

## 10. 结论

```text
多机构只读查询：PASS
对象身份隔离：PASS
派生属性：PASS
一跳关系：PASS
聚合口径：PASS
```

当前多机构本体查询可以进入业务试用，但详情性能和多 member 文案需要记录为改进项。

## 11. 多机构智能体验证

### 未指定机构

提问：

```text
查询前 5 个客户，显示用户名和订单数
```

智能体行为：

```text
读取本体目录
list_execution_targets 拿到 6 个机构
发起澄清，不再自动查全部机构
```

澄清 UI：

```text
机构范围：财付通/支付宝/平安付/拉卡拉/银商务/货拉拉
每个对象查询只能指定一个机构
1/2
```

判定：**PASS**，未指定机构时会先澄清。

### 选择机构后的追问

澄清问题 2：

```text
按订单数从高到低 / 从低到高
```

判定：**PASS**，排序口径也会确认。

### 发现并修复的问题

选择“财付通 + 订单数从高到低”后，智能体结果中：

```text
ORDER_COUNT = -
MAX_AMOUNT = -
```

复现：

```text
customer 列表 sort=order_count desc
前 5 条派生列全部 NULL
```

根因：

```text
LEFT JOIN 无对端行时 COUNT/SUM 为 NULL
Hive ORDER BY DESC 默认 NULL 在前
→ 先返回无订单客户
```

修复：

```text
count / count_distinct / sum 派生表达式改为 COALESCE(..., 0)
```

复测 API：

```text
秦静军 361 单
武怡 90 单
田思丽 85 单
汤文 55 单
傅墨刚 51 单
```

判定：**PASS**。

### 未完成

智能体修复后的同一问题端到端复测未完成：

```text
验证过程中后端重启
页面提示“后端接口连接异常”
```

API 已复测正确，但智能体自然语言回答需下一轮补测。

## 12. 验证结论

```text
对象数量：PASS
对象身份：PASS
详情/派生属性：PASS
一跳关系：PASS
聚合：PASS
未指定机构澄清：PASS
派生排序：修复后 PASS（API 级）
智能体端到端复测：待补
```

## 13. 智能体端到端复测（2026-10-24 12:05）

### 财付通前 5 客户（派生排序）

问题：

```text
继续，按订单数从高到低取财付通前 5 个客户，显示用户名和订单数
```

结果：

| 排名 | 客户 | 订单数 |
| ---: | --- | ---: |
| 1 | 秦静军 | 361 |
| 2 | 武怡 | 90 |
| 3 | 田思丽 | 85 |
| 4 | 汤文 | 55 |
| 5 | 傅墨刚 | 51 |

判定：

```text
派生排序 PASS
零订单客户不再排在前列
模型明确说明“空值按 0 处理”
```

### 跨机构追问

问题：

```text
再查支付宝前 3 个客户，按订单数从高到低
```

结果：

| 排名 | 客户 | 订单数 |
| ---: | --- | ---: |
| 1 | 杜思静 | 186 |
| 2 | 唐佳勇 | 119 |
| 3 | 陈琪 | 88 |

对象 key 全部为 `ORG_CODE2:` 前缀，未串机构。

判定：

```text
跨机构追问 PASS
对象身份隔离 PASS
```

### 复测中补修的问题

派生属性数值过滤此前生成：

```text
d.value > '0'
```

Hive 严格类型检查报错：

```text
Unsafe compares between different types
```

修复：

```text
Integer / Decimal 属性过滤值不再加引号
数值字面量做格式校验
字符串属性仍走转义
```

复测：

```text
order_count > 0 且按 order_count 降序
→ 秦静军 361 / 武怡 90 / 田思丽 85 / 汤文 55 / 傅墨刚 51
```

## 14. 最终验证结论

```text
多机构对象查询：PASS
对象身份隔离：PASS
派生属性：PASS
派生排序：PASS
派生数值过滤：PASS
未指定机构澄清：PASS
跨机构智能体追问：PASS
控制台：无异常
```

当前只读多机构使用验证通过。
