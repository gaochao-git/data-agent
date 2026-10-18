/**
 * ChatData API
 * 使用主项目统一的 request 封装
 * 服务前缀: /agents (通过 vite proxy 转发到 FastAPI)
 */

import request from '@/utils/request'
import requestStream from '@/utils/requestStream'
import { parseSseChunk } from '../utils/sse'
import type {
  ApiResponse,
  DataSource,
  ConversationItem,
  ConversationGroupPage,
  SemanticConfigItem,
  SemanticConfigValidateResult,
  SemanticMetric,
  SemanticDimension,
  SemanticRelationship,
  ChatDataModelOption,
  SkillInfo,
  SkillVersionInfo,
  SqlHistoryItem,
  DashboardSpecPayload,
  DatasourceTableIndexItem,
  PendingUserQuestion,
  UserQuestionAnswer,
  AgentScheduleItem,
  AgentScheduleOverview,
  AgentSchedulePayload,
  AgentScheduleRunItem,
  PluginInfo,
  ChatDataSettingItem,
  MetaGlobalEnum,
  MetaGlobalEnumRef,
  MetaGlobalEnumValueItem,
} from '../types'
import type { Dashboard } from './dashboard'

export * from './query'

// 看板 API 统一复用 ./dashboard 实现，避免重复维护
export {
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
    // 分帧与字段解析复用共享 parseSseChunk;本接口只消费 data 字段,
    // 事件类型在 data JSON 的 event 字段里,与 SSE event: 行无关。
    const parsed = parseSseChunk(buffer)
    buffer = parsed.rest
    for (const { data: dataStr } of parsed.events) {
      if (!dataStr) continue
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(dataStr) as Record<string, unknown>
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

// 平台技能固定目录只有 references 与 assets；scripts（可执行脚本）因安全顾虑已整体移除
export type SkillFileDirName = 'references' | 'assets'

export function getSkillVersionFiles(skillId: string, versionNo: number): Promise<ApiResponse<{ references: string[]; assets: string[] }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files`,
    method: 'get'
  })
}

export function getSkillVersionFile(skillId: string, versionNo: number, params: { dir_name: SkillFileDirName; file_path: string }): Promise<ApiResponse<{ dir_name: string; file_path: string; content: string }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files/content`,
    method: 'get',
    params
  })
}

export function saveSkillVersionFile(skillId: string, versionNo: number, data: { dir_name: SkillFileDirName; file_path: string; content: string }): Promise<ApiResponse<{ saved: boolean; skill_version_no?: number }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files`,
    method: 'put',
    data
  })
}

export function deleteSkillVersionFile(skillId: string, versionNo: number, params: { dir_name: SkillFileDirName; file_path: string }): Promise<ApiResponse<{ deleted: boolean; skill_version_no?: number }>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files`,
    method: 'delete',
    params
  })
}

export function uploadSkillVersionFile(skillId: string, versionNo: number, data: { dir_name: SkillFileDirName; file: File }): Promise<ApiResponse<{ dir_name: string; file_path: string; skill_version_no?: number; uploaded: boolean }>> {
  const formData = new FormData()
  formData.append('dir_name', data.dir_name)
  formData.append('file', data.file)
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files/upload`,
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

/** 读取技能版本原始字节文件（图片/PDF 等二进制预览，返回 Blob）。 */
export function getSkillVersionRawFile(skillId: string, versionNo: number, params: { dir_name: SkillFileDirName; file_path: string }): Promise<Blob> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files/raw`,
    method: 'get',
    params,
    responseType: 'blob'
  }) as unknown as Promise<Blob>
}

export interface SkillOfficeHtmlPreview {
  available: boolean
  reason?: string
  renderer?: 'officecli-html'
  html?: string
  truncated?: boolean
}

/** 获取技能版本 Office 文件的服务端 HTML 预览（复用主对话 OfficeCLI 引擎）。 */
export function getSkillVersionOfficeHtmlPreview(skillId: string, versionNo: number, params: { dir_name: SkillFileDirName; file_path: string }): Promise<ApiResponse<SkillOfficeHtmlPreview>> {
  return request({
    url: `/agents/api/chatdata/skills/${skillId}/versions/${versionNo}/files/office-preview`,
    method: 'get',
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
export function getSkill(skillName: string): Promise<ApiResponse<SkillInfo & { content?: string; references?: string[] }>> {
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
  // 引用全局枚举名；非空时 enum_values 必须为空（二选一），缺省为本地枚举
  enum_ref?: string
  enum_values: DatasourceColumnDictEnumItem[]
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

export function getAdminDatasourceTableIndexes(
  datasourceId: string,
  tableName: string,
): Promise<ApiResponse<{ datasource_id: string; table_name: string; indexes: DatasourceTableIndexItem[] }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/indexes`,
    method: 'get',
  })
}

export function updateAdminDatasourceTableIndexes(
  datasourceId: string,
  tableName: string,
  indexes: DatasourceTableIndexItem[],
): Promise<ApiResponse<{ datasource_id: string; table_name: string; indexes: DatasourceTableIndexItem[] }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/indexes`,
    method: 'put',
    data: { indexes },
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

export function getAdminDatasourceColumnDict(
  datasourceId: string,
  tableName: string,
  columnName: string
): Promise<ApiResponse<{ datasource_id: string; table_name: string; column_name: string; dict: DatasourceColumnDictPayload }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/columns/${encodeURIComponent(columnName)}/dict`,
    method: 'get'
  })
}

export function getAdminDatasourceTableColumnDicts(
  datasourceId: string,
  tableName: string
): Promise<ApiResponse<{ datasource_id: string; table_name: string; dicts: Record<string, DatasourceColumnDictPayload> }>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/tables/${encodeURIComponent(tableName)}/column-dicts`,
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
    data: { text },
    timeout: 120000, // LLM 生成，超过全局 30s
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
    data,
    timeout: 120000, // 回源拉全量表结构，慢数据源可能超全局 30s
  })
}

// ============ 会话 API ============

/** 获取会话列表 */
export function getConversations(
  agentId: string,
  pagination?: {
    page?: number
    page_size?: number
    view?: 'flat' | 'grouped'
    group_key?: string
    cursor?: string
  }
): Promise<ApiResponse<ConversationItem[] | {
  conversations?: ConversationItem[]
  groups?: ConversationGroupPage[]
  page?: number
  page_size?: number
  has_more?: boolean
}>> {
  return request({
    url: '/agents/api/chatdata/conversations',
    method: 'get',
    params: { agent_id: agentId, ...pagination }
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

/**
 * 把前端内部使用的相对路径 key（如 workspace/a.md）转成后端唯一接受的
 * 规范形式 /mnt/user-data/workspace/a.md。规范形式直接透传。
 */
export function toCanonicalWorkspacePath(path: string): string {
  const value = String(path || '').trim().replace(/\\/g, '/')
  if (!value || value.startsWith('/mnt/user-data/') || value.startsWith('/mnt/skills/')) return value
  return `/mnt/user-data/${value.replace(/^\/+/, '')}`
}

/** 删除会话工作区中的单个文件。SQL 查询结果需留存审计，后端会拒绝并返回业务错误。 */
export function deleteConversationWorkspaceFile(
  conversationId: string,
  path: string,
): Promise<ApiResponse<{ path: string; type: string; record_cleaned: boolean }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files`,
    method: 'delete',
    params: { path: toCanonicalWorkspacePath(path) }
  })
}

/** 读取会话工作区文本文件。 */
export function getConversationWorkspaceFileContent(conversationId: string, path: string, refreshToken?: string): Promise<ApiResponse<{ path: string; relative_path: string; content: string; size: number }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files/content`,
    method: 'get',
    params: refreshToken ? { path: toCanonicalWorkspacePath(path), _refresh: refreshToken } : { path: toCanonicalWorkspacePath(path) }
  })
}

/** 读取工作区原始字节文件（PDF/Word 等二进制预览，返回 Blob） */
export function getConversationWorkspaceRawFile(conversationId: string, path: string, refreshToken?: string): Promise<Blob> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files/raw`,
    method: 'get',
    params: refreshToken ? { path: toCanonicalWorkspacePath(path), _refresh: refreshToken } : { path: toCanonicalWorkspacePath(path) },
    responseType: 'blob',
  }) as unknown as Promise<Blob>
}

/**
 * 打包下载会话工作区顶层目录（当前仅 outputs），返回 zip Blob。
 * 业务错误（超过压缩包大小上限 / 目录为空 / 无权访问）以 HTTP 200 + JSON 体返回，
 * 调用方需检查 blob.type 后自行解析 msg 提示。
 */
export function getConversationWorkspaceArchive(conversationId: string, directory: string): Promise<Blob> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files/archive`,
    method: 'get',
    params: { directory },
    responseType: 'blob',
    timeout: 120000,
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
  refreshToken?: string,
): Promise<ApiResponse<ConversationWorkspaceOfficeHtmlPreview>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-files/office-html`,
    method: 'get',
    params: refreshToken ? { path: toCanonicalWorkspacePath(path), _refresh: refreshToken } : { path: toCanonicalWorkspacePath(path) },
  })
}

export function importConversationWorkspaceSkill(conversationId: string, data: { skill_dir_path: string }): Promise<ApiResponse<SkillInfo & { content?: string }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/workspace-skills/import`,
    method: 'post',
    data: { skill_dir_path: toCanonicalWorkspacePath(data.skill_dir_path) },
    timeout: 120000, // 目录打包下载，超过全局 30s
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

/** 切换会话收藏状态（收藏的会话置顶显示） */
export function toggleConversationFavorite(
  conversationId: string
): Promise<ApiResponse<{ id: string; is_favorited: boolean }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/favorite`,
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

export interface GenerateBasicSemanticConfigsResult {
  created: number
  skipped: number
  failed: number
  items: Array<{
    table: string
    status: 'created' | 'skipped' | 'failed'
    semantic_config_id?: string
    reason?: string
  }>
}

/** 后台为尚未建模的物理表批量创建公共基础模型（默认启用） */
export function generateAdminBasicSemanticConfigs(datasourceId: string): Promise<ApiResponse<GenerateBasicSemanticConfigsResult>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/generate-basic`,
    method: 'post'
  })
}

// ============ Ossie 语义模型导入 API（仅管理端） ============

/** 校验清单单行：{group, name, status, message}，由后端从既有判定结果确定性映射 */
export interface OssieChecklistRow {
  /** 检查分组：schema（OSSI 校验）/ mapping（结构映射）/ physical（物理校验）/ quality（质量规则）/ dict（数据字典仅填空） */
  group: 'schema' | 'mapping' | 'physical' | 'quality' | 'dict'
  name: string
  /** pass=通过 warn=警告（不阻断） fail=未通过（阻断导入） skip=跳过（未执行） */
  status: 'pass' | 'warn' | 'fail' | 'skip'
  message: string
}

/** 导入预览的质量检查结果：只透出触发的 findings，不输出评分 */
export interface OssieImportQualitySummary {
  findings: OssieQualityFinding[]
}

/** Ossie 导入预览：单个语义模型的映射草稿与问题清单 */
export interface OssieImportModelPreview {
  name: string
  description?: string
  business_notes?: string
  metrics: SemanticMetric[]
  dimensions: SemanticDimension[]
  relationships: SemanticRelationship[]
  /** 源文件的 CHAT_DATA 扩展里记录的配置 ID（round-trip 提示用，不用于更新） */
  source_config_id?: string
  /** 阻断性问题：存在任一条则该模型不可导入 */
  errors: string[]
  /** 非阻断提示：推断、快照角色等，人工确认时参考 */
  warnings: string[]
  importable: boolean
  /** 校验清单：四组检查项逐行状态（schema / mapping / physical / quality） */
  checklist?: OssieChecklistRow[]
  /** 质量检查触发的规则明细：仅可导入的模型附带 */
  quality?: OssieImportQualitySummary
  /** 数据源组内同名活跃配置摘要：存在时导入需选择 replace/skip */
  existing_config?: OssieExistingConfig | null
  /** 存在同名但当前不可操作的配置（如他人私有配置）：不允许 replace，导入时强制跳过；
   *  该配置的元信息（config_id 等）不返回，故不提供替换/跳过选择 */
  conflict_locked?: boolean
}

/** 同名冲突的已有配置摘要 */
export interface OssieExistingConfig {
  config_id: string
  name: string
  enabled: boolean
}

/** 语义质量检查：单条聚合问题 */
export interface OssieQualityFinding {
  code: string
  severity: 'error' | 'warning' | 'info'
  category: string
  message: string
  targets: string[]
}

/** 语义质量检查：单配置报告（与后端 semantic_quality_checker 同口径） */
export interface OssieQualityReport {
  score: number
  grade: string
  stats: Record<string, number>
  findings: OssieQualityFinding[]
}

export interface OssieImportPreviewData {
  models: OssieImportModelPreview[]
}

export interface OssieImportCreatedItem {
  config_id: string
  name: string
}

export interface OssieImportData {
  created: OssieImportCreatedItem[]
  /** 替换的同名配置：保留原 config_id 全量覆盖内容，启用状态保持不变 */
  replaced?: OssieImportCreatedItem[]
  /** 因同名冲突被跳过的模型：无操作权限的同名配置（他人私有）不带 config_id，故为可选 */
  skipped?: Array<{ name: string; config_id?: string; reason: string }>
  models?: OssieImportModelPreview[]
}

/** 解析 Ossie 语义模型 YAML，预览导入草稿与问题清单（不落库） */
export function previewAdminOssieSemanticImport(
  datasourceId: string,
  yamlContent: string
): Promise<ApiResponse<OssieImportPreviewData>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/ossie-import/preview`,
    method: 'post',
    data: { yaml_content: yamlContent },
    timeout: 120000, // 大 YAML 解析 + 质量检查，超过全局 30s
  })
}

/**
 * 导入 Ossie 语义模型：逐模型创建公共配置（全有或全无），新模型默认启用。
 * conflictActions：模型名 → replace（覆盖同名配置内容，启用状态保持不变）/ skip（跳过）；
 * 同名冲突缺省按 skip 处理。
 */
export function importAdminOssieSemanticConfigs(
  datasourceId: string,
  yamlContent: string,
  conflictActions: Record<string, 'replace' | 'skip'> = {}
): Promise<ApiResponse<OssieImportData>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/ossie-import`,
    method: 'post',
    data: { yaml_content: yamlContent, conflict_actions: conflictActions },
    timeout: 120000, // 导入落库 + 跨模型校验，超过全局 30s
  })
}

// ============ 语义质量检查 API（仅管理端） ============

/** 语义质量检查：数据源级汇总 */
export interface SemanticQualityOverall {
  config_count: number
  avg_score: number
  grade: string
  table_total: number
  table_modeled: number
  unmodeled_tables: string[]
  unmodeled_table_count: number
  inventory_available: boolean
}

/** 语义质量检查：单配置报告（含配置标识） */
export interface SemanticQualityConfigReport extends OssieQualityReport {
  config_id: string
  config_name: string
  enabled: boolean
}

export interface SemanticQualityCheckData {
  configs: SemanticQualityConfigReport[]
  overall: SemanticQualityOverall
}

/** 对数据源下全部语义配置执行质量检查（评分 + 问题清单 + 汇总） */
export function checkAdminSemanticConfigQuality(datasourceId: string): Promise<ApiResponse<SemanticQualityCheckData>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/quality-check`,
    method: 'get'
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

/** 语义配置保存前校验（dry-run，不落库）：结构校验 + 质量/跨模型口径提示 */
export function validateSemanticConfig(
  datasourceId: string,
  data: Partial<SemanticConfigItem>,
  excludeConfigId?: string
): Promise<ApiResponse<SemanticConfigValidateResult>> {
  return request({
    url: `/agents/api/chatdata/datasources/${datasourceId}/semantic-configs/validate`,
    method: 'post',
    params: excludeConfigId ? { exclude_config_id: excludeConfigId } : undefined,
    data,
    timeout: 120000, // 物理字段校验实时回源拉全量表结构，超过全局 30s
  })
}

/** 后台公共语义配置保存前校验（dry-run，不落库） */
export function validateAdminSemanticConfig(
  datasourceId: string,
  data: Partial<SemanticConfigItem>,
  excludeConfigId?: string
): Promise<ApiResponse<SemanticConfigValidateResult>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/validate`,
    method: 'post',
    params: excludeConfigId ? { exclude_config_id: excludeConfigId } : undefined,
    data,
    timeout: 120000, // 物理字段校验实时回源拉全量表结构，超过全局 30s
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

/** 批量切换启用状态结果：成功 / 无权限跳过 / 不存在 的 ID 明细 */
export interface SemanticConfigBatchToggleResult {
  updated_ids: string[]
  skipped_ids: string[]
  not_found_ids: string[]
  updated_count: number
}

/** 批量切换个人语义配置启用状态 */
export function batchToggleSemanticConfig(datasourceId: string, configIds: string[], enabled: boolean): Promise<ApiResponse<SemanticConfigBatchToggleResult>> {
  return request({
    url: `${DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/batch-toggle`,
    method: 'patch',
    data: { config_ids: configIds, enabled }
  })
}

/** 后台批量切换公共语义配置启用状态 */
export function batchToggleAdminSemanticConfig(datasourceId: string, configIds: string[], enabled: boolean): Promise<ApiResponse<SemanticConfigBatchToggleResult>> {
  return request({
    url: `${ADMIN_DATASOURCE_API_PREFIX}/${datasourceId}/semantic-configs/batch-toggle`,
    method: 'patch',
    data: { config_ids: configIds, enabled }
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
  queued_message_id?: string
  preserve_queued_messages?: boolean
  /** 用户点击发送的客户端时刻（epoch 毫秒），服务端作 harness 计时起点 */
  client_timestamp_ms?: number
}, signal?: AbortSignal): Promise<Response> {
  return requestStream({
    url: `/agents/api/chatdata/conversations/${conversationId}/completion`,
    method: 'POST',
    data,
    signal
  })
}

/** 向运行中的智能体追加纯文本消息。 */
export function steerConversation(
  conversationId: string,
  query: string,
  queueType?: 'next-step' | 'next-turn',
  meta?: { file_selections?: string[]; file_ids?: string[] },
): Promise<ApiResponse<{ message_id: string }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/steer`,
    method: 'post',
    data: {
      query,
      ...(queueType ? { queue_type: queueType } : {}),
      // 随行引用/附件快照：服务端存引用，续跑执行时统一校验解析
      ...(meta?.file_selections?.length ? { file_selections: meta.file_selections } : {}),
      ...(meta?.file_ids?.length ? { file_ids: meta.file_ids } : {}),
    },
  })
}

/** 显式停止当前 Agent 运行，未消费的队列消息由下一次 completion 接管。 */
export function cancelConversationRun(
  conversationId: string,
  runId: string,
): Promise<ApiResponse<{ cancelled: boolean }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/run`,
    method: 'delete',
    params: { run_id: runId },
  })
}

/** 编辑排队中的追加消息内容或切换队列类型。 */
export function updateSteerMessage(
  conversationId: string,
  messageId: string,
  payload: { content?: string; queueType?: 'next-step' | 'next-turn' },
): Promise<ApiResponse<{ message_id: string; content: string; queue_type: 'next-step' | 'next-turn' }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/steer/${messageId}`,
    method: 'put',
    data: {
      ...(payload.content !== undefined ? { content: payload.content } : {}),
      ...(payload.queueType ? { queue_type: payload.queueType } : {}),
    },
  })
}

/** 删除仍在排队等待注入的追加消息。 */
export function cancelSteerMessage(
  conversationId: string,
  messageId: string,
): Promise<ApiResponse<{ message_id: string }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/steer/${messageId}`,
    method: 'delete',
  })
}

/** 读取仍排队的追加消息与运行状态，供页面刷新后恢复展示。 */
export function listSteerMessages(
  conversationId: string,
): Promise<ApiResponse<{
  has_active_run: boolean
  run_id?: string | null
  run_status?: 'pending' | 'active' | 'cancelling' | 'waiting_user' | 'finished' | 'failed' | 'cancelled' | null
  next_run_id?: string | null
  turn_id?: string | null
  is_continuation?: boolean
  history_checkpoint?: {
    run_id: string
    turn_id?: string | null
    history_sequence_no?: number
    stream_id: string
  } | null
  messages: Array<{
    message_id: string
    content: string
    queue_type: 'next-step' | 'next-turn'
    status: 'pending' | 'claimed' | 'consumed' | 'cancelled'
    create_time: string
  }>
}>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/steer`,
    method: 'get',
  })
}

export function getPendingUserQuestion(
  conversationId: string,
): Promise<ApiResponse<PendingUserQuestion | null>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/pending-question`,
    method: 'get',
  })
}

/** 订阅后台 Run 的持久化 SSE 事件，支持按序号断线续传。 */
export function streamRunEvents(
  conversationId: string,
  runId: string,
  lastEventId = '0-0',
  signal?: AbortSignal,
): Promise<Response> {
  return requestStream({
    url: `/agents/api/chatdata/conversations/${conversationId}/runs/${runId}/events`,
    method: 'get',
    headers: { 'Last-Event-ID': lastEventId },
    signal,
  })
}

export function answerUserQuestion(
  conversationId: string,
  questionId: string,
  answers: UserQuestionAnswer[],
  signal?: AbortSignal,
): Promise<Response> {
  return requestStream({
    url: `/agents/api/chatdata/conversations/${conversationId}/questions/${questionId}/answer`,
    method: 'POST',
    data: { answers },
    signal,
  })
}

export function cancelUserQuestion(
  conversationId: string,
  questionId: string,
  signal?: AbortSignal,
): Promise<Response> {
  return requestStream({
    url: `/agents/api/chatdata/conversations/${conversationId}/questions/${questionId}/cancel`,
    method: 'POST',
    signal,
  })
}

/** 获取会话历史（流式接口）。limit 指定时只取尾部窗口，用 beforeSequence 向上翻页。 */
export function getConversationHistory(
  conversationId: string,
  options?: { limit?: number; beforeSequence?: number },
): Promise<Response> {
  return requestStream({
    url: `/agents/api/chatdata/conversations/${conversationId}`,
    params: {
      view: 'timeline',
      limit: options?.limit,
      before_sequence: options?.beforeSequence,
    },
  })
}

/** 获取历史会话绑定的数据源组。 */
export function getConversationContext(conversationId: string): Promise<ApiResponse<{ conversation_id: string; datasource_id?: string | null }>> {
  return request({
    url: `/agents/api/chatdata/conversations/${conversationId}/context`,
    method: 'get',
  })
}

// ============ 智能体定时任务 API ============

/** 获取当前用户的定时任务分页列表 */
export function getAgentSchedules(params?: { limit?: number; offset?: number }): Promise<ApiResponse<{ jobs: AgentScheduleItem[]; total: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/agent-schedules`,
    method: 'get',
    params
  })
}

/** 创建定时任务 */
export function createAgentSchedule(data: AgentSchedulePayload): Promise<ApiResponse<AgentScheduleItem>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/agent-schedules`,
    method: 'post',
    data
  })
}

/** 更新定时任务（未传字段保持不变） */
export function updateAgentSchedule(jobId: string, data: Partial<AgentSchedulePayload> & { enabled_flag?: number }): Promise<ApiResponse<AgentScheduleItem>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/agent-schedules/${jobId}`,
    method: 'put',
    data
  })
}

/** 删除定时任务（软删除并停止调度） */
export function deleteAgentSchedule(jobId: string): Promise<ApiResponse<{ job_id: string }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/agent-schedules/${jobId}`,
    method: 'delete'
  })
}

/** 立即执行定时任务（手动触发一次，便于验证；会话正忙时转后台队列自动重试） */
export function triggerAgentScheduleNow(jobId: string): Promise<ApiResponse<{ trigger_id: number; status: string; run_id: string | null }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/agent-schedules/${jobId}/trigger-now`,
    method: 'post'
  })
}

/** 获取定时任务执行历史分页 */
export function getAgentScheduleRuns(jobId: string, params?: { limit?: number; offset?: number }): Promise<ApiResponse<{ runs: AgentScheduleRunItem[]; total: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/agent-schedules/${jobId}/runs`,
    method: 'get',
    params
  })
}

/** 后台：定时任务全局概览统计 */
export function getAgentScheduleAdminOverview(): Promise<ApiResponse<AgentScheduleOverview>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/agent-schedules/overview`,
    method: 'get'
  })
}

/** 后台：全局一键暂停/恢复所有定时任务调度 */
export function setAgentSchedulePaused(data: { paused: boolean; reason?: string }): Promise<ApiResponse<AgentScheduleOverview>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/agent-schedules/pause`,
    method: 'put',
    data
  })
}

/** 后台：全量定时任务分页列表（跨用户，附执行统计） */
export function getAdminAgentSchedules(params?: { keyword?: string; limit?: number; offset?: number; enabled?: boolean; expired?: boolean }): Promise<ApiResponse<{ jobs: AgentScheduleItem[]; total: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/agent-schedules`,
    method: 'get',
    params
  })
}

/** 后台：强制启停单个定时任务 */
export function setAdminAgentScheduleEnabled(jobId: string, enabledFlag: number): Promise<ApiResponse<AgentScheduleItem>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/agent-schedules/${jobId}/enabled`,
    method: 'put',
    data: { enabled_flag: enabledFlag }
  })
}

/** 后台：查看任意定时任务的执行历史分页 */
export function getAdminAgentScheduleRuns(jobId: string, params?: { limit?: number; offset?: number }): Promise<ApiResponse<{ runs: AgentScheduleRunItem[]; total: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/agent-schedules/${jobId}/runs`,
    method: 'get',
    params
  })
}

// ============ 插件管理 API（Admin） ============

/** 后台：已安装插件列表（含禁用项） */
export function getAdminPlugins(params?: { limit?: number; offset?: number }): Promise<ApiResponse<{ plugins: PluginInfo[]; total: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/plugins`,
    method: 'get',
    params
  })
}

/** 后台：配置变量列表（= 可动态修改变量白名单全量，含生效值与来源徽标） */
export function getAdminSettings(params?: { keyword?: string }): Promise<ApiResponse<{ items: ChatDataSettingItem[]; total: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/settings`,
    method: 'get',
    params
  })
}

/** 后台：上传插件 zip 安装（同名插件需先卸载；变更对新会话生效） */
export function installPlugin(file: File): Promise<ApiResponse<{ plugin: PluginInfo; effective: string }>> {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/plugins/install`,
    method: 'post',
    data: formData,
    headers: { 'Content-Type': 'multipart/form-data' }
  })
}

/** 后台：启用/禁用插件（立即影响新会话快照与新装配） */
export function setAdminPluginEnabled(pluginId: string, enabled: boolean): Promise<ApiResponse<{ name: string; enabled: boolean }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/plugins/${pluginId}/enabled`,
    method: 'put',
    data: { enabled }
  })
}

/** 后台：卸载插件并删除其目录 */
export function uninstallPlugin(pluginId: string): Promise<ApiResponse<{ name: string; version: string }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/plugins/${pluginId}`,
    method: 'delete'
  })
}

/** 后台：覆盖已注册变量（key 限白名单；已重置的 key 自动复用原行恢复） */
export function upsertAdminSetting(settingKey: string, data: { setting_value: string }): Promise<ApiResponse<{ setting_key: string; action: 'created' | 'updated' | 'restored' }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/settings/${settingKey}`,
    method: 'put',
    data
  })
}

/** 后台：重置单个变量（软删覆盖行，回退 env/代码默认值） */
export function deleteAdminSetting(settingKey: string): Promise<ApiResponse<{ setting_key: string }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/settings/${settingKey}`,
    method: 'delete'
  })
}

/** 后台：全部重置（软删所有覆盖行，所有变量回退 env/代码默认值） */
export function resetAllAdminSettings(): Promise<ApiResponse<{ reset_count: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/settings`,
    method: 'delete'
  })
}

// ============ 元数据管理 API（Admin：全局枚举） ============

/** 后台：全局枚举列表（keyword 按名称/说明模糊过滤；businessDomain 按域过滤，不传返回全部域） */
export function getAdminMetaEnums(params?: { keyword?: string; business_domain?: string }): Promise<ApiResponse<MetaGlobalEnum[]>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/meta/enums`,
    method: 'get',
    params
  })
}

/** 后台：已有全局枚举的业务域去重清单（供域选择器） */
export function getAdminMetaEnumDomains(): Promise<ApiResponse<{ domains: string[] }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/meta/enums/domains`,
    method: 'get'
  })
}

/** 后台：全局枚举详情（按业务域 + 枚举名定位） */
export function getAdminMetaEnum(enumName: string, businessDomain?: string): Promise<ApiResponse<MetaGlobalEnum>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/meta/enums/${encodeURIComponent(enumName)}`,
    method: 'get',
    params: businessDomain ? { business_domain: businessDomain } : undefined
  })
}

/** 后台：查询全局枚举的数据字典引用方（删除前确认引用范围） */
export function getAdminMetaEnumRefs(
  enumName: string,
  businessDomain?: string
): Promise<ApiResponse<{ business_domain: string; enum_name: string; refs: MetaGlobalEnumRef[]; ref_count: number }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/meta/enums/${encodeURIComponent(enumName)}/refs`,
    method: 'get',
    params: businessDomain ? { business_domain: businessDomain } : undefined
  })
}

/** 后台：按 (business_domain, enum_name) 创建/更新全局枚举（软删行自动复用恢复）。
 * allow_overwrite 仅编辑既有枚举时传 true；新建/存为模板保持 false，同域同名活跃枚举已存在时后端拒绝。 */
export function upsertAdminMetaEnum(
  enumName: string,
  data: { business_domain?: string; enum_desc: string; enum_items: MetaGlobalEnumValueItem[]; allow_overwrite?: boolean }
): Promise<ApiResponse<MetaGlobalEnum>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/meta/enums/${encodeURIComponent(enumName)}`,
    method: 'put',
    data
  })
}

/** 后台：删除全局枚举（软删；仍被引用时后端拒绝并返回引用位置） */
export function deleteAdminMetaEnum(enumName: string, businessDomain?: string): Promise<ApiResponse<{ business_domain: string; enum_name: string }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/meta/enums/${encodeURIComponent(enumName)}`,
    method: 'delete',
    params: businessDomain ? { business_domain: businessDomain } : undefined
  })
}

/** 后台：从文本提取全局枚举值候选（复用字段字典生成链路），不直接保存 */
export function generateAdminMetaEnumItems(text: string): Promise<ApiResponse<{ enum_items: MetaGlobalEnumValueItem[] }>> {
  return request({
    url: `${CHATDATA_API_PREFIX}/admin/meta/enums/generate`,
    method: 'post',
    data: { text },
    timeout: 120000 // LLM 生成，超过全局 30s
  })
}
