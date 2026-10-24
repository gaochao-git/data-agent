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

## 边界

```text
只支持 FK → PK 单列关联
不支持自关联 / 多列关联
不支持多跳
不支持图数据库遍历
```

## 失败处理

```text
关系不存在 → 列可用关系
连接列未解析 → 回读对象 source_binding 确认
无对端对象 → 返回空 links，不报错
```
