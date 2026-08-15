# 下一版本变更

## 数据库变更

上线前执行：

```sql
CREATE TABLE `chat_data_conversation_event` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `turn_id` varchar(64) NOT NULL DEFAULT '' COMMENT '用户轮次ID',
  `sequence_no` bigint NOT NULL COMMENT '会话内严格递增事件序号',
  `event_key` varchar(255) NOT NULL COMMENT '幂等事件键',
  `event_type` varchar(32) NOT NULL COMMENT '事件类型',
  `payload` mediumtext NOT NULL COMMENT '事件JSON文本',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识 0未删除 1已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_conv_event_key` (`conversation_id`,`event_key`),
  KEY `idx_conv_event_seq` (`conversation_id`,`sequence_no`),
  KEY `idx_conv_event_turn` (`conversation_id`,`turn_id`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COMMENT='ChatData会话原始事件历史';

CREATE TABLE `chat_data_agent_inbox` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `run_id` varchar(64) NOT NULL COMMENT '运行ID',
  `owner_token` varchar(64) DEFAULT NULL COMMENT '当前运行者 fencing token',
  `message_id` varchar(64) NOT NULL DEFAULT '' COMMENT '消息ID，运行记录等于运行ID',
  `record_type` varchar(16) NOT NULL COMMENT '记录类型: run/message',
  `queue_type` varchar(16) NOT NULL DEFAULT 'next-step' COMMENT '队列类型: next-step-下个模型步骤注入, next-turn-下一轮处理',
  `content` mediumtext NOT NULL COMMENT '追加消息内容，运行记录为空',
  `status` varchar(16) NOT NULL COMMENT '运行或消息状态',
  `claimed_time` datetime DEFAULT NULL COMMENT '消息领取时间',
  `consumed_time` datetime DEFAULT NULL COMMENT '消息注入时间',
  `lease_expire_time` datetime DEFAULT NULL COMMENT '领取租约过期时间',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_inbox_msg` (`record_type`, `message_id`),
  KEY `idx_agent_inbox_conv` (`conversation_id`, `user_id`, `record_type`, `status`),
  KEY `idx_agent_inbox_run` (`run_id`, `record_type`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData运行消息收件箱';

-- 如果 chat_data_agent_inbox 已经存在且建表时没有 owner_token，再单独执行：
-- ALTER TABLE `chat_data_agent_inbox`
--   ADD COLUMN `owner_token` varchar(64) DEFAULT NULL COMMENT '当前运行者 fencing token' AFTER `run_id`;

CREATE TABLE `chat_data_agent_run_event` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `run_id` varchar(64) NOT NULL COMMENT '运行ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `sequence_no` bigint(20) NOT NULL COMMENT '运行内严格递增事件序号',
  `event_id` varchar(255) NOT NULL COMMENT '事件幂等ID',
  `event_type` varchar(32) NOT NULL COMMENT 'SSE事件类型',
  `payload` mediumtext NOT NULL COMMENT 'SSE事件原文',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0未删除 1已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '修改人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_agent_run_event` (`run_id`,`event_id`),
  KEY `idx_agent_run_event_seq` (`run_id`,`sequence_no`),
  KEY `idx_agent_run_event_conv` (`conversation_id`,`user_id`,`create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData后台运行可重放事件';

ALTER TABLE datasource_table
    ADD COLUMN indexes_json TEXT NULL COMMENT '表级物理索引和手工索引 JSON';

ALTER TABLE `user_threads`
  ADD KEY `idx_thread_history` (`user_id`,`agent_id`,`delete_flag`,`update_time`,`id`);
```

## 依赖变更

上线前执行：

```bash
python -m pip install opensandbox==0.1.15
python -m pip install redis==7.0.1
```

Redis 配置：

```env
CHAT_DATA_REDIS_URL=redis://127.0.0.1:6379/0
CHAT_DATA_REDIS_STREAM_TTL_SECONDS=86400
```

Run 实时事件使用 Redis Streams：

- `chatdata:run:{run_id}:events`：前端 SSE 实时订阅和断线续传。
- `chatdata:run-events:persist`：Consumer Group 异步批量持久化到 `chat_data_agent_run_event`。
- `chat_data_agent_run_event` 仍保留，作为长期事件历史和 Redis 故障后的补偿来源。
- `chat_data_conversation_event` 只保存最终会话展示历史，LangGraph checkpoint 只保存 Agent 执行状态。
