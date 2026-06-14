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
  ChatDataModelOption,
  SkillInfo,
  SkillVersionInfo,
  SqlHistoryItem,
  DashboardSpecPayload,
} from '../types'
import type { Dashboard } from './dashboard'

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

// ============ 技能 API ============

export interface ChatDataRuntimeConfig {
  models: ChatDataModelOption[]
  data_analyze_enabled?: boolean
  default_row_limit?: number
  query_cache_enabled?: boolean
}

export interface AccuracyEvaluationRunParams {
  datasource_id?: string
  semantic_config_id?: string
  model_id?: string
  agent_id?: 'chat_data' | 'chat_data_data_analyze'
  query_mode?: string
  limit?: number
  case_ids?: string[]
  include_traces?: boolean
  local_compile?: boolean
  skip_execution?: boolean
  request_timeout?: number
}

export interface AccuracyEvaluationCaseItem {
  id: string
  nl: string
  gold_dsl: Record<string, unknown>
  gold_sql: string
  query_type: 'filter' | 'join' | 'aggregation' | 'subquery'
  difficulty: 'easy' | 'medium' | 'hard'
  source: 'human' | 'llm'
  business_domain: string
}

export interface AccuracyEvaluationCaseSet {
  cases: AccuracyEvaluationCaseItem[]
  total: number
  business_domain: string
  scene_id: string
}

export interface AccuracyEvaluationReport {
  summary: {
    total: number
    scored: number
    pending?: number
    failed?: number
    semantic_accuracy: number
    compile_success_count?: number
    dsl2sql_compile_success_rate?: number
    execution_checked?: number
    execution_success_count?: number
    sql_execution_success_rate?: number | null
    debug_metrics?: {
      dsl_exact_match_count?: number
      dsl_exact_match_rate?: number
      sql_exact_match_count?: number
      sql_exact_match_rate?: number | null
      avg_dsl_slot_accuracy?: number
    }
  }
  results: Array<Record<string, unknown>>
}

export interface AccuracyEvaluationRunHistoryItem {
  run_id: string
  case_set_id: string
  datasource_id: string
  semantic_config_id: string
  user_id: string
  create_by?: string
  create_by_name?: string
  update_by?: string
  update_by_name?: string
  tester_name?: string
  run_mode: string
  run_status: string
  request_payload?: Record<string, unknown>
  summary?: AccuracyEvaluationReport['summary'] | Record<string, unknown>
  total_count: number
  scored_count: number
  passed_count: number
  failed_count: number
  semantic_accuracy: number
  error_message?: string
  started_time?: string | null
  finished_time?: string | null
  create_time?: string | null
  update_time?: string | null
}

export interface AccuracyEvaluationRunCaseHistoryItem {
  run_case_id: string
  run_id: string
  case_id: string
  case_status: string
  result: Record<string, unknown>
  semantic_accuracy: number
  elapsed_ms?: number | null
  error_message?: string
  create_time?: string | null
  update_time?: string | null
}

/** 获取智能问数模型列表 */
export function getChatDataModels(): Promise<ApiResponse<{ models: ChatDataModelOption[]; default_model: string }>> {
  return request({
    url: `/agents/api/chatdata/models`,
    method: 'get'
  })
}

/** 获取智能问数运行时配置 */
export function getChatDataConfig(): Promise<ApiResponse<ChatDataRuntimeConfig>> {
  return request({
    url: `/agents/api/chatdata/config`,
    method: 'get'
  })
}

export interface AccuracyEvaluationStreamHandlers {
  onStart?: (payload: { total: number; case_ids: string[] }) => void
  onCaseStart?: (payload: { index: number; total: number; id: string; question?: string }) => void
  onCaseResult?: (payload: { index: number; total: number; id: string; result: Record<string, unknown> }) => void
  onProgress?: (payload: { index: number; total: number; summary: AccuracyEvaluationReport['summary'] }) => void
  onDone?: (payload: { report: AccuracyEvaluationReport }) => void
  onError?: (message: string) => void
}

function parseAccuracyEvaluationSseBuffer(buffer: string): { events: Array<{ data: string }>; remaining: string } {
  const events: Array<{ data: string }> = []
  const blocks = buffer.split('\n\n')
  const remaining = blocks.pop() || ''
  for (const block of blocks) {
    if (!block.trim()) continue
    const lines = block.split('\n')
    let dataStr = ''
    for (const line of lines) {
      if (line.startsWith('data:')) dataStr = line.substring(5).trim()
    }
    if (dataStr) events.push({ data: dataStr })
  }
  return { events, remaining }
}

/** 流式运行准确率评测，逐条回调用例结果 */
export async function runAccuracyEvaluationStream(
  data: AccuracyEvaluationRunParams,
  handlers: AccuracyEvaluationStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const response = await requestStream({
    url: `/agents/api/chatdata/evaluations/accuracy/run-stream`,
    method: 'post',
    data,
    signal,
  })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `评测请求失败: ${response.status}`)
  }
  const reader = response.body?.getReader()
  if (!reader) throw new Error('评测流响应不可读')
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parsed = parseAccuracyEvaluationSseBuffer(buffer)
    buffer = parsed.remaining
    for (const item of parsed.events) {
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(item.data) as Record<string, unknown>
      } catch {
        continue
      }
      const event = String(payload.event || '')
      if (event === 'error') {
        handlers.onError?.(String(payload.message || '评测失败'))
        return
      }
      if (event === 'start') handlers.onStart?.(payload as { total: number; case_ids: string[] })
      if (event === 'case_start') handlers.onCaseStart?.(payload as { index: number; total: number; id: string; question?: string })
      if (event === 'case_result') handlers.onCaseResult?.(payload as { index: number; total: number; id: string; result: Record<string, unknown> })
      if (event === 'progress') handlers.onProgress?.(payload as { index: number; total: number; summary: AccuracyEvaluationReport['summary'] })
      if (event === 'done') handlers.onDone?.(payload as { report: AccuracyEvaluationReport })
    }
  }
}

export function getAccuracyEvaluationCases(datasourceId: string, configId: string): Promise<ApiResponse<AccuracyEvaluationCaseSet>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases`,
    method: 'get'
  })
}

export function saveAccuracyEvaluationCases(datasourceId: string, configId: string, data: { business_domain?: string; cases: AccuracyEvaluationCaseItem[] }): Promise<ApiResponse<AccuracyEvaluationCaseSet>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases`,
    method: 'put',
    data
  })
}

export function generateAccuracyEvaluationCases(datasourceId: string, configId: string): Promise<ApiResponse<AccuracyEvaluationCaseSet>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases/generate`,
    method: 'post'
  })
}

export function draftAccuracyEvaluationCase(datasourceId: string, configId: string, data: { hint?: string; existing_questions?: string[] }): Promise<ApiResponse<{ case: AccuracyEvaluationCaseItem }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases/draft-case`,
    method: 'post',
    data
  })
}

export function getAccuracyEvaluationRuns(params: {
  datasource_id?: string
  semantic_config_id?: string
  page?: number
  page_size?: number
}): Promise<ApiResponse<{ runs: AccuracyEvaluationRunHistoryItem[]; total: number; page: number; page_size: number }>> {
  return request({
    url: `/agents/api/chatdata/evaluations/accuracy/runs`,
    method: 'get',
    params,
  })
}

export function getAccuracyEvaluationRunDetail(runId: string): Promise<ApiResponse<{ run: AccuracyEvaluationRunHistoryItem; cases: AccuracyEvaluationRunCaseHistoryItem[] }>> {
  return request({
    url: `/agents/api/chatdata/evaluations/accuracy/runs/${runId}`,
    method: 'get',
  })
}

/** 获取专家技能列表 */
export function getSkills(params?: { search?: string; category?: string; scope?: 'all' | 'favorites' | 'mine' | 'system' }): Promise<ApiResponse<{ skills: SkillInfo[]; total: number }>> {
  return request({
    url: `/agents/api/chatdata/skills`,
    method: 'get',
    params
  })
}

export function createSkill(data: {
  skill_name?: string
  skill_title: string
  skill_desc?: string
  skill_category?: string
  visibility_type?: 'private' | 'global'
  content?: string
}): Promise<ApiResponse<SkillInfo & { content?: string }>> {
  return request({
    url: `/agents/api/chatdata/skills`,
    method: 'post',
    data
  })
}

export function publishSkill(skillId: string): Promise<ApiResponse<SkillInfo>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/publish`,
    method: 'post'
  })
}

export function updateSkill(skillId: string, data: {
  skill_title: string
  skill_desc?: string
  skill_category?: string
  visibility_type?: 'private' | 'global'
  content?: string
}): Promise<ApiResponse<SkillInfo & { content?: string }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}`,
    method: 'put',
    data
  })
}

export function deleteSkill(skillId: string): Promise<ApiResponse<{ deleted: boolean }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}`,
    method: 'delete'
  })
}

export function copySkill(skillId: string): Promise<ApiResponse<SkillInfo>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/copy`,
    method: 'post'
  })
}

export function getSkillVersions(skillId: string): Promise<ApiResponse<{ versions: SkillVersionInfo[]; total: number }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions`,
    method: 'get'
  })
}

export function getSkillVersion(skillId: string, versionNo: number): Promise<ApiResponse<SkillVersionInfo>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}`,
    method: 'get'
  })
}

export function getSkillVersionFiles(skillId: string, versionNo: number): Promise<ApiResponse<{ references: string[]; scripts: string[]; assets: string[] }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files`,
    method: 'get'
  })
}

export function getSkillVersionFile(skillId: string, versionNo: number, params: { dir_name: 'references' | 'assets' | 'scripts'; file_path: string }): Promise<ApiResponse<{ dir_name: string; file_path: string; content: string }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files/content`,
    method: 'get',
    params
  })
}

export function saveSkillVersionFile(skillId: string, versionNo: number, data: { dir_name: 'references' | 'assets' | 'scripts'; file_path: string; content: string }): Promise<ApiResponse<{ saved: boolean; skill_version_no?: number }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files`,
    method: 'put',
    data
  })
}

export function deleteSkillVersionFile(skillId: string, versionNo: number, params: { dir_name: 'references' | 'assets' | 'scripts'; file_path: string }): Promise<ApiResponse<{ deleted: boolean; skill_version_no?: number }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files`,
    method: 'delete',
    params
  })
}

export function rollbackSkillVersion(skillId: string, versionNo: number): Promise<ApiResponse<SkillInfo>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/rollback`,
    method: 'post'
  })
}

export function favoriteSkill(skillId: string): Promise<ApiResponse<{ skill_id: string; favorite: boolean }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/favorite`,
    method: 'post'
  })
}

export function unfavoriteSkill(skillId: string): Promise<ApiResponse<{ skill_id: string; favorite: boolean }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/favorite`,
    method: 'delete'
  })
}

export function syncSystemSkills(): Promise<ApiResponse<{ synced: number; total: number }>> {
  return request({
    url: `/agents/api/chatdata/skills/sync-system`,
    method: 'post'
  })
}

/** 获取专家技能详情 */
export function getSkill(skillName: string): Promise<ApiResponse<SkillInfo & { content?: string; references?: string[]; scripts?: string[] }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillName}`,
    method: 'get'
  })
}

/** 根据 AI 看板规格创建看板 */
export function createDashboardFromSpec(data: {
  dashboard_spec: DashboardSpecPayload['dashboard_spec']
  datasource_id?: string | null
  semantic_config_id?: string | null
  conversation_id?: string | null
}): Promise<ApiResponse<{ dashboard: Dashboard; panels: unknown[]; warnings: string[] }>> {
  return request({
    url: `/agents/api/chatdata/dashboards/from-spec`,
    method: 'post',
    data
  })
}

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
  query_executor_type?: 'direct' | 'http'
  org_code?: string
  org_name?: string
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

export interface DatasourceTableGroupItem {
  table_group_name: string
  table_names: string[]
}

export interface DatasourceColumnDictEnumItem {
  value: string
  label: string
}

export interface DatasourceColumnDictPayload {
  enum_values: DatasourceColumnDictEnumItem[]
  synonyms: string[]
  storage_format: string
  unit: string
}

export function getDatasourceTableGroups(datasourceId: string): Promise<ApiResponse<{ datasource_id: string; groups: DatasourceTableGroupItem[] }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/table-groups`,
    method: 'get'
  })
}

export function updateDatasourceTableGroups(datasourceId: string, groups: DatasourceTableGroupItem[]): Promise<ApiResponse<{ datasource_id: string; groups: DatasourceTableGroupItem[] }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/table-groups`,
    method: 'put',
    data: { groups }
  })
}

export function getDatasourceColumnDict(
  datasourceId: string,
  tableName: string,
  columnName: string
): Promise<ApiResponse<{ datasource_id: string; table_name: string; column_name: string; dict: DatasourceColumnDictPayload }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns/${encodeURIComponent(columnName)}/dict`,
    method: 'get'
  })
}

export function getDatasourceTableColumnDicts(
  datasourceId: string,
  tableName: string
): Promise<ApiResponse<{ datasource_id: string; table_name: string; dicts: Record<string, DatasourceColumnDictPayload> }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/tables/${encodeURIComponent(tableName)}/column-dicts`,
    method: 'get'
  })
}

export function updateDatasourceColumnDict(
  datasourceId: string,
  tableName: string,
  columnName: string,
  dict: DatasourceColumnDictPayload
): Promise<ApiResponse<{ datasource_id: string; table_name: string; column_name: string; dict: DatasourceColumnDictPayload }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns/${encodeURIComponent(columnName)}/dict`,
    method: 'put',
    data: { dict }
  })
}

export function generateDatasourceColumnDict(
  datasourceId: string,
  tableName: string,
  columnName: string,
  text: string
): Promise<ApiResponse<{ datasource_id: string; table_name: string; column_name: string; dict: DatasourceColumnDictPayload }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns/${encodeURIComponent(columnName)}/dict/generate`,
    method: 'post',
    data: { text }
  })
}

export function updateDatasourceTableBusinessDesc(
  datasourceId: string,
  tableName: string,
  businessDesc: string
): Promise<ApiResponse<{ datasource_id: string; table: DataSource['ds_tables'][number] }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/tables/${encodeURIComponent(tableName)}/business-desc`,
    method: 'put',
    data: { business_desc: businessDesc }
  })
}

export interface SemanticHealthIssue {
  level: 'error' | 'warning'
  type: string
  message: string
  target: string
  config_id?: string
  config_name?: string
}

export interface SemanticHealthConfigResult {
  config_id: string
  config_name: string
  status: 'ok' | 'warning' | 'invalid'
  issues: SemanticHealthIssue[]
}

export interface DatasourceSchemaHealthResult {
  status: 'ok' | 'warning' | 'invalid'
  summary: {
    cached_table_count: number
    physical_table_count: number
    issue_count: number
    error_count: number
    warning_count: number
  }
  issues: SemanticHealthIssue[]
}

export interface DatasourceSemanticHealthResult {
  datasource_id: string
  group_name: string
  status: 'ok' | 'warning' | 'invalid'
  summary: {
    config_count: number
    issue_count: number
    error_count: number
    warning_count: number
  }
  configs: SemanticHealthConfigResult[]
  issues: SemanticHealthIssue[]
  schema?: DatasourceSchemaHealthResult
  semantic?: {
    datasource_id: string
    group_name: string
    status: 'ok' | 'warning' | 'invalid'
    summary: DatasourceSemanticHealthResult['summary']
    configs: SemanticHealthConfigResult[]
    issues: SemanticHealthIssue[]
  }
}

export function getDatasourceSemanticHealth(datasourceId: string): Promise<ApiResponse<DatasourceSemanticHealthResult>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-health`,
    method: 'get'
  })
}

/** 创建数据源组 */
export function createDatasourceGroup(data: {
  group_name: string
  group_desc?: string
  source_type: 'mysql' | 'hive'
  template_source: {
    org_name: string
    org_code: string
    query_executor_type: 'direct' | 'http'
    host: string
    port?: number
    database: string
  }
  template_table_names?: string[]
  member_sources?: Array<{
    org_name: string
    org_code: string
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
    org_name: string
    org_code: string
    query_executor_type: 'direct' | 'http'
    host: string
    port?: number
    database: string
  }
  template_table_names?: string[]
  member_sources?: Array<{
    org_name: string
    org_code: string
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
export function getConversations(
  agentId: string,
  datasourceId?: string,
  pagination?: { page?: number; page_size?: number }
): Promise<ApiResponse<ConversationItem[] | { conversations: ConversationItem[]; page?: number; page_size?: number; has_more?: boolean }>> {
  return request({
    url: '/agents/api/conversations',
    method: 'get',
    params: { agent_id: agentId, datasource_id: datasourceId, ...pagination }
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

export interface DataAnalyzeWorkspaceFile {
  path: string
  relative_path: string
  directory: string
  name: string
  size: number
  mtime: number
  source: string
  artifact_type?: string
  renderer?: string
  artifact_payload?: unknown
  chart_spec?: unknown
  dashboard_spec?: unknown
}

/** 获取智能分析会话工作区文件 */
export function getDataAnalyzeWorkspaceFiles(conversationId: string): Promise<ApiResponse<{ files: DataAnalyzeWorkspaceFile[] }>> {
  return request({
    url: `/agents/api/chatdata/data-analyze/conversations/${conversationId}/workspace-files`,
    method: 'get'
  })
}

/** 读取智能分析会话工作区文本文件 */
export function getDataAnalyzeWorkspaceFileContent(conversationId: string, path: string): Promise<ApiResponse<{ path: string; relative_path: string; content: string; size: number }>> {
  return request({
    url: `/agents/api/chatdata/data-analyze/conversations/${conversationId}/workspace-files/content`,
    method: 'get',
    params: { path }
  })
}

export function importDataAnalyzeWorkspaceSkill(conversationId: string, data: { skill_dir_path: string }): Promise<ApiResponse<SkillInfo & { content?: string }>> {
  return request({
    url: `/agents/api/chatdata/data-analyze/conversations/${conversationId}/workspace-skills/import`,
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
    table_model_type?: 'detail' | 'aggregated'
    generate_business_flow?: boolean
    business_context?: string
    tables: Array<{
      table_name: string
      table_alias?: string
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
): Promise<ApiResponse<{
  fields: Array<{
    table: string
    table_alias?: string
    column: string
    source_column?: string
    role: 'metric' | 'dimension'
    expression: string
    name?: string
    field_name?: string
    agg?: string
  }>
  total: number
  knowledge?: { business_flow?: string }
}>> {
  return request({
    url: `/agents/api/chatdata/semantic/prefill`,
    method: 'post',
    data
  })
}

/** 上传并解析自动预填业务补充文件 */
export function parseSemanticPrefillContextFile(file: File): Promise<ApiResponse<{ filename: string; size: number; text: string }>> {
  const data = new FormData()
  data.append('file', file)
  return request({
    url: `/agents/api/chatdata/semantic/prefill/context-file`,
    method: 'post',
    data,
    headers: { repeatSubmit: false },
    transformRequest: [(formData: FormData, headers?: Record<string, unknown>) => {
      if (headers) {
        delete (headers as Record<string, unknown>)['Content-Type']
        delete (headers as Record<string, unknown>)['content-type']
      }
      return formData
    }],
  })
}

export interface SemanticHistoryLearningSample {
  execution_id: string
  task_id: string
  source: 'agent' | 'builder' | 'sql'
  status: string
  conversation_id?: string
  query_text?: string
  sql?: string
  error_message?: string
  executed_at?: string | null
  conversation_context?: {
    user_turns?: Array<{ content: string; timestamp?: string }>
    assistant_turns?: Array<{ content: string; timestamp?: string }>
    negative_feedback?: string[]
    tool_errors?: Array<{ tool_name?: string; content: string; timestamp?: string }>
  }
}

export interface SemanticHistoryLearningSuggestion {
  name?: string
  title?: string
  table?: string
  field_name?: string
  column?: string
  expression?: string
  type?: 'categorical' | 'time' | 'number'
  content?: string
  reason: string
  source: 'agent' | 'builder' | 'sql'
  confidence: 'high' | 'medium' | 'low'
  support_count: number
  samples: SemanticHistoryLearningSample[]
  needs_review?: boolean
  review_reason?: string
}

export interface SemanticHistoryLearningResult {
  datasource_id: string
  semantic_config_id: string
  days: number
  limit: number
  scan_limit?: number
  scanned_count: number
  matched_count: number
  skipped: Record<string, number>
  source_stats?: Record<'agent' | 'builder' | 'sql', { scanned: number; matched: number; skipped: number }>
  suggestions: {
    metrics: SemanticHistoryLearningSuggestion[]
    dimensions: SemanticHistoryLearningSuggestion[]
    issues: SemanticHistoryLearningSuggestion[]
  }
  samples: SemanticHistoryLearningSample[]
}

/** 从当前指标集的查询历史生成维护建议 */
export function learnSemanticConfigFromHistory(
  datasourceId: string,
  configId: string,
  data: {
    days: number
    limit: number
    sources: Array<'agent' | 'builder' | 'sql'>
    include_failed: boolean
  }
): Promise<ApiResponse<SemanticHistoryLearningResult>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/history-learning`,
    method: 'post',
    data
  })
}

// ============ 查询 API ============

/** SQL 执行结果类型 */
export interface SqlExecuteResult {
  success?: boolean
  error?: string | null
  sql?: string
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  execution_time_ms?: number
  member_datasource_id?: string
  member_name?: string
  results?: MemberQueryResult[]
  cache_hit?: boolean
  cache_age_seconds?: number | null
  cache_expires_in_seconds?: number | null
}

export function getConversationArtifact<T = unknown>(artifactId: string): Promise<ApiResponse<T>> {
  return request({
    url: `/agents/api/conversations/artifacts/${artifactId}`,
    method: 'get',
  })
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
  executions?: SqlHistoryItem[] | null
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

export interface QueryHistoryResult {
  execution_id: string
  query: string
  member_datasource_id?: string
  member_name?: string
  execution_time_ms?: number | null
  error_message?: string | null
  result_payload?: {
    success?: boolean
    columns?: string[]
    data?: Record<string, unknown>[]
    total_count?: number
    execution_time_ms?: number | null
    error?: string | null
  } | null
}

/** 执行 SQL 查询 */
export function executeSql(params: {
  sql: string
  datasource_id: string
  member_datasource_ids?: string[]
  limit?: number
  timeout?: number
  skip_query_cache?: boolean
}): Promise<ApiResponse<SqlExecuteResult>> {
  return request({
    url: `/agents/api/chatdata/execute-sql`,
    method: 'post',
    data: {
      sql: params.sql,
      datasource_id: params.datasource_id,
      member_datasource_ids: params.member_datasource_ids || [],
      ...(params.limit !== undefined ? { limit: params.limit } : {}),
      timeout: params.timeout,
      skip_query_cache: params.skip_query_cache ?? false,
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
  semantic_builder_config?: Record<string, unknown>
  semantic_config_id?: string
  query_text?: string
  skip_query_cache?: boolean
}): Promise<ApiResponse<QueryTaskCreateResult>> {
  const data: Record<string, unknown> = {
    task_type: 'sql',
    sql: params.sql,
    datasource_id: params.datasource_id,
    member_datasource_ids: params.member_datasource_ids || [],
    ...(params.limit !== undefined ? { limit: params.limit } : {}),
    timeout: params.timeout,
    query_type: params.query_type ?? 'sql',
    semantic_config_id: params.semantic_config_id,
    query_text: params.query_text,
    skip_query_cache: params.skip_query_cache ?? false,
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

/** 查询异步 SQL 任务状态 */
export function getQueryTask(taskId: string): Promise<ApiResponse<QueryTaskStatusResult>> {
  return request({
    url: `/agents/api/chatdata/query-tasks/${taskId}`,
    method: 'get',
  })
}

/** 停止异步 SQL 查询任务 */
export function cancelQueryTask(taskId: string): Promise<ApiResponse<CancelQueryTaskResult>> {
  return request({
    url: `/agents/api/chatdata/query-tasks/${taskId}/cancel`,
    method: 'post',
  })
}

/** 获取单条 SQL 查询历史结果 */
export function getQueryHistoryResult(executionId: string): Promise<ApiResponse<QueryHistoryResult>> {
  return request({
    url: `/agents/api/chatdata/query-history/${executionId}/result`,
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

/** 编译语义可视化查询 DSL 为 SQL（不执行） */
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

/** 获取数据源表结构 */
export function getDatasourceSchema(datasourceId: string): Promise<ApiResponse<{ tables: Array<{ table_name: string; description?: string; business_desc?: string; columns?: Array<{ name: string; type: string; mysql_type?: string; description?: string }> }> }>> {
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

/** 获取 SQL 查询历史 */
export function getQueryHistory(params: {
  datasource_id: string
  page?: number
  page_size?: number
  source?: 'all' | 'sql' | 'agent' | 'builder' | 'dashboard'
  status?: 'all' | 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timeout' | 'kill_failed'
}): Promise<ApiResponse<{ queries: SqlHistoryItem[]; total: number; page: number; page_size: number }>> {
  return request({
    url: '/agents/api/chatdata/query-history',
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
  conversation_id?: string
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
