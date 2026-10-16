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

-- 本体对象表删除「复数展示名 / 上下文可见性」两列（实验期裁剪，Palantir 对齐字段确认无用；仅 dev 已建表需执行）
ALTER TABLE chat_data_ontology_object DROP COLUMN plural_display_name;
ALTER TABLE chat_data_ontology_object DROP COLUMN visibility;

-- 本体重建四张表：对象/属性/关系/对象映射（属性一等实体；口径不落本体，唯一权威在语义层；
-- 绑定独立成映射表对齐 ossie 概念层/映射层分离，主键=标识属性组合、标题键=标题属性，均由属性表表达）
CREATE TABLE `chat_data_ontology_object` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_name` varchar(128) NOT NULL COMMENT '对象API名，仅字母数字下划线，certified 后冻结',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名',
  `description` varchar(1024) NOT NULL DEFAULT '' COMMENT '对象描述',
  `business_notes` text COMMENT '业务说明（建模背景、口径边界等，不入 agent 上下文）',
  `status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '生命周期: draft-草稿, experimental-实验, certified-认证, deprecated-废弃(不可逆)',
  `domain` varchar(128) NOT NULL DEFAULT '' COMMENT '业务域扁平标签（语义归属，与数据源组采集范围解耦）',
  `deprecation_reason` varchar(512) NOT NULL DEFAULT '' COMMENT '废弃原因，status=deprecated 时有效',
  `deprecated_until` datetime DEFAULT NULL COMMENT '废弃删除期限，status=deprecated 时有效',
  `successor_object_name` varchar(128) NOT NULL DEFAULT '' COMMENT '替代对象API名，status=deprecated 时有效',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_object_name` (`object_name`),
  KEY `idx_object_status` (`status`,`delete_flag`),
  KEY `idx_object_domain` (`domain`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体对象表';

CREATE TABLE `chat_data_ontology_property` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_id` bigint NOT NULL COMMENT '所属本体对象ID（chat_data_ontology_object.id，逻辑关联无外键）',
  `property_name` varchar(128) NOT NULL COMMENT '属性API名，仅字母数字下划线，对象内唯一，certified 后禁删',
  `display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '展示名',
  `description` varchar(1024) NOT NULL DEFAULT '' COMMENT '属性描述',
  `value_type` varchar(32) NOT NULL DEFAULT 'String' COMMENT '值类型: String/Integer/Decimal/Boolean/Date/DateTime 或 enum(引用全局枚举)',
  `enum_ref` varchar(260) NOT NULL DEFAULT '' COMMENT '全局枚举引用，value_type=enum 时非空: "enum_name" 或 "business_domain:enum_name"',
  `render_hints_json` text COMMENT '渲染提示JSON文本: {"searchable":true,"sortable":true,"low_cardinality":false}',
  `is_identifier` tinyint NOT NULL DEFAULT '0' COMMENT '标识属性: 1-参与对象主键组合（业务主键，禁自增行号）',
  `is_title` tinyint NOT NULL DEFAULT '0' COMMENT '标题属性: 1-对象标题键（每对象至多一个）',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_object_property` (`object_id`,`property_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体属性表';

CREATE TABLE `chat_data_ontology_relation` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `relation_name` varchar(128) NOT NULL COMMENT '关系API名，仅字母数字下划线，全局唯一',
  `from_object_id` bigint NOT NULL COMMENT '起始对象ID（chat_data_ontology_object.id）',
  `to_object_id` bigint NOT NULL COMMENT '目标对象ID（chat_data_ontology_object.id）',
  `from_columns_json` text COMMENT '起始侧关联列JSON文本: ["属性名"], 与目标侧等长顺序一一对应',
  `to_columns_json` text COMMENT '目标侧关联列JSON文本: ["属性名"], 必须引用对方标识属性',
  `multiplicity` varchar(16) NOT NULL DEFAULT 'many_to_one' COMMENT '基数: many_to_one, one_to_one（多对多走 junction 对象表达）',
  `from_display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '正向方向展示名',
  `to_display_name` varchar(128) NOT NULL DEFAULT '' COMMENT '反向方向展示名',
  `from_verbalize` varchar(512) NOT NULL DEFAULT '' COMMENT '正向自然语言模式串，如 "{from} 归属于 {to}"',
  `to_verbalize` varchar(512) NOT NULL DEFAULT '' COMMENT '反向自然语言模式串',
  `description` varchar(1024) NOT NULL DEFAULT '' COMMENT '关系描述',
  `business_notes` text COMMENT '业务说明',
  `status` varchar(16) NOT NULL DEFAULT 'draft' COMMENT '生命周期随对象级联: 对象降级自动同步，升级需手动确认',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人ID',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人ID',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '修改人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_relation_name` (`relation_name`),
  KEY `idx_relation_from` (`from_object_id`,`delete_flag`),
  KEY `idx_relation_to` (`to_object_id`,`delete_flag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体关系表';

-- 对象映射表：语义模型映射独立成表（对齐 ossie 本体文档/映射文档分离）。
-- 映射随对象 payload 提交、随对象 upsert 同事务全量重写（软删+重插，无独立生命周期）；
-- delete_flag 语义特殊：0=活跃，软删时置为行 id——软删行不占唯一索引槽位，同源可重绑；
-- 「一源一对象」由 uniq_mapping_semantic_source(含 delete_flag) 对活跃行直接约束
-- （DB 兜底，service 先查给友好报错）
CREATE TABLE `chat_data_ontology_object_mapping` (
  `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `object_id` bigint NOT NULL COMMENT '所属本体对象ID（chat_data_ontology_object.id，逻辑关联无外键）',
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
  UNIQUE KEY `uniq_mapping_object_source` (`object_id`,`semantic_config_id`,`delete_flag`),
  UNIQUE KEY `uniq_mapping_semantic_source` (`semantic_config_id`,`delete_flag`),
  KEY `idx_mapping_object` (`object_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体对象映射表';
```

## 功能变更

### 本体关系状态手动升级入口（2026-10-16 落地）

现状（修复前）：关系状态只在创建时按两侧对象状态较低者计算，级联只降不升，编辑时状态原样保留——「升级需手动」没有操作入口；且删除重建无效（软删行复活复用旧状态）。

已实现：`POST /admin/meta/ontology/relations/{relation_name}/status` 迁移端点（目标状态只允许 draft/experimental/certified，deprecated 仅随对象级联；上限 = 两侧对象状态的较低者，关系不得高于任一端；任一端废弃时拒绝手动迁移）。前端关系面板加「状态」操作弹窗。涉及：schema（OntologyRelationStatusRequest）、ontology_service.set_relation_status_async、ontology_admin 路由、前端 RelationPanel、openapi 快照、单测。

### 本体映射层级重构：组 → 模型 → 维度映射（2026-10-16 落地，含 bindings→mapping 重命名）

绑定语义从「config_id + table_alias + 属性→物理列」改为「config_id + 属性→模型维度名」：本体层只引用模型维度（Ossie 映射文档同构），物理列解析归语义模型；实体锚点由映射维度所属表派生（同一映射全部维度必须同表，service 校验）；唯一键收紧为 (object_id, config_id) 与 (config_id)。API/payload/目录 YAML 键同步 bindings→mappings，table_alias 全链路删除。

上线前对已建环境的 DDL（新环境直接按上方建表语句）：

```sql
ALTER TABLE `chat_data_ontology_object_mapping`
  DROP INDEX `uniq_mapping_object_source`,
  DROP INDEX `uniq_mapping_semantic_source`,
  DROP COLUMN `table_alias`,
  ADD UNIQUE KEY `uniq_mapping_object_source` (`object_id`,`semantic_config_id`,`delete_flag`),
  ADD UNIQUE KEY `uniq_mapping_semantic_source` (`semantic_config_id`,`delete_flag`);
```

存量数据迁移：column_mappings_json 右值由物理列名改写为对应模型维度名（按语义模型 dimensions 的 name/column 对照，dev 库 2026-10-16 已执行；生产若已有存量映射需按同口径生成 UPDATE）。

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
