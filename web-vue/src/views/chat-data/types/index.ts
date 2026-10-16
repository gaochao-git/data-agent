/**
 * ChatData 模块类型定义
 */

import type { ChartType } from '../chartTypes'
export type { MemberQueryResult, QueryResult } from './query'

/** Ossie 语义模型 yaml 文件载荷（展示名 + 文本内容）：会话工作区「一键导入」与导入弹窗装载共用 */
export interface SemanticYamlFile {
  name: string
  content: string
}

// 表信息
export interface TableIndex {
  name: string
  columns: string[]
  source?: 'physical' | 'manual'
}

export type DatasourceTableIndexType = 'primary' | 'unique' | 'secondary'

export interface DatasourceTableIndexItem {
  index_type: DatasourceTableIndexType
  index_name: string
  columns: string[]
  source?: 'manual'
}

export interface TableInfo {
  table_name: string
  description: string
  business_desc?: string
  columns: ColumnInfo[]
  primary_keys?: string[][]
  unique_indexes?: TableIndex[]
  indexes?: TableIndex[]
  manual_indexes?: DatasourceTableIndexItem[]
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
  unique_key?: boolean
  index_key?: boolean
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
  output_type?: string
  input_schema?: Record<string, unknown>
  source?: 'system' | 'user' | 'plugin'
  visibility_type?: 'private' | 'global'
  publish_status?: 'draft' | 'published' | 'disabled'
  current_version_no?: number
  draft_version_no?: number
  has_draft?: boolean
  owner_user_id?: string
  creator_id?: string
  creator_name?: string
  create_by_name?: string
  can_manage?: boolean
  favorite?: boolean
  enabled?: boolean
  create_time?: string
  update_time?: string
  references?: string[]
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

/** 模型思考能力声明（来自服务端 models.json 注册表，经 /config 下发）。 */
export interface ChatDataModelThinking {
  mode: 'none' | 'optional' | 'forced'
  efforts: string[]
  default_on: boolean
  default_effort: string | null
}

export interface ChatDataModelOption {
  id: string
  label: string
  value: string
  is_default?: boolean
  thinking?: ChatDataModelThinking
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
  current_cached_input_tokens?: number
  cumulative_cached_input_tokens?: number
  current_cache_hit_rate?: number
  cumulative_cache_hit_rate?: number
  output_tokens?: number
  total_tokens?: number
  context_window?: number
  max_output_tokens?: number
  hard_input_limit_tokens?: number
  hard_input_guard_ratio?: number
  hard_input_guard_tokens?: number
  compression_trigger_tokens?: number
  compression_trigger_ratio?: number
  compression_target_tokens?: number
  estimated?: boolean
  context_compression?: ChatDataContextCompressionInfo | null
  breakdown?: ChatDataTokenUsageBreakdown[]
}

export interface ChatDataContextCompressionInfo {
  enabled?: boolean
  triggered?: boolean
  estimated_tokens_before?: number
  estimated_tokens_after?: number
  estimated_cleared_tokens?: number
  threshold_tokens?: number
  trigger_ratio?: number
  hard_input_limit_tokens?: number
  hard_input_guard_ratio?: number
  hard_input_guard_tokens?: number
  compression_target_tokens?: number
  static_input_tokens?: number
  compressed_messages?: number
  summary_chars?: number
  hard_limit_enforced?: boolean
  hard_limit_dropped_messages?: number
  hard_limit_summary_dropped?: boolean
  hard_limit_user_message_truncated?: boolean
  compression_skipped_reason?: string
  legacy_summary_migrated?: boolean
}

export interface ChatDataContextCompressionEvent {
  id: string
  phase?: 'started' | 'completed'
  compressing?: boolean
  turn_id?: string
  anchor_message_id?: string
  timestamp?: string
  estimated_tokens_before?: number
  estimated_tokens_after?: number
  estimated_cleared_tokens?: number
  compressed_messages?: number
}

export interface ChatDataMessageEndPayload {
  usage?: ChatDataTokenUsage | null
  timestamp?: string
  /** 后端服务端口径生成统计（snake_case 原始结构，经 toGenerationStats 校验后挂 message.generationStats） */
  generation_stats?: unknown
}

export interface ChatDataTokenUsageBreakdown {
  key: string
  label: string
  tokens: number
  estimated_tokens?: number
  role?: string
  round?: number
  message_index?: number
  /** 后端消息 id，前端凭它从已加载的会话消息中取正文（payload 不再携带 content） */
  message_id?: string | null
  /** ToolMessage 对应的 tool_call_id，用于在 assistant.toolCalls 中反查工具结果 */
  tool_call_id?: string | null
  /** 执行开始/结束时间（epoch 毫秒）：AI 行为模型调用、工具行为工具执行，其余行无值；耗时由两者相减得出 */
  started_at_ms?: number | null
  ended_at_ms?: number | null
  estimated?: boolean
  content?: string
  description?: string
  redacted?: boolean
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
    // 用户可见展示名（MCP 工具由后端下发；内置工具为空串）
    display_name?: string
    args: Record<string, unknown>
  }>
  tool_call_id?: string
  tool_name?: string
  // 用户可见展示名（MCP 工具由后端下发；内置工具为空串）
  display_name?: string
  // 后端在流式模式下累积的完整工具参数
  accumulated_args?: Record<string, unknown>
  timestamp?: string
  // 工具消息的执行起止时间（epoch 毫秒，build_tool_message_payload 提升为顶层字段）
  started_at_ms?: number | null
  ended_at_ms?: number | null
}

export interface ThinkingSegment {
  id: string
  content: string
  toolCallCount: number
  reasoningTurnId?: string
}

// 会话项
export interface ConversationItem {
  id: string
  title: string
  create_time: string
  updated_at?: string
  agent_id?: string
  datasource_id?: string
  is_favorited?: boolean
  run_status?: 'pending' | 'active' | 'cancelling' | 'waiting_user' | 'finished' | 'failed' | 'cancelled' | null
}

export interface ConversationGroupPage {
  key: string
  datasource_id?: string | null
  conversations: ConversationItem[]
  page: number
  page_size: number
  has_more: boolean
  next_cursor?: string | null
}

export interface ConversationGroupView {
  key: string
  label: string
  items: ConversationItem[]
  page?: number
  has_more?: boolean
  next_cursor?: string | null
  is_loading_more?: boolean
}

// 消息
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'context_compression'
  content: string
  timestamp?: Date
  result?: unknown
  toolCalls?: ToolCallInfo[]
  thinkingSegments?: ThinkingSegment[]
  segments?: MessageSegment[]
  error?: string
  isStreaming?: boolean
  turnId?: string
  dashboardSpec?: DashboardSpecPayload
  /** 用户消息 @ 引用的会话工作区文件清单（相对路径，本地发送与历史回放共用）。 */
  fileSelections?: string[]
  skills?: Array<Pick<SkillInfo, 'name' | 'title' | 'description'>>
  // 用户消息附带的文件信息（上传文件或超长输入转文件）
  attachments?: MessageAttachment[]
  // tool 消息专用字段
  toolCallId?: string
  toolName?: string
  contextCompression?: ChatDataContextCompressionEvent
  // 助手气泡生成统计（首 token / 输出 tokens / 纯模型生成时长 / 速度）：
  // 后端 finish 时服务端口径计算，随 message_end 下发（本轮）与 AI 消息持久化（历史回放）
  generationStats?: MessageGenerationStats
}

// 助手气泡生成统计：分母 ≈ 模型流墙钟时间 − 工具执行时间（纯模型生成时间，服务端计算）
export interface MessageGenerationStats {
  /** 输出 token 总数（后端按模型返回的 usage 累加，非估算） */
  outputTokens: number
  /** 估算的纯模型生成时长（毫秒） */
  durationMs: number
  /** 生成速度（tokens/s） */
  tokensPerSecond: number
  /** 首 token 耗时（毫秒，模型流开始 → 首个可见 payload，服务端口径） */
  firstTokenMs?: number
}

// 用户消息附件（文件卡片展示用）
export interface MessageAttachment {
  file_id: string
  filename: string
  file_type?: string
  file_size?: number
  // 已链接到工作区后的可访问路径（用于点击查看）
  parsed_path?: string
  original_path?: string
}

export interface DashboardSpecPayload {
  dashboard_spec: Record<string, unknown>
  validation_warnings?: string[]
  writable_card_count?: number
  datasource_id?: string | null
  semantic_config_id?: string | null
  semantic_config_ids?: string[]
  conversation_id?: string | null
}

// 语义参数（从 data_analyzing_tool 的 action_payload 提取）
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
  // 用户可见展示名（MCP 工具由后端按 plugin.json tool_labels 下发；内置工具走前端映射表）
  display_name?: string
  args: Record<string, unknown>
  result?: string
  progressMessage?: string
  progress?: number
  // file_write / file_edit 流式生成时的实时行数（工具执行中由 tool_progress 事件推送）
  contentLines?: number
  // file_edit 整段替换口径下的被移除行数（old_content，由 tool_progress 事件推送）
  removedLines?: number
  // 流式提取的调用意图（args 未解析前由 tool_progress 事件提前推送，长耗时工具执行中即可展示）
  streamingIntent?: string
  status: 'generating' | 'running' | 'completed' | 'error'
  // 关联的渲染内容
  renderId?: string
  renderType?: 'queryResult' | 'chart'
  // 图表推荐
  visualization?: VisualizationConfig
  // 语义参数（从关联的 data_analyzing_tool 获取）
  semanticParams?: SemanticParams
  // 工具执行起止时间（epoch 毫秒，后端 ToolTimingMiddleware 盖章）：耗时由两者相减得出
  started_at_ms?: number | null
  ended_at_ms?: number | null
  // 子智能体内部过程（task 卡片下方独立区域，按真实发生顺序交错展示）
  subagentTrail?: SubagentTrailItem[]
  // 子智能体上下文用量快照（subagent_context_usage 事件实时更新，最终值随
  // task 结果持久化、历史回放还原）：task 卡片标题行显示「上下文 N%」徽标
  subagentContextUsage?: SubagentContextUsage
}

/** 子智能体上下文用量快照（percent 相对子图预算告警线，≥100 表示已注入收尾告警）。 */
export interface SubagentContextUsage {
  used_tokens: number
  threshold_tokens: number
  percent: number
}

/** 子智能体内部过程条目：工具调用与中间文本输出按发生顺序混排。 */
export interface SubagentTrailItem {
  kind: 'tool' | 'message'
  /** tool：tool_call_id；message：message_id */
  id: string
  // 工具条目
  tool_name?: string
  args?: Record<string, unknown>
  result?: string
  status?: 'running' | 'completed' | 'error'
  // 文本条目
  content?: string
  truncated?: boolean
  // 思考过程（流式增量拼接；默认收起，仅流式进行中露尾部预览）
  thinking?: string
  thinkingTruncated?: boolean
}

// 消息内容片段（用于按顺序渲染文本和工具调用）
export type MessageSegment =
  | { type: 'text'; content: string }
  | { type: 'thinkingSegment'; thinkingSegmentId: string }
  | { type: 'toolCall'; toolCallId: string }

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

// 视图模式
export type ViewMode = 'conversation' | 'builder' | 'editor' | 'dashboard' | 'metricConfig'
export type ChatDataModeLabels = Record<Exclude<ViewMode, 'metricConfig'>, string> & Partial<Record<'metricConfig', string>>

export interface ChatDataSqlChangePayload {
  mode: 'editor' | 'builder'
  sql: string
  builderConfig?: Record<string, unknown>
}

// 对外嵌入配置（ChatDataDialog / ChatDataFloat 统一使用）
// 嵌入前提：宿主项目需已安装并注册 vue-router 与 pinia（页面使用 useRoute 与 user store）
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
export type SSEEventType = 'message' | 'message_end' | 'token_usage' | 'stream_stats' | 'context_compression' | 'thinking' | 'tool' | 'tool_started' | 'tool_progress' | 'tool_completed' | 'tool_failed' | 'subagent_message' | 'subagent_message_delta' | 'subagent_thinking_delta' | 'subagent_context_usage' | 'inbox_admitted' | 'question_requested' | 'run_restarted' | 'error' | 'done' | 'dashboard_spec'

export interface UserQuestionOption {
  label: string
  description?: string
}

export interface UserQuestionItem {
  id: string
  question: string
  header?: string
  options: UserQuestionOption[]
  multi_select: boolean
}

export interface PendingUserQuestion {
  question_id: string
  turn_id?: string
  questions: UserQuestionItem[]
}

export interface UserQuestionAnswer {
  id: string
  selected: string[]
  custom: string
  skipped?: boolean
}

// 工具状态
export type ToolStatus = 'running' | 'completed' | 'error'

// 工具事件数据
export interface ToolEventData {
  tool_name: string
  // 用户可见展示名（MCP 工具由后端下发，内置工具为空串）
  display_name?: string
  tool_call_id: string | null
  status: ToolStatus
  args?: Record<string, unknown>
  result?: string
  message?: string
  timestamp?: string
  // 执行起止时间（epoch 毫秒）：tool_started 只带 started_at_ms，结果事件两者都带
  started_at_ms?: number | null
  ended_at_ms?: number | null
}

export interface ToolLifecycleEventData extends ToolEventData {
  type: 'tool_started' | 'tool_progress' | 'tool_completed' | 'tool_failed' | 'subagent_message' | 'subagent_message_delta' | 'subagent_thinking_delta' | 'subagent_context_usage'
  progress?: number
  accumulated_args?: Record<string, unknown>
  content?: string
  // 子智能体内部过程事件：所属 task 工具调用的 ID（前端据此嵌套展示）
  parent_tool_call_id?: string
  // 子智能体中间文本输出的稳定 ID（message 条目去重）
  message_id?: string
  // 内层结果超长截断标记
  truncated?: boolean
  // 流式增量（subagent_message_delta / subagent_thinking_delta）
  delta?: string
  // 完整消息已通过增量流出：收敛时前端只补截断标记，不覆盖内容
  streamed?: boolean
  // tool_progress：file_write/file_edit 流式生成时的实时行数
  content_lines?: number
  // tool_progress：file_edit 整段替换口径下的被移除行数（old_content）
  removed_lines?: number
  // tool_progress：流式提取的调用意图（args 未解析前提前推送，长耗时工具执行中即可展示）
  intent?: string
  // subagent_context_usage：子图上下文用量快照（每个节点更新后推送一次，最终值随 task 结果持久化）
  used_tokens?: number
  threshold_tokens?: number
  percent?: number
}

// 语义层配置
// 派生指标的统计周期；kind 为 between 时使用 start/end，last_n_days 时使用 n
export interface DerivedMetricPeriod {
  kind: 'today' | 'yesterday' | 'last_n_days' | 'this_week' | 'this_month' | 'between'
  n?: number
  start?: string
  end?: string
  time_dimension: string
}

// 派生指标的修饰词过滤（值支持 IN/BETWEEN 的数组形态）
export interface DerivedMetricFilter {
  dimension: string
  operator?: string
  value?: string | number | boolean | Array<string | number>
}

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
  // 派生指标：原子指标 + 固定周期 + 修饰词；配置后 expression/table 由基础指标决定
  metric_kind?: 'derived' | 'composite'
  base_metric?: string
  period?: DerivedMetricPeriod
  filters?: DerivedMetricFilter[]
  // 复合指标：{指标名} 占位公式（如 "{销售额} / {订单数}"）；配置后 expression/table 由成员指标决定
  formula?: string
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

export interface SemanticQualityFinding {
  code: string
  severity: 'error' | 'warning' | 'info'
  category: string
  message: string
  targets?: string[]
}

/** 保存前校验（dry-run）结果 */
export interface SemanticConfigValidateResult {
  /** 结构校验错误消息；空串表示通过 */
  structure_error: string
  quality_findings: SemanticQualityFinding[]
}

export interface SemanticConfigItem {
  id: string
  name: string
  description?: string
  business_notes?: string
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

// QueryBuilder 拖拽载荷：表
export interface BuilderDragTableData {
  name: string
  alias: string
  label: string
}

// 语义配置-扩展聚合指标选项
export interface MetricExpandOption {
  aggFunc: string
  label: string
  fieldName: string
  displayName: string
  expression: string
  exists: boolean
  selected: boolean
}

// 语义配置-新增字段表单；mode 为 derived/composite 时（派生/复合指标）不使用物理表/字段，口径由 baseMetric+period+filters 或 formula 决定
export interface AddFieldForm {
  tableAlias: string
  column: string
  fieldName: string
  // 原子指标模式：聚合表达式（如 SUM(`amount`)）；字段名由系统自动生成
  expression?: string
  // 原子指标模式：字段备注（派生/复合指标的备注由弹窗内部 ref 管理，不经此模型）
  description?: string
  displayName: string
  mode: 'dimension' | 'metric' | 'derived' | 'composite'
}

// 语义配置-新增字段弹窗的表选项
export interface AddFieldTableOption {
  alias: string
  table: string
  description?: string
  columns: Array<{ name: string; description?: string }>
}

// QueryBuilder 拖拽载荷：字段（数据表字段或语义维度/指标）
export interface BuilderDragFieldData {
  table: string
  tableAlias?: string
  tableLabel?: string
  column: string
  label?: string
  semanticName?: string
  expression?: string
  displayName?: string
  aggFunc?: string
  type?: string
  // 逻辑口径指标（派生/复合）：无物理表字段，后端按 semanticName 解析
  metricKind?: 'derived' | 'composite'
}

// ============ 智能体定时任务 ============

// 定时任务定义（用户侧 / 后台通用）
export interface AgentScheduleItem {
  job_id: string
  user_id: string
  create_by_name?: string
  conversation_id: string
  conversation_mode: 'fixed' | 'new_each_run'
  conversation_title?: string | null
  title: string
  prompt: string
  schedule_type: 'cron' | 'once'
  cron_expr: string
  plan_time: string | null
  effective_end_time: string | null
  enabled_flag: number
  next_trigger_time: string | null
  last_trigger_time: string | null
  datasource_id?: string | null
  options?: Record<string, unknown> | null
  create_time: string | null
  update_time: string | null
  // 后台列表附带的执行统计
  total_count?: number
  succeeded_count?: number
  failed_count?: number
  cancelled_count?: number
  skipped_count?: number
  last_plan_time?: string | null
  last_run_status?: string | null
  last_run_time?: string | null
}

// 定时任务执行历史条目
export interface AgentScheduleRunItem {
  job_id: string
  user_id: string
  conversation_id: string
  plan_time: string | null
  status: 'pending' | 'claimed' | 'dispatched' | 'succeeded' | 'failed' | 'cancelled' | 'skipped'
  trigger_type: 'schedule' | 'manual'
  run_id: string
  run_status: string | null
  error_message: string
  claimed_time: string | null
  dispatched_time: string | null
  finished_time: string | null
  create_time: string | null
}

// 后台全局概览统计
export interface AgentScheduleOverview {
  paused_flag: number
  pause_reason: string
  control_update_time: string | null
  total_jobs: number
  enabled_jobs: number
  total_runs: number
  succeeded_runs: number
  failed_runs: number
  cancelled_runs: number
  skipped_runs: number
  running_runs: number
  runs_last_24h: number
}

// 定时任务创建/编辑载荷
export interface AgentSchedulePayload {
  title: string
  prompt: string
  conversation_id?: string
  conversation_mode?: 'fixed' | 'new_each_run'
  schedule_type: 'cron' | 'once'
  cron_expr?: string
  plan_time?: string | null
  effective_end_time?: string | null
  datasource_id?: string | null
  options?: Record<string, unknown>
}

// 插件 manifest 中声明的 MCP server（管理端展示只需 id/transport）
export interface PluginMcpServerInfo {
  id: string
  transport: string
  url?: string
  command?: string
}

// 插件 manifest 信息
export interface PluginManifestInfo {
  name: string
  version: string
  display_name: string
  description: string
  skills: string[]
  mcp_servers: PluginMcpServerInfo[]
}

// 后台插件列表条目（manifest + 运行状态 + DB 审计字段）
export interface PluginInfo extends PluginManifestInfo {
  enabled: boolean
  create_by_name?: string
  create_time?: string
}

// 后台配置变量条目（可动态修改变量白名单，标注生效值与来源；分类由注册表统一管理）
export interface ChatDataSettingItem {
  setting_key: string
  setting_desc: string
  category: string
  category_label: string
  value_type: 'int' | 'bool' | 'float' | 'string'
  effect_label: string
  setting_value: string | null
  source: 'table' | 'env' | 'default'
  effective_value: unknown
  value_invalid: boolean
  update_time: string
  update_by_name: string
}

// ============ 元数据管理（全局枚举） ============

// 全局枚举值项（value 入库真实值，label 用户可读含义）
export interface MetaGlobalEnumValueItem {
  value: string
  label: string
}

// 全局枚举条目（全系统枚举值域唯一权威，跨数据源跨环境共享；不同业务域同名枚举互相隔离）
export interface MetaGlobalEnum {
  business_domain: string
  enum_name: string
  enum_desc: string
  enum_items: MetaGlobalEnumValueItem[]
  ref_count?: number
  create_time?: string
  update_time?: string
  update_by_name?: string
}

// 全局枚举的数据字典引用方定位（数据源组 / 表 / 列）
export interface MetaGlobalEnumRef {
  group_id: string
  group_name?: string
  table_name: string
  column_name: string
}

// ============ 元数据管理（本体 Ontology） ============

// 本体对象状态机：draft → experimental → certified；deprecated 为终态不可逆
export type OntologyObjectStatus = 'draft' | 'experimental' | 'certified' | 'deprecated'
export type OntologyVisibility = 'prominent' | 'normal' | 'hidden'

// 本体属性项（属性一等实体，随对象表单整体提交）
export interface OntologyPropertyItem {
  property_name: string
  display_name: string
  description: string
  value_type: 'String' | 'Integer' | 'Decimal' | 'Boolean' | 'Date' | 'DateTime' | 'enum'
  // value_type=enum 时的全局枚举引用："enum_name" 或 "domain:enum_name"
  enum_ref: string
  render_hints: Record<string, boolean>
  // 标识属性：参与对象主键组合（业务主键，禁自增行号）；标题属性：对象标题键（至多一个）
  is_identifier: boolean
  is_title: boolean
}

// 本体绑定项（统一绑语义模型：semantic_config_id + table_alias，口径权威在语义层，不触碰物理表）
export interface OntologyBindingItem {
  semantic_config_id: string
  table_alias: string
  // 列映射: {属性API名: 物理列名}
  column_mappings: Record<string, string>
}

// 本体对象（列表行与详情同构；详情含完整 properties）
export interface OntologyObjectItem {
  id?: number
  object_name: string
  display_name: string
  plural_display_name: string
  description: string
  business_notes: string
  status: OntologyObjectStatus
  visibility: OntologyVisibility
  domain: string
  deprecation_reason: string
  deprecated_until: string
  successor_object_name: string
  bindings: OntologyBindingItem[]
  properties?: OntologyPropertyItem[]
  create_time?: string
  update_time?: string
  update_by_name?: string
}

// 本体关系项（关系状态 = 两侧对象状态的较低者，降级自动级联、升级手动）
export interface OntologyRelationItem {
  id?: number
  relation_name: string
  from_object_name?: string
  to_object_name?: string
  from_columns: string[]
  to_columns: string[]
  multiplicity: 'many_to_one' | 'one_to_one'
  from_display_name: string
  to_display_name: string
  from_verbalize: string
  to_verbalize: string
  description: string
  business_notes: string
  status: OntologyObjectStatus
  create_time?: string
  update_time?: string
  update_by_name?: string
}
