# 下一版本变更

以下内容均由发布流程/DBA 在上线前手工执行，应用启动不会自动建表、也不会自动安装依赖。

建议顺序：**DDL → 配置同步 → 依赖升级 → 重启服务**。依赖升级与重启必须在同一发版窗口内覆盖所有后端节点（含 Active 机房全部 worker 节点与 Standby 机房），避免同一会话的请求落到新旧版本混装的进程上。

## 数据库变更

### 子智能体多轮会话快照

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

### 子智能体生命周期事件 outbox

统一承载审批、取消、终态以及后续用户直聊等需要同步给主智能体的通知；
状态变更与事件同事务写入，由调度器统一投递、重试和唤醒。上线前执行：

```sql
CREATE TABLE `chat_data_subagent_event` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `event_id` varchar(96) NOT NULL COMMENT '事件唯一ID（含任务与事件类型的确定性去重键）',
  `parent_run_id` varchar(64) NOT NULL DEFAULT '' COMMENT '派发该子智能体的父Run ID',
  `task_id` varchar(64) NOT NULL COMMENT '子智能体任务ID',
  `session_id` varchar(64) NOT NULL DEFAULT '' COMMENT '子智能体会话ID',
  `conversation_id` varchar(255) NOT NULL COMMENT '父会话ID',
  `user_id` varchar(255) NOT NULL COMMENT '业务归属用户ID',
  `event_type` varchar(32) NOT NULL COMMENT '事件类型: waiting_approval/terminal/paused/resumed/user_message',
  `task_status` varchar(32) NOT NULL DEFAULT '' COMMENT '事件发生时的任务状态',
  `wake_policy` varchar(16) NOT NULL DEFAULT 'wake' COMMENT '投递策略: wake-可唤醒/attach-仅挂活跃Run/silent-仅审计',
  `event_content` mediumtext NOT NULL COMMENT '通知正文（内部隐藏消息）',
  `record_meta` text COMMENT '事件附加元数据JSON',
  `delivery_status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT '投递状态: pending/delivered/failed/cancelled',
  `delivery_attempts` int NOT NULL DEFAULT '0' COMMENT '投递尝试次数',
  `last_error` varchar(500) NOT NULL DEFAULT '' COMMENT '最近一次投递错误',
  `delivered_time` datetime DEFAULT NULL COMMENT '投递成功时间',
  `delete_flag` tinyint NOT NULL DEFAULT '0' COMMENT '删除标识: 0-未删除, 1-已删除',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `create_by` varchar(255) NOT NULL DEFAULT '' COMMENT '创建人',
  `create_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '创建人名称',
  `update_by` varchar(255) NOT NULL DEFAULT '' COMMENT '更新人',
  `update_by_name` varchar(100) NOT NULL DEFAULT '' COMMENT '更新人名称',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_subagent_event_id` (`event_id`),
  KEY `idx_subagent_event_conv` (`conversation_id`, `user_id`, `delivery_status`),
  KEY `idx_subagent_event_task` (`task_id`, `event_type`, `delivery_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='子智能体生命周期事件outbox';
```

## 运行期配置变更

以 `server/.env.example` 为对照，把以下变更同步到生产 `server/conf/.env-<环境>`（示例文件本身不参与运行）：

- 子智能体收敛为 explore / general：`CHAT_DATA_DEFAULT_PLAN_SUBAGENT_ENABLED` 已无对应实现，生产若残留可删除；新增 `CHAT_DATA_DEFAULT_GENERAL_SUBAGENT_ENABLED=false`（默认关闭，按需开启）。
- 新增 4 个后台任务控制项：`CHAT_DATA_SUBAGENT_TASK_TIMEOUT_SECONDS=1800`、`CHAT_DATA_SUBAGENT_TASK_LEASE_SECONDS=30`、`CHAT_DATA_SUBAGENT_TASK_MAX_ACTIVE=20`、`CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION=5`。最后一项是**单个主对话内子智能体总数上限**，长会话容易撞到，若线上同步派发较多需按实际评估后调大。
- 旧名 `CHAT_DATA_SUBAGENT_MAX_SESSIONS_PER_CONVERSATION` 已统一为 `CHAT_DATA_SUBAGENT_MAX_PER_CONVERSATION`，旧名不再读取；生产配置若残留请直接删除。子智能体预算告警软约束 `CHAT_DATA_SUBAGENT_BUDGET_ALERT_TRIGGER_RATIO` 已无实现，残留配置同样可删除。

## 依赖升级（LangChain / LangGraph 全家桶）

本版本升级了 LangChain/LangGraph 全家桶并移除两个不再使用的包，版本唯一真值为 `server/requirements.txt`。相对 `master` 的变化：

| 包 | 原版本 | 新版本 |
| --- | --- | --- |
| `langchain` | 1.4.6 | 1.5.0 |
| `langchain-core` | 1.6.12 | 1.8.0 |
| `langgraph` | 1.2.16 | 1.2.22 |
| `langchain-openai` | 1.7.2 | 1.8.0 |
| `langgraph-checkpoint` | 4.3.0 | 4.3.2 |
| `langgraph-checkpoint-sqlite` | 3.0.3 | 3.1.3 |
| `tiktoken` | 0.12.0 | 0.14.0 |
| `langchain-experimental` | 0.4.1 | 移除 |
| `langgraph-checkpoint-postgres` | 3.0.5 | 移除 |

`openai==3.51.0`、`httpx2==2.16.0`、`langgraph-checkpoint-mysql==3.0.0` 已在 `master` 钉版，本版本不变；开发环境中若仍为旧版，本次 `pip install` 会一并补齐。

在每个后端节点上执行（路径按 `server/dataagent.service` 中的生产部署示例，实际以现场 venv 为准）：

```bash
cd /tools/apps/dataagent/server
/tools/apps/dataagent/venv/bin/python -m pip install -r requirements.txt
```

> 必须在 `server/` 目录下执行：`requirements.txt` 中的 `wheels/*.whl` 是相对路径。现场部署目录不同时，同样要保证当前工作目录下能找到 `wheels/`。

`pip install -r` 不会卸载已从 requirements 中移除的包。确认无其他用途后可选清理：

```bash
/tools/apps/dataagent/venv/bin/python -m pip uninstall -y langchain-experimental langgraph-checkpoint-postgres
```

校验已安装版本（用 distribution 名，避免依赖模块名差异）：

```bash
/tools/apps/dataagent/venv/bin/python - <<'PY'
from importlib.metadata import version
for pkg in (
    "langchain", "langchain-core", "langgraph", "langchain-openai",
    "langgraph-checkpoint", "langgraph-checkpoint-mysql",
    "langgraph-checkpoint-sqlite", "openai", "httpx2", "tiktoken",
):
    print(pkg, version(pkg))
PY
```

**装完依赖必须完整重启服务，不能只热更代码**：只 `pip install` 而不重启会出现新旧包在 `sys.modules` 中混装，典型报错为 `ImportError: cannot import name 'materialize_input_tools'`。

```bash
sudo systemctl restart dataagent
sudo systemctl status dataagent
```

开发环境等价操作：在所用虚拟环境（如 conda `fxcjg`）中执行 `python -m pip install -r server/requirements.txt`，然后重启 `python server/main.py` / 5001 端口进程。

两点提示：

- pip 若输出 `fish-speech 0.1.0 requires pydantic==2.9.2, but you have pydantic 2.13.4` 之类的一致性警告，是环境中其他项目的旧账，与本发版无关；**不要为消除它降级 pydantic**，LangChain 1.x 需要较新的 pydantic 2.x。
- 本次未替换 `wheels/apache_ossie-*.whl`。若后续版本重新构建该 wheel，按 `requirements.txt` 中的注释用 `--force-reinstall --no-deps` 显式重装，普通 `pip install -r` 会因版本号未变而跳过。
