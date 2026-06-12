/**
 * ChatData 模块类型定义
 */

// 表信息
export interface TableInfo {
  table_name: string
  description: string
  columns: ColumnInfo[]
}

// 列信息
export interface ColumnInfo {
  name: string
  type: string
  mysql_type?: string
  hive_type?: string
  description: string
  primary_key?: boolean
  nullable?: boolean
}

// 数据源
export interface DataSource {
  id: string
  ds_name: string
  ds_description: string
  ds_tables: TableInfo[]
  is_default?: boolean
  source_type?: string
  query_executor_type?: 'direct' | 'http'
  source_config?: Record<string, unknown>
  create_time?: string
  update_time?: string
  mapped_modules?: string[]
  member_datasource_ids?: string[]
  member_sources?: Array<{
    member_datasource_id?: string
    datasource_name?: string
    namespace?: string
    source_type?: string
    query_executor_type?: 'direct' | 'http'
    host: string
    port: number
    database: string
  }>
  template_datasource_id?: string
}

export interface ExecutionTargetOption {
  member_datasource_id: string
  label: string
  datasource_name?: string
  source_type?: string
  host?: string
  port?: number
  database?: string
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
}

// API 响应
export interface ApiResponse<T> {
  status: 'success' | 'error'
  msg: string
  code: number
  data: T
}

// 待确认工具
export interface PendingTool {
  tool_call_id: string
  tool_name: string
  args: Record<string, unknown>
  message?: string
}

// LangGraph 消息
export interface LangGraphMessage {
  id?: string
  type: string
  content?: string
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

// 会话项
export interface ConversationItem {
  id: string
  title: string
  created_at: string
  updated_at?: string
}

// 消息
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string
  timestamp: Date
  result?: unknown
  toolCalls?: ToolCallInfo[]
  segments?: MessageSegment[]
  error?: string
  isStreaming?: boolean
  // tool 消息专用字段
  toolCallId?: string
  toolName?: string
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
}

// 图表推荐配置
export interface VisualizationConfig {
  type: 'table' | 'card' | 'bar' | 'line' | 'pie'
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
  sql?: string
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  execution_time_ms?: number
  error?: string
}

// 查询结果项（用于 QueryResultPanel）
export interface QueryResultItem {
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  sql?: string
  error?: string
  executionTime?: number
  title?: string
  memberDatasourceId?: string
  memberName?: string
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
  panel_count?: number
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
  chart_type: 'table' | 'bar' | 'line' | 'pie' | 'kpi'
  chart_config?: Record<string, unknown>
  layout: { i: string; x: number; y: number; w: number; h: number }
  refresh_interval?: number
  source_thread_id?: string
  datasource_id?: string
  create_time: string
  update_time: string
}

// 视图模式
export type ViewMode = 'conversation' | 'builder' | 'editor' | 'dashboard'
export type ChatDataModeLabels = Record<ViewMode, string>

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
  /** 四个主模式自定义名称（需一次性传全） */
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
export type SSEEventType = 'message' | 'tool' | 'error' | 'done'

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
  data_type?: string
  filter?: string
  description?: string
}

export interface SemanticDimension {
  name: string
  field_name?: string
  column: string
  expression?: string
  table: string
  type: 'categorical' | 'time' | 'number'
  description?: string
  mapping?: Record<string, string>
  allowed_operators?: string[]
  allowed_values?: string[]
}

export interface SemanticRelationship {
  left_table: string
  right_table: string
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
  enabled: boolean
  metrics: SemanticMetric[]
  dimensions: SemanticDimension[]
  relationships: SemanticRelationship[]
  create_time?: string
  update_time?: string
}

// QueryBuilder 相关类型
export interface JoinConfig {
  id: string
  leftTable?: string
  rightTable: string
  leftColumn: string
  rightColumn: string
  joinType: 'LEFT' | 'RIGHT' | 'INNER' | 'FULL'
}

export type DateTransform = 'raw' | 'year' | 'quarter' | 'month' | 'week' | 'day' | 'weekday'

export interface DimensionItem {
  id: string
  table: string
  column: string
  alias?: string
  transform?: DateTransform
}

export interface AggregationItem {
  id: string
  func: 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN' | 'COUNT_DISTINCT'
  table: string
  column: string
  alias?: string
}

export interface FilterItem {
  id: string
  table: string
  column: string
  operator: string
  value: string
  logic: 'AND' | 'OR'
}

export interface OrderByItem {
  id: string
  table: string
  column: string
  direction: 'ASC' | 'DESC'
}
