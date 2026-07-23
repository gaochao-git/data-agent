import request from '@/utils/request'
import type { ApiResponse } from '../types'
import type {
  CancelQueryExecutionResult,
  CancelQueryTaskResult,
  CancelRunningQueryHistoryResult,
  QueryHistoryResult,
  QueryTaskCreateResult,
  QueryTaskStatusResult,
} from '../types/query'

export type {
  CancelQueryExecutionResult,
  CancelQueryTaskResult,
  CancelRunningQueryHistoryResult,
  QueryHistoryResult,
  QueryTaskCreateResult,
  QueryTaskStatusResult,
} from '../types/query'

export function createQueryTask(params: {
  sql?: string[]
  datasource_id: string
  member_datasource_ids?: string[]
  limit?: number
  timeout?: number
  query_type?: string
  builder_config?: Record<string, unknown>
  semantic_builder_config?: Record<string, unknown>
  semantic_config_id?: string
  query_text?: string
  skip_query_cache?: boolean
}): Promise<ApiResponse<QueryTaskCreateResult>> {
  const data: Record<string, unknown> = {
    datasource_id: params.datasource_id,
    member_datasource_ids: params.member_datasource_ids || [],
    ...(params.limit !== undefined ? { limit: params.limit } : {}),
    timeout: params.timeout,
    query_type: params.query_type ?? 'sql',
    semantic_config_id: params.semantic_config_id,
    query_text: params.query_text,
    skip_query_cache: params.skip_query_cache ?? false,
  }
  if (params.sql !== undefined) {
    data.sql = params.sql
  }
  if (params.builder_config !== undefined) {
    data.builder_config = params.builder_config
  }
  if (params.semantic_builder_config !== undefined) {
    data.semantic_builder_config = params.semantic_builder_config
  }
  return request({
    url: `/agents/api/chatdata/query-tasks`,
    method: 'post',
    data
  })
}

export function getQueryTask(taskId: string): Promise<ApiResponse<QueryTaskStatusResult>> {
  return request({
    url: `/agents/api/chatdata/query-tasks/${taskId}`,
    method: 'get',
  })
}

export function cancelQueryTask(taskId: string): Promise<ApiResponse<CancelQueryTaskResult>> {
  return request({
    url: `/agents/api/chatdata/query-tasks/${taskId}/cancel`,
    method: 'post',
  })
}

export function cancelQueryHistoryExecution(executionId: string): Promise<ApiResponse<CancelQueryExecutionResult>> {
  return request({
    url: `/agents/api/chatdata/query-history/${executionId}/cancel`,
    method: 'post',
  })
}

export function cancelRunningQueryHistory(params?: { datasource_id?: string }): Promise<ApiResponse<CancelRunningQueryHistoryResult>> {
  return request({
    url: '/agents/api/chatdata/query-history/cancel-running',
    method: 'post',
    params
  })
}

export function cancelGlobalRunningQueryHistory(params?: { datasource_id?: string }): Promise<ApiResponse<CancelRunningQueryHistoryResult>> {
  return request({
    url: '/agents/api/chatdata/admin/query-history/cancel-running',
    method: 'post',
    params
  })
}

export function getQueryHistoryResult(executionId: string): Promise<ApiResponse<QueryHistoryResult>> {
  return request({
    url: `/agents/api/chatdata/query-history/${executionId}/result`,
    method: 'get',
  })
}

export function compileSemanticBuilderSql(params: {
  datasource_id: string
  semantic_config_id: string
  semantic_builder_config: Record<string, unknown>
}): Promise<ApiResponse<{ sql: string; builder_config?: Record<string, unknown> }>> {
  return request({
    url: `/agents/api/chatdata/compile-semantic-builder-sql`,
    method: 'post',
    data: params
  })
}
