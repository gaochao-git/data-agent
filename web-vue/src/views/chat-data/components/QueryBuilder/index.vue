<script setup lang="ts">
/**
 * 辅助查询构建器 - 拖拽式界面
 * 支持拖拽表到数据源区域，拖拽字段到维度/聚合区域
 */
import { ref, reactive, computed, watch, onMounted, onUnmounted, useAttrs, type ComponentPublicInstance, type PropType } from 'vue'
import { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { cancelQueryTask, getChatDataConfig, getDatasourceSchema, compileSemanticBuilderSql } from '../../api'
import { useQueryTask } from '../../composables/useQueryTask'
import { useSaveToPanel } from '../../composables/useSaveToPanel'
import { useTableSchemaPanel } from '../../composables/useTableSchemaPanel'
import QueryResultPanel from '../QueryResultPanel.vue'
import QuickQueries, { type BuilderConfig, type BuilderFilterNode } from '../QuickQueries.vue'
import SaveToPanelModal from '../SaveToPanelModal.vue'
import QueryFavoriteModal from '../QueryFavoriteModal.vue'
import ExecutionTargetPicker from '../ExecutionTargetPicker.vue'
import AdvancedFilterTree from './AdvancedFilterTree.vue'
import QueryBuilderChartPanel from './QueryBuilderChartPanel.vue'
import QueryBuilderFieldManagers from './QueryBuilderFieldManagers.vue'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'
import IconSetting from '../icons/IconSetting.vue'
import IconPlay from '../icons/IconPlay.vue'
import IconClose from '../icons/IconClose.vue'
import IconCaretDown from '../icons/IconCaretDown.vue'
import type { ChartType } from '../../chartTypes'
import type {
  BuilderDragFieldData,
  BuilderDragTableData,
  ChatDataSqlChangePayload,
  SemanticConfigItem,
  SemanticRelationship,
  ExecutionTargetOption,
} from '../../types'
import { isQuerySucceeded } from '../../utils'
import { buildExecutionTargets, isSameStringArray, normalizeSelectedMemberDatasourceIds } from '../../utils/executionTargets'
import type {
  AggregationField,
  ColumnInfo,
  DimensionField,
  FieldPickerGroup,
  FieldPickerOption,
  FilterCondition,
  FilterLogic,
  AdvancedFilterGroup,
  AdvancedFilterLeaf,
  AdvancedFilterScope,
  OrderByField,
  QueryResultPanelItem,
  SelectedTable,
  TableInfo,
  DataSource,
} from './types'
import {
  appendFilterToAdvancedTree as appendFilterToAdvancedTreeUtil,
  cloneAdvancedFilterNode,
  cloneFilterCondition,
  collectAdvancedLeafFilters,
  createNodeId,
  detectAdvancedScopeFromTree,
  findAdvancedGroup,
  findAdvancedLeafByFilterId,
  getAdvancedLeafIdSet,
  getAdvancedScopeLabel,
  getConfiguredScopeExcludingFilter,
  getFilterScope,
  getFirstConfiguredLeaf,
  removeAdvancedNode,
  removeFilterFromAdvancedTree as removeFilterFromAdvancedTreeUtil,
  syncAdvancedTreeLeafByFilter as syncAdvancedTreeLeafByFilterUtil,
} from './utils/advancedFilterTree'
import { ensureArrayValues, parseManualValues } from './utils/filterValues'
import { buildSemanticSelectedTables } from './utils/semanticSelectedTables'
import { buildBuilderConfigPayload as buildBuilderConfigPayloadUtil, getBuilderFilterValidationError, snapshotBuilderConfigPayload, toSemanticBuilderConfigPayload } from './utils/builderConfig'
import { useQueryBuilderResultAnalysis } from './composables/useQueryBuilderResultAnalysis'
import { isAbortError } from '../../utils/abort'

const props = defineProps({
  datasourceId: { type: String, required: true },
  datasource: { type: Object as PropType<DataSource | null>, default: null },
  datasources: { type: Array as PropType<DataSource[]>, default: () => [] },
  semanticConfigId: { type: String as PropType<string | null>, default: null },
  semanticConfig: { type: Object as PropType<SemanticConfigItem | null>, default: null },
  semanticConfigs: { type: Array as PropType<SemanticConfigItem[]>, default: () => [] },
  initialBuilderConfig: { type: Object as PropType<BuilderConfig | null>, default: null },
  hideSidebar: { type: Boolean, default: false },
  hideChartPanel: { type: Boolean, default: false },
  executionTargets: { type: Array as PropType<ExecutionTargetOption[] | undefined>, default: undefined },
  executionTargetMultiple: { type: Boolean, default: true },
  selectedMemberDatasourceIds: { type: Array as PropType<string[] | undefined>, default: undefined },
  hideExecutionTargetSelector: { type: Boolean, default: false },
  onSqlChange: { type: Function as PropType<((payload: ChatDataSqlChangePayload) => void) | undefined>, default: undefined },
})
const attrs = useAttrs()
const { submitSqlTask } = useQueryTask()
// 当前查询任务轮询的取消通道：组件卸载时中止，避免销毁后仍继续轮询/写状态
let queryTaskAbortController: AbortController | null = null

const emit = defineEmits<{
  datasourceChange: [dsId: string]
  semanticConfigChange: [configId: string]
  'update:selectedMemberDatasourceIds': [value: string[]]
}>()

// 表结构数据
const tables = ref<TableInfo[]>([])
const loading = ref(false)

// 左侧数据表面板（搜索/展开/悬浮标题，与 SQL 编辑器共享逻辑）
const { searchText, expandedTables, filteredTables, buildTableTitle, toggleTable } = useTableSchemaPanel(tables)

// 数据源悬浮状态
const isDatasourceHovered = ref(false)

// 处理数据源变更
const handleDatasourceSelect = (dsId: string) => {
  emit('datasourceChange', dsId)
}

// 拖拽状态
type DraggedItem =
  | { type: 'table'; data: BuilderDragTableData }
  | { type: 'field'; data: BuilderDragFieldData }

const draggedItem = ref<DraggedItem | null>(null)
const dropTargetActive = ref<'dimension' | 'aggregation' | 'filter' | 'order' | null>(null)

// 已选数据
const selectedTables = ref<SelectedTable[]>([])
const dimensions = ref<DimensionField[]>([])
const aggregations = ref<AggregationField[]>([])
const filters = ref<FilterCondition[]>([])
const orderBy = ref<OrderByField[]>([])
const aggFuncSnapshot = ref<Record<string, AggregationField['func']>>({})
const showDimensionManager = ref(false)
const showAggregationManager = ref(false)
const dimensionDrafts = ref<Array<DimensionField & { selected?: boolean }>>([])
const aggregationDrafts = ref<Array<AggregationField & { selected?: boolean }>>([])

// SQL预览弹窗
const showSqlPreview = ref(false)
const previewSql = ref('')
const previewSqlLoading = ref(false)

const getTableAlias = (item: { table?: string; tableAlias?: string; alias?: string }) => {
  return String(item.tableAlias || item.alias || item.table || '').trim()
}

const getSelectedTableAlias = (table: SelectedTable) => table.alias || table.name

const buildFieldKey = (tableAlias: string | undefined, table: string | undefined, column: string | undefined) => {
  const alias = String(tableAlias || table || '').trim()
  const col = String(column || '').trim()
  return alias && col ? `${alias}.${col}` : ''
}

const getTableRoleLabel = (table: string, tableAlias?: string, fallbackLabel?: string) => {
  const alias = String(tableAlias || table || '').trim()
  if (!table) return fallbackLabel || alias
  if (!alias || alias === table) return fallbackLabel || table
  return `${fallbackLabel || alias}（${table}）`
}


// 执行状态
const executing = ref(false)
const executingTaskText = ref('')
const executingTaskId = ref('')
const cancellingTask = ref(false)
const rawResultData = ref<string | null>(null)
const analyzedResultData = ref<string | null>(null)
const executedSql = ref<string>('')
const compiledSql = ref('')
const compiledSqlSnapshot = ref('')
const savedBuilderConfigSnapshot = ref('')
const skipQueryCache = ref(false)
const queryCacheEnabled = ref(false)

// 快捷查询刷新触发器
const quickQueryRefresh = ref(0)
const resultTabs = ref<Array<{
  title: string
  rawResultData: string
  executedSql: string
  memberDatasourceId?: string
  memberName?: string
  status?: 'success' | 'error' | 'none'
}>>([])
const activeResultTabIndex = ref(0)
const queryResultPanelItems = computed<QueryResultPanelItem[]>(() =>
  resultTabs.value.map((result, index) => ({
    key: `${result.memberDatasourceId || result.title || 'result'}-${index}`,
    title: result.title || result.memberName || `结果${index + 1}`,
    resultData: result.rawResultData,
    sql: result.executedSql || executedSql.value || previewSql.value,
    memberDatasourceId: result.memberDatasourceId,
    memberName: result.memberName,
    status: result.status,
  }))
)

// 本地兜底执行对象选项：优先使用父级传入的 executionTargets
const localExecutionTargets = computed<ExecutionTargetOption[]>(() => (
  buildExecutionTargets({ datasource: props.datasource, fallbackId: props.datasourceId })
))

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
  selectedMemberDatasourceIds.value = firstAvailableId ? [firstAvailableId] : []
}

const activateResultTab = (index: number) => {
  const target = resultTabs.value[index]
  activeResultTabIndex.value = index
  analyzedResultData.value = null
  rawResultData.value = target?.rawResultData || null
  executedSql.value = target?.executedSql || ''
}

const handleResultPanelActiveIndex = (index: number) => {
  if (hasAnalyzedResult.value) return
  activateResultTab(index)
}

const buildBuilderConfigSnapshot = () => JSON.stringify(buildBuilderConfigPayload())
const hasSemanticConfigSelected = computed(() => !!props.semanticConfigId)
const isBuilderConfigured = computed(() => hasSemanticConfigSelected.value && (dimensions.value.length > 0 || aggregations.value.length > 0))
const hasSqlChanged = computed(() => buildBuilderConfigSnapshot() !== savedBuilderConfigSnapshot.value)
const hasRawResult = computed(() => !!rawResultData.value)

const ensureSemanticConfigSelected = () => {
  if (hasSemanticConfigSelected.value) return true
  ElMessage.warning('请先选择语义模型')
  return false
}

const handleSaveSql = async () => {
  const cb = props.onSqlChange
  if (!cb) return
  if (!ensureSemanticConfigSelected()) {
    return
  }
  if (!isBuilderConfigured.value) {
    ElMessage.warning('请先配置查询条件')
    return
  }
  if (!validateQueryPagination()) {
    return
  }
  try {
    const builderConfigPayload = snapshotBuilderConfigPayload(buildBuilderConfigParams())
    const builderConfigSnapshot = JSON.stringify(builderConfigPayload)
    const sqlText = await compileCurrentBuilderSql(
      toSemanticBuilderConfigPayload(builderConfigPayload),
      builderConfigSnapshot,
    )
    if (!sqlText) {
      ElMessage.warning('请先配置查询条件')
      return
    }
    cb({
      mode: 'builder',
      sql: sqlText,
      builderConfig: builderConfigPayload
    })
    savedBuilderConfigSnapshot.value = builderConfigSnapshot
    ElMessage.success('SQL 已保存')
  } catch (err) {
    const rawMessage = getApiErrorDetail(err) || (err instanceof Error ? err.message : '')
    const message = normalizeBuilderErrorMessage(rawMessage, 'SQL 编译失败')
    ElMessage.error(message)
  }
}

// 收藏
const showFavoriteModal = ref(false)
const favoriteTitle = ref('')
const favoriteContent = ref('')
const favoriteBuilderConfig = ref<Record<string, unknown> | null>(null)
const pendingFavoriteBuilderConfig = ref<BuilderConfig | null>(null)

// 保存到看板：共享弹窗状态与打开逻辑（chartType 回退当前图表类型、执行对象回退当前结果页签）
const {
  showSaveModal,
  saveSql,
  saveChartType,
  saveMemberDatasourceId,
  saveMemberDatasourceIds,
  handleSaveToDashboard,
} = useSaveToPanel({
  getFallbackChartType: () => selectedChartType.value,
  getFallbackMemberDatasourceId: () => resultTabs.value[activeResultTabIndex.value]?.memberDatasourceId,
})

// 拖动调整高度
const configPanelRef = ref<HTMLDivElement | null>(null)
const configHeight = ref<number | null>(null) // null = 自然高度，拖动后为固定像素值
const isResizing = ref(false)

const startResize = (e: MouseEvent) => {
  e.preventDefault()
  isResizing.value = true
}

const onMouseMove = (e: MouseEvent) => {
  if (isResizing.value && configPanelRef.value) {
    const rect = configPanelRef.value.getBoundingClientRect()
    const mouseY = e.clientY - rect.top
    const minH = rect.height * 0.2
    const maxH = rect.height * 0.8
    configHeight.value = Math.max(minH, Math.min(maxH, mouseY))
  }
}

const onMouseUp = () => {
  isResizing.value = false
}

// 聚合函数选项
const aggFunctions = [
  { value: 'RAW', label: '原始值' },
  { value: 'COUNT', label: '计数' },
  { value: 'SUM', label: '求和' },
  { value: 'AVG', label: '平均' },
  { value: 'MAX', label: '最大' },
  { value: 'MIN', label: '最小' },
  { value: 'COUNT_DISTINCT', label: '去重计数' }
]

const normalizeAggFunc = (func?: string): AggregationField['func'] => {
  const upper = String(func || 'RAW').toUpperCase()
  if (upper === 'COUNT' || upper === 'SUM' || upper === 'AVG' || upper === 'MAX' || upper === 'MIN' || upper === 'COUNT_DISTINCT' || upper === 'RAW') {
    return upper as AggregationField['func']
  }
  return 'RAW'
}

const isRawAggFunc = (func?: string) => normalizeAggFunc(func) === 'RAW'

const getAggregationModeState = (excludeAggId?: string) => {
  const list = excludeAggId ? aggregations.value.filter(item => item.id !== excludeAggId) : aggregations.value
  // 派生/复合指标为逻辑口径，不参与 RAW/聚合模式约束
  const physical = list.filter(item => !isLogicalAggregation(item))
  const hasRaw = physical.some(item => isRawAggFunc(item.func))
  const hasAgg = physical.some(item => !isRawAggFunc(item.func))
  return { hasRaw, hasAgg }
}

const canUseAggregationFunc = (func: string, excludeAggId?: string) => {
  const nextIsRaw = isRawAggFunc(func)
  const { hasRaw, hasAgg } = getAggregationModeState(excludeAggId)
  if (nextIsRaw && hasAgg) return false
  if (!nextIsRaw && hasRaw) return false
  return true
}

const showAggregationModeConflictMessage = (nextFunc: string) => {
  if (isRawAggFunc(nextFunc)) {
    ElMessage.warning('指标区已存在聚合指标，不能再添加原始值指标，请将字段拖到维度或统一改为聚合函数')
    return
  }
  ElMessage.warning('指标区已存在原始值指标，不能再添加聚合指标，请先移除原始值或统一改为原始值')
}

const validateAggregationModeBeforeRun = () => {
  const { hasRaw, hasAgg } = getAggregationModeState()
  if (!hasRaw || !hasAgg) return true
  ElMessage.warning('指标区不支持原始值与聚合函数混用，请在指标区统一函数类型后再执行')
  return false
}

const getCompareAnalysisWarning = (): string | null => {
  if (dimensions.value.length !== 1 || aggregations.value.length !== 1) {
    return '同环比结果分析当前仅支持一个时间维度和一个指标，已展示原始查询结果'
  }
  const transform = dimensions.value[0]?.transform || 'raw'
  if (!['year', 'quarter', 'month', 'day'].includes(transform)) {
    return '同环比结果分析当前仅支持 year/quarter/month/day 时间粒度，暂不支持按周分析，已展示原始查询结果'
  }
  return null
}

const AGG_EXPR_PATTERN = /^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i

const getAggFuncFromExpression = (expression?: string): AggregationField['func'] | undefined => {
  const expr = (expression || '').trim()
  if (!expr) return undefined
  const matched = expr.match(AGG_EXPR_PATTERN)
  if (!matched) return undefined
  const func = (matched[1] || '').toUpperCase()
  const hasDistinct = !!(matched[2] || '').trim()
  if (func === 'COUNT' && hasDistinct) return 'COUNT_DISTINCT'
  if (func === 'COUNT' || func === 'SUM' || func === 'AVG' || func === 'MAX' || func === 'MIN') {
    return func as AggregationField['func']
  }
  return undefined
}

const parseMetricExpressionMeta = (expression?: string) => {
  const expr = (expression || '').trim()
  const normalizeColumn = (raw: string) => {
    const cleaned = (raw || '').replace(/`/g, '').trim()
    if (!cleaned) return '*'
    const parts = cleaned.split('.')
    return parts[parts.length - 1] || '*'
  }
  const distinctMatch = expr.match(/^COUNT\s*\(\s*DISTINCT\s+([^)]+)\s*\)$/i)
  if (distinctMatch) {
    return { func: 'COUNT_DISTINCT' as AggregationField['func'], column: normalizeColumn(distinctMatch[1] || '') }
  }
  const aggMatch = expr.match(/^(COUNT|SUM|AVG|MAX|MIN)\s*\(\s*([^)]+)\s*\)$/i)
  if (aggMatch) {
    const fn = (aggMatch[1] || 'COUNT').toUpperCase() as AggregationField['func']
    const raw = (aggMatch[2] || '').trim()
    const col = raw === '*' ? '*' : normalizeColumn(raw)
    return { func: fn, column: col || '*' }
  }
  const fallbackCol = normalizeColumn(expr)
  return { func: 'RAW' as AggregationField['func'], column: fallbackCol }
}

const parseFirstQualifiedFromExpression = (expression?: string): { table: string; column: string } | null => {
  const expr = (expression || '').trim()
  if (!expr) return null
  const matched = expr.match(/`?([\w\u4e00-\u9fa5]+)`?\.`?([\w\u4e00-\u9fa5]+)`?/)
  if (!matched) return null
  return { table: matched[1] || '', column: matched[2] || '' }
}

// 日期转换选项
const transformOptions = [
  { value: 'raw', label: '原始值' },
  { value: 'year', label: '按年' },
  { value: 'quarter', label: '按季度' },
  { value: 'month', label: '按月' },
  { value: 'week', label: '按周' },
  { value: 'day', label: '按日' }
]

const selectedChartType = ref<ChartType>('table')

const isChartPanelCollapsed = ref(false)
const chartPanelTab = ref<'chart' | 'analysis'>('chart')

// 结果表格格式
const chartFormatCollapsed = ref(false)
const chartFormat = reactive({
  showIndex: false,
  autoWrap: false,
  metricsFirst: false,
})


// 结果表格前端分页
const chartPaginationCollapsed = ref(false)
const chartPagination = reactive({
  enabled: false,
  pageSize: 20,
})

const queryPagination = reactive({
  enabled: false,
  limit: 20 as number | string | null,
  offset: null as number | string | null,
})

const normalizeOptionalInteger = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null
  if (typeof value !== 'number' && typeof value !== 'string') return null
  const normalizedValue = typeof value === 'string' ? value.trim() : value
  if (normalizedValue === '') return null
  const num = Number(normalizedValue)
  return Number.isInteger(num) ? num : null
}

const validateQueryPagination = () => {
  if (!queryPagination.enabled) return true
  const limit = normalizeOptionalInteger(queryPagination.limit)
  if (limit === null || limit <= 0) {
    ElMessage.warning('分页返回条数必须为正整数')
    return false
  }
  const offset = normalizeOptionalInteger(queryPagination.offset)
  if (queryPagination.offset !== null && queryPagination.offset !== '' && (offset === null || offset < 0)) {
    ElMessage.warning('分页跳过条数必须为非负整数')
    return false
  }
  return true
}

const validateBuilderFilters = () => {
  const message = getBuilderFilterValidationError(filters.value)
  if (!message) return true
  ElMessage.warning(message)
  return false
}

// 运算符选项
const operators = [
  { value: '=', label: '等于' },
  { value: '!=', label: '不等于' },
  { value: '>', label: '大于' },
  { value: '>=', label: '大于等于' },
  { value: '<', label: '小于' },
  { value: '<=', label: '小于等于' },
  { value: 'BETWEEN', label: '介于' },
  { value: 'STARTS WITH', label: '开头为' },
  { value: 'ENDS WITH', label: '结尾为' },
  { value: 'EMPTY STRING', label: '为空字符串' },
  { value: 'IS NULL', label: '为空' },
  { value: 'IS NOT NULL', label: '不为空' }
]

const fieldRefOperators = [
  { value: '=', label: '等于' },
  { value: '!=', label: '不等于' },
  { value: '>', label: '大于' },
  { value: '>=', label: '大于等于' },
  { value: '<', label: '小于' },
  { value: '<=', label: '小于等于' }
]
const listOperators = [
  { value: 'IN', label: '包含' },
  { value: 'NOT IN', label: '不包含' }
]

const showFilterEditor = ref(false)
const editingFilter = ref<FilterCondition | null>(null)
const editingFilterFromAdvanced = ref(false)
const filterEditorCommitted = ref(false)
const editingFilterSnapshot = ref<FilterCondition | null>(null)
const manualInputText = ref('')
const manualDelimiter = ref<',' | ';' | ' ' | '\n'>(',')
const inInputMode = ref<'list' | 'subquery'>('list')
const subquerySql = ref('')
const subqueryExclude = ref(false)
const showAsChartFilter = ref(false)
const showAdvancedFieldPicker = ref(false)
const advancedFieldKeyword = ref('')
const pickingAdvancedFilter = ref<FilterCondition | null>(null)
const showAdvancedFilterDialog = ref(false)
const advancedFilterTree = ref<AdvancedFilterGroup | null>(null)
const advancedFilterDraft = ref<AdvancedFilterGroup | null>(null)
const advancedFilterDraftScope = ref<AdvancedFilterScope>('detail')
const dateFilterMode = ref<'dynamic' | 'fixed' | 'single'>('dynamic')
const fixedDateRange = ref<string[]>([])
const singleDateValue = ref('')

const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate())
const shiftDays = (base: Date, offset: number) => {
  const shifted = new Date(base)
  shifted.setDate(shifted.getDate() + offset)
  return shifted
}
const monthStart = (date: Date) => new Date(date.getFullYear(), date.getMonth(), 1)
const monthEnd = (date: Date) => new Date(date.getFullYear(), date.getMonth() + 1, 0)

const fixedDateShortcuts = [
  {
    text: '今天',
    value: () => {
      const today = startOfDay(new Date())
      return [today, today]
    }
  },
  {
    text: '昨天',
    value: () => {
      const yesterday = startOfDay(shiftDays(new Date(), -1))
      return [yesterday, yesterday]
    }
  },
  {
    text: '最近7天',
    value: () => [startOfDay(shiftDays(new Date(), -6)), startOfDay(new Date())]
  },
  {
    text: '最近30天',
    value: () => [startOfDay(shiftDays(new Date(), -29)), startOfDay(new Date())]
  },
  {
    text: '本月',
    value: () => [startOfDay(monthStart(new Date())), startOfDay(new Date())]
  },
  {
    text: '上月',
    value: () => {
      const now = new Date()
      const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1)
      return [startOfDay(monthStart(lastMonth)), startOfDay(monthEnd(lastMonth))]
    }
  }
]

const singleDateShortcuts = [
  { text: '今天', value: () => startOfDay(new Date()) },
  { text: '昨天', value: () => startOfDay(shiftDays(new Date(), -1)) },
  { text: '7天前', value: () => startOfDay(shiftDays(new Date(), -7)) },
  { text: '月初', value: () => startOfDay(monthStart(new Date())) },
  { text: '月末', value: () => startOfDay(monthEnd(new Date())) }
]

interface DateQuickPreset {
  label: string
  operator: string
  expression: string
}

const dateQuickPresets: DateQuickPreset[] = [
  { label: '最近 1 天', operator: '>=', expression: 'DATE_SUB(CURRENT_DATE, INTERVAL 1 DAY)' },
  { label: '最近 7 天', operator: '>=', expression: 'DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)' },
  { label: '最近 30 天', operator: '>=', expression: 'DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)' },
  { label: '本月', operator: '>=', expression: "DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')" },
  { label: '上月', operator: 'BETWEEN', expression: "DATE_FORMAT(DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH), '%Y-%m-01') AND LAST_DAY(DATE_SUB(CURRENT_DATE, INTERVAL 1 MONTH))" },
  { label: '今年', operator: '>=', expression: "DATE_FORMAT(CURRENT_DATE, '%Y-01-01')" },
]

const isDatePresetActive = (preset: DateQuickPreset) => {
  const f = editingFilter.value
  if (!f) return false
  if ((f.operator || '').toUpperCase() !== preset.operator.toUpperCase()) return false
  if (f.valueType !== 'expression') return false
  return (f.rightExpression || '').trim() === preset.expression
}

const currentFilterOperatorOptions = computed(() => {
  const f = editingFilter.value
  if (!f) return [...operators, ...listOperators]
  return f.valueType === 'field_ref' ? fieldRefOperators : [...operators, ...listOperators]
})

const getFilterColumnType = (f: FilterCondition | null) => {
  if (!f?.table || !f?.column) return ''
  const table = tables.value.find(t => t.table_name === f.table)
  const col = table?.columns?.find(c => c.name === f.column)
  return `${col?.mysql_type || col?.type || ''}`.toLowerCase()
}

const isDateLikeType = (rawType: string) => /(date|time|timestamp|datetime)/i.test(rawType || '')
const isDateTimeType = (rawType: string) => /(time|timestamp|datetime)/i.test(rawType || '')

const isEditingDateField = computed(() => {
  if (!editingFilter.value) return false
  if (editingFilter.value.aggFunc) return false
  return isDateLikeType(getFilterColumnType(editingFilter.value))
})

const isEditingDateTimeField = computed(() => {
  if (!editingFilter.value) return false
  if (editingFilter.value.aggFunc) return false
  return isDateTimeType(getFilterColumnType(editingFilter.value))
})

const currentFixedDatePickerType = computed(() => (isEditingDateTimeField.value ? 'datetimerange' : 'daterange'))
const currentSingleDatePickerType = computed(() => (isEditingDateTimeField.value ? 'datetime' : 'date'))
const currentDateValueFormat = computed(() => (isEditingDateTimeField.value ? 'YYYY-MM-DD HH:mm:ss' : 'YYYY-MM-DD'))
const currentStartPlaceholder = computed(() => (isEditingDateTimeField.value ? '开始时间' : '开始日期'))
const currentEndPlaceholder = computed(() => (isEditingDateTimeField.value ? '结束时间' : '结束日期'))
const currentSinglePlaceholder = computed(() => (isEditingDateTimeField.value ? '请选择日期时间' : '请选择日期'))

const advancedOutsideFilters = computed(() => {
  const leafIds = getAdvancedLeafIdSet(advancedFilterTree.value)
  if (leafIds.size === 0) return []
  return filters.value.filter(f => !leafIds.has(f.id))
})

const appendFilterToAdvancedTree = (filter: FilterCondition) => {
  if (!advancedFilterTree.value) return
  appendFilterToAdvancedTreeUtil(advancedFilterTree.value, filter)
}

const removeFilterFromAdvancedTree = (filterId: string) => {
  advancedFilterTree.value = removeFilterFromAdvancedTreeUtil(advancedFilterTree.value, filterId)
}

const syncAdvancedTreeLeafByFilter = (filter: FilterCondition) => {
  syncAdvancedTreeLeafByFilterUtil(advancedFilterTree.value, filter)
}

const createDefaultDetailFilter = (): FilterCondition => {
  const fromDimension = dimensions.value[0]
  if (fromDimension) {
    return {
      id: createNodeId(),
      table: fromDimension.table,
      tableAlias: getTableAlias(fromDimension),
      column: fromDimension.column,
      semanticName: fromDimension.semanticName,
      displayName: fromDimension.displayName || fromDimension.column,
      expression: fromDimension.expression,
      operator: '=',
      value: '',
      valueType: 'literal',
      logic: 'AND'
    }
  }
  const firstTableName = selectedTables.value[0]?.name || tables.value[0]?.table_name || ''
  const firstTableAlias = selectedTables.value[0] ? getSelectedTableAlias(selectedTables.value[0]) : firstTableName
  const tableInfo = tables.value.find(t => t.table_name === firstTableName)
  const firstCol = tableInfo?.columns?.[0]
  if (!firstTableName || !firstCol) {
    return {
      id: createNodeId(),
      table: '',
      column: '',
      operator: '=',
      value: '',
      valueType: 'literal',
      logic: 'AND'
    }
  }
  return {
    id: createNodeId(),
    table: firstTableName,
    tableAlias: firstTableAlias,
    column: firstCol.name,
    displayName: firstCol.description || firstCol.name,
    operator: '=',
    value: '',
    valueType: 'literal',
    logic: 'AND'
  }
}

const createEmptyFilter = (): FilterCondition => ({
  id: createNodeId(),
  table: '',
  column: '',
  displayName: '',
  operator: '=',
  value: '',
  valueType: 'literal',
  logic: 'AND'
})

const createDefaultAggregateFilter = (): FilterCondition => {
  const fromAgg = aggregations.value[0]
  if (fromAgg) {
    return {
      id: createNodeId(),
      table: fromAgg.table,
      tableAlias: getTableAlias(fromAgg),
      column: fromAgg.column,
      semanticName: fromAgg.semanticName,
      displayName: fromAgg.displayName || fromAgg.column,
      expression: fromAgg.expression,
      operator: '=',
      value: '',
      valueType: 'literal',
      logic: 'AND',
      aggFunc: fromAgg.func,
      aggId: fromAgg.id
    }
  }
  return createEmptyFilter()
}

// 根据字段类型返回图标类型（DataWind 风格）
const getFieldType = (col: ColumnInfo): 'string' | 'number' | 'date' => {
  const t = (col.mysql_type || col.type || '').toLowerCase()
  if (/int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/.test(t)) return 'number'
  if (/date|time|timestamp|datetime|year/.test(t)) return 'date'
  return 'string'
}

const isDimensionDateField = (item: Pick<DimensionField, 'table' | 'column'>) => {
  const column = getTableColumns(item.table).find(col => col.name === item.column)
  if (!column) return false
  return getFieldType(column) === 'date'
}

const getDefaultAggregationFunc = (field: { table?: string; column?: string; type?: string }): AggregationField['func'] => {
  if (field.type) {
    return /int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/i.test(field.type) ? 'SUM' : 'COUNT_DISTINCT'
  }
  const column = getTableColumns(field.table || '').find(col => col.name === field.column)
  return column && getFieldType(column) === 'number' ? 'SUM' : 'COUNT_DISTINCT'
}

// ===== @mention 选择功能 =====
type AtRowType = 'dimension' | 'aggregation' | 'filter' | 'order'
interface AtItem {
  name: string
  label: string
  table?: string
  tableAlias?: string
  tableLabel?: string
  type?: string
  fieldType: 'table' | 'string' | 'number' | 'date'
}

const atState = reactive({
  activeRow: '' as AtRowType | '',
  inputText: '',
  dropdownVisible: false,
  selectedIndex: 0,
})
const atInputRef = ref<HTMLInputElement | null>(null)

// 计算 @mention 下拉列表项
const atFilteredItems = computed<AtItem[]>(() => {
  const keyword = atState.inputText.replace(/^@/, '').toLowerCase()

  // 维度/指标/筛选行：显示已选表的字段（按表分组）
  const items: AtItem[] = []
  for (const t of selectedTables.value) {
    const cols = getTableColumns(t.name)
    // 多表时插入分组标题
    if (selectedTables.value.length > 1) {
      const groupLabel = t.label || t.name
      if (!keyword || cols.some(col => (col.name.toLowerCase().includes(keyword) || (col.description || '').toLowerCase().includes(keyword)))) {
        items.push({
          name: `__group__${t.name}`,
          label: groupLabel,
          table: t.name,
          tableAlias: getSelectedTableAlias(t),
          tableLabel: t.label,
          fieldType: 'table',
        })
      }
    }
    for (const col of cols) {
      if (!keyword || col.name.toLowerCase().includes(keyword) || (col.description || '').toLowerCase().includes(keyword)) {
        items.push({
          name: col.name,
          label: col.description || col.name,
          table: t.name,
          tableAlias: getSelectedTableAlias(t),
          tableLabel: t.label,
          type: col.mysql_type || col.type || '',
          fieldType: getFieldType(col),
        })
      }
    }
  }
  return items
})

// 输入变化 — 检测 @ 触发下拉
const handleAtInputChange = (row: AtRowType) => {
  const v = atState.inputText
  if (v.includes('@')) {
    if (selectedTables.value.length === 0) {
      ElMessage.warning('请先添加数据表')
      atState.inputText = ''
      return
    }
    atState.activeRow = row
    atState.dropdownVisible = true
    // 跳过分组标题，选中第一个可选项
    const firstSelectable = atFilteredItems.value.findIndex(i => !i.name.startsWith('__group__'))
    atState.selectedIndex = firstSelectable >= 0 ? firstSelectable : 0
  } else {
    atState.dropdownVisible = false
  }
}

// 键盘导航（跳过分组标题）
const handleAtKeydown = (e: KeyboardEvent) => {
  if (!atState.dropdownVisible) return
  const items = atFilteredItems.value
  const len = items.length
  const isGroup = (idx: number) => items[idx]?.name.startsWith('__group__')

  if (e.key === 'ArrowDown') {
    e.preventDefault()
    let next = atState.selectedIndex + 1
    while (next < len && isGroup(next)) next++
    if (next < len) atState.selectedIndex = next
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    let prev = atState.selectedIndex - 1
    while (prev >= 0 && isGroup(prev)) prev--
    if (prev >= 0) atState.selectedIndex = prev
  } else if (e.key === 'Enter') {
    e.preventDefault()
    const item = items[atState.selectedIndex]
    if (item && !item.name.startsWith('__group__')) handleAtSelect(item)
  } else if (e.key === 'Escape') {
    closeAtDropdown()
  }
}

// 选择项
const handleAtSelect = (item: AtItem) => {
  const row = atState.activeRow
  const tableAlias = item.tableAlias || item.table
  if (row === 'dimension') {
    if (!dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(tableAlias, item.table, item.name))) {
      dimensions.value.push({
        id: `${Date.now()}`,
        table: item.table!,
        tableAlias,
        column: item.name,
        semanticName: item.label,
        displayName: item.label,
        label: `${item.tableLabel}.${item.label}`,
        transform: 'raw'
      })
    }
  } else if (row === 'aggregation') {
    const targetFunc = getDefaultAggregationFunc({ table: item.table, column: item.name, type: item.type })
    if (!canUseAggregationFunc(targetFunc)) {
      showAggregationModeConflictMessage(targetFunc)
      closeAtDropdown()
      return
    }
    if (!aggregations.value.find(a => buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(tableAlias, item.table, item.name) && a.func === targetFunc)) {
      aggregations.value.push({
        id: `${Date.now()}`,
        table: item.table!,
        tableAlias,
        column: item.name,
        semanticName: item.label,
        displayName: item.label,
        label: `${item.tableLabel}.${item.label}`,
        func: targetFunc
      })
    }
  } else if (row === 'filter') {
    const nextFilter: FilterCondition = {
      id: `${Date.now()}`,
      table: item.table!,
      tableAlias,
      column: item.name,
      semanticName: item.label,
      operator: '=',
      value: '',
      valueType: 'literal',
      logic: 'AND'
    }
    filters.value.push(nextFilter)
  } else if (row === 'order') {
    const existed = orderBy.value.find(o => buildFieldKey(o.tableAlias, o.table, o.column) === buildFieldKey(tableAlias, item.table, item.name) && !o.aggFunc)
    if (!existed) {
      orderBy.value.push({
        id: `${Date.now()}`,
        table: item.table!,
        tableAlias,
        column: item.name,
        semanticName: item.label,
        direction: 'ASC'
      })
    }
  }
  closeAtDropdown()
}

// 关闭下拉
const closeAtDropdown = () => {
  atState.activeRow = ''
  atState.inputText = ''
  atState.dropdownVisible = false
}

const handleAtInputBlur = () => {
  window.setTimeout(() => closeAtDropdown(), 150)
}

// 加载表结构
const loadTables = async () => {
  if (!props.datasourceId) return
  loading.value = true
  try {
    const response = await getDatasourceSchema(props.datasourceId)
    if (response.data?.tables) {
      tables.value = response.data.tables as TableInfo[]
    }
  } catch (err) {
    console.error('加载表结构失败:', err)
    ElMessage.error('加载表结构失败')
  } finally {
    loading.value = false
  }
}

// 拖拽开始 - 表
const handleTableDragStart = (table: TableInfo, e: DragEvent) => {
  draggedItem.value = {
    type: 'table',
    data: { name: table.table_name, alias: table.table_name, label: table.description || table.table_name }
  }
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'copy'
  }
}

// 拖拽开始 - 字段
const handleFieldDragStart = (table: TableInfo, field: ColumnInfo, e: DragEvent) => {
  draggedItem.value = {
    type: 'field',
    data: {
      table: table.table_name,
      tableAlias: table.table_name,
      tableLabel: table.description || table.table_name,
      column: field.name,
      label: field.description || field.name,
      type: field.mysql_type || field.type
    }
  }
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'copy'
  }
}

// 拖拽结束
const handleDragEnd = () => {
  draggedItem.value = null
  dropTargetActive.value = null
}

// 从外部侧边栏开始拖拽（供 index.vue 调用）
const handleSidebarDragStart = (type: 'field', data: BuilderDragFieldData, e: DragEvent) => {
  draggedItem.value = { type, data }
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'copy'
  }
}

// 从外部侧边栏结束拖拽（供 index.vue 调用）
const handleSidebarDragEnd = () => {
  draggedItem.value = null
  dropTargetActive.value = null
}

const getRelationshipLeftAlias = (rel: SemanticRelationship) => String(rel.left_alias || rel.left_table || '').trim()
const getRelationshipRightAlias = (rel: SemanticRelationship) => String(rel.right_alias || rel.right_table || '').trim()

const resolvePhysicalTableByAlias = (alias: string) => {
  const selected = selectedTables.value.find(table => getSelectedTableAlias(table) === alias)
  if (selected) return selected.name
  const semanticConfig = props.semanticConfig
  const dim = semanticConfig?.dimensions?.find(item => getTableAlias({ table: item.table, tableAlias: item.table_alias }) === alias)
  if (dim) return dim.table
  const metric = semanticConfig?.metrics?.find(item => getTableAlias({ table: item.table, tableAlias: item.table_alias }) === alias)
  if (metric) return metric.table
  const rel = semanticConfig?.relationships?.find(item => getRelationshipLeftAlias(item) === alias || getRelationshipRightAlias(item) === alias)
  if (!rel) return alias
  return getRelationshipLeftAlias(rel) === alias ? rel.left_table : rel.right_table
}

const canConnectTableInSemanticMode = (target: string | { table?: string; tableAlias?: string }) => {
  if (!props.semanticConfig) return true
  const targetTable = typeof target === 'string' ? target : String(target.table || '').trim()
  const targetAlias = typeof target === 'string' ? target : getTableAlias(target)
  const used = new Set<string>()
  dimensions.value.forEach(d => used.add(getTableAlias(d)))
  aggregations.value.forEach(a => used.add(getTableAlias(a)))
  filters.value.forEach(f => used.add(getTableAlias(f)))
  orderBy.value.forEach(o => used.add(getTableAlias(o)))
  if (used.size === 0 || used.has(targetAlias)) return true

  const adjacency = new Map<string, string[]>()
  props.semanticConfig.relationships.forEach(rel => {
    const leftAlias = getRelationshipLeftAlias(rel)
    const rightAlias = getRelationshipRightAlias(rel)
    if (!adjacency.has(leftAlias)) adjacency.set(leftAlias, [])
    if (!adjacency.has(rightAlias)) adjacency.set(rightAlias, [])
    adjacency.get(leftAlias)?.push(rightAlias)
    adjacency.get(rightAlias)?.push(leftAlias)
  })

  const visited = new Set<string>([targetAlias || targetTable])
  const queue = [targetAlias || targetTable]
  while (queue.length > 0) {
    const curr = queue.shift() as string
    if (used.has(curr)) return true
    const neighbors = adjacency.get(curr) || []
    neighbors.forEach(next => {
      if (visited.has(next)) return
      visited.add(next)
      queue.push(next)
    })
  }
  return false
}

const addFieldToDimension = (field: BuilderDragFieldData) => {
  const { table, column, label, aggFunc, expression, displayName, semanticName, metricKind } = field
  // 派生/复合指标是纯逻辑口径，不支持作为维度
  if (metricKind === 'derived' || metricKind === 'composite') {
    ElMessage.warning('扩展指标不支持作为维度，请拖入指标区')
    return
  }
  const tableAlias = getTableAlias(field)
  if (!table || !column) return
  if (!canConnectTableInSemanticMode({ table, tableAlias })) {
    ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善语义模型关系')
    return
  }
  // 支持指标 -> 维度：自动转换为非聚合字段
  if (aggFunc && column === '*') {
    ElMessage.warning('COUNT(*) 类型指标无法转换为维度，请选择具体字段')
    return
  }
  if (aggFunc) {
    const normalizedCol = String(column || '').replace(/`/g, '').trim()
    if (!/^[\w\u4e00-\u9fa5]+$/.test(normalizedCol)) {
      ElMessage.warning('复杂指标表达式无法直接转换为维度，请手工选择具体字段')
      return
    }
  }

  const dimensionExpression = aggFunc ? `${quoteIdentifier(table)}.${quoteIdentifier(column)}` : expression
  const dimensionLabel = aggFunc ? (displayName || label || column) : label
  if (!dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(tableAlias, table, column))) {
    dimensions.value.push({
      id: `${Date.now()}`,
      table,
      tableAlias,
      column,
      semanticName,
      expression: dimensionExpression,
      displayName,
      label: dimensionLabel || column,
      transform: 'raw'
    })
    if (aggFunc) {
      ElMessage.success('已将指标转换为维度')
    }
  }
}

const addFieldToAggregation = (field: BuilderDragFieldData) => {
  const { table, column, label, aggFunc, displayName, expression, semanticName, metricKind } = field
  const isLogicalMetric = metricKind === 'derived' || metricKind === 'composite'
  const tableAlias = getTableAlias(field)
  // 派生/复合指标按 semanticName 交给后端解析，无物理表字段
  if (!isLogicalMetric) {
    if (!table || !column) return
    if (!canConnectTableInSemanticMode({ table, tableAlias })) {
      ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善语义模型关系')
      return
    }
  }
  const targetFunc = (isLogicalMetric ? 'RAW' : (aggFunc || getDefaultAggregationFunc({ table, column, type: field?.type }))) as AggregationField['func']
  if (!canUseAggregationFunc(targetFunc)) {
    showAggregationModeConflictMessage(targetFunc)
    return
  }
  // 去重 key 必须基于条目自身判定逻辑/物理，不能读拖拽 field 的闭包，
  // 否则第二个逻辑指标（或 RAW 物理指标）会被误判为已存在而静默跳过
  const isLogicalAggregationItem = (item: { table?: string; column?: string; semanticName?: string; metricKind?: string }) =>
    item.metricKind === 'derived' || item.metricKind === 'composite' ||
    (!item.table && !item.column && !!item.semanticName)
  const dedupKey = (item: { tableAlias?: string; table?: string; column?: string; semanticName?: string; displayName?: string; metricKind?: string }) => {
    if (isLogicalAggregationItem(item)) {
      return `logical:${item.semanticName || item.displayName}`
    }
    return buildFieldKey(item.tableAlias, item.table || '', item.column || '')
  }
  const currentKey = isLogicalMetric
    ? `logical:${semanticName || displayName}`
    : buildFieldKey(tableAlias, table, column)
  if (!aggregations.value.find(a => dedupKey(a) === currentKey && a.func === targetFunc && (a.expression || '') === (expression || ''))) {
    aggregations.value.push({
      id: `${Date.now()}`,
      table,
      tableAlias,
      column,
      semanticName,
      displayName,
      expression,
      label: label || column,
      func: targetFunc,
      metricKind
    })
  }
}

// 放置到维度区域
const handleDropToDimension = (e: DragEvent) => {
  e.preventDefault()
  dropTargetActive.value = null
  if (draggedItem.value?.type === 'field') {
    addFieldToDimension(draggedItem.value.data)
  }
  draggedItem.value = null
}

// 放置到聚合区域
const handleDropToAggregation = (e: DragEvent) => {
  e.preventDefault()
  dropTargetActive.value = null
  if (draggedItem.value?.type === 'field') {
    addFieldToAggregation(draggedItem.value.data)
  }
  draggedItem.value = null
}

const handleAggregationFuncChange = (agg: AggregationField, nextFuncRaw: string) => {
  const nextFunc = normalizeAggFunc(nextFuncRaw)
  if (nextFunc === agg.func) return
  if (!canUseAggregationFunc(nextFunc, agg.id)) {
    showAggregationModeConflictMessage(nextFunc)
    return
  }
  agg.func = nextFunc
}

// 拖拽经过
const handleDragOver = (e: DragEvent, target: 'dimension' | 'aggregation' | 'filter' | 'order') => {
  e.preventDefault()
  if (draggedItem.value?.type === 'field') {
    dropTargetActive.value = target
  }
}

// 拖拽离开
const handleDragLeave = () => {
  dropTargetActive.value = null
}

// 获取表的字段列表
const getTableColumns = (tableName: string) => {
  const tableInfo = tables.value.find(t => t.table_name === tableName)
  return tableInfo?.columns || []
}

const quoteIdentifier = (name: string) => {
  const safe = (name || '').replace(/`/g, '``')
  return `\`${safe}\``
}

// 移除维度
const removeDimension = (index: number) => {
  dimensions.value.splice(index, 1)
}

// 移除聚合
const removeAggregation = (index: number) => {
  aggregations.value.splice(index, 1)
}

const pruneInvalidFieldReferences = () => {
  const validDetailFields = new Set<string>()
  selectedTables.value.forEach((table) => {
    getTableColumns(table.name).forEach((column) => {
      validDetailFields.add(buildFieldKey(getSelectedTableAlias(table), table.name, column.name))
    })
  })
  const hasAggregation = (item: { table: string; tableAlias?: string; column: string; aggFunc?: string; aggId?: string }) => {
    if (item.aggId && aggregations.value.some(agg => agg.id === item.aggId)) return true
    return aggregations.value.some(agg => buildFieldKey(agg.tableAlias, agg.table, agg.column) === buildFieldKey(item.tableAlias, item.table, item.column) && agg.func === item.aggFunc)
  }
  const nextFilters = filters.value.filter((item) => {
    if (item.aggFunc) return hasAggregation(item)
    return validDetailFields.has(buildFieldKey(item.tableAlias, item.table, item.column))
  })
  orderBy.value = orderBy.value.filter((item) => {
    if (item.aggFunc) return hasAggregation(item)
    return validDetailFields.has(buildFieldKey(item.tableAlias, item.table, item.column))
  })
  if (nextFilters.length !== filters.value.length && advancedFilterTree.value) {
    advancedFilterTree.value = null
  }
  filters.value = nextFilters
}

const showSectionSettingComingSoon = (section: '维度' | '指标' | '排序') => {
  if (section === '维度') {
    openDimensionManager()
    return
  }
  if (section === '指标') {
    openAggregationManager()
    return
  }
  ElMessage.info(`${section}系统配置功能待实现`)
}

const openDimensionManager = () => {
  if (dimensions.value.length === 0) {
    ElMessage.warning('请先添加维度')
    return
  }
  dimensionDrafts.value = dimensions.value.map(item => ({ ...item, selected: false }))
  showDimensionManager.value = true
}

const openAggregationManager = () => {
  if (aggregations.value.length === 0) {
    ElMessage.warning('请先添加指标')
    return
  }
  aggregationDrafts.value = aggregations.value.map(item => ({ ...item, selected: false }))
  showAggregationManager.value = true
}

const moveDimensionDraft = (index: number, direction: -1 | 1) => {
  const targetIndex = index + direction
  if (targetIndex < 0 || targetIndex >= dimensionDrafts.value.length) return
  const list = [...dimensionDrafts.value]
  const current = list[index]
  const target = list[targetIndex]
  if (!current || !target) return
  list[index] = target
  list[targetIndex] = current
  dimensionDrafts.value = list
}

const moveAggregationDraft = (index: number, direction: -1 | 1) => {
  const targetIndex = index + direction
  if (targetIndex < 0 || targetIndex >= aggregationDrafts.value.length) return
  const list = [...aggregationDrafts.value]
  const current = list[index]
  const target = list[targetIndex]
  if (!current || !target) return
  list[index] = target
  list[targetIndex] = current
  aggregationDrafts.value = list
}

const removeSelectedDimensionDrafts = () => {
  const nextList = dimensionDrafts.value.filter(item => !item.selected)
  if (nextList.length === dimensionDrafts.value.length) {
    ElMessage.warning('请先勾选要删除的维度')
    return
  }
  dimensionDrafts.value = nextList
}

const removeSelectedAggregationDrafts = () => {
  const nextList = aggregationDrafts.value.filter(item => !item.selected)
  if (nextList.length === aggregationDrafts.value.length) {
    ElMessage.warning('请先勾选要删除的指标')
    return
  }
  aggregationDrafts.value = nextList
}

const saveDimensionManager = () => {
  dimensions.value = dimensionDrafts.value.map(draft => {
    const item = { ...draft }
    delete item.selected
    return {
      ...item,
      displayName: (item.displayName || '').trim() || item.column,
      label: item.label || item.column,
      transform: isDimensionDateField(item) ? (item.transform || 'raw') : 'raw'
    }
  })
  pruneInvalidFieldReferences()
  showDimensionManager.value = false
}

const saveAggregationManager = () => {
  const nextList = aggregationDrafts.value.map(draft => {
    const item = { ...draft }
    delete item.selected
    return {
      ...item,
      displayName: (item.displayName || '').trim() || item.column,
      label: item.label || item.column,
      func: normalizeAggFunc(item.func)
    }
  })
  const hasRaw = nextList.some(item => isRawAggFunc(item.func))
  const hasAgg = nextList.some(item => !isRawAggFunc(item.func))
  if (hasRaw && hasAgg) {
    ElMessage.warning('指标区不支持原始值与聚合函数混用，请统一函数类型后再保存')
    return
  }
  aggregations.value = nextList
  pruneInvalidFieldReferences()
  showAggregationManager.value = false
}

const removeFilter = (index: number) => {
  const target = filters.value[index]
  if (!target) return
  removeFilterFromAdvancedTree(target.id)
  filters.value.splice(index, 1)
}

const removeFilterById = (filterId: string) => {
  const index = filters.value.findIndex(f => f.id === filterId)
  if (index < 0) return
  removeFilter(index)
}


const stripFieldPrefix = (name?: string) => {
  const text = (name || '').trim()
  const idx = text.lastIndexOf('.')
  return idx > 0 ? text.slice(idx + 1) : text
}

const getDimensionDisplayName = (d: DimensionField) => {
  return stripFieldPrefix(d.displayName || d.label || d.column) || d.column
}

const getMetricDisplayName = (a: AggregationField) => {
  return stripFieldPrefix(a.displayName || a.label || a.column) || a.column
}

const isLogicalAggregation = (a: AggregationField) => a.metricKind === 'derived' || a.metricKind === 'composite'

// 逻辑口径指标（派生/复合）没有聚合函数概念，chip 显示不带函数前缀
const formatMetricChipLabel = (a: AggregationField) => {
  const name = getMetricDisplayName(a)
  return isLogicalAggregation(a) ? name : `${getAggFuncLabel(a.func)}(${name})`
}

const getAggFuncLabel = (func?: string) => {
  const key = String(func || '').toUpperCase()
  return aggFunctions.find(item => item.value === key)?.label || key
}

const resultData = computed(() => analyzedResultData.value || rawResultData.value)
const {
  selectedAnalysisType,
  compareType,
  resultSortField,
  resultSortDirection,
  resultTopN,
  topNField,
  topNDirection,
  percentMetricField,
  percentScope,
  percentGroupField,
  compareMetricField,
  compareBaselineMode,
  hasAnalyzedResult,
  resultAnalysisFieldOptions,
  resultAnalysisMetricOptions,
  resultAnalysisDimensionOptions,
  isCompareAnalysisApplied,
  isCompareAnalysisDisabled,
  resultSemanticParams,
  runCompareAnalysis,
  runSortAnalysis,
  runTopNAnalysis,
  runTotalAnalysis,
  runPercentAnalysis,
  runCompareBaselineAnalysis,
  cancelResultAnalysis,
  resetResultAnalysis,
} = useQueryBuilderResultAnalysis({
  resultData,
  analyzedResultData,
  dimensions,
  aggregations,
  getDimensionDisplayName,
  getMetricDisplayName,
  getCompareAnalysisWarning,
})

const dimensionFieldRefOptions = computed(() => {
  const dedup = new Map<string, { value: string; label: string }>()
  dimensions.value.forEach((d) => {
    const key = buildFieldKey(d.tableAlias, d.table, d.column)
    if (dedup.has(key)) return
    dedup.set(key, {
      value: key,
      label: getDimensionDisplayName(d)
    })
  })
  return Array.from(dedup.values())
})

const filterFieldRefOptions = computed(() => {
  if (editingFilter.value?.aggFunc) return []
  return dimensionFieldRefOptions.value
})

const isListOperator = (operator: string) => operator === 'IN' || operator === 'NOT IN'

const handleFilterOperatorChange = (f: FilterCondition, operator: string) => {
  f.operator = operator
  if (isListOperator(operator)) {
    f.valueType = 'literal'
    inInputMode.value = 'list'
    subqueryExclude.value = operator === 'NOT IN'
    return
  }
  if (Array.isArray(f.value)) {
    f.value = ''
  }
  if (operator !== 'BETWEEN') {
    fixedDateRange.value = []
  }
  if (operator !== '=') {
    singleDateValue.value = ''
  }
}

const applyDatePreset = (preset: DateQuickPreset) => {
  const f = editingFilter.value
  if (!f) return
  f.operator = preset.operator
  f.valueType = 'expression'
  f.rightExpression = preset.expression
  f.value = ''
  dateFilterMode.value = 'dynamic'
}

const openFilterEditor = (f: FilterCondition) => {
  filterEditorCommitted.value = false
  editingFilterSnapshot.value = cloneFilterCondition(f)
  editingFilter.value = f
  if (!f.valueType) f.valueType = 'literal'
  if (f.aggFunc && f.valueType === 'field_ref') {
    f.valueType = 'literal'
  }
  if (f.valueType === 'field_ref' && (!f.rightTable || !f.rightColumn)) {
    const first = filterFieldRefOptions.value[0]
    if (first) {
      const parsed = parseFieldValue(first.value)
      f.rightTable = parsed.table
      f.rightTableAlias = parsed.tableAlias || parsed.table
      f.rightColumn = parsed.column
    }
  }
  showAsChartFilter.value = Boolean(f.asChartFilter)
  const op = (f.operator || '').toUpperCase()
  if (isListOperator(op) && f.valueType === 'expression' && /select/i.test(f.rightExpression || '')) {
    inInputMode.value = 'subquery'
    subquerySql.value = f.rightExpression || ''
    subqueryExclude.value = op === 'NOT IN'
    manualInputText.value = ''
  } else if (isListOperator(op)) {
    inInputMode.value = 'list'
    const arr = ensureArrayValues(f.value)
    manualInputText.value = arr.join('\n')
    subquerySql.value = ''
    subqueryExclude.value = op === 'NOT IN'
  } else {
    inInputMode.value = 'list'
    manualInputText.value = ''
    subquerySql.value = ''
    subqueryExclude.value = false
  }
  if (isDateLikeType(getFilterColumnType(f))) {
    const op = (f.operator || '').toUpperCase()
    if (op === 'BETWEEN' && Array.isArray(f.value) && f.value.length === 2) {
      dateFilterMode.value = 'fixed'
      fixedDateRange.value = [String(f.value[0] || ''), String(f.value[1] || '')]
    } else if (op === 'BETWEEN' && f.valueType === 'expression' && (f.rightExpression || '').includes('AND')) {
      dateFilterMode.value = 'dynamic'
      fixedDateRange.value = []
    } else if (op === '=' && !Array.isArray(f.value) && String(f.value || '').trim()) {
      dateFilterMode.value = 'single'
      singleDateValue.value = String(f.value || '').trim()
      fixedDateRange.value = []
    } else {
      dateFilterMode.value = 'dynamic'
      fixedDateRange.value = []
      singleDateValue.value = ''
    }
  }
  showFilterEditor.value = true
}

const openFilterEditorFromAdvanced = (f: FilterCondition) => {
  editingFilterFromAdvanced.value = true
  openFilterEditor(f)
}

const closeFilterEditor = () => {
  if (!filterEditorCommitted.value && editingFilter.value && editingFilterSnapshot.value) {
    Object.assign(editingFilter.value, editingFilterSnapshot.value)
  }
  showFilterEditor.value = false
  editingFilter.value = null
  editingFilterFromAdvanced.value = false
  editingFilterSnapshot.value = null
  manualInputText.value = ''
  inInputMode.value = 'list'
  subquerySql.value = ''
  subqueryExclude.value = false
  fixedDateRange.value = []
  singleDateValue.value = ''
  dateFilterMode.value = 'dynamic'
  filterEditorCommitted.value = false
}

const handleFilterEditorConfirm = () => {
  if (!editingFilter.value) return
  const f = editingFilter.value
  if (isEditingDateField.value && !isListOperator(f.operator) && !['IS NULL', 'IS NOT NULL'].includes(f.operator)) {
    if (dateFilterMode.value === 'fixed') {
      if (fixedDateRange.value.length !== 2 || !fixedDateRange.value[0] || !fixedDateRange.value[1]) {
        ElMessage.warning('请选择开始和结束日期')
        return
      }
      f.operator = 'BETWEEN'
      f.valueType = 'literal'
      f.value = [fixedDateRange.value[0] || '', fixedDateRange.value[1] || '']
      f.rightExpression = undefined
    } else if (dateFilterMode.value === 'single') {
      if (!singleDateValue.value) {
        ElMessage.warning('请选择日期')
        return
      }
      if ((f.operator || '').toUpperCase() === 'BETWEEN') {
        f.operator = '='
      }
      f.valueType = 'literal'
      f.value = singleDateValue.value
      f.rightExpression = undefined
    } else {
      const expr = (f.rightExpression || '').trim()
      if (!(f.valueType === 'expression' && expr)) {
        applyDatePreset(dateQuickPresets[1]!)
      }
    }
  }
  if (isListOperator(f.operator)) {
    f.operator = subqueryExclude.value ? 'NOT IN' : 'IN'
    if (inInputMode.value === 'subquery') {
      const subSql = subquerySql.value.trim()
      if (!subSql) {
        ElMessage.warning('请输入子查询语句')
        return
      }
      f.valueType = 'expression'
      f.rightExpression = subSql
      f.value = ''
    } else {
      const values = parseManualValues(manualInputText.value, manualDelimiter.value)
      if (values.length === 0) {
        ElMessage.warning('请至少输入一个值')
        return
      }
      f.value = values
      f.valueType = 'literal'
      f.rightExpression = undefined
    }
  }
  f.asChartFilter = showAsChartFilter.value
  filterEditorCommitted.value = true
  showFilterEditor.value = false
}

const handleFilterRightFieldChange = (f: FilterCondition, value: string) => {
  const parsed = parseFieldValue(value)
  f.rightTable = parsed.table
  f.rightTableAlias = parsed.tableAlias || parsed.table
  f.rightColumn = parsed.column
}

const getFilterRightFieldValue = (f: FilterCondition) => {
  if (!f.rightTable || !f.rightColumn) return ''
  return buildFieldKey(f.rightTableAlias, f.rightTable, f.rightColumn)
}

const handleFilterValueTypeChange = (f: FilterCondition, valueType: 'literal' | 'field_ref' | 'expression') => {
  if (f.aggFunc && valueType === 'field_ref') {
    ElMessage.warning('聚合过滤暂不支持字段对字段比较')
    f.valueType = 'literal'
    return
  }
  if (valueType === 'field_ref' && filterFieldRefOptions.value.length === 0) {
    ElMessage.warning('暂无可比较字段，请先添加维度')
    f.valueType = 'literal'
    return
  }
  f.valueType = valueType
  if (valueType === 'field_ref' && !fieldRefOperators.some(op => op.value === f.operator)) {
    f.operator = '='
  }
  if (valueType !== 'field_ref' && ![...operators, ...listOperators].some(op => op.value === f.operator)) {
    f.operator = '='
  }
  if (valueType === 'field_ref' && (!f.rightTable || !f.rightColumn)) {
    const first = filterFieldRefOptions.value[0]
    if (first) {
      handleFilterRightFieldChange(f, first.value)
    }
  }
}

const getOperatorLabel = (operator: string) => {
  const allOps = [...operators, ...fieldRefOperators, ...listOperators]
  return allOps.find(op => op.value === operator)?.label || operator
}

const getFilterFieldLabel = (f: FilterCondition) => {
  if (!f.table || !f.column) return '编辑筛选器'
  if (f.aggId || f.aggFunc) {
    const matchedAgg = f.aggId
      ? aggregations.value.find(a => a.id === f.aggId)
      : aggregations.value.find(a => buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(f.tableAlias, f.table, f.column) && a.func === f.aggFunc)
    if (matchedAgg) return formatMetricChipLabel(matchedAgg)
    const funcLabel = getAggFuncLabel(f.aggFunc)
    if ((f.displayName || '').trim()) return `${funcLabel}(${stripFieldPrefix(f.displayName)})`
    return `${funcLabel || '指标'}(${f.column})`
  }
  const matchedDim = dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(f.tableAlias, f.table, f.column))
  if (matchedDim) return getDimensionDisplayName(matchedDim)
  if ((f.displayName || '').trim()) return stripFieldPrefix(f.displayName)
  const tableInfo = tables.value.find(t => t.table_name === f.table)
  const colInfo = tableInfo?.columns?.find(c => c.name === f.column)
  const colLabel = colInfo?.description || f.column
  return colLabel
}

const getFilterValueLabel = (f: FilterCondition) => {
  if (!f.table || !f.column) return ''
  if (f.operator === 'IS NULL' || f.operator === 'IS NOT NULL') return ''
  if (f.valueType === 'field_ref' && f.rightTable && f.rightColumn) {
    const tableInfo = tables.value.find(t => t.table_name === f.rightTable)
    const tableLabel = getTableRoleLabel(f.rightTable, f.rightTableAlias, tableInfo?.description || f.rightTable)
    const colInfo = tableInfo?.columns?.find(c => c.name === f.rightColumn)
    const colLabel = colInfo?.description || f.rightColumn
    return `${tableLabel}.${colLabel}`
  }
  if (f.valueType === 'expression') {
    const matchedPreset = dateQuickPresets.find(
      preset => preset.operator.toUpperCase() === (f.operator || '').toUpperCase()
        && preset.expression === ((f.rightExpression || '').trim())
    )
    if (matchedPreset) return matchedPreset.label
    if (f.operator === 'BETWEEN' && (f.rightExpression || '').includes('AND')) {
      return '动态时间范围'
    }
    return (f.rightExpression || '').trim() || '表达式'
  }
  if (f.operator === 'BETWEEN' && Array.isArray(f.value) && f.value.length === 2) {
    return `${f.value[0]} ~ ${f.value[1]}`
  }
  if (Array.isArray(f.value)) {
    const arr = f.value
    if (arr.length === 0) return '值'
    if (arr.length === 1) return String(arr[0])
    return `${arr[0]} 等${arr.length}项`
  }
  return f.value || '值'
}

const getAdvancedLeafLabel = (leaf: AdvancedFilterLeaf) => {
  const f = leaf.filter
  if (!f.table || !f.column) return ''
  return `${getFilterFieldLabel(f)} ${getOperatorLabel(f.operator)} ${getFilterValueLabel(f)}`
}

const advancedFirstConfiguredLeaf = computed(() => getFirstConfiguredLeaf(advancedFilterDraft.value))
const advancedEffectiveScope = computed<AdvancedFilterScope | null>(() => {
  const firstLeaf = advancedFirstConfiguredLeaf.value
  return firstLeaf ? getFilterScope(firstLeaf) : null
})
const advancedActiveScope = computed<AdvancedFilterScope>(() => advancedEffectiveScope.value || advancedFilterDraftScope.value)

const advancedAllFieldGroups = computed(() => {
  return [...advancedDimensionFieldsGrouped.value, ...advancedMetricFieldsGrouped.value]
})

const appendCurrentEditingFilterOption = (
  groups: FieldPickerGroup[]
) => {
  const f = editingFilter.value
  if (!f?.table || !f.column) return groups
  const optionValue = f.aggFunc
    ? (f.aggId ? `AGGID:${f.aggId}` : `AGG:${f.aggFunc}:${getTableAlias(f)}.${f.column}`)
    : buildFieldKey(f.tableAlias, f.table, f.column)
  const exists = groups.some(group => group.fields.some(field => field.value === optionValue))
  if (exists) return groups

  const option = { value: optionValue, label: getFilterFieldLabel(f) }
  const targetLabel = f.aggFunc ? '指标' : '维度'
  const copied = groups.map(group => ({ groupLabel: group.groupLabel, fields: [...group.fields] }))
  const target = copied.find(group => group.groupLabel === targetLabel)
  if (target) {
    target.fields.unshift(option)
    return copied
  }
  return [{ groupLabel: targetLabel, fields: [option] }, ...copied]
}

const advancedEditingFieldGroups = computed(() => {
  let baseGroups: FieldPickerGroup[]
  if (!editingFilterFromAdvanced.value) {
    baseGroups = advancedAllFieldGroups.value
  } else if (!advancedEffectiveScope.value) {
    baseGroups = advancedAllFieldGroups.value
  } else {
    baseGroups = advancedEffectiveScope.value === 'aggregate' ? advancedMetricFieldsGrouped.value : advancedDimensionFieldsGrouped.value
  }
  return appendCurrentEditingFilterOption(baseGroups)
})

const advancedFieldPickerGroups = computed(() => {
  const keyword = advancedFieldKeyword.value.trim().toLowerCase()
  const sourceGroups = advancedEffectiveScope.value
    ? (advancedEffectiveScope.value === 'aggregate' ? advancedMetricFieldsGrouped.value : advancedDimensionFieldsGrouped.value)
    : advancedAllFieldGroups.value
  const groups = sourceGroups.map(group => ({
    groupLabel: group.groupLabel,
    fields: group.fields.filter(field => {
      if (!keyword) return true
      return field.label.toLowerCase().includes(keyword) || field.value.toLowerCase().includes(keyword)
    })
  })).filter(group => group.fields.length > 0)
  return groups
})

const getAdvancedPickerFieldType = (value: string, groupLabel?: string): 'string' | 'number' | 'date' => {
  if (groupLabel === '指标' || value.startsWith('AGGID:') || value.startsWith('AGG:')) return 'number'
  const parsed = parseFieldValue(value)
  if (!parsed.table || !parsed.column) return 'string'
  const tableInfo = tables.value.find(t => t.table_name === parsed.table)
  const colInfo = tableInfo?.columns?.find(c => c.name === parsed.column)
  if (!colInfo) return 'string'
  return getFieldType(colInfo)
}

const openAdvancedFieldPicker = (f: FilterCondition) => {
  pickingAdvancedFilter.value = f
  advancedFieldKeyword.value = ''
  showAdvancedFieldPicker.value = true
}

const closeAdvancedFieldPicker = () => {
  showAdvancedFieldPicker.value = false
  advancedFieldKeyword.value = ''
  pickingAdvancedFilter.value = null
}

const handleAdvancedFieldPick = (value: string) => {
  if (!pickingAdvancedFilter.value) return
  const parsed = parseFieldValue(value)
  if (!parsed.table || !parsed.column) {
    ElMessage.warning('请选择有效字段')
    return
  }
  const nextScope = parsed.aggFunc ? 'aggregate' : 'detail'
  const establishedScope = getConfiguredScopeExcludingFilter(advancedFilterDraft.value, pickingAdvancedFilter.value.id)
  if (establishedScope && nextScope !== establishedScope) {
    ElMessage.warning(`当前组合筛选仅支持${getAdvancedScopeLabel(establishedScope)}`)
    return
  }
  if (!establishedScope) advancedFilterDraftScope.value = nextScope
  pickingAdvancedFilter.value.table = parsed.table
  pickingAdvancedFilter.value.tableAlias = parsed.tableAlias || parsed.table
  pickingAdvancedFilter.value.column = parsed.column
  pickingAdvancedFilter.value.semanticName = parsed.semanticName
  pickingAdvancedFilter.value.aggFunc = parsed.aggFunc
  pickingAdvancedFilter.value.aggId = parsed.aggId
  if (parsed.aggId) {
    const matchedAgg = aggregations.value.find(a => a.id === parsed.aggId)
    if (matchedAgg) {
      pickingAdvancedFilter.value.displayName = getMetricDisplayName(matchedAgg)
    }
  } else if (parsed.aggFunc) {
    pickingAdvancedFilter.value.displayName = `${parsed.aggFunc}(${parsed.column})`
  } else {
    const matchedDim = dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(parsed.tableAlias, parsed.table, parsed.column))
    pickingAdvancedFilter.value.displayName = matchedDim ? getDimensionDisplayName(matchedDim) : parsed.column
  }
  if (nextScope === 'aggregate' && pickingAdvancedFilter.value.valueType === 'field_ref') {
    pickingAdvancedFilter.value.valueType = 'literal'
  }
  if (!pickingAdvancedFilter.value.operator) pickingAdvancedFilter.value.operator = '='
  if (!pickingAdvancedFilter.value.valueType) pickingAdvancedFilter.value.valueType = 'literal'
  closeAdvancedFieldPicker()
}

const openAdvancedFilterDialog = () => {
  const whereFilters = filters.value.filter(f => !f.aggFunc)
  const havingFilters = filters.value.filter(f => !!f.aggFunc)
  const baseTree: AdvancedFilterGroup = advancedFilterTree.value
    ? cloneAdvancedFilterNode(advancedFilterTree.value) as AdvancedFilterGroup
    : { kind: 'group', id: createNodeId(), logic: 'AND', children: [] }
  advancedFilterDraftScope.value = detectAdvancedScopeFromTree(baseTree) || (havingFilters.length > 0 && whereFilters.length === 0 ? 'aggregate' : 'detail')
  advancedFilterDraft.value = baseTree
  showAdvancedFilterDialog.value = true
}

const clearAdvancedFilter = () => {
  const treeLeafIds = getAdvancedLeafIdSet(advancedFilterTree.value)
  advancedFilterTree.value = null
  if (treeLeafIds.size === 0) return
  filters.value = filters.value.filter(f => !treeLeafIds.has(f.id))
}

const closeAdvancedFilterDialog = () => {
  showAdvancedFilterDialog.value = false
  advancedFilterDraft.value = null
}

const handleAdvancedToggleLogic = (groupId: string) => {
  const group = findAdvancedGroup(advancedFilterDraft.value, groupId)
  if (!group) return
  group.logic = group.logic === 'AND' ? 'OR' : 'AND'
}

const handleAdvancedAddLeaf = (groupId: string) => {
  const group = findAdvancedGroup(advancedFilterDraft.value, groupId)
  if (!group) return
  if (advancedActiveScope.value === 'aggregate' && aggregations.value.length === 0) {
    ElMessage.warning('请先添加指标，再配置结果过滤')
    return
  }
  const next = advancedEffectiveScope.value
    ? (advancedEffectiveScope.value === 'aggregate' ? createDefaultAggregateFilter() : createDefaultDetailFilter())
    : createEmptyFilter()
  group.children.push({
    kind: 'leaf',
    id: createNodeId(),
    filter: next
  })
}

const handleAdvancedAddGroup = (groupId: string) => {
  const group = findAdvancedGroup(advancedFilterDraft.value, groupId)
  if (!group) return
  if (advancedActiveScope.value === 'aggregate' && aggregations.value.length === 0) {
    ElMessage.warning('请先添加指标，再配置结果过滤')
    return
  }
  const next = advancedEffectiveScope.value
    ? (advancedEffectiveScope.value === 'aggregate' ? createDefaultAggregateFilter() : createDefaultDetailFilter())
    : createEmptyFilter()
  group.children.push({
    kind: 'group',
    id: createNodeId(),
    logic: 'AND',
    children: [{ kind: 'leaf', id: createNodeId(), filter: next }]
  })
}

const handleAdvancedRemoveNode = (nodeId: string) => {
  if (!advancedFilterDraft.value) return
  if (advancedFilterDraft.value.id === nodeId) {
    advancedFilterDraft.value = null
    return
  }
  removeAdvancedNode(advancedFilterDraft.value, nodeId)
}

const handleAdvancedEditLeaf = (filterId: string) => {
  const leaf = findAdvancedLeafByFilterId(advancedFilterDraft.value, filterId)
  if (!leaf) return
  if (!leaf.filter.table || !leaf.filter.column) {
    openAdvancedFieldPicker(leaf.filter)
    return
  }
  openFilterEditorFromAdvanced(leaf.filter)
}

const confirmAdvancedFilterDialog = () => {
  const scope = advancedEffectiveScope.value || advancedFilterDraftScope.value
  const previousTreeLeafIds = getAdvancedLeafIdSet(advancedFilterTree.value)
  if (!advancedFilterDraft.value) {
    advancedFilterTree.value = null
    if (previousTreeLeafIds.size > 0) {
      filters.value = filters.value.filter(f => !previousTreeLeafIds.has(f.id))
    }
    closeAdvancedFilterDialog()
    return
  }
  const nextTree = cloneAdvancedFilterNode(advancedFilterDraft.value) as AdvancedFilterGroup
  const scopedLeaves = collectAdvancedLeafFilters(nextTree)
  const invalidLeaf = scopedLeaves.find(f => !f.table || !f.column)
  if (invalidLeaf) {
    ElMessage.warning('请先为筛选项选择字段')
    return
  }
  if (scope === 'detail' && scopedLeaves.some(f => !!f.aggFunc)) {
    ElMessage.warning('当前组合筛选仅支持明细过滤')
    return
  }
  if (scope === 'aggregate' && scopedLeaves.some(f => !f.aggFunc)) {
    ElMessage.warning('当前组合筛选仅支持结果过滤')
    return
  }
  const nextScopedFilters = scopedLeaves.map((f, idx) => {
    const cloned = cloneFilterCondition(f)
    if (scope === 'detail') {
      cloned.aggFunc = undefined
      cloned.aggId = undefined
    }
    cloned.logic = (idx === 0 ? 'AND' : 'AND') as FilterLogic
    return cloned
  })
  const sameScopeOutsideFilters = filters.value
    .filter(f => (scope === 'detail' ? !f.aggFunc : !!f.aggFunc))
    .filter(f => !previousTreeLeafIds.has(f.id))
    .map(f => {
      const cloned = cloneFilterCondition(f)
      cloned.logic = 'AND'
      return cloned
    })
  const oppositeFilters = filters.value
    .filter(f => (scope === 'detail' ? !!f.aggFunc : !f.aggFunc))
    .map(f => {
      const cloned = cloneFilterCondition(f)
      cloned.logic = 'AND'
      return cloned
    })
  filters.value = scope === 'detail'
    ? [...nextScopedFilters, ...sameScopeOutsideFilters, ...oppositeFilters]
    : [...oppositeFilters, ...nextScopedFilters, ...sameScopeOutsideFilters]
  advancedFilterTree.value = nextTree
  closeAdvancedFilterDialog()
}

const removeOrderBy = (index: number) => {
  orderBy.value.splice(index, 1)
}

// 放置到筛选区域
const handleDropToFilter = (e: DragEvent) => {
  e.preventDefault()
  dropTargetActive.value = null
  if (draggedItem.value?.type === 'field') {
    const { table, column, aggFunc, expression, displayName, semanticName } = draggedItem.value.data
    const tableAlias = getTableAlias(draggedItem.value.data)
    if (!canConnectTableInSemanticMode({ table, tableAlias })) {
      draggedItem.value = null
    ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善语义模型关系')
      return
    }
    const resolvedAggFunc = (aggFunc || getAggFuncFromExpression(expression)) as AggregationField['func'] | undefined
    const matchedAgg = resolvedAggFunc
      ? aggregations.value.find(a => buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(tableAlias, table, column) && a.func === resolvedAggFunc && (a.expression || '') === (expression || ''))
      : null
    const nextFilter: FilterCondition = {
      id: `${Date.now()}`,
      table,
      tableAlias,
      column,
      semanticName,
      displayName: displayName || column,
      operator: '=',
      value: '',
      valueType: 'literal',
      logic: 'AND',
      expression: expression || undefined,
      aggFunc: resolvedAggFunc,
      aggId: matchedAgg?.id
    }
    filters.value.push(nextFilter)
  }
  draggedItem.value = null
}

// 放置到排序区域
const handleDropToOrder = (e: DragEvent) => {
  e.preventDefault()
  dropTargetActive.value = null
  if (draggedItem.value?.type === 'field') {
    const { table, column, aggFunc, expression, semanticName } = draggedItem.value.data
    const tableAlias = getTableAlias(draggedItem.value.data)
    if (!canConnectTableInSemanticMode({ table, tableAlias })) {
      draggedItem.value = null
    ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善语义模型关系')
      return
    }
    const resolvedAggFunc = (aggFunc || getAggFuncFromExpression(expression)) as AggregationField['func'] | undefined
    const matchedAgg = resolvedAggFunc
      ? aggregations.value.find(a => buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(tableAlias, table, column) && a.func === resolvedAggFunc && (a.expression || '') === (expression || ''))
      : null
    orderBy.value.push({
      id: `${Date.now()}`,
      table,
      tableAlias,
      column,
      semanticName,
      direction: 'ASC',
      aggFunc: resolvedAggFunc,
      aggId: matchedAgg?.id
    })
  }
  draggedItem.value = null
}

// SQL 编译已下沉到后端，前端不再本地拼接 SQL。

// 执行查询
const buildBuilderConfigParams = () => ({
  semanticConfigId: props.semanticConfigId,
  semanticConfigName: props.semanticConfig?.name,
  selectedTables: selectedTables.value,
  dimensions: dimensions.value,
  aggregations: aggregations.value,
  filters: filters.value,
  advancedFilterTree: advancedFilterTree.value,
  orderBy: orderBy.value,
  pagination: queryPagination,
})

const buildBuilderConfigPayload = () => buildBuilderConfigPayloadUtil(buildBuilderConfigParams())
const snapshotSemanticBuilderConfig = () => (
  toSemanticBuilderConfigPayload(snapshotBuilderConfigPayload(buildBuilderConfigParams())) as Record<string, unknown>
)

const builderConfigSnapshot = computed(() => buildBuilderConfigSnapshot())

const openFavoriteModal = () => {
  if (!ensureSemanticConfigSelected()) {
    return
  }
  if (!validateQueryPagination()) {
    return
  }
  if (!validateBuilderFilters()) {
    return
  }
  const builderConfigPayload = buildBuilderConfigPayload() as Record<string, unknown>
  if (!builderConfigPayload.mainTable) {
    ElMessage.warning('请先配置可视化查询')
    return
  }
  const mainTable = String(builderConfigPayload.mainTable || '')
  favoriteTitle.value = `${mainTable} 查询`.slice(0, 40)
  favoriteContent.value = JSON.stringify(builderConfigPayload, null, 2)
  favoriteBuilderConfig.value = builderConfigPayload
  showFavoriteModal.value = true
}

const handleFavoriteSaved = () => {
  quickQueryRefresh.value++
}

const normalizeBuilderErrorMessage = (raw: unknown, fallback: string) => {
  const text = String(raw || '').trim()
  if (!text) return fallback
  if (text.includes('不能混用明细过滤和结果过滤')) {
    return '组合筛选配置冲突：同一筛选组内不能同时使用明细过滤和结果过滤，请在组合筛选弹窗中调整字段类型'
  }
  return text
}

// 提取后端统一响应中的错误信息（msg 优先，其次 detail）
const getApiErrorDetail = (err: unknown): string => {
  const data = (err as { response?: { data?: { msg?: string; detail?: string } } } | null)?.response?.data
  return data?.msg || data?.detail || ''
}

const compileCurrentBuilderSql = async (
  semanticBuilderConfig: Record<string, unknown> = snapshotSemanticBuilderConfig(),
  snapshot: string = buildBuilderConfigSnapshot(),
  datasourceId: string = props.datasourceId,
  semanticConfigId: string = props.semanticConfigId as string,
  force = false,
): Promise<string> => {
  if (!validateBuilderFilters()) {
    return ''
  }
  if (!validateQueryPagination()) {
    return ''
  }
  const compileSnapshot = JSON.stringify({ datasourceId, semanticConfigId, snapshot })
  if (!force && compiledSql.value && compiledSqlSnapshot.value === compileSnapshot) {
    return compiledSql.value
  }
  if (!ensureSemanticConfigSelected()) {
    return ''
  }
  const response = await compileSemanticBuilderSql({
    datasource_id: datasourceId,
    semantic_config_id: semanticConfigId,
    semantic_builder_config: semanticBuilderConfig,
  })
  const sqlText = String(response.data?.sql || '').trim()
  compiledSql.value = sqlText
  compiledSqlSnapshot.value = compileSnapshot
  return sqlText
}

const handlePreviewSql = async () => {
  if (!validateAggregationModeBeforeRun()) {
    return
  }
  if (!ensureSemanticConfigSelected()) {
    return
  }
  if (!isBuilderConfigured.value) {
    ElMessage.warning('请先配置查询条件')
    return
  }
  if (!validateQueryPagination()) {
    return
  }
  if (showSqlPreview.value) {
    showSqlPreview.value = false
    return
  }
  previewSqlLoading.value = true
  try {
    previewSql.value = await compileCurrentBuilderSql()
    if (!previewSql.value) {
      return
    }
    showSqlPreview.value = true
  } catch (err) {
    const rawMessage = getApiErrorDetail(err) || (err instanceof Error ? err.message : '')
    const message = normalizeBuilderErrorMessage(rawMessage, 'SQL 编译失败')
    ElMessage.error(message)
  } finally {
    previewSqlLoading.value = false
  }
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

const executeQuery = async () => {
  if (!validateAggregationModeBeforeRun()) {
    return
  }
  if (!ensureSemanticConfigSelected()) {
    return
  }
  if (!isBuilderConfigured.value) {
    ElMessage.warning('请先配置查询条件')
    return
  }
  if (executionTargets.value.length > 0 && selectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行数据源')
    return
  }
  if (!validateQueryPagination()) {
    return
  }
  if (!validateBuilderFilters()) {
    return
  }
  executing.value = true
  executingTaskText.value = '任务创建中...'
  executingTaskId.value = ''
  rawResultData.value = null
  cancelResultAnalysis()
  executedSql.value = ''
  resultTabs.value = []
  activeResultTabIndex.value = 0
  // 每次执行创建独立取消通道，供组件卸载时中止本次轮询
  const taskController = new AbortController()
  queryTaskAbortController = taskController
  try {
    const datasourceId = props.datasourceId
    const semanticConfigId = props.semanticConfigId as string
    const memberDatasourceIds = [...selectedMemberDatasourceIds.value]
    const shouldSkipQueryCache = queryCacheEnabled.value ? skipQueryCache.value : false
    const semanticBuilderConfig = snapshotSemanticBuilderConfig()

    const task = await submitSqlTask({
      datasource_id: datasourceId,
      member_datasource_ids: memberDatasourceIds,
      query_type: 'builder',
      semantic_builder_config: semanticBuilderConfig,
      semantic_config_id: semanticConfigId,
      query_text: '可视化查询',
      skip_query_cache: shouldSkipQueryCache,
    }, {
      returnTerminalTask: true,
      signal: taskController.signal,
      onProgress: (taskStatus) => {
        executingTaskText.value = taskStatus.progress_text || '执行中...'
        executingTaskId.value = taskStatus.task_id
      }
    })
    const memberResults = task.results || []
    if (memberResults.length > 0) {
      resultTabs.value = memberResults.map((result, index) => {
        const memberLabel = result.member_name || result.member_datasource_id || `成员${index + 1}`
        const payload = isQuerySucceeded(result)
          ? {
              status: 'succeeded',
              sql: result.sql || '',
              columns: result.columns || [],
              data: result.data || [],
              total_count: result.total_count || 0,
              execution_time_ms: result.execution_time_ms,
              cache_hit: result.cache_hit,
              cache_age_seconds: result.cache_age_seconds,
              cache_expires_in_seconds: result.cache_expires_in_seconds,
              member_datasource_id: result.member_datasource_id,
              member_name: result.member_name,
            }
          : {
              status: result.status || 'failed',
              sql: result.sql || '',
              columns: result.columns || [],
              data: result.data || [],
              total_count: result.total_count || 0,
              execution_time_ms: result.execution_time_ms,
              error: result.error || '查询失败',
              member_datasource_id: result.member_datasource_id,
              member_name: result.member_name,
            }
        return {
          title: memberLabel,
          rawResultData: JSON.stringify(payload),
          executedSql: result.sql || '',
          memberDatasourceId: result.member_datasource_id,
          memberName: result.member_name,
          status: isQuerySucceeded(result) ? 'success' : 'error',
        }
      })
      activateResultTab(0)
      // 刷新快捷查询
      quickQueryRefresh.value++
    } else {
      rawResultData.value = JSON.stringify({
        status: 'failed',
        error: task.error_message || '查询失败'
      })
      resultTabs.value = [{
        title: '结果1',
        rawResultData: rawResultData.value,
        executedSql: '',
        status: 'error',
      }]
    }
  } catch (err) {
    // 组件卸载触发的轮询取消不属于查询失败，不向结果区写错误，避免已销毁组件仍弹错误提示
    if (!isAbortError(err)) {
      const rawMessage = getApiErrorDetail(err) || (err instanceof Error ? err.message : '')
      const message = normalizeBuilderErrorMessage(rawMessage, '查询失败')
      rawResultData.value = JSON.stringify({
        status: 'failed',
        error: message
      })
      resultTabs.value = [{
        title: '结果1',
        rawResultData: rawResultData.value,
        executedSql: '',
        status: 'error',
      }]
    }
  } finally {
    executing.value = false
    executingTaskText.value = ''
    executingTaskId.value = ''
    cancellingTask.value = false
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
      const firstMessage = response.data?.executions?.find(execution => execution.message)?.message
      ElMessage.warning(firstMessage || '部分 SQL 暂停失败')
    } else {
      ElMessage.success('已提交暂停请求')
    }
  } catch (err) {
    console.error('暂停可视化查询任务失败:', err)
    ElMessage.error('暂停失败，请稍后重试')
    cancellingTask.value = false
  }
}

// 配置发生变化时，若 SQL 预览已展开则自动折叠；再次点击时重新编译最新 SQL。
watch(builderConfigSnapshot, (nextSnapshot, prevSnapshot) => {
  if (nextSnapshot === prevSnapshot) return
  compiledSql.value = ''
  compiledSqlSnapshot.value = ''
  previewSql.value = ''
  if (!showSqlPreview.value) return
  showSqlPreview.value = false
})

// 清空所有
const clearAll = () => {
  selectedTables.value = []
  dimensions.value = []
  aggregations.value = []
  filters.value = []
  orderBy.value = []
  queryPagination.enabled = false
  queryPagination.limit = 20
  queryPagination.offset = null
  advancedFilterTree.value = null
  advancedFilterDraft.value = null
  showAdvancedFilterDialog.value = false
  rawResultData.value = null
  cancelResultAnalysis()
  resultTabs.value = []
  activeResultTabIndex.value = 0
  resetResultAnalysis()
  executedSql.value = ''
  compiledSql.value = ''
  compiledSqlSnapshot.value = ''
  previewSql.value = ''
  showSqlPreview.value = false
  savedBuilderConfigSnapshot.value = ''
}

const parseAdvancedFilterFromConfig = (node: BuilderFilterNode | null | undefined): AdvancedFilterGroup | AdvancedFilterLeaf | null => {
  if (!node || typeof node !== 'object') return null
  if (Array.isArray(node.and) || Array.isArray(node.or)) {
    const logic: FilterLogic = Array.isArray(node.and) ? 'AND' : 'OR'
    const source = node[logic === 'AND' ? 'and' : 'or'] || []
    const children = source
      .map((child) => parseAdvancedFilterFromConfig(child))
      .filter((child): child is AdvancedFilterLeaf | AdvancedFilterGroup => Boolean(child))
    return {
      kind: 'group',
      id: createNodeId(),
      logic,
      children
    }
  }
  if (!node.table || !node.column) return null
  return {
    kind: 'leaf',
    id: createNodeId(),
    filter: {
      id: node.id || createNodeId(),
      table: node.table,
      tableAlias: node.tableAlias || node.table,
      column: node.column,
      semanticName: node.semanticName,
      displayName: node.displayName,
      operator: node.operator || '=',
      value: node.value || '',
      valueType: node.valueType || 'literal',
      rightTable: node.rightTable,
      rightTableAlias: node.rightTableAlias || node.rightTable,
      rightColumn: node.rightColumn,
      rightExpression: node.rightExpression,
      logic: node.logic || 'AND',
      expression: node.expression,
      aggFunc: node.aggFunc,
      aggId: node.aggId
    }
  }
}

const getBuilderSemanticConfigId = (config: BuilderConfig | null | undefined) => {
  return String(config?.semantic_config_id || config?.semanticConfigId || '').trim()
}

const getBuilderSemanticConfigName = (config: BuilderConfig | null | undefined) => {
  return String(config?.semantic_config_name || config?.semanticConfigName || '').trim()
}

const isAvailableSemanticConfig = (configId: string) => {
  if (!props.semanticConfigs.length) return true
  return props.semanticConfigs.some(item => item.id === configId)
}

const applyBuilderConfig = (config: BuilderConfig) => {
  loadBuilderConfig(config)
  savedBuilderConfigSnapshot.value = buildBuilderConfigSnapshot()
}

const handleQuickBuilderConfig = (config: BuilderConfig) => {
  const targetConfigId = getBuilderSemanticConfigId(config)
  if (!targetConfigId || targetConfigId === props.semanticConfigId) {
    pendingFavoriteBuilderConfig.value = null
    applyBuilderConfig(config)
    return
  }

  if (!isAvailableSemanticConfig(targetConfigId)) {
    const configName = getBuilderSemanticConfigName(config) || targetConfigId
    ElMessage.warning(`收藏关联的语义模型不可用：${configName}`)
    return
  }

  pendingFavoriteBuilderConfig.value = config
  emit('semanticConfigChange', targetConfigId)
}

// 从快捷查询加载 Builder 配置
const loadBuilderConfig = (config: BuilderConfig) => {
  // 清空当前配置
  clearAll()

  // 设置主表
  if (config.mainTable) {
    selectedTables.value.push({
      name: config.mainTable,
      alias: config.mainAlias || config.mainTable,
      label: getTableRoleLabel(config.mainTable, config.mainAlias || config.mainTable, config.mainTable)
    })
  }

  // 设置关联表
  if (config.joins?.length) {
    config.joins.forEach(join => {
      const joinConditions = join.joinConditions?.length
        ? join.joinConditions.map((cond) => ({
          leftCol: cond.leftColumn || '',
          rightCol: cond.rightColumn || ''
        })).filter((cond: { leftCol: string; rightCol: string }) => cond.leftCol || cond.rightCol)
        : [{
          leftCol: join.leftColumn,
          rightCol: join.rightColumn
        }]
      selectedTables.value.push({
        name: join.rightTable,
        alias: join.rightAlias || join.rightTable,
        label: getTableRoleLabel(join.rightTable, join.rightAlias || join.rightTable, join.rightTable),
        joinType: join.joinType,
        joinOn: {
          leftTable: join.leftTable || config.mainTable,
          leftAlias: join.leftAlias || join.leftTable || config.mainAlias || config.mainTable,
          conditions: joinConditions
        }
      })
    })
  }

  // 设置维度
  if (config.dimensions?.length) {
    dimensions.value = config.dimensions.map(d => {
      const tableLabel = tables.value.find(t => t.table_name === d.table)?.description || d.table
      const colLabel = d.alias || d.column
      return {
        id: d.id || `${Date.now()}-${Math.random()}`,
        table: d.table,
        tableAlias: d.tableAlias || d.table,
        column: d.column,
        semanticName: d.semanticName,
        expression: d.expression,
        displayName: d.alias || d.column,
        label: `${tableLabel}.${colLabel}`,
        transform: d.transform || 'raw'
      }
    })
  }

  // 设置聚合
  if (config.aggregations?.length) {
    aggregations.value = config.aggregations.map(a => {
      const tableLabel = tables.value.find(t => t.table_name === a.table)?.description || a.table
      const colLabel = a.alias || a.column
      // 收藏配置未持久化 metricKind，按 semanticName 从语义模型反查回填
      const semanticMetric = a.semanticName
        ? props.semanticConfig?.metrics?.find(m => m.name === a.semanticName)
        : undefined
      const metricKind = semanticMetric?.metric_kind as AggregationField['metricKind'] | undefined
      return {
        id: a.id || `${Date.now()}-${Math.random()}`,
        table: a.table,
        tableAlias: a.tableAlias || a.table,
        column: a.column,
        semanticName: a.semanticName,
        displayName: a.alias || a.column,
        expression: a.expression,
        label: `${tableLabel}.${colLabel}`,
        func: a.func,
        metricKind
      }
    })
  }

  // 设置筛选
  if (config.filters?.length) {
    const flatFilters: FilterCondition[] = []
    let whereTree: AdvancedFilterGroup | null = null
    ;(config.filters || []).forEach((f) => {
      const parsed = parseAdvancedFilterFromConfig(f)
      if (!parsed) return
      if (parsed.kind === 'group') {
        whereTree = parsed
        flatFilters.push(...collectAdvancedLeafFilters(parsed))
        return
      }
      flatFilters.push(parsed.filter)
    })
    filters.value = flatFilters.map((f, idx) => ({
      ...f,
      logic: 'AND'
    }))
    advancedFilterTree.value = whereTree
  }

  // 设置排序
  if (config.orderBy?.length) {
    orderBy.value = config.orderBy.map(o => ({
      id: o.id || `${Date.now()}-${Math.random()}`,
      table: o.table,
      tableAlias: o.tableAlias || o.table,
      column: o.column,
      semanticName: o.semanticName,
      direction: o.direction
    }))
  }

  const loadedLimit = normalizeOptionalInteger(config.limit)
  const loadedOffset = normalizeOptionalInteger(config.offset)
  if (loadedLimit !== null && loadedLimit > 0) {
    queryPagination.enabled = true
    queryPagination.limit = loadedLimit
    queryPagination.offset = loadedOffset !== null && loadedOffset >= 0 ? loadedOffset : null
  }
}

// 筛选和排序的可用字段（按表分组 + 聚合字段分组）
const filterAndOrderFieldsGrouped = computed(() => {
  const groups: FieldPickerGroup[] = []
  // 按表分组的原始字段
  selectedTables.value.forEach(t => {
    const tableInfo = tables.value.find(tb => tb.table_name === t.name)
    const tableLabel = t.label || t.name
    if (tableInfo?.columns?.length) {
      groups.push({
        groupLabel: tableLabel,
        fields: tableInfo.columns.map(col => ({
          value: buildFieldKey(getSelectedTableAlias(t), t.name, col.name),
          label: `${tableLabel}.${col.description || col.name}`
        }))
      })
    }
  })
  // 聚合字段分组
  if (aggregations.value.length > 0) {
    groups.push({
      groupLabel: '聚合过滤字段 (HAVING)',
      fields: aggregations.value.map(a => ({
        value: `AGGID:${a.id}`,
        label: formatMetricChipLabel(a)
      }))
    })
  }
  return groups
})

const allDetailFieldsGrouped = computed(() => {
  const groups: FieldPickerGroup[] = []
  tables.value.forEach(table => {
    if (!table.columns?.length) return
    const tableLabel = table.description || table.table_name
    groups.push({
      groupLabel: tableLabel,
      fields: table.columns.map(col => ({
        value: `${table.table_name}.${col.name}`,
        label: `${tableLabel}.${col.description || col.name}`
      }))
    })
  })
  return groups
})

const semanticDimensionFilterFields = computed<FieldPickerOption[]>(() => {
  const items = props.semanticConfig?.dimensions || []
  const dedup = new Map<string, FieldPickerOption>()
  items.forEach((dim) => {
    if (!dim?.table || !dim?.column) return
    const alias = getTableAlias({ table: dim.table, tableAlias: dim.table_alias })
    const key = buildFieldKey(alias, dim.table, dim.column)
    if (dedup.has(key)) return
    dedup.set(key, {
      value: key,
      label: dim.name || dim.column,
      semanticName: dim.name,
      expression: dim.expression
    })
  })
  return Array.from(dedup.values())
})

const semanticMetricFilterFields = computed<FieldPickerOption[]>(() => {
  const items = props.semanticConfig?.metrics || []
  const fields: FieldPickerOption[] = []
  items.forEach((metric) => {
    const meta = parseMetricExpressionMeta(metric.expression || '')
    const qualified = parseFirstQualifiedFromExpression(metric.expression || '')
    const table = metric.table || qualified?.table || ''
    const tableAlias = getTableAlias({ table, tableAlias: metric.table_alias })
    const func = meta.func || 'RAW'
    const column = meta.column || metric.field_name || qualified?.column || ''
    if (!table || !column) return
    fields.push({
      value: `AGG:${func}:${tableAlias}.${column}`,
      label: metric.name || `${func}(${column})`,
      semanticName: metric.name,
      expression: metric.expression
    })
  })
  return fields
})

const advancedDimensionFieldsGrouped = computed(() => {
  const dedup = new Map<string, FieldPickerOption>()
  dimensions.value.forEach((d) => {
    const key = buildFieldKey(d.tableAlias, d.table, d.column)
    if (dedup.has(key)) return
    dedup.set(key, {
      value: key,
      label: getDimensionDisplayName(d)
    })
  })
  if (dedup.size === 0) {
    if (semanticDimensionFilterFields.value.length === 0) return []
    return [{ groupLabel: '维度', fields: semanticDimensionFilterFields.value }]
  }
  return [{ groupLabel: '维度', fields: Array.from(dedup.values()) }]
})

const advancedMetricFieldsGrouped = computed(() => {
  const merged = new Map<string, FieldPickerOption>()
  aggregations.value.forEach((a) => {
    merged.set(`AGGID:${a.id}`, {
      value: `AGGID:${a.id}`,
      label: formatMetricChipLabel(a)
    })
  })
  semanticMetricFilterFields.value.forEach((field) => {
    if (merged.has(field.value)) return
    merged.set(field.value, field)
  })
  filters.value.forEach((f) => {
    if (!f.aggFunc || !f.table || !f.column) return
    const value = f.aggId ? `AGGID:${f.aggId}` : `AGG:${f.aggFunc}:${getTableAlias(f)}.${f.column}`
    if (merged.has(value)) return
    const funcLabel = getAggFuncLabel(f.aggFunc)
    const labelBase = stripFieldPrefix(f.displayName || f.column) || f.column
    merged.set(value, { value, label: `${funcLabel}(${labelBase})` })
  })
  if (merged.size === 0) return []
  return [{ groupLabel: '指标', fields: Array.from(merged.values()) }]
})

// 解析筛选/排序字段值
const parseFieldValue = (value: string): { table: string; tableAlias?: string; column: string; semanticName?: string; aggFunc?: string; aggId?: string } => {
  if (value.startsWith('AGGID:')) {
    const aggId = value.substring(6)
    const agg = aggregations.value.find(a => a.id === aggId)
    if (agg) {
      return { table: agg.table, tableAlias: getTableAlias(agg), column: agg.column, semanticName: agg.semanticName, aggFunc: agg.func, aggId }
    }
    const fallbackDim = dimensions.value[0]
    if (fallbackDim) {
      ElMessage.warning('原聚合字段已失效，已回退到可用维度字段')
      return { table: fallbackDim.table, tableAlias: getTableAlias(fallbackDim), column: fallbackDim.column, semanticName: fallbackDim.semanticName }
    }
    const fallbackAgg = aggregations.value[0]
    if (fallbackAgg) {
      ElMessage.warning('原聚合字段已失效，已回退到可用聚合字段')
      return { table: fallbackAgg.table, tableAlias: getTableAlias(fallbackAgg), column: fallbackAgg.column, semanticName: fallbackAgg.semanticName, aggFunc: fallbackAgg.func, aggId: fallbackAgg.id }
    }
    ElMessage.warning('当前无可用字段，请先添加维度或指标')
    return { table: '', column: '' }
  }
  if (value.startsWith('AGG:')) {
    const rest = value.substring(4)
    const colonIdx = rest.indexOf(':')
    if (colonIdx <= 0) return { table: '', column: '' }
    const aggFunc = rest.substring(0, colonIdx)
    const dotIdx = rest.indexOf('.', colonIdx + 1)
    if (dotIdx <= colonIdx + 1) return { table: '', column: '' }
    const tableAlias = rest.substring(colonIdx + 1, dotIdx)
    const column = rest.substring(dotIdx + 1)
    if (!tableAlias || !column) return { table: '', column: '' }
    const table = resolvePhysicalTableByAlias(tableAlias)
    const matchedAgg = aggregations.value.find(a => buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(tableAlias, table, column) && a.func === aggFunc)
    const semanticOption = semanticMetricFilterFields.value.find(field => field.value === value)
    return { table, tableAlias, column, semanticName: matchedAgg?.semanticName || semanticOption?.semanticName, aggFunc }
  }
  const dotIdx = value.indexOf('.')
  if (dotIdx <= 0) return { table: '', column: '' }
  const tableAlias = value.substring(0, dotIdx)
  const table = resolvePhysicalTableByAlias(tableAlias)
  const column = value.substring(dotIdx + 1)
  const matchedDim = dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(tableAlias, table, column))
  const semanticOption = semanticDimensionFilterFields.value.find(field => field.value === value)
  return { table, tableAlias, column, semanticName: matchedDim?.semanticName || semanticOption?.semanticName }
}

// 生成筛选字段组合值
const getFilterFieldValue = (f: FilterCondition) => {
  if (!f.table || !f.column) return ''
  if (f.aggFunc && f.aggId) return `AGGID:${f.aggId}`
  if (f.aggFunc) return `AGG:${f.aggFunc}:${getTableAlias(f)}.${f.column}`
  return buildFieldKey(f.tableAlias, f.table, f.column)
}

// 生成排序字段组合值
const getOrderFieldValue = (o: OrderByField) => {
  if (o.aggFunc && o.aggId) return `AGGID:${o.aggId}`
  if (o.aggFunc) return `AGG:${o.aggFunc}:${getTableAlias(o)}.${o.column}`
  return buildFieldKey(o.tableAlias, o.table, o.column)
}

const getOrderFieldLabel = (o: OrderByField) => {
  if (o.aggId) {
    const agg = aggregations.value.find(a => a.id === o.aggId)
    if (agg) return formatMetricChipLabel(agg)
  }
  if (o.aggFunc) {
    const agg = aggregations.value.find(a => (isLogicalAggregation(a) ? a.semanticName === o.semanticName : buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(o.tableAlias, o.table, o.column) && a.func === o.aggFunc))
    if (agg) return formatMetricChipLabel(agg)
    return `${getAggFuncLabel(o.aggFunc)}(${o.column})`
  }
  const dim = dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(o.tableAlias, o.table, o.column))
  if (dim) return getDimensionDisplayName(dim)
  return o.column ? `${o.table}.${o.column}` : '未选择字段'
}

// 处理筛选字段变更
const handleFilterFieldChange = (f: FilterCondition, value: string) => {
  const isFlatFilterRef = filters.value.some(item => item === f)
  const parsed = parseFieldValue(value)
  if (editingFilterFromAdvanced.value) {
    const nextScope: AdvancedFilterScope = parsed.aggFunc ? 'aggregate' : 'detail'
    const establishedScope = getConfiguredScopeExcludingFilter(advancedFilterDraft.value, f.id)
    if (establishedScope && nextScope !== establishedScope) {
      ElMessage.warning(`当前组合筛选仅支持${getAdvancedScopeLabel(establishedScope)}字段`)
      return
    }
    if (!establishedScope) advancedFilterDraftScope.value = nextScope
  }
  const wasAgg = !!f.aggFunc
  const wasScope: AdvancedFilterScope = wasAgg ? 'aggregate' : 'detail'
  const nextScope: AdvancedFilterScope = parsed.aggFunc ? 'aggregate' : 'detail'
  const treeScope = detectAdvancedScopeFromTree(advancedFilterTree.value)
  const isInAdvancedTree = !!findAdvancedLeafByFilterId(advancedFilterTree.value, f.id)
  if (treeScope && isInAdvancedTree && wasScope !== nextScope && nextScope !== treeScope) {
    removeFilterFromAdvancedTree(f.id)
  } else if (treeScope && isInAdvancedTree && wasScope !== nextScope && nextScope === treeScope) {
    appendFilterToAdvancedTree({ ...f, table: parsed.table, tableAlias: parsed.tableAlias || parsed.table, column: parsed.column, semanticName: parsed.semanticName, aggFunc: parsed.aggFunc, aggId: parsed.aggId })
  }
  if (editingFilterFromAdvanced.value && nextScope === 'aggregate' && f.valueType === 'field_ref') {
    f.valueType = 'literal'
  }
  if (!parsed.table || !parsed.column) {
    ElMessage.warning('请选择有效字段')
    return
  }
  f.table = parsed.table
  f.tableAlias = parsed.tableAlias || parsed.table
  f.column = parsed.column
  f.semanticName = parsed.semanticName
  f.aggFunc = parsed.aggFunc
  f.aggId = parsed.aggId
  if (parsed.aggId) {
    const matchedAgg = aggregations.value.find(a => a.id === parsed.aggId)
    if (matchedAgg) {
      f.displayName = getMetricDisplayName(matchedAgg)
    }
  } else if (parsed.aggFunc) {
    f.displayName = `${parsed.aggFunc}(${parsed.column})`
  } else {
    const matchedDim = dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(parsed.tableAlias, parsed.table, parsed.column))
    f.displayName = matchedDim ? getDimensionDisplayName(matchedDim) : parsed.column
  }
  if (f.aggFunc && f.valueType === 'field_ref') {
    f.valueType = 'literal'
  }
  if (!isFlatFilterRef) return
  const isAgg = !!f.aggFunc
  if (treeScope && isInAdvancedTree && getFilterScope(f) !== treeScope) {
    removeFilterFromAdvancedTree(f.id)
    return
  }
  if (isInAdvancedTree) syncAdvancedTreeLeafByFilter(f)
}

// 处理排序字段变更
// 可视化配置（始终传递用户选择的图表类型）
const currentVisualization = computed(() => {
  if (isCompareAnalysisApplied.value) {
    return {
      type: selectedChartType.value,
      y_field: '比率'
    }
  }
  return { type: selectedChartType.value }
})

// 监听数据源变化
watch(() => props.datasourceId, () => {
  loadTables()
  clearAll()
}, { immediate: true })

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

// 切换语义指标集时，自动清空上一个指标集的构建配置
watch(() => props.semanticConfigId, (nextId, prevId) => {
  const pendingConfig = pendingFavoriteBuilderConfig.value
  const pendingConfigId = getBuilderSemanticConfigId(pendingConfig)
  if (pendingConfig && (!pendingConfigId || pendingConfigId === nextId)) {
    pendingFavoriteBuilderConfig.value = null
    applyBuilderConfig(pendingConfig)
    return
  }
  if (pendingConfig && pendingConfigId && pendingConfigId !== nextId) {
    pendingFavoriteBuilderConfig.value = null
  }
  if (!prevId || nextId === prevId) return
  clearAll()
})

watch(() => props.initialBuilderConfig, (config) => {
  if (!config) return
  loadBuilderConfig(config)
  savedBuilderConfigSnapshot.value = buildBuilderConfigSnapshot()
}, { immediate: true, deep: true })

watch(filters, (list) => {
  if (!advancedFilterTree.value) return
  list.forEach((f) => {
    syncAdvancedTreeLeafByFilter(f)
  })
}, { deep: true })

watch([() => props.semanticConfig, tables, dimensions, aggregations, filters, orderBy], ([semanticConfig]) => {
  if (!semanticConfig) return
  const used = new Set<string>()
  // 逻辑指标（派生/复合）无物理表别名，跳过空别名避免污染已选表推导
  const addUsedAlias = (item: { tableAlias?: string; table?: string }) => {
    const alias = getTableAlias(item)
    if (alias) used.add(alias)
  }
  dimensions.value.forEach(addUsedAlias)
  aggregations.value.forEach(addUsedAlias)
  filters.value.forEach(addUsedAlias)
  orderBy.value.forEach(addUsedAlias)
  selectedTables.value = buildSemanticSelectedTables(semanticConfig, tables.value, used)
}, { deep: true, immediate: true })

watch(aggregations, (list) => {
  list.forEach((agg) => {
    const prevFunc = aggFuncSnapshot.value[agg.id]
    if (!prevFunc || prevFunc === agg.func) return
    filters.value.forEach((f) => {
      if (f.aggId === agg.id) {
        f.aggFunc = agg.func
        return
      }
      if (!f.aggId && buildFieldKey(f.tableAlias, f.table, f.column) === buildFieldKey(agg.tableAlias, agg.table, agg.column) && f.aggFunc === prevFunc) {
        f.aggFunc = agg.func
      }
    })
    orderBy.value.forEach((o) => {
      if (o.aggId === agg.id) {
        o.aggFunc = agg.func
        return
      }
      if (!o.aggId && buildFieldKey(o.tableAlias, o.table, o.column) === buildFieldKey(agg.tableAlias, agg.table, agg.column) && o.aggFunc === prevFunc) {
        o.aggFunc = agg.func
      }
    })
  })
  aggFuncSnapshot.value = list.reduce((acc, agg) => {
    acc[agg.id] = agg.func
    return acc
  }, {} as Record<string, AggregationField['func']>)
}, { deep: true, immediate: true })

// 注册鼠标事件
onMounted(() => {
  loadRuntimeConfig()
  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
})

onUnmounted(() => {
  document.removeEventListener('mousemove', onMouseMove)
  document.removeEventListener('mouseup', onMouseUp)
  // 卸载时中止进行中的查询任务轮询（AbortError 由调用方静默处理）
  queryTaskAbortController?.abort()
  queryTaskAbortController = null
})

// 暴露方法给父组件
defineExpose({
  handleSidebarDragStart,
  handleSidebarDragEnd,
  addFieldToDimension,
  addFieldToAggregation
})
</script>

<template>
  <div v-bind="attrs" class="flex flex-row h-full bg-white">
    <!-- 左侧：数据表面板 -->
    <div v-if="!props.hideSidebar" class="w-[240px] flex flex-col bg-white border-r border-[#e3e5eb] shrink-0">
      <!-- 数据源选择（悬浮显示） -->
      <div class="relative h-7 overflow-hidden" @mouseenter="isDatasourceHovered = true" @mouseleave="isDatasourceHovered = false">
        <div
          class="absolute top-0 left-0 right-0 px-3 py-1.5 bg-white transition-all duration-200"
          :class="isDatasourceHovered ? 'translate-y-0 opacity-100' : '-translate-y-full opacity-0'"
        >
          <select
            class="w-full py-1 px-2 border border-[#e3e5eb] rounded text-[12px] text-[#2e2f32] bg-white cursor-pointer outline-none focus:border-[#165DFF]"
            :value="datasourceId"
            @change="handleDatasourceSelect(($event.target as HTMLSelectElement).value)"
          >
            <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
              {{ ds.group_name }}
            </option>
          </select>
        </div>
        <div
          class="absolute top-0 left-0 right-0 h-7 flex items-start justify-center pt-2 transition-all duration-200"
          :class="isDatasourceHovered ? 'opacity-0 -translate-y-2' : ''"
        >
          <div class="w-8 h-0.5 bg-[#93949580] rounded-sm"></div>
        </div>
      </div>

      <div class="h-[28px] flex items-center px-4">
        <span class="text-[12px] font-medium text-[#2e2f32]">数据表</span>
      </div>

      <!-- 搜索框 -->
      <div class="relative px-4 py-1.5">
        <i class="fa-solid fa-magnifying-glass absolute left-[26px] top-1/2 -translate-y-1/2 text-[12px] text-[#57585a]"></i>
        <input v-model="searchText" type="text" placeholder="搜索表..." class="w-full h-[28px] pl-[28px] pr-[2px] border border-[#e3e5eb] rounded text-[12px] text-[#2e2f32] bg-white outline-none transition-all duration-150 focus:border-[#165DFF] placeholder:text-[#93949580]" />
      </div>

      <!-- 表列表 -->
      <div class="flex-1 overflow-y-auto px-2 py-1">
        <div v-if="loading" class="flex flex-col items-center justify-center gap-2 p-6 text-[#57585a] text-[12px]">
          <i class="fa-solid fa-spinner fa-spin text-lg"></i>
          <span>加载中...</span>
        </div>

        <div v-else-if="!tables.length" class="flex flex-col items-center justify-center gap-2 p-6 text-[#57585a] text-[12px]">
          <i class="fa-solid fa-database text-lg"></i>
          <span>暂无数据表</span>
        </div>

        <template v-else>
          <div v-for="table in filteredTables" :key="table.table_name" class="mb-0.5">
            <!-- 表头 -->
            <div
              class="flex items-center h-[28px] px-4 rounded transition-colors duration-100 cursor-grab hover:bg-[#f0f1f3] active:cursor-grabbing"
              draggable="true"
              @dragstart="handleTableDragStart(table, $event)"
              @dragend="handleDragEnd"
              @click="toggleTable(table.table_name)"
            >
              <i :class="['fa-solid fa-chevron-right text-[10px] text-[#57585a] mr-1.5 transition-transform duration-150', expandedTables.has(table.table_name) && 'rotate-90']"></i>
              <i class="fa-solid fa-table text-[14px] text-[#57585a] mr-1.5"></i>
              <span class="flex-1 text-[12px] text-[#2e2f32] overflow-hidden text-ellipsis whitespace-nowrap" :title="buildTableTitle(table)">
                {{ table.description || table.table_name }}
              </span>
            </div>

            <!-- 字段列表 -->
            <div v-if="expandedTables.has(table.table_name)" class="ml-4">
              <div
                v-for="col in table.columns"
                :key="col.name"
                class="flex items-center h-[28px] px-4 rounded transition-colors duration-100 cursor-grab hover:bg-[#f0f1f3] active:cursor-grabbing"
                draggable="true"
                @dragstart="handleFieldDragStart(table, col, $event)"
                @dragend="handleDragEnd"
              >
                <FieldTypeIcon :type="getFieldType(col)" class="text-[#165DFF] mr-1.5" />
                <span class="flex-1 text-[12px] text-[#2e2f32] overflow-hidden text-ellipsis whitespace-nowrap">{{ col.description || col.name }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <QueryBuilderChartPanel
      v-if="!props.hideChartPanel"
      v-model:selected-chart-type="selectedChartType"
      v-model:is-chart-panel-collapsed="isChartPanelCollapsed"
      v-model:chart-panel-tab="chartPanelTab"
      v-model:chart-format-collapsed="chartFormatCollapsed"
      :chart-format="chartFormat"
      @update:chart-format="Object.assign(chartFormat, $event)"
      v-model:chart-pagination-collapsed="chartPaginationCollapsed"
      :chart-pagination="chartPagination"
      @update:chart-pagination="Object.assign(chartPagination, $event)"
      v-model:selected-analysis-type="selectedAnalysisType"
      v-model:compare-type="compareType"
      v-model:result-sort-field="resultSortField"
      v-model:result-sort-direction="resultSortDirection"
      v-model:result-top-n="resultTopN"
      v-model:top-n-field="topNField"
      v-model:top-n-direction="topNDirection"
      v-model:percent-metric-field="percentMetricField"
      v-model:percent-scope="percentScope"
      v-model:percent-group-field="percentGroupField"
      v-model:compare-metric-field="compareMetricField"
      v-model:compare-baseline-mode="compareBaselineMode"
      :executing="executing"
      :has-raw-result="hasRawResult"
      :has-analyzed-result="hasAnalyzedResult"
      :result-analysis-field-options="resultAnalysisFieldOptions"
      :result-analysis-metric-options="resultAnalysisMetricOptions"
      :result-analysis-dimension-options="resultAnalysisDimensionOptions"
      :is-compare-analysis-disabled="isCompareAnalysisDisabled"
      @run-compare-analysis="runCompareAnalysis"
      @run-sort-analysis="runSortAnalysis"
      @run-top-n-analysis="runTopNAnalysis"
      @run-total-analysis="runTotalAnalysis"
      @run-percent-analysis="runPercentAnalysis"
      @run-compare-baseline-analysis="runCompareBaselineAnalysis"
      @cancel-result-analysis="cancelResultAnalysis"
    />
    <!-- 右侧：配置区域 -->
    <div ref="configPanelRef" class="flex-1 flex flex-col overflow-hidden min-w-0 bg-white">
      <!-- 上部：配置区域（固定高度带滚动） -->
      <div class="overflow-y-auto shrink-0" :style="configHeight !== null ? { height: configHeight + 'px' } : {}">
        <div class="px-4 pt-0 pb-3">
        <!-- 紧凑配置区（DataWind 风格：统一容器 + 分割线） -->
        <div class="bg-white divide-y divide-[#e3e5eb]">
          <!-- Row 1: 维度 -->
          <div
            class="flex items-start gap-3 px-4 py-2 min-h-[40px] transition-all duration-150"
            :class="dropTargetActive === 'dimension' ? 'bg-[#E8F3FF]' : ''"
            @dragover="handleDragOver($event, 'dimension')"
            @dragleave="handleDragLeave"
            @drop="handleDropToDimension"
          >
            <span
              class="mt-0.5 text-[12px] font-medium text-[#2e3232d9] shrink-0 whitespace-nowrap cursor-help"
              title="维度：用于明细查询展示字段，也用于分组聚合（GROUP BY）字段。"
            >
              维度
            </span>
            <IconSetting
              class="mt-0.5 text-[#2e3232d9] cursor-pointer hover:text-[#165DFF] transition-colors"
              @click.stop="showSectionSettingComingSoon('维度')"
            />
            <div class="flex-1 flex items-center gap-1.5 flex-wrap min-h-[28px]">
              <template v-if="dimensions.length > 0">
                <span v-for="(d, index) in dimensions" :key="d.id" class="inline-flex items-center h-[24px] px-2 rounded-[10px] bg-[#ebf1ff] text-[#2e2f32] hover:bg-[#dce5ff] transition-colors shrink-0">
                  <span v-if="isDimensionDateField(d)" class="relative inline-flex items-center cursor-pointer shrink-0">
                    <IconCaretDown class="mr-1 text-[#3772ff]" />
                    <span v-if="d.transform !== 'raw'" class="text-[12px]">{{ transformOptions.find(o => o.value === d.transform)?.label }}</span>
                    <select v-model="d.transform" class="absolute inset-0 opacity-0 cursor-pointer" @click.stop>
                      <option v-for="opt in transformOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                    </select>
                  </span><span v-if="isDimensionDateField(d) && d.transform !== 'raw'" class="text-[12px]">({{ getDimensionDisplayName(d) }})</span>
                  <span v-else class="text-[12px]">{{ getDimensionDisplayName(d) }}</span>
                  <button class="ml-1 text-[#2e2f3250] hover:text-[#2e2f32] transition-colors" @click.stop="removeDimension(index)">
                    <IconClose />
                  </button>
                </span>
              </template>
              <span v-else class="text-[12px] text-[#93949580]">拖拽字段作为维度</span>
                <!-- @mention 输入 -->
                <span class="text-[#e3e5eb] mx-1">|</span>
                <input
                  :ref="(el: Element | ComponentPublicInstance | null) => { if (atState.activeRow === 'dimension') atInputRef = el as HTMLInputElement | null }"
                  :value="atState.activeRow === 'dimension' ? atState.inputText : ''"
                  class="flex-1 min-w-[60px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: Event) => { atState.inputText = (e.target as HTMLInputElement).value; handleAtInputChange('dimension') }"
                  @keydown="handleAtKeydown"
                  @blur="handleAtInputBlur"
                  @focus="atState.activeRow = 'dimension'"
                />
            </div>
          </div>

          <!-- Row 2: 指标（聚合） -->
          <div
            class="flex items-start gap-3 px-4 py-2 min-h-[40px] transition-all duration-150"
            :class="dropTargetActive === 'aggregation' ? 'bg-[#E8FFF3]' : ''"
            @dragover="handleDragOver($event, 'aggregation')"
            @dragleave="handleDragLeave"
            @drop="handleDropToAggregation"
          >
            <span
              class="mt-0.5 text-[12px] font-medium text-[#2e3232d9] shrink-0 whitespace-nowrap cursor-help"
              title="指标：用于聚合计算字段（如 COUNT、SUM、AVG、MAX、MIN）。"
            >
              指标
            </span>
            <IconSetting
              class="mt-0.5 text-[#2e3232d9] cursor-pointer hover:text-[#165DFF] transition-colors"
              @click.stop="showSectionSettingComingSoon('指标')"
            />
            <div class="flex-1 flex items-center gap-1.5 flex-wrap min-h-[28px]">
              <template v-if="aggregations.length > 0">
                <span v-for="(a, index) in aggregations" :key="a.id" class="inline-flex items-center h-[24px] px-2 rounded-[10px] bg-[#ebf1ff] text-[#2e2f32] hover:bg-[#dce5ff] transition-colors shrink-0">
                  <template v-if="isLogicalAggregation(a)">
                    <span class="text-[12px]">{{ getMetricDisplayName(a) }}</span>
                    <span
                      class="ml-1 px-1.5 h-[16px] inline-flex items-center rounded text-[10px] text-white"
                      :class="a.metricKind === 'composite' ? 'bg-[#7c5cd6]' : 'bg-[#3772ff]'"
                      :title="a.metricKind === 'composite' ? '复合指标：由多个指标四则运算得出' : '派生指标：基础指标 + 统计周期 + 过滤'"
                    >
                      {{ a.metricKind === 'composite' ? '复合' : '派生' }}
                    </span>
                  </template>
                  <template v-else>
                    <span class="relative inline-flex items-center cursor-pointer shrink-0">
                      <IconCaretDown class="mr-1 text-[#3772ff]" />
                      <span class="text-[12px]">{{ aggFunctions.find(f => f.value === a.func)?.label }}</span>
                      <select :value="a.func" class="absolute inset-0 opacity-0 cursor-pointer" @click.stop @change="handleAggregationFuncChange(a, ($event.target as HTMLSelectElement).value)">
                        <option v-for="opt in aggFunctions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                      </select>
                    </span><span class="text-[12px]">({{ getMetricDisplayName(a) }})</span>
                  </template>
                  <button class="ml-1 text-[#2e2f3250] hover:text-[#2e2f32] transition-colors" @click.stop="removeAggregation(index)">
                    <IconClose />
                  </button>
                </span>
              </template>
              <span v-else class="text-[12px] text-[#93949580]">拖拽字段进行聚合</span>
                <!-- @mention 输入 -->
                <span class="text-[#e3e5eb] mx-1">|</span>
                <input
                  :ref="(el: Element | ComponentPublicInstance | null) => { if (atState.activeRow === 'aggregation') atInputRef = el as HTMLInputElement | null }"
                  :value="atState.activeRow === 'aggregation' ? atState.inputText : ''"
                  class="flex-1 min-w-[60px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: Event) => { atState.inputText = (e.target as HTMLInputElement).value; handleAtInputChange('aggregation') }"
                  @keydown="handleAtKeydown"
                  @blur="handleAtInputBlur"
                  @focus="atState.activeRow = 'aggregation'"
                />
            </div>
          </div>

          <!-- Row 3: 筛选 -->
          <div
            class="flex items-start gap-3 px-4 py-2 transition-all duration-150"
            :class="dropTargetActive === 'filter' ? 'bg-[#FFF7E8]' : ''"
            @dragover="handleDragOver($event, 'filter')"
            @dragleave="handleDragLeave"
            @drop="handleDropToFilter"
          >
            <span
              class="text-[12px] font-medium text-[#2e3232d9] shrink-0 whitespace-nowrap mt-0.5 cursor-help"
              title="筛选：用于限制结果范围，明细过滤条件对应 WHERE，聚合过滤条件对应 HAVING。"
            >
              筛选
            </span>
            <button type="button" class="mt-0.5 p-0 border-none bg-transparent" title="配置组合筛选器" @click="openAdvancedFilterDialog">
              <IconSetting class="text-[#2e3232d9] cursor-pointer hover:text-[#165DFF] transition-colors" />
            </button>
            <div class="flex-1 min-w-0 flex flex-col gap-1.5 min-h-[28px] justify-center">
              <template v-if="filters.length > 0">
                <div v-if="advancedFilterTree" class="flex items-center gap-1.5 flex-wrap">
                  <button
                    type="button"
                    class="inline-flex items-center h-[28px] px-3 rounded-[14px] bg-[#eef4ff] text-[#165DFF] text-[14px] border border-[#d7e5ff] hover:bg-[#e6f0ff] transition-colors shrink-0"
                    @click="openAdvancedFilterDialog"
                  >
                    组合筛选已启用
                    <span
                      class="ml-2 text-[#2e2f3250] hover:text-[#2e2f32] transition-colors"
                      @click.stop="clearAdvancedFilter"
                    >
                      <IconClose />
                    </span>
                  </button>
                  <template v-for="f in advancedOutsideFilters" :key="`outside-${f.id}`">
                    <button
                      type="button"
                      class="inline-flex items-center gap-1 h-[24px] px-2 rounded-[10px] bg-[#ebf1ff] text-[#2e2f32] hover:bg-[#dce5ff] transition-colors max-w-full shrink-0"
                      :title="`${getFilterFieldLabel(f)} ${getOperatorLabel(f.operator)} ${getFilterValueLabel(f)}`"
                      @click="openFilterEditor(f)"
                    >
                      <span class="text-[12px] overflow-hidden text-ellipsis whitespace-nowrap max-w-[300px]">
                        {{ getFilterFieldLabel(f) }} {{ getOperatorLabel(f.operator) }} {{ getFilterValueLabel(f) }}
                      </span>
                      <i class="fa-solid fa-pen text-[10px] text-[#3772ff]"></i>
                      <span
                        class="ml-0.5 text-[#2e2f3250] hover:text-[#2e2f32] transition-colors"
                        @click.stop="removeFilterById(f.id)"
                      >
                        <IconClose />
                      </span>
                    </button>
                  </template>
                </div>
                <div v-else class="flex items-center gap-1.5 flex-wrap">
                  <template v-for="(f, index) in filters" :key="f.id">
                    <span
                      v-if="index > 0"
                      class="inline-flex items-center h-[22px] px-1.5 rounded text-[10px] font-semibold bg-white border border-[#e3e5eb] text-[#6b7280] shrink-0"
                    >
                      AND
                    </span>
                    <button
                      type="button"
                      class="inline-flex items-center gap-1 h-[24px] px-2 rounded-[10px] bg-[#ebf1ff] text-[#2e2f32] hover:bg-[#dce5ff] transition-colors max-w-full shrink-0"
                      :title="`${getFilterFieldLabel(f)} ${getOperatorLabel(f.operator)} ${getFilterValueLabel(f)}`"
                      @click="openFilterEditor(f)"
                    >
                      <span class="text-[12px] overflow-hidden text-ellipsis whitespace-nowrap max-w-[300px]">
                        {{ getFilterFieldLabel(f) }} {{ getOperatorLabel(f.operator) }} {{ getFilterValueLabel(f) }}
                      </span>
                      <i class="fa-solid fa-pen text-[10px] text-[#3772ff]"></i>
                      <span
                        class="ml-0.5 text-[#2e2f3250] hover:text-[#2e2f32] transition-colors"
                        @click.stop="removeFilter(index)"
                      >
                        <IconClose />
                      </span>
                    </button>
                  </template>
                </div>
              </template>
              <div v-else class="flex items-center min-h-[28px]">
                <span class="text-[12px] text-[#93949580]">拖拽字段添加筛选</span>
                <span class="text-[#e3e5eb] mx-1">|</span>
                <input
                  :ref="(el: Element | ComponentPublicInstance | null) => { if (atState.activeRow === 'filter') atInputRef = el as HTMLInputElement | null }"
                  :value="atState.activeRow === 'filter' ? atState.inputText : ''"
                  class="flex-1 min-w-[60px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: Event) => { atState.inputText = (e.target as HTMLInputElement).value; handleAtInputChange('filter') }"
                  @keydown="handleAtKeydown"
                  @blur="handleAtInputBlur"
                  @focus="atState.activeRow = 'filter'"
                />
              </div>
            </div>
          </div>

          <!-- Row 4: 排序 -->
          <div
            class="flex items-start gap-3 px-4 py-2 transition-all duration-150 border-t border-[#eef0f4]"
            :class="dropTargetActive === 'order' ? 'bg-[#eef6ff]' : ''"
            @dragover="handleDragOver($event, 'order')"
            @dragleave="handleDragLeave"
            @drop="handleDropToOrder"
          >
            <span
              class="text-[12px] font-medium text-[#2e3232d9] shrink-0 whitespace-nowrap mt-0.5 cursor-help"
              title="排序：确定结果先后顺序，对应 SQL ORDER BY。"
            >
              排序
            </span>
            <el-popover trigger="click" placement="bottom-start" :width="300">
              <template #reference>
                <button type="button" class="mt-0.5 p-0 border-none bg-transparent">
                  <IconSetting class="text-[#2e3232d9] cursor-pointer hover:text-[#165DFF] transition-colors" />
                </button>
              </template>
              <div class="text-[12px] text-[#2e2f32]">
                <div class="font-medium mb-2">排序配置</div>
                <template v-if="orderBy.length > 0">
                  <div v-for="o in orderBy" :key="`order-setting-${o.id}`" class="flex items-center justify-between gap-2 mb-2 last:mb-0">
                    <span class="flex-1 min-w-0 truncate text-[#4b5563]" :title="getOrderFieldLabel(o)">{{ getOrderFieldLabel(o) }}</span>
                    <select
                      v-model="o.direction"
                      class="h-[24px] px-2 border border-[#dbe1ec] rounded text-[12px] bg-white text-[#4b5563] outline-none cursor-pointer"
                    >
                      <option value="ASC">升序</option>
                      <option value="DESC">降序</option>
                    </select>
                  </div>
                </template>
                <div v-else class="text-[#9ca3af]">请先添加排序字段</div>
              </div>
            </el-popover>
            <div class="flex-1 min-w-0 flex flex-col gap-1.5 min-h-[28px] justify-center">
              <template v-if="orderBy.length > 0">
                <div class="flex items-center gap-1.5 flex-wrap">
                  <div
                    v-for="(o, index) in orderBy"
                    :key="o.id"
                    class="inline-flex items-center gap-1 h-[24px] px-2 rounded-[10px] bg-[#edf3ff] border border-[#d8e4ff] text-[#2e2f32] max-w-full shrink-0"
                  >
                    <span
                      class="max-w-[240px] text-[12px] overflow-hidden text-ellipsis whitespace-nowrap"
                      :title="getOrderFieldLabel(o)"
                    >
                      {{ getOrderFieldLabel(o) }}
                    </span>
                    <span class="text-[11px] text-[#7b8798] shrink-0">{{ o.direction === 'DESC' ? '降序' : '升序' }}</span>
                    <button class="ml-0.5 text-[#2e2f3250] hover:text-[#2e2f32] transition-colors" @click.stop="removeOrderBy(index)">
                      <IconClose />
                    </button>
                  </div>
                  <span class="text-[#e3e5eb] mx-1">|</span>
                  <input
                    :ref="(el: Element | ComponentPublicInstance | null) => { if (atState.activeRow === 'order') atInputRef = el as HTMLInputElement | null }"
                    :value="atState.activeRow === 'order' ? atState.inputText : ''"
                    class="min-w-[120px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                    placeholder="可@选择字段"
                    @input="(e: Event) => { atState.inputText = (e.target as HTMLInputElement).value; handleAtInputChange('order') }"
                    @keydown="handleAtKeydown"
                    @blur="handleAtInputBlur"
                    @focus="atState.activeRow = 'order'"
                  />
                </div>
              </template>
              <div v-else class="flex items-center min-h-[28px]">
                <span class="text-[12px] text-[#93949580]">拖拽字段添加排序</span>
                <span class="text-[#e3e5eb] mx-1">|</span>
                <input
                  :ref="(el: Element | ComponentPublicInstance | null) => { if (atState.activeRow === 'order') atInputRef = el as HTMLInputElement | null }"
                  :value="atState.activeRow === 'order' ? atState.inputText : ''"
                  class="flex-1 min-w-[120px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: Event) => { atState.inputText = (e.target as HTMLInputElement).value; handleAtInputChange('order') }"
                  @keydown="handleAtKeydown"
                  @blur="handleAtInputBlur"
                  @focus="atState.activeRow = 'order'"
                />
              </div>
            </div>
          </div>

          <!-- Row 5: 分页 -->
          <div class="flex items-start gap-3 px-4 py-2 transition-all duration-150 border-t border-[#eef0f4]">
            <span
              class="text-[12px] font-medium text-[#2e3232d9] shrink-0 whitespace-nowrap mt-0.5 cursor-help"
              title="分页：限制 SQL 返回条数，对应 SQL LIMIT。"
            >
              分页
            </span>
            <div class="flex-1 flex items-center gap-2 min-h-[28px] flex-wrap">
              <button
                type="button"
                class="relative w-[28px] h-[16px] rounded-full transition-colors duration-200 shrink-0"
                :class="queryPagination.enabled ? 'bg-[#165DFF]' : 'bg-[#c9cdd4]'"
                @click="queryPagination.enabled = !queryPagination.enabled"
              >
                <span
                  class="absolute top-[2px] w-[12px] h-[12px] bg-white rounded-full shadow transition-all duration-200"
                  :class="queryPagination.enabled ? 'left-[14px]' : 'left-[2px]'"
                ></span>
              </button>
              <span v-if="!queryPagination.enabled" class="text-[12px] text-[#93949580]">限制返回条数</span>
              <template v-else>
                <span class="text-[12px] text-[#6b7280]">返回</span>
                <input
                  v-model.number="queryPagination.limit"
                  type="number"
                  min="1"
                  step="1"
                  class="w-[72px] h-[24px] px-2 border border-[#dbe1ec] rounded text-[12px] bg-white text-[#4b5563] outline-none focus:border-[#165DFF]"
                />
                <span class="text-[12px] text-[#6b7280]">条</span>
                <span class="text-[12px] text-[#6b7280] ml-1">跳过</span>
                <input
                  v-model.number="queryPagination.offset"
                  type="number"
                  min="0"
                  step="1"
                  placeholder="0"
                  class="w-[72px] h-[24px] px-2 border border-[#dbe1ec] rounded text-[12px] bg-white text-[#4b5563] outline-none focus:border-[#165DFF] placeholder:text-[#b8bcc6]"
                />
                <span class="text-[12px] text-[#6b7280]">条</span>
              </template>
            </div>
          </div>

        </div>


        <!-- SQL预览（inline） -->
        <div v-if="showSqlPreview" class="mb-3 rounded overflow-hidden border border-[#e3e5eb]">
          <div v-if="previewSqlLoading" class="px-4 py-3 bg-[#f8f9fb] text-[12px] text-[#6b7280]">
            编译中...
          </div>
          <pre v-else class="px-4 py-3 m-0 bg-[#f8f9fb] font-mono text-[12px] leading-relaxed text-[#2e2f32] whitespace-pre-wrap max-h-[150px] overflow-y-auto">{{ previewSql }}</pre>
        </div>

        <!-- 操作栏 -->
        <div class="flex items-center justify-between px-4 py-2 border-t border-[#e3e5eb] gap-3">
          <div class="flex items-center gap-2">
            <button
              class="flex items-center gap-1 h-[24px] px-3 border rounded text-[12px] font-semibold cursor-pointer transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
              :class="executing ? 'border-red-200 bg-red-50 text-red-600 hover:bg-red-100' : 'border-[#e3e5eb] bg-white text-[#21252C] hover:bg-[#f0f1f3]'"
              :disabled="executing && (!executingTaskId || cancellingTask)"
              @click="executing ? handleCancelExecutingTask() : executeQuery()"
            >
              <i v-if="executing" :class="['fa-solid text-[12px]', cancellingTask ? 'fa-spinner fa-spin' : 'fa-stop']"></i>
              <IconPlay v-else />
              <span class="ml-0.5">{{ executing ? (cancellingTask ? '暂停中...' : '暂停执行') : '开始查询' }}</span>
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
            <button
              class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]"
              :class="{ 'bg-[#E8F3FF] border-[#165DFF] text-[#165DFF]': showSqlPreview }"
              @click="handlePreviewSql"
            >
              <i :class="previewSqlLoading ? 'fa-solid fa-spinner fa-spin' : 'fa-regular fa-file-code'"></i>
              预览SQL
            </button>
            <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" @click="openFavoriteModal">
              <i class="fa-regular fa-star"></i>
              收藏
            </button>
            <button
              v-if="props.onSqlChange"
              class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3] disabled:opacity-50 disabled:cursor-not-allowed"
              :disabled="!hasSqlChanged"
              @click="handleSaveSql"
            >
              <i class="fa-regular fa-floppy-disk"></i>
              保存SQL
            </button>
            <button class="flex items-center gap-1 h-[24px] px-3 border border-[#e3e5eb] rounded bg-white text-[12px] font-semibold text-[#21252C] cursor-pointer transition-all duration-150 hover:bg-[#f0f1f3]" @click="clearAll">
              <i class="fa-solid fa-rotate-right"></i>
              重置
            </button>
          </div>
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

      <!-- 下部：结果或快捷查询 -->
      <div class="flex-1 min-h-[100px] flex flex-col overflow-hidden bg-white">
        <!-- 结果区域 -->
        <div v-if="resultData" class="bg-white flex-1 flex flex-col overflow-hidden">
          <QueryResultPanel
            :items="hasAnalyzedResult ? [] : queryResultPanelItems"
            :result-data="hasAnalyzedResult ? resultData : null"
            :active-index="hasAnalyzedResult ? 0 : activeResultTabIndex"
            :sql="executedSql || previewSql"
            :semantic-params="resultSemanticParams"
            :visualization="currentVisualization"
            :on-save="handleSaveToDashboard"
            :pagination-enabled="chartPagination.enabled"
            :page-size="chartPagination.pageSize"
            :show-index="chartFormat.showIndex"
            :auto-wrap="chartFormat.autoWrap"
            :metrics-first="chartFormat.metricsFirst"
            @update:active-index="handleResultPanelActiveIndex"
          />
        </div>

        <!-- 执行中加载状态 -->
        <div v-else-if="executing" class="flex-1 flex flex-col items-center justify-center px-6 py-16 bg-white">
          <div class="w-16 h-16 flex items-center justify-center rounded-2xl bg-[#E8F3FF] mb-4">
            <i class="fa-solid fa-spinner fa-spin text-[28px] text-[#165DFF]"></i>
          </div>
          <p class="text-[14px] font-medium text-[#2e2f32] m-0 mb-1">{{ executingTaskText || '执行查询中...' }}</p>
          <p v-if="executingTaskId" class="m-0 text-[12px] text-gray-400">任务ID: {{ executingTaskId }}</p>
        </div>

        <!-- 快捷查询推荐（无结果时显示） -->
        <div v-else class="flex-1 overflow-y-auto">
          <QuickQueries
            :datasource-id="datasourceId"
            query-type="builder"
            :refresh-trigger="quickQueryRefresh"
            :max-items="8"
            @select-builder-config="handleQuickBuilderConfig"
          />
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
    mode="builder"
    :datasource-id="datasourceId"
    :semantic-config-id="props.semanticConfigId"
    :initial-title="favoriteTitle"
    :initial-content="favoriteContent"
    :initial-builder-config="favoriteBuilderConfig"
    @saved="handleFavoriteSaved"
  />

  <QueryBuilderFieldManagers
    v-model:show-dimension-manager="showDimensionManager"
    v-model:show-aggregation-manager="showAggregationManager"
    :dimension-drafts="dimensionDrafts"
    :aggregation-drafts="aggregationDrafts"
    :transform-options="transformOptions"
    :agg-functions="aggFunctions"
    :get-dimension-display-name="getDimensionDisplayName"
    :get-metric-display-name="getMetricDisplayName"
    :is-dimension-date-field="isDimensionDateField"
    @remove-selected-dimension-drafts="removeSelectedDimensionDrafts"
    @move-dimension-draft="moveDimensionDraft"
    @save-dimension-manager="saveDimensionManager"
    @remove-selected-aggregation-drafts="removeSelectedAggregationDrafts"
    @move-aggregation-draft="moveAggregationDraft"
    @save-aggregation-manager="saveAggregationManager"
  />

  <el-dialog
    v-model="showAdvancedFilterDialog"
    width="1100px"
    :close-on-click-modal="false"
    title="配置组合筛选器"
    @closed="closeAdvancedFilterDialog"
  >
    <div class="mb-2 text-[12px] text-[#6b7280] leading-5">
      <template v-if="advancedEffectiveScope">
        第一个筛选字段为 <span class="font-semibold">{{ getAdvancedScopeLabel(advancedActiveScope) }}</span>，组合筛选器内其他筛选字段也只能为
        <span class="font-semibold">{{ getAdvancedScopeLabel(advancedActiveScope) }}</span>
      </template>
      <template v-else>
        请先选择第一个筛选字段，系统会自动识别为明细过滤或结果过滤
      </template>
    </div>
    <div class="max-w-full overflow-x-auto pb-1">
      <div class="min-w-[980px]">
        <AdvancedFilterTree
          v-if="advancedFilterDraft"
          :group="advancedFilterDraft"
          :describe-leaf="getAdvancedLeafLabel"
          @toggle-logic="handleAdvancedToggleLogic"
          @add-leaf="handleAdvancedAddLeaf"
          @add-group="handleAdvancedAddGroup"
          @remove-node="handleAdvancedRemoveNode"
          @edit-leaf="handleAdvancedEditLeaf"
        />
        <div v-else class="h-[72px] flex items-center justify-center text-[13px] text-[#9CA3AF] border border-dashed border-[#d7dbe7] rounded">
          当前无组合筛选条件
        </div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="closeAdvancedFilterDialog">取消</el-button>
        <el-button type="primary" @click="confirmAdvancedFilterDialog">确定</el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="showAdvancedFieldPicker"
    width="560px"
    title="选择筛选字段"
    :close-on-click-modal="false"
    append-to-body
    @closed="closeAdvancedFieldPicker"
  >
    <div class="border border-[#e3e5eb] rounded-[8px] bg-white">
      <div class="relative px-4 py-1.5 border-b border-[#edf0f5]">
        <div class="relative">
          <i class="fa-solid fa-magnifying-glass absolute left-[10px] top-1/2 -translate-y-1/2 text-[12px] text-[#57585a]"></i>
          <input
            v-model="advancedFieldKeyword"
            type="text"
            class="w-full h-[28px] pl-[28px] pr-[8px] rounded border border-[#e3e5eb] text-[12px] text-[#2e2f32] bg-white outline-none transition-all duration-150 focus:border-[#165DFF] placeholder:text-[#93949580]"
            placeholder="搜索关键词"
          />
        </div>
      </div>
      <div class="max-h-[420px] overflow-y-auto px-2 py-1">
        <template v-if="advancedFieldPickerGroups.length > 0">
          <div v-for="group in advancedFieldPickerGroups" :key="group.groupLabel" class="mb-0.5">
            <div class="h-[28px] flex items-center px-4 text-[12px] font-medium text-[#2e3232d9]">
              {{ group.groupLabel }}
            </div>
            <div
              v-for="field in group.fields"
              :key="field.value"
              class="w-full h-[28px] px-4 text-left rounded transition-colors duration-100 cursor-pointer hover:bg-[#f0f1f3] border-0 bg-transparent shadow-none flex items-center"
              role="button"
              tabindex="0"
              @click="handleAdvancedFieldPick(field.value)"
              @keydown.enter.prevent="handleAdvancedFieldPick(field.value)"
            >
              <FieldTypeIcon
                :type="getAdvancedPickerFieldType(field.value, group.groupLabel)"
                :class="group.groupLabel === '指标' ? 'text-[#8CB8FF] mr-1.5' : 'text-[#165DFF] mr-1.5'"
              />
              <span class="flex-1 text-[12px] text-[#2e2f32] overflow-hidden text-ellipsis whitespace-nowrap">{{ field.label }}</span>
            </div>
          </div>
        </template>
        <div v-else class="h-[120px] flex items-center justify-center text-[13px] text-[#9CA3AF]">
          暂无匹配字段
        </div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end">
        <el-button @click="closeAdvancedFieldPicker">关闭</el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="showFilterEditor"
    width="760px"
    :close-on-click-modal="false"
    title="编辑筛选"
    @closed="closeFilterEditor"
  >
    <template #header>
      <div class="flex items-center">
        <span class="text-[20px] font-semibold text-[#1f2937] leading-none">编辑筛选</span>
      </div>
    </template>
    <div v-if="editingFilter" class="space-y-4">
      <div class="space-y-3">
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="text-xs text-gray-500 mb-1">字段</div>
            <el-select
              :model-value="getFilterFieldValue(editingFilter)"
              filterable
              class="w-full"
              placeholder="请选择字段"
              @change="(val: string) => editingFilter && handleFilterFieldChange(editingFilter, val)"
            >
              <el-option-group v-for="group in advancedEditingFieldGroups" :key="group.groupLabel" :label="group.groupLabel">
                <el-option v-for="field in group.fields" :key="field.value" :label="field.label" :value="field.value" />
              </el-option-group>
            </el-select>
          </div>
          <div>
            <div class="text-xs text-gray-500 mb-1">操作符</div>
            <select
              v-model="editingFilter.operator"
              class="w-full px-2.5 py-2 border border-[#e3e5eb] rounded text-sm bg-white text-[#2e2f32] outline-none"
              @change="handleFilterOperatorChange(editingFilter, ($event.target as HTMLSelectElement).value)"
            >
              <option
                v-for="op in currentFilterOperatorOptions"
                :key="op.value"
                :value="op.value"
              >{{ op.label }}</option>
            </select>
          </div>
        </div>

        <div v-if="!['IS NULL', 'IS NOT NULL'].includes(editingFilter.operator)">
          <div v-if="['IN', 'NOT IN'].includes(editingFilter.operator)" class="space-y-3">
            <div class="flex items-center gap-4 text-[14px] text-[#2e2f32]">
              <label class="inline-flex items-center gap-1.5 cursor-pointer">
                <input
                  type="radio"
                  :checked="inInputMode === 'list'"
                  @change="inInputMode = 'list'"
                />
                值列表
              </label>
              <label class="inline-flex items-center gap-1.5 cursor-pointer">
                <input
                  type="radio"
                  :checked="inInputMode === 'subquery'"
                  @change="inInputMode = 'subquery'"
                />
                子查询
              </label>
            </div>
            <template v-if="inInputMode === 'list'">
              <textarea
                v-model="manualInputText"
                class="w-full h-[250px] px-3 py-2 border border-[#e3e5eb] rounded text-sm bg-white text-[#2e2f32] outline-none resize-none"
                placeholder="使用分隔符批量输入，例如每行一个值"
              ></textarea>
              <div class="flex items-center gap-3">
                <span class="text-[14px] text-[#2e2f32]">分隔符</span>
                <select v-model="manualDelimiter" class="h-[34px] px-2 border border-[#e3e5eb] rounded text-[14px] bg-white text-[#2e2f32] outline-none">
                  <option value=",">逗号 (,)</option>
                  <option value=";">分号 (;)</option>
                  <option value=" ">空格</option>
                  <option value="\n">换行</option>
                </select>
              </div>
            </template>
            <template v-else>
              <div class="flex items-center gap-4 text-[14px] text-[#2e2f32]">
                <label class="inline-flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    :checked="!subqueryExclude"
                    @change="subqueryExclude = false"
                  />
                  IN
                </label>
                <label class="inline-flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    :checked="subqueryExclude"
                    @change="subqueryExclude = true"
                  />
                  NOT IN
                </label>
              </div>
              <textarea
                v-model="subquerySql"
                class="w-full h-[250px] px-3 py-2 border border-[#e3e5eb] rounded text-sm bg-white text-[#2e2f32] outline-none resize-none font-mono"
                placeholder="请输入子查询 SQL，例如 SELECT user_id FROM orders WHERE amount > 1000"
              ></textarea>
            </template>
          </div>

          <template v-else-if="isEditingDateField">
          <div class="text-xs text-gray-500 mb-1">时间筛选</div>
          <div class="flex items-center gap-2 mb-3">
            <button
              type="button"
              class="h-[28px] px-3 rounded text-[13px] border transition-colors"
              :class="dateFilterMode === 'dynamic' ? 'bg-[#e8f3ff] text-[#165DFF] border-[#bedaff]' : 'bg-white text-[#4b5563] border-[#e3e5eb]'"
              @click="dateFilterMode = 'dynamic'"
            >
              动态日期
            </button>
            <button
              type="button"
              class="h-[28px] px-3 rounded text-[13px] border transition-colors"
              :class="dateFilterMode === 'fixed' ? 'bg-[#e8f3ff] text-[#165DFF] border-[#bedaff]' : 'bg-white text-[#4b5563] border-[#e3e5eb]'"
              @click="dateFilterMode = 'fixed'"
            >
              固定日期
            </button>
            <button
              type="button"
              class="h-[28px] px-3 rounded text-[13px] border transition-colors"
              :class="dateFilterMode === 'single' ? 'bg-[#e8f3ff] text-[#165DFF] border-[#bedaff]' : 'bg-white text-[#4b5563] border-[#e3e5eb]'"
              @click="dateFilterMode = 'single'"
            >
              单个日期
            </button>
          </div>

          <div v-if="dateFilterMode === 'dynamic'" class="grid grid-cols-3 gap-2">
            <button
              v-for="preset in dateQuickPresets"
              :key="preset.label"
              type="button"
              class="h-[32px] px-2 rounded border text-[13px] transition-colors"
              :class="isDatePresetActive(preset)
                ? 'border-[#bedaff] bg-[#e8f3ff] text-[#165DFF]'
                : 'border-[#e3e5eb] bg-white text-[#2e2f32] hover:bg-[#f4f8ff] hover:border-[#bedaff]'"
              @click="applyDatePreset(preset)"
            >
              {{ preset.label }}
            </button>
          </div>
          <div v-else-if="dateFilterMode === 'fixed'">
            <el-config-provider :locale="zhCn">
              <el-date-picker
                v-model="fixedDateRange"
                :type="currentFixedDatePickerType"
                range-separator="→"
                :start-placeholder="currentStartPlaceholder"
                :end-placeholder="currentEndPlaceholder"
                :value-format="currentDateValueFormat"
                :shortcuts="fixedDateShortcuts"
                class="w-full"
              />
            </el-config-provider>
          </div>
          <div v-else>
            <el-config-provider :locale="zhCn">
              <el-date-picker
                v-model="singleDateValue"
                :type="currentSingleDatePickerType"
                :placeholder="currentSinglePlaceholder"
                :value-format="currentDateValueFormat"
                :shortcuts="singleDateShortcuts"
                class="w-full"
              />
            </el-config-provider>
          </div>
          </template>

          <template v-else>
          <div class="text-xs text-gray-500 mb-1">比较对象</div>
          <div class="flex items-center gap-3 mb-2">
            <label class="inline-flex items-center gap-1.5 text-sm text-[#2e2f32]">
              <input
                type="radio"
                :checked="(editingFilter.valueType || 'literal') === 'literal'"
                @change="handleFilterValueTypeChange(editingFilter, 'literal')"
              />
              值
            </label>
            <label class="inline-flex items-center gap-1.5 text-sm text-[#2e2f32]">
              <input
                type="radio"
                :checked="editingFilter.valueType === 'field_ref'"
                :disabled="!!editingFilter.aggFunc"
                @change="handleFilterValueTypeChange(editingFilter, 'field_ref')"
              />
              字段
            </label>
            <label class="inline-flex items-center gap-1.5 text-sm text-[#2e2f32]">
              <input
                type="radio"
                :checked="editingFilter.valueType === 'expression'"
                @change="handleFilterValueTypeChange(editingFilter, 'expression')"
              />
              表达式
            </label>
          </div>

          <input
            v-if="(editingFilter.valueType || 'literal') === 'literal'"
            v-model="editingFilter.value"
            class="w-full px-2.5 py-2 border border-[#e3e5eb] rounded text-sm bg-white text-[#2e2f32] outline-none"
            placeholder="请输入比较值"
          />
          <select
            v-else-if="editingFilter.valueType === 'field_ref'"
            :value="getFilterRightFieldValue(editingFilter)"
            class="w-full px-2.5 py-2 border border-[#e3e5eb] rounded text-sm bg-white text-[#2e2f32] outline-none"
            @change="handleFilterRightFieldChange(editingFilter, ($event.target as HTMLSelectElement).value)"
          >
            <option value="">请选择比较字段</option>
            <option v-for="field in filterFieldRefOptions" :key="field.value" :value="field.value">{{ field.label }}</option>
          </select>
          <textarea
            v-else
            v-model="editingFilter.rightExpression"
            class="w-full px-2.5 py-2 border border-[#e3e5eb] rounded text-sm bg-white text-[#2e2f32] outline-none resize-none"
            rows="3"
            placeholder="请输入 SQL 表达式，例如 DATE(order_items.created_at)"
          ></textarea>
          </template>
        </div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="closeFilterEditor">取消</el-button>
        <el-button type="primary" @click="handleFilterEditorConfirm">确定</el-button>
      </div>
    </template>
  </el-dialog>

  <!-- @mention 下拉列表 -->
  <Teleport to="body">
    <div
      v-if="atState.dropdownVisible && atInputRef"
      class="fixed z-[10000] w-[220px] max-h-[260px] bg-white border border-[#e3e5eb] rounded-lg shadow-[0_4px_16px_rgba(0,0,0,0.12)] overflow-hidden"
      :style="{
        left: (atInputRef.getBoundingClientRect().left) + 'px',
        top: (atInputRef.getBoundingClientRect().bottom + 4) + 'px'
      }"
    >
      <div class="overflow-y-auto max-h-[220px] py-1">
        <div v-if="atFilteredItems.length === 0" class="px-3 py-4 text-center text-[12px] text-[#939495]">无匹配项</div>
        <template v-for="(item, idx) in atFilteredItems" :key="item.name + (item.table || '')">
          <!-- 表分组标题 -->
          <div v-if="item.name.startsWith('__group__')" class="flex items-center gap-2 h-[28px] px-3 text-[11px] font-medium text-[#939495] bg-[#f8f9fb] border-b border-[#f0f1f3]">
            <i class="fa-solid fa-table text-[10px]"></i>
            <span>{{ item.label }}</span>
          </div>
          <!-- 字段项 -->
          <div
            v-else
            class="flex items-center gap-2 h-[36px] px-3 cursor-pointer transition-colors text-[13px]"
            :class="idx === atState.selectedIndex ? 'bg-[#E8F3FF]' : 'hover:bg-[#f0f1f3]'"
            @mousedown.prevent="handleAtSelect(item)"
            @mouseenter="atState.selectedIndex = idx"
          >
            <FieldTypeIcon :type="(item.fieldType as 'string' | 'number' | 'date') || 'date'" class="text-[#165DFF]" />
            <span class="flex-1 text-[#21252C] overflow-hidden text-ellipsis whitespace-nowrap">{{ item.label }}</span>
          </div>
        </template>
      </div>
      <!-- 底部提示 -->
      <div class="flex items-center gap-2 px-3 h-[30px] border-t border-[#e3e5eb] text-[11px] text-[#939495]">
        <span>↑↓</span>
        <span>选择字段</span>
      </div>
    </div>
  </Teleport>
</template>
