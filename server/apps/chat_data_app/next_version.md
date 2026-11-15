# 下一版本数据库变更

以下 DDL 由发布流程/DBA 在上线前手工执行，应用启动不会自动建表。

## 子智能体多轮会话快照

上线前执行：

```sql
CREATE TABLE `chat_data_subagent_session` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `session_id` varchar(64) NOT NULL COMMENT '子智能体会话ID（agent_session_id；当前等于首轮 task_id）',
  `conversation_id` varchar(255) NOT NULL COMMENT '父会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `latest_task_id` varchar(64) NOT NULL COMMENT '最近一轮任务ID',
  `turn_count` int NOT NULL DEFAULT '1' COMMENT '已完成的对话轮次',
  `messages_json` mediumtext NOT NULL COMMENT '完整消息历史JSON（每次收口整体覆盖）',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_subagent_session_id` (`session_id`),
  KEY `idx_subagent_session_conv` (`conversation_id`, `user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='后台子智能体多轮会话快照';
```
