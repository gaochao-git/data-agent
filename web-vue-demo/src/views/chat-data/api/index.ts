/**
 * ChatData API
 * 使用主项目统一的 request 封装
 * 服务前缀: /agents (通过 vite proxy 转发到 FastAPI)
 */

import request from '@/utils/request'
import requestStream from '@/utils/requestStream'
import type {
  ApiResponse,
  DataSource,
  ConversationItem,
  MemberQueryResult,
  SemanticConfigItem,
} from '../types'

// 看板 API 统一复用 ./dashboard 实现，避免重复维护
export {
  listDashboards as getDashboards,
  listDashboards,
  getDashboard,
  createDashboard,
  updateDashboard,
  deleteDashboard,
  addPanel,
  updatePanel,
  deletePanel,
  updateLayout,
  executePanel,
  executePanelsBatch,
} from './dashboard'

// ============ 数据源 API ============

/** 获取数据源列表 */
export function getDatasources(params?: { include_tables?: boolean }): Promise<ApiResponse<{ datasources: DataSource[] }>> {
  return request({
    url: `/agents/api/chatdata/datasources`,
    method: 'get',
    params
  })
}

/** 删除数据源 */
export function deleteDatasource(id: string): Promise<ApiResponse<null>> {
  return request({
    url: `/agents/api/chatdata/datasources/${id}`,
    method: 'delete'
  })
}

/** 获取数据库表列表（用于同步前选择表） */
export function getDbTables(data: {
  source_type: string
  host: string
  port: number
  database: string
}): Promise<ApiResponse<{ tables: string[] }>> {
  return request({
    url: `/agents/api/chatdata/datasources/db-tables`,
    method: 'post',
    data
  })
}

/** 设置默认数据源 */
export function setDefaultDatasource(dsId: string): Promise<ApiResponse<null>> {
  return request({
    url: `/agents/api/chatdata/datasources/${dsId}/set-default`,
    method: 'post'
  })
}

/** 取消默认数据源 */
export function unsetDefaultDatasource(dsId: string): Promise<ApiResponse<null>> {
  return request({
    url: `/agents/api/chatdata/datasources/${dsId}/set-default`,
    method: 'delete'
  })
}

/** 更新数据源元信息 */
export function updateDatasource(
  datasourceId: string,
  data: { name?: string; description?: string }
): Promise<ApiResponse<DataSource>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}`,
    method: 'patch',
    data
  })
}

/** 更新数据源映射模块 */
export function updateMappedModules(datasourceId: string, mappedModules: string[]): Promise<ApiResponse<null>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/mapped-modules`,
    method: 'patch',
    data: { mapped_modules: mappedModules }
  })
}

/** 创建数据源组 */
export function createDatasourceGroup(data: {
  group_name: string
  group_desc?: string
  source_type: 'mysql' | 'hive'
  template_source: {
    datasource_name: string
    query_executor_type: 'direct' | 'http'
    host: string
    port?: number
    database: string
  }
  template_table_names?: string[]
  member_sources?: Array<{
    datasource_name: string
    query_executor_type: 'direct' | 'http'
    host: string
    port?: number
    database: string
  }>
}): Promise<ApiResponse<DataSource>> {
  return request({
    url: `/agents/api/chatdata/datasource-groups`,
    method: 'post',
    data
  })
}

/** 更新数据源组 */
export function updateDatasourceGroup(datasourceId: string, data: {
  group_name: string
  group_desc?: string
  source_type: 'mysql' | 'hive'
  template_source: {
    datasource_name: string
    query_executor_type: 'direct' | 'http'
    host: string
    port?: number
    database: string
  }
  template_table_names?: string[]
  member_sources?: Array<{
    datasource_name: string
    query_executor_type: 'direct' | 'http'
    host: string
    port?: number
    database: string
  }>
}): Promise<ApiResponse<DataSource>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/group-config`,
    method: 'put',
    data
  })
}

// ============ 会话 API ============

/** 获取会话列表 */
export function getConversations(agentId: string, datasourceId?: string): Promise<ApiResponse<{ conversations: ConversationItem[] }>> {
  return request({
    url: '/agents/api/conversations',
    method: 'get',
    params: { agent_id: agentId, datasource_id: datasourceId }
  })
}

/** 创建会话 */
export function createConversation(data: {
  agent_id: string
  datasource_id?: string
  title?: string
}): Promise<ApiResponse<{ id: string; conversation?: ConversationItem; create_time?: string }>> {
  return request({
    url: '/agents/api/conversations',
    method: 'post',
    data
  })
}

/** 删除会话 */
export function deleteConversation(conversationId: string): Promise<ApiResponse<null>> {
  return request({
    url: `/agents/api/conversations/${conversationId}`,
    method: 'delete'
  })
}

/** 更新会话 */
export function updateConversation(conversationId: string, data: { title?: string }): Promise<ApiResponse<null>> {
  return request({
    url: `/agents/api/conversations/${conversationId}`,
    method: 'put',
    data
  })
}

/** 生成会话标题 */
export function generateConversationTitle(conversationId: string): Promise<ApiResponse<{ title: string }>> {
  return request({
    url: `/agents/api/conversations/${conversationId}/title`,
    method: 'post'
  })
}

// ============ 语义配置 API ============

/** 获取语义配置列表 */
export function getSemanticConfigs(datasourceId: string): Promise<ApiResponse<{ configs: SemanticConfigItem[] }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs`,
    method: 'get'
  })
}

/** 创建语义配置 */
export function createSemanticConfig(datasourceId: string, data: Partial<SemanticConfigItem>): Promise<ApiResponse<SemanticConfigItem>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs`,
    method: 'post',
    data
  })
}

/** 更新语义配置 */
export function updateSemanticConfig(datasourceId: string, configId: string, data: Partial<SemanticConfigItem>): Promise<ApiResponse<SemanticConfigItem>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}`,
    method: 'put',
    data
  })
}

/** 删除语义配置 */
export function deleteSemanticConfig(datasourceId: string, configId: string): Promise<ApiResponse<null>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}`,
    method: 'delete'
  })
}

/** 切换语义配置启用状态 */
export function toggleSemanticConfig(datasourceId: string, configId: string, enabled: boolean): Promise<ApiResponse<SemanticConfigItem>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/toggle`,
    method: 'patch',
    params: { enabled }
  })
}

/** 语义字段自动预填（规则版） */
export function autoPrefillSemanticFields(
  data: {
    datasource_id: string
    config_id: string
    tables: Array<{
      table_name: string
      description?: string
      columns: Array<{
        name: string
        type: string
        mysql_type?: string
        hive_type?: string
        description?: string
      }>
    }>
  }
): Promise<ApiResponse<{ fields: Array<{ table: string; column: string; is_dimension: boolean; is_metric: boolean; expression: string }>; total: number }>> {
  return request({
    url: `/agents/api/chatdata/semantic/prefill`,
    method: 'post',
    data
  })
}

// ============ 查询 API ============

/** SQL 执行结果类型 */
export interface SqlExecuteResult {
  sql?: string
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  execution_time_ms?: number
  member_datasource_id?: string
  member_name?: string
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

/** 执行 SQL 查询 */
export function executeSql(params: {
  sql: string
  datasource_id: string
  member_datasource_ids?: string[]
  limit?: number
  timeout?: number
}): Promise<ApiResponse<SqlExecuteResult>> {
  return request({
    url: `/agents/api/chatdata/execute-sql`,
    method: 'post',
    data: {
      sql: params.sql,
      datasource_id: params.datasource_id,
      member_datasource_ids: params.member_datasource_ids || [],
      limit: params.limit ?? 100,
      timeout: params.timeout,
    }
  })
}

/** 创建异步 SQL 查询任务 */
export function createQueryTask(params: {
  sql: string
  datasource_id: string
  member_datasource_ids?: string[]
  limit?: number
  timeout?: number
  query_type?: string
  builder_config?: Record<string, unknown>
}): Promise<ApiResponse<QueryTaskCreateResult>> {
  return request({
    url: `/agents/api/chatdata/query-tasks`,
    method: 'post',
    data: {
      task_type: 'sql',
      sql: params.sql,
      datasource_id: params.datasource_id,
      member_datasource_ids: params.member_datasource_ids || [],
      limit: params.limit ?? 100,
      timeout: params.timeout,
      query_type: params.query_type ?? 'sql',
      builder_config: params.builder_config ?? null,
    }
  })
}

/** 查询异步 SQL 任务状态 */
export function getQueryTask(taskId: string): Promise<ApiResponse<QueryTaskStatusResult>> {
  return request({
    url: `/agents/api/chatdata/query-tasks/${taskId}`,
    method: 'get',
  })
}

/** 编译可视化查询 DSL 为 SQL（不执行） */
export function compileBuilderSql(params: {
  builder_config: Record<string, unknown>
  datasource_id?: string
}): Promise<ApiResponse<{ sql: string }>> {
  return request({
    url: `/agents/api/chatdata/compile-builder-sql`,
    method: 'post',
    data: params
  })
}

/** 获取数据源表结构 */
export function getDatasourceSchema(datasourceId: string): Promise<ApiResponse<{ tables: Array<{ table_name: string; description?: string; columns?: Array<{ name: string; type: string; mysql_type?: string; description?: string }> }> }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/schema`,
    method: 'get'
  })
}

/** 获取收藏查询 */
export function getQueryFavorites(params: {
  datasource_id: string
  limit?: number
  query_type?: 'conversation' | 'sql' | 'builder'
}): Promise<ApiResponse<{ queries: unknown[] }>> {
  return request({
    url: '/agents/api/chatdata/query-favorites',
    method: 'get',
    params
  })
}

/** 创建收藏查询 */
export function createQueryFavorite(data: {
  datasource_id: string
  query_type: 'conversation' | 'sql' | 'builder'
  favorite_title: string
  query_text: string
  builder_config?: Record<string, unknown>
  semantic_config_id?: string
}): Promise<ApiResponse<Record<string, unknown>>> {
  return request({
    url: '/agents/api/chatdata/query-favorites',
    method: 'post',
    data
  })
}

/** 更新收藏查询 */
export function updateQueryFavorite(id: number | string, data: {
  favorite_title?: string
  query_text?: string
  builder_config?: Record<string, unknown>
  semantic_config_id?: string
}): Promise<ApiResponse<Record<string, unknown>>> {
  return request({
    url: `/agents/api/chatdata/query-favorites/${id}`,
    method: 'put',
    data
  })
}

/** 删除收藏查询 */
export function deleteQueryFavorite(id: number | string): Promise<ApiResponse<{ id: number | string }>> {
  return request({
    url: `/agents/api/chatdata/query-favorites/${id}`,
    method: 'delete'
  })
}

// ============ 流式 API (SSE) ============

/** 流式对话补全 */
export function streamCompletion(conversationId: string, data: {
  query: string
  agent_id: string
  options?: Record<string, unknown>
}, signal?: AbortSignal): Promise<Response> {
  return requestStream({
    url: `/agents/api/conversations/${conversationId}/completion`,
    method: 'POST',
    data,
    signal
  })
}

/** 流式恢复执行 */
export function streamResume(conversationId: string, data: {
  action: 'approve' | 'edit' | 'reject'
  modified_sql?: string
  datasource_id?: string
  semantic_config_id?: string
}): Promise<Response> {
  return requestStream({
    url: `/agents/api/conversations/${conversationId}/resume`,
    method: 'POST',
    data
  })
}

/** 流式快速生成 SQL */
export function streamQuickSql(data: {
  query: string
  datasource_id: string
  member_datasource_ids?: string[]
  semantic_config_id?: string
  session_id?: string
}, signal?: AbortSignal): Promise<Response> {
  return requestStream({
    url: '/agents/api/chatdata/nl2sql',
    method: 'POST',
    data,
    signal
  })
}

/** 获取会话历史（流式接口） */
export function getConversationHistory(conversationId: string): Promise<Response> {
  return requestStream({
    url: `/agents/api/conversations/${conversationId}`
  })
}
