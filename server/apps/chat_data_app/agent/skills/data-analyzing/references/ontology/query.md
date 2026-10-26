# 本体对象查询

## 适用

```text
具体对象 / 对象列表 / 对象详情
对象属性过滤 / 排序 / 分页
对象聚合（group_by + count/count_distinct/sum/avg/min/max）
对象派生属性
对象集合跨轮传递（保存 / 加载 / 列出）
```

## 参数契约

`action_payload` 的字段名、必填、默认值与上限不写进本文档：调
`ontology_tool(action="help", action_payload={"action": "<动作名>"})` 查（由工具入参模型现算）。
本文只写用哪个动作、什么顺序、口径与边界。

## 查询顺序

1. 先调 `list_object_types` 确认当前业务域对象类型；需要属性、数据源绑定或关系类型详情时调 `locate_object_type`。
2. 需要机构范围时调 `list_execution_targets`，`member_datasource_ids` 只能从返回清单选择。
3. 明细/过滤/排序/分页用 `find_objects`，分组聚合用 `aggregate_objects`，按主键读单对象用 `get_object`。
4. `find_objects` / `aggregate_objects` 支持一次传多个 member：工具按机构并行下发 SQL，返回 `results[]`；`get_object` 一次只能指定一个 member。
5. 结果需要跨轮复用时用 `save_object_set` 保存主键，`load_object_set` 分页回读，`list_object_sets` 列出已有集合。

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
单成员失败 → 该成员 results[] status=failed，其它成员照常返回
get_object 多 member → 要求选择一个
无权限 → 如实说明
```
