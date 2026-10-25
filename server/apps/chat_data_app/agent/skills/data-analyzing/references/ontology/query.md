# 本体对象查询

## 适用

```text
具体对象 / 对象列表 / 对象详情
对象属性过滤 / 排序 / 分页
对象聚合（group_by + count/count_distinct/sum/avg/min/max）
对象派生属性
```

## 前置

1. 先调 `ontology_tool` 的 `list_object_types` 确认当前业务域对象类型。
2. 需要机构范围时调 `list_execution_targets`，`member_datasource_ids` 只能从返回清单选择。
3. `find_objects` / `aggregate_objects` 支持一次传多个 member：工具按机构并行下发 SQL，返回 `results[]`；`get_object` 一次只能指定一个 member。

## 工具

```text
find_objects
get_object
aggregate_objects
```

### find_objects

```json
{
  "object_type": "customer",
  "member_datasource_ids": ["ORG_CODE1"],
  "filters": [{"property": "status", "operator": "eq", "value": "active"}],
  "where": {"operator": "and", "children": []},
  "sort": [{"property": "order_count", "direction": "desc"}],
  "limit": 10,
  "offset": 0
}
```

规则：

```text
属性必须是对象类型已定义属性
派生属性可用于过滤/排序
in 用 values 数组；旧接口只传 value 时按逗号拆分
member_datasource_ids 可一次传多个机构；单机构传一个，全部机构传清单里全部 ID
```

### get_object

```text
object_key 格式：member:object_type:local_key
返回对象属性、派生属性、一跳 links
```

### aggregate_objects

```text
group_by 最多 5 个属性
metrics 最多 5 个
count 可省略 property
其他算子必须指定属性
派生属性可作为指标
结果按分组列排序，不支持 HAVING / 指标排序
```

## 金额口径

订单金额、明细小计、退款金额要分开说：

```text
订单金额 / 名义金额
  订单表里登记的金额合计，可能包含退款/取消订单

实际消费 / 净额
  需要扣除退款、取消部分后再计算
```

不要把含已退款订单的金额合计直接称为“消费”。回答时必须说明：

```text
是否含退款订单
是否已扣除退款
数据截至时间
```

## 派生属性

```text
aggregate
  count / count_distinct / sum / avg / min / max
select
  取对端单个属性

count/count_distinct/sum 无对端行时为 0
max/min/avg 无对端行时为空
```

## 边界

```text
单对象类型
find_objects / aggregate_objects 可多 member（results[] 分机构返回）
get_object 单 member
一跳派生
图查询开启时支持 1~3 跳 / 有界路径
不支持任意深度 / 图算法 / 全图扫描
无 HAVING / 指标排序
多机构是多条 SQL 并行下发，不是单条跨库 SQL
```

## 失败处理

```text
对象类型不存在 → 列出可用对象
属性未映射 → 指出可用属性
单成员失败 → 该成员 results[] status=failed，其它成员照常返回；get_object 多 member → 要求选择一个
无权限 → 如实说明
```
