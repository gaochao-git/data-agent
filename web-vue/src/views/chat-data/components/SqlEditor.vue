<script setup lang="ts">
/**
 * SQL 编辑器组件
 * 使用 CodeMirror 实现语法高亮和代码补全
 * 支持多条 SQL 语句执行，各自显示结果集
 */
import { ref, computed, watch, onMounted, onUnmounted, shallowRef, useAttrs, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { cancelQueryHistoryExecution, cancelQueryTask, cancelRunningQueryHistory, getChatDataConfig, getQueryHistory, getQueryHistoryResult } from '../api'
import { useQueryTask } from '../composables/useQueryTask'
import { useSaveToPanel } from '../composables/useSaveToPanel'
import { useTableSchemaPanel } from '../composables/useTableSchemaPanel'
import QueryResultPanel from './QueryResultPanel.vue'
import QuickQueries from './QuickQueries.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import QueryFavoriteModal from './QueryFavoriteModal.vue'
import ExecutionTargetPicker from './ExecutionTargetPicker.vue'
import { SQL_FUNCTION_GROUPS, getSqlFunctionCatalog, isQuerySucceeded } from '../utils'
import type { ChatDataSqlChangePayload, ExecutionTargetOption, SqlHistoryItem } from '../types'
import type { SqlFunctionCatalogItem, SqlFunctionGroupKey } from '../utils'
import { buildRecentDaysDateTimeRange, buildRecentMinutesRange, buildRecentHoursRange, buildRecentDaysRange, formatDateTimeRange } from '../utils/dateTime'
import { buildExecutionTargets, isSameStringArray, normalizeSelectedMemberDatasourceIds } from '../utils/executionTargets'
import { isAbortError } from '../utils/abort'
import { parseSQLStatements } from '../utils/sqlStatements'
import { appleTheme } from './sqlEditorTheme'

const { submitSqlTask } = useQueryTask()
// 当前查询任务轮询的取消通道:组件卸载时中止,避免销毁后仍继续轮询/写状态
let queryTaskAbortController: AbortController | null = null
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

import type { QueryResultPanelItem } from '../types'

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

// 本地兜底执行对象选项：优先使用父级传入的 executionTargets
const localExecutionTargets = computed<ExecutionTargetOption[]>(() => (
  buildExecutionTargets({ datasource: props.datasource, fallbackId: props.datasourceId })
))

const emit = defineEmits<{
  'update:selectedMemberDatasourceIds': [value: string[]]
}>()

const executionTargets = computed<ExecutionTargetOption[]>(() => props.executionTargets ?? localExecutionTargets.value)
const internalSelectedMemberDatasourceIds = ref<string[]>([])
const selectedMemberDatasourceIds = computed({
  get: () => props.selectedMemberDatasourceIds ?? internalSelectedMemberDatasourceIds.value,
  set: (value: string[]) => {
    const normalizedValue = normalizeSelectedMemberDatasourceIds(value, props.executionTargetMultiple)
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
    selectedMemberDatasourceIds.value = normalizeSelectedMemberDatasourceIds(retainedIds, props.executionTargetMultiple)
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
    if (response.data?.status === 'kill_failed') {
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

// 保存到看板：共享弹窗状态与打开逻辑
const {
  showSaveModal,
  saveSql,
  saveChartType,
  saveMemberDatasourceId,
  saveMemberDatasourceIds,
  handleSaveToDashboard,
} = useSaveToPanel()

// 编辑器引用
const containerRef = ref<HTMLDivElement | null>(null)
const editorContainerRef = ref<HTMLDivElement | null>(null)
const codemirrorRef = ref<any>(null)

// 编辑器高度（像素），null = 自然高度，拖动后为固定像素值
const editorHeight = ref<number | null>(null)
const isResizing = ref(false)
const DEFAULT_EDITOR_HEIGHT_RATIO = 0.55

// 编辑器/AI面板宽度


// CodeMirror 主题 - Apple 风格（配置体较大，抽至 sqlEditorTheme.ts）

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

// 解析多条 SQL 语句（实现抽至 utils/sqlStatements.ts，含注释/引号跳过逻辑）

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
  // 每次执行创建独立取消通道,供组件卸载时中止本次轮询
  const taskController = new AbortController()
  queryTaskAbortController = taskController
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
      signal: taskController.signal,
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
    // 组件卸载触发的轮询取消不属于查询失败,不向结果区写错误,避免已销毁组件仍弹错误提示
    if (!isAbortError(err)) {
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
    Boolean(target.closest('.el-dialog, .el-popper'))
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
}

const onMouseUp = () => {
  isResizing.value = false
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
  // 卸载时中止进行中的查询任务轮询(AbortError 由调用方静默处理)
  queryTaskAbortController?.abort()
  queryTaskAbortController = null
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
        <div class="flex-1 min-w-0 overflow-hidden bg-white editor-wrapper">
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

.sql-context-menu {
  position: fixed;
  z-index: var(--z-context-menu);
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

</style>
