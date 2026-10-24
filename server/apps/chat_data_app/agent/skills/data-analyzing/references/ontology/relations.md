# 本体关系导航

## 适用

```text
对象一跳关系
沿关系找对端对象
订单 ↔ 客户 / 发货单
关系方向确认
```

## 工具

```text
get_object        读取对象 links
search_around     沿关系找对端
```

## 规则

```text
关系类型由本体定义，不能自行拼外键
关系方向：
  from 侧持有外键
  to 侧持有主键
单次一跳
每关系最多 20 条
```

## search_around

```text
object_type
object_key
relation_type
direction
member_datasource_ids
```

返回：

```text
related_object_type
related_key
related_title
```

## 多跳图查询（可选）

当部署开启 `CHAT_DATA_NEBULA_ENABLED=1` 且图同步已完成时：

```text
traverse_objects   从对象出发 1~3 跳
find_graph_paths   两个对象之间的有界路径
```

用途：

```text
多跳关联
关系链解释
路径查询
```

注意：

```text
图查询只返回对象身份/标题/关系
对象属性仍需 get_object 回源
必须指定且只能指定一个 member_datasource_id
depth <= 3，limit <= 200
```

图查询不可用时回退一跳 `search_around`，不要假装图查询成功。

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
