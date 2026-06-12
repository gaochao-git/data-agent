"""
LangGraph 公共数据库表结构定义

支持 SQLite 和 MySQL 两种数据库
"""

# ============ SQLite 表结构 ============

SQLITE_USER_THREADS_TABLE = """
CREATE TABLE IF NOT EXISTS user_threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    thread_id TEXT NOT NULL UNIQUE,
    thread_title TEXT DEFAULT '未命名会话',
    agent_id TEXT,
    is_favorited INTEGER DEFAULT 0,
    delete_flag INTEGER DEFAULT 0,
    create_time TEXT NOT NULL,
    update_time TEXT,
    create_by TEXT NOT NULL DEFAULT '',
    update_by TEXT NOT NULL DEFAULT ''
)
"""

SQLITE_UPLOADED_FILES_TABLE = """
CREATE TABLE IF NOT EXISTS uploaded_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT UNIQUE,
    thread_id TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    storage_dir TEXT NOT NULL,
    parse_status TEXT DEFAULT 'pending',
    parse_filename TEXT,
    delete_flag INTEGER DEFAULT 0,
    create_time TEXT NOT NULL,
    update_time TEXT,
    create_by TEXT NOT NULL DEFAULT '',
    update_by TEXT NOT NULL DEFAULT ''
)
"""

SQLITE_CHAT_DATA_DASHBOARD_TABLE = """
CREATE TABLE IF NOT EXISTS chat_data_dashboard (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dashboard_id TEXT NOT NULL UNIQUE,
    dashboard_name TEXT NOT NULL,
    dashboard_desc TEXT,
    user_id TEXT NOT NULL,
    datasource_id TEXT,
    dashboard_theme TEXT DEFAULT 'light',
    background_id TEXT,
    is_public INTEGER DEFAULT 0,
    delete_flag INTEGER DEFAULT 0,
    create_time TEXT NOT NULL,
    update_time TEXT,
    create_by TEXT NOT NULL DEFAULT '',
    update_by TEXT NOT NULL DEFAULT ''
)
"""

SQLITE_CHAT_DATA_DASHBOARD_PANEL_TABLE = """
CREATE TABLE IF NOT EXISTS chat_data_dashboard_panel (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id TEXT NOT NULL UNIQUE,
    dashboard_id TEXT NOT NULL,
    panel_name TEXT NOT NULL,
    panel_sql TEXT NOT NULL,
    chart_type TEXT DEFAULT 'table',
    chart_config TEXT,
    datasource_id TEXT,
    layout_x INTEGER DEFAULT 0,
    layout_y INTEGER DEFAULT 0,
    layout_w INTEGER DEFAULT 6,
    layout_h INTEGER DEFAULT 4,
    refresh_interval INTEGER,
    source_thread_id TEXT,
    delete_flag INTEGER DEFAULT 0,
    create_time TEXT NOT NULL,
    update_time TEXT,
    create_by TEXT NOT NULL DEFAULT '',
    update_by TEXT NOT NULL DEFAULT ''
)
"""

SQLITE_SQL_QUERY_HISTORY_TABLE = """
CREATE TABLE IF NOT EXISTS sql_query_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id TEXT NOT NULL,
    user_id TEXT,
    sql_text TEXT NOT NULL,
    query_title TEXT,
    query_type TEXT DEFAULT 'sql',
    builder_config TEXT,
    last_used TEXT,
    execution_time_ms INTEGER,
    result_row_count INTEGER,
    success_flag INTEGER NOT NULL DEFAULT 1,
    error_message TEXT,
    delete_flag INTEGER DEFAULT 0,
    create_time TEXT NOT NULL,
    update_time TEXT,
    create_by TEXT NOT NULL DEFAULT '',
    update_by TEXT NOT NULL DEFAULT ''
)
"""

SQLITE_SQL_QUERY_FAVORITE_TABLE = """
CREATE TABLE IF NOT EXISTS sql_query_favorite (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id TEXT NOT NULL,
    user_id TEXT,
    semantic_config_id TEXT,
    query_type TEXT NOT NULL DEFAULT 'sql',
    favorite_title TEXT NOT NULL,
    query_text TEXT NOT NULL,
    builder_config TEXT,
    delete_flag INTEGER DEFAULT 0,
    create_time TEXT NOT NULL,
    update_time TEXT,
    create_by TEXT NOT NULL DEFAULT '',
    update_by TEXT NOT NULL DEFAULT ''
)
"""

SQLITE_CHAT_DATA_QUERY_TASK_TABLE = """
CREATE TABLE IF NOT EXISTS chat_data_query_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    task_type TEXT NOT NULL DEFAULT 'sql',
    datasource_id TEXT NOT NULL,
    query_type TEXT NOT NULL DEFAULT 'sql',
    task_status TEXT NOT NULL DEFAULT 'pending',
    progress_text TEXT,
    request_payload TEXT NOT NULL,
    result_payload TEXT,
    remote_task_payload TEXT,
    error_message TEXT,
    started_time TEXT,
    finished_time TEXT,
    delete_flag INTEGER DEFAULT 0,
    create_time TEXT NOT NULL,
    update_time TEXT,
    create_by TEXT NOT NULL DEFAULT '',
    update_by TEXT NOT NULL DEFAULT ''
)
"""

SQLITE_TABLES = [
    SQLITE_USER_THREADS_TABLE,
    SQLITE_UPLOADED_FILES_TABLE,
    SQLITE_CHAT_DATA_DASHBOARD_TABLE,
    SQLITE_CHAT_DATA_DASHBOARD_PANEL_TABLE,
    SQLITE_SQL_QUERY_HISTORY_TABLE,
    SQLITE_SQL_QUERY_FAVORITE_TABLE,
    SQLITE_CHAT_DATA_QUERY_TASK_TABLE,
]

# ============ MySQL 表结构 ============

MYSQL_USER_THREADS_TABLE = """
CREATE TABLE `user_threads` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '自增主键',
  `user_id` varchar(255) NOT NULL COMMENT '用户ID',
  `thread_id` varchar(255) NOT NULL COMMENT '会话ID',
  `thread_title` varchar(255) DEFAULT '未命名会话' COMMENT '会话标题',
  `agent_id` varchar(255) DEFAULT NULL COMMENT '智能体ID',
  `is_favorited` tinyint DEFAULT '0' COMMENT '是否收藏: 0-否, 1-是',
  `delete_flag` tinyint DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_thread_id` (`thread_id`),
  KEY `idx_user_id` (`user_id`),
  KEY `idx_agent_id` (`agent_id`)
) ENGINE=InnoDB  DEFAULT CHARSET=utf8mb4 COMMENT='用户会话表'
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
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
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
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
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
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
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
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
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
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
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
  `source_type` varchar(32) NOT NULL COMMENT '数据源类型: mysql | hive',
  `template_source` varchar(256) NOT NULL COMMENT '模板数据源，格式 host:port:database',
  `member_sources_text` text COMMENT '组内数据源，多行文本，格式 host:port:database',
  `mapped_modules_json` text COMMENT '模块映射JSON',
  `is_default` tinyint NOT NULL DEFAULT '0' COMMENT '是否默认数据源: 0-否, 1-是',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(128) NOT NULL DEFAULT '' COMMENT '更新人',
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
  `columns_json` text COMMENT '字段结构JSON',
  `sort_no` int NOT NULL DEFAULT '0' COMMENT '排序号',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(128) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_group_id_table_names` (`group_id`, `table_names`),
  KEY `idx_group_id` (`group_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源表结构缓存表';
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
  `sort_no` int NOT NULL DEFAULT '0' COMMENT '排序号',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(128) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(128) NOT NULL DEFAULT '' COMMENT '更新人',
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
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_task_id` (`task_id`),
  KEY `idx_task_type_task_status` (`task_type`, `task_status`),
  KEY `idx_datasource_id_task_status` (`datasource_id`, `task_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData 异步查询任务表';
"""
MYSQL_TABLES = [
    MYSQL_USER_THREADS_TABLE,
    MYSQL_UPLOADED_FILES_TABLE,
    MYSQL_CHAT_DATA_DASHBOARD_TABLE,
    MYSQL_CHAT_DATA_DASHBOARD_PANEL_TABLE,
    MYSQL_SQL_QUERY_HISTORY_TABLE,
    MYSQL_SQL_QUERY_FAVORITE_TABLE,
    MYSQL_DATASOURCE_GROUP_TABLE,
    MYSQL_DATASOURCE_TABLE_SCHEMA_TABLE,
    MYSQL_SEMANTIC_CONFIG_TABLE,
    MYSQL_CHAT_DATA_QUERY_TASK_TABLE,
]


# ============ 兼容接口 ============

import os

def _get_database_type() -> str:
    """获取业务表数据库类型。"""
    if any(
        os.getenv(key)
        for key in ("FXCJG_DB_HOST", "FXCJG_DB_PORT", "FXCJG_DB", "FXCJG_DB_USER", "FXCJG_DB_PASSWORD")
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
