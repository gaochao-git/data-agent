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

export interface QueryResult {
  status?: string | null
  sql?: string
  total_count?: number
  execution_time_ms?: number
  error?: string | null
  results?: MemberQueryResult[]
}

export interface QueryTaskCreateResult {
  task_id: string
  task_type: 'sql'
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'partial_success'
  poll_interval_ms: number
}

export interface QueryTaskStatusResult {
  task_id: string
  task_type: 'sql'
  datasource_id: string
  query_type: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'partial_success'
  progress_text?: string
  results?: MemberQueryResult[] | null
  error_message?: string | null
  started_time?: string | null
  finished_time?: string | null
  create_time?: string | null
  update_time?: string | null
}

export interface CancelQueryTaskResult {
  task_id: string
  status: string
  executions?: Array<{
    execution_id?: string
    status?: string
    success?: boolean
    message?: string | null
  }>
}

export interface CancelRunningQueryHistoryResult {
  total: number
  cancelled: number
  failed: number
  executions?: Array<{
    execution_id?: string
    status?: string
    success?: boolean
    message?: string | null
  }>
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
    success?: boolean
    columns?: string[]
    data?: Record<string, unknown>[]
    total_count?: number
    execution_time_ms?: number | null
    error?: string | null
  } | null
}
