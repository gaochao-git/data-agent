# 630 Version TODO

## 待办项

### 1. SQL / 数据源报错分类【已处理】

- 范围：
  - 数据源同步
  - SQL 查询执行
- 目标：
  - 对用户可见报错做基础分类，至少包含：
    - 权限不足
    - SQL 语法错误
    - 连接失败 / 网络异常
    - 超时
    - 数据源不存在或配置错误
    - 其它未知错误
- 处理原则：
  - 先基于当前驱动和实际异常信息做最小改造，不额外扩业务口径
  - 不允许静默吞异常，需保留原始错误日志和必要上下文
  - 面向用户返回结构中增加统一错误类别字段，便于前端展示

### 2. SQL 语法错误透出具体信息【已处理】

- 目标：
  - SQL 语法错误时，前端要能看到数据库返回的具体错误信息
  - 让用户知道 SQL 大致错在哪里，而不是只看到“执行失败”
- 实现口径：
  - 捕获数据库驱动返回的 SQL 语法相关异常
  - 保留并透传对应错误消息
  - 不自行猜测或重写 SQL 错误内容，优先使用数据库原始错误信息
- 备注：
  - 如后续需要，可再补充不同数据库错误码到统一错误类别的映射

### 3. SQL 查询编辑框增加箭头入口【已处理】

- 目标：
  - SQL 查询编辑区域增加明显箭头标识
  - 点击后自动进入 SQL 编辑框并聚焦光标
- 预期行为：
  - 用户点击箭头后，无需再次点击编辑器
  - 如果当前是代码编辑器组件，优先聚焦到现有光标位置；如无选区，可放到末尾

### 4. Hive/MySQL 类型选错导致同步卡死【已处理】

- 现象：
  - 同步数据源时，如果实际是 Hive，却误选成 MySQL，可能因为连接阶段没有及时超时，导致同步请求长时间阻塞
  - 请求阻塞后，可能拖住后台服务，表现为接口长时间不返回，严重时影响其它请求
- 分析方向：
  - 当前同步链路里，MySQL / Hive 连接超时控制可能不一致
  - 类型选错时，底层网络连接、认证或协议握手可能一直等待，服务层没有及时失败返回
  - 需要重点检查：
    - 数据源同步入口
    - `services/mysql_sync.py`
    - `services/hive_sync.py`
    - 相关执行器中的连接超时、socket 超时、查询超时配置
- 目标：
  - 无论把 Hive 选成 MySQL，还是把 MySQL 选成 Hive，都必须在可控时间内失败返回，不能把服务卡死
- 处理方向：
  - 为同步链路补齐统一的连接超时和查询超时
  - 必要时在上层增加异步超时包裹，避免底层驱动无限等待
  - 对“数据源类型选错 / 协议不匹配”场景返回明确提示，引导用户检查数据源类型
  - 记录超时和异常上下文，便于区分是 DNS、TCP、认证、协议握手还是驱动层阻塞

### 5. `sql_query_history` 表结构调整【已处理】

- 背景：
  - 当前 `sql_query_history` 已从“去重推荐池”逐步转成“每次执行一条的执行历史”
  - 在不去重模式下，以下字段价值已明显下降：
    - `frequency`
    - `avg_execution_time_ms`
    - `card_icon`
    - `card_color`
- 目标：
  - 保留执行历史最核心的审计信息
  - 增加单次执行视角下更有价值的字段：
    - `execution_time_ms`
    - `result_row_count`
    - `success_flag`
    - `error_message`
- 迁移脚本：
  - `server/scripts/upgrade_sql_query_history_execution_audit.sql`
- 核心 SQL 变更语句：

```sql
ALTER TABLE `sql_query_history`
  ADD COLUMN `execution_time_ms` INT NULL COMMENT '单次执行耗时(毫秒)' AFTER `last_used`,
  ADD COLUMN `result_row_count` INT NULL COMMENT '返回结果行数' AFTER `execution_time_ms`,
  ADD COLUMN `success_flag` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '执行是否成功: 1-成功, 0-失败' AFTER `result_row_count`,
  ADD COLUMN `error_message` TEXT NULL COMMENT '执行失败时的错误信息' AFTER `success_flag`,
  DROP COLUMN `frequency`,
  DROP COLUMN `avg_execution_time_ms`,
  DROP COLUMN `card_icon`,
  DROP COLUMN `card_color`;
```

- 已同步更新：
  - `server/utils/lang_util/models.py`
  - `server/utils/lang_util/schema.py`
  - `routes._record_sql_query_history`
  - 智能体 SQL 历史写入链路
  - SQL 编辑器手工执行和异步 `query-tasks` 成功/失败写入链路
- 记录口径：
  - `success_flag` / `error_message` 覆盖 SQL 执行返回失败的场景
  - `result_row_count` 记录最终返回前端的数据行数

## 说明

- 当前文件仅记录待办，不包含实现。
- 下次修改时，先按最小改动原则分别处理后端报错口径和前端交互。
# langcahin升级兼容qwen3.5


# 创建查询任务管理表
CREATE TABLE IF NOT EXISTS `chat_data_query_execution` (
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
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_execution_id` (`execution_id`),
  KEY `idx_execution_task_id` (`task_id`),
  KEY `idx_execution_user_ds_time` (`user_id`, `datasource_id`, `create_time`),
  KEY `idx_execution_ds_status` (`datasource_id`, `sql_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData SQL执行记录表';
# SQL编辑查询逻辑分组显示配置表
CREATE TABLE `datasource_table_group_rel` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `group_id` varchar(64) NOT NULL DEFAULT '' COMMENT '数据源组ID',
  `table_group_name` varchar(100) NOT NULL DEFAULT '' COMMENT '表分组名称',
  `table_name` varchar(128) NOT NULL DEFAULT '' COMMENT '表名',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_grp_tbl_group` (`group_id`,`table_group_name`,`table_name`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8mb4 COMMENT='数据源表分组关系表';

# ChatData技能管理表
CREATE TABLE IF NOT EXISTS `chat_data_skill` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `skill_id` varchar(64) NOT NULL DEFAULT '' COMMENT '技能ID',
  `skill_name` varchar(100) NOT NULL DEFAULT '' COMMENT '技能名称',
  `skill_title` varchar(200) NOT NULL DEFAULT '' COMMENT '技能标题',
  `skill_desc` varchar(1000) NOT NULL DEFAULT '' COMMENT '技能描述',
  `skill_category` varchar(100) NOT NULL DEFAULT '' COMMENT '技能分类',
  `skill_source` varchar(20) NOT NULL DEFAULT 'user' COMMENT '技能来源: system/user',
  `visibility_type` varchar(20) NOT NULL DEFAULT 'private' COMMENT '可见范围: private/global',
  `enabled_flag` tinyint NOT NULL DEFAULT '1' COMMENT '是否启用: 0-否, 1-是',
  `skill_publish_status` varchar(20) NOT NULL DEFAULT 'draft' COMMENT '发布状态: draft/published/disabled',
  `current_skill_version_no` int NOT NULL DEFAULT '0' COMMENT '当前发布版本号',
  `owner_user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '归属用户ID',
  `skill_root_dir` varchar(500) NOT NULL DEFAULT '' COMMENT '技能根目录',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_skill_id` (`skill_id`),
  UNIQUE KEY `uniq_skill_name` (`skill_name`),
  KEY `idx_skill_source` (`skill_source`,`skill_publish_status`),
  KEY `idx_skill_owner` (`owner_user_id`,`skill_publish_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData技能表';

# ChatData技能版本表
CREATE TABLE IF NOT EXISTS `chat_data_skill_version` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `skill_id` varchar(64) NOT NULL DEFAULT '' COMMENT '技能ID',
  `skill_version_no` int NOT NULL DEFAULT '0' COMMENT '技能版本号',
  `skill_version_dir` varchar(500) NOT NULL DEFAULT '' COMMENT '技能版本目录',
  `version_publish_status` varchar(20) NOT NULL DEFAULT 'draft' COMMENT '发布状态: draft/published/disabled',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_skill_version` (`skill_id`,`skill_version_no`),
  KEY `idx_version_skill` (`skill_id`,`version_publish_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData技能版本表';

# ChatData技能收藏表
CREATE TABLE IF NOT EXISTS `chat_data_skill_favorite` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `favorite_user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '收藏用户ID',
  `skill_id` varchar(64) NOT NULL DEFAULT '' COMMENT '技能ID',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_fav_user_skill` (`favorite_user_id`,`skill_id`),
  KEY `idx_fav_skill` (`skill_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData技能收藏表';

# ChatData SQL 查询结果缓存表
CREATE TABLE `chat_data_sql_result_cache` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
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
  KEY `idx_ds_update` (`datasource_id`,`update_time`)
) ENGINE=InnoDB AUTO_INCREMENT=87 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='ChatData SQL结果缓存表'


CREATE TABLE `chat_data_eval_case_set` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
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
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_case_set_id` (`case_set_id`),
  KEY `idx_eval_case_set_scene` (`datasource_id`, `semantic_config_id`),
  KEY `idx_eval_case_set_status` (`case_set_status`, `update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测测试集表';

CREATE TABLE `chat_data_eval_case` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
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
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_case_set_case` (`case_set_id`, `case_id`),
  KEY `idx_eval_case_set_id` (`case_set_id`),
  KEY `idx_eval_case_type` (`query_type`),
  KEY `idx_eval_case_source` (`case_source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测用例表';

CREATE TABLE `chat_data_eval_run` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
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
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_run_id` (`run_id`),
  KEY `idx_eval_run_scene` (`datasource_id`, `semantic_config_id`, `create_time`),
  KEY `idx_eval_run_status` (`run_status`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测运行记录表';

CREATE TABLE `chat_data_eval_run_case` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
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
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_eval_run_case_id` (`run_case_id`),
  UNIQUE KEY `uniq_eval_run_case` (`run_id`, `case_id`),
  KEY `idx_eval_run_case_run` (`run_id`),
  KEY `idx_eval_run_case_case` (`case_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData评测单用例结果表';


# 增加自定义指标集表
ALTER TABLE semantic_config ADD COLUMN scope_type varchar(20) NOT NULL DEFAULT 'public' COMMENT '范围: public公共, private个人';

# 增加审计用户名字段

```sql
ALTER TABLE `chat_data_dashboard`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_dashboard_panel`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_eval_case`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_eval_case_set`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_eval_run`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_eval_run_case`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_query_execution`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_query_task`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_skill`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_skill_favorite`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `chat_data_skill_version`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `datasource_group`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `datasource_table`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `datasource_table_group_rel`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `semantic_config`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `sql_query_favorite`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `uploaded_files`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `user_threads`
  ADD COLUMN `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称' AFTER `create_by`,
  ADD COLUMN `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称' AFTER `update_by`;

ALTER TABLE `datasource_table`
  ADD COLUMN `table_business_desc` varchar(255) NOT NULL DEFAULT '' COMMENT '表业务说明' AFTER `table_desc`;

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
```
