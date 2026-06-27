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

### 3. 技能增加适用范围字段

用途：区分技能适用于智能问数、智能分析或两者，避免智能问数加载脚本/素材类技能。

```sql
ALTER TABLE `chat_data_skill`
  ADD COLUMN `skill_scope` varchar(32) NOT NULL DEFAULT 'analyze' COMMENT '适用范围: query/analyze/all' AFTER `skill_category`;

UPDATE `chat_data_skill`
SET `skill_scope` = 'analyze'
WHERE `skill_name` IN ('chart-processing', 'dashboard-generation');
```
