# HTTP 查询接口说明

## 1. 说明

用于对接异步 SQL 查询。

查询请求固定传 3 个字段：
- `sql`
- `namespace`
- `limit`

其中：
- `namespace` 为执行对象名称
- `sql` 为最终执行 SQL
- `limit` 为结果行数上限

## 2. 提交接口

- Method：`POST`
- URL 示例：`https://query.example.com/api/v1/query/submit`
- Content-Type：`application/json`

### 请求体

```json
{
  "sql": "select * from departments limit 100",
  "namespace": "hive-localhost",
  "limit": 100
}
```

### 响应体

提交后固定返回：

```json
{
  "task_id": "task_001",
  "status": "pending"
}
```

## 3. 状态接口

- Method：`GET`
- URL 示例：`https://query.example.com/api/v1/query/status/{task_id}`
- 路径中固定传入 `task_id`

示例：

```text
GET /query/status/task_001
```

### 运行中响应

```json
{
  "task_id": "task_001",
  "status": "running"
}
```

### 成功响应

```json
{
  "task_id": "task_001",
  "status": "succeeded",
  "columns": ["col1", "col2"],
  "data": [
    { "col1": "a", "col2": 1 }
  ],
  "total_count": 1,
  "execution_time_ms": 123
}
```

字段说明：
- `columns`：结果列名
- `data`：结果数据
- `total_count`：结果行数
- `execution_time_ms`：执行耗时，毫秒

### 失败响应

```json
{
  "task_id": "task_001",
  "status": "failed",
  "error_message": "SQL syntax error"
}
```

## 4. 状态值

固定使用以下状态值：
- `pending`
- `running`
- `succeeded`
- `failed`

## 5. 数据库变更 SQL

按实际环境执行以下 SQL：

```sql
ALTER TABLE `chat_data_query_task`
  CHANGE COLUMN `status` `task_status` varchar(32) NOT NULL DEFAULT 'pending' COMMENT '任务状态: pending | running | succeeded | failed | cancelled',
  DROP INDEX `idx_task_type_status`,
  DROP INDEX `idx_datasource_id_status`,
  ADD KEY `idx_task_type_task_status` (`task_type`, `task_status`),
  ADD KEY `idx_datasource_id_task_status` (`datasource_id`, `task_status`);

ALTER TABLE `datasource_table`
  CHANGE COLUMN `table_name` `table_names` varchar(128) NOT NULL COMMENT '表名',
  DROP INDEX `uniq_group_id_table_name`,
  ADD UNIQUE KEY `uniq_group_id_table_names` (`group_id`, `table_names`);

ALTER TABLE `chat_data_query_task`
  ADD COLUMN `remote_task_payload` text COMMENT '远端HTTP任务映射JSON' AFTER `result_payload`;
```
