# 630 Version TODO

## 数据库变更

### 1. 看板面板增加成员数据源字段

用途：保存看板面板实际绑定的成员数据源，避免多成员数据源场景下只依赖数据源组和 SQL 无法准确定位执行目标。

```sql
ALTER TABLE `chat_data_dashboard_panel`
  ADD COLUMN `member_datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '成员数据源ID' AFTER `datasource_id`;
```

### 2. 数据源组增加智能问数说明字段

用途：在数据源组页面维护智能问数补充说明、规则和正反例，运行时注入当前数据源组的智能问数上下文。

```sql
ALTER TABLE `datasource_group`
  ADD COLUMN `query_context` text COMMENT '智能问数说明' AFTER `group_desc`;
```
