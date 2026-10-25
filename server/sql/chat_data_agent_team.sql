-- ChatData Agent Team: durable roster, shared task board, teammate messages.
-- MySQL 8 / fxcjg_ai. JSON is stored as text, not JSON column.

CREATE TABLE IF NOT EXISTS `chat_data_team_member` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '所属主会话ID',
  `user_id` varchar(255) NOT NULL DEFAULT '' COMMENT '业务归属用户ID',
  `name` varchar(64) NOT NULL COMMENT 'teammate 唯一名称',
  `description` varchar(500) NOT NULL DEFAULT '' COMMENT '职责说明',
  `subagent_type` varchar(64) NOT NULL DEFAULT '' COMMENT '子智能体类型',
  `thread_id` varchar(255) NOT NULL COMMENT 'teammate checkpoint thread',
  `status` varchar(16) NOT NULL DEFAULT 'provisioning' COMMENT 'provisioning/active/failed',
  `error` text COMMENT '失败原因',
  `last_message` mediumtext COMMENT '最近一条回复',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_team_member_name` (`conversation_id`, `name`),
  UNIQUE KEY `uniq_team_member_thread` (`thread_id`),
  KEY `idx_team_member_conv` (`conversation_id`, `delete_flag`, `status`),
  KEY `idx_team_member_user` (`user_id`, `conversation_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData Agent Team 成员';

CREATE TABLE IF NOT EXISTS `chat_data_team_task` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '所属主会话ID',
  `user_id` varchar(255) NOT NULL DEFAULT '' COMMENT '业务归属用户ID',
  `task_key` varchar(32) NOT NULL COMMENT '会话内任务编号 task-N',
  `subject` varchar(255) NOT NULL COMMENT '任务标题',
  `description` mediumtext COMMENT '任务详情',
  `status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/in_progress/completed/deleted',
  `owner` varchar(64) NOT NULL DEFAULT '' COMMENT '任务 owner',
  `blocked_by` text COMMENT '依赖任务ID列表 JSON 文本',
  `write_scopes` text COMMENT '写路径提示 JSON 文本',
  `revision` int(11) NOT NULL DEFAULT '1' COMMENT 'CAS revision',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_team_task_key` (`conversation_id`, `task_key`),
  KEY `idx_team_task_conv` (`conversation_id`, `delete_flag`, `status`),
  KEY `idx_team_task_owner` (`conversation_id`, `owner`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData Agent Team 共享任务';

CREATE TABLE IF NOT EXISTS `chat_data_team_message` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '所属主会话ID',
  `member_name` varchar(64) NOT NULL COMMENT 'teammate 名称',
  `message_id` varchar(64) NOT NULL COMMENT '消息ID',
  `role` varchar(16) NOT NULL COMMENT 'user/assistant/tool',
  `content` mediumtext COMMENT '消息正文',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_team_message_id` (`message_id`),
  KEY `idx_team_message_conv` (`conversation_id`, `member_name`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData Agent Team 成员消息历史';
