# 下一版本变更

## 数据库变更

上线前执行：

```sql
CREATE TABLE `chat_data_meta_global_enum` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `business_domain` varchar(128) NOT NULL DEFAULT '' COMMENT '业务域（如支付业务、信贷业务），空为通用域；不同域同名枚举互相隔离',
  `enum_name` varchar(128) NOT NULL COMMENT '全局枚举名，仅字母数字下划线，域内唯一',
  `enum_desc` varchar(512) NOT NULL DEFAULT '' COMMENT '枚举说明',
  `enum_json` text NOT NULL COMMENT '枚举值JSON文本: [{"value":"...","label":"..."}]',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_domain_enum_name` (`business_domain`,`enum_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='全局枚举表';

CREATE TABLE `chat_data_setting` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `setting_key` varchar(128) NOT NULL COMMENT '变量名，与 env 同名',
  `setting_value` varchar(2048) NOT NULL COMMENT '变量值（字符串存储，按注册类型解析）',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识 0未删除 1已删除（即重置回退）',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_setting_key` (`setting_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData 后台配置变量表';

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
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `run_id` varchar(64) NOT NULL COMMENT '运行ID',
  `owner_token` varchar(64) DEFAULT NULL COMMENT '当前运行者 fencing token',
  `message_id` varchar(64) NOT NULL DEFAULT '' COMMENT '消息ID，运行记录等于运行ID',
  `record_type` varchar(16) NOT NULL COMMENT '记录类型: run/message',
  `queue_type` varchar(16) NOT NULL DEFAULT 'next-step' COMMENT '队列类型: next-step-下个模型步骤注入, next-turn-下一轮处理',
  `record_content` mediumtext NOT NULL COMMENT '记录内容：消息行为追加消息文本，运行行为运行状态JSON',
  `record_meta` text COMMENT '排队消息随行元数据JSON: {file_selections, file_ids}',
  `record_status` varchar(16) NOT NULL COMMENT '运行或消息状态',
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
  UNIQUE KEY `uniq_agent_inbox_msg` (`record_type`,`message_id`),
  KEY `idx_agent_inbox_conv` (`conversation_id`,`user_id`,`record_type`,`record_status`),
  KEY `idx_agent_inbox_run` (`run_id`,`record_type`,`record_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData运行消息收件箱';

-- 智能体定时任务：任务定义 + 触发实例（需在全局唯一写主库执行；模块说明见 apps/chat_data_app/services/agent_schedule/README.md）
CREATE TABLE `chat_data_agent_schedule` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `job_id` varchar(64) NOT NULL DEFAULT '' COMMENT '任务业务ID',
  `user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '业务归属用户ID',
  `conversation_id` varchar(64) NOT NULL DEFAULT '' COMMENT '绑定的会话ID，conversation_mode=new_each_run 时为空',
  `conversation_mode` varchar(16) NOT NULL DEFAULT 'fixed' COMMENT '会话绑定方式：fixed-指定会话，new_each_run-每次执行新建会话',
  `job_title` varchar(100) NOT NULL DEFAULT '' COMMENT '任务标题',
  `job_prompt` text NOT NULL COMMENT '任务指令（提示词）',
  `schedule_type` varchar(16) NOT NULL DEFAULT 'cron' COMMENT '调度类型：cron-周期，once-单次',
  `cron_expr` varchar(64) NOT NULL DEFAULT '' COMMENT '五字段 cron 表达式（分 时 日 月 周），schedule_type=cron 时有效',
  `plan_time` datetime DEFAULT NULL COMMENT '单次任务执行时间，schedule_type=once 时有效',
  `effective_end_time` datetime DEFAULT NULL COMMENT '有效期结束时间，NULL 表示长期有效',
  `enabled_flag` smallint(6) NOT NULL DEFAULT '1' COMMENT '启用状态：1-启用，0-停用',
  `next_trigger_time` datetime DEFAULT NULL COMMENT '下一次计划触发时间，NULL 表示无计划触发',
  `last_trigger_time` datetime DEFAULT NULL COMMENT '最近一次计划触发的计划时间点',
  `options_json` text COMMENT '运行选项 JSON（与智能问数 options 口径一致），服务端解析校验',
  `delete_flag` smallint(6) NOT NULL DEFAULT '0' COMMENT '删除标记：0-未删除，1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_schedule_job_id` (`job_id`),
  KEY `idx_schedule_user` (`user_id`, `delete_flag`),
  KEY `idx_schedule_next_trigger` (`enabled_flag`, `delete_flag`, `next_trigger_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能体定时任务定义表';

CREATE TABLE `chat_data_agent_schedule_run` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `job_id` varchar(64) NOT NULL DEFAULT '' COMMENT '任务业务ID',
  `user_id` varchar(100) NOT NULL DEFAULT '' COMMENT '业务归属用户ID',
  `conversation_id` varchar(64) NOT NULL DEFAULT '' COMMENT '执行的会话ID',
  `plan_time` datetime NOT NULL COMMENT '计划触发时间点',
  `trigger_status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT '状态：pending-待派发，claimed-已抢占，dispatched-已提交Run，succeeded/failed/cancelled-终态，skipped-跳过',
  `trigger_type` varchar(16) NOT NULL DEFAULT 'schedule' COMMENT '触发来源：schedule-计划触发，manual-手动立即执行',
  `attempt_count` int(11) NOT NULL DEFAULT '0' COMMENT '派发尝试次数',
  `owner_node` varchar(128) NOT NULL DEFAULT '' COMMENT '抢占节点标识（hostname:pid），仅排查用',
  `run_id` varchar(64) NOT NULL DEFAULT '' COMMENT '派发的 Agent Run ID（chat_data_agent_inbox.run_id）',
  `error_message` varchar(1000) NOT NULL DEFAULT '' COMMENT '失败/跳过原因',
  `claimed_time` datetime DEFAULT NULL COMMENT '抢占时间',
  `dispatched_time` datetime DEFAULT NULL COMMENT '提交 Agent Run 时间',
  `finished_time` datetime DEFAULT NULL COMMENT '进入终态时间',
  `delete_flag` smallint(6) NOT NULL DEFAULT '0' COMMENT '删除标记：0-未删除，1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_schedule_job_plan` (`job_id`, `plan_time`),
  KEY `idx_schedule_run_scan` (`trigger_status`, `plan_time`),
  KEY `idx_schedule_run_job` (`job_id`, `create_time`),
  KEY `idx_schedule_run_user` (`user_id`, `trigger_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能体定时任务触发实例表';

-- 智能体定时任务全局控制表（固定单行，后台一键暂停入口；缺行时服务端会惰性补建）
CREATE TABLE `chat_data_schedule_control` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID，固定为1',
  `paused_flag` smallint(6) NOT NULL DEFAULT '0' COMMENT '全局暂停：0-正常，1-暂停所有调度',
  `pause_reason` varchar(255) NOT NULL DEFAULT '' COMMENT '暂停原因',
  `delete_flag` smallint(6) NOT NULL DEFAULT '0' COMMENT '删除标记：0-未删除，1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能体定时任务全局控制表';

CREATE TABLE `chat_data_plugin` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `plugin_name` varchar(64) NOT NULL DEFAULT '' COMMENT '插件名(目录名/资源id)',
  `version` varchar(32) NOT NULL DEFAULT '' COMMENT '插件版本',
  `display_name` varchar(64) NOT NULL DEFAULT '' COMMENT '展示名',
  `description` varchar(500) NOT NULL DEFAULT '' COMMENT '描述',
  `enabled_flag` tinyint NOT NULL DEFAULT '1' COMMENT '启用标识: 0-禁用, 1-启用',
  `manifest_text` text COMMENT 'plugin.json 原文(JSON文本)',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_plugin_name` (`plugin_name`),
  KEY `idx_plugin_enabled_delete` (`enabled_flag`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData 插件状态表';

ALTER TABLE datasource_table ADD COLUMN indexes_json TEXT NULL COMMENT '表级物理索引和手工索引 JSON';
ALTER TABLE user_threads ADD COLUMN datasource_id varchar(255) NOT NULL DEFAULT '' COMMENT '会话绑定数据源组ID' after agent_id;

-- 唯一索引改造
-- 清理后复查应返回空，再建索引
DELETE FROM semantic_config WHERE id NOT IN (SELECT min_id FROM (SELECT MIN(id) AS min_id FROM semantic_config GROUP BY group_id, semantic_config_name, delete_flag) t);
ALTER TABLE semantic_config ADD UNIQUE KEY uniq_grp_id_cfg_name_del_flag (group_id, semantic_config_name,delete_flag);

-- CHANGE 一步完成改名+改注释（RENAME COLUMN 不会更新列注释）
ALTER TABLE semantic_config CHANGE COLUMN ontology_rules business_notes text COMMENT '业务说明';

-- 技能表清理废弃字段：skill_scope 从未使用；root_dir/version_dir 已由推导规则替代
ALTER TABLE chat_data_skill DROP COLUMN skill_scope;
ALTER TABLE chat_data_skill DROP COLUMN skill_root_dir;
ALTER TABLE chat_data_skill_version DROP COLUMN skill_version_dir;

-- 本体对象类型表删除「复数展示名 / 上下文可见性」两列（实验期裁剪，Palantir 对齐字段确认无用；仅 dev 已建表需执行）
ALTER TABLE chat_data_ontology_object_type DROP COLUMN plural_display_name;
ALTER TABLE chat_data_ontology_object_type DROP COLUMN visibility;

-- 本体重建四张表：对象类型/属性/关系类型/对象类型映射（属性一等实体；口径不落本体，唯一权威在语义层；
-- 绑定独立成映射表对齐 ossie 概念层/映射层分离，主键=标识属性组合、标题键=标题属性，均由属性表表达）
CREATE TABLE `chat_data_ontology_object_type` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_type_name` varchar(128) NOT NULL COMMENT '对象类型API名，仅字母数字下划线，certified 后冻结',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名',
  `description_text` varchar(1024) NOT NULL DEFAULT '' COMMENT '对象类型描述',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '生命周期: draft-草稿, experimental-实验, certified-认证, deprecated-废弃(不可逆)',
  `domain_id` bigint DEFAULT NULL COMMENT '所属领域ID（chat_data_ontology_domain.id，逻辑关联无外键），NULL=未分域',
  `deprecation_reason` varchar(512) NOT NULL DEFAULT '' COMMENT '废弃原因，status=deprecated 时有效',
  `deprecated_until` datetime DEFAULT NULL COMMENT '废弃删除期限，status=deprecated 时有效',
  `successor_object_type_name` varchar(128) NOT NULL DEFAULT '' COMMENT '替代对象类型API名，status=deprecated 时有效',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_object_type_name` (`object_type_name`),
  KEY `idx_object_type_status` (`lifecycle_status`,`delete_flag`),
  KEY `idx_object_type_domain_id` (`domain_id`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体对象类型表';

CREATE TABLE `chat_data_ontology_property` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_type_id` bigint NOT NULL COMMENT '所属本体对象类型ID（chat_data_ontology_object_type.id，逻辑关联无外键）',
  `property_name` varchar(128) NOT NULL COMMENT '属性API名，仅字母数字下划线，对象类型内唯一，certified 后禁删',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名',
  `description_text` varchar(1024) NOT NULL DEFAULT '' COMMENT '属性描述',
  `value_type` varchar(32) NOT NULL DEFAULT 'String' COMMENT '值类型: String/Integer/Decimal/Boolean/Date/DateTime 或 enum(引用全局枚举)',
  `enum_ref` varchar(260) NOT NULL DEFAULT '' COMMENT '全局枚举引用，value_type=enum 时非空: "enum_name" 或 "business_domain:enum_name"',
  `render_hints_json` text COMMENT '渲染提示JSON文本: {"searchable":true,"sortable":true,"low_cardinality":false}',
  `is_identifier` tinyint NOT NULL DEFAULT '0' COMMENT '标识属性: 1-参与对象类型主键组合（业务主键，禁自增行号）',
  `is_title` tinyint NOT NULL DEFAULT '0' COMMENT '标题属性: 1-对象类型标题键（每对象类型至多一个）',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_object_type_property` (`object_type_id`,`property_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体属性表';

CREATE TABLE `chat_data_ontology_relation_type` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `relation_type_name` varchar(128) NOT NULL COMMENT '关系类型API名，仅字母数字下划线，全局唯一',
  `from_object_type_id` bigint NOT NULL COMMENT '起始对象类型ID（chat_data_ontology_object_type.id）',
  `to_object_type_id` bigint NOT NULL COMMENT '目标对象类型ID（chat_data_ontology_object_type.id）',
  `from_columns_json` text COMMENT '起始侧关联列JSON文本: ["属性名"], 与目标侧等长顺序一一对应',
  `to_columns_json` text COMMENT '目标侧关联列JSON文本: ["属性名"], 必须引用对方标识属性',
  `multiplicity_type` varchar(16) NOT NULL DEFAULT 'many_to_one' COMMENT '基数: many_to_one, one_to_one（多对多走 junction 对象类型表达）',
  `from_display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '正向方向展示名',
  `to_display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '反向方向展示名',
  `from_verbalize` varchar(512) NOT NULL DEFAULT '' COMMENT '正向自然语言模式串，如 "{from} 归属于 {to}"',
  `to_verbalize` varchar(512) NOT NULL DEFAULT '' COMMENT '反向自然语言模式串',
  `description_text` varchar(1024) NOT NULL DEFAULT '' COMMENT '关系类型描述',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '生命周期随对象类型级联: 对象类型降级自动同步，升级需手动确认',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_relation_type_name` (`relation_type_name`),
  KEY `idx_relation_from` (`from_object_type_id`,`delete_flag`),
  KEY `idx_relation_to` (`to_object_type_id`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体关系类型表';

-- 对象类型映射表：语义模型映射独立成表（对齐 ossie 本体文档/映射文档分离）。
-- 映射随对象类型 payload 提交、随对象类型 upsert 同事务全量重写（软删+重插，无独立生命周期）；
-- delete_flag 语义特殊：0=活跃，软删时置为行 id——软删行不占唯一索引槽位，同源可重绑；
-- 「一源一对象类型」由 uniq_mapping_semantic_source(含 delete_flag) 对活跃行直接约束
-- （DB 兜底，service 先查给友好报错）
CREATE TABLE `chat_data_ontology_mapping` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_type_id` bigint NOT NULL COMMENT '所属本体对象类型ID（chat_data_ontology_object_type.id，逻辑关联无外键）',
  `semantic_config_id` varchar(128) NOT NULL COMMENT '语义模型配置ID（口径唯一权威在语义层，不触碰物理表）',
  `column_mappings_json` text COMMENT '字段映射JSON文本: {属性API名: 模型维度名}',
  `delete_flag` bigint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-活跃, 软删时置为行id（腾出唯一索引槽位）',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_mapping_object_type_source` (`object_type_id`,`semantic_config_id`,`delete_flag`),
  UNIQUE KEY `uniq_mapping_semantic_source` (`semantic_config_id`,`delete_flag`),
  KEY `idx_mapping_object_type` (`object_type_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体对象类型映射表';

-- 本体五表对齐两条命名规范（2026-10-19）：
--   ① 术语：表名 object/relation → object_type/relation_type，列名 object_name/object_id/
--      relation_name/from_object_id/to_object_id 同步加 _type；
--   ② 红线「除 id 外必须 ≥2 个单词下划线」：description → description_text、
--      status → lifecycle_status、multiplicity → multiplicity_type。
-- 已建表的存量环境（dev）按序执行以下迁移；新环境直接按上方 CREATE TABLE 建表，无需执行。
-- 注意：MySQL 8 RENAME COLUMN/RENAME TABLE 自动同步索引定义但不改索引名，索引名需单独 RENAME INDEX。

```sql
-- ① 表名对齐术语
RENAME TABLE `chat_data_ontology_object`   TO `chat_data_ontology_object_type`;
RENAME TABLE `chat_data_ontology_relation` TO `chat_data_ontology_relation_type`;

-- ② 列名：红线（单词列加后缀）
ALTER TABLE `chat_data_ontology_domain`       RENAME COLUMN `description`  TO `description_text`;
ALTER TABLE `chat_data_ontology_object_type`  RENAME COLUMN `description`  TO `description_text`;
ALTER TABLE `chat_data_ontology_object_type`  RENAME COLUMN `status`       TO `lifecycle_status`;
ALTER TABLE `chat_data_ontology_property`     RENAME COLUMN `description`  TO `description_text`;
ALTER TABLE `chat_data_ontology_relation_type` RENAME COLUMN `multiplicity` TO `multiplicity_type`;
ALTER TABLE `chat_data_ontology_relation_type` RENAME COLUMN `description`  TO `description_text`;
ALTER TABLE `chat_data_ontology_relation_type` RENAME COLUMN `status`       TO `lifecycle_status`;

-- ③ 列名：术语（对象类型/关系类型标识符）
ALTER TABLE `chat_data_ontology_object_type`   RENAME COLUMN `object_name`     TO `object_type_name`;
ALTER TABLE `chat_data_ontology_property`      RENAME COLUMN `object_id`       TO `object_type_id`;
ALTER TABLE `chat_data_ontology_relation_type` RENAME COLUMN `relation_name`   TO `relation_type_name`;
ALTER TABLE `chat_data_ontology_relation_type` RENAME COLUMN `from_object_id`  TO `from_object_type_id`;
ALTER TABLE `chat_data_ontology_relation_type` RENAME COLUMN `to_object_id`    TO `to_object_type_id`;
ALTER TABLE `chat_data_ontology_mapping`       RENAME COLUMN `object_id`       TO `object_type_id`;

-- ④ 索引名对齐新列名
ALTER TABLE `chat_data_ontology_object_type`   RENAME INDEX `uniq_object_name`           TO `uniq_object_type_name`;
ALTER TABLE `chat_data_ontology_object_type`   RENAME INDEX `idx_object_status`          TO `idx_object_type_status`;
ALTER TABLE `chat_data_ontology_property`      RENAME INDEX `uniq_object_property`       TO `uniq_object_type_property`;
ALTER TABLE `chat_data_ontology_relation_type` RENAME INDEX `uniq_relation_name`         TO `uniq_relation_type_name`;
ALTER TABLE `chat_data_ontology_mapping`       RENAME INDEX `uniq_mapping_object_source` TO `uniq_mapping_object_type_source`;
ALTER TABLE `chat_data_ontology_mapping`       RENAME INDEX `idx_mapping_object`         TO `idx_mapping_object_type`;

-- ⑤ 补遗：更早版本建的库还带旧列/旧索引名（fxcjg_ai 3307 实测，2026-10-19 已执行），上方四步未覆盖；
--    仅当库中存在旧名时执行，已对齐的环境会因列/索引不存在报错，跳过即可
ALTER TABLE `chat_data_ontology_object_type`   RENAME COLUMN `successor_object_name` TO `successor_object_type_name`;
ALTER TABLE `chat_data_ontology_object_type`   RENAME INDEX `idx_object_domain_id`   TO `idx_object_type_domain_id`;
```

-- 模型注册表：智能问数模型清单页面化管理（替代 CHAT_DATA_MODELS_FILE JSON 文件）
-- 除身份/连接/状态列外，全部模型参数收在 params_json（JSON 文本），扩参不改表结构
-- 注册键 model_key：服务端自动生成的唯一引用键（格式 {请求名slug}-{8位随机hex}，URL 安全；
-- 存量含 # / 等老键继续可用，管理端写接口用 body/query 传键不走 URL 路径）；
-- model_id 是上游请求名；业务唯一性=(provider, model_id, delete_flag)，软删行请求名
-- 改写墓碑名（{原名}__d{id}_{日期}）不占用组合，重加同名生成全新行
-- 【适用范围】新装库只跑本 CREATE TABLE；已建过旧版表的存量库跳过 CREATE，只执行下方「存量库迁移」的 ALTER 序列，两者不可都跑（列/键会重复报错）
CREATE TABLE chat_data_model_registry (
  id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  model_key varchar(128) NOT NULL DEFAULT '' COMMENT '注册键（服务端自动生成的唯一引用标识）',
  model_id varchar(128) NOT NULL DEFAULT '' COMMENT '模型请求名（发给上游 API 的 model 参数）',
  provider varchar(128) NOT NULL DEFAULT '' COMMENT '提供商（与请求名组成业务唯一键，如 DeepSeek/阿里云/本地 vLLM）',
  display_name varchar(128) NOT NULL DEFAULT '' COMMENT '显示名（空则用 model_id）',
  base_url varchar(500) NOT NULL DEFAULT '' COMMENT 'API Base URL（连接信息只以模型行为准）',
  api_key_cipher text NOT NULL COMMENT 'API Key SM4 密文（base64:base64，管理端只接受密文；空则该模型未配置鉴权）',
  params_json text NOT NULL COMMENT '模型参数 JSON（temperature/context_window/max_output_tokens/vlm/thinking 等，扩参不改表）',
  is_default smallint NOT NULL DEFAULT 0 COMMENT '唯一默认模型标志（1=默认）',
  enabled_flag smallint NOT NULL DEFAULT 1 COMMENT '启用标志（1=启用 0=停用）',
  remark_text varchar(500) NOT NULL DEFAULT '' COMMENT '备注',
  delete_flag smallint NOT NULL DEFAULT 0 COMMENT '软删标志（0=未删，非 0=已删；软删行请求名改写墓碑名）',
  create_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  update_time datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  create_by varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  create_by_name varchar(100) NOT NULL DEFAULT '' COMMENT '创建人姓名',
  update_by varchar(100) NOT NULL DEFAULT '' COMMENT '更新人ID',
  update_by_name varchar(100) NOT NULL DEFAULT '' COMMENT '更新人姓名',
  PRIMARY KEY (id),
  UNIQUE KEY uniq_model_key (model_key),
  UNIQUE KEY uniq_provider_model_del (provider, model_id, delete_flag),
  KEY idx_model_enabled_delete (enabled_flag, delete_flag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData 模型注册表';
```

## 功能变更

### 本体领域实体化：领域绑数据源组一次绑定（2026-10-17 落地）

领域从对象上的扁平文本标签（`chat_data_ontology_object.domain`）升级为独立实体 `chat_data_ontology_domain`，一对一绑定数据源组：对象挂领域即继承组（映射级联第一级锁定领域组），关系两端同领域即同组必合法。对象表 `domain` 文本列与 `business_notes` 死列删除，关系表 `business_notes` 死列删除（三者均无消费方）。

新环境建表 + 存量环境迁移 DDL：

```sql
-- 新表：本体领域（领域名唯一；组必填=取数后端锚点）
CREATE TABLE `chat_data_ontology_domain` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `domain_name` varchar(64) NOT NULL COMMENT '领域API名，仅字母数字下划线，全局唯一',
  `group_id` varchar(128) NOT NULL COMMENT '绑定的数据源组ID（一次绑定，映射与关系的组锚点）',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名，缺省用领域名',
  `description_text` varchar(512) NOT NULL DEFAULT '' COMMENT '领域描述',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除（挂有活跃对象类型时禁删）',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_domain_name` (`domain_name`),
  KEY `idx_domain_group` (`group_id`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体领域表';

-- 对象表：加领域引用，删旧列（domain 文本 / business_notes 死列）
ALTER TABLE `chat_data_ontology_object`
  ADD COLUMN `domain_id` bigint DEFAULT NULL COMMENT '领域ID（chat_data_ontology_domain.id，组随领域继承），NULL=未分域',
  ADD INDEX `idx_object_domain_id` (`domain_id`,`delete_flag`),
  DROP INDEX `idx_object_domain`,
  DROP COLUMN `domain`,
  DROP COLUMN `business_notes`;

-- 关系表：删业务说明死列（无 UI 无消费方）
ALTER TABLE `chat_data_ontology_relation` DROP COLUMN `business_notes`;
```

存量数据迁移（先建领域再迁对象，dev 库 2026-10-17 已执行）：

```sql
-- 1) 按存量 domain 文本建领域（group_id 按环境实际数据源组填写）
INSERT INTO chat_data_ontology_domain (domain_name, group_id, display_name, description_text)
SELECT 'ecommerce_trade', '<数据源组ID>', '电商交易', '电商交易业务域'
WHERE NOT EXISTS (SELECT 1 FROM chat_data_ontology_domain WHERE domain_name = 'ecommerce_trade');

-- 2) 对象按旧 domain 文本挂领域
UPDATE chat_data_ontology_object o
JOIN chat_data_ontology_domain d ON d.display_name = o.domain OR d.domain_name = o.domain
SET o.domain_id = d.id
WHERE o.delete_flag = 0 AND o.domain_id IS NULL;
```

API 变化：`OntologyObjectUpsertRequest` 的 `domain`/`business_notes` 字段删除、新增 `domain_id`；`OntologyRelationUpsertRequest` 的 `business_notes` 删除；对象列表过滤参数 `domain` → `domain_id`；新增领域 CRUD 路由（GET/PUT/DELETE `/admin/meta/ontology/domains/{domain_name}`、GET `/admin/meta/ontology/domains`）。目录 YAML 对象行新增 `domain`（领域名，未分域为空）。

### 本体关系状态手动升级入口（2026-10-16 落地）

现状（修复前）：关系状态只在创建时按两侧对象状态较低者计算，级联只降不升，编辑时状态原样保留——「升级需手动」没有操作入口；且删除重建无效（软删行复活复用旧状态）。

已实现：`POST /admin/meta/ontology/relations/{relation_name}/status` 迁移端点（目标状态只允许 draft/experimental/certified，deprecated 仅随对象级联；上限 = 两侧对象状态的较低者，关系不得高于任一端；任一端废弃时拒绝手动迁移）。前端关系面板加「状态」操作弹窗。涉及：schema（OntologyRelationStatusRequest）、ontology_service.set_relation_status_async、ontology_admin 路由、前端 RelationPanel、openapi 快照、单测。

### 本体映射层级重构：组 → 模型 → 维度映射（2026-10-16 落地，含 bindings→mapping 重命名）

绑定语义从「config_id + table_alias + 属性→物理列」改为「config_id + 属性→模型维度名」：本体层只引用模型维度（Ossie 映射文档同构），物理列解析归语义模型；实体锚点由映射维度所属表派生（同一映射全部维度必须同表，service 校验）；唯一键收紧为 (object_id, config_id) 与 (config_id)。API/payload/目录 YAML 键同步 bindings→mappings，table_alias 全链路删除。

上线前对已建环境的 DDL（新环境直接按上方建表语句）。表名已于 2026-10-17 由 `chat_data_ontology_object_mapping`（33 字符，超 AGENTS 32 上限）收敛为 `chat_data_ontology_mapping`（26 字符），仍持旧表名的环境先执行改名（索引随表自动迁移，无数据变更）：

```sql
RENAME TABLE `chat_data_ontology_object_mapping` TO `chat_data_ontology_mapping`;
```

```sql
ALTER TABLE `chat_data_ontology_mapping`
  DROP INDEX `uniq_mapping_object_source`,
  DROP INDEX `uniq_mapping_semantic_source`,
  DROP COLUMN `table_alias`,
  ADD UNIQUE KEY `uniq_mapping_object_source` (`object_id`,`semantic_config_id`,`delete_flag`),
  ADD UNIQUE KEY `uniq_mapping_semantic_source` (`semantic_config_id`,`delete_flag`);
```

存量数据迁移：column_mappings_json 右值由物理列名改写为对应模型维度名（按语义模型 dimensions 的 name/column 对照，dev 库 2026-10-16 已执行；生产若已有存量映射需按同口径生成 UPDATE）。

### 本体实现审计修复（2026-10-17 落地）

对本体四表/服务/注入/前端做了一轮全量正确性审计，修复以下问题（无 DDL；接口行为有增量变化，见末尾）：

**后端核心**
- 事务：`OntologyService` 写方法统一 `_transactional`（`transaction_scope` + DAO 环境会话复用），删除/状态迁移/领域写等多步操作整体回滚，不再产生半成功脏数据。
- 软删复活：关系/领域视图补 `delete_flag`，删后同名重建不再被误判为「已存在」；对象/关系复活按新建语义重置状态（对象回 `draft` 并清空废弃三元数据，关系按两侧对象状态重新派生），杜绝「响应 draft / 库内 deprecated」与「关系状态高于两端」。
- 关系状态：活跃编辑只降不升（`min(旧, 派生)`），结构编辑不再保留虚高状态。
- 认证门禁：每条映射必须非空且覆盖全部标识属性，空映射/主键未落模型的认证被拒绝。
- 编辑门禁：删除/改名属性或变更主键组合导致关系列悬空时直接拒绝。
- 组锚点：对象挂领域时映射模型必须在领域数据源组内；对象换域、领域改组均校验既有映射；数据源组删除前阻断仍被本体领域引用的组（40070）。
- 其他：`deprecated_until` 服务层解析（非法 40067）、领域名限 64、活跃同名重建改 40068（不再泄漏 allow_overwrite）、对象/关系写接口返回真实全量视图（关系不再泄漏 `*_json` 存储字段）、删除死代码、`upsert_object_full_async` 复杂度 18→达标。

**消费链路**
- 关系列翻译修复多映射串模型：按映射排序并用同一映射的 config 查维度列；新增 `from_columns_by_model`/`to_columns_by_model` 与 `unresolved` 标记（未解析必须回读模型 YAML）。
- 本体目录新增顶层 `models`（全部启用模型的 file/description/business_notes），二选一后未建对象模型不丢索引。
- 目录规模上限：`objects` 按名装入 40k 字符预算，超出 `truncated: true` + `omitted_objects`，其余交给 `file_grep`。
- 空启用集清理旧 `_ontology_catalog.yaml`/`_semantic_catalog.yaml` 残留；旧 `ontology/` 目录清理在 sandbox 不再刷警告。
- 属性补 description/enum_ref、关系补 description/status、空映射不渲染；objects/relations 确定性排序；提示词与 data-analyzing skill 同步新口径。

**前端**
- 修复未提交改动中 `PluginAdminPanel` 缺少 `AdminPanelToolbar` import（安装/刷新按钮不渲染）。
- 对象编辑破坏性比较对后端映射行做同口径归一化（不再每次编辑恒弹确认）；关系状态上限/替代对象候选改用全量对象（不受工具栏筛选影响）。
- 认证前置提示改用 `mappings_count`；废弃加二次确认；deprecated 禁编辑、certified 禁删；刷新后恢复选中详情；关系默认状态目标不再等于当前；保存后失效属性缓存；属性改名/删除同步映射键、切离 enum 清 `enum_ref`、Decimal 禁标识、标题勾选只影响非冻结行。

**接口/契约变化**
- PUT 对象返回全量详情（含 id/properties/mappings/status）；PUT 关系返回真实行 + `from_object_name`/`to_object_name`，不再返回 `*_json`。
- 活跃同名重复创建错误码由 40069 改为 40068（文案不含内部参数）。
- `_ontology_catalog.yaml` 新增 `models` / `truncated` / `omitted_objects` / `from_columns_by_model` / `to_columns_by_model` / `unresolved`；`objects[].properties[]` 可能含 `description`/`enum_ref`。
- 新增环境变量 `CHAT_DATA_ONTOLOGY_INJECTION_ENABLED`（默认 false，已登记 `server/.env.example`）。

**补充收紧（同日第二轮，审计遗留项）**
- 多源对象属性单源：非标识属性只能出现在一个映射（标识属性可跨源作主键），保存时拒绝跨源重复；前端映射编辑器同步排除已被其他映射占用的非标识属性。
- `one_to_one` 关系两侧都要求关联列引用标识属性（from 侧唯一性从"仅声明"升级为硬校验）；前端一对一模式起始列候选只保留标识属性。
- 数据源组授权：建会话与每轮 completion 绑定数据源组前复用机构授权口径（`can_access_datasource_async`，403 拒绝），避免无权用户把该组语义快照/本体目录物化进工作区；历史会话每轮完成前重新校验。
- 前端 ontology 目录 ESLint 清零（未使用导入、props 直改改为父组件持有数组 + 事件回传）。

**门禁**
- Sonar 认知复杂度本体相关函数全部 ≤15；`ruff check` 与 `ruff format --check` 全库通过；审计补测覆盖事务回滚/复活/认证门禁/组锚点/多映射翻译/目录截断。
- 覆盖率门禁补测：新增 `test_coverage_gate_ontology.py` / `test_coverage_gate_platform.py`，全量后端行覆盖 97.70%、分支覆盖 96.03%（均 ≥95%）。
- 已知遗留：多源对象「属性单源查重」仍属 §12 已知缺口；本体注入的数据源组授权沿用语义层既有口径，未在本轮收紧。（原遗留「映射表名 33 字符超限」已于同日改名收敛，见上。）

## 依赖变更

上线前执行：

```bash
python -m pip install opensandbox==0.1.15
python -m pip install redis==7.0.1
```

apache-ossie 离线 wheel 已重打（commit 7c03c46e：根形状平铺 + Ossie* 换名 + 严格校验）。**存量机器上 `pip install -r requirements.txt` 会因版本号未变（仍 0.2.0.dev0）静默跳过该 wheel，必须单独强制重装**：

```bash
python -m pip install --force-reinstall --no-deps wheels/apache_ossie-0.2.0.dev0-py3-none-any.whl
# 验证：应输出 <class 'ossie.models.OssieDocument'>（旧包是 OSI 前缀类名，装到旧包说明没换成功）
python -c "import ossie; print(ossie.OssieDocument)"
```

Redis 配置（密码为 SM4 密文，加密方式：`CHAT_DATA_ENV=dev python scripts/crypto.py encrypt <明文密码>`）：

```env
CHAT_DATA_REDIS_SENTINELS=62.234.152.217:26380
CHAT_DATA_REDIS_MASTER_NAME=chatdata-master
CHAT_DATA_REDIS_PASSWORD="<base64(iv):base64(ciphertext)>"
CHAT_DATA_REDIS_STREAM_TTL_SECONDS=3600
```

Run 实时事件使用 Redis Streams：

- `chatdata:run:{run_id}:events`：前端 SSE 实时订阅和断线续传（TTL 内唯一可重放事件源，Run 事件不落 MySQL）。
- 问答恢复的持久兜底在 `chat_data_agent_inbox` Run 行 `content.terminal_event`（done 发布成功后写入其 Redis 游标）。
- `chat_data_conversation_event` 保存用户消息以及每次模型调用完成后的会话展示快照；同一消息通过 `event_key` 幂等更新，整轮结束时再做最终收口。LangGraph checkpoint 只保存 Agent 执行状态。
### locate_object：本体对象定位（2026-10-17，query_object 执行版当日改判为导航版）

`data_analyzing_tool` 的 `query_object`（对象明细查询，执行模式）当天经实测与架构复盘改判：执行模式与指标语义层的工具入口重叠、当前规模下价值有限，**改造成纯导航的 `locate_object`**——按本体对象批量定位语义模型，降低模型 grep/精读 YAML 的定位猜测。无 DDL。

- **链路**：入参 `{"objects": [对象API名...（≤10 个）], "semantic_config_id": 可选过滤绑定}`；服务端按「本轮启用语义模型」加载本体注入 bundle（`OntologyDao.load_injection_bundle_async`，与目录物化同源），纯函数在 `services/ontology/object_locator.py`。返回每对象：绑定模型列表（semantic_config_id + 模型名 + 快照文件名 + column_mappings 属性→维度对照）、属性清单（含 value_type/is_identifier/display_name）、对象关系（对端对象 + 基数 + 方向口语化描述）；未命中对象进 `not_found` 并列可定位候选（不泄露全量本体）。**不生成 DSL、不执行查询**——查询仍由 AI 依据报告走 `execute_query`，语义模型层保持唯一编译执行权威。
- **背景**：执行版（query_object，对象级意图→服务端翻译 DSL→编译执行）已完成并实测，但复盘认定：①与 execute_query 形成双查询入口，本体侵入了工具层语义；②当前规模（个位数模型）本体定位价值有限，执行链维护成本不划算；③实测暴露执行链取数契约坑（聚合结果顶层无 data）。导航版保留其确定性价值（对象→模型路由、词汇翻译白送），砍掉执行链。
- **开关**：`CHAT_DATA_ONTOLOGY_LOCATE_ENABLED`（默认 false，登记 `server/.env.example`；替代原 `CHAT_DATA_ONTOLOGY_QUERY_ENABLED`）。关闭时 action 报错、系统提示词不注入引导块。
- **改动面**：`schema.py`（`_LocateObjectActionPayload` + 注册 + Literal）、`agent/tools/data_analyzing.py`（分发 + `_run_locate_object` + docstring）、`services/ontology/object_locator.py`（新，纯函数；`object_query.py` 删除）、`semantic_materializer.py`（`model_snapshot_file_name` 公开别名）、`agent/prompts/chat_data.py`（条件引导块改导航口径）、data-analyzing 技能 `references/analysis/query.md`（已 cp 同步运行时根）。
- **测试**：`tests/unit/test_object_locator.py`（定位纯函数）+ `test_data_analyzing_tool.py` locate_object 分发用例（原 query_object 用例删除）。
- **门禁修复（当日 review）**：初版 `build_object_locator_report`（68 分）与 `_object_relations`（18 分）超复杂度阈值（>15）且新函数不许登记 baseline，verify 复杂度门禁会红——已纯搬移拆为「去重校验 / bundle 索引（NamedTuple）/ 绑定 / 属性 / 关系条目」小函数，全库扫描 0 超标，行为无漂移（61 例相关测试全过）；顺带修正模块 docstring「不读环境变量」与实际含 `object_locator_enabled` 的矛盾表述。
- **遗留演进位**：本体 relations 与模型 relationships 的一致性校验（防定位报告指向 join 不出的路径）；二期关系可执行化（一跳导航服务端执行）优先于聚合。

### 领域更名本体：概念对齐 Palantir ontology（2026-10-20 落地）

「领域/业务领域」整体更名为「本体」（原领域一对一绑定数据源组、图语义即封闭对象图，对位 Palantir 一个 ontology；UI 树改为 本体模块根 → 本体 → 对象类型/关系类型）。代码/接口/文案全链路改名，物理表同步更名。无数据迁移（纯改名）。

存量环境迁移 DDL（dev 与生产均需执行；MySQL 8 RENAME 语法）：

```sql
-- 本体表更名 + 列/索引更名
RENAME TABLE `chat_data_ontology_domain` TO `chat_data_ontology`;
ALTER TABLE `chat_data_ontology`
  CHANGE COLUMN `domain_name` `ontology_name` varchar(64) NOT NULL COMMENT '本体API名，仅字母数字下划线，全局唯一',
  RENAME INDEX `uniq_domain_name` TO `uniq_ontology_name`,
  RENAME INDEX `idx_domain_group` TO `idx_ontology_group`;

-- 对象类型表：领域引用列更名
ALTER TABLE `chat_data_ontology_object_type`
  CHANGE COLUMN `domain_id` `ontology_id` bigint DEFAULT NULL COMMENT '本体ID（chat_data_ontology.id，组随本体继承），NULL=未挂本体',
  RENAME INDEX `idx_object_type_domain_id` TO `idx_object_type_ontology_id`;
```

API 变化：领域 CRUD 路由 `/admin/meta/ontology/domains` → `/admin/meta/ontology/ontologies`（GET/PUT/DELETE，路径参数 `{domain_name}` → `{ontology_name}`）；对象类型归属窄写 `POST .../object-types/{name}/domain` → `.../ontology`；请求模型 `OntologyDomainUpsertRequest` → `OntologyUpsertRequest`、`OntologyObjectTypeDomainRequest` → `OntologyObjectTypeOntologyRequest`；对象列表过滤参数 `domain_id` → `ontology_id`；响应键 `domains` → `ontologies`、行键 `domain_name`/`domain_id` → `ontology_name`/`ontology_id`；目录 YAML 对象行键 `domain` → `ontology`。前端 api client、类型（`OntologyDomainItem` → `OntologyItem`）、组件（`OntologyDomainForm.vue` → `OntologyForm.vue`）同步更名。

### 本体与数据源组解耦：group_id 退役（2026-10-20 下半年）

本体改为纯语义容器（对齐 Palantir「本体不绑存储」）：对象类型的取数后端=各自映射指向的语义模型（可跨数据源组）。删除本体表的 group_id 绑定与全部组锚点校验（挂本体/换本体/改组的映射组校验、数据源组删除的「组被本体引用」阻断）；一致性检查比较域改为全部数据源组的公共语义配置。

存量环境迁移 DDL（dev 与生产均需执行；注意本节 DDL 依赖上一节「领域更名本体」已先行执行）：

```sql
ALTER TABLE `chat_data_ontology`
  DROP INDEX `idx_ontology_group`,
  DROP COLUMN `group_id`;
```

API 变化：`OntologyUpsertRequest` 不再要求 `group_id`（body 仅 display_name/description/allow_overwrite）；本体列表/详情响应行无 `group_id`；一致性检查 `group_count` 语义变为「扫描的数据源组数（全部组）」。前端本体表单去掉组选择、树节点去掉组名角标、对象表单映射候选放开全部组。

### 本体 Action Type 定义表（2026-10-22）

Action Type 先落定义层（参数 + 编辑契约），暂不执行写回；对象写路径落地后接
`chat_data_ontology_action_execution` 审计与执行器。新环境直接建表；存量环境执行同一 DDL。

```sql
CREATE TABLE `chat_data_ontology_action_type` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `action_type_name` varchar(128) NOT NULL COMMENT '动作类型API名，仅字母数字下划线，全局唯一',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名',
  `description_text` varchar(1024) NOT NULL DEFAULT '' COMMENT '描述',
  `object_type_id` bigint NOT NULL COMMENT '目标对象类型ID（chat_data_ontology_object_type.id）',
  `parameter_schema_json` text COMMENT '参数定义JSON文本: [{name,type,required,description}]',
  `edit_schema_json` text COMMENT '编辑定义JSON文本: [{operation,property,value}]',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '生命周期: draft/experimental/certified',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_action_type_name` (`action_type_name`),
  KEY `idx_action_type_object` (`object_type_id`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体 Action Type 定义表';
```

### 本体 Action 执行：对象写覆盖层 + 执行审计（2026-10-22）

第一版执行不直接改写源库/语义模型：Action 执行追加对象属性覆盖值，对象读取时按最新覆盖值合并；
`CHAT_DATA_ONTOLOGY_ACTION_EXECUTE_ENABLED=true` 时生效。DDL：

```sql
CREATE TABLE `chat_data_ontology_object_edit` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_type_id` bigint NOT NULL COMMENT '对象类型ID',
  `object_key` varchar(512) NOT NULL COMMENT '对象主键值（Action 执行时的 object_key）',
  `property_name` varchar(128) NOT NULL COMMENT '被覆盖属性API名',
  `property_value_json` text COMMENT '覆盖值JSON文本（保留类型）',
  `action_execution_id` bigint NOT NULL DEFAULT '0' COMMENT '关联 Action 执行ID',
  `action_type_name` varchar(128) NOT NULL DEFAULT '' COMMENT '动作类型API名',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '操作人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '操作人名称',
  PRIMARY KEY (`id`),
  KEY `idx_object_edit_lookup` (`object_type_id`,`object_key`,`property_name`),
  KEY `idx_object_edit_execution` (`action_execution_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体对象属性写覆盖层';

CREATE TABLE `chat_data_ontology_action_execution` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `action_type_name` varchar(128) NOT NULL COMMENT '动作类型API名',
  `object_type_id` bigint NOT NULL COMMENT '目标对象类型ID',
  `object_key` varchar(512) NOT NULL COMMENT '对象主键值',
  `parameters_json` text COMMENT '执行参数JSON文本',
  `edits_json` text COMMENT '解析后的编辑JSON文本',
  `execution_status` varchar(16) NOT NULL DEFAULT 'succeeded' COMMENT '执行状态',
  `error_message` varchar(1000) NOT NULL DEFAULT '' COMMENT '错误信息',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '操作人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '操作人名称',
  PRIMARY KEY (`id`),
  KEY `idx_action_execution_type` (`action_type_name`,`create_time`),
  KEY `idx_action_execution_object` (`object_type_id`,`object_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体 Action 执行审计表';
```

### Logical Dataset 基础表（2026-10-23）

语义层与本体层共用的逻辑数据资产层。Dataset 只描述逻辑结构/主键/版本/member 绑定，
不承载指标口径，也不承载对象定义；新环境 `server/utils/schema.py` 已同步，存量环境执行以下 DDL。

```sql
CREATE TABLE `chat_data_logical_dataset` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `dataset_name` varchar(128) NOT NULL COMMENT 'Dataset API名',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名',
  `description_text` varchar(1024) NOT NULL DEFAULT '' COMMENT '描述',
  `primary_key_json` text NOT NULL COMMENT '主键列JSON',
  `schema_json` text NOT NULL COMMENT '逻辑列JSON',
  `current_version_id` bigint(20) DEFAULT NULL COMMENT '当前版本行ID',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '生命周期状态',
  `delete_flag` bigint(20) NOT NULL DEFAULT '0' COMMENT '删除标识:0=活跃,非0=已删除(值=行ID)',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_logical_dataset_name_del` (`dataset_name`, `delete_flag`),
  KEY `idx_logical_dataset_status` (`lifecycle_status`, `delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData逻辑数据集';

CREATE TABLE `chat_data_dataset_branch` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `dataset_id` bigint(20) NOT NULL COMMENT 'Dataset ID',
  `branch_name` varchar(128) NOT NULL COMMENT '分支名',
  `base_version_id` bigint(20) DEFAULT NULL COMMENT '基线版本行ID',
  `is_default` tinyint NOT NULL DEFAULT '0' COMMENT '是否默认分支',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'active' COMMENT '状态',
  `delete_flag` bigint(20) NOT NULL DEFAULT '0' COMMENT '删除标识',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_dataset_branch_del` (`dataset_id`, `branch_name`, `delete_flag`),
  KEY `idx_dataset_branch_dataset` (`dataset_id`, `delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData数据集分支';

CREATE TABLE `chat_data_dataset_transaction` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `transaction_id` varchar(64) NOT NULL COMMENT '事务ID',
  `dataset_id` bigint(20) NOT NULL COMMENT 'Dataset ID',
  `branch_id` bigint(20) NOT NULL COMMENT '分支ID',
  `transaction_status` varchar(16) NOT NULL DEFAULT 'open' COMMENT '事务状态',
  `run_id` varchar(128) NOT NULL DEFAULT '' COMMENT '产生方运行ID',
  `error_message` varchar(1000) NOT NULL DEFAULT '' COMMENT '错误信息',
  `started_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开始时间',
  `committed_at` datetime DEFAULT NULL COMMENT '提交时间',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_dataset_transaction_id` (`transaction_id`),
  KEY `idx_dataset_transaction_dataset` (`dataset_id`, `branch_id`, `transaction_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData数据集事务';

CREATE TABLE `chat_data_dataset_version` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `version_id` varchar(64) NOT NULL COMMENT '版本ID',
  `dataset_id` bigint(20) NOT NULL COMMENT 'Dataset ID',
  `branch_id` bigint(20) NOT NULL COMMENT '分支ID',
  `version_no` bigint(20) NOT NULL COMMENT '分支内版本号',
  `transaction_id` varchar(64) NOT NULL DEFAULT '' COMMENT '来源事务ID',
  `schema_json` text NOT NULL COMMENT '版本schema JSON',
  `row_count` bigint(20) NOT NULL DEFAULT '0' COMMENT '行数',
  `version_status` varchar(16) NOT NULL DEFAULT 'committed' COMMENT '版本状态',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_dataset_version_id` (`version_id`),
  UNIQUE KEY `uniq_dataset_version_no` (`dataset_id`, `branch_id`, `version_no`),
  KEY `idx_dataset_version_dataset` (`dataset_id`, `branch_id`, `version_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData数据集不可变版本';

CREATE TABLE `chat_data_dataset_binding` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `dataset_id` bigint(20) NOT NULL COMMENT 'Dataset ID',
  `datasource_group_id` varchar(128) NOT NULL COMMENT '数据源组ID',
  `member_datasource_id` varchar(128) NOT NULL COMMENT '成员数据源ID',
  `physical_table` varchar(256) NOT NULL COMMENT '物理表名',
  `binding_status` varchar(16) NOT NULL DEFAULT 'active' COMMENT '绑定状态',
  `delete_flag` bigint(20) NOT NULL DEFAULT '0' COMMENT '删除标识',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_dataset_binding_member` (`dataset_id`, `member_datasource_id`, `delete_flag`),
  KEY `idx_dataset_binding_dataset` (`dataset_id`, `delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData数据集成员绑定';

CREATE TABLE `chat_data_dataset_lineage_edge` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `from_version_id` varchar(64) NOT NULL DEFAULT '' COMMENT '来源版本ID',
  `to_version_id` varchar(64) NOT NULL DEFAULT '' COMMENT '目标版本ID',
  `operation` varchar(64) NOT NULL DEFAULT '' COMMENT '操作类型',
  `run_id` varchar(128) NOT NULL DEFAULT '' COMMENT '运行ID',
  `edge_metadata_json` text NOT NULL COMMENT '边元数据JSON',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  PRIMARY KEY (`id`),
  KEY `idx_dataset_lineage_from` (`from_version_id`),
  KEY `idx_dataset_lineage_to` (`to_version_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData数据集版本血缘';
```

### 对象类型数据源绑定（2026-10-23）

```sql
CREATE TABLE `chat_data_ontology_source_binding` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_type_id` bigint(20) NOT NULL COMMENT '对象类型ID',
  `dataset_id` bigint(20) NOT NULL COMMENT 'Logical Dataset ID',
  `column_mappings_json` text NOT NULL COMMENT '对象属性到Dataset逻辑列映射JSON',
  `delete_flag` bigint(20) NOT NULL DEFAULT '0' COMMENT '删除标识',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_source_binding_object` (`object_type_id`, `delete_flag`),
  KEY `idx_source_binding_dataset` (`dataset_id`, `delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData对象类型数据源绑定';
```

### 对象类型语义映射表下线（2026-10-23）

对象类型取数已统一迁移到 Logical Dataset source binding，`chat_data_ontology_mapping`
不再读写。存量环境执行：

```sql
DROP TABLE IF EXISTS `chat_data_ontology_mapping`;
```

### Dataset ↔ 语义模型关联（指标层与本体层显式关联，2026-10-23）

背景：对象类型只绑定 Logical Dataset，指标口径仍只在语义层；为让「对象类型可执行哪些指标」
成为可查询、可校验的事实（对齐 Ossie mapping document 的简化形态），新增 Dataset 级关联表：

- 关联只存**引用与列对照**，不存指标表达式（口径唯一权威仍在 `semantic_config`）；
- `column_mappings_json`：Dataset 逻辑列 → 语义维度名；与对象类型 source binding 的
  「属性 → Dataset 逻辑列」组合，派生得到「对象属性 → 指标维度」；
- `metric_scope_json`：可选指标白名单（空 = 该模型全部指标）；
- 运行时 MVP 约束：指标与对象 Dataset 必须同表，跨表/跨数据集指标先标记不可执行；
- Catalog：`_ontology_catalog.yaml` 的 `object_types[].metrics` 输出只读指标索引
  （名称/kind/描述/是否可在对象作用域执行），Agent 可用 `list_object_metrics` 查询、
  用 `aggregate_objects.metric_names` 执行命名指标。

```sql
CREATE TABLE `chat_data_dataset_semantic_link` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `dataset_id` bigint(20) NOT NULL COMMENT 'Logical Dataset ID',
  `semantic_config_id` varchar(128) NOT NULL COMMENT '语义模型ID（与 datasource_group_id 同域）',
  `datasource_group_id` varchar(128) NOT NULL COMMENT '业务域=数据源组ID',
  `table_name` varchar(256) NOT NULL COMMENT '语义模型内对应表名（须等于该组 Dataset 物理表）',
  `column_mappings_json` text NOT NULL COMMENT 'Dataset逻辑列→语义维度名映射JSON',
  `metric_scope_json` text NOT NULL COMMENT '允许在对象作用域执行的指标名白名单JSON，空=全部',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '状态:draft/active',
  `delete_flag` bigint(20) NOT NULL DEFAULT '0' COMMENT '删除标识:0=活跃,非0=已删除(值=行ID)',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_dataset_semantic_link` (`dataset_id`,`semantic_config_id`,`delete_flag`),
  KEY `idx_dataset_semantic_link_dataset` (`dataset_id`,`delete_flag`),
  KEY `idx_dataset_semantic_link_group` (`datasource_group_id`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Dataset与语义模型关联（指标口径权威在语义层）';
```

管理接口：

```text
GET    /admin/meta/datasets/{dataset_name}/semantic-links?datasource_group_id=
PUT    /admin/meta/datasets/{dataset_name}/semantic-links/{semantic_config_id}
DELETE /admin/meta/datasets/{dataset_name}/semantic-links/{semantic_config_id}
GET    /admin/meta/ontology/object-types/{object_type_name}/metrics
```

新环境 `server/utils/schema.py` 的 `MYSQL_TABLES` 已包含该表；存量环境需由有 DDL 权限的
账号执行上面的建表 SQL（应用账号 `fxcjg_rw` 无 CREATE 权限）。

### Logical Dataset 分层字段（2026-10-23）

按 Palantir Pipeline stages 给 Dataset 增加分层与来源标识：

- `dataset_layer`：`source`（数据源直注册）/ `cleaned` / `enriched`（Pipeline 产出）
- `dataset_origin`：`datasource`（数据源注册）/ `pipeline`（Pipeline build 产出）

存量数据默认 `source` / `datasource`（列默认值 + `ALTER` 兜底）。存量环境执行：

```sql
ALTER TABLE `chat_data_logical_dataset`
  ADD COLUMN `dataset_layer` varchar(16) NOT NULL DEFAULT 'source' COMMENT 'Pipeline分层:source/cleaned/enriched' AFTER `lifecycle_status`,
  ADD COLUMN `dataset_origin` varchar(16) NOT NULL DEFAULT 'datasource' COMMENT '来源:datasource/pipeline' AFTER `dataset_layer`,
  ADD KEY `idx_logical_dataset_layer` (`dataset_layer`, `dataset_origin`, `delete_flag`);
```

新环境 `server/utils/schema.py` 的 `chat_data_logical_dataset` DDL 已同步。

### Dataset Pipeline 表（2026-10-23）

Pipeline 定义采用「稳定 ID + 只追加版本」：

- `chat_data_pipeline_definition`：稳定 `pipeline_id`、当前定义版本号 `current_version_no`；
- `chat_data_pipeline_node` / `chat_data_pipeline_edge`：按 `(pipeline_id, pipeline_version)`
  只追加，不覆盖历史版本；
- `chat_data_pipeline_run`：手动 build 运行记录，`run_id` 稳定标识，关联输出 Dataset。

Pipeline build 不另造 Dataset 版本：输出 Dataset 的 transaction / 不可变 version / lineage
仍写 `chat_data_dataset_transaction` / `chat_data_dataset_version` /
`chat_data_dataset_lineage_edge`（lineage 的 `run_id` 指向 pipeline run）。

```sql
CREATE TABLE `chat_data_pipeline_definition` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `pipeline_id` varchar(64) NOT NULL COMMENT '稳定Pipeline ID',
  `pipeline_name` varchar(128) NOT NULL COMMENT 'Pipeline API名',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名',
  `description_text` varchar(1024) NOT NULL DEFAULT '' COMMENT '描述',
  `target_layer` varchar(16) NOT NULL DEFAULT 'cleaned' COMMENT '目标层:cleaned/enriched',
  `lifecycle_status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '生命周期状态',
  `current_version_no` bigint(20) NOT NULL DEFAULT '0' COMMENT '当前定义版本号',
  `delete_flag` bigint(20) NOT NULL DEFAULT '0' COMMENT '删除标识',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_pipeline_id` (`pipeline_id`),
  UNIQUE KEY `uniq_pipeline_name_del` (`pipeline_name`, `delete_flag`),
  KEY `idx_pipeline_definition_status` (`lifecycle_status`, `delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData Pipeline定义';

CREATE TABLE `chat_data_pipeline_node` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `pipeline_id` varchar(64) NOT NULL COMMENT '稳定Pipeline ID',
  `pipeline_version` bigint(20) NOT NULL COMMENT '定义版本号',
  `node_id` varchar(64) NOT NULL COMMENT '节点稳定ID',
  `node_type` varchar(16) NOT NULL COMMENT '节点类型:source/transform/output',
  `node_name` varchar(128) NOT NULL DEFAULT '' COMMENT '节点展示名',
  `config_json` text NOT NULL COMMENT '节点配置JSON',
  `ordinal` int(11) NOT NULL DEFAULT '0' COMMENT '节点顺序',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_pipeline_node_version` (`pipeline_id`, `pipeline_version`, `node_id`),
  KEY `idx_pipeline_node_pipeline` (`pipeline_id`, `pipeline_version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData Pipeline节点';

CREATE TABLE `chat_data_pipeline_edge` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `pipeline_id` varchar(64) NOT NULL COMMENT '稳定Pipeline ID',
  `pipeline_version` bigint(20) NOT NULL COMMENT '定义版本号',
  `edge_id` varchar(64) NOT NULL COMMENT '边稳定ID',
  `from_node_id` varchar(64) NOT NULL COMMENT '上游节点ID',
  `to_node_id` varchar(64) NOT NULL COMMENT '下游节点ID',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_pipeline_edge_version` (`pipeline_id`, `pipeline_version`, `edge_id`),
  KEY `idx_pipeline_edge_pipeline` (`pipeline_id`, `pipeline_version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData Pipeline边';

CREATE TABLE `chat_data_pipeline_run` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `run_id` varchar(64) NOT NULL COMMENT '运行ID',
  `pipeline_id` varchar(64) NOT NULL COMMENT '稳定Pipeline ID',
  `pipeline_version` bigint(20) NOT NULL COMMENT '运行使用的定义版本号',
  `run_status` varchar(16) NOT NULL DEFAULT 'running' COMMENT '运行状态:running/succeeded/failed',
  `trigger_type` varchar(16) NOT NULL DEFAULT 'manual' COMMENT '触发方式',
  `output_dataset_id` bigint(20) DEFAULT NULL COMMENT '输出Dataset ID',
  `output_summary_json` text NOT NULL COMMENT '输出摘要JSON',
  `error_message` varchar(1000) NOT NULL DEFAULT '' COMMENT '错误信息',
  `started_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开始时间',
  `finished_at` datetime DEFAULT NULL COMMENT '结束时间',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_pipeline_run_id` (`run_id`),
  KEY `idx_pipeline_run_pipeline` (`pipeline_id`, `pipeline_version`),
  KEY `idx_pipeline_run_status` (`run_status`, `create_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ChatData Pipeline运行记录';
```

### 对象类型 certified 降级确认（2026-10-23）

对齐 Palantir「active 资源受保护、降级走显式路径」：此前 certified 对象类型可一键降级到
experimental/draft，主键/标题键/属性冻结保护形同虚设（降级后即可改主键、删属性）。现按
既有破坏性变更口径收口：

- `POST /admin/meta/ontology/object-types/{name}/status` 请求体新增 `confirm_destructive`
  （默认 false）；certified→experimental/draft 必传 true，否则 40074；
- certified→deprecated 不变（已有必填 `deprecation_reason` 兜底）；experimental→draft 等
  不涉及冻结保护的迁移、以及幂等迁移（目标=当前）不受影响；
- 前端 `OntologyStatusDialog` 对 certified 降级增加二次确认弹窗并自动携带
  `confirm_destructive=true`。
