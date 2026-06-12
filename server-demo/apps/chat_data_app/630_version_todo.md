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
  DROP COLUMN `card_color`,
  DROP INDEX `idx_datasource_frequency`;
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
