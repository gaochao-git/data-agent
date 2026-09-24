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

ALTER TABLE datasource_table ADD COLUMN indexes_json TEXT NULL COMMENT '表级物理索引和手工索引 JSON';
ALTER TABLE user_threads ADD COLUMN datasource_id varchar(255) NOT NULL DEFAULT '' COMMENT '会话绑定数据源组ID' after agent_id;

-- 唯一索引改造（与 utils/schema.py 模板同名同列序）：生产已于 2026-09-19 执行并核对
-- （SHOW CREATE TABLE 已含 uniq_group_id_config_name_delete_flag，无旧两列索引需 DROP）；
-- 其余环境执行 ALTER 前先跑查重，有结果则先处理重复行。
SELECT group_id, semantic_config_name, COUNT(*) from semantic_config group by group_id,semantic_config_name,delete_flag HAVING COUNT(*)>1
-- 查重有结果时清理：每组（group_id, semantic_config_name, delete_flag）保留 MIN(id) 最早一行，删其余；
-- 预览将删的行 = 同 WHERE 换成 SELECT *。与 ALTER 连续执行，避免两步之间新写入再造重复。
DELETE FROM semantic_config
WHERE id NOT IN (
    SELECT min_id FROM (
        SELECT MIN(id) AS min_id
        FROM semantic_config
        GROUP BY group_id, semantic_config_name, delete_flag
    ) t
);
-- 清理后复查应返回空，再建索引
SELECT group_id, semantic_config_name, COUNT(*) from semantic_config group by group_id,semantic_config_name,delete_flag HAVING COUNT(*)>1;
ALTER TABLE semantic_config   ADD UNIQUE KEY uniq_group_id_config_name_delete_flag (group_id, semantic_config_name,delete_flag);

-- CHANGE 一步完成改名+改注释（RENAME COLUMN 不会更新列注释）
ALTER TABLE semantic_config CHANGE COLUMN ontology_rules business_notes text COMMENT '业务说明';

-- 技能表清理废弃字段：skill_scope 从未使用；root_dir/version_dir 已由推导规则替代
ALTER TABLE chat_data_skill DROP COLUMN skill_scope;
ALTER TABLE chat_data_skill DROP COLUMN skill_root_dir;
ALTER TABLE chat_data_skill_version DROP COLUMN skill_version_dir;
```

## 依赖变更

上线前执行：

```bash
python -m pip install opensandbox==0.1.15
python -m pip install redis==7.0.1
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
