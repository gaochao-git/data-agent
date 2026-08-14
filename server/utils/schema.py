"""
LangGraph 公共数据库表结构定义

业务表统一使用 MySQL
"""

import os

# ============ MySQL 表结构 ============

MYSQL_USER_THREADS_TABLE = """
CREATE TABLE `user_threads` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `user_id` varchar(255) NOT NULL COMMENT '用户ID',
  `thread_id` varchar(255) NOT NULL COMMENT '会话ID',
  `thread_title` varchar(255) DEFAULT '未命名会话' COMMENT '会话标题',
  `agent_id` varchar(255) DEFAULT NULL COMMENT '智能体ID',
  `datasource_id` varchar(255) NOT NULL DEFAULT '' COMMENT '会话绑定数据源组ID',
  `is_favorited` tinyint DEFAULT '0' COMMENT '是否收藏: 0-否, 1-是',
  `delete_flag` tinyint DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_thread_id` (`thread_id`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_agent_id` (`agent_id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8mb4 COMMENT='用户会话表'
"""

MYSQL_CHAT_DATA_CONVERSATION_EVENT_TABLE = """
CREATE TABLE `chat_data_conversation_event` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `turn_id` varchar(64) NOT NULL DEFAULT '' COMMENT '用户轮次ID',
  `sequence_no` bigint(20) NOT NULL COMMENT '会话内严格递增事件序号',
  `event_key` varchar(255) NOT NULL COMMENT '幂等事件键',
  `event_type` varchar(32) NOT NULL COMMENT '事件类型',
  `payload` mediumtext NOT NULL COMMENT '事件JSON文本',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_conv_event_key` (`conversation_id`, `event_key`),
  KEY `idx_conv_event_seq` (`conversation_id`, `sequence_no`),
  KEY `idx_conv_event_turn` (`conversation_id`, `turn_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData会话原始事件历史';
"""

MYSQL_CHAT_DATA_AGENT_INBOX_TABLE = """
CREATE TABLE `chat_data_agent_inbox` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `run_id` varchar(64) NOT NULL COMMENT '运行ID',
  `message_id` varchar(64) NOT NULL DEFAULT '' COMMENT '消息ID，运行记录等于运行ID',
  `record_type` varchar(16) NOT NULL COMMENT '记录类型: run/message',
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
"""

MYSQL_UPLOADED_FILES_TABLE = """
CREATE TABLE `uploaded_files` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `file_id` varchar(255) DEFAULT NULL COMMENT '文件ID',
  `thread_id` varchar(255) NOT NULL COMMENT '所属会话ID',
  `original_filename` varchar(255) NOT NULL COMMENT '原始文件名',
  `file_type` varchar(50) NOT NULL COMMENT '文件类型',
  `file_size` bigint NOT NULL COMMENT '文件大小(字节)',
  `storage_dir` varchar(500) NOT NULL COMMENT '存储目录',
  `parse_status` varchar(50) DEFAULT 'pending' COMMENT '解析状态: pending-待解析, done-已完成, failed-失败',
  `parse_filename` varchar(255) DEFAULT NULL COMMENT '解析后文件名',
  `delete_flag` tinyint DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_file_id` (`file_id`),
  KEY `idx_thread_id` (`thread_id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8mb4 COMMENT='上传文件表';
;
"""

MYSQL_CHAT_DATA_DASHBOARD_TABLE = """
CREATE TABLE `chat_data_dashboard` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `dashboard_id` varchar(255) NOT NULL COMMENT '大屏ID',
  `dashboard_name` varchar(255) NOT NULL COMMENT '大屏名称',
  `dashboard_desc` varchar(1000) DEFAULT NULL COMMENT '大屏描述',
  `user_id` varchar(255) NOT NULL COMMENT '用户ID',
  `datasource_id` varchar(255) DEFAULT NULL COMMENT '数据源ID',
  `dashboard_theme` varchar(50) DEFAULT 'light' COMMENT '主题: light-浅色, dark-深色',
  `background_id` varchar(50) DEFAULT NULL COMMENT '背景图片ID',
  `is_public` tinyint DEFAULT '0' COMMENT '是否公开: 0-否, 1-是',
  `delete_flag` tinyint DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_dashboard_id` (`dashboard_id`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_datasource_id` (`datasource_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分析大屏表';
"""

MYSQL_CHAT_DATA_DASHBOARD_PANEL_TABLE = """
CREATE TABLE `chat_data_dashboard_panel` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `panel_id` varchar(255) NOT NULL COMMENT '面板ID',
  `dashboard_id` varchar(255) NOT NULL COMMENT '所属大屏ID',
  `panel_name` varchar(255) NOT NULL COMMENT '面板名称',
  `panel_sql` varchar(5000) NOT NULL COMMENT 'SQL语句',
  `chart_type` varchar(50) DEFAULT 'table' COMMENT '图表类型: table-表格, bar-柱状图, line-折线图, pie-饼图, kpi-KPI卡片',
  `chart_config` varchar(2000) DEFAULT NULL COMMENT '图表配置(JSON)',
  `datasource_id` varchar(255) DEFAULT NULL COMMENT '数据源ID',
  `member_datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '成员数据源ID',
  `layout_x` int DEFAULT '0' COMMENT '布局X坐标',
  `layout_y` int DEFAULT '0' COMMENT '布局Y坐标',
  `layout_w` int DEFAULT '6' COMMENT '布局宽度',
  `layout_h` int DEFAULT '4' COMMENT '布局高度',
  `refresh_interval` int DEFAULT NULL COMMENT '刷新间隔(秒)',
  `source_thread_id` varchar(255) DEFAULT NULL COMMENT '来源会话ID',
  `delete_flag` tinyint DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_panel_id` (`panel_id`),
  KEY `idx_dashboard_id` (`dashboard_id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8mb4 COMMENT='大屏面板表';
"""

MYSQL_SQL_QUERY_HISTORY_TABLE = """
CREATE TABLE `sql_query_history` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `datasource_id` varchar(255) NOT NULL COMMENT '数据源ID',
  `user_id` varchar(255) DEFAULT NULL COMMENT '用户ID',
  `sql_text` text NOT NULL COMMENT 'SQL文本',
  `query_title` varchar(255) DEFAULT NULL COMMENT '查询标题',
  `query_type` varchar(20) DEFAULT 'sql' COMMENT '查询类型: sql | builder',
  `builder_config` text COMMENT 'Builder配置(JSON)',
  `last_used` datetime DEFAULT NULL COMMENT '最近使用时间',
  `execution_time_ms` int DEFAULT NULL COMMENT '单次执行耗时(毫秒)',
  `result_row_count` int DEFAULT NULL COMMENT '返回结果行数',
  `success_flag` tinyint NOT NULL DEFAULT '1' COMMENT '执行是否成功: 1-成功, 0-失败',
  `error_message` text COMMENT '执行失败时的错误信息',
  `delete_flag` tinyint DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8mb4 COMMENT='SQL查询历史表';
"""

MYSQL_SQL_QUERY_FAVORITE_TABLE = """
CREATE TABLE `sql_query_favorite` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `datasource_id` varchar(255) NOT NULL COMMENT '数据源ID',
  `user_id` varchar(255) DEFAULT NULL COMMENT '用户ID',
  `semantic_config_id` varchar(255) DEFAULT NULL COMMENT '语义配置ID',
  `query_type` varchar(20) NOT NULL DEFAULT 'sql' COMMENT '收藏类型: conversation | builder | sql',
  `favorite_title` varchar(255) NOT NULL COMMENT '收藏标题',
  `query_text` text NOT NULL COMMENT '原始查询内容',
  `builder_config` text COMMENT 'Builder配置(JSON)',
  `delete_flag` tinyint DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8mb4 COMMENT='查询收藏表'
;
"""

MYSQL_DATASOURCE_GROUP_TABLE = """
CREATE TABLE `datasource_group` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `group_id` varchar(128) NOT NULL COMMENT '数据源业务ID',
  `group_name` varchar(128) NOT NULL COMMENT '数据源名称',
  `group_desc` varchar(128) DEFAULT NULL COMMENT '描述',
  `query_context` text COMMENT '智能问数说明',
  `recommended_questions_json` text COMMENT '推荐问题JSON',
  `source_type` varchar(32) NOT NULL COMMENT '数据源类型: mysql | hive',
  `template_source` varchar(256) NOT NULL COMMENT '模板数据源，格式 机构名称|机构编码|连接方式|host:port:database',
  `member_sources_text` text COMMENT '组内数据源，多行文本，格式 机构名称|机构编码|连接方式|host:port:database',
  `mapped_modules_json` text COMMENT '模块映射JSON',
  `is_default` tinyint NOT NULL DEFAULT '0' COMMENT '是否默认数据源: 0-否, 1-是',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(128) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_group_id` (`group_id`),
  KEY `idx_group_name` (`group_name`),
  KEY `idx_source_type` (`source_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源组表';
"""

MYSQL_DATASOURCE_TABLE_SCHEMA_TABLE = """
CREATE TABLE `datasource_table` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `group_id` varchar(128) NOT NULL COMMENT '所属数据源ID',
  `table_names` varchar(128) NOT NULL COMMENT '表名',
  `table_desc` varchar(128) DEFAULT NULL COMMENT '表描述',
  `table_business_desc` varchar(255) NOT NULL DEFAULT '' COMMENT '表业务说明',
  `columns_json` text COMMENT '字段结构JSON',
  `sort_no` int NOT NULL DEFAULT '0' COMMENT '排序号',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(128) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_group_id_table_names` (`group_id`, `table_names`),
  KEY `idx_group_id` (`group_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源表结构缓存表';
"""

MYSQL_DATASOURCE_TABLE_GROUP_REL_TABLE = """
CREATE TABLE `datasource_table_group_rel` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `group_id` varchar(64) NOT NULL DEFAULT '' COMMENT '数据源组ID',
  `table_group_name` varchar(100) NOT NULL DEFAULT '' COMMENT '表分组名称',
  `table_name` varchar(128) NOT NULL DEFAULT '' COMMENT '表名',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_grp_tbl_group` (`group_id`, `table_group_name`, `table_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源表分组关系表';
"""

MYSQL_DATASOURCE_COLUMN_DICT_TABLE = """
CREATE TABLE `datasource_column_dict` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `group_id` varchar(128) NOT NULL DEFAULT '' COMMENT '数据源组ID',
  `table_name` varchar(128) NOT NULL DEFAULT '' COMMENT '表名',
  `column_name` varchar(128) NOT NULL DEFAULT '' COMMENT '字段名',
  `dict_json` text COMMENT '字段字典JSON字符串',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_grp_tbl_col` (`group_id`, `table_name`, `column_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源字段数据字典';
"""

MYSQL_SEMANTIC_CONFIG_TABLE = """
CREATE TABLE `semantic_config` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `semantic_config_id` varchar(128) NOT NULL COMMENT '语义配置业务ID',
  `group_id` varchar(128) NOT NULL COMMENT '所属数据源ID',
  `semantic_config_name` varchar(128) NOT NULL COMMENT '语义配置名称',
  `semantic_config_desc` varchar(128) DEFAULT NULL COMMENT '描述',
  `ontology_rules` text COMMENT '本体规则/业务规则',
  `enabled_flag` tinyint NOT NULL DEFAULT '1' COMMENT '是否启用: 0-否, 1-是',
  `metrics_json` text COMMENT '指标配置JSON',
  `dimensions_json` text COMMENT '维度配置JSON',
  `relationships_json` text COMMENT '关系配置JSON',
  `scope_type` varchar(20) NOT NULL DEFAULT 'public' COMMENT '范围: public公共, private个人',
  `sort_no` int NOT NULL DEFAULT '0' COMMENT '排序号',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(128) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_semantic_config_id` (`semantic_config_id`),
  KEY `idx_group_id` (`group_id`),
  KEY `idx_semantic_config_name` (`semantic_config_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='语义配置表';
"""

MYSQL_CHAT_DATA_QUERY_TASK_TABLE = """
CREATE TABLE `chat_data_query_task` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `task_id` varchar(64) NOT NULL COMMENT '任务ID',
  `task_type` varchar(32) NOT NULL DEFAULT 'sql' COMMENT '任务类型: sql',
  `datasource_id` varchar(128) NOT NULL COMMENT '数据源ID',
  `query_type` varchar(32) NOT NULL DEFAULT 'sql' COMMENT '查询类型: sql | builder',
  `task_status` varchar(32) NOT NULL DEFAULT 'pending' COMMENT '任务状态: pending | running | succeeded | failed | cancelled',
  `progress_text` varchar(255) DEFAULT NULL COMMENT '进度描述',
  `request_payload` mediumtext NOT NULL COMMENT '请求参数JSON',
  `result_payload` mediumtext COMMENT '结果JSON',
  `remote_task_payload` text COMMENT '远端HTTP任务映射JSON',
  `error_message` text COMMENT '错误信息',
  `started_time` datetime DEFAULT NULL COMMENT '开始时间',
  `finished_time` datetime DEFAULT NULL COMMENT '结束时间',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_task_id` (`task_id`),
  KEY `idx_task_type_task_status` (`task_type`, `task_status`),
  KEY `idx_datasource_id_task_status` (`datasource_id`, `task_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData 异步查询任务表';
"""

MYSQL_CHAT_DATA_QUERY_EXECUTION_TABLE = """
CREATE TABLE `chat_data_query_execution` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `execution_id` varchar(64) NOT NULL DEFAULT '' COMMENT '执行记录ID',
  `task_id` varchar(64) NOT NULL DEFAULT '' COMMENT '任务ID',
  `user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '用户ID',
  `datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '数据源ID',
  `member_datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '成员数据源ID',
  `member_name` varchar(255) NOT NULL DEFAULT '' COMMENT '成员数据源名称',
  `executor_type` varchar(32) NOT NULL DEFAULT '' COMMENT '执行器类型',
  `sql_text` mediumtext NOT NULL COMMENT '执行SQL',
  `sql_status` varchar(32) NOT NULL DEFAULT 'pending' COMMENT 'SQL状态',
  `remote_task_id` varchar(128) NOT NULL DEFAULT '' COMMENT '远端任务ID',
  `db_connection_id` varchar(128) NOT NULL DEFAULT '' COMMENT '数据库连接ID',
  `execution_time_ms` int DEFAULT NULL COMMENT '执行耗时毫秒',
  `result_row_count` int DEFAULT NULL COMMENT '结果行数',
  `result_payload` mediumtext COMMENT '执行结果JSON',
  `error_message` text COMMENT '错误信息',
  `started_time` datetime DEFAULT NULL COMMENT '开始时间',
  `finished_time` datetime DEFAULT NULL COMMENT '结束时间',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_execution_id` (`execution_id`),
  KEY `idx_execution_task_id` (`task_id`),
  KEY `idx_execution_user_ds_time` (`user_id`, `datasource_id`, `create_time`),
  KEY `idx_execution_ds_status` (`datasource_id`, `sql_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData SQL执行记录表';
"""

MYSQL_CHAT_DATA_SQL_RESULT_CACHE_TABLE = """
CREATE TABLE `chat_data_sql_result_cache` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `cache_key` varchar(64) NOT NULL DEFAULT '' COMMENT '缓存键',
  `datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '数据源ID',
  `member_datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '成员数据源ID',
  `executor_type` varchar(32) NOT NULL DEFAULT '' COMMENT '执行器类型',
  `source_type` varchar(32) NOT NULL DEFAULT '' COMMENT '数据源类型',
  `sql_hash` varchar(64) NOT NULL DEFAULT '' COMMENT 'SQL哈希',
  `sql_text` text NOT NULL COMMENT '执行SQL',
  `result_payload` mediumtext NOT NULL COMMENT '查询结果JSON',
  `result_row_count` int DEFAULT NULL COMMENT '结果行数',
  `expire_time` datetime NOT NULL COMMENT '过期时间',
  `last_hit_time` datetime DEFAULT NULL COMMENT '最近命中时间',
  `hit_count` int NOT NULL DEFAULT '0' COMMENT '命中次数',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_cache_key` (`cache_key`),
  KEY `idx_expire_time` (`expire_time`),
  KEY `idx_ds_update` (`datasource_id`, `update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData SQL结果缓存表';
"""

MYSQL_CHAT_DATA_EVAL_CASE_SET_TABLE = """
CREATE TABLE `chat_data_eval_case_set` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `case_set_id` varchar(64) NOT NULL DEFAULT '' COMMENT '测试集ID',
  `datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '数据源ID',
  `semantic_config_id` varchar(128) NOT NULL DEFAULT '' COMMENT '指标集ID',
  `case_set_name` varchar(128) NOT NULL DEFAULT '' COMMENT '测试集名称',
  `business_domain` varchar(128) NOT NULL DEFAULT '' COMMENT '业务域',
  `version_no` int NOT NULL DEFAULT '1' COMMENT '版本号',
  `case_count` int NOT NULL DEFAULT '0' COMMENT '用例数量',
  `case_set_status` varchar(32) NOT NULL DEFAULT 'active' COMMENT '测试集状态',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_case_set_id` (`case_set_id`),
  KEY `idx_eval_case_set_scene` (`datasource_id`, `semantic_config_id`),
  KEY `idx_eval_case_set_status` (`case_set_status`, `update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测测试集表';
"""

MYSQL_CHAT_DATA_EVAL_CASE_TABLE = """
CREATE TABLE `chat_data_eval_case` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `case_set_id` varchar(64) NOT NULL DEFAULT '' COMMENT '测试集ID',
  `case_id` varchar(128) NOT NULL DEFAULT '' COMMENT '用例ID',
  `nl_text` text NOT NULL COMMENT '自然语言问题',
  `gold_dsl_text` mediumtext NOT NULL COMMENT '标准DSL JSON',
  `gold_sql_text` mediumtext NOT NULL COMMENT '标准SQL',
  `query_type` varchar(32) NOT NULL DEFAULT 'aggregation' COMMENT '查询类型',
  `case_difficulty` varchar(32) NOT NULL DEFAULT 'easy' COMMENT '难度',
  `case_source` varchar(32) NOT NULL DEFAULT 'human' COMMENT '来源',
  `business_domain` varchar(128) NOT NULL DEFAULT '' COMMENT '业务域',
  `enabled_flag` tinyint NOT NULL DEFAULT '1' COMMENT '是否启用: 0-否, 1-是',
  `sort_no` int NOT NULL DEFAULT '0' COMMENT '排序号',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_case_set_case` (`case_set_id`, `case_id`),
  KEY `idx_eval_case_set_id` (`case_set_id`),
  KEY `idx_eval_case_type` (`query_type`),
  KEY `idx_eval_case_source` (`case_source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测用例表';
"""

MYSQL_CHAT_DATA_EVAL_RUN_TABLE = """
CREATE TABLE `chat_data_eval_run` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `run_id` varchar(64) NOT NULL DEFAULT '' COMMENT '评测运行ID',
  `case_set_id` varchar(64) NOT NULL DEFAULT '' COMMENT '测试集ID',
  `datasource_id` varchar(128) NOT NULL DEFAULT '' COMMENT '数据源ID',
  `semantic_config_id` varchar(128) NOT NULL DEFAULT '' COMMENT '指标集ID',
  `user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '用户ID',
  `run_mode` varchar(32) NOT NULL DEFAULT 'live' COMMENT '评测模式',
  `run_status` varchar(32) NOT NULL DEFAULT 'running' COMMENT '运行状态',
  `request_payload` mediumtext NOT NULL COMMENT '请求参数JSON',
  `summary_payload` mediumtext COMMENT '汇总结果JSON',
  `total_count` int NOT NULL DEFAULT '0' COMMENT '总用例数',
  `scored_count` int NOT NULL DEFAULT '0' COMMENT '已评分数',
  `passed_count` int NOT NULL DEFAULT '0' COMMENT '通过数',
  `failed_count` int NOT NULL DEFAULT '0' COMMENT '失败数',
  `semantic_accuracy_bp` int NOT NULL DEFAULT '0' COMMENT '语义准确率万分比',
  `error_message` text COMMENT '错误信息',
  `started_time` datetime DEFAULT NULL COMMENT '开始时间',
  `finished_time` datetime DEFAULT NULL COMMENT '结束时间',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_run_id` (`run_id`),
  KEY `idx_eval_run_scene` (`datasource_id`, `semantic_config_id`, `create_time`),
  KEY `idx_eval_run_status` (`run_status`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测运行记录表';
"""

MYSQL_CHAT_DATA_EVAL_RUN_CASE_TABLE = """
CREATE TABLE `chat_data_eval_run_case` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `run_case_id` varchar(64) NOT NULL DEFAULT '' COMMENT '运行用例结果ID',
  `run_id` varchar(64) NOT NULL DEFAULT '' COMMENT '评测运行ID',
  `case_id` varchar(128) NOT NULL DEFAULT '' COMMENT '用例ID',
  `case_status` varchar(32) NOT NULL DEFAULT 'running' COMMENT '运行状态',
  `result_payload` mediumtext COMMENT '单用例结果JSON',
  `semantic_accuracy_bp` int NOT NULL DEFAULT '0' COMMENT '语义准确率万分比',
  `elapsed_ms` int DEFAULT NULL COMMENT '耗时毫秒',
  `error_message` text COMMENT '错误信息',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_run_case_id` (`run_case_id`),
  UNIQUE KEY `uniq_eval_run_case` (`run_id`, `case_id`),
  KEY `idx_eval_run_case_run` (`run_id`),
  KEY `idx_eval_run_case_case` (`case_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测单用例结果表';
"""

MYSQL_TABLES = [
    MYSQL_USER_THREADS_TABLE,
    MYSQL_CHAT_DATA_CONVERSATION_EVENT_TABLE,
    MYSQL_CHAT_DATA_AGENT_INBOX_TABLE,
    MYSQL_UPLOADED_FILES_TABLE,
    MYSQL_CHAT_DATA_DASHBOARD_TABLE,
    MYSQL_CHAT_DATA_DASHBOARD_PANEL_TABLE,
    MYSQL_SQL_QUERY_HISTORY_TABLE,
    MYSQL_SQL_QUERY_FAVORITE_TABLE,
    MYSQL_DATASOURCE_GROUP_TABLE,
    MYSQL_DATASOURCE_TABLE_SCHEMA_TABLE,
    MYSQL_DATASOURCE_TABLE_GROUP_REL_TABLE,
    MYSQL_DATASOURCE_COLUMN_DICT_TABLE,
    MYSQL_SEMANTIC_CONFIG_TABLE,
    MYSQL_CHAT_DATA_QUERY_TASK_TABLE,
    MYSQL_CHAT_DATA_QUERY_EXECUTION_TABLE,
    MYSQL_CHAT_DATA_SQL_RESULT_CACHE_TABLE,
    MYSQL_CHAT_DATA_EVAL_CASE_SET_TABLE,
    MYSQL_CHAT_DATA_EVAL_CASE_TABLE,
    MYSQL_CHAT_DATA_EVAL_RUN_TABLE,
    MYSQL_CHAT_DATA_EVAL_RUN_CASE_TABLE,
]


# ============ 兼容接口 ============

def _get_database_type() -> str:
    """获取业务表数据库类型。"""
    if any(
        os.getenv(key)
        for key in ("FXCJG_DB_HOST", "FXCJG_DB", "FXCJG_DB_USER", "FXCJG_DB_PASSWORD")
    ):
        return "mysql"
    raise ValueError("缺少业务数据库配置: FXCJG_DB_*")


def get_tables(db_type: str = None) -> list:
    """根据数据库类型获取表结构"""
    if db_type and db_type.lower() != "mysql":
        raise ValueError(f"业务表仅支持 mysql，当前传入: {db_type}")
    _get_database_type()
    return MYSQL_TABLES


# 向后兼容（根据环境变量自动选择）
def _get_all_tables():
    _get_database_type()
    return MYSQL_TABLES

ALL_TABLES = _get_all_tables()
