/**
 * ChatData 模块类型定义
 */

import type { ChartType } from '../chartTypes'

// 表信息
export interface TableInfo {
  table_name: string
  description: string
  business_desc?: string
  columns: ColumnInfo[]
  groups?: Array<{
    table_group_name: string
  }>
}

// 列信息
export interface DatasourceColumnDictInfo {
  enum_values?: Array<{
    value: string
    label?: string
  }>
  synonyms?: string[]
  storage_format?: string
  unit?: string
}

export interface ColumnInfo {
  name: string
  type: string
  mysql_type?: string
  hive_type?: string
  description: string
  primary_key?: boolean
  nullable?: boolean
  column_dict?: DatasourceColumnDictInfo
}

// 数据源
export interface DataSource {
  id: string
  group_name: string
  ds_description: string
  query_context?: string
  recommended_questions?: string[]
  ds_tables: TableInfo[]
  is_default?: boolean
  source_type?: string
  query_executor_type?: 'direct' | 'http'
  source_config?: Record<string, unknown>
  create_time?: string
  update_time?: string
  create_by?: string
  update_by?: string
  create_by_name?: string
  update_by_name?: string
  mapped_modules?: string[]
  member_datasource_ids?: string[]
  member_sources?: Array<{
    member_datasource_id?: string
    org_name?: string
    org_code?: string
    source_type?: string
    query_executor_type?: 'direct' | 'http'
    host?: string
    port?: number
    database: string
  }>
  template_datasource_id?: string
}

export interface ExecutionTargetOption {
  member_datasource_id: string
  label: string
  org_code?: string
  org_name?: string
  source_type?: string
  host?: string
  port?: number
  database?: string
}

export interface SkillInfo {
  skill_id?: string
  name: string
  title?: string
  description?: string
  domain?: string
  category?: string
  skill_scope?: 'query' | 'analyze' | 'all'
  output_type?: string
  input_schema?: Record<string, unknown>
  source?: 'system' | 'user'
  visibility_type?: 'private' | 'global'
  publish_status?: 'draft' | 'published' | 'disabled'
  current_version_no?: number
  draft_version_no?: number
  has_draft?: boolean
  owner_user_id?: string
  creator_id?: string
  creator_name?: string
  can_manage?: boolean
  favorite?: boolean
  enabled?: boolean
  create_time?: string
  update_time?: string
  references?: string[]
  scripts?: string[]
  assets?: string[]
}

export interface SkillVersionInfo {
  skill_id: string
  skill_version_no: number
  version_publish_status: 'draft' | 'published' | 'disabled'
  current?: boolean
  content?: string
  create_by?: string
  create_by_name?: string
  update_by?: string
  update_by_name?: string
  create_time?: string
  update_time?: string
}

export interface ChatDataModelOption {
  id: string
  label: string
  value: string
  is_default?: boolean
}

// 表选择信息
export interface TableSelectionInfo {
  selected_tables: string[]
  reasoning: string
}

// NL2SQL 元数据
export interface NL2SQLMetadata {
  table_selection: TableSelectionInfo
  table_schemas: Record<string, TableInfo>
}

// NL2SQL 结果
export interface NL2SQLResult {
  sql: string
  tables_used: string[]
  explanation: string
  estimated_complexity: 'simple' | 'medium' | 'complex' | 'unknown'
  metadata?: NL2SQLMetadata
  is_qa?: boolean
}

// NL2SQL 请求
export interface NL2SQLRequest {
  query: string
  return_metadata: boolean
  datasource_id?: string | null
  conversation_id?: string | null
  enable_thinking?: boolean
}

// API 响应
export interface ApiResponse<T> {
  status: 'success' | 'error'
  msg: string
  code: number
  data: T
}

// LangGraph 消息
export interface ChatDataTokenUsage {
  used: number
  total: number
  percentage?: number
  remaining?: number
  input_tokens?: number
  current_input_tokens?: number
  max_input_tokens?: number
  cumulative_input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  context_window?: number
  estimated?: boolean
  llm_calls?: Array<{
    index: number
    model?: string | null
    input_tokens: number
    message_tokens?: number
    tool_schema_tokens?: number
    overhead_tokens?: number
    output_tokens?: number
    actual_input_tokens?: number
    actual_total_tokens?: number
    total_tokens?: number
    estimated?: boolean
    message_count?: number
    breakdown?: ChatDataTokenUsageBreakdown[]
  }>
  breakdown?: ChatDataTokenUsageBreakdown[]
}

export interface ChatDataMessageEndPayload {
  usage?: ChatDataTokenUsage | null
  timestamp?: string
}

export interface ChatDataTokenUsageBreakdown {
  key: string
  label: string
  tokens: number
  estimated_tokens?: number
  role?: string
  round?: number
  message_index?: number
  estimated?: boolean
  content?: string
  description?: string
  children?: ChatDataTokenUsageBreakdown[]
}

export interface LangGraphMessage {
  id?: string
  type: string
  content?: string
  additional_kwargs?: Record<string, unknown>
  response_metadata?: Record<string, unknown>
  status?: ToolCallInfo['status']
  tool_calls?: Array<{
    id: string
    name: string
    args: Record<string, unknown>
  }>
  tool_call_id?: string
  tool_name?: string
  // 后端在流式模式下累积的完整工具参数
  accumulated_args?: Record<string, unknown>
  timestamp?: string
}

export interface TaskStageInfo {
  stage: 'thinking' | 'plan' | 'execute' | 'done'
  status: 'running' | 'completed' | 'error'
  title?: string
  content?: string
  steps?: string[]
  source?: 'reasoning' | 'task_stage'
}

export interface ThinkingSegment {
  id: string
  content: string
  toolCallCount: number
}

export interface SemanticConfigSelectionInfo {
  semantic_config_id: string
  semantic_config_name?: string
  confidence?: 'high' | 'medium' | 'low' | 'none' | string
  reason?: string
  matched_metrics?: string[]
  matched_dimensions?: string[]
  auto_selected?: boolean
  user_selected?: boolean
}

// 会话项
export interface ConversationItem {
  id: string
  title: string
  created_at: string
  updated_at?: string
  agent_id?: string
}

// 消息
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  timestamp?: Date
  result?: unknown
  toolCalls?: ToolCallInfo[]
  thinkingSegments?: ThinkingSegment[]
  segments?: MessageSegment[]
  error?: string
  isStreaming?: boolean
  dashboardSpec?: DashboardSpecPayload
  taskStages?: TaskStageInfo[]
  semanticConfigSelection?: SemanticConfigSelectionInfo
  skill?: Pick<SkillInfo, 'name' | 'title' | 'description'>
  // tool 消息专用字段
  toolCallId?: string
  toolName?: string
}

export interface DashboardSpecPayload {
  dashboard_spec: Record<string, unknown>
  validation_warnings?: string[]
  writable_card_count?: number
  datasource_id?: string | null
  semantic_config_id?: string | null
  conversation_id?: string | null
}

// 语义参数（从 dsl2sql_tool 提取）
export interface SemanticParams {
  metrics?: string[]
  // dimensions 可以是字符串或对象（带 time_grain）
  dimensions?: Array<string | { name: string; time_grain?: string; [key: string]: unknown }>
  // filters 格式：{ dimension, operator, value } 或树形结构 { and: [...] } / { or: [...] }
  filters?: Array<{
    dimension?: string
    operator?: string
    value?: unknown
    and?: unknown[]
    or?: unknown[]
    [key: string]: unknown
  }>
  having?: Array<{ metric: string; operator: string; value: unknown }>
  order_by?: Array<{ field: string; direction: string }>
  limit?: number
  offset?: number
}

// 图表推荐配置
export interface VisualizationConfig {
  type: ChartType
  x_field?: string
  y_field?: string
}

// 工具调用信息
export interface ToolCallInfo {
  tool_call_id: string
  tool_name: string
  args: Record<string, unknown>
  result?: string
  status: 'generating' | 'running' | 'completed' | 'error'
  // 关联的渲染内容
  renderId?: string
  renderType?: 'queryResult' | 'chart'
  // 图表推荐
  visualization?: VisualizationConfig
  // 语义参数（从关联的 dsl2sql_tool 获取）
  semanticParams?: SemanticParams
}

// 消息内容片段（用于按顺序渲染文本和工具调用）
export type MessageSegment =
  | { type: 'text'; content: string }
  | { type: 'toolCall'; toolCallId: string }
  | { type: 'taskStage'; stageIndex: number }

// 查询结果
export interface QueryResult {
  success: boolean
  sql?: string
  columns?: string[]
  data?: Record<string, unknown>[]
  total_count?: number
  execution_time_ms?: number
  error?: string
}

export interface MemberQueryResult {
  member_datasource_id?: string
  member_name?: string
  success: boolean
  status?: string | null
  sql?: string
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  execution_time_ms?: number
  started_time?: string | null
  finished_time?: string | null
  error?: string
  cache_hit?: boolean
  cache_age_seconds?: number | null
  cache_expires_in_seconds?: number | null
}

export interface SqlHistoryItem {
  id: number | string
  execution_id?: string
  task_id?: string
  datasource_id?: string
  member_datasource_id?: string
  member_name?: string
  title?: string
  query: string
  query_type?: string
  task_status?: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'partial_success'
  sql_status?: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timeout' | 'kill_failed'
  last_used?: string | null
  executed_at?: string | null
  started_time?: string | null
  finished_time?: string | null
  execution_time_ms?: number | null
  result_row_count?: number | null
  has_result?: boolean
  success_flag?: number
  error_message?: string | null
  user_id?: string | null
  operator_name?: string | null
  source?: string
  panel_execution_plan?: Record<string, {
    mode?: 'single' | 'multi'
    member_datasource_ids?: string[]
    primary_member_datasource_id?: string
  }> | null
}

// 看板
export interface Dashboard {
  id: string
  name: string
  description?: string
  user_id: string
  datasource_id?: string
  theme: 'light' | 'dark'
  background_id?: string
  is_public: boolean
  can_manage?: boolean
  panels?: Panel[]
  create_time: string
  update_time: string
}

// 面板
export interface Panel {
  id: string
  dashboard_id: string
  name: string
  sql: string
  chart_type: ChartType
  chart_config?: Record<string, unknown>
  layout: { i: string; x: number; y: number; w: number; h: number }
  refresh_interval?: number
  source_thread_id?: string
  datasource_id?: string
  create_time: string
  update_time: string
}

// 视图模式
export type ViewMode = 'conversation' | 'builder' | 'editor' | 'dashboard' | 'metricConfig'
export type ChatDataModeLabels = Record<Exclude<ViewMode, 'metricConfig'>, string> & Partial<Record<'metricConfig', string>>

export interface ChatDataSqlChangePayload {
  mode: 'editor' | 'builder'
  sql: string
  builderConfig?: Record<string, unknown>
}

// 对外嵌入配置（ChatDataDialog / ChatDataFloat 统一使用）
export interface ChatDataOptions {
  /** 调用方组件/模块名称，用于标识来源 */
  parentComponentName?: string
  /** 初始视图模式 */
  defaultMode?: ViewMode
  /** 默认打开的看板名称（仅 dashboard 模式生效，不传则默认第一个） */
  defaultDashboardName?: string
  /** 主模式自定义名称（指标集配置可选，不传使用默认名称） */
  modeLabels?: ChatDataModeLabels
  /** SQL 查询模式初始化 SQL */
  initialSql?: string
  /** 可视化查询初始化配置 */
  initialBuilderConfig?: Record<string, unknown>
  /** SQL 手动保存回调（SQL查询/可视化查询） */
  onSqlChange?: (payload: ChatDataSqlChangePayload) => void
  /** 隐藏指定模式（支持传模式 key 或当前展示名称） */
  hideTabs?: Array<ViewMode | string>
  /** 隐藏可视化查询中部“图表/分析”面板 */
  hideBuilderChartPanel?: boolean
}

// 侧边栏 Tab
export type SidebarTab = 'dashboards' | 'config' | 'history'

// SSE 事件类型
export type SSEEventType = 'message' | 'message_end' | 'token_usage' | 'thinking' | 'tool' | 'error' | 'done' | 'dashboard_spec' | 'task_stage' | 'task_stage_delta'

// 工具状态
export type ToolStatus = 'pending' | 'running' | 'completed'

// 工具事件数据
export interface ToolEventData {
  tool_name: string
  tool_call_id: string | null
  status: ToolStatus
  args?: Record<string, unknown>
  result?: string
  message?: string
  timestamp?: string
}

// 语义层配置
export interface SemanticMetric {
  name: string
  field_name?: string
  expression: string
  table: string
  table_alias?: string
  data_type?: string
  filter?: string
  description?: string
  metadata_supplement?: string
}

export interface SemanticDimension {
  name: string
  field_name?: string
  column: string
  expression?: string
  table: string
  table_alias?: string
  logical_type?: 'text' | 'number' | 'date' | 'datetime' | 'enum'
  description?: string
  metadata_supplement?: string
  mapping?: Record<string, string>
  allowed_operators?: string[]
  allowed_values?: string[]
}

export interface SemanticRelationship {
  left_table: string
  left_alias?: string
  right_table: string
  right_alias?: string
  left_column: string
  right_column: string
  join_conditions?: Array<{
    left_column: string
    right_column: string
  }>
  relationship_type: 'one_to_one' | 'one_to_many' | 'many_to_one'
  join_type: 'inner' | 'left' | 'right'
}

export interface SemanticConfigItem {
  id: string
  name: string
  description?: string
  ontology_rules?: string
  scope_type?: 'public' | 'private'
  can_manage?: boolean
  enabled: boolean
  metrics: SemanticMetric[]
  dimensions: SemanticDimension[]
  relationships: SemanticRelationship[]
  create_time?: string
  update_time?: string
  create_by?: string
  update_by?: string
  create_by_name?: string
  update_by_name?: string
}

// QueryBuilder 相关类型
export interface JoinConfig {
  id: string
  leftTable?: string
  leftAlias?: string
  rightTable: string
  rightAlias?: string
  leftColumn: string
  rightColumn: string
  joinType: 'LEFT' | 'RIGHT' | 'INNER' | 'FULL'
  joinConditions?: Array<{
    leftTable?: string
    leftAlias?: string
    leftColumn: string
    rightColumn: string
  }>
}

export type DateTransform = 'raw' | 'year' | 'quarter' | 'month' | 'week' | 'day' | 'weekday'

export interface DimensionItem {
  id: string
  table: string
  tableAlias?: string
  column: string
  alias?: string
  transform?: DateTransform
}

export interface AggregationItem {
  id: string
  func: 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN' | 'COUNT_DISTINCT'
  table: string
  tableAlias?: string
  column: string
  alias?: string
}

export interface FilterItem {
  id: string
  table: string
  tableAlias?: string
  column: string
  operator: string
  value: string
  logic: 'AND' | 'OR'
  rightTable?: string
  rightTableAlias?: string
  rightColumn?: string
}

export interface OrderByItem {
  id: string
  table: string
  tableAlias?: string
  column: string
  direction: 'ASC' | 'DESC'
}
