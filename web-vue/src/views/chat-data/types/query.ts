export interface MemberQueryResult {
  execution_id?: string
  member_datasource_id?: string
  member_name?: string
  executor_type?: string
  status?: string | null
  sql?: string
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  execution_time_ms?: number
  started_time?: string | null
  finished_time?: string | null
  remote_task_id?: string | null
  db_connection_id?: string | null
  error?: string | null
  cache_hit?: boolean
  cache_age_seconds?: number | null
  cache_expires_in_seconds?: number | null
}

/**
 * 聚合级查询结果。
 *
 * 聚合约定（消费方必读，与后端 build_query_result_payload 对齐），状态按序判定：
 * - 任一成员仍在 `pending`/`running`：顶层 `status=running`，
 *   `error` 为固定提示“部分执行对象仍在查询中”。
 * - 无运行中成员且有任一成员成功：顶层 `status=succeeded` 且 `error` 为
 *   `null`；部分成员失败的信息只在对应 `results[]` 成员的 `status`/`error`
 *   上。判断整体成败必须遍历 `results[]`，不能只看顶层字段。
 * - 全部成员取消：顶层 `status=cancelled`；其余情况为 `failed`，这两种情形
 *   顶层 `error` 聚合各成员错误。
 */
export interface QueryResult {
  status?: string | null
  sql?: string
  total_count?: number
  execution_time_ms?: number
  error?: string | null
  results?: MemberQueryResult[]
}

/**
 * 智能问数工具结果在成员级之上扩展的字段，由 agent 语义查询工具注入
 * （见后端 agent/tools/semantic_query.py），SQL 任务 API（query-tasks）不返回。
 * 工具结果 JSON 由组件宽松解析（失败成员可能缺列数据），故
 * MemberQueryResult 的必填字段在此统一放宽为可选。
 */
export type ToolQueryResult = Omit<MemberQueryResult, 'columns' | 'data' | 'total_count'> & {
  columns?: string[]
  data?: Record<string, unknown>[]
  total_count?: number
  query_mode?: string
  semantic_config_id?: string
  semantic_config_name?: string
}

export interface QueryTaskCreateResult {
  task_id: string
  task_type: 'sql' | 'sql_batch'
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'partial_success'
  poll_interval_ms: number
}

export interface StatementQueryResult {
  statement_index: number
  sql: string
  status: string
  error?: string | null
  results?: MemberQueryResult[]
}

export interface QueryTaskStatusResult {
  task_id: string
  task_type: 'sql' | 'sql_batch'
  datasource_id: string
  query_type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'partial_success'
  progress_text?: string
  results?: MemberQueryResult[] | null
  statement_results?: StatementQueryResult[] | null
  error_message?: string | null
  started_time?: string | null
  finished_time?: string | null
  create_time?: string | null
  update_time?: string | null
}

export interface CancelQueryExecutionResult {
  execution_id?: string
  status?: string
  message?: string | null
}

export interface CancelQueryTaskResult {
  task_id: string
  status: string
  executions?: CancelQueryExecutionResult[]
}

export interface CancelRunningQueryHistoryResult {
  total: number
  cancelled: number
  failed: number
  executions?: CancelQueryExecutionResult[]
}

export interface QueryHistoryResult {
  execution_id: string
  query: string
  member_datasource_id?: string
  member_name?: string
  execution_time_ms?: number | null
  error_message?: string | null
  result_payload?: {
    status?: string | null
    columns?: string[]
    data?: Record<string, unknown>[]
    total_count?: number
    execution_time_ms?: number | null
    error?: string | null
  } | null
}
