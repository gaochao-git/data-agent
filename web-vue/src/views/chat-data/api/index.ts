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
  SemanticConfigItem,
  ChatDataModelOption,
  SkillInfo,
  SkillVersionInfo,
  SqlHistoryItem,
  DashboardSpecPayload,
} from '../types'
import type { Dashboard } from './dashboard'

export * from './query'

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

const CHATDATA_API_PREFIX = '/agents/api/chatdata'
const DATASOURCE_API_PREFIX = `${CHATDATA_API_PREFIX}/datasources`
const ADMIN_DATASOURCE_API_PREFIX = `${CHATDATA_API_PREFIX}/admin/datasources`

type DatasourceListParams = { include_tables?: boolean }
type DatasourceMetaUpdatePayload = { name?: string; description?: string }
type SemanticConfigCreatePayload = Omit<
  Partial<SemanticConfigItem>,
  'id' | 'scope_type' | 'can_manage' | 'create_time' | 'update_time' | 'create_by' | 'update_by'
>
type DbTablesRequestPayload = {
  source_type: string
  query_executor_type?: 'direct' | 'http'
  org_code?: string
  org_name?: string
  host?: string
  port?: number
  database: string
}
type DatasourceGroupPayload = {
  group_name: string
  group_desc?: string
  query_context?: string
  recommended_questions?: string[]
  source_type: 'mysql' | 'hive'
  template_source: {
    org_name: string
    org_code: string
    query_executor_type: 'direct' | 'http'
    host?: string
    port?: number
    database: string
  }
  member_sources?: Array<{
    org_name: string
    org_code: string
    query_executor_type: 'direct' | 'http'
    host?: string
    port?: number
    database: string
  }>
}
type DatasourceGroupMetadataSyncPayload = { template_table_names: string[] }

// ============ 技能 API ============

export interface ChatDataRuntimeOptions {
  workspace_backend: 'local' | 'sandbox'
  bash_enabled: boolean
}

export interface ChatDataRuntimeConfig {
  models: ChatDataModelOption[]
  runtime_options?: ChatDataRuntimeOptions
  /** 是否允许用户在前端手动覆盖系统默认运行选项。 */
  user_overridable?: boolean
  default_row_limit?: number
  query_cache_enabled?: boolean
}

export interface AccuracyEvaluationRunParams {
  datasource_id?: string
  semantic_config_id?: string
  model_id?: string
  agent_id?: 'chat_data'
  query_mode?: string
  limit?: number
  case_ids?: string[]
  include_traces?: boolean
  local_compile?: boolean
  skip_execution?: boolean
  request_timeout?: number
}

type AdminModeApiOptions = { adminMode?: boolean }

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
  signal?: AbortSignal,
  options?: AdminModeApiOptions
): Promise<void> {
  const response = await requestStream({
    url: options?.adminMode ? `/agents/api/chatdata/admin/evaluations/accuracy/run-stream` : `/agents/api/chatdata/evaluations/accuracy/run-stream`,
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

export function getAccuracyEvaluationCases(datasourceId: string, configId: string, options?: AdminModeApiOptions): Promise<ApiResponse<AccuracyEvaluationCaseSet>> {
  return request({
    url: options?.adminMode
      ? `/agents/api/chatdata/admin/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases`
      : `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases`,
    method: 'get'
  })
}

export function saveAccuracyEvaluationCases(datasourceId: string, configId: string, data: { business_domain?: string; cases: AccuracyEvaluationCaseItem[] }, options?: AdminModeApiOptions): Promise<ApiResponse<AccuracyEvaluationCaseSet>> {
  return request({
    url: options?.adminMode
      ? `/agents/api/chatdata/admin/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases`
      : `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases`,
    method: 'put',
    data
  })
}

export function generateAccuracyEvaluationCases(datasourceId: string, configId: string, options?: AdminModeApiOptions): Promise<ApiResponse<AccuracyEvaluationCaseSet>> {
  return request({
    url: options?.adminMode
      ? `/agents/api/chatdata/admin/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases/generate`
      : `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases/generate`,
    method: 'post'
  })
}

export function draftAccuracyEvaluationCase(datasourceId: string, configId: string, data: { hint?: string; existing_questions?: string[] }, options?: AdminModeApiOptions): Promise<ApiResponse<{ case: AccuracyEvaluationCaseItem }>> {
  return request({
    url: options?.adminMode
      ? `/agents/api/chatdata/admin/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases/draft-case`
      : `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/evaluation-cases/draft-case`,
    method: 'post',
    data
  })
}

export function getAccuracyEvaluationRuns(params: {
  datasource_id?: string
  semantic_config_id?: string
  page?: number
  page_size?: number
}, options?: AdminModeApiOptions): Promise<ApiResponse<{ runs: AccuracyEvaluationRunHistoryItem[]; total: number; page: number; page_size: number }>> {
  return request({
    url: options?.adminMode
      ? `/agents/api/chatdata/admin/evaluations/accuracy/runs`
      : `/agents/api/chatdata/evaluations/accuracy/runs`,
    method: 'get',
    params,
  })
}

export function getAccuracyEvaluationRunDetail(runId: string, options?: AdminModeApiOptions): Promise<ApiResponse<{ run: AccuracyEvaluationRunHistoryItem; cases: AccuracyEvaluationRunCaseHistoryItem[] }>> {
  return request({
    url: options?.adminMode
      ? `/agents/api/chatdata/admin/evaluations/accuracy/runs/${runId}`
      : `/agents/api/chatdata/evaluations/accuracy/runs/${runId}`,
    method: 'get',
  })
}

/** 获取专家技能列表 */
export function getSkills(params?: { search?: string; category?: string; scope?: 'all' | 'favorites' | 'mine' | 'system' | 'runtime' }): Promise<ApiResponse<{ skills: SkillInfo[]; total: number }>> {
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
export function getDatasources(params?: DatasourceListParams): Promise<ApiResponse<{ datasources: DataSource[] }>> {
  return request({
    url: DATASOURCE_API_PREFIX,
    method: 'get',
    params
  })
}

/** 获取后台数据源列表 */
export function getAdminDatasources(params?: DatasourceListParams): Promise<ApiResponse<{ datasources: DataSource[] }>> {
  return request({
    url: ADMIN_DATASOURCE_API_PREFIX,
    method: 'get',
    params
  })
}

/** 删除数据源 */
export function deleteAdminDatasource(id: string): Promise<ApiResponse<null>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${id}`,
    method: 'delete'
  })
}

/** 获取数据库表列表（用于同步前选择表） */
export function getAdminDbTables(data: DbTablesRequestPayload): Promise<ApiResponse<{ tables: string[] }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/db-tables`,
    method: 'post',
    data
  })
}

/** 设置默认数据源 */
export function setAdminDefaultDatasource(dsId: string): Promise<ApiResponse<null>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${dsId}/set-default`,
    method: 'post'
  })
}

/** 取消默认数据源 */
export function unsetAdminDefaultDatasource(dsId: string): Promise<ApiResponse<null>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${dsId}/set-default`,
    method: 'delete'
  })
}

/** 更新数据源元信息 */
export function updateAdminDatasource(
  datasourceId: string,
  data: DatasourceMetaUpdatePayload
): Promise<ApiResponse<DataSource>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}`,
    method: 'patch',
    data
  })
}

/** 更新数据源映射模块 */
export function updateAdminMappedModules(datasourceId: string, mappedModules: string[]): Promise<ApiResponse<null>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/mapped-modules`,
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

export function getAdminDatasourceTableGroups(datasourceId: string): Promise<ApiResponse<{ datasource_id: string; groups: DatasourceTableGroupItem[] }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/table-groups`,
    method: 'get'
  })
}

export function updateAdminDatasourceTableGroups(datasourceId: string, groups: DatasourceTableGroupItem[]): Promise<ApiResponse<{ datasource_id: string; groups: DatasourceTableGroupItem[] }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/table-groups`,
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
    url: `${DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns/${encodeURIComponent(columnName)}/dict`,
    method: 'get'
  })
}

export function getDatasourceTableColumnDicts(
  datasourceId: string,
  tableName: string
): Promise<ApiResponse<{ datasource_id: string; table_name: string; dicts: Record<string, DatasourceColumnDictPayload> }>> {
  return request({
    url: `${DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/column-dicts`,
    method: 'get'
  })
}

export function updateAdminDatasourceColumnDict(
  datasourceId: string,
  tableName: string,
  columnName: string,
  dict: DatasourceColumnDictPayload
): Promise<ApiResponse<{ datasource_id: string; table_name: string; column_name: string; dict: DatasourceColumnDictPayload }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns/${encodeURIComponent(columnName)}/dict`,
    method: 'put',
    data: { dict }
  })
}

export function generateAdminDatasourceColumnDict(
  datasourceId: string,
  tableName: string,
  columnName: string,
  text: string
): Promise<ApiResponse<{ datasource_id: string; table_name: string; column_name: string; dict: DatasourceColumnDictPayload }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns/${encodeURIComponent(columnName)}/dict/generate`,
    method: 'post',
    data: { text }
  })
}

export function updateAdminDatasourceTableBusinessDesc(
  datasourceId: string,
  tableName: string,
  businessDesc: string
): Promise<ApiResponse<{ datasource_id: string; table: DataSource['ds_tables'][number] }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/business-desc`,
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

export function getAdminDatasourceSemanticHealth(datasourceId: string): Promise<ApiResponse<DatasourceSemanticHealthResult>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-health`,
    method: 'get'
  })
}

/** 创建数据源组 */
export function createAdminDatasourceGroup(data: DatasourceGroupPayload): Promise<ApiResponse<DataSource>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/groups`,
    method: 'post',
    data
  })
}

/** 更新数据源组 */
export function updateAdminDatasourceGroup(datasourceId: string, data: DatasourceGroupPayload): Promise<ApiResponse<DataSource>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/group-config`,
    method: 'put',
    data
  })
}

/** 同步数据源组模板元数据 */
export function syncAdminDatasourceMetadata(
  datasourceId: string,
  data: DatasourceGroupMetadataSyncPayload
): Promise<ApiResponse<{ datasource: DataSource; table_count: number }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/metadata-sync`,
    method: 'post',
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
    url: '/agents/api/chatdata/conversations',
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
    url: '/agents/api/chatdata/conversations',
    method: 'post',
    data
  })
}

export interface ConversationWorkspaceFile {
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

/** 获取会话工作区文件。 */
export function getConversationWorkspaceFiles(conversationId: string): Promise<ApiResponse<{ files: ConversationWorkspaceFile[] }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files`,
    method: 'get'
  })
}

/** 读取会话工作区文本文件。 */
export function getConversationWorkspaceFileContent(conversationId: string, path: string): Promise<ApiResponse<{ path: string; relative_path: string; content: string; size: number }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files/content`,
    method: 'get',
    params: { path }
  })
}

/** 读取工作区原始字节文件（PDF/Word 等二进制预览，返回 Blob） */
export function getConversationWorkspaceRawFile(conversationId: string, path: string): Promise<Blob> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files/raw`,
    method: 'get',
    params: { path },
    responseType: 'blob',
  }) as unknown as Promise<Blob>
}

export interface ConversationWorkspaceOfficeHtmlPreview {
  available: boolean
  reason?: string
  renderer?: 'officecli-html'
  html?: string
  truncated?: boolean
}

/** 获取沙箱 OfficeCLI 生成的 Word HTML 预览；不可用时由调用方回退本地渲染。 */
export function getConversationWorkspaceOfficeHtmlPreview(
  conversationId: string,
  path: string,
): Promise<ApiResponse<ConversationWorkspaceOfficeHtmlPreview>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files/office-html`,
    method: 'get',
    params: { path },
  })
}

export function importConversationWorkspaceSkill(conversationId: string, data: { skill_dir_path: string }): Promise<ApiResponse<SkillInfo & { content?: string }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-skills/import`,
    method: 'post',
    data
  })
}

// ============ 文件上传 ============

export interface UploadedFileInfo {
  file_id: string
  original_filename: string
  file_type: string
  file_size: number
  // 上传后实际存储文件名（去重后可能与 original_filename 不同），用于拼装可访问路径
  storage_dir: string
}

/** 上传文件（落盘到会话工作区 uploads，解析由 agent 按需进行） */
export function uploadFile(conversationId: string, file: File): Promise<ApiResponse<UploadedFileInfo>> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('thread_id', conversationId)
  return request({
    url: '/agents/api/chatdata/uploaded-files/upload',
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

/** 修改会话标题（后端仅开放 title 子资源，标题会被截断到 20 字） */
export function updateConversationTitle(
  conversationId: string,
  title: string
): Promise<ApiResponse<{ id: string; title: string }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/title`,
    method: 'put',
    data: { title }
  })
}

/** 生成会话标题 */
export function generateConversationTitle(conversationId: string): Promise<ApiResponse<{ title: string }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/title`,
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

/** 获取当前用户可维护的个人语义配置列表 */
export function getMySemanticConfigs(datasourceId: string): Promise<ApiResponse<{ configs: SemanticConfigItem[] }>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/my-semantic-configs`,
    method: 'get'
  })
}

/** 后台获取可维护的公共语义配置列表 */
export function getAdminSemanticConfigs(datasourceId: string): Promise<ApiResponse<{ configs: SemanticConfigItem[] }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs`,
    method: 'get'
  })
}

/** 创建语义配置 */
export function createSemanticConfig(datasourceId: string, data: SemanticConfigCreatePayload): Promise<ApiResponse<SemanticConfigItem>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs`,
    method: 'post',
    data
  })
}

/** 后台创建公共语义配置 */
export function createAdminSemanticConfig(datasourceId: string, data: SemanticConfigCreatePayload): Promise<ApiResponse<SemanticConfigItem>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs`,
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

/** 后台更新公共语义配置 */
export function updateAdminSemanticConfig(datasourceId: string, configId: string, data: Partial<SemanticConfigItem>): Promise<ApiResponse<SemanticConfigItem>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/${configId}`,
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

/** 后台删除公共语义配置 */
export function deleteAdminSemanticConfig(datasourceId: string, configId: string): Promise<ApiResponse<null>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/${configId}`,
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

/** 后台切换公共语义配置启用状态 */
export function toggleAdminSemanticConfig(datasourceId: string, configId: string, enabled: boolean): Promise<ApiResponse<SemanticConfigItem>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/${configId}/toggle`,
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
  },
  options?: AdminModeApiOptions
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
    url: options?.adminMode ? `/agents/api/chatdata/admin/semantic/prefill` : `/agents/api/chatdata/semantic/prefill`,
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
  logical_type?: 'text' | 'number' | 'date' | 'datetime' | 'enum'
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
  },
  options?: AdminModeApiOptions
): Promise<ApiResponse<SemanticHistoryLearningResult>> {
  return request({
    url: options?.adminMode
      ? `/agents/api/chatdata/admin/datasources/${datasourceId}/semantic-configs/${configId}/history-learning`
      : `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/${configId}/history-learning`,
    method: 'post',
    data
  })
}

export function getConversationQueryResult<T = unknown>(conversationId: string, resultId: string): Promise<ApiResponse<T>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/query-results/${resultId}`,
    method: 'get',
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
  query_type?: 'sql' | 'builder'
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
  start_time?: string
  end_time?: string
}): Promise<ApiResponse<{ queries: SqlHistoryItem[]; total: number; page: number; page_size: number }>> {
  return request({
    url: '/agents/api/chatdata/query-history',
    method: 'get',
    params
  })
}

/** 获取全局 SQL 查询历史 */
export function getGlobalQueryHistory(params: {
  datasource_id?: string
  semantic_config_id?: string
  user_id?: string
  username?: string
  keyword?: string
  page?: number
  page_size?: number
  source?: 'all' | 'sql' | 'agent' | 'builder' | 'dashboard'
  status?: 'all' | 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timeout' | 'kill_failed'
  start_time?: string
  end_time?: string
}): Promise<ApiResponse<{ queries: SqlHistoryItem[]; total: number; page: number; page_size: number }>> {
  return request({
    url: '/agents/api/chatdata/admin/query-history',
    method: 'get',
    params
  })
}

/** 创建收藏查询 */
export function createQueryFavorite(data: {
  datasource_id: string
  query_type: 'sql' | 'builder'
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
  file_ids?: string[]
}, signal?: AbortSignal): Promise<Response> {
  return requestStream({
    url: `/agents/api/chatdata/conversations/${conversationId}/completion`,
    method: 'POST',
    data,
    signal
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
    url: `/agents/api/chatdata/conversations/${conversationId}`
  })
}

/** 获取历史会话绑定的数据源组。 */
export function getConversationContext(conversationId: string): Promise<ApiResponse<{ conversation_id: string; datasource_id?: string | null }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/context`,
    method: 'get',
  })
}
