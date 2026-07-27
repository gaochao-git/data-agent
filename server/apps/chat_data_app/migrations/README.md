# ChatData Database Migrations

ChatData 业务表由部署环境手动创建。本目录的 SQL 必须在对应版本发布前按顺序执行；应用启动不会自动建表。

执行顺序：

1. `20260724_add_conversation_event.sql`
2. `20260727_remove_system_skill_records.sql`
3. `20260727_set_skill_scope_default.sql`
