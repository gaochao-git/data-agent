<script setup lang="ts">
/**
 * SQL 编辑器组件
 * 使用 CodeMirror 实现语法高亮和代码补全
 * 支持多条 SQL 语句执行，各自显示结果集
 * 支持 AI 生成 SQL
 */
import { ref, computed, watch, onMounted, onUnmounted, shallowRef, useAttrs, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import { cancelQueryHistoryExecution, cancelQueryTask, cancelRunningQueryHistory, createConversation, generateConversationTitle, getChatDataConfig, getConversationHistory, getConversations, getQueryHistory, getQueryHistoryResult, streamQuickSql } from '../api'
import { useQueryTask } from '../composables/useQueryTask'
import { useTableSchemaPanel } from '../composables/useTableSchemaPanel'
import QueryResultPanel from './QueryResultPanel.vue'
import QuickQueries from './QuickQueries.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import QueryFavoriteModal from './QueryFavoriteModal.vue'
import ExecutionTargetPicker from './ExecutionTargetPicker.vue'
import AiGeneratedNotice from './AiGeneratedNotice.vue'
import { SQL_FUNCTION_GROUPS, getSqlFunctionCatalog, isQuerySucceeded } from '../utils'
import type { ChatDataSqlChangePayload, ExecutionTargetOption, SqlHistoryItem } from '../types'
import type { SqlFunctionCatalogItem, SqlFunctionGroupKey } from '../utils'
import { buildRecentDaysDateTimeRange, buildRecentMinutesRange, buildRecentHoursRange, buildRecentDaysRange, formatDateTimeRange } from '../utils/dateTime'

const md = new MarkdownIt({ html: false, breaks: true, linkify: true })
const renderMarkdown = (text: string) => md.render(text)
const { submitSqlTask } = useQueryTask()
const SQL_ASSIST_AGENT_ID = 'chat_data_sql_assist'
const isMacPlatform = /Mac|iPhone|iPad|iPod/i.test(globalThis.navigator?.platform || '')
const primaryShortcutLabel = isMacPlatform ? '⌘' : 'Ctrl'
const optionShortcutLabel = isMacPlatform ? 'Option' : 'Alt'
const executeShortcutLabel = `${primaryShortcutLabel}+Enter`
const executeAllShortcutLabel = `${primaryShortcutLabel}+Shift+Enter`
const formatShortcutLabel = `Shift+${optionShortcutLabel}+F`
const clearShortcutLabel = `${primaryShortcutLabel}+K`

// CodeMirror
import { Codemirror } from 'vue-codemirror'
import { sql, MySQL } from '@codemirror/lang-sql'
import { EditorView } from '@codemirror/view'
import { EditorSelection } from '@codemirror/state'
import type { Extension } from '@codemirror/state'
import IconPlay from './icons/IconPlay.vue'

const safeRandomId = (): string => {
  const c = (globalThis as any)?.crypto
  if (typeof c?.randomUUID === 'function') {
    return c.randomUUID()
  }
  if (typeof c?.getRandomValues === 'function') {
    const bytes = new Uint8Array(16)
    c.getRandomValues(bytes)
    return Array.from(bytes).map((b) => b.toString(16).padStart(2, '0')).join('')
  }
  return `id_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

type SqlEditorSSEEventType = 'message' | 'error'
type AiChatMessage = { role: 'user' | 'assistant'; content: string; time?: string; isStreaming?: boolean }
type AiMessageSegment = { type: 'markdown' | 'sql'; content: string }
type QueryResultPanelItem = {
  key?: string | number
  title?: string
  resultData: string
  sql?: string
  memberDatasourceId?: string
  memberName?: string
  status?: 'success' | 'error' | 'none'
}
type AiConversationRecord = {
  id: string
  datasourceId: string
  datasourceName: string
  title: string
  updatedAt: string
  messages: AiChatMessage[]
}

const AI_CONVERSATION_HISTORY_KEY = 'chat_data_sql_ai_conversation_history'
const AI_CONVERSATION_HISTORY_LIMIT = 50

const splitAiMessageSegments = (content: string): AiMessageSegment[] => {
  if (!content) return []
  const segments: AiMessageSegment[] = []
  const codeBlockPattern = /```(?:sql|mysql|hive)?\s*([\s\S]*?)```/gi
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = codeBlockPattern.exec(content)) !== null) {
    const markdown = content.slice(lastIndex, match.index).trim()
    if (markdown) {
      segments.push({ type: 'markdown', content: markdown })
    }
    const sqlContent = (match[1] || '').trim()
    if (sqlContent) {
      segments.push({ type: 'sql', content: sqlContent })
    }
    lastIndex = match.index + match[0].length
  }

  const remaining = content.slice(lastIndex).trim()
  if (remaining) {
    segments.push({ type: 'markdown', content: remaining })
  }

  return segments.length ? segments : [{ type: 'markdown', content }]
}

function parseSSEEvents(buffer: string): {
  events: Array<{ event: SqlEditorSSEEventType; data: string }>
  remaining: string
} {
  const events: Array<{ event: SqlEditorSSEEventType; data: string }> = []
  const blocks = buffer.split('\n\n')
  const remaining = blocks.pop() || ''

  for (const block of blocks) {
    if (!block.trim()) continue

    const lines = block.split('\n')
    let eventType: SqlEditorSSEEventType = 'message'
    const dataLines: string[] = []

    for (const line of lines) {
      if (line.startsWith('event:')) {
        const rawType = line.substring(6).trim()
        if (rawType === 'error') {
          eventType = 'error'
        }
      } else if (line.startsWith('data:')) {
        dataLines.push(line.substring(5).trim())
      }
    }

    const dataStr = dataLines.join('\n')
    if (dataStr) {
      events.push({ event: eventType, data: dataStr })
    }
  }

  return { events, remaining }
}

interface TableInfo {
  table_name: string
  description?: string
  business_desc?: string
  columns?: Array<{
    name: string
    type?: string
    mysql_type?: string
    description?: string
    primary_key?: boolean
  }>
}

interface DataSource {
  id: string
  group_name: string
  ds_tables?: TableInfo[]
  template_datasource_id?: string
  source_type?: string
  source_config?: {
    host?: string
    port?: number
    database?: string
  }
  member_sources?: Array<{
    member_datasource_id?: string
    org_name?: string
    org_code?: string
    source_type?: string
    host?: string
    port?: number
    database: string
  }>
}

interface Props {
  datasourceId: string
  datasource?: DataSource | null
  tableSchema?: Record<string, string[]>
  hideSidebar?: boolean
  initialSql?: string
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  hideExecutionTargetSelector?: boolean
  onSqlChange?: (payload: ChatDataSqlChangePayload) => void
}

const props = withDefaults(defineProps<Props>(), {
  datasource: null,
  tableSchema: () => ({}),
  hideSidebar: false,
  executionTargetMultiple: true,
  hideExecutionTargetSelector: false
})
const attrs = useAttrs()

// 表结构面板（搜索/展开/悬浮标题，与 QueryBuilder 共享逻辑）
const schemaTables = computed(() => props.datasource?.ds_tables || [])
const { searchText, expandedTables, filteredTables, buildTableTitle, toggleTable } = useTableSchemaPanel(schemaTables)
const isDatasourceHovered = ref(false)

const focusSqlEditor = () => {
  const view = editorViewRef.value
  if (!view) return
  view.focus()
  const mainSelection = view.state.selection.main
  const hasRange = mainSelection.from !== mainSelection.to
  const anchor = hasRange ? mainSelection.to : view.state.doc.length
  view.dispatch({
    selection: { anchor },
    scrollIntoView: true,
  })
}

const editorViewRef = shallowRef<EditorView | null>(null)
const lastEditorSelection = ref<{ from: number; to: number } | null>(null)
const sqlContextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
  selectedText: '',
  selection: null as { from: number; to: number } | null,
})

const sqlContextFunctionGroups = SQL_FUNCTION_GROUPS.filter(group => group.key !== 'common') as Array<{ key: SqlFunctionGroupKey; label: string }>

const sqlContextFunctions = computed<SqlFunctionCatalogItem[]>(() => {
  const sourceType = String(props.datasource?.source_type || '').trim().toLowerCase()
  return getSqlFunctionCatalog(sourceType).filter(item => item.usesExpression !== false)
})

const rememberEditorSelection = () => {
  const view = editorViewRef.value
  if (!view || !view.hasFocus) return
  const selection = view.state.selection.main
  lastEditorSelection.value = { from: selection.from, to: selection.to }
}

const handleEditorReady = (payload: { view: EditorView }) => {
  editorViewRef.value = payload.view
  rememberEditorSelection()
}

const rememberEditorSelectionFromView = (view: EditorView) => {
  if (!view.hasFocus) return
  const selection = view.state.selection.main
  lastEditorSelection.value = { from: selection.from, to: selection.to }
}

const getSelectedSqlText = (): string => {
  const view = editorViewRef.value
  if (!view) return ''
  const selection = view.state.selection.main
  if (!selection || selection.empty) return ''
  return view.state.doc.sliceString(selection.from, selection.to).trim()
}

const closeSqlContextMenu = () => {
  sqlContextMenu.value.visible = false
}

const handleSqlEditorContextMenu = (event: MouseEvent, view: EditorView) => {
  const selection = view.state.selection.main
  if (!selection || selection.empty) {
    closeSqlContextMenu()
    return false
  }
  const selectedText = view.state.doc.sliceString(selection.from, selection.to).trim()
  if (!selectedText) {
    closeSqlContextMenu()
    return false
  }
  event.preventDefault()
  const menuWidth = 260
  const menuHeight = 360
  sqlContextMenu.value = {
    visible: true,
    x: Math.min(event.clientX, window.innerWidth - menuWidth - 12),
    y: Math.min(event.clientY, window.innerHeight - menuHeight - 12),
    selectedText,
    selection: { from: selection.from, to: selection.to },
  }
  lastEditorSelection.value = { from: selection.from, to: selection.to }
  return true
}

const applySqlContextFunction = (fn: SqlFunctionCatalogItem) => {
  const view = editorViewRef.value
  const menuState = sqlContextMenu.value
  if (!view || !menuState.selection || !menuState.selectedText) return
  const docLength = view.state.doc.length
  const from = Math.max(0, Math.min(menuState.selection.from, docLength))
  const to = Math.max(from, Math.min(menuState.selection.to, docLength))
  const expression = fn.build(menuState.selectedText)
  view.focus()
  view.dispatch({
    changes: { from, to, insert: expression },
    selection: EditorSelection.cursor(from + expression.length),
    scrollIntoView: true,
  })
  sqlCode.value = view.state.doc.toString()
  lastEditorSelection.value = { from: from + expression.length, to: from + expression.length }
  closeSqlContextMenu()
}

// 插入表名到编辑器
const insertTableName = (tableName: string) => {
  insertSqlToken(tableName)
}

const getReferencedTableNames = () => {
  const tableNames = props.datasource?.ds_tables?.map(table => table.table_name) || []
  if (tableNames.length === 0) return new Set<string>()
  const sqlText = sqlCode.value.toLowerCase()
  return new Set(tableNames.filter(tableName => {
    const normalizedTableName = tableName.toLowerCase()
    return new RegExp(`(^|[^\\w$])${normalizedTableName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^\\w$]|$)`).test(sqlText)
  }))
}

const shouldInsertColumnWithoutTable = (tableName: string) => {
  const referencedTables = getReferencedTableNames()
  if (referencedTables.size === 0) return true
  return referencedTables.size === 1 && referencedTables.has(tableName)
}

const getCurrentInsertSelection = () => {
  const view = editorViewRef.value
  if (!view) return null
  const docLength = view.state.doc.length
  const selection = view.hasFocus ? view.state.selection.main : lastEditorSelection.value
  if (!selection) return null
  const from = Math.max(0, Math.min(selection.from, docLength))
  const to = Math.max(from, Math.min(selection.to, docLength))
  return { view, from, to }
}

const shouldPrefixCommaForColumnInsert = () => {
  const insertSelection = getCurrentInsertSelection()
  if (!insertSelection || insertSelection.from !== insertSelection.to) return false
  const textBeforeCursor = insertSelection.view.state.doc.sliceString(0, insertSelection.from)
  const normalizedText = textBeforeCursor.toLowerCase()
  const selectMatch = Array.from(normalizedText.matchAll(/\bselect\b/g)).pop()
  if (!selectMatch || typeof selectMatch.index !== 'number') return false
  const selectIndex = selectMatch.index
  if (/\bfrom\b/.test(normalizedText.slice(selectIndex))) return false

  const selectListText = textBeforeCursor.slice(selectIndex + selectMatch[0].length)
  const trimmedSelectList = selectListText.trim().toLowerCase()
  if (!trimmedSelectList || trimmedSelectList === 'distinct' || trimmedSelectList === 'all') return false
  return !/[,(]\s*$/.test(selectListText)
}

// 插入列名到编辑器
const insertColumnName = (tableName: string, columnName: string) => {
  const columnToken = shouldInsertColumnWithoutTable(tableName) ? columnName : `${tableName}.${columnName}`
  insertSqlToken(`${shouldPrefixCommaForColumnInsert() ? ', ' : ''}${columnToken}`)
}

const insertSqlToken = (token: string) => {
  const view = editorViewRef.value
  const fallbackAppend = () => {
    const current = sqlCode.value
    sqlCode.value = current + (current && !current.endsWith(' ') ? ' ' : '') + token
    nextTick(() => focusSqlEditor())
  }
  if (!view) {
    fallbackAppend()
    return
  }

  const docLength = view.state.doc.length
  const selection = view.hasFocus ? view.state.selection.main : lastEditorSelection.value
  if (!selection) {
    fallbackAppend()
    return
  }

  const from = Math.max(0, Math.min(selection.from, docLength))
  const to = Math.max(from, Math.min(selection.to, docLength))
  view.focus()
  view.dispatch({
    changes: { from, to, insert: token },
    selection: EditorSelection.cursor(from + token.length),
    scrollIntoView: true,
  })
  sqlCode.value = view.state.doc.toString()
  lastEditorSelection.value = { from: from + token.length, to: from + token.length }
}

const resolveInitialSql = () => props.initialSql ?? ''
// SQL 内容
const sqlCode = ref(resolveInitialSql())
// 最近一次保存值（用于“保存SQL”按钮启用状态）
const savedSqlCode = ref(sqlCode.value)
const hasSqlChanged = computed(() => sqlCode.value !== savedSqlCode.value)

const handleSaveSql = () => {
  if (!hasSqlChanged.value) return
  const cb = props.onSqlChange
  if (!cb) {
    ElMessage.warning('未配置保存回调')
    return
  }
  cb({
    mode: 'editor',
    sql: sqlCode.value
  })
  savedSqlCode.value = sqlCode.value
  ElMessage.success('SQL 已保存')
}

const loadRuntimeConfig = async () => {
  try {
    const response = await getChatDataConfig()
    queryCacheEnabled.value = !!response.data?.query_cache_enabled
    if (!queryCacheEnabled.value) {
      skipQueryCache.value = false
    }
  } catch (err) {
    console.warn('获取智能问数运行时配置失败:', err)
    queryCacheEnabled.value = false
    skipQueryCache.value = false
  }
}

// 执行状态
const executing = ref(false)
const executingTaskText = ref('')
const executingTaskId = ref('')
const cancellingTask = ref(false)
const executionTime = ref<number | null>(null)
const skipQueryCache = ref(false)
const queryCacheEnabled = ref(false)

// 多结果支持
interface QueryResultItem {
  sql: string
  status?: string
  columns: string[]
  data: Record<string, unknown>[]
  total_count: number
  execution_time_ms: number
  error?: string
  title?: string
  memberDatasourceId?: string
  memberName?: string
  cache_hit?: boolean
  cache_age_seconds?: number | null
  cache_expires_in_seconds?: number | null
}
const results = ref<QueryResultItem[]>([])
const queryResultPanelItems = computed<QueryResultPanelItem[]>(() =>
  results.value.map((result, index) => ({
    key: `${result.memberDatasourceId || result.title || 'result'}-${index}`,
    title: result.title || result.memberName || result.sql || `结果${index + 1}`,
    resultData: JSON.stringify({
      status: result.status || (result.error ? 'failed' : 'succeeded'),
      columns: result.columns,
      data: result.data,
      total_count: result.total_count,
      execution_time_ms: result.execution_time_ms,
      error: result.error,
      cache_hit: result.cache_hit,
      cache_age_seconds: result.cache_age_seconds,
      cache_expires_in_seconds: result.cache_expires_in_seconds,
      member_datasource_id: result.memberDatasourceId,
      member_name: result.memberName,
    }),
    sql: result.sql,
    memberDatasourceId: result.memberDatasourceId,
    memberName: result.memberName,
    status: isQuerySucceeded(result) ? 'success' : 'error',
  }))
)
const queryHistoryRefreshKey = ref(0)

const localExecutionTargets = computed<ExecutionTargetOption[]>(() => {
  const members = props.datasource?.member_sources || []
  if (members.length) {
    return members
      .filter(member => member.member_datasource_id)
      .map(member => ({
        member_datasource_id: String(member.member_datasource_id),
        org_name: member.org_name || '',
        org_code: member.org_code || '',
        source_type: member.source_type || props.datasource?.source_type || '',
        host: member.host,
        port: member.port,
        database: member.database,
        label: member.org_name || `${member.host}:${member.port}/${member.database}`,
      }))
  }
  if (!props.datasourceId) return []
  return [{
    member_datasource_id: props.datasource?.template_datasource_id || props.datasourceId,
    source_type: props.datasource?.source_type || '',
    host: props.datasource?.source_config?.host,
    port: props.datasource?.source_config?.port,
    database: props.datasource?.source_config?.database,
    label: props.datasource?.group_name || props.datasourceId,
  }]
})

const emit = defineEmits<{
  'update:selectedMemberDatasourceIds': [value: string[]]
}>()

const executionTargets = computed<ExecutionTargetOption[]>(() => props.executionTargets ?? localExecutionTargets.value)
const internalSelectedMemberDatasourceIds = ref<string[]>([])
const normalizeSelectedMemberDatasourceIds = (value: string[]) => (
  props.executionTargetMultiple ? value : value.slice(0, 1)
)
const isSameStringArray = (left: string[], right: string[]) => {
  if (left.length !== right.length) return false
  return left.every((item, index) => item === right[index])
}
const selectedMemberDatasourceIds = computed({
  get: () => props.selectedMemberDatasourceIds ?? internalSelectedMemberDatasourceIds.value,
  set: (value: string[]) => {
    const normalizedValue = normalizeSelectedMemberDatasourceIds(value)
    if (props.selectedMemberDatasourceIds !== undefined) {
      if (isSameStringArray(normalizedValue, props.selectedMemberDatasourceIds)) return
      emit('update:selectedMemberDatasourceIds', normalizedValue)
      return
    }
    if (isSameStringArray(normalizedValue, internalSelectedMemberDatasourceIds.value)) return
    internalSelectedMemberDatasourceIds.value = normalizedValue
  }
})

const syncSelectedExecutionTargets = () => {
  const availableIds = executionTargets.value.map(item => item.member_datasource_id)
  if (!availableIds.length) {
    selectedMemberDatasourceIds.value = []
    return
  }
  const retainedIds = selectedMemberDatasourceIds.value.filter(id => availableIds.includes(id))
  if (retainedIds.length > 0) {
    selectedMemberDatasourceIds.value = normalizeSelectedMemberDatasourceIds(retainedIds)
    return
  }
  const templateId = props.datasource?.template_datasource_id
  if (templateId && availableIds.includes(templateId)) {
    selectedMemberDatasourceIds.value = [templateId]
    return
  }
  const firstAvailableId = availableIds[0]
  if (firstAvailableId) {
    selectedMemberDatasourceIds.value = [firstAvailableId]
  }
}

watch(
  () => [
    props.datasourceId,
    props.datasource?.template_datasource_id,
    executionTargets.value.map(item => item.member_datasource_id).join(','),
  ],
  () => {
    syncSelectedExecutionTargets()
  },
  { immediate: true }
)

// 收藏
const showFavoriteModal = ref(false)
const favoriteTitle = ref('')
const favoriteContent = ref('')

const openFavoriteModal = () => {
  const sql = sqlCode.value.trim()
  if (!sql) {
    ElMessage.warning('请先输入 SQL')
    return
  }
  favoriteTitle.value = sql.split('\n')[0]?.trim().slice(0, 40) || 'SQL 查询'
  favoriteContent.value = sql
  showFavoriteModal.value = true
}

const handleFavoriteSaved = () => {
  queryHistoryRefreshKey.value++
}

const showHistoryDialog = ref(false)
const historyLoading = ref(false)
const stoppingAllHistory = ref(false)
const historyItems = ref<SqlHistoryItem[]>([])
const historyCurrentPage = ref(1)
const historyPageSize = ref(10)
const historyTotal = ref(0)
const historySourceFilter = ref<'all' | 'sql' | 'agent' | 'builder' | 'dashboard'>('all')
const historyStatusFilter = ref<'all' | 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'timeout' | 'kill_failed'>('all')
const buildDefaultHistoryTimeRange = () => buildRecentDaysDateTimeRange(7)

const applyHistoryTimeShortcut = (unit: 'minutes' | 'hours' | 'days', value: number) => {
  const range =
    unit === 'minutes'
      ? buildRecentMinutesRange(value)
      : unit === 'hours'
        ? buildRecentHoursRange(value)
        : buildRecentDaysRange(value)
  historyTimeRange.value = formatDateTimeRange(range)
  loadQueryHistory()
}
const historyTimeRange = ref<[string, string] | null>(buildDefaultHistoryTimeRange())
const defaultVisibleHistoryColumnKeys = [
  'source',
  'query',
  'executed_at',
  'operator',
  'member',
  'duration',
  'status',
  'error',
  'actions',
]
const visibleHistoryColumnKeys = ref<string[]>([...defaultVisibleHistoryColumnKeys])
const historyColumnOptions = [
  { key: 'source', label: '来源' },
  { key: 'query', label: '执行语句' },
  { key: 'executed_at', label: '执行时间' },
  { key: 'operator', label: '执行人员' },
  { key: 'member', label: '执行对象' },
  { key: 'duration', label: '执行耗时' },
  { key: 'status', label: '执行状态' },
  { key: 'error', label: '错误信息' },
  { key: 'actions', label: '操作' },
  { key: 'execution_id', label: '执行ID' },
  { key: 'task_id', label: '任务ID' },
  { key: 'datasource_id', label: '数据源ID' },
  { key: 'member_datasource_id', label: '成员ID' },
  { key: 'query_type', label: '查询类型' },
  { key: 'task_status', label: '任务状态' },
  { key: 'row_count', label: '结果行数' },
  { key: 'started_time', label: '开始时间' },
  { key: 'finished_time', label: '结束时间' },
]
const visibleHistoryColumnSet = computed(() => new Set(visibleHistoryColumnKeys.value))
const isHistoryColumnVisible = (key: string) => visibleHistoryColumnSet.value.has(key)

const loadQueryHistory = async () => {
  if (!props.datasourceId) {
    historyItems.value = []
    historyTotal.value = 0
    return
  }
  historyLoading.value = true
  try {
    const response = await getQueryHistory({
      datasource_id: props.datasourceId,
      page: historyCurrentPage.value,
      page_size: historyPageSize.value,
      source: historySourceFilter.value,
      status: historyStatusFilter.value,
      start_time: historyTimeRange.value?.[0],
      end_time: historyTimeRange.value?.[1],
    })
    historyItems.value = response.data?.queries || []
    historyTotal.value = Number(response.data?.total || 0)
  } catch (err) {
    console.error('获取查询历史失败:', err)
    ElMessage.error('获取查询历史失败，请稍后重试')
    historyItems.value = []
    historyTotal.value = 0
  } finally {
    historyLoading.value = false
  }
}

const openHistoryDialog = async () => {
  showHistoryDialog.value = true
  historyCurrentPage.value = 1
  await loadQueryHistory()
}

const handleHistoryPageChange = async (page: number) => {
  historyCurrentPage.value = page
  await loadQueryHistory()
}

const handleHistoryPageSizeChange = async (pageSize: number) => {
  historyPageSize.value = pageSize
  historyCurrentPage.value = 1
  await loadQueryHistory()
}

const handleHistoryFilterChange = async () => {
  historyCurrentPage.value = 1
  await loadQueryHistory()
}

const formatHistoryTime = (value?: string | null) => {
  if (!value) return '-'
  const normalized = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  const seconds = String(date.getSeconds()).padStart(2, '0')
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
}

const formatHistoryDuration = (value?: number | null) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-'
  const normalized = Number(value)
  return `${(normalized / 1000).toFixed(2)}秒`
}

const formatHistoryNullableText = (value?: string | number | null) => {
  const text = String(value ?? '').trim()
  return text || '-'
}

const getHistoryStatusTagType = (item: SqlHistoryItem) => {
  if (item.sql_status === 'pending' || item.sql_status === 'running') return 'warning'
  if (item.sql_status === 'succeeded') return 'success'
  if (item.sql_status === 'cancelled') return 'info'
  return item.success_flag === 1 ? 'success' : 'danger'
}

const getHistoryStatusText = (item: SqlHistoryItem) => {
  const statusMap: Record<string, string> = {
    pending: '等待中',
    running: '执行中',
    succeeded: '成功',
    failed: '失败',
    cancelled: '已取消',
    timeout: '超时',
    kill_failed: '取消失败',
  }
  if (item.sql_status && statusMap[item.sql_status]) return statusMap[item.sql_status]
  return item.success_flag === 1 ? '成功' : '失败'
}

const isHistoryItemRunnable = (item: SqlHistoryItem) => {
  return !['pending', 'running'].includes(String(item.sql_status || ''))
}

const isHistoryItemCancellable = (item: SqlHistoryItem) => {
  return Boolean(item.execution_id) && ['pending', 'running'].includes(String(item.sql_status || ''))
}

const isHistoryResultViewable = (item: SqlHistoryItem) => {
  return Boolean(item.execution_id) && !['pending', 'running'].includes(String(item.sql_status || ''))
}

const getHistorySourceText = (item: SqlHistoryItem) => {
  if (item.source) return item.source
  if (item.query_type === 'builder') return '可视化查询'
  if (item.query_type === 'agent' || item.query_type === 'conversation') return '智能问数'
  return 'SQL编辑器'
}

const getHistoryPanelExecutionPlanMemberIds = (item: SqlHistoryItem) => {
  const plan = item.panel_execution_plan
  if (!plan || typeof plan !== 'object') return []
  const result: string[] = []
  Object.values(plan).forEach((entry) => {
    const ids = Array.isArray(entry?.member_datasource_ids) ? entry.member_datasource_ids : []
    ids.forEach((rawId) => {
      const memberId = String(rawId || '').trim()
      if (memberId && !result.includes(memberId)) {
        result.push(memberId)
      }
    })
  })
  return result
}

const getHistoryExecutionTargetText = (item: SqlHistoryItem) => {
  const executionPlanMemberIds = getHistoryPanelExecutionPlanMemberIds(item)
  if ((item.query_type === 'dashboard' || item.source === '看板') && executionPlanMemberIds.length > 1) {
    return `${executionPlanMemberIds.length}个执行对象`
  }
  return item.member_name || item.member_datasource_id || '-'
}

const handleRunHistoryItem = async (item: SqlHistoryItem) => {
  if (!isHistoryItemRunnable(item)) {
    ElMessage.warning('查询仍在执行中，请完成后再运行')
    return
  }
  sqlCode.value = item.query || ''
  showHistoryDialog.value = false
  await nextTick()
  await handleExecute({ ignoreSelection: true })
}

const handleCopyHistoryItem = async (item: SqlHistoryItem) => {
  try {
    await navigator.clipboard.writeText(item.query || '')
    ElMessage.success('已复制')
  } catch (err) {
    console.error('复制查询历史失败:', err)
    ElMessage.error('复制失败，请稍后重试')
  }
}

const handleViewHistoryResult = async (item: SqlHistoryItem) => {
  if (!item.execution_id) {
    ElMessage.warning('缺少执行ID，无法查看结果')
    return
  }
  let response
  try {
    response = await getQueryHistoryResult(item.execution_id)
  } catch (err) {
    console.error('获取历史结果失败:', err)
    ElMessage.error('获取历史结果失败，请稍后重试')
    return
  }
  if (response.status !== 'success') {
    ElMessage.error(response.msg || '获取历史结果失败')
    return
  }
  const payload = response.data?.result_payload
  if (!payload) {
    ElMessage.warning('该查询暂无可查看结果')
    return
  }
  if (!isQuerySucceeded(payload)) {
    ElMessage.warning(payload.error || response.data?.error_message || '历史结果已不可用')
    return
  }
  results.value = [{
    sql: response.data?.query || item.query || '',
    columns: payload.columns || [],
    data: payload.data || [],
    total_count: Number(payload.total_count || 0),
    execution_time_ms: Number(payload.execution_time_ms || response.data?.execution_time_ms || item.execution_time_ms || 0),
    error: undefined,
    title: response.data?.member_name || item.member_name || item.source || '历史结果',
    memberDatasourceId: response.data?.member_datasource_id || item.member_datasource_id,
    memberName: response.data?.member_name || item.member_name,
  }]
  executionTime.value = payload.execution_time_ms ?? response.data?.execution_time_ms ?? item.execution_time_ms ?? null
  showHistoryDialog.value = false
  await nextTick()
  ElMessage.success('已打开历史结果')
}

const handleCancelHistoryItem = async (item: SqlHistoryItem) => {
  if (!item.execution_id) {
    ElMessage.warning('缺少执行ID，无法停止本条 SQL')
    return
  }
  try {
    const response = await cancelQueryHistoryExecution(item.execution_id)
    if (response.status !== 'success') {
      ElMessage.error(response.msg || '停止失败')
      return
    }
    if (response.data?.success === false || response.data?.status === 'kill_failed') {
      ElMessage.warning(response.data?.message || '本条 SQL 停止失败')
    } else {
      ElMessage.success('已提交停止本条 SQL 请求')
    }
    await loadQueryHistory()
  } catch (err) {
    console.error('停止查询历史明细失败:', err)
    ElMessage.error('停止失败，请稍后重试')
  }
}

const handleCancelAllRunningHistory = async () => {
  try {
    await ElMessageBox.confirm(
      '确定要停止你所有正在执行的 SQL 吗？该操作会影响个人查询历史中的所有等待中和执行中任务。',
      '停止全部SQL',
      {
        confirmButtonText: '停止',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      },
    )
  } catch {
    return
  }
  stoppingAllHistory.value = true
  try {
    const response = await cancelRunningQueryHistory()
    if (response.status !== 'success') {
      ElMessage.error(response.msg || '批量停止失败')
      return
    }
    const data = response.data
    if (!data || data.total === 0) {
      ElMessage.info('当前没有正在执行的 SQL')
    } else if (data.failed > 0) {
      ElMessage.warning(`已提交 ${data.total} 条停止请求，${data.failed} 条停止失败`)
    } else {
      ElMessage.success(`已提交 ${data.cancelled} 条停止请求`)
    }
    await loadQueryHistory()
  } catch (err) {
    console.error('批量停止个人查询历史失败:', err)
    ElMessage.error('批量停止失败，请稍后重试')
  } finally {
    stoppingAllHistory.value = false
  }
}

// 保存到看板
const showSaveModal = ref(false)
const saveSql = ref('')
const saveChartType = ref('table')
const saveMemberDatasourceId = ref('')
const saveMemberDatasourceIds = ref<string[]>([])
const handleSaveToDashboard = (sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => {
  saveSql.value = sql
  saveChartType.value = chartType
  saveMemberDatasourceId.value = String(memberDatasourceId || '').trim()
  const executedMemberIds = (memberDatasourceIds || []).map(item => String(item || '').trim()).filter(Boolean)
  saveMemberDatasourceIds.value = executedMemberIds.length > 1
    ? executedMemberIds
    : (saveMemberDatasourceId.value ? [saveMemberDatasourceId.value] : [])
  showSaveModal.value = true
}

// 编辑器引用
const containerRef = ref<HTMLDivElement | null>(null)
const editorContainerRef = ref<HTMLDivElement | null>(null)
const codemirrorRef = ref<any>(null)

// 编辑器高度（像素），null = 自然高度，拖动后为固定像素值
const editorHeight = ref<number | null>(null)
const isResizing = ref(false)
const DEFAULT_EDITOR_HEIGHT_RATIO = 0.55

// 编辑器/AI面板宽度
const editorWidthPercent = ref(50)
const isHorizontalResizing = ref(false)

// AI 生成 SQL 状态
const showAiPanel = ref(false)
const aiQuery = ref('')
const isAiGenerating = ref(false)
const aiMessages = ref<AiChatMessage[]>([])
const aiInputRef = ref<HTMLInputElement | null>(null)
const aiOutputRef = ref<HTMLDivElement | null>(null)
const isAiOutputNearBottom = ref(true)
let abortController: AbortController | null = null
const aiGenerationSeq = ref(0)
// 多轮对话 session
const aiSessionId = ref<string>('')
const showAiConversationHistoryPopover = ref(false)
const aiConversationHistoryItems = ref<AiConversationRecord[]>([])
const isLoadingAiConversationHistory = ref(false)
const isLoadingMoreAiConversationHistory = ref(false)
const aiConversationHistoryPage = ref(1)
const aiConversationHistoryPageSize = 20
const hasMoreAiConversationHistory = ref(false)
const aiConversationBackendReady = ref(false)

const readAiConversationHistory = (): AiConversationRecord[] => {
  try {
    const raw = localStorage.getItem(AI_CONVERSATION_HISTORY_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(item => item?.id && Array.isArray(item?.messages))
  } catch (err) {
    console.error('读取 AI 辅助历史对话失败:', err)
    return []
  }
}

const writeAiConversationHistory = (items: AiConversationRecord[]) => {
  try {
    localStorage.setItem(AI_CONVERSATION_HISTORY_KEY, JSON.stringify(items.slice(0, AI_CONVERSATION_HISTORY_LIMIT)))
  } catch (err) {
    console.error('保存 AI 辅助历史对话失败:', err)
  }
}

const getAiConversationTitle = (messages: AiChatMessage[]) => {
  const firstUserMessage = messages.find(item => item.role === 'user' && item.content.trim())
  const title = firstUserMessage?.content.trim() || '未命名会话'
  return title.length > 32 ? `${title.slice(0, 32)}...` : title
}

const defaultAiConversationTitles = new Set(['', '新会话', '未命名会话'])
const normalizeAiConversationTitle = (value: unknown) => {
  const title = String(value || '').trim()
  return title.length > 32 ? `${title.slice(0, 32)}...` : title
}

const isDefaultAiConversationTitle = (value: unknown) => {
  return defaultAiConversationTitles.has(String(value || '').trim())
}

const normalizeAiConversationMessages = (messages: unknown): AiChatMessage[] => {
  if (!Array.isArray(messages)) return []
  return messages
    .filter((message: any) => message?.role === 'user' || message?.role === 'assistant')
    .map((message: any) => ({
      role: message.role,
      content: String(message.content || ''),
      time: message.time,
      isStreaming: false,
    }))
}

const loadAiConversationMessages = async (conversationId: string) => {
  const response = await getConversationHistory(conversationId)
  const data = await response.json()
  return normalizeAiConversationMessages(data?.data?.messages)
}

const getAiConversationHistoryItems = () => {
  const currentDatasourceId = props.datasourceId || ''
  return readAiConversationHistory().filter(item => item.datasourceId === currentDatasourceId)
}

const normalizeConversationResponse = (response: any): { items: AiConversationRecord[]; hasMore: boolean } => {
  const raw = response?.data
  const conversations = Array.isArray(raw)
    ? raw
    : Array.isArray(raw?.conversations)
      ? raw.conversations
      : []
  const localHistoryMap = new Map(readAiConversationHistory().map(item => [item.id, item]))
  const items = conversations.map((item: any) => {
    const id = String(item?.id || item?.conversation_id || '').trim()
    const localItem = localHistoryMap.get(id)
    const messages = normalizeAiConversationMessages(item?.messages || localItem?.messages)
    const messageTitle = messages.length > 0 ? getAiConversationTitle(messages) : ''
    const titleCandidate = [item?.title, item?.first_message_preview, localItem?.title, messageTitle]
      .map(normalizeAiConversationTitle)
      .find(title => title && !isDefaultAiConversationTitle(title))
    return {
      id,
      datasourceId: props.datasourceId || localItem?.datasourceId || '',
      datasourceName: props.datasource?.group_name || localItem?.datasourceName || '',
      title: titleCandidate || '未命名会话',
      updatedAt: item?.update_time || item?.updated_at || item?.create_time || item?.created_at || localItem?.updatedAt || new Date().toISOString(),
      messages,
    }
  }).filter((item: AiConversationRecord) => item.id)
  return { items, hasMore: !Array.isArray(raw) && Boolean(raw?.has_more) }
}

const groupedAiConversationHistory = computed(() => {
  const groupMap: Record<string, AiConversationRecord[]> = {}
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const weekAgo = new Date(today)
  weekAgo.setDate(today.getDate() - 7)

  for (const item of aiConversationHistoryItems.value) {
    const date = new Date(item.updatedAt)
    const label = Number.isNaN(date.getTime()) || date >= weekAgo ? '7天内' : '更早'
    if (!groupMap[label]) groupMap[label] = []
    groupMap[label].push(item)
  }

  return ['7天内', '更早']
    .filter(label => groupMap[label]?.length)
    .map(label => ({ label, items: groupMap[label] }))
})

const refreshAiConversationHistoryItems = () => {
  aiConversationHistoryItems.value = getAiConversationHistoryItems()
  hasMoreAiConversationHistory.value = false
}

const hydrateAiConversationTitles = async (items: AiConversationRecord[]) => {
  const targets = items.filter(item => isDefaultAiConversationTitle(item.title) && item.messages.length === 0)
  if (targets.length === 0) return items
  const titleEntries = await Promise.all(targets.map(async (item) => {
    try {
      const messages = await loadAiConversationMessages(item.id)
      return [item.id, { title: getAiConversationTitle(messages), messages }] as const
    } catch (err) {
      console.error('加载 AI 辅助历史标题失败:', err)
      return [item.id, null] as const
    }
  }))
  const titleMap = new Map(titleEntries.filter(([, value]) => value).map(([id, value]) => [id, value!]))
  return items.map(item => {
    const hydrated = titleMap.get(item.id)
    return hydrated ? { ...item, title: hydrated.title, messages: hydrated.messages } : item
  })
}

const mergeAiConversationHistoryItems = (items: AiConversationRecord[]) => {
  return Array.from(new Map(items.map(item => [item.id, item])).values())
}

const updateAiConversationTitleCache = (conversationId: string, title: string) => {
  const normalizedTitle = normalizeAiConversationTitle(title)
  if (!conversationId || !normalizedTitle || isDefaultAiConversationTitle(normalizedTitle)) return
  aiConversationHistoryItems.value = aiConversationHistoryItems.value.map(item => (
    item.id === conversationId ? { ...item, title: normalizedTitle } : item
  ))
  const updatedHistory = readAiConversationHistory().map(item => (
    item.id === conversationId ? { ...item, title: normalizedTitle } : item
  ))
  writeAiConversationHistory(updatedHistory)
}

const generateAiConversationTitle = async (conversationId: string) => {
  try {
    const response = await generateConversationTitle(conversationId)
    if (response.status === 'success' && response.data?.title) {
      updateAiConversationTitleCache(conversationId, response.data.title)
      return response.data.title
    }
  } catch (err) {
    console.error('生成 AI 辅助历史标题失败:', err)
  }
  return ''
}

const loadAiConversationHistoryItems = async (page = 1) => {
  const isFirstPage = page === 1
  if (isFirstPage ? isLoadingAiConversationHistory.value : isLoadingMoreAiConversationHistory.value) return
  if (isFirstPage) {
    isLoadingAiConversationHistory.value = true
  } else {
    isLoadingMoreAiConversationHistory.value = true
  }
  try {
    const response = await getConversations(SQL_ASSIST_AGENT_ID, undefined, {
      page,
      page_size: aiConversationHistoryPageSize,
    })
    const { items, hasMore } = normalizeConversationResponse(response)
    const hydratedItems = await hydrateAiConversationTitles(items)
    aiConversationHistoryItems.value = isFirstPage
      ? mergeAiConversationHistoryItems(hydratedItems)
      : mergeAiConversationHistoryItems([...aiConversationHistoryItems.value, ...hydratedItems])
    aiConversationHistoryPage.value = page
    hasMoreAiConversationHistory.value = hasMore
    aiConversationBackendReady.value = true
  } catch (err) {
    console.error('获取 AI 辅助历史对话失败:', err)
    aiConversationBackendReady.value = false
    if (isFirstPage) {
      refreshAiConversationHistoryItems()
    }
  } finally {
    if (isFirstPage) {
      isLoadingAiConversationHistory.value = false
    } else {
      isLoadingMoreAiConversationHistory.value = false
    }
  }
}

const loadMoreAiConversationHistoryItems = async () => {
  if (
    isLoadingAiConversationHistory.value ||
    isLoadingMoreAiConversationHistory.value ||
    !hasMoreAiConversationHistory.value
  ) {
    return
  }
  await loadAiConversationHistoryItems(aiConversationHistoryPage.value + 1)
}

const handleAiConversationHistoryScroll = (event: Event) => {
  const container = event.currentTarget as HTMLElement | null
  if (!container) return
  const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight
  if (distanceToBottom <= 40) {
    void loadMoreAiConversationHistoryItems()
  }
}

const ensureAiConversationId = async () => {
  if (aiSessionId.value && aiConversationBackendReady.value) return aiSessionId.value
  try {
    const response = await createConversation({ agent_id: SQL_ASSIST_AGENT_ID })
    const conversationId = response.data?.id || response.data?.conversation?.id
    if (conversationId) {
      aiSessionId.value = conversationId
      aiConversationBackendReady.value = true
      return conversationId
    }
  } catch (err) {
    console.error('创建 AI 辅助会话失败:', err)
  }
  if (!aiSessionId.value) {
    aiSessionId.value = safeRandomId()
  }
  return aiSessionId.value
}

const saveCurrentAiConversation = (datasourceId = props.datasourceId, datasourceName = props.datasource?.group_name || '') => {
  const messages = aiMessages.value
    .filter(item => item.content.trim() || item.role === 'user')
    .map(item => ({ ...item, isStreaming: false }))

  if (!datasourceId || messages.length === 0) return

  const record: AiConversationRecord = {
    id: aiSessionId.value,
    datasourceId,
    datasourceName,
    title: getAiConversationTitle(messages),
    updatedAt: new Date().toISOString(),
    messages,
  }
  const rest = readAiConversationHistory().filter(item => item.id !== record.id)
  writeAiConversationHistory([record, ...rest])
  refreshAiConversationHistoryItems()
}

const handleAiOutputScroll = () => {
  const container = aiOutputRef.value
  if (!container) return
  const { scrollTop, scrollHeight, clientHeight } = container
  isAiOutputNearBottom.value = scrollHeight - scrollTop - clientHeight < 100
}

const scrollAiOutputToBottom = (force = false) => {
  nextTick(() => {
    const container = aiOutputRef.value
    if (!container) return
    if (!force && !isAiOutputNearBottom.value) return
    container.scrollTop = container.scrollHeight
    isAiOutputNearBottom.value = true
  })
}

// CodeMirror 主题 - Apple 风格
const appleTheme = EditorView.theme({
  '&': {
    backgroundColor: '#FFFFFF',
    color: '#1D1D1F',
    height: '100%',
    fontSize: '14px',
  },
  '&.cm-editor': {
    height: '100%',
  },
  '&.cm-editor.cm-focused': {
    outline: 'none',
  },
  '.cm-scroller': {
    overflow: 'auto',
    height: '100%',
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.6',
  },
  '.cm-content': {
    caretColor: '#1677FF',
    padding: '8px 0',
  },
  '.cm-cursor': {
    borderLeftColor: '#1677FF',
    borderLeftWidth: '2px',
  },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {
    backgroundColor: 'rgba(22, 119, 255, 0.12)',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(22, 119, 255, 0.04)',
  },
  '.cm-gutters': {
    backgroundColor: '#FAFAFA',
    color: '#AEAEB2',
    border: 'none',
    borderRight: '1px solid rgba(0, 0, 0, 0.06)',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 12px 0 12px',
    minWidth: '32px',
    fontSize: '12px',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
    color: '#1677FF',
  },
  // SQL 语法高亮
  '.cm-keyword': {
    color: '#1677FF',
    fontWeight: '600',
  },
  '.cm-string': {
    color: '#FF3B30',
  },
  '.cm-number': {
    color: '#FF9500',
  },
  '.cm-comment': {
    color: '#8E8E93',
    fontStyle: 'italic',
  },
  '.cm-operator': {
    color: '#1677FF',
  },
  '.cm-punctuation': {
    color: '#636366',
  },
  '.cm-variableName, .cm-propertyName': {
    color: '#30B0C7',
  },
  '.cm-typeName': {
    color: '#30B0C7',
  },
  // 自动补全样式
  '.cm-tooltip': {
    backgroundColor: '#FFFFFF',
    border: '1px solid rgba(0, 0, 0, 0.1)',
    borderRadius: '8px',
    boxShadow: '0 4px 16px rgba(0, 0, 0, 0.12)',
  },
  '.cm-tooltip-autocomplete': {
    '& > ul': {
      fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
      fontSize: '13px',
    },
    '& > ul > li': {
      padding: '4px 12px',
    },
    '& > ul > li[aria-selected]': {
      backgroundColor: 'rgba(22, 119, 255, 0.1)',
      color: '#1677FF',
    },
  },
})

// CodeMirror 扩展
const extensions = computed<Extension[]>(() => {
  return [
    sql({
      dialect: MySQL,
      schema: props.tableSchema,
      upperCaseKeywords: true,
    }),
    EditorView.updateListener.of((update) => {
      if (update.selectionSet || update.docChanged) {
        rememberEditorSelectionFromView(update.view)
      }
    }),
    EditorView.domEventHandlers({
      contextmenu: handleSqlEditorContextMenu,
    }),
    EditorView.lineWrapping,
    appleTheme,
  ]
})

// 解析多条 SQL 语句
function parseSQLStatements(sqlText: string): string[] {
  const statements: string[] = []
  let current = ''
  let quoteChar = ''
  let inLineComment = false
  let inBlockComment = false

  for (let i = 0; i < sqlText.length; i++) {
    const char = sqlText[i]
    const nextChar = sqlText[i + 1]

    if (inLineComment) {
      current += char
      if (char === '\n') {
        inLineComment = false
      }
      continue
    }

    if (inBlockComment) {
      current += char
      if (char === '*' && nextChar === '/') {
        current += nextChar
        i++
        inBlockComment = false
      }
      continue
    }

    if (quoteChar) {
      current += char
      if (char === '\\') {
        if (i + 1 < sqlText.length) {
          current += sqlText[i + 1]
          i++
        }
        continue
      }
      if (char === quoteChar) {
        if (quoteChar === "'" && nextChar === "'") {
          current += nextChar
          i++
          continue
        }
        quoteChar = ''
      }
      continue
    }

    if (char === '-' && nextChar === '-') {
      inLineComment = true
      current += char + nextChar
      i++
      continue
    }
    if (char === '#') {
      inLineComment = true
      current += char
      continue
    }
    if (char === '/' && nextChar === '*') {
      inBlockComment = true
      current += char + nextChar
      i++
      continue
    }
    if (char === "'" || char === '"' || char === '`') {
      quoteChar = char
      current += char
      continue
    }

    if (char === ';') {
      const trimmed = current.trim()
      if (trimmed) {
        statements.push(trimmed)
      }
      current = ''
    } else {
      current += char
    }
  }

  const trimmed = current.trim()
  if (trimmed) {
    statements.push(trimmed)
  }

  return statements
}

// 执行 SQL（支持多条）
const handleExecute = async (options: { ignoreSelection?: boolean } = {}) => {
  const selectedSqlText = options.ignoreSelection ? '' : getSelectedSqlText()
  const sqlText = (selectedSqlText || sqlCode.value).trim()
  if (!sqlText) {
    ElMessage.warning('请输入 SQL 语句')
    return
  }

  if (!props.datasourceId) {
    ElMessage.warning('请选择数据源')
    return
  }
  if (executionTargets.value.length > 0 && selectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行数据源')
    return
  }

  const statements = parseSQLStatements(sqlText)
  if (statements.length === 0) {
    ElMessage.warning('没有有效的 SQL 语句')
    return
  }

  executing.value = true
  executingTaskText.value = '任务创建中...'
  executingTaskId.value = ''
  results.value = []
  executionTime.value = null

  const startTime = Date.now()
  try {
    const task = await submitSqlTask({
      datasource_id: props.datasourceId,
      member_datasource_ids: selectedMemberDatasourceIds.value,
      sql: statements,
      query_type: 'sql',
      query_text: 'SQL查询',
      skip_query_cache: queryCacheEnabled.value ? skipQueryCache.value : false,
    }, {
      returnTerminalTask: true,
      onProgress: (taskStatus) => {
        executingTaskText.value = taskStatus.progress_text || '执行中...'
        executingTaskId.value = taskStatus.task_id
      }
    })
    const elapsed = Date.now() - startTime
    const statementResults = task.statement_results || [{
      statement_index: 0,
      sql: statements[0] || sqlText,
      status: task.status,
      error: task.error_message,
      results: task.results || [],
    }]
    const nextResults: QueryResultItem[] = statementResults.flatMap<QueryResultItem>((statementResult) => {
      const memberResults = statementResult.results || []
      if (memberResults.length === 0) {
        return [{
          sql: statementResult.sql,
          status: statementResult.status || undefined,
          columns: [],
          data: [],
          total_count: 0,
          execution_time_ms: elapsed,
          error: isQuerySucceeded(statementResult) ? undefined : (statementResult.error || '执行失败'),
          title: statements.length > 1 ? `SQL ${statementResult.statement_index + 1}` : '结果1',
          memberDatasourceId: undefined,
          memberName: undefined,
          cache_hit: undefined,
          cache_age_seconds: undefined,
          cache_expires_in_seconds: undefined,
        }]
      }
      return memberResults.map((result, memberIndex) => {
        const memberLabel = result.member_name || result.member_datasource_id || `成员${memberIndex + 1}`
        return {
          sql: result.sql || statementResult.sql,
          status: result.status || undefined,
          columns: result.columns || [],
          data: result.data || [],
          total_count: result.total_count || 0,
          execution_time_ms: result.execution_time_ms ?? elapsed,
          error: isQuerySucceeded(result) ? undefined : (result.error || '执行失败'),
          title: statements.length > 1 ? `${memberLabel} · SQL ${statementResult.statement_index + 1}` : memberLabel,
          memberDatasourceId: result.member_datasource_id,
          memberName: result.member_name,
          cache_hit: result.cache_hit,
          cache_age_seconds: result.cache_age_seconds,
          cache_expires_in_seconds: result.cache_expires_in_seconds,
        }
      })
    })
    results.value = nextResults
  } catch (err) {
    results.value = [{
      sql: sqlText,
      status: 'failed',
      columns: [],
      data: [],
      total_count: 0,
      execution_time_ms: Date.now() - startTime,
      error: err instanceof Error ? err.message : '执行失败',
      title: '结果1',
    }]
  }

  executing.value = false
  executingTaskText.value = ''
  executingTaskId.value = ''
  cancellingTask.value = false

  // 如果有成功的查询，触发历史刷新
  const hasSuccess = results.value.some(r => !r.error)
  if (hasSuccess) {
    queryHistoryRefreshKey.value++
  }
}

const handleCancelExecutingTask = async () => {
  if (!executingTaskId.value || cancellingTask.value) return
  cancellingTask.value = true
  try {
    const response = await cancelQueryTask(executingTaskId.value)
    if (response.status !== 'success') {
      ElMessage.error(response.msg || '暂停失败')
      cancellingTask.value = false
      return
    }
    executingTaskText.value = '暂停请求已提交...'
    if (response.data?.status === 'partial_success' || response.data?.status === 'failed') {
      const firstMessage = response.data?.executions?.find(item => item.message)?.message
      ElMessage.warning(firstMessage || '部分 SQL 暂停失败')
    } else {
      ElMessage.success('已提交暂停请求')
    }
    await loadQueryHistory()
  } catch (err) {
    console.error('暂停当前查询失败:', err)
    ElMessage.error('暂停失败，请稍后重试')
    cancellingTask.value = false
  }
}

// 格式化 SQL
const handleFormat = async () => {
  if (!sqlCode.value.trim()) return
  try {
    const { format } = await import('sql-formatter')
    sqlCode.value = format(sqlCode.value, {
      language: 'mysql',
      tabWidth: 2,
      keywordCase: 'upper'
    })
  } catch {
    ElMessage.warning('SQL 格式化失败')
  }
}

// 清空
const handleClear = () => {
  sqlCode.value = ''
  results.value = []
  executionTime.value = null
}

// 快捷键
const isPrimaryShortcut = (e: KeyboardEvent) => e.ctrlKey || e.metaKey

const isEditableShortcutTarget = (target: EventTarget | null): boolean => {
  if (!(target instanceof HTMLElement)) return false
  if (target.closest('.cm-editor')) return false
  const tagName = target.tagName.toLowerCase()
  return (
    tagName === 'input' ||
    tagName === 'textarea' ||
    tagName === 'select' ||
    target.isContentEditable ||
    Boolean(target.closest('.el-dialog, .el-popper, .ai-history-popover'))
  )
}

const isSqlEditorShortcutScope = (target: EventTarget | null): boolean => {
  if (!containerRef.value || !(target instanceof Node)) return false
  return containerRef.value.contains(target)
}

const closeTopOverlay = (): boolean => {
  if (sqlContextMenu.value.visible) {
    closeSqlContextMenu()
    return true
  }
  if (showAiConversationHistoryPopover.value) {
    showAiConversationHistoryPopover.value = false
    return true
  }
  if (showHistoryDialog.value) {
    showHistoryDialog.value = false
    return true
  }
  if (showFavoriteModal.value) {
    showFavoriteModal.value = false
    return true
  }
  if (showSaveModal.value) {
    showSaveModal.value = false
    return true
  }
  if (showAiPanel.value) {
    showAiPanel.value = false
    return true
  }
  return false
}

const handleKeydown = (e: KeyboardEvent) => {
  const key = e.key.toLowerCase()
  const code = e.code
  if (key === 'escape' && closeTopOverlay()) {
    e.preventDefault()
    return
  }

  if (!isSqlEditorShortcutScope(e.target)) return
  if (isEditableShortcutTarget(e.target)) return

  if (isPrimaryShortcut(e) && code === 'Enter') {
    e.preventDefault()
    if (!executing.value) {
      handleExecute({ ignoreSelection: e.shiftKey })
    }
    return
  }

  if (e.shiftKey && e.altKey && code === 'KeyF') {
    e.preventDefault()
    handleFormat()
    return
  }

  if (isPrimaryShortcut(e) && code === 'KeyK') {
    e.preventDefault()
    handleClear()
  }
}

const handleDocumentClick = (event: MouseEvent) => {
  if (!sqlContextMenu.value.visible) return
  const target = event.target as HTMLElement | null
  if (target?.closest('.sql-context-menu')) return
  closeSqlContextMenu()
}

// 拖动调整高度
const startResize = (e: MouseEvent) => {
  e.preventDefault()
  isResizing.value = true
}

const onMouseMove = (e: MouseEvent) => {
  if (isResizing.value && containerRef.value) {
    const rect = containerRef.value.getBoundingClientRect()
    const mouseY = e.clientY - rect.top
    const minH = rect.height * 0.2
    const maxH = rect.height * 0.8
    editorHeight.value = Math.max(minH, Math.min(maxH, mouseY))
  }

  if (isHorizontalResizing.value && editorContainerRef.value) {
    const rect = editorContainerRef.value.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    let newPercent = (mouseX / rect.width) * 100
    newPercent = Math.max(30, Math.min(70, newPercent))
    editorWidthPercent.value = newPercent
  }
}

const onMouseUp = () => {
  isResizing.value = false
  isHorizontalResizing.value = false
}

const syncEditorHeight = (forceDefault = false) => {
  if (!containerRef.value) return
  const rect = containerRef.value.getBoundingClientRect()
  if (!rect.height) return
  const minH = rect.height * 0.2
  const maxH = rect.height * 0.8
  const target = forceDefault || editorHeight.value === null
    ? rect.height * DEFAULT_EDITOR_HEIGHT_RATIO
    : editorHeight.value
  editorHeight.value = Math.max(minH, Math.min(maxH, target))
}

const handleWindowResize = () => {
  syncEditorHeight(false)
}

// AI 生成 SQL
const handleAiGenerate = async () => {
  if (!aiQuery.value.trim()) {
    ElMessage.warning('请输入查询描述')
    return
  }

  if (!props.datasourceId) {
    ElMessage.warning('请先选择数据源')
    return
  }

  const userMessage = aiQuery.value.trim()
  const generationSeq = aiGenerationSeq.value + 1
  const isFirstAiMessage = aiMessages.value.filter(item => item.role === 'user').length === 0
  aiGenerationSeq.value = generationSeq
  const now = new Date()
  const timeStr = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
  aiMessages.value.push({ role: 'user', content: userMessage, time: timeStr })
  aiMessages.value.push({ role: 'assistant', content: '', isStreaming: true })
  saveCurrentAiConversation()
  scrollAiOutputToBottom(true)
  aiQuery.value = ''

  abortController = new AbortController()
  isAiGenerating.value = true

  let fullContent = ''

  try {
    const conversationId = await ensureAiConversationId()
    const response = await streamQuickSql({
      query: userMessage,
      datasource_id: props.datasourceId,
      member_datasource_ids: selectedMemberDatasourceIds.value,
      session_id: aiSessionId.value,
      conversation_id: conversationId,
    }, abortController.signal)

    if (aiGenerationSeq.value !== generationSeq) return

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('无法获取响应流')
    }

    const decoder = new TextDecoder()
    let buffer = ''
    let streamEnded = false

    while (true) {
      const { done, value } = await reader.read()
      if (aiGenerationSeq.value !== generationSeq) return
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const { events, remaining } = parseSSEEvents(buffer)
      buffer = remaining

      for (const { event, data: dataStr } of events) {
        if (dataStr === '[DONE]') {
          streamEnded = true
          break
        }

        try {
          const data = JSON.parse(dataStr)
          if (event === 'error' || data.type === 'error') {
            throw new Error(data.message || '生成失败')
          }
          if (data.type === 'chunk') {
            fullContent += data.content || ''
            if (aiGenerationSeq.value === generationSeq) {
              const lastMessage = aiMessages.value[aiMessages.value.length - 1]
              if (lastMessage?.role === 'assistant') {
                lastMessage.content = fullContent
                lastMessage.isStreaming = true
                scrollAiOutputToBottom()
              }
            }
            continue
          }
          if (data.type === 'complete') {
            streamEnded = true
            break
          }
        } catch (err) {
          console.error('解析 AI SQL SSE 数据失败:', err, dataStr)
          if (err instanceof Error) {
            throw err
          }
        }
      }

      if (streamEnded) break
    }

    if (fullContent) {
      if (aiGenerationSeq.value === generationSeq) {
        const lastMessage = aiMessages.value[aiMessages.value.length - 1]
        if (lastMessage?.role === 'assistant') {
          lastMessage.content = fullContent
          lastMessage.isStreaming = false
        }
        saveCurrentAiConversation()
        if (isFirstAiMessage && conversationId) {
          void generateAiConversationTitle(conversationId)
        }
        scrollAiOutputToBottom()
      }
    }
  } catch (err: any) {
    if (err.name === 'AbortError') {
      if (fullContent) {
        if (aiGenerationSeq.value === generationSeq) {
          const lastMessage = aiMessages.value[aiMessages.value.length - 1]
          if (lastMessage?.role === 'assistant') {
            lastMessage.content = fullContent
            lastMessage.isStreaming = false
          }
          saveCurrentAiConversation()
          scrollAiOutputToBottom()
        }
      }
    } else {
      console.error('AI 生成 SQL 失败:', err)
      ElMessage.error('生成失败，请重试')
      if (aiGenerationSeq.value === generationSeq) {
        const lastMessage = aiMessages.value[aiMessages.value.length - 1]
        if (lastMessage?.role === 'assistant') {
          lastMessage.content = '抱歉，生成失败，请重试。'
          lastMessage.isStreaming = false
        }
        saveCurrentAiConversation()
        scrollAiOutputToBottom()
      }
    }
  } finally {
    if (aiGenerationSeq.value === generationSeq) {
      isAiGenerating.value = false
      const lastMessage = aiMessages.value[aiMessages.value.length - 1]
      if (lastMessage?.role === 'assistant') {
        lastMessage.isStreaming = false
      }
      saveCurrentAiConversation()
      abortController = null
    }
  }
}

// 停止生成
const handleStopGenerate = () => {
  if (abortController) {
    abortController.abort()
  }
}

// 复制文本到剪贴板
const copyToClipboard = (text: string) => {
  navigator.clipboard.writeText(text)
  ElMessage.success('已复制')
}

// 提取 SQL 并添加到编辑器
const extractAndAddSql = (content: string) => {
  // 提取 markdown 代码块中的 SQL
  const sqlMatch = content.match(/```sql\s*([\s\S]*?)```/i) ?? content.match(/```\s*([\s\S]*?)```/)
  const sqlContent = sqlMatch?.[1]?.trim() ?? content.trim()

  const trimmed = sqlCode.value.trim()
  if (trimmed && !trimmed.endsWith(';')) {
    sqlCode.value = trimmed + ';\n\n' + sqlContent
  } else if (trimmed) {
    sqlCode.value = trimmed + '\n\n' + sqlContent
  } else {
    sqlCode.value = sqlContent
  }

  ElMessage.success('已添加到编辑器')
}

// 清空 AI 对话
const clearAiMessages = () => {
  saveCurrentAiConversation()
  aiGenerationSeq.value += 1
  if (isAiGenerating.value) {
    handleStopGenerate()
  }
  isAiGenerating.value = false
  abortController = null
  aiMessages.value = []
  aiQuery.value = ''
  aiSessionId.value = safeRandomId()
  aiConversationBackendReady.value = false
  ElMessage.success('已新建会话')
}

const handleAiConversationHistoryPopoverShow = () => {
  saveCurrentAiConversation()
  void loadAiConversationHistoryItems(1)
}

const handleLoadAiConversation = async (item: AiConversationRecord) => {
  saveCurrentAiConversation()
  aiGenerationSeq.value += 1
  if (isAiGenerating.value) {
    handleStopGenerate()
  }
  isAiGenerating.value = false
  abortController = null
  aiSessionId.value = item.id
  aiConversationBackendReady.value = true
  if (item.messages.length > 0) {
    aiMessages.value = item.messages.map(message => ({ ...message, isStreaming: false }))
  } else {
    try {
      aiMessages.value = await loadAiConversationMessages(item.id)
    } catch (err) {
      console.error('加载 AI 辅助历史对话失败:', err)
      aiMessages.value = []
    }
  }
  aiQuery.value = ''
  showAiConversationHistoryPopover.value = false
  showAiPanel.value = true
  scrollAiOutputToBottom(true)
}

// 数据源切换时保存并重置 AI 会话（表结构变了，历史上下文无意义）
watch(() => props.datasourceId, (_newId, oldId) => {
  saveCurrentAiConversation(oldId || '', '')
  aiMessages.value = []
  aiQuery.value = ''
  aiSessionId.value = safeRandomId()
  aiConversationBackendReady.value = false
  refreshAiConversationHistoryItems()
})

// 外部更新初始化 SQL 时同步到编辑器
watch(() => props.initialSql, () => {
  const next = resolveInitialSql()
  // 避免外部更新直接覆盖用户未保存的本地编辑
  if (hasSqlChanged.value && next !== savedSqlCode.value) {
    ElMessage.warning('检测到本地未保存 SQL，已跳过外部覆盖')
    return
  }
  sqlCode.value = next
  savedSqlCode.value = next
})

// 显示 AI 面板时自动聚焦
watch(showAiPanel, (show) => {
  if (show && aiInputRef.value) {
    setTimeout(() => aiInputRef.value?.focus(), 100)
  }
})

onMounted(() => {
  loadRuntimeConfig()
  document.addEventListener('click', handleDocumentClick)
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
  document.addEventListener('keydown', handleKeydown)
  window.addEventListener('resize', handleWindowResize)
  nextTick(() => syncEditorHeight(true))
})

onUnmounted(() => {
  document.removeEventListener('click', handleDocumentClick)
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
  document.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('resize', handleWindowResize)
})

// 暴露方法
defineExpose({
  setSql: (text: string) => { sqlCode.value = text },
  appendSql: (text: string) => {
    const trimmed = sqlCode.value.trim()
    if (trimmed && !trimmed.endsWith(';')) {
      sqlCode.value = trimmed + ';\n\n' + text
    } else if (trimmed) {
      sqlCode.value = trimmed + '\n\n' + text
    } else {
      sqlCode.value = text
    }
  },
  focusEditor: focusSqlEditor,
  insertTableName,
  insertColumnName
})
</script>

<template>
  <div ref="containerRef" v-bind="attrs" class="flex flex-row h-full bg-gradient-to-br from-slate-50 to-slate-100">
    <!-- 左侧：数据表面板 -->
    <div v-if="!props.hideSidebar" class="w-60 flex flex-col bg-white border-r border-black/[0.06]">
      <!-- 数据源选择（悬浮显示） -->
      <div class="relative" @mouseenter="isDatasourceHovered = true" @mouseleave="isDatasourceHovered = false">
        <div :class="[
          'overflow-hidden transition-all duration-300 bg-gradient-to-br from-white/95 to-gray-50/95 backdrop-blur-[10px] border-b border-black/[0.06]',
          isDatasourceHovered ? 'max-h-[60px] opacity-100 px-3 py-2.5' : 'max-h-0 opacity-0'
        ]">
          <div class="flex items-center gap-2 px-2.5 py-1.5 bg-[#1f82d9]/[0.05] rounded-lg border border-[#1f82d9]/10">
            <i class="fa-solid fa-database text-xs text-[#1f82d9]"></i>
            <span class="flex-1 text-[13px] text-gray-700 font-medium">{{ datasource?.group_name || '未选择数据源' }}</span>
          </div>
        </div>
        <div :class="[
          'flex justify-center py-1 transition-all duration-300',
          isDatasourceHovered && 'opacity-0 h-0 py-0'
        ]">
          <div class="w-9 h-1 bg-gradient-to-b from-gray-400 to-gray-300 rounded-b transition-transform duration-200 hover:scale-x-110"></div>
        </div>
      </div>

      <div class="flex items-center justify-between p-4 border-b border-black/[0.06] bg-gradient-to-br from-blue-50 to-blue-50/50">
        <div class="flex items-center gap-2 text-sm font-semibold text-gray-800">
          <i class="fa-solid fa-database text-[#1f82d9]"></i>
          数据表
        </div>
      </div>

      <!-- 搜索框 -->
      <div class="relative p-3 border-b border-black/[0.06]">
        <i class="fa-solid fa-magnifying-glass absolute left-[22px] top-1/2 -translate-y-1/2 text-xs text-gray-400"></i>
        <input v-model="searchText" type="text" placeholder="支持中英文搜索表名/字段" class="w-full h-9 pl-8 pr-3 border border-black/10 rounded-lg text-[13px] outline-none transition-all duration-200 focus:border-[#1f82d9] focus:shadow-[0_0_0_3px_rgba(31,130,217,0.1)]" />
      </div>

      <!-- 表列表 -->
      <div class="flex-1 overflow-y-auto p-2">
        <div v-if="!datasource" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-[13px]">
          <i class="fa-solid fa-database"></i>
          <span>请选择数据源</span>
        </div>

        <div v-else-if="filteredTables.length === 0" class="flex flex-col items-center justify-center gap-2 p-8 text-gray-400 text-[13px]">
          <span>未找到表</span>
        </div>

        <template v-else>
          <div v-for="table in filteredTables" :key="table.table_name" class="mb-1.5">
            <!-- 表头 -->
            <div
              :class="[
                'flex items-center gap-2 px-3 py-2.5 rounded-[10px] cursor-pointer transition-all duration-200 bg-gradient-to-br from-blue-50/50 to-blue-100/30 border border-blue-500/10 hover:from-blue-100/80 hover:to-blue-200/50 hover:border-blue-500/20',
                expandedTables.has(table.table_name) && 'rounded-b-none'
              ]"
              @click="toggleTable(table.table_name)"
            >
              <div class="text-gray-400 text-xs">
                <i class="fa-solid fa-grip-vertical"></i>
              </div>
              <i class="fa-solid fa-table text-xs text-[#1f82d9]"></i>
              <span class="flex-1 text-[13px] font-medium text-gray-700 overflow-hidden text-ellipsis whitespace-nowrap cursor-pointer hover:text-[#1f82d9]" :title="buildTableTitle(table)" @dblclick.stop="insertTableName(table.table_name)">
                {{ table.description || table.table_name }}
              </span>
              <button class="w-5 h-5 flex items-center justify-center border-none rounded bg-transparent text-gray-400 cursor-pointer text-[10px]">
                <i :class="['fa-solid', expandedTables.has(table.table_name) ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
              </button>
            </div>

            <!-- 字段列表 -->
            <div v-if="expandedTables.has(table.table_name)" class="ml-4 py-1 border-l-2 border-[#1f82d9]/20 bg-slate-50/50 rounded-b-lg">
              <div
                v-for="col in table.columns"
                :key="col.name"
                class="flex items-center gap-2 px-3 py-2 cursor-pointer transition-all duration-150 hover:bg-[#1f82d9]/[0.05]"
                @dblclick="insertColumnName(table.table_name, col.name)"
              >
                <div class="text-gray-400 text-[10px]">
                  <i class="fa-solid fa-grip-vertical"></i>
                </div>
                <span class="flex-1 text-xs text-gray-600">{{ col.description || col.name }}</span>
                <span class="text-[10px] text-gray-400 px-1.5 py-0.5 bg-black/[0.04] rounded font-mono">{{ col.mysql_type || col.type }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 右侧：编辑器区域 -->
    <div class="flex-1 flex flex-col overflow-hidden">
      <!-- 编辑器区域 -->
      <div
        class="flex flex-col min-h-[250px] bg-white shrink-0 overflow-hidden"
        :style="editorHeight !== null ? { height: editorHeight + 'px' } : { height: `${Math.round(DEFAULT_EDITOR_HEIGHT_RATIO * 100)}%` }"
      >
        <!-- 工具栏 -->
        <div class="flex items-start justify-between px-3 py-2 bg-white border-b border-[#e3e5eb]">
          <div class="flex flex-wrap gap-2">
          <button
            class="flex items-center gap-1 h-[24px] px-3 border rounded text-[12px] font-semibold cursor-pointer transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
            :class="executing ? 'border-red-200 bg-red-50 text-red-600 hover:bg-red-100' : 'border-[#e3e5eb] bg-white text-[#21252C] hover:bg-[#f0f1f3]'"
            :disabled="executing && (!executingTaskId || cancellingTask)"
            :title="executing ? '暂停当前查询' : `查询（${executeShortcutLabel}）`"
            @click="executing ? handleCancelExecutingTask() : handleExecute()"
          >
            <i v-if="executing" :class="['fa-solid text-[12px]', cancellingTask ? 'fa-spinner fa-spin' : 'fa-stop']"></i>
            <IconPlay v-else />
            <span class="ml-0.5">{{ executing ? (cancellingTask ? '暂停中...' : '暂停执行') : '查询' }}</span>
          </button>
          <ExecutionTargetPicker
            v-if="!hideExecutionTargetSelector && executionTargets.length > 0"
            v-model="selectedMemberDatasourceIds"
            :targets="executionTargets"
            :multiple="executionTargetMultiple"
          />
          <label v-if="queryCacheEnabled" class="flex items-center gap-1 h-[24px] px-2 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer select-none transition-all duration-150 hover:bg-[#f0f1f3]">
            <input v-model="skipQueryCache" type="checkbox" class="m-0">
            <span>跳过缓存</span>
          </label>
          <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" :title="`格式化（${formatShortcutLabel}）`" @click="handleFormat">
            <i class="fa-solid fa-indent"></i>
            <span>格式化</span>
          </button>
          <button
            class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded text-[12px] font-semibold cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]"
            :class="showAiPanel ? 'bg-[#E8F3FF] border-[#165DFF] text-[#165DFF]' : 'bg-white text-[#21252C]'"
            @click="showAiPanel = !showAiPanel"
          >
            <i class="fa-regular fa-lightbulb"></i>
            <span>SQL助手</span>
          </button>
          <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" @click="openFavoriteModal">
            <i class="fa-regular fa-star"></i>
            <span>收藏</span>
          </button>
          <button
            v-if="props.onSqlChange"
            class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3] disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="!hasSqlChanged"
            @click="handleSaveSql"
          >
            <i class="fa-regular fa-floppy-disk"></i>
            <span>保存SQL</span>
          </button>
          <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" :title="`重置（${clearShortcutLabel}）`" @click="handleClear">
            <i class="fa-regular fa-file"></i>
            <span>重置</span>
          </button>
          <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" @click="openHistoryDialog">
            <i class="fa-regular fa-clock"></i>
            <span>查询历史</span>
          </button>
          </div>
        </div>

      <!-- 编辑器区域（左右分屏） -->
      <div ref="editorContainerRef" class="flex-1 min-h-0 flex overflow-hidden">
        <!-- 左侧：SQL 编辑器 -->
        <div class="overflow-hidden bg-white editor-wrapper" :style="{ width: showAiPanel ? `${editorWidthPercent}%` : '100%' }">
          <Codemirror
            ref="codemirrorRef"
            v-model="sqlCode"
            :extensions="extensions"
            :placeholder="`输入 SQL 语句...（${executeShortcutLabel} 查询，${executeAllShortcutLabel} 查询全部）`"
            :style="{ height: '100%' }"
            :autofocus="true"
            :indent-with-tab="true"
            :tab-size="2"
            @ready="handleEditorReady"
          />
        </div>

        <!-- 横向拖动分隔条 -->
        <div
          v-if="showAiPanel"
          :class="[
            'w-1.5 cursor-col-resize flex items-center justify-center bg-transparent transition-colors duration-150',
            isHorizontalResizing && 'bg-[#1677FF]/[0.05]'
          ]"
          @mousedown.prevent="isHorizontalResizing = true"
        >
          <div :class="[
            'w-0.5 h-6 rounded-sm bg-black/15 transition-all duration-150',
            isHorizontalResizing && 'bg-[#1677FF]'
          ]"></div>
        </div>

        <!-- 右侧：AI 辅助面板 -->
        <div v-if="showAiPanel" class="relative flex flex-col min-h-0 overflow-hidden bg-[#FAFAFA] border-l border-black/[0.06]" :style="{ width: `${100 - editorWidthPercent}%` }">
          <!-- AI 面板工具按钮 -->
          <div class="absolute top-3 left-3 flex items-center gap-3 z-10">
            <el-popover
              v-model:visible="showAiConversationHistoryPopover"
              trigger="hover"
              placement="bottom-start"
              :width="360"
              :show-after="120"
              :hide-after="150"
              :offset="10"
              popper-class="ai-history-popover"
              @show="handleAiConversationHistoryPopoverShow"
            >
              <template #reference>
                <button
                  class="w-8 h-8 flex items-center justify-center border border-black/[0.08] rounded-[15px] bg-white text-[#64748b] shadow-[0_4px_12px_rgba(15,23,42,0.12)] cursor-pointer transition-all duration-150 hover:-translate-y-px hover:text-[#1677FF]"
                  title="历史对话"
                >
                  <i class="fa-solid fa-clock-rotate-left text-[16px]"></i>
                </button>
              </template>

              <div class="ai-history-panel">
                <div class="px-4 py-3 border-b border-slate-100 text-base font-semibold text-slate-800">AI 生成 SQL 历史会话</div>
                <div class="ai-history-list" @scroll="handleAiConversationHistoryScroll">
                  <div v-if="isLoadingAiConversationHistory" class="flex flex-col items-center justify-center gap-2 px-4 py-10 text-gray-400 text-xs">
                    <i class="fa-solid fa-spinner fa-spin text-lg opacity-70"></i>
                    <span>加载中...</span>
                  </div>
                  <div v-else-if="aiConversationHistoryItems.length === 0" class="flex flex-col items-center justify-center gap-2 px-4 py-10 text-gray-400 text-xs">
                    <i class="fa-regular fa-comments text-2xl opacity-50"></i>
                    <span>暂无会话</span>
                  </div>

                  <template v-else>
                    <template v-for="group in groupedAiConversationHistory" :key="group.label">
                      <div class="mb-2">
                        <div class="px-2 py-1 text-[11px] text-gray-400 font-medium">{{ group.label }}</div>
                        <div
                          v-for="item in group.items"
                          :key="item.id"
                          :class="[
                            'group flex items-center gap-2.5 px-3 py-2.5 rounded-lg cursor-pointer transition-all duration-150',
                            aiSessionId === item.id ? 'bg-gradient-to-br from-[#1f82d9]/10 to-[#1f82d9]/[0.05]' : 'hover:bg-black/[0.04]'
                          ]"
                          @click="handleLoadAiConversation(item)"
                        >
                          <i :class="['fa-regular fa-message text-sm shrink-0', aiSessionId === item.id ? 'text-[#1f82d9]' : 'text-gray-400']"></i>
                          <span
                            :class="[
                              'flex-1 text-[13px] overflow-hidden text-ellipsis whitespace-nowrap',
                              aiSessionId === item.id ? 'text-[#1f82d9] font-medium' : 'text-gray-700'
                            ]"
                            :title="item.title"
                          >
                            {{ item.title }}
                          </span>
                        </div>
                      </div>
                    </template>
                    <div v-if="isLoadingMoreAiConversationHistory" class="flex items-center justify-center gap-2 px-4 py-3 text-gray-400 text-xs">
                      <i class="fa-solid fa-spinner fa-spin opacity-70"></i>
                      <span>加载中...</span>
                    </div>
                  </template>
                </div>
              </div>
            </el-popover>
            <button
              class="w-8 h-8 flex items-center justify-center border border-black/[0.08] rounded-[15px] bg-white text-[#334155] shadow-[0_4px_12px_rgba(15,23,42,0.12)] cursor-pointer transition-all duration-150 hover:-translate-y-px hover:text-[#1677FF]"
              title="新建会话"
              @click="clearAiMessages"
            >
              <i class="fa-solid fa-plus text-[15px]"></i>
            </button>
          </div>
          <button class="absolute top-3 right-3 w-7 h-7 flex items-center justify-center border-none rounded-md bg-black/[0.04] text-gray-400 cursor-pointer transition-all duration-150 z-10 hover:bg-black/[0.04] hover:text-gray-700" @click="showAiPanel = false">
            <i class="fa-solid fa-xmark"></i>
          </button>

          <!-- AI 消息区域 -->
          <div ref="aiOutputRef" class="flex-1 min-h-0 overflow-y-auto px-3 pb-3 pt-20" @scroll="handleAiOutputScroll">
            <div v-if="aiMessages.length === 0 && !isAiGenerating" class="flex flex-col items-center justify-center h-full text-gray-400">
              <i class="fa-solid fa-wand-magic-sparkles text-2xl mb-3 text-[#1677FF] opacity-50"></i>
              <p class="text-[13px] m-0">输入自然语言描述</p>
              <p class="text-[11px] mt-1 text-gray-400">AI 将为你生成 SQL 语句</p>
            </div>
            <template v-else>
              <!-- 历史消息 -->
              <div v-for="(msg, idx) in aiMessages" :key="idx" class="mb-3" :class="msg.role === 'user' ? 'flex flex-col items-end' : ''">
                <div v-if="msg.role === 'assistant'" class="max-w-[85%]">
                  <div class="ai-assistant-message px-4 py-3 rounded-2xl rounded-bl bg-white border border-black/[0.06] shadow-[0_2px_8px_rgba(0,0,0,0.04)] text-[13px] leading-relaxed break-words markdown-body">
                    <template v-if="msg.content">
                      <template v-for="(segment, segmentIdx) in splitAiMessageSegments(msg.content)" :key="segmentIdx">
                        <div v-if="segment.type === 'markdown'" class="ai-message-markdown" v-html="renderMarkdown(segment.content)"></div>
                        <div v-else class="ai-sql-block">
                          <div class="ai-sql-actions">
                            <button class="ai-sql-action-btn" title="复制 SQL" @click="copyToClipboard(segment.content)">
                              <i class="fa-regular fa-copy"></i>
                              复制
                            </button>
                            <button class="ai-sql-action-btn" title="添加到编辑器" @click="extractAndAddSql(segment.content)">
                              <i class="fa-solid fa-check"></i>
                              接受
                            </button>
                          </div>
                          <pre><code>{{ segment.content }}</code></pre>
                        </div>
                      </template>
                      <span v-if="msg.isStreaming" class="animate-pulse text-gray-400">▌</span>
                    </template>
                    <div v-else-if="msg.isStreaming" class="flex items-center gap-2 text-gray-500 text-sm">
                      <i class="fa-solid fa-spinner fa-spin"></i>
                      <span>正在思考...</span>
                    </div>
                  </div>
                  <div v-if="!msg.isStreaming" class="flex items-center gap-2 mt-2 pl-1">
                    <button class="flex items-center gap-1 px-2 py-1 border-none rounded bg-transparent text-[11px] text-gray-400 cursor-pointer transition-all duration-150 hover:bg-black/[0.04] hover:text-[#1677FF]" title="复制" @click="copyToClipboard(msg.content)">
                      <i class="fa-regular fa-copy"></i>
                    </button>
                    <AiGeneratedNotice class="ml-auto" />
                  </div>
                  <div v-if="msg.isStreaming" class="flex items-center gap-2 py-1 pl-1">
                    <span class="text-xs font-medium text-gray-500">正在生成</span>
                    <div class="flex gap-1">
                      <span v-for="i in 3" :key="i" class="w-1.5 h-1.5 rounded-full bg-[#1677FF] animate-pulse" :style="{ animationDelay: `${i * 0.2}s` }"></span>
                    </div>
                  </div>
                </div>
                <div v-if="msg.role === 'user'" class="max-w-[85%] px-3.5 py-2.5 rounded-2xl rounded-br bg-[#1677FF]/10 text-[#1F3A56] text-[13px] leading-relaxed">{{ msg.content }}</div>
                <div v-if="msg.role === 'user' && msg.time" class="text-[11px] text-gray-400 mt-1">{{ msg.time }}</div>
              </div>
            </template>
          </div>

          <!-- AI 输入区域 -->
          <div class="p-3 bg-[#FAFAFA] border-t border-black/[0.06]">
            <div class="flex items-center gap-2 px-3 py-2 rounded-[20px] bg-white border border-black/[0.08] shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
              <input
                ref="aiInputRef"
                v-model="aiQuery"
                type="text"
                placeholder="描述你想查询的数据..."
                class="flex-1 border-none bg-transparent text-[13px] outline-none text-gray-900 placeholder:text-gray-400"
                @keydown.enter.prevent="!isAiGenerating && handleAiGenerate()"
              />
              <button
                v-if="isAiGenerating"
                type="button"
                class="relative w-8 h-8 flex items-center justify-center border-none rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200 animate-breathe hover:scale-105 active:scale-95"
                title="停止生成"
                @click="handleStopGenerate"
              >
                <i class="fa-solid fa-stop"></i>
                <span class="absolute inset-0 rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] opacity-40 animate-ping"></span>
              </button>
              <button
                v-else
                type="button"
                :class="[
                  'w-8 h-8 flex items-center justify-center border-none rounded-full bg-gradient-to-br from-[#1677FF] to-[#1677FF] text-white shadow-[0_2px_8px_rgba(0,122,255,0.35)] cursor-pointer transition-all duration-200',
                  !aiQuery.trim() ? 'bg-gray-200 shadow-none cursor-not-allowed text-gray-400' : 'hover:scale-105 active:scale-95'
                ]"
                :disabled="!aiQuery.trim()"
                @click="handleAiGenerate"
              >
                <i class="fa-solid fa-arrow-up"></i>
              </button>
            </div>
          </div>
        </div>
	      </div>

      <div
        v-if="sqlContextMenu.visible"
        class="sql-context-menu"
        :style="{ left: `${sqlContextMenu.x}px`, top: `${sqlContextMenu.y}px` }"
        @mousedown.prevent
      >
        <div class="sql-context-menu__title">处理选中表达式</div>
        <div class="sql-context-menu__selection" :title="sqlContextMenu.selectedText">{{ sqlContextMenu.selectedText }}</div>
        <div
          v-for="group in sqlContextFunctionGroups"
          :key="group.key"
          class="sql-context-menu__group"
        >
          <div class="sql-context-menu__group-title">{{ group.label }}</div>
          <button
            v-for="fn in sqlContextFunctions.filter(item => item.group === group.key)"
            :key="fn.key"
            type="button"
            class="sql-context-menu__item"
            @click="applySqlContextFunction(fn)"
          >
            <span class="sql-context-menu__item-label">{{ fn.label }}</span>
            <span class="sql-context-menu__item-desc">{{ fn.desc }}</span>
          </button>
        </div>
      </div>
      </div>

    <!-- 拖动分隔条 -->
    <div
      class="h-1 cursor-row-resize flex items-center justify-center bg-[#f0f1f3] transition-colors duration-150 shrink-0 hover:bg-[#E8F3FF] group"
      :class="{ 'bg-[#E8F3FF]': isResizing }"
      @mousedown="startResize"
    >
      <div
        class="w-8 h-[2px] rounded-sm bg-[#93949580] transition-all duration-150 group-hover:w-12 group-hover:bg-[#165DFF]"
        :class="{ 'w-12 bg-[#165DFF]': isResizing }"
      ></div>
    </div>

    <!-- 结果区域 -->
    <div class="flex-1 flex flex-col min-h-[100px] overflow-hidden bg-white">
      <!-- 结果内容 -->
      <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
        <QueryResultPanel
          v-if="queryResultPanelItems.length > 0"
          :items="queryResultPanelItems"
          :on-save="handleSaveToDashboard"
        />

        <div v-else-if="executing" class="flex-1 flex flex-col items-center justify-center py-16 px-6 bg-gradient-to-br from-gray-50 to-slate-50">
          <div class="w-20 h-20 flex items-center justify-center rounded-[20px] bg-gradient-to-br from-blue-50 to-blue-100 mb-5 shadow-[0_4px_16px_rgba(31,130,217,0.1)]">
            <i class="fa-solid fa-spinner fa-spin text-[32px] bg-gradient-to-br from-[#1f82d9] to-[#135589] bg-clip-text text-transparent"></i>
          </div>
          <p class="text-base font-semibold text-gray-700 m-0 mb-2">{{ executingTaskText || '执行查询中...' }}</p>
          <p v-if="executingTaskId" class="m-0 text-[12px] text-gray-400">任务ID: {{ executingTaskId }}</p>
        </div>

        <div v-else class="flex-1 flex flex-col overflow-y-auto">
          <!-- 快捷查询历史 -->
          <QuickQueries
            v-if="datasourceId"
            :datasource-id="datasourceId"
            query-type="sql"
            :refresh-trigger="queryHistoryRefreshKey"
            :max-items="8"
            @select-query="(query) => { sqlCode = query }"
          />
        </div>
      </div>
    </div>
    </div>
  </div>

  <!-- 保存到看板弹窗 -->
  <SaveToPanelModal
    v-model:visible="showSaveModal"
    :sql="saveSql"
    :chart-type="saveChartType"
    :datasource-id="datasourceId"
    :member-datasource-id="saveMemberDatasourceId"
    :member-datasource-ids="saveMemberDatasourceIds"
    :execution-targets="executionTargets"
  />

  <QueryFavoriteModal
    v-model:visible="showFavoriteModal"
    mode="sql"
    :datasource-id="datasourceId"
    :initial-title="favoriteTitle"
    :initial-content="favoriteContent"
    @saved="handleFavoriteSaved"
  />

  <el-dialog
    :model-value="showHistoryDialog"
    title="个人查询历史"
    width="min(1440px, 94vw)"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="showHistoryDialog = $event"
  >
    <div class="mb-3 space-y-3">
      <div class="flex flex-wrap items-center gap-3 rounded-md border border-[#eef1f6] bg-[#fafcff] px-3 py-2">
        <label class="flex items-center gap-2 text-[13px] text-[#606266]">
          <span class="whitespace-nowrap">来源</span>
          <el-select
            v-model="historySourceFilter"
            style="width: 150px"
            size="default"
            @change="handleHistoryFilterChange"
          >
            <el-option label="全部" value="all" />
            <el-option label="SQL编辑器" value="sql" />
            <el-option label="智能问数" value="agent" />
            <el-option label="可视化查询" value="builder" />
            <el-option label="看板" value="dashboard" />
          </el-select>
        </label>
        <label class="flex items-center gap-2 text-[13px] text-[#606266]">
          <span class="whitespace-nowrap">状态</span>
          <el-select
            v-model="historyStatusFilter"
            style="width: 140px"
            size="default"
            @change="handleHistoryFilterChange"
          >
            <el-option label="全部" value="all" />
            <el-option label="等待中" value="pending" />
            <el-option label="执行中" value="running" />
            <el-option label="成功" value="succeeded" />
            <el-option label="失败" value="failed" />
            <el-option label="已取消" value="cancelled" />
            <el-option label="超时" value="timeout" />
            <el-option label="取消失败" value="kill_failed" />
          </el-select>
        </label>
        <label class="flex items-center gap-2 text-[13px] text-[#606266]">
          <span class="whitespace-nowrap">时间</span>
          <el-date-picker
            v-model="historyTimeRange"
            type="datetimerange"
            value-format="YYYY-MM-DD HH:mm:ss"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            style="width: 360px"
            @change="handleHistoryFilterChange"
          />
          <div class="flex items-center gap-1">
            <button class="h-8 rounded border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-50" @click="applyHistoryTimeShortcut('minutes', 5)">5分钟</button>
            <button class="h-8 rounded border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-50" @click="applyHistoryTimeShortcut('minutes', 30)">30分钟</button>
            <button class="h-8 rounded border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-50" @click="applyHistoryTimeShortcut('hours', 2)">2小时</button>
            <button class="h-8 rounded border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-50" @click="applyHistoryTimeShortcut('hours', 24)">1天</button>
            <button class="h-8 rounded border border-slate-200 px-2 text-xs text-slate-600 hover:bg-slate-50" @click="applyHistoryTimeShortcut('days', 7)">7天</button>
          </div>
        </label>
        <el-button :loading="historyLoading" @click="loadQueryHistory">刷新</el-button>
        <el-button plain :loading="stoppingAllHistory" @click="handleCancelAllRunningHistory">停止全部SQL</el-button>
        <el-dropdown trigger="click" :hide-on-click="false">
          <el-button plain>
            字段显示
            <i class="fa-solid fa-chevron-down ml-1 text-[10px] text-slate-400"></i>
          </el-button>
          <template #dropdown>
            <div class="w-48 px-2 py-2">
              <el-checkbox-group v-model="visibleHistoryColumnKeys" class="sql-history-column-menu">
                <el-checkbox
                  v-for="column in historyColumnOptions"
                  :key="column.key"
                  :value="column.key"
                >
                  {{ column.label }}
                </el-checkbox>
              </el-checkbox-group>
            </div>
          </template>
        </el-dropdown>
      </div>
    </div>

    <el-table
      v-loading="historyLoading"
      :data="historyItems"
      border
      height="520"
      empty-text="暂无查询历史"
    >
      <el-table-column v-if="isHistoryColumnVisible('source')" label="来源" width="120" align="center">
        <template #default="{ row }">
          {{ getHistorySourceText(row) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('query')" prop="query" label="执行语句" min-width="360">
        <template #default="{ row }">
          <div class="history-sql-cell" :title="row.query">{{ row.query }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('executed_at')" label="执行时间" width="180">
        <template #default="{ row }">
          {{ formatHistoryTime(row.executed_at || row.last_used) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('operator')" label="执行人员" width="140" align="center">
        <template #default="{ row }">
          {{ row.operator_name || '-' }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('member')" label="执行对象" width="160" align="center">
        <template #default="{ row }">
          <span :title="getHistoryExecutionTargetText(row)">{{ getHistoryExecutionTargetText(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('duration')" label="执行耗时" width="120" align="center">
        <template #default="{ row }">
          {{ formatHistoryDuration(row.execution_time_ms) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('status')" label="执行状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="getHistoryStatusTagType(row)" size="small">{{ getHistoryStatusText(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('error')" prop="error_message" label="错误信息" min-width="220">
        <template #default="{ row }">
          <div class="history-error-cell" :title="row.error_message || ''">{{ row.error_message || '-' }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('execution_id')" label="执行ID" width="220">
        <template #default="{ row }">
          <div class="history-id-cell" :title="row.execution_id">{{ formatHistoryNullableText(row.execution_id) }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('task_id')" label="任务ID" width="220">
        <template #default="{ row }">
          <div class="history-id-cell" :title="row.task_id">{{ formatHistoryNullableText(row.task_id) }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('datasource_id')" label="数据源ID" width="180">
        <template #default="{ row }">
          <div class="history-id-cell" :title="row.datasource_id">{{ formatHistoryNullableText(row.datasource_id) }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('member_datasource_id')" label="成员ID" width="180">
        <template #default="{ row }">
          <div class="history-id-cell" :title="row.member_datasource_id">{{ formatHistoryNullableText(row.member_datasource_id) }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('query_type')" label="查询类型" width="120" align="center">
        <template #default="{ row }">
          {{ formatHistoryNullableText(row.query_type) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('task_status')" label="任务状态" width="120" align="center">
        <template #default="{ row }">
          {{ formatHistoryNullableText(row.task_status) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('row_count')" label="结果行数" width="120" align="center">
        <template #default="{ row }">
          {{ row.result_row_count ?? '-' }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('started_time')" label="开始时间" width="180">
        <template #default="{ row }">
          {{ formatHistoryTime(row.started_time) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('finished_time')" label="结束时间" width="180">
        <template #default="{ row }">
          {{ formatHistoryTime(row.finished_time) }}
        </template>
      </el-table-column>
      <el-table-column v-if="isHistoryColumnVisible('actions')" label="操作" width="360" align="center" fixed="right">
        <template #default="{ row }">
          <div class="history-action-cell">
            <el-button v-if="isHistoryItemCancellable(row)" type="danger" link @click="handleCancelHistoryItem(row)">停止该SQL</el-button>
            <el-button type="primary" link :disabled="!isHistoryResultViewable(row)" @click="handleViewHistoryResult(row)">查看结果</el-button>
            <el-button type="primary" link :disabled="!isHistoryItemRunnable(row)" @click="handleRunHistoryItem(row)">一键运行</el-button>
            <el-button type="primary" link @click="handleCopyHistoryItem(row)">一键复制</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <div class="mt-4 flex justify-end">
      <el-pagination
        background
        layout="total, sizes, prev, pager, next"
        :current-page="historyCurrentPage"
        :page-size="historyPageSize"
        :page-sizes="[10, 20, 50]"
        :total="historyTotal"
        @current-change="handleHistoryPageChange"
        @size-change="handleHistoryPageSizeChange"
      />
    </div>
  </el-dialog>

</template>

<style scoped>
/* CodeMirror 编辑器样式 - 需要 :deep() 穿透 */
.editor-wrapper :deep(.cm-editor) {
  height: 100%;
}

.history-sql-cell {
  overflow: hidden;
  max-width: 50ch;
  color: #1f2937;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-error-cell {
  overflow: hidden;
  max-width: 50ch;
  color: #6b7280;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-id-cell {
  overflow: hidden;
  color: #6b7280;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-action-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 18px;
  min-width: 320px;
  white-space: nowrap;
}

.sql-history-column-menu {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: min(60vh, 460px);
  overflow-y: auto;
  padding-right: 4px;
}

.sql-history-column-menu :deep(.el-checkbox) {
  width: 100%;
  margin-right: 0;
}

.ai-history-list {
  max-height: 420px;
  overflow-y: auto;
  padding: 8px;
}

:global(.ai-history-popover.el-popper) {
  padding: 0;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.16);
}

/* Markdown 渲染样式 - 需要 :deep() 穿透 */
.ai-assistant-message {
  color: #1f2937;
}

.ai-message-markdown {
  color: #1f2937;
}

.ai-message-markdown + .ai-sql-block,
.ai-sql-block + .ai-message-markdown {
  margin-top: 10px;
}

.markdown-body :deep(p) {
  margin: 0 0 8px 0;
  color: #1f2937;
}
.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown-body :deep(strong) {
  font-weight: 600;
  color: #1f2937;
}
.markdown-body :deep(em) {
  font-style: italic;
  color: #374151;
}
.markdown-body :deep(code) {
  background-color: #f3f4f6;
  color: #e11d48;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
}
.markdown-body :deep(pre) {
  border: 1px solid #e2e8f0;
  background-color: #f8fafc;
  color: #334155;
  border-radius: 10px;
  padding: 14px 16px;
  overflow-x: auto;
  margin: 8px 0;
}
.markdown-body :deep(pre code) {
  background: none;
  color: inherit;
  padding: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.ai-sql-block {
  position: relative;
  margin: 10px 0;
}

.ai-sql-block pre {
  margin: 0;
  padding: 34px 16px 14px;
  overflow-x: auto;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  background-color: #f8fafc;
  color: #334155;
}

.ai-sql-block code {
  background: none;
  color: inherit;
  padding: 0;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.ai-sql-actions {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.ai-sql-action-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border: 1px solid #dbe3ef;
  border-radius: 999px;
  background: #ffffff;
  color: #475569;
  cursor: pointer;
  font-size: 11px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
  transition: all 0.15s ease;
}

.ai-sql-action-btn:hover {
  border-color: rgba(59, 130, 246, 0.35);
  background: #eff6ff;
  color: #2563eb;
}

.sql-context-menu {
  position: fixed;
  z-index: 4600;
  width: 260px;
  max-height: 360px;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.18);
}

.sql-context-menu__title {
  padding: 4px 6px 6px;
  font-size: 12px;
  font-weight: 600;
  color: #334155;
}

.sql-context-menu__selection {
  margin-bottom: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #f8fafc;
  color: #64748b;
  font-family: "SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sql-context-menu__group + .sql-context-menu__group {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f1f5f9;
}

.sql-context-menu__group-title {
  padding: 2px 6px 4px;
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
}

.sql-context-menu__item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 8px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #334155;
  text-align: left;
  transition: background-color 0.15s ease, color 0.15s ease;
}

.sql-context-menu__item:hover {
  background: #eff6ff;
  color: #2563eb;
}

.sql-context-menu__item-label {
  font-family: "SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
}

.sql-context-menu__item-desc {
  flex-shrink: 0;
  font-size: 11px;
  color: #94a3b8;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 20px;
  margin: 4px 0 8px 0;
}
.markdown-body :deep(li) {
  margin: 2px 0;
  color: #1f2937;
}
.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-weight: 600;
  color: #111827;
  margin: 8px 0 4px 0;
}
.markdown-body :deep(h1) { font-size: 16px; }
.markdown-body :deep(h2) { font-size: 15px; }
.markdown-body :deep(h3) { font-size: 14px; }
.markdown-body :deep(blockquote) {
  border-left: 3px solid #d1d5db;
  padding-left: 12px;
  color: #6b7280;
  margin: 8px 0;
}
.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 8px 0;
}
</style>
