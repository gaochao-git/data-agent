# 630 Version TODO

## 数据库变更

### 1. 看板面板增加成员数据源字段

用途：保存看板面板实际绑定的成员数据源，避免多成员数据源场景下只依赖数据源组和 SQL 无法准确定位执行目标。

```sql
ALTER TABLE `chat_data_dashboard_panel`
  ADD COLUMN `member_datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '成员数据源ID' AFTER `datasource_id`;
```
