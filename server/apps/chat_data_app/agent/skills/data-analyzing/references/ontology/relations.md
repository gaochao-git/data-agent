# 本体关系导航

## 适用

```text
对象一跳关系
沿关系找对端对象
订单 ↔ 客户 / 发货单
关系方向确认
多跳 / 路径查询（图查询开启时）
```

## 参数契约

`action_payload` 的字段名、必填、默认值与上限不写进本文档：调
`ontology_tool(action="help", action_payload={"action": "<动作名>"})` 查（由工具入参模型现算）。
本文只写用哪个动作、返回什么、边界与回退规则。

## 一跳关系

```text
关系类型由本体定义，不能自行拼外键
关系方向：from 侧持有外键，to 侧持有主键
单次一跳，每关系最多 20 条
```

`search_around` 返回每条关系的物理连接列与对端对象导航条目：

```text
join：from_columns / to_columns，未解析时带 unresolved: true
neighbor：对端对象的属性 / 绑定 / 映射，relations 只列名字提示下一跳
unresolved: true 时，回读对象类型 source_binding 的 column_mappings / columns 确认物理列
```

需要获取对象当前属性时用 `get_object`（对象查询流程见 `query.md`）。

## 多跳图查询（可选）

先判断用户问的是不是“对象之间的关系链”，例如：

```text
张三有哪些订单，订单又对应哪些发货单
这个产品被哪些订单买过，对应哪些客户
从 A 到 B 中间经过什么
```

这种“多跳 / 链路 / 路径”更适合图探索；如果只是查对象属性、查一跳对端、拿 join 列写 SQL，继续用 `get_object` / `search_around` 即可。

图查询不是硬性必经步骤：图未开启、未同步、超时或只需要一跳时，按普通对象查询处理，并在回答里说明范围。

当部署开启 `CHAT_DATA_NEBULA_ENABLED=1` 且图同步已完成时：

```text
traverse_objects   从对象出发 1~3 跳
find_graph_paths   两个对象之间的有界路径
```

注意：

```text
图查询只返回对象身份/标题/关系，对象属性仍需 get_object 回源
一次只能指定一个 member_datasource_id
depth / max_depth <= 3，limit <= 200
```

图查询不可用时回退一跳 `search_around`，不要假装图查询成功。

回答链路结论时说明：

```text
查了几跳
有没有“完整/无断点”的证据
结果是否只覆盖部分对象
```

不要把“两笔订单各有一张发货单”说成“全量链路无断点”。

## 边界

```text
SQL 投影只支持 FK → PK 单列关联
不支持自关联 / 多列关联
图查询支持有界多跳（1~3）
不支持任意深度 / 图算法 / 全图扫描
```

## 失败处理

```text
关系不存在 → 列可用关系
连接列未解析 → 回读对象 source_binding 确认
无对端对象 → 返回空 links，不报错
```
