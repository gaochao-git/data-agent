# 630 Version TODO

## 数据库变更

### 1. 数据源组增加推荐问题字段

用途：在数据源组页面维护智能问数空态推荐问题，每行一个问题，运行时用于推荐问题列表展示。

```sql
ALTER TABLE `datasource_group`
  ADD COLUMN `recommended_questions_json` text COMMENT '推荐问题JSON' AFTER `query_context`;
```
