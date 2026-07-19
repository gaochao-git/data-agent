<script setup lang="ts">
/**
 * 辅助查询构建器 - 拖拽式界面
 * 支持拖拽表到数据源区域，拖拽字段到维度/聚合区域
 */
import { ref, reactive, computed, watch, onMounted, onUnmounted, nextTick, useAttrs } from 'vue'
import { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { cancelQueryTask, getChatDataConfig, getDatasourceSchema, compileSemanticBuilderSql } from '../../api'
import { useQueryTask } from '../../composables/useQueryTask'
import QueryResultPanel from '../QueryResultPanel.vue'
import QuickQueries, { type BuilderConfig } from '../QuickQueries.vue'
import SaveToPanelModal from '../SaveToPanelModal.vue'
import QueryFavoriteModal from '../QueryFavoriteModal.vue'
import ExecutionTargetPicker from '../ExecutionTargetPicker.vue'
import AdvancedFilterTree from './AdvancedFilterTree.vue'
import FieldTypeIcon from '../icons/FieldTypeIcon.vue'
import IconSetting from '../icons/IconSetting.vue'
import IconPlay from '../icons/IconPlay.vue'
import IconClose from '../icons/IconClose.vue'
import IconCaretDown from '../icons/IconCaretDown.vue'
import type { ChartType } from '../../chartTypes'
import type { ChatDataSqlChangePayload, SemanticConfigItem, SemanticRelationship, ExecutionTargetOption } from '../../types'
import { isQuerySucceeded } from '../../utils'

interface ColumnInfo {
  name: string
  type: string
  mysql_type?: string
  description?: string
  primary_key?: boolean
}

type QueryResultPanelItem = {
  key?: string | number
  title?: string
  resultData: string
  sql?: string
  memberDatasourceId?: string
  memberName?: string
  status?: 'success' | 'error' | 'none'
}

interface TableInfo {
  table_name: string
  description?: string
  business_desc?: string
  columns?: ColumnInfo[]
}

// 已选数据源（支持多表关联）
interface SelectedTable {
  name: string
  alias: string
  label: string
  joinType?: 'LEFT' | 'RIGHT' | 'INNER' | 'FULL'
  joinOn?: { leftTable: string; leftAlias: string; conditions: Array<{ leftCol: string; rightCol: string }> }
}

// 维度字段
interface DimensionField {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  expression?: string
  displayName?: string
  label: string
  transform?: 'raw' | 'year' | 'quarter' | 'month' | 'week' | 'day'
}

// 聚合字段
interface AggregationField {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  displayName?: string
  expression?: string
  label: string
  func: 'RAW' | 'COUNT' | 'SUM' | 'AVG' | 'MAX' | 'MIN' | 'COUNT_DISTINCT'
}

// 筛选条件
interface FilterCondition {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  displayName?: string
  expression?: string
  operator: string
  value: string
  valueType?: 'literal' | 'field_ref' | 'expression'
  rightTable?: string
  rightTableAlias?: string
  rightColumn?: string
  rightExpression?: string
  logic: 'AND' | 'OR'
  aggFunc?: string  // 如果设置，则为 HAVING 条件
  aggId?: string
}

type FilterLogic = 'AND' | 'OR'

interface AdvancedFilterLeaf {
  kind: 'leaf'
  id: string
  filter: FilterCondition
}

interface AdvancedFilterGroup {
  kind: 'group'
  id: string
  logic: FilterLogic
  children: Array<AdvancedFilterLeaf | AdvancedFilterGroup>
}

// 排序规则
interface OrderByField {
  id: string
  table: string
  tableAlias?: string
  column: string
  semanticName?: string
  direction: 'ASC' | 'DESC'
  aggFunc?: string  // 如果设置，排序使用聚合表达式
  aggId?: string
}

interface FieldPickerOption {
  value: string
  label: string
  semanticName?: string
  expression?: string
}

interface FieldPickerGroup {
  groupLabel: string
  fields: FieldPickerOption[]
}

interface DataSource {
  id: string
  group_name: string
  ds_tables?: TableInfo[]
  is_default?: boolean
  source_type?: string
  template_datasource_id?: string
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

interface BuilderQueryResultPayload {
  status?: string | null
  sql?: string
  columns?: string[]
  data?: Record<string, unknown>[]
  total_count?: number
  execution_time_ms?: number
}

interface Props {
  datasourceId: string
  datasource?: DataSource | null
  datasources?: DataSource[]
  semanticConfigId?: string | null
  semanticConfig?: SemanticConfigItem | null
  semanticConfigs?: SemanticConfigItem[]
  initialBuilderConfig?: BuilderConfig | null
  hideSidebar?: boolean
  hideChartPanel?: boolean
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  hideExecutionTargetSelector?: boolean
  onSqlChange?: (payload: ChatDataSqlChangePayload) => void
}

const props = withDefaults(defineProps<Props>(), {
  datasource: null,
  datasources: () => [],
  semanticConfigId: null,
  semanticConfig: null,
  semanticConfigs: () => [],
  initialBuilderConfig: null,
  hideSidebar: false,
  hideChartPanel: false,
  executionTargetMultiple: true,
  hideExecutionTargetSelector: false,
})
const attrs = useAttrs()
const { submitSqlTask } = useQueryTask()

const emit = defineEmits<{
  datasourceChange: [dsId: string]
  semanticConfigChange: [configId: string]
  'update:selectedMemberDatasourceIds': [value: string[]]
}>()

// 表结构数据
const tables = ref<TableInfo[]>([])
const loading = ref(false)
const expandedTables = ref<Set<string>>(new Set())
const searchText = ref('')

// 数据源悬浮状态
const isDatasourceHovered = ref(false)

// 处理数据源变更
const handleDatasourceSelect = (dsId: string) => {
  emit('datasourceChange', dsId)
}

// 拖拽状态
const draggedItem = ref<{ type: 'table' | 'field'; data: any } | null>(null)
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
const hasAnalyzedResult = computed(() => !!analyzedResultData.value)
const resultData = computed(() => analyzedResultData.value || rawResultData.value)
const isCompareAnalysisDisabled = computed(() => {
  const transform = dimensions.value[0]?.transform || 'raw'
  return transform === 'week'
})

const ensureSemanticConfigSelected = () => {
  if (hasSemanticConfigSelected.value) return true
  ElMessage.warning('请先选择指标集')
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
    const sqlText = await compileCurrentBuilderSql()
    if (!sqlText) {
      ElMessage.warning('请先配置查询条件')
      return
    }
    const builderConfigPayload = buildBuilderConfigPayload() as Record<string, unknown>
    cb({
      mode: 'builder',
      sql: sqlText,
      builderConfig: builderConfigPayload
    })
    savedBuilderConfigSnapshot.value = buildBuilderConfigSnapshot()
    ElMessage.success('SQL 已保存')
  } catch (err) {
    const apiMsg = (err as any)?.response?.data?.msg
    const apiDetail = (err as any)?.response?.data?.detail
    const rawMessage = apiMsg || apiDetail || (err instanceof Error ? err.message : '')
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

// 保存到看板
const showSaveModal = ref(false)
const saveSql = ref('')
const saveChartType = ref<ChartType>('table')
const saveMemberDatasourceId = ref('')
const saveMemberDatasourceIds = ref<string[]>([])
const handleSaveToDashboard = (sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => {
  saveSql.value = sql
  saveChartType.value = (chartType as ChartType) || selectedChartType.value
  saveMemberDatasourceId.value = String(memberDatasourceId || resultTabs.value[activeResultTabIndex.value]?.memberDatasourceId || '').trim()
  const executedMemberIds = (memberDatasourceIds || []).map(item => String(item || '').trim()).filter(Boolean)
  saveMemberDatasourceIds.value = executedMemberIds.length > 1
    ? executedMemberIds
    : (saveMemberDatasourceId.value ? [saveMemberDatasourceId.value] : [])
  showSaveModal.value = true
}

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
  const hasRaw = list.some(item => isRawAggFunc(item.func))
  const hasAgg = list.some(item => !isRawAggFunc(item.func))
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

const isAggregateExpression = (expression?: string) => {
  const expr = (expression || '').trim()
  if (!expr) return false
  return AGG_EXPR_PATTERN.test(expr)
}

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
type AnalysisType = 'sort' | 'topn' | 'total' | 'percent' | 'compare' | 'yoy'
const selectedAnalysisType = ref<AnalysisType>('sort')
const appliedAnalysisType = ref<AnalysisType | null>(null)
const compareType = ref<'mom' | 'yoy'>('mom')
const resultSortField = ref('')
const resultSortDirection = ref<'ASC' | 'DESC'>('DESC')
const resultTopN = ref(10)
const topNField = ref('')
const topNDirection = ref<'ASC' | 'DESC'>('DESC')
const percentMetricField = ref('')
const percentScope = ref<'total' | 'group'>('total')
const percentGroupField = ref('')
const compareMetricField = ref('')
const compareBaselineMode = ref<'first' | 'max' | 'min' | 'avg'>('first')

// 结果表格格式
const chartFormatCollapsed = ref(false)
const chartFormat = reactive({
  showIndex: false,
  autoWrap: false,
  metricsFirst: false,
})

const parseResultEnvelope = (result: string | null) => {
  if (!result) return null
  try {
    return JSON.parse(result) as {
      status?: string | null
      error?: string
      sql?: string
      columns?: string[]
      data?: Record<string, unknown>[]
      total_count?: number
      execution_time_ms?: number
    }
  } catch {
    return null
  }
}

const rawResultEnvelope = computed(() => parseResultEnvelope(rawResultData.value))
const currentResultEnvelope = computed(() => parseResultEnvelope(resultData.value))
const resultAnalysisFieldOptions = computed(() => currentResultEnvelope.value?.columns || [])
const resultAnalysisMetricOptions = computed(() => {
  const rows = currentResultEnvelope.value?.data || []
  return resultAnalysisFieldOptions.value.filter(column => rows.some(row => toNumericValue(row[column]) !== null))
})
const resultAnalysisDimensionOptions = computed(() => {
  return resultAnalysisFieldOptions.value.filter(column => column !== percentMetricField.value)
})
const isCompareAnalysisApplied = computed(() => hasAnalyzedResult.value && appliedAnalysisType.value === 'yoy')
const analyzedResultSemanticParams = computed(() => {
  const columns = currentResultEnvelope.value?.columns || []
  const rows = currentResultEnvelope.value?.data || []
  const metrics = columns.filter(column => rows.some(row => toNumericValue(row[column]) !== null))
  const dimensions = columns.filter(column => !metrics.includes(column))
  return { metrics, dimensions }
})

const resultSemanticParams = computed(() => {
  if (isCompareAnalysisApplied.value) {
    return {
      metrics: ['当前值', '上期值', '差值', '比率'],
      dimensions: dimensions.value.map(d => getDimensionDisplayName(d))
    }
  }
  if (hasAnalyzedResult.value) {
    return analyzedResultSemanticParams.value
  }
  return {
    metrics: aggregations.value.map(a => getMetricDisplayName(a)),
    dimensions: dimensions.value.map(d => getDimensionDisplayName(d))
  }
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

const createNodeId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

const cloneFilterCondition = (filter: FilterCondition): FilterCondition => ({ ...filter })

const cloneAdvancedFilterNode = (node: AdvancedFilterLeaf | AdvancedFilterGroup): AdvancedFilterLeaf | AdvancedFilterGroup => {
  if (node.kind === 'leaf') {
    return { kind: 'leaf', id: node.id, filter: cloneFilterCondition(node.filter) }
  }
  return {
    kind: 'group',
    id: node.id,
    logic: node.logic,
    children: node.children.map(child => cloneAdvancedFilterNode(child))
  }
}

const collectAdvancedLeafFilters = (node: AdvancedFilterGroup): FilterCondition[] => {
  const result: FilterCondition[] = []
  node.children.forEach(child => {
    if (child.kind === 'leaf') {
      result.push(child.filter)
      return
    }
    result.push(...collectAdvancedLeafFilters(child))
  })
  return result
}

const buildFlatWhereTree = (whereFilters: FilterCondition[]): AdvancedFilterGroup | null => {
  if (whereFilters.length === 0) return null
  let expr: AdvancedFilterLeaf | AdvancedFilterGroup = {
    kind: 'leaf',
    id: createNodeId(),
    filter: cloneFilterCondition(whereFilters[0]!)
  }
  for (let i = 1; i < whereFilters.length; i++) {
    const f = whereFilters[i] as FilterCondition
    expr = {
      kind: 'group',
      id: createNodeId(),
      logic: (f.logic || 'AND') as FilterLogic,
      children: [
        expr,
        { kind: 'leaf', id: createNodeId(), filter: cloneFilterCondition(f) }
      ]
    }
  }
  if (expr.kind === 'group') return expr
  return { kind: 'group', id: createNodeId(), logic: 'AND', children: [expr] }
}

type AdvancedFilterScope = 'detail' | 'aggregate'

const getFilterScope = (f: FilterCondition): AdvancedFilterScope => (f.aggFunc ? 'aggregate' : 'detail')

const detectAdvancedScopeFromTree = (tree: AdvancedFilterGroup | null): AdvancedFilterScope | null => {
  if (!tree) return null
  const leaves = collectAdvancedLeafFilters(tree).filter(f => !!f.table && !!f.column)
  if (leaves.length === 0) return null
  return getFilterScope(leaves[0]!)
}

const getAdvancedScopeLabel = (scope: AdvancedFilterScope) => (scope === 'detail' ? '明细过滤' : '结果过滤')

const getFirstConfiguredLeaf = (tree: AdvancedFilterGroup | null): FilterCondition | null => {
  if (!tree) return null
  const leaves = collectAdvancedLeafFilters(tree)
  return leaves.find(f => !!f.table && !!f.column) || null
}

const getAdvancedLeafIdSet = (tree: AdvancedFilterGroup | null) => {
  if (!tree) return new Set<string>()
  return new Set(collectAdvancedLeafFilters(tree).map(f => f.id))
}

const advancedOutsideFilters = computed(() => {
  const leafIds = getAdvancedLeafIdSet(advancedFilterTree.value)
  if (leafIds.size === 0) return []
  return filters.value.filter(f => !leafIds.has(f.id))
})

const getConfiguredScopeExcludingFilter = (tree: AdvancedFilterGroup | null, excludeFilterId?: string): AdvancedFilterScope | null => {
  if (!tree) return null
  const leaves = collectAdvancedLeafFilters(tree)
  const target = leaves.find(f => f.id !== excludeFilterId && !!f.table && !!f.column)
  return target ? getFilterScope(target) : null
}

const findAdvancedGroup = (node: AdvancedFilterGroup | null, groupId: string): AdvancedFilterGroup | null => {
  if (!node) return null
  if (node.id === groupId) return node
  for (const child of node.children) {
    if (child.kind === 'group') {
      const matched = findAdvancedGroup(child, groupId)
      if (matched) return matched
    }
  }
  return null
}

const removeAdvancedNode = (node: AdvancedFilterGroup, nodeId: string): boolean => {
  const idx = node.children.findIndex(child => child.id === nodeId)
  if (idx >= 0) {
    node.children.splice(idx, 1)
    return true
  }
  for (const child of node.children) {
    if (child.kind !== 'group') continue
    if (removeAdvancedNode(child, nodeId)) return true
  }
  return false
}

const findAdvancedLeafByFilterId = (node: AdvancedFilterGroup | null, filterId: string): AdvancedFilterLeaf | null => {
  if (!node) return null
  for (const child of node.children) {
    if (child.kind === 'leaf' && child.filter.id === filterId) return child
    if (child.kind === 'group') {
      const leaf = findAdvancedLeafByFilterId(child, filterId)
      if (leaf) return leaf
    }
  }
  return null
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

const appendFilterToAdvancedTree = (filter: FilterCondition) => {
  if (!advancedFilterTree.value) return
  const scope = detectAdvancedScopeFromTree(advancedFilterTree.value)
  if (!scope) return
  if (getFilterScope(filter) !== scope) return
  advancedFilterTree.value.children.push({ kind: 'leaf', id: createNodeId(), filter: cloneFilterCondition(filter) })
}

const removeFilterFromAdvancedTree = (filterId: string) => {
  if (!advancedFilterTree.value) return
  const leaf = findAdvancedLeafByFilterId(advancedFilterTree.value, filterId)
  if (!leaf) return
  removeAdvancedNode(advancedFilterTree.value, leaf.id)
  if (collectAdvancedLeafFilters(advancedFilterTree.value).length === 0) {
    advancedFilterTree.value = null
  }
}

const syncAdvancedTreeLeafByFilter = (filter: FilterCondition) => {
  if (!advancedFilterTree.value) return
  const leaf = findAdvancedLeafByFilterId(advancedFilterTree.value, filter.id)
  if (!leaf) return
  leaf.filter = cloneFilterCondition(filter)
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

// 过滤表
const filteredTables = computed(() => {
  if (!searchText.value.trim()) return tables.value
  const keyword = searchText.value.toLowerCase()
  return tables.value.filter(t =>
    t.table_name.toLowerCase().includes(keyword) ||
    t.description?.toLowerCase().includes(keyword) ||
    t.business_desc?.toLowerCase().includes(keyword)
  )
})

const buildTableTitle = (table: TableInfo) => [
  `表名：${table.table_name}`,
  `备注：${table.description || '-'}`,
  table.business_desc ? `表业务说明：${table.business_desc}` : '',
].filter(Boolean).join('\n')

// 切换表展开
const toggleTable = (tableName: string) => {
  const newSet = new Set(expandedTables.value)
  if (newSet.has(tableName)) {
    newSet.delete(tableName)
  } else {
    newSet.add(tableName)
  }
  expandedTables.value = newSet
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
const handleSidebarDragStart = (type: 'table' | 'field', data: any, e: DragEvent) => {
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

const addFieldToDimension = (field: any) => {
  const { table, column, label, aggFunc, expression, displayName, semanticName } = field || {}
  const tableAlias = getTableAlias(field || {})
  if (!table || !column) return
  if (!canConnectTableInSemanticMode({ table, tableAlias })) {
    ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善指标集关系')
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
      label: dimensionLabel,
      transform: 'raw'
    })
    if (aggFunc) {
      ElMessage.success('已将指标转换为维度')
    }
  }
}

const addFieldToAggregation = (field: any) => {
  const { table, column, label, aggFunc, displayName, expression, semanticName } = field || {}
  const tableAlias = getTableAlias(field || {})
  if (!table || !column) return
  if (!canConnectTableInSemanticMode({ table, tableAlias })) {
    ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善指标集关系')
    return
  }
  const targetFunc = aggFunc || getDefaultAggregationFunc({ table, column, type: field?.type })
  if (!canUseAggregationFunc(targetFunc)) {
    showAggregationModeConflictMessage(targetFunc)
    return
  }
  if (!aggregations.value.find(a => buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(tableAlias, table, column) && a.func === targetFunc && (a.expression || '') === (expression || ''))) {
    aggregations.value.push({
      id: `${Date.now()}`,
      table,
      tableAlias,
      column,
      semanticName,
      displayName,
      expression,
      label,
      func: targetFunc
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
  const hasAggregation = (item: { table: string; column: string; aggFunc?: string; aggId?: string }) => {
    if (item.aggId && aggregations.value.some(agg => agg.id === item.aggId)) return true
    return aggregations.value.some(agg => buildFieldKey(agg.tableAlias, agg.table, agg.column) === buildFieldKey((item as any).tableAlias, item.table, item.column) && agg.func === item.aggFunc)
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
  dimensions.value = dimensionDrafts.value.map(({ selected, ...item }) => ({
    ...item,
    displayName: (item.displayName || '').trim() || item.column,
    label: item.label || item.column,
    transform: isDimensionDateField(item) ? (item.transform || 'raw') : 'raw'
  }))
  pruneInvalidFieldReferences()
  showDimensionManager.value = false
}

const saveAggregationManager = () => {
  const nextList = aggregationDrafts.value.map(({ selected, ...item }) => ({
    ...item,
    displayName: (item.displayName || '').trim() || item.column,
    label: item.label || item.column,
    func: normalizeAggFunc(item.func)
  }))
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

// 添加筛选条件
const addFilter = () => {
  if (dimensions.value.length === 0 && aggregations.value.length === 0) {
    ElMessage.warning('请先添加维度或聚合字段')
    return
  }
  const firstField = dimensions.value[0] ?? aggregations.value[0]
  if (!firstField) return
  const nextFilter: FilterCondition = {
    id: `${Date.now()}`,
    table: firstField.table,
    tableAlias: getTableAlias(firstField),
    column: firstField.column,
    semanticName: firstField.semanticName,
    displayName: 'displayName' in firstField ? firstField.displayName || firstField.column : firstField.column,
    operator: '=',
    value: '',
    valueType: 'literal',
    logic: 'AND',
    expression: 'expression' in firstField ? firstField.expression : undefined,
    aggFunc: 'func' in firstField ? firstField.func : undefined,
    aggId: 'func' in firstField ? firstField.id : undefined
  }
  filters.value.push(nextFilter)
}

// 移除筛选
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

const parseTableColumnValue = (value: string): { table: string; column: string } => {
  const dotIdx = value.indexOf('.')
  if (dotIdx <= 0) return { table: '', column: '' }
  return { table: value.substring(0, dotIdx), column: value.substring(dotIdx + 1) }
}

const ensureArrayValues = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.map(v => String(v)).filter(Boolean)
  }
  if (typeof value === 'string') {
    return value.split(',').map(v => v.trim()).filter(Boolean)
  }
  return []
}

const parseManualValues = (text: string, delimiter: ',' | ';' | ' ' | '\n') => {
  if (!text.trim()) return []
  if (delimiter === '\n') {
    return text.split(/\r?\n/).map(v => v.trim()).filter(Boolean)
  }
  if (delimiter === ' ') {
    return text.split(/\s+/).map(v => v.trim()).filter(Boolean)
  }
  return text.split(delimiter).map(v => v.trim()).filter(Boolean)
}

const _sqlLiteralFallback = (value: unknown) => {
  const str = `${value ?? ''}`
  return isNaN(Number(str)) ? `'${str.replace(/'/g, "''")}'` : str
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

const getAggFuncLabel = (func?: string) => {
  const key = String(func || '').toUpperCase()
  return aggFunctions.find(item => item.value === key)?.label || key
}

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
  if (Array.isArray((f as any).value)) {
    ;(f as any).value = ''
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
  ;(f as any).value = ''
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
  showAsChartFilter.value = Boolean((f as any).asChartFilter)
  const op = (f.operator || '').toUpperCase()
  if (isListOperator(op) && f.valueType === 'expression' && /select/i.test(f.rightExpression || '')) {
    inInputMode.value = 'subquery'
    subquerySql.value = f.rightExpression || ''
    subqueryExclude.value = op === 'NOT IN'
    manualInputText.value = ''
  } else if (isListOperator(op)) {
    inInputMode.value = 'list'
    const arr = ensureArrayValues((f as any).value)
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
    if (op === 'BETWEEN' && Array.isArray((f as any).value) && (f as any).value.length === 2) {
      dateFilterMode.value = 'fixed'
      fixedDateRange.value = [String((f as any).value[0] || ''), String((f as any).value[1] || '')]
    } else if (op === 'BETWEEN' && f.valueType === 'expression' && (f.rightExpression || '').includes('AND')) {
      dateFilterMode.value = 'dynamic'
      fixedDateRange.value = []
    } else if (op === '=' && !Array.isArray((f as any).value) && String((f as any).value || '').trim()) {
      dateFilterMode.value = 'single'
      singleDateValue.value = String((f as any).value || '').trim()
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
      ;(f as any).value = [fixedDateRange.value[0], fixedDateRange.value[1]]
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
      ;(f as any).value = singleDateValue.value
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
      ;(f as any).value = values
      f.valueType = 'literal'
      f.rightExpression = undefined
    }
  }
  ;(f as any).asChartFilter = showAsChartFilter.value
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
    if (matchedAgg) return `${getAggFuncLabel(matchedAgg.func)}(${getMetricDisplayName(matchedAgg)})`
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
  if (f.operator === 'BETWEEN' && Array.isArray((f as any).value) && (f as any).value.length === 2) {
    return `${(f as any).value[0]} ~ ${(f as any).value[1]}`
  }
  if (Array.isArray((f as any).value)) {
    const arr = (f as any).value as string[]
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

// 添加排序规则
const addOrderBy = () => {
  if (dimensions.value.length === 0 && aggregations.value.length === 0) {
    ElMessage.warning('请先添加维度或聚合字段')
    return
  }
  const firstField = dimensions.value[0] ?? aggregations.value[0]
  if (!firstField) return
  orderBy.value.push({
    id: `${Date.now()}`,
    table: firstField.table,
    tableAlias: getTableAlias(firstField),
    column: firstField.column,
    semanticName: firstField.semanticName,
    direction: 'ASC',
    aggFunc: 'func' in firstField ? firstField.func : undefined,
    aggId: 'func' in firstField ? firstField.id : undefined
  })
}

// 移除排序
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
      ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善指标集关系')
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
      ElMessage.warning('该字段所属表与当前已选字段无关联，请先完善指标集关系')
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

const buildSemanticSelectedTables = (semanticConfig: SemanticConfigItem | null | undefined, usedAliases: Set<string>) => {
  if (!semanticConfig) return []
  const orderedAliases = Array.from(usedAliases).filter(Boolean)
  if (orderedAliases.length === 0) return []
  const getRelConditions = (rel: SemanticRelationship) => {
    return rel.join_conditions || []
  }

  const mapJoinType = (joinType?: SemanticRelationship['join_type']) => {
    if (!joinType) return 'LEFT'
    return joinType.toUpperCase() as 'LEFT' | 'RIGHT' | 'INNER' | 'FULL'
  }

  const getAliasPhysicalTable = (alias: string) => {
    const dim = semanticConfig.dimensions.find(item => getTableAlias({ table: item.table, tableAlias: item.table_alias }) === alias)
    if (dim) return dim.table
    const metric = semanticConfig.metrics.find(item => getTableAlias({ table: item.table, tableAlias: item.table_alias }) === alias)
    if (metric) return metric.table
    const rel = semanticConfig.relationships.find(item => getRelationshipLeftAlias(item) === alias || getRelationshipRightAlias(item) === alias)
    if (!rel) return alias
    return getRelationshipLeftAlias(rel) === alias ? rel.left_table : rel.right_table
  }

  const root = orderedAliases[0] || ''
  const adjacency = new Map<string, Array<{ rel: SemanticRelationship; next: string }>>()
  semanticConfig.relationships.forEach(rel => {
    const leftAlias = getRelationshipLeftAlias(rel)
    const rightAlias = getRelationshipRightAlias(rel)
    if (!adjacency.has(leftAlias)) adjacency.set(leftAlias, [])
    if (!adjacency.has(rightAlias)) adjacency.set(rightAlias, [])
    adjacency.get(leftAlias)?.push({ rel, next: rightAlias })
    adjacency.get(rightAlias)?.push({ rel, next: leftAlias })
  })

  const prev = new Map<string, { parent: string; rel: SemanticRelationship }>()
  const queue: string[] = [root]
  const visited = new Set<string>([root])
  while (queue.length > 0) {
    const curr = queue.shift() as string
    const neighbors = adjacency.get(curr) || []
    neighbors.forEach(({ rel, next }) => {
      if (visited.has(next)) return
      visited.add(next)
      prev.set(next, { parent: curr, rel })
      queue.push(next)
    })
  }

  const requiredNodes = new Set<string>([root])
  const requiredRels: SemanticRelationship[] = []
  const relSeen = new Set<string>()
  orderedAliases.slice(1).forEach(target => {
    let node = target
    while (node !== root && prev.has(node)) {
      requiredNodes.add(node)
      const { parent, rel } = prev.get(node) as { parent: string; rel: SemanticRelationship }
      const relId = `${getRelationshipLeftAlias(rel)}|${getRelationshipRightAlias(rel)}|${rel.join_type}|${getRelConditions(rel).map(c => `${c.left_column}=${c.right_column}`).join('&')}`
      if (!relSeen.has(relId)) {
        relSeen.add(relId)
        requiredRels.push(rel)
      }
      node = parent
    }
  })

  if (requiredNodes.size === 1) {
    const physicalTable = getAliasPhysicalTable(root)
    const tableInfo = tables.value.find(t => t.table_name === physicalTable)
    return [{ name: physicalTable, alias: root, label: getTableRoleLabel(physicalTable, root, tableInfo?.description || root) }]
  }

  const result: SelectedTable[] = []
  const rootPhysicalTable = getAliasPhysicalTable(root)
  const rootInfo = tables.value.find(t => t.table_name === rootPhysicalTable)
  result.push({ name: rootPhysicalTable, alias: root, label: getTableRoleLabel(rootPhysicalTable, root, rootInfo?.description || root) })
  const added = new Set<string>([root])
  while (added.size < requiredNodes.size) {
    const rel = requiredRels.find(r =>
      (added.has(getRelationshipLeftAlias(r)) && requiredNodes.has(getRelationshipRightAlias(r)) && !added.has(getRelationshipRightAlias(r))) ||
      (added.has(getRelationshipRightAlias(r)) && requiredNodes.has(getRelationshipLeftAlias(r)) && !added.has(getRelationshipLeftAlias(r)))
    )
    if (!rel) break
    const leftAlias = getRelationshipLeftAlias(rel)
    const rightAlias = getRelationshipRightAlias(rel)
    if (added.has(leftAlias) && !added.has(rightAlias)) {
      const info = tables.value.find(t => t.table_name === rel.right_table)
      result.push({
        name: rel.right_table,
        alias: rightAlias,
        label: getTableRoleLabel(rel.right_table, rightAlias, info?.description || rightAlias),
        joinType: mapJoinType(rel.join_type),
        joinOn: {
          leftTable: rel.left_table,
          leftAlias,
          conditions: getRelConditions(rel).map(c => ({ leftCol: c.left_column, rightCol: c.right_column }))
        }
      })
      added.add(rightAlias)
    } else if (added.has(rightAlias) && !added.has(leftAlias)) {
      const info = tables.value.find(t => t.table_name === rel.left_table)
      result.push({
        name: rel.left_table,
        alias: leftAlias,
        label: getTableRoleLabel(rel.left_table, leftAlias, info?.description || leftAlias),
        joinType: mapJoinType(rel.join_type),
        joinOn: {
          leftTable: rel.right_table,
          leftAlias: rightAlias,
          conditions: getRelConditions(rel).map(c => ({ leftCol: c.right_column, rightCol: c.left_column }))
        }
      })
      added.add(leftAlias)
    }
  }
  return result
}

// SQL 编译已下沉到后端，前端不再本地拼接 SQL。

// 执行查询
const serializeFilterCondition = (f: FilterCondition) => ({
  id: f.id,
  table: f.table,
  tableAlias: getTableAlias(f),
  column: f.column,
  semanticName: f.semanticName,
  displayName: f.displayName,
  operator: f.operator,
  value: f.value,
  valueType: f.valueType || 'literal',
  rightTable: f.rightTable,
  rightTableAlias: f.rightTableAlias,
  rightColumn: f.rightColumn,
  rightExpression: f.rightExpression,
  logic: f.logic,
  expression: f.expression,
  aggFunc: f.aggFunc,
  aggId: f.aggId
})

const serializeFlatFilterCondition = (f: FilterCondition) => ({
  ...serializeFilterCondition(f),
  logic: 'AND' as FilterLogic
})

const buildAdvancedFilterDsl = (node: AdvancedFilterGroup | AdvancedFilterLeaf): Record<string, any> => {
  if (node.kind === 'leaf') {
    return serializeFilterCondition(node.filter)
  }
  const key = node.logic === 'AND' ? 'and' : 'or'
  return {
    [key]: node.children.map(child => buildAdvancedFilterDsl(child))
  }
}

const buildQueryPaginationPayload = () => {
  if (!queryPagination.enabled) return {}
  const limit = normalizeOptionalInteger(queryPagination.limit)
  const offset = normalizeOptionalInteger(queryPagination.offset)
  const payload: { limit?: number | null; offset?: number | null } = {
    limit: limit !== null && limit > 0 ? limit : null,
  }
  if (offset !== null && offset >= 0) {
    payload.offset = offset
  }
  return payload
}

const buildBuilderConfigPayload = () => {
  const whereFilters = filters.value.filter(f => !f.aggFunc)
  const havingFilters = filters.value.filter(f => !!f.aggFunc)
  const advancedScope = detectAdvancedScopeFromTree(advancedFilterTree.value)
  const payloadFilters = advancedFilterTree.value
    ? [
      buildAdvancedFilterDsl(advancedFilterTree.value),
      ...(advancedScope === 'aggregate'
        ? whereFilters.map(f => serializeFlatFilterCondition(f))
        : havingFilters.map(f => serializeFlatFilterCondition(f)))
    ]
    : filters.value.map(f => serializeFlatFilterCondition(f))

  return {
    semantic_config_id: props.semanticConfigId || undefined,
    semantic_config_name: props.semanticConfig?.name || undefined,
    mainTable: selectedTables.value[0]?.name || '',
    mainAlias: selectedTables.value[0] ? getSelectedTableAlias(selectedTables.value[0]) : '',
    joins: selectedTables.value.slice(1).map(t => ({
      id: `${getSelectedTableAlias(t)}-${t.joinOn?.leftAlias || t.joinOn?.leftTable || ''}`,
      leftTable: t.joinOn?.leftTable || selectedTables.value[0]?.name || '',
      leftAlias: t.joinOn?.leftAlias || (selectedTables.value[0] ? getSelectedTableAlias(selectedTables.value[0]) : ''),
      rightTable: t.name,
      rightAlias: getSelectedTableAlias(t),
      joinType: t.joinType || 'LEFT',
      joinConditions: (t.joinOn?.conditions || []).map(cond => ({
        leftTable: t.joinOn?.leftTable || selectedTables.value[0]?.name || '',
        leftAlias: t.joinOn?.leftAlias || (selectedTables.value[0] ? getSelectedTableAlias(selectedTables.value[0]) : ''),
        leftColumn: cond.leftCol,
        rightColumn: cond.rightCol
      })),
      leftColumn: t.joinOn?.conditions?.[0]?.leftCol || '',
      rightColumn: t.joinOn?.conditions?.[0]?.rightCol || ''
    })),
    dimensions: dimensions.value.map(d => ({
      id: d.id,
      table: d.table,
      tableAlias: getTableAlias(d),
      column: d.column,
      semanticName: d.semanticName,
      alias: d.displayName || d.column,
      expression: d.expression,
      transform: d.transform || 'raw'
    })),
    aggregations: aggregations.value.map(a => ({
      id: a.id,
      func: a.func,
      table: a.table,
      tableAlias: getTableAlias(a),
      column: a.column,
      semanticName: a.semanticName,
      alias: a.displayName || a.column,
      expression: a.expression
    })),
    filters: payloadFilters.length > 0 ? payloadFilters : whereFilters.map(f => serializeFlatFilterCondition(f)),
    orderBy: orderBy.value.map(o => ({
      id: o.id,
      table: o.table,
      tableAlias: getTableAlias(o),
      column: o.column,
      semanticName: o.semanticName,
      direction: o.direction,
      aggFunc: o.aggFunc,
      aggId: o.aggId
    })),
    ...buildQueryPaginationPayload()
  }
}

const buildSemanticBuilderConfigPayload = () => {
  const fullPayload = buildBuilderConfigPayload()
  return {
    dimensions: fullPayload.dimensions,
    aggregations: fullPayload.aggregations,
    filters: fullPayload.filters,
    orderBy: fullPayload.orderBy,
    ...buildQueryPaginationPayload()
  }
}

const builderConfigSnapshot = computed(() => buildBuilderConfigSnapshot())

const openFavoriteModal = () => {
  if (!ensureSemanticConfigSelected()) {
    return
  }
  if (!validateQueryPagination()) {
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

const compileCurrentBuilderSql = async (force = false): Promise<string> => {
  if (!validateQueryPagination()) {
    return ''
  }
  const snapshot = buildBuilderConfigSnapshot()
  if (!force && compiledSql.value && compiledSqlSnapshot.value === snapshot) {
    return compiledSql.value
  }
  if (!ensureSemanticConfigSelected()) {
    return ''
  }
  const response = await compileSemanticBuilderSql({
    datasource_id: props.datasourceId,
    semantic_config_id: props.semanticConfigId as string,
    semantic_builder_config: buildSemanticBuilderConfigPayload() as Record<string, unknown>
  })
  const sqlText = String(response.data?.sql || '').trim()
  compiledSql.value = sqlText
  compiledSqlSnapshot.value = snapshot
  return sqlText
}

const padNumber = (value: number, size = 2) => String(value).padStart(size, '0')

const formatDateKey = (date: Date) => {
  return `${date.getFullYear()}-${padNumber(date.getMonth() + 1)}-${padNumber(date.getDate())}`
}

const formatMonthKey = (date: Date) => {
  return `${date.getFullYear()}-${padNumber(date.getMonth() + 1)}`
}

const formatQuarterKey = (year: number, quarter: number) => `${year}-Q${quarter}`

const parseDateValue = (value: unknown): Date | null => {
  if (value instanceof Date && !Number.isNaN(value.getTime())) return value
  const text = String(value ?? '').trim()
  if (!text) return null
  const normalized = text.replace(/\//g, '-')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return null
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

const parseWeekKey = (value: unknown): { year: number; week: number } | null => {
  const text = String(value ?? '').trim().toUpperCase()
  if (!text) return null
  const compact = text.replace(/\s+/g, '')
  let matched = compact.match(/^(\d{4})[-_/]?W?(\d{1,2})$/)
  if (!matched && /^\d{6}$/.test(compact)) {
    matched = compact.match(/^(\d{4})(\d{2})$/)
  }
  if (!matched) return null
  const year = Number(matched[1])
  const week = Number(matched[2])
  if (!Number.isInteger(year) || !Number.isInteger(week) || week < 1 || week > 53) return null
  return { year, week }
}

const normalizeCompareKey = (value: unknown, transform: DimensionField['transform']) => {
  const text = String(value ?? '').trim()
  if (!text) return null
  switch (transform) {
    case 'year': {
      const year = Number(text)
      return Number.isInteger(year) ? String(year) : null
    }
    case 'month': {
      const date = parseDateValue(text.length <= 7 ? `${text}-01` : text)
      return date ? formatMonthKey(date) : null
    }
    case 'day': {
      const date = parseDateValue(text)
      return date ? formatDateKey(date) : null
    }
    case 'week': {
      const week = parseWeekKey(text)
      return week ? `${week.year}-W${padNumber(week.week)}` : null
    }
    case 'quarter': {
      const matched = text.toUpperCase().match(/^(\d{4})[-_/]?Q([1-4])$/)
      if (!matched) return null
      return formatQuarterKey(Number(matched[1]), Number(matched[2]))
    }
    default:
      return null
  }
}

const getPreviousCompareKey = (
  normalizedKey: string,
  transform: DimensionField['transform'],
  type: 'mom' | 'yoy'
) => {
  switch (transform) {
    case 'year': {
      const year = Number(normalizedKey)
      if (!Number.isInteger(year)) return null
      return String(year - 1)
    }
    case 'month': {
      const matched = normalizedKey.match(/^(\d{4})-(\d{2})$/)
      if (!matched) return null
      const date = new Date(Number(matched[1]), Number(matched[2]) - 1, 1)
      if (type === 'mom') date.setMonth(date.getMonth() - 1)
      else date.setFullYear(date.getFullYear() - 1)
      return formatMonthKey(date)
    }
    case 'day': {
      const date = parseDateValue(normalizedKey)
      if (!date) return null
      if (type === 'mom') date.setDate(date.getDate() - 1)
      else date.setFullYear(date.getFullYear() - 1)
      return formatDateKey(date)
    }
    case 'week': {
      const matched = normalizedKey.match(/^(\d{4})-W(\d{2})$/)
      if (!matched) return null
      const year = Number(matched[1])
      const week = Number(matched[2])
      if (type === 'yoy') return `${year - 1}-W${padNumber(week)}`
      if (week > 1) return `${year}-W${padNumber(week - 1)}`
      return `${year - 1}-W53`
    }
    case 'quarter': {
      const matched = normalizedKey.match(/^(\d{4})-Q([1-4])$/)
      if (!matched) return null
      const year = Number(matched[1])
      const quarter = Number(matched[2])
      if (type === 'yoy') return formatQuarterKey(year - 1, quarter)
      if (quarter > 1) return formatQuarterKey(year, quarter - 1)
      return formatQuarterKey(year - 1, 4)
    }
    default:
      return null
  }
}

const toNumericValue = (value: unknown): number | null => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

const applyCompareAnalysisToResult = (payload: BuilderQueryResultPayload) => {
  const warning = getCompareAnalysisWarning()
  if (warning) return { payload, warning }

  const columns = payload.columns || []
  const rows = payload.data || []
  if (columns.length < 2 || rows.length === 0) {
    return { payload, warning: '同环比结果分析需要可用的时间列和指标列，已展示原始查询结果' }
  }

  const timeDim = dimensions.value[0]
  const timeCol = columns[0] as string | undefined
  const metricCol = columns[1] as string | undefined
  if (!timeDim || !timeCol || !metricCol) {
    return { payload, warning: '同环比结果分析需要可用的时间列和指标列，已展示原始查询结果' }
  }

  const keyedRows = rows.map((row) => {
    const compareKey = normalizeCompareKey(row[timeCol], timeDim.transform || 'raw')
    return { row, compareKey }
  })
  const compareMap = new Map<string, Record<string, unknown>>()
  keyedRows.forEach(({ row, compareKey }) => {
    if (compareKey) compareMap.set(compareKey, row)
  })

  const transformedRows = rows.map((row, index) => {
    const currentValue = toNumericValue(row[metricCol])
    const compareKey = keyedRows[index]?.compareKey || null
    const previousKey = compareKey ? getPreviousCompareKey(compareKey, timeDim.transform || 'raw', compareType.value) : null
    const previousRow = previousKey ? compareMap.get(previousKey) : null
    const previousValue = previousRow ? toNumericValue(previousRow[metricCol]) : null
    const diffValue = currentValue !== null && previousValue !== null ? currentValue - previousValue : null
    const ratioValue = diffValue !== null && previousValue ? diffValue / previousValue : null

    return {
      [timeCol]: row[timeCol],
      当前值: currentValue,
      上期值: previousValue,
      差值: diffValue,
      比率: ratioValue
    }
  })

  return {
    payload: {
      ...payload,
      columns: [timeCol, '当前值', '上期值', '差值', '比率'] as string[],
      data: transformedRows,
      total_count: transformedRows.length
    },
    warning: null as string | null
  }
}

const buildAnalyzedResultEnvelope = (
  parsed: NonNullable<ReturnType<typeof parseResultEnvelope>>,
  payload: BuilderQueryResultPayload
) => {
  analyzedResultData.value = JSON.stringify({
    status: 'succeeded',
    sql: parsed.sql,
    columns: payload.columns,
    data: payload.data,
    total_count: payload.total_count,
    execution_time_ms: payload.execution_time_ms
  })
}

const ensureResultReadyForAnalysis = () => {
  if (!resultData.value) {
    ElMessage.warning('请先查询原始结果')
    return null
  }
  const parsed = parseResultEnvelope(resultData.value)
  if (!parsed || !isQuerySucceeded(parsed) || parsed.error) {
    ElMessage.warning('当前原始结果不可分析')
    return null
  }
  return parsed
}

const getDefaultAnalysisSortField = (columns: string[]) => {
  if (columns.length === 0) return ''
  const rows = currentResultEnvelope.value?.data || []
  const numericColumn = columns.find(column => rows.some(row => toNumericValue(row[column]) !== null))
  return numericColumn || columns[columns.length - 1] || ''
}

const applyResultSortAnalysis = (payload: BuilderQueryResultPayload, field: string, direction: 'ASC' | 'DESC') => {
  const columns = payload.columns || []
  const rows = [...(payload.data || [])]
  if (!field || !columns.includes(field)) {
    return { payload, warning: '请选择有效的排序字段' }
  }

  rows.sort((left, right) => {
    const leftNumeric = toNumericValue(left[field])
    const rightNumeric = toNumericValue(right[field])
    let result = 0

    if (leftNumeric !== null && rightNumeric !== null) {
      result = leftNumeric - rightNumeric
    } else {
      result = String(left[field] ?? '').localeCompare(String(right[field] ?? ''), 'zh-CN')
    }

    return direction === 'ASC' ? result : -result
  })

  return {
    payload: {
      ...payload,
      data: rows,
      total_count: rows.length
    },
    warning: null as string | null
  }
}

const applyResultTopNAnalysis = (
  payload: BuilderQueryResultPayload,
  field: string,
  direction: 'ASC' | 'DESC',
  size: number
) => {
  const columns = payload.columns || []
  const rows = [...(payload.data || [])]
  if (!Number.isInteger(size) || size <= 0) {
    return { payload, warning: 'Top N 需要输入大于 0 的整数' }
  }
  if (!field || !columns.includes(field)) {
    return { payload, warning: '请选择 Top N 排序字段' }
  }

  rows.sort((left, right) => {
    const leftNumeric = toNumericValue(left[field])
    const rightNumeric = toNumericValue(right[field])
    let result = 0

    if (leftNumeric !== null && rightNumeric !== null) {
      result = leftNumeric - rightNumeric
    } else {
      result = String(left[field] ?? '').localeCompare(String(right[field] ?? ''), 'zh-CN')
    }

    return direction === 'ASC' ? result : -result
  })

  const sliced = rows.slice(0, size)
  return {
    payload: {
      ...payload,
      data: sliced,
      total_count: sliced.length
    },
    warning: null as string | null
  }
}

const applyResultTotalAnalysis = (payload: BuilderQueryResultPayload) => {
  const columns = payload.columns || []
  const rows = payload.data || []
  if (!columns.length || !rows.length) {
    return { payload, warning: '当前结果没有可合计的数据' }
  }

  const summaryRow: Record<string, unknown> = {}
  let hasNumericColumn = false

  columns.forEach((column, index) => {
    const numericValues = rows
      .map(row => toNumericValue(row[column]))
      .filter((value): value is number => value !== null)

    if (numericValues.length > 0) {
      hasNumericColumn = true
      summaryRow[column] = numericValues.reduce((sum, value) => sum + value, 0)
      return
    }

    summaryRow[column] = index === 0 ? '合计' : null
  })

  if (!hasNumericColumn) {
    return { payload, warning: '当前结果没有可合计的数值列' }
  }

  const mergedRows = [...rows, summaryRow]
  return {
    payload: {
      ...payload,
      data: mergedRows,
      total_count: mergedRows.length
    },
    warning: null as string | null
  }
}

const applyResultPercentAnalysis = (
  payload: BuilderQueryResultPayload,
  metricColumn: string,
  scope: 'total' | 'group',
  groupColumn?: string
) => {
  const columns = payload.columns || []
  const rows = payload.data || []
  if (!columns.length || !rows.length) {
    return { payload, warning: '当前结果没有可计算百分比的数据' }
  }

  if (!metricColumn || !columns.includes(metricColumn)) {
    return { payload, warning: '请选择需要计算百分比的指标列' }
  }

  const hasNumericValues = rows.some(row => toNumericValue(row[metricColumn]) !== null)
  if (!hasNumericValues) {
    return { payload, warning: '所选指标列不是数值列，无法计算百分比' }
  }

  if (scope === 'group' && (!groupColumn || !columns.includes(groupColumn))) {
    return { payload, warning: '请选择分组维度后再计算组内占比' }
  }

  const totalsByGroup = new Map<string, number>()
  const total = rows.reduce((sum, row) => {
    const value = toNumericValue(row[metricColumn]) || 0
    if (scope === 'group') {
      const groupKey = String(row[groupColumn as string] ?? '')
      totalsByGroup.set(groupKey, (totalsByGroup.get(groupKey) || 0) + value)
    }
    return sum + value
  }, 0)

  if (scope === 'total' && !total) {
    return { payload, warning: '当前结果总计为 0，无法计算百分比' }
  }
  if (scope === 'group' && Array.from(totalsByGroup.values()).every(value => !value)) {
    return { payload, warning: '当前分组总计为 0，无法计算百分比' }
  }

  const percentColumn = scope === 'group'
    ? `${metricColumn}_组内占比`
    : `${metricColumn}_百分比`
  const transformedRows = rows.map((row) => {
    const value = toNumericValue(row[metricColumn])
    const denominator = scope === 'group'
      ? totalsByGroup.get(String(row[groupColumn as string] ?? '')) || 0
      : total
    return {
      ...row,
      [percentColumn]: value === null || !denominator ? null : value / denominator
    }
  })

  return {
    payload: {
      ...payload,
      columns: [...columns, percentColumn],
      data: transformedRows,
      total_count: transformedRows.length
    },
    warning: null as string | null
  }
}

const applyResultCompareAnalysis = (
  payload: BuilderQueryResultPayload,
  metricColumn: string,
  baselineMode: 'first' | 'max' | 'min' | 'avg'
) => {
  const columns = payload.columns || []
  const rows = payload.data || []
  if (!columns.length || !rows.length) {
    return { payload, warning: '当前结果没有可对比的数据' }
  }
  if (!metricColumn || !columns.includes(metricColumn)) {
    return { payload, warning: '请选择需要对比的指标列' }
  }

  const numericValues = rows
    .map(row => toNumericValue(row[metricColumn]))
    .filter((value): value is number => value !== null)

  if (!numericValues.length) {
    return { payload, warning: '所选指标列不是数值列，无法进行对比' }
  }

  let baselineValue: number | null = null
  if (baselineMode === 'first') {
    baselineValue = toNumericValue(rows[0]?.[metricColumn]) ?? null
  } else if (baselineMode === 'max') {
    baselineValue = Math.max(...numericValues)
  } else if (baselineMode === 'min') {
    baselineValue = Math.min(...numericValues)
  } else {
    baselineValue = numericValues.reduce((sum, value) => sum + value, 0) / numericValues.length
  }

  if (baselineValue === null) {
    return { payload, warning: '基准值为空，无法进行对比' }
  }

  const baselineColumn = `${metricColumn}_基准值`
  const diffColumn = `${metricColumn}_差值`
  const ratioColumn = `${metricColumn}_差异率`
  const transformedRows = rows.map((row) => {
    const value = toNumericValue(row[metricColumn])
    const diffValue = value === null ? null : value - baselineValue!
    return {
      ...row,
      [baselineColumn]: baselineValue,
      [diffColumn]: diffValue,
      [ratioColumn]: value === null || !baselineValue ? null : diffValue! / baselineValue
    }
  })

  return {
    payload: {
      ...payload,
      columns: [...columns, baselineColumn, diffColumn, ratioColumn],
      data: transformedRows,
      total_count: transformedRows.length
    },
    warning: null as string | null
  }
}

const runCompareAnalysis = () => {
  if (isCompareAnalysisDisabled.value) {
    ElMessage.warning('同环比结果分析暂不支持按周')
    return
  }
  const parsed = ensureResultReadyForAnalysis()
  if (!parsed) return
  try {
    const { payload: processedPayload, warning } = applyCompareAnalysisToResult(parsed)
    if (warning) {
      ElMessage.warning(warning)
      analyzedResultData.value = null
      appliedAnalysisType.value = null
      return
    }
    buildAnalyzedResultEnvelope(parsed, processedPayload)
    appliedAnalysisType.value = 'yoy'
  } catch {
    ElMessage.warning('当前原始结果无法解析，不能进行分析')
  }
}

const runSortAnalysis = () => {
  const parsed = ensureResultReadyForAnalysis()
  if (!parsed) return

  const field = resultSortField.value || getDefaultAnalysisSortField(parsed.columns || [])
  if (!field) {
    ElMessage.warning('当前结果没有可排序字段')
    return
  }

  resultSortField.value = field
  const { payload: processedPayload, warning } = applyResultSortAnalysis(parsed, field, resultSortDirection.value)
  if (warning) {
    ElMessage.warning(warning)
    return
  }

  buildAnalyzedResultEnvelope(parsed, processedPayload)
  appliedAnalysisType.value = 'sort'
}

const runTopNAnalysis = () => {
  const parsed = ensureResultReadyForAnalysis()
  if (!parsed) return

  const field = topNField.value || getDefaultAnalysisSortField(parsed.columns || [])
  if (!field) {
    ElMessage.warning('当前结果没有可用于 Top N 的排序字段')
    return
  }

  topNField.value = field
  const { payload: processedPayload, warning } = applyResultTopNAnalysis(parsed, field, topNDirection.value, Number(resultTopN.value))
  if (warning) {
    ElMessage.warning(warning)
    return
  }

  buildAnalyzedResultEnvelope(parsed, processedPayload)
  appliedAnalysisType.value = 'topn'
}

const runTotalAnalysis = () => {
  const parsed = ensureResultReadyForAnalysis()
  if (!parsed) return

  const { payload: processedPayload, warning } = applyResultTotalAnalysis(parsed)
  if (warning) {
    ElMessage.warning(warning)
    return
  }

  buildAnalyzedResultEnvelope(parsed, processedPayload)
  appliedAnalysisType.value = 'total'
}

const runPercentAnalysis = () => {
  const parsed = ensureResultReadyForAnalysis()
  if (!parsed) return

  const metricColumn = percentMetricField.value || resultAnalysisMetricOptions.value[0] || ''
  if (!metricColumn) {
    ElMessage.warning('当前结果没有可计算百分比的数值列')
    return
  }

  if (percentScope.value === 'group' && !percentGroupField.value) {
    ElMessage.warning('请选择分组维度后再计算组内占比')
    return
  }

  percentMetricField.value = metricColumn
  const { payload: processedPayload, warning } = applyResultPercentAnalysis(parsed, metricColumn, percentScope.value, percentGroupField.value)
  if (warning) {
    ElMessage.warning(warning)
    return
  }

  buildAnalyzedResultEnvelope(parsed, processedPayload)
  appliedAnalysisType.value = 'percent'
}

const runCompareBaselineAnalysis = () => {
  const parsed = ensureResultReadyForAnalysis()
  if (!parsed) return

  const metricColumn = compareMetricField.value || resultAnalysisMetricOptions.value[0] || ''
  if (!metricColumn) {
    ElMessage.warning('当前结果没有可对比的数值列')
    return
  }

  compareMetricField.value = metricColumn
  const { payload: processedPayload, warning } = applyResultCompareAnalysis(parsed, metricColumn, compareBaselineMode.value)
  if (warning) {
    ElMessage.warning(warning)
    return
  }

  buildAnalyzedResultEnvelope(parsed, processedPayload)
  appliedAnalysisType.value = 'compare'
}

const cancelCompareAnalysis = () => {
  analyzedResultData.value = null
  appliedAnalysisType.value = null
}

const cancelResultAnalysis = () => {
  analyzedResultData.value = null
  appliedAnalysisType.value = null
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
    const apiMsg = (err as any)?.response?.data?.msg
    const apiDetail = (err as any)?.response?.data?.detail
    const rawMessage = apiMsg || apiDetail || (err instanceof Error ? err.message : '')
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
  executing.value = true
  executingTaskText.value = '任务创建中...'
  executingTaskId.value = ''
  rawResultData.value = null
  analyzedResultData.value = null
  appliedAnalysisType.value = null
  executedSql.value = ''
  resultTabs.value = []
  activeResultTabIndex.value = 0
  try {
    const sqlText = await compileCurrentBuilderSql()
    if (!sqlText) {
      ElMessage.warning('请先配置查询条件')
      return
    }

    const task = await submitSqlTask({
      datasource_id: props.datasourceId,
      member_datasource_ids: selectedMemberDatasourceIds.value,
      sql: sqlText,
      query_type: 'builder',
      semantic_builder_config: buildSemanticBuilderConfigPayload() as Record<string, unknown>,
      semantic_config_id: props.semanticConfigId as string,
      query_text: '可视化查询',
      skip_query_cache: queryCacheEnabled.value ? skipQueryCache.value : false,
    }, {
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
              sql: result.sql || sqlText,
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
              sql: result.sql || sqlText,
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
          executedSql: result.sql || sqlText,
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
        executedSql: sqlText,
        status: 'error',
      }]
    }
  } catch (err) {
    const apiMsg = (err as any)?.response?.data?.msg
    const apiDetail = (err as any)?.response?.data?.detail
    const rawMessage = apiMsg || apiDetail || (err instanceof Error ? err.message : '')
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
  analyzedResultData.value = null
  appliedAnalysisType.value = null
  resultTabs.value = []
  activeResultTabIndex.value = 0
  resultSortField.value = ''
  resultSortDirection.value = 'DESC'
  resultTopN.value = 10
  topNField.value = ''
  topNDirection.value = 'DESC'
  percentMetricField.value = ''
  percentScope.value = 'total'
  percentGroupField.value = ''
  compareMetricField.value = ''
  compareBaselineMode.value = 'first'
  executedSql.value = ''
  compiledSql.value = ''
  compiledSqlSnapshot.value = ''
  previewSql.value = ''
  showSqlPreview.value = false
  savedBuilderConfigSnapshot.value = ''
}

const parseAdvancedFilterFromConfig = (node: any): AdvancedFilterGroup | AdvancedFilterLeaf | null => {
  if (!node || typeof node !== 'object') return null
  if (Array.isArray(node.and) || Array.isArray(node.or)) {
    const logic: FilterLogic = Array.isArray(node.and) ? 'AND' : 'OR'
    const source = (node[logic === 'AND' ? 'and' : 'or'] || []) as any[]
    const children = source
      .map((child) => parseAdvancedFilterFromConfig(child))
      .filter(Boolean) as Array<AdvancedFilterLeaf | AdvancedFilterGroup>
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
    ElMessage.warning(`收藏关联的指标集不可用：${configName}`)
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
      alias: (config as any).mainAlias || config.mainTable,
      label: getTableRoleLabel(config.mainTable, (config as any).mainAlias || config.mainTable, config.mainTable)
    })
  }

  // 设置关联表
  if (config.joins?.length) {
    config.joins.forEach(join => {
      const joinConditions = (join as any).joinConditions?.length
        ? (join as any).joinConditions.map((cond: any) => ({
          leftCol: cond.leftColumn || '',
          rightCol: cond.rightColumn || ''
        })).filter((cond: { leftCol: string; rightCol: string }) => cond.leftCol || cond.rightCol)
        : [{
          leftCol: join.leftColumn,
          rightCol: join.rightColumn
        }]
      selectedTables.value.push({
        name: join.rightTable,
        alias: (join as any).rightAlias || join.rightTable,
        label: getTableRoleLabel(join.rightTable, (join as any).rightAlias || join.rightTable, join.rightTable),
        joinType: join.joinType,
        joinOn: {
          leftTable: join.leftTable || config.mainTable,
          leftAlias: (join as any).leftAlias || join.leftTable || (config as any).mainAlias || config.mainTable,
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
        tableAlias: (d as any).tableAlias || d.table,
        column: d.column,
        semanticName: (d as any).semanticName,
        expression: (d as any).expression,
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
      return {
        id: a.id || `${Date.now()}-${Math.random()}`,
        table: a.table,
        tableAlias: (a as any).tableAlias || a.table,
        column: a.column,
        semanticName: (a as any).semanticName,
        displayName: a.alias || a.column,
        expression: (a as any).expression,
        label: `${tableLabel}.${colLabel}`,
        func: a.func
      }
    })
  }

  // 设置筛选
  if (config.filters?.length) {
    const flatFilters: FilterCondition[] = []
    let whereTree: AdvancedFilterGroup | null = null
    ;(config.filters as any[]).forEach((f) => {
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
      tableAlias: (o as any).tableAlias || o.table,
      column: o.column,
      semanticName: (o as any).semanticName,
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
      fields: aggregations.value.map(a => {
        const funcLabel = aggFunctions.find(f => f.value === a.func)?.label || a.func
        return {
          value: `AGGID:${a.id}`,
          label: `${funcLabel}(${getMetricDisplayName(a)})`
        }
      })
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

const detailFilterFieldsGrouped = computed(() => {
  const selectedDetailGroups = filterAndOrderFieldsGrouped.value.filter(group => group.groupLabel !== '聚合过滤字段 (HAVING)')
  return selectedDetailGroups.length > 0 ? selectedDetailGroups : allDetailFieldsGrouped.value
})

const aggregateFilterFieldsGrouped = computed(() => {
  return filterAndOrderFieldsGrouped.value.filter(group => group.groupLabel === '聚合过滤字段 (HAVING)')
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
    const funcLabel = getAggFuncLabel(a.func)
    merged.set(`AGGID:${a.id}`, {
      value: `AGGID:${a.id}`,
      label: `${funcLabel}(${getMetricDisplayName(a)})`
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
    if (agg) return `${getAggFuncLabel(agg.func)}(${getMetricDisplayName(agg)})`
  }
  if (o.aggFunc) {
    const agg = aggregations.value.find(a => buildFieldKey(a.tableAlias, a.table, a.column) === buildFieldKey(o.tableAlias, o.table, o.column) && a.func === o.aggFunc)
    if (agg) return `${getAggFuncLabel(agg.func)}(${getMetricDisplayName(agg)})`
    return `${getAggFuncLabel(o.aggFunc)}(${o.column})`
  }
  const dim = dimensions.value.find(d => buildFieldKey(d.tableAlias, d.table, d.column) === buildFieldKey(o.tableAlias, o.table, o.column))
  if (dim) return getDimensionDisplayName(dim)
  return o.column ? `${o.table}.${o.column}` : '未选择字段'
}

const hasOrderFieldOption = (o: OrderByField) => {
  const value = getOrderFieldValue(o)
  return filterAndOrderFieldsGrouped.value.some(group => group.fields.some(field => field.value === value))
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
const handleOrderFieldChange = (o: OrderByField, value: string) => {
  const parsed = parseFieldValue(value)
  o.table = parsed.table
  o.tableAlias = parsed.tableAlias || parsed.table
  o.column = parsed.column
  o.semanticName = parsed.semanticName
  o.aggFunc = parsed.aggFunc
  o.aggId = parsed.aggId
}

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

watch(resultAnalysisFieldOptions, (columns) => {
  if (!columns.length) {
    resultSortField.value = ''
    topNField.value = ''
    percentMetricField.value = ''
    percentGroupField.value = ''
    compareMetricField.value = ''
    return
  }
  if (!columns.includes(resultSortField.value)) {
    resultSortField.value = getDefaultAnalysisSortField(columns)
  }
  if (!columns.includes(topNField.value)) {
    topNField.value = getDefaultAnalysisSortField(columns)
  }
  if (!resultAnalysisMetricOptions.value.includes(percentMetricField.value)) {
    percentMetricField.value = resultAnalysisMetricOptions.value[0] || ''
  }
  if (!resultAnalysisDimensionOptions.value.includes(percentGroupField.value)) {
    percentGroupField.value = resultAnalysisDimensionOptions.value[0] || ''
  }
  if (!resultAnalysisMetricOptions.value.includes(compareMetricField.value)) {
    compareMetricField.value = resultAnalysisMetricOptions.value[0] || ''
  }
}, { immediate: true })

watch(filters, (list) => {
  if (!advancedFilterTree.value) return
  list.forEach((f) => {
    syncAdvancedTreeLeafByFilter(f)
  })
}, { deep: true })

watch([() => props.semanticConfig, tables, dimensions, aggregations, filters, orderBy], ([semanticConfig]) => {
  if (!semanticConfig) return
  const used = new Set<string>()
  dimensions.value.forEach(d => used.add(getTableAlias(d)))
  aggregations.value.forEach(a => used.add(getTableAlias(a)))
  filters.value.forEach(f => used.add(getTableAlias(f)))
  orderBy.value.forEach(o => used.add(getTableAlias(o)))
  selectedTables.value = buildSemanticSelectedTables(semanticConfig, used)
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

    <!-- 图表类型面板 -->
    <div
      v-if="!props.hideChartPanel"
      class="flex flex-col border-r border-[#e3e5eb] bg-white shrink-0 transition-all duration-200"
      :style="{ width: isChartPanelCollapsed ? '0px' : '220px' }"
    >
      <template v-if="!isChartPanelCollapsed">
        <!-- 顶部栏：Tab + 折叠按钮 -->
        <div class="flex items-center border-b border-[#e3e5eb] shrink-0 h-[40px]">
          <div class="flex-1 flex items-center justify-center">
            <button
              class="relative h-[40px] px-4 text-[13px] font-medium transition-colors"
              :class="chartPanelTab === 'chart' ? 'text-[#165DFF]' : 'text-[#57585a] hover:text-[#2e2f32]'"
              @click="chartPanelTab = 'chart'"
            >
              图表展示
              <span v-if="chartPanelTab === 'chart'" class="absolute bottom-0 left-1/2 -translate-x-1/2 w-full h-[2px] bg-[#165DFF] rounded-t"></span>
            </button>
          </div>
          <div class="flex-1 flex items-center justify-center">
            <button
              class="relative h-[40px] px-4 text-[13px] font-medium transition-colors"
              :class="chartPanelTab === 'analysis' ? 'text-[#165DFF]' : 'text-[#57585a] hover:text-[#2e2f32]'"
              @click="chartPanelTab = 'analysis'"
            >
              结果分析
              <span v-if="chartPanelTab === 'analysis'" class="absolute bottom-0 left-1/2 -translate-x-1/2 w-full h-[2px] bg-[#165DFF] rounded-t"></span>
            </button>
          </div>
          <button
            class="w-5 h-5 flex items-center justify-center rounded text-[#57585a] hover:bg-[#f0f1f3] transition-all mr-3 shrink-0"
            title="收起"
            @click="isChartPanelCollapsed = true"
          >
            <i class="fa-solid fa-chevron-left text-[10px]"></i>
          </button>
        </div>
        <!-- 可滚动内容 -->
        <div class="flex-1 overflow-y-auto">
          <!-- 图表 Tab -->
          <div v-show="chartPanelTab === 'chart'" class="px-4 py-2.5">
            <div class="grid grid-cols-5 gap-x-1 gap-y-3">
              <!-- 表格 TableSelected -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'table' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="表格" @click="selectedChartType = 'table'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><path fill="#fff" d="M1.596 6.872h16.863v10.455H1.596z"/><path fill="#165DFF" d="M18.125 2.083a.833.833 0 0 1 .834.834v14.166a.833.833 0 0 1-.834.834H1.875a.833.833 0 0 1-.833-.834V2.917a.833.833 0 0 1 .833-.834h16.25ZM1.875 7.292v9.791h16.25V7.292H1.875Z"/><path fill="#8CB8FF" d="M7.708 7.292v3.125h4.584V7.292h.833v3.125h5v3.541h-5v3.125h-.833v-3.125H7.708v3.125h-.833v-3.125h-5v-3.541h5V7.292h.833Z"/></svg>
              </button>
              <!-- 柱状图 PillarchartSelected -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'bar' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="柱状图" @click="selectedChartType = 'bar'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><path fill="#8CB8FF" d="M6.909 4.806a.836.836 0 0 1 .835.836v9.612a.836.836 0 0 1-.835.836H5.237a.836.836 0 0 1-.836-.836V5.642a.836.836 0 0 1 .836-.836H6.91Zm4.806-2.717a.836.836 0 0 1 .835.836v12.329a.836.836 0 0 1-.835.836h-1.672a.836.836 0 0 1-.836-.836V2.925a.836.836 0 0 1 .836-.835h1.672Zm4.805 6.06a.836.836 0 0 1 .836.836v6.269a.836.836 0 0 1-.836.836h-1.67a.836.836 0 0 1-.836-.836V8.985a.836.836 0 0 1 .836-.836h1.671Z"/><path fill="#165DFF" d="M2.292 1.667a.625.625 0 0 1 .625.625v14.791h14.792a.625.625 0 0 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.292a.625.625 0 0 1 .625-.625Zm14.23 6.482a.836.836 0 0 1 .835.836v2.89h-3.343v-2.89a.836.836 0 0 1 .836-.836h1.671ZM6.908 4.806a.836.836 0 0 1 .836.836v4.566H4.402V5.642a.836.836 0 0 1 .836-.836h1.671Zm4.807-2.716a.836.836 0 0 1 .835.836v5.616H9.208V2.925a.836.836 0 0 1 .836-.835h1.671Z"/></svg>
              </button>
              <!-- 条形图 BarchartSelected（横向） -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'horizontal_bar' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="条形图" @click="selectedChartType = 'horizontal_bar'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><path fill="#8CB8FF" d="M15.517 13.225a.833.833 0 0 0-.833-.833h-9.58a.833.833 0 0 0-.833.833v1.666a.833.833 0 0 0 .833.833h9.58a.834.834 0 0 0 .833-.833v-1.666Zm2.708-4.79a.833.833 0 0 0-.833-.833H5.105a.833.833 0 0 0-.833.833v1.666a.833.833 0 0 0 .833.833h12.287a.834.834 0 0 0 .833-.833V8.435Zm-6.04-4.79a.833.833 0 0 0-.833-.833H5.104a.833.833 0 0 0-.833.833v1.666a.833.833 0 0 0 .833.833h6.248a.833.833 0 0 0 .833-.833V3.645Z"/><path fill="#165DFF" d="M2.292 1.666a.625.625 0 0 1 .625.625v14.792h14.792a.625.625 0 1 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.29a.625.625 0 0 1 .625-.625Zm12.391 10.725a.833.833 0 0 1 .832.792l.001.041v1.666a.833.833 0 0 1-.791.832l-.042.002H8.435V12.39h6.248Zm2.707-4.79a.833.833 0 0 1 .832.792l.001.042V10.1a.833.833 0 0 1-.791.832l-.042.002h-5.623V7.6h5.623Zm-6.039-4.79a.833.833 0 0 1 .832.792l.001.042V5.31a.834.834 0 0 1-.792.831l-.041.002H9.268V2.81h2.083Z"/></svg>
              </button>
              <!-- 折线图 LinechartSelected -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'line' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="折线图" @click="selectedChartType = 'line'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><path fill="#8CB8FF" d="M11.976 9.682a.625.625 0 0 0 .733-.026l4.544-3.54a.625.625 0 0 0-.769-.986l-4.186 3.262-3.979-2.678a.625.625 0 0 0-.702.003L4.019 8.185a.625.625 0 0 0 .707 1.03l3.247-2.227 4.002 2.693Z"/><path fill="#165DFF" d="M2.292 1.666a.625.625 0 0 1 .625.625v14.792h14.792a.625.625 0 1 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.29a.625.625 0 0 1 .625-.625Zm14.193 8.046a.625.625 0 0 1 .768.985l-4.543 3.54a.625.625 0 0 1-.733.026L7.974 11.57l-3.247 2.228a.625.625 0 0 1-.707-1.03L7.617 10.3a.625.625 0 0 1 .703-.003l3.979 2.678 4.186-3.262Z"/></svg>
              </button>
              <!-- 面积图 -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'area' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="面积图" @click="selectedChartType = 'area'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><path fill="#9BF" fill-opacity=".5" d="M4 8.2l3.6-2.5 4 2.7 4.5-3.5 1.9 1.5V17H4z"/><path fill="#E5ECFF" fill-opacity=".5" d="M4 12.8l3.6-2.2 4 2.7 4.5-3.5 1.9 1.5V17H4z"/><path fill="#165DFF" d="M2.292 1.666a.625.625 0 0 1 .625.625v14.792h14.792a.625.625 0 1 1 0 1.25H2.292a.625.625 0 0 1-.625-.625V2.29a.625.625 0 0 1 .625-.625Z"/><path stroke="#165DFF" stroke-width="1" stroke-linecap="round" stroke-linejoin="round" d="M4 8.2l3.6-2.5 4 2.7 4.5-3.5 1.9 1.5"/><path stroke="#8CB8FF" stroke-width="1" stroke-linecap="round" stroke-linejoin="round" d="M4 12.8l3.6-2.2 4 2.7 4.5-3.5 1.9 1.5"/></svg>
              </button>
              <!-- 饼图 PiechartSelected -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'pie' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="饼图" @click="selectedChartType = 'pie'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><g clip-path="url(#pie_clip)"><path fill="#165DFF" d="m14.701 17.86-.123.072.123-.073Zm.118-.072-.074.045.074-.045Zm.13-.083a6.126 6.126 0 0 1-.08.052l.08-.052ZM10.002.833a9.163 9.163 0 0 1 4.989 16.845l.11-.073-5.104-7.603.005-.006V.833Z"/><path fill="#8CB8FF" d="m9.997 10.002 5.105 7.603A9.164 9.164 0 0 1 3.52 16.48l6.477-6.477Z"/><path fill="#9BF" d="M9.996.833v9.163H.833A9.163 9.163 0 0 1 9.996.833Z"/><path fill="#E5ECFF" d="m9.996 9.996-6.48 6.48a9.164 9.164 0 0 1-2.683-6.48h9.163Z"/></g><defs><clipPath id="pie_clip"><path fill="#fff" d="M0 0h20v20H0z"/></clipPath></defs></svg>
              </button>
              <!-- 指标卡 measurecard-selected01 -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'kpi' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="指标卡" @click="selectedChartType = 'kpi'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><g clip-path="url(#card_clip)"><path fill="#165DFF" d="M4.724 7.838c.064.58-.118 1.16-.487 1.553L2.3 12.4h2.424v1.047H.829v-1.006l2.424-3.608a1.55 1.55 0 0 0 .197-.954c0-.354 0-.803-.672-.803-.406 0-.673.204-.673.558v.6H.76V7.58c0-.899.87-1.607 2.017-1.607 1.46.013 1.948.858 1.948 1.865h-.001Zm8.551-1.865h1.287v7.474h-1.287V7.073l-1.287.802v-1.1l1.287-.802Zm5.95 1.865c.07.576-.103 1.155-.463 1.553L16.816 12.4h2.42v1.047h-3.915v-1.006l2.422-3.608a1.55 1.55 0 0 0 .196-.954c0-.354 0-.803-.672-.803-.405 0-.671.204-.671.558v.6h-1.332V7.58c0-.899.868-1.607 2.015-1.607 1.414.013 1.946.858 1.946 1.865Zm-8.739 1.738c.8.41.8.655.8 1.706s-.068 1.201-.324 1.501c-.424.477-1.003.716-1.588.655-.858 0-1.924-.396-1.924-1.556v-.627h1.32v.56c0 .354.267.545.661.545s.66-.246.66-.6v-1.065c0-.546-.196-.696-1.054-.696v-.86h-.14c.801 0 1.067-.095 1.067-.655V7.53c0-.3-.266-.505-.66-.505-.395 0-.708.3-.708.505v.6H7.31v-.6c0-.86.927-1.556 1.982-1.556a2.292 2.292 0 0 1 1.46.505c.395.355.534.655.534 1.556 0 .9-.069 1.2-.8 1.542v-.001Zm-3.41 1.859-.895 2.587h-.743l.652-2.587h.986Z"/><path fill="#8CB8FF" d="m2.379 16.393.015.022 1.37 2.253a.283.283 0 0 1-.016.303.13.13 0 0 1-.043.038c-.017.01-.055.015-.055.015H.91c-.083 0-.15-.096-.15-.215 0-.052.013-.102.036-.14l1.37-2.254c.054-.09.15-.1.213-.022Zm16.925.313v2.087h-14.1v-2.087h14.1Z"/><path fill="#9BF" d="M.76.403h8.403v3.07H.76V.403Z"/></g><defs><clipPath id="card_clip"><path fill="#fff" d="M0 0h20v20H0z"/></clipPath></defs></svg>
              </button>
              <!-- 地图 MapSelected -->
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'map' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="地图" @click="selectedChartType = 'map'">
                <svg class="w-[20px] h-[20px]" viewBox="0 0 20 20" fill="none"><path fill="#D9E6FF" d="m14.803 8.986.068.119s1.51.061 1.51.203c0 .143-1.047 1.221-.745 1.262.302.04.886 1.22.886 1.22s-.322-.04-.06.225c.262.264-.121.142-.322.345.284.136.443.123.443.123v.447s-.081.366-.323.631c-.241.265-.362.326-.362.326v.101s-.1.63-.322.936c-.222.305-.685.61-.886.753-.201.143-.926.061-.926.061s-.262.183-.322.326c-.045.106-.625.234-.919.293l-.053-.125.22-.445h.44v-.889l-.22-.444v-.222h-.66v-.89l-.44-.888h.44v-1.111h.22l.88.444h.44l.66.222.22-.444-.44-.222v-.445h.44l-.22-.444-.44-.222.22-.667.573-.58Zm1.477 6.06c.142-.55.504-.529.504-.529.262.204-.04.468 0 .723.04.254-.235.64-.235.64s-.027-.06-.268-.183c-.242-.122-.08-.427 0-.65v-.001Z"/><path fill="#8CB8FF" d="m16.208 5.565.44.222h.66l.44.223.66.222h.272a.642.642 0 0 1-.024.045c-.14.244-.14.244-.342.142-.02.366-.484.57-.484.57l.162.224h-.504s-.06.366-.261.447c-.202.082-.323.265-.403.428-.604.04-1.067 1.261-.826.285.092-.372.07-.538-.01-.579v-.007l-.004.005c-.133-.058-.413.202-.61.438l-.033.039c-.316.355-.692.45-.692.45l.154.267-.574.579-.22.667.44.222.22.444h-.44v.445l.44.222-.22.445-.66-.223h-.44l-.879-.444h-.22v1.111h-.44l.44.889v.889h.66v.222l.22.444v.89h-.44l-.22.444.053.125-.17.032s.101.184.101.346c0 .163.363.265.202.387-.161.122-.383.59-.765.529-.383-.061-.04-.672.2-.651.243.02.202-.204.041-.53-.16-.325.04-.264-.443-.142-.483.122-.765-.63-.825-.671-.06-.041-.322-.102-.463-.102-.141 0-.242.224-.322.224-.081 0-.303-.082-.303-.082s-.18.123-.281.123c-.101 0-.081-.184-.182-.184-.1 0-.18.204-.18.204l-.202-.082.06.57H8.77l-.121-.244-.181.102-.242-.366h-.231l.17-.408h-.17s.03-.203-.091-.406c-.906.06-.262-.407.09-.794.233-.255.075-.671-.077-.935l.155-.156h.22v-1.334l-.22-.444v-.667l.22-.222.44.667.439.222.44-.222.44-.445.44.445.22.444.44-.889.22-.222.439-.222-.22-.445v-.444l.66-.667 1.1-.666h.44l.22-.445.439-.222.44.222.44-.222.66-.222.439-.445-.22-.444h-.44v-.445l.44-.222Z"/><path fill="#165DFF" d="M14.085 4.04c-.003.013-.045.2.343.141.403-.061.403-.59.685-.916.282-.325-.222-.386-.222-.386.725-.814 1.188-.305 1.188-.305s.121-.041.322 0c.186.037.627 1.057.696 1.22.06.001.19.012.452.041.725.081.363.63.645.672.281.04.925-.652 1.046-.53.121.123-.16 1.303-.241 1.303-.08 0-.443.244-.343.346.09.09.132.344.04.572l-.068.034h-.22l-.66-.222-.44-.223h-.66l-.44-.222-.44.222v.445h.44l.22.444-.44.445-.659.222-.44.222-.44-.222-.44.222-.22.445h-.439l-1.1.666-.659.667v.444l.22.445-.44.222-.22.222-.44.889-.22-.444-.44-.445-.44.445-.44.222-.439-.222-.44-.667-.22.222v.667l.22.444v1.334h-.22l-.154.156a1.13 1.13 0 0 0-.174-.245c-.06-.04-.101.285-.162.265-.06-.02-.443-.184-.563-.184-.121 0-.463.306-.846.387-.382.081-.624-.102-.624-.102s.06-.203-.06-.37c-.121-.167-.383-.159-.625-.301-.241-.143-.302.488-.463.244-.16-.244-.1-.224-.382-.244-.282-.02-.403-.265-.725-.387-.322-.122-.383-.63-.564-.63-.181 0-.302-.59-.443-.652a.742.742 0 0 1-.021-.01c.017.02.016.059-.18.092-.363.06-.341-.429-.463-.509-.302-.081-.222-.305-.222-.447 0-.143.1-.367.1-.367s-.08.224.122.204c.201-.02-.02-.875.02-.835.04.041.181.102.221 0 .04-.101-.322-.223-.322-.223s.06-.224.06-.367c-.483-.203-.503-.63-.523-.712-.02-.081-.302-.305-.286-.447.013-.118.103-.042.134-.011l.011.01s.143-.095.08-.427c-.382-.203-.18-.508-.1-.691.08-.184.684-.102.684-.102s0 .448.222.244c.222-.204.342-.061.383-.142.04-.082 1.006-.204 1.006-.204V5.89s.262-.183.302-.366c.04-.183-.08-.61 0-.57.08.041.685-.02.645-.081-.04-.061.342-.814.423-.61.08.203.704.284.684.101-.02-.183.302-.427.362-.427s.363-.305.363-.305.14.081.1.224c-.04.142.162.345.202.407.04.06.282.183.282.284 0 .102.16.346.16.346l-.06.37-.1.383.886.265.423.407.281.549s.402.14.584.142c1.108 0 .866.285 1.088.285.221 0 .966.407.946.346-.02-.061.363-.224.544-.346.18-.122.584.081.865 0 .282-.081.469-.366.469-.366s.247-.09.135-.224c-.322-.183.02-.651.101-.55.08.102.443.164.443.164l.322-.367c.282.265.443-.203.463-.264.02-.061.415-.285.415-.285s.25.122.512 0c.261-.122-.222-.753-.705-.427-.02-.122-.343.122-.443 0-.1-.122.181-.936.181-.936Z"/></svg>
              </button>
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'funnel' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="漏斗图" @click="selectedChartType = 'funnel'">
                <i class="fa-solid fa-filter text-[18px] text-[#165DFF]"></i>
              </button>
              <button class="flex items-center justify-center w-[32px] h-[32px] mx-auto rounded transition-all cursor-pointer" :class="selectedChartType === 'graph' ? 'bg-[#e3e5eba3]' : 'hover:bg-[#f0f1f3]'" title="关系图" @click="selectedChartType = 'graph'">
                <i class="fa-solid fa-diagram-project text-[18px] text-[#165DFF]"></i>
              </button>
            </div>
            <!-- 基本格式 -->
            <div class="border-t border-[#e3e5eb]">
              <div class="flex items-center justify-between px-1 py-2.5 cursor-pointer select-none" @click="chartFormatCollapsed = !chartFormatCollapsed">
                <span class="text-[12px] font-medium text-[#2e2f32]">基本格式</span>
                <i class="fa-solid fa-chevron-up text-[10px] text-[#57585a] transition-transform duration-150" :class="chartFormatCollapsed && 'rotate-180'"></i>
              </div>
              <div v-show="!chartFormatCollapsed" class="px-1 pb-3">
                <div class="grid grid-cols-2 gap-x-2 gap-y-2">
                  <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                    <input type="checkbox" v-model="chartFormat.showIndex" class="accent-[#165DFF] w-3.5 h-3.5" />序号
                  </label>
                  <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                    <input type="checkbox" v-model="chartFormat.autoWrap" class="accent-[#165DFF] w-3.5 h-3.5" />自动换行
                  </label>
                  <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                    <input type="checkbox" v-model="chartFormat.metricsFirst" class="accent-[#165DFF] w-3.5 h-3.5" />指标在前
                  </label>
                </div>
              </div>
            </div>
            <!-- 翻页器 -->
            <div class="border-t border-[#e3e5eb]">
              <div class="flex items-center justify-between px-1 py-2.5">
                <div class="flex items-center gap-2">
                  <span class="text-[12px] font-medium text-[#2e2f32]">翻页器</span>
                  <button class="relative w-[28px] h-[16px] rounded-full transition-colors duration-200" :class="chartPagination.enabled ? 'bg-[#165DFF]' : 'bg-[#c9cdd4]'" @click="chartPagination.enabled = !chartPagination.enabled">
                    <span class="absolute top-[2px] w-[12px] h-[12px] bg-white rounded-full shadow transition-all duration-200" :class="chartPagination.enabled ? 'left-[14px]' : 'left-[2px]'"></span>
                  </button>
                </div>
                <i class="fa-solid fa-chevron-up text-[10px] text-[#57585a] cursor-pointer transition-transform duration-150" :class="chartPaginationCollapsed && 'rotate-180'" @click="chartPaginationCollapsed = !chartPaginationCollapsed"></i>
              </div>
              <div v-show="!chartPaginationCollapsed" class="px-1 pb-3">
                <div class="flex items-center gap-2 mb-2">
                  <span class="text-[12px] text-[#57585a] shrink-0">每页显示</span>
                  <select v-model.number="chartPagination.pageSize" class="w-[52px] px-1 py-0.5 border border-[#e3e5eb] rounded text-[12px] bg-white text-[#2e2f32] outline-none cursor-pointer">
                    <option :value="10">10</option>
                    <option :value="20">20</option>
                    <option :value="50">50</option>
                    <option :value="100">100</option>
                  </select>
                  <span class="text-[12px] text-[#57585a]">条/页</span>
                </div>
              </div>
            </div>
          </div>
          <!-- 分析 Tab -->
          <div v-show="chartPanelTab === 'analysis'" class="px-3 pt-3 pb-4">
            <!-- 分析类型图标网格 -->
            <div class="grid grid-cols-3 gap-x-1 gap-y-3 mb-3">
              <!-- 排序 SortSelected -->
              <button class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all" :class="selectedAnalysisType === 'sort' ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]' : 'hover:bg-[#f0f1f3]'" @click="selectedAnalysisType = 'sort'">
                <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 21 20" fill="none"><path fill="#165DFF" fill-rule="evenodd" d="M10.042 2.083h7.917c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-7.917a.833.833 0 0 1-.833-.834V2.917c0-.46.373-.834.833-.834Zm0 4.375h6.459c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-6.459a.833.833 0 0 1-.833-.834V7.292c0-.46.373-.834.833-.834Zm0 4.375h4.584c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-4.584a.833.833 0 0 1-.833-.834v-1.041c0-.46.373-.834.833-.834Zm0 4.375h2.709c.46 0 .833.373.833.834v1.041c0 .46-.373.834-.833.834h-2.709a.833.833 0 0 1-.833-.834v-1.041c0-.46.373-.834.833-.834Z" clip-rule="evenodd"/><path fill="#8CB8FF" fill-rule="evenodd" d="M4.981 1.875c.46 0 .834.373.834.833v10.834c0 .072-.01.142-.026.208h1.84a.417.417 0 0 1 .34.658l-.021.027-3.064 3.31a.417.417 0 0 1-.615.025l-.023-.025-3.064-3.31a.417.417 0 0 1 .288-.684l.03-.001h1.841a.835.835 0 0 1-.026-.208V2.708c0-.46.373-.833.833-.833h.833Z" clip-rule="evenodd"/></svg>
                <span class="text-[10px] leading-tight" :class="selectedAnalysisType === 'sort' ? 'text-[#165DFF]' : 'text-[#57585a]'">排序</span>
              </button>
              <!-- Top N（显示数量） -->
              <button class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all" :class="selectedAnalysisType === 'topn' ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]' : 'hover:bg-[#f0f1f3]'" @click="selectedAnalysisType = 'topn'">
                <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 21 20" fill="none"><path fill="#165DFF" fill-rule="evenodd" d="M2.542 10h2.709c.46 0 .833.373.833.833v5.834c0 .46-.373.833-.833.833H2.542a.833.833 0 0 1-.833-.833v-5.834c0-.46.373-.833.833-.833Z" clip-rule="evenodd"/><path fill="#8CB8FF" fill-rule="evenodd" d="M8.583 2.5H11.5c.46 0 .833.373.833.833v13.334c0 .46-.373.833-.833.833H8.583a.833.833 0 0 1-.833-.833V3.333c0-.46.373-.833.833-.833Z" clip-rule="evenodd"/><path fill="#165DFF" fill-rule="evenodd" d="M14.833 5.833h2.709c.46 0 .833.373.833.834v10c0 .46-.373.833-.833.833h-2.709a.833.833 0 0 1-.833-.833v-10c0-.46.373-.834.833-.834Z" clip-rule="evenodd"/></svg>
                <span class="text-[10px] leading-tight" :class="selectedAnalysisType === 'topn' ? 'text-[#165DFF]' : 'text-[#57585a]'">Top N</span>
              </button>
              <!-- 合计 -->
              <button class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all" :class="selectedAnalysisType === 'total' ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]' : 'hover:bg-[#f0f1f3]'" @click="selectedAnalysisType = 'total'">
                <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 24 24" fill="none"><path d="M17 5H7l5 7-5 7h10" stroke="#165DFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <span class="text-[10px] leading-tight" :class="selectedAnalysisType === 'total' ? 'text-[#165DFF]' : 'text-[#57585a]'">合计</span>
              </button>
              <!-- 百分比 -->
              <button class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all" :class="selectedAnalysisType === 'percent' ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]' : 'hover:bg-[#f0f1f3]'" @click="selectedAnalysisType = 'percent'">
                <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 24 24" fill="none"><circle cx="8" cy="8" r="2.5" stroke="#165DFF" stroke-width="1.8"/><circle cx="16" cy="16" r="2.5" stroke="#165DFF" stroke-width="1.8"/><path d="M18 6L6 18" stroke="#8CB8FF" stroke-width="1.8" stroke-linecap="round"/></svg>
                <span class="text-[10px] leading-tight" :class="selectedAnalysisType === 'percent' ? 'text-[#165DFF]' : 'text-[#57585a]'">百分比</span>
              </button>
              <!-- 对比 -->
              <button class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all" :class="selectedAnalysisType === 'compare' ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]' : 'hover:bg-[#f0f1f3]'" @click="selectedAnalysisType = 'compare'">
                <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 24 24" fill="none"><rect x="3" y="5" width="10" height="10" rx="2" fill="#165DFF" fill-opacity="0.6"/><rect x="11" y="9" width="10" height="10" rx="2" fill="#8CB8FF" fill-opacity="0.6"/></svg>
                <span class="text-[10px] leading-tight" :class="selectedAnalysisType === 'compare' ? 'text-[#165DFF]' : 'text-[#57585a]'">对比</span>
              </button>
              <!-- 同环比 -->
              <button class="flex flex-col items-center py-2 px-0.5 rounded cursor-pointer transition-all" :class="selectedAnalysisType === 'yoy' ? 'bg-[#E8F3FF] ring-1 ring-[#BEDAFF]' : 'hover:bg-[#f0f1f3]'" @click="selectedAnalysisType = 'yoy'">
                <svg class="w-[20px] h-[20px] mb-1" viewBox="0 0 21 20" fill="none"><circle cx="10.042" cy="10" r="2.5" fill="#165DFF"/><path fill="#165DFF" fill-rule="evenodd" d="M8.72 4.87a.833.833 0 0 0-1.1-.423A6.083 6.083 0 0 0 4 10.003a6.082 6.082 0 0 0 3.602 5.549.833.833 0 1 0 .682-1.52 4.416 4.416 0 0 1-2.617-4.029 4.416 4.416 0 0 1 2.63-4.033c.42-.187.61-.68.423-1.1Zm3.764-.42a.833.833 0 0 0-.689 1.517 4.431 4.431 0 0 1 2.622 4.03c0 1.751-1.042 3.318-2.634 4.035a.833.833 0 1 0 .684 1.52c2.183-.983 3.616-3.14 3.616-5.555 0-2.41-1.425-4.56-3.599-5.548Z" clip-rule="evenodd"/><path fill="#8CB8FF" fill-rule="evenodd" d="M13.024 1.295a.833.833 0 0 0-.542 1.576A7.53 7.53 0 0 1 17.542 10a7.53 7.53 0 0 1-5.063 7.13.833.833 0 0 0 .542 1.576A9.197 9.197 0 0 0 19.208 10a9.197 9.197 0 0 0-6.184-8.704Zm-4.901.52a.833.833 0 0 0-1.057-.52C3.396 2.538.875 6.026.875 10.001c0 3.97 2.514 7.455 6.179 8.703a.833.833 0 0 0 .537-1.577c-2.991-1.02-5.05-3.872-5.05-7.126 0-3.258 2.064-6.113 5.06-7.13a.833.833 0 0 0 .522-1.056Z" clip-rule="evenodd"/></svg>
                <span class="text-[10px] leading-tight" :class="selectedAnalysisType === 'yoy' ? 'text-[#165DFF]' : 'text-[#57585a]'">同环比</span>
              </button>
            </div>
            <!-- 分隔线 -->
            <div class="border-t border-[#e3e5eb] mb-3"></div>
            <!-- 排序配置 -->
            <template v-if="selectedAnalysisType === 'sort'">
              <div class="space-y-3">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[12px] font-medium text-[#2e2f32]">排序</span>
                </div>
                <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                  <div class="text-[11px] text-[#57585a] mb-1.5">基于当前查询结果排序，不改 SQL</div>
                  <select v-model="resultSortField" class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]">
                    <option v-for="field in resultAnalysisFieldOptions" :key="field" :value="field">{{ field }}</option>
                  </select>
                  <div class="flex items-center gap-4">
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input v-model="resultSortDirection" type="radio" value="ASC" class="accent-[#165DFF]" />
                      升序
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input v-model="resultSortDirection" type="radio" value="DESC" class="accent-[#165DFF]" />
                      降序
                    </label>
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasRawResult || executing || !resultSortField"
                    @click="runSortAnalysis"
                  >
                    开始分析
                  </button>
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasAnalyzedResult"
                    @click="cancelResultAnalysis"
                  >
                    取消分析
                  </button>
                </div>
              </div>
            </template>
            <!-- Top N 配置（显示数量） -->
            <template v-else-if="selectedAnalysisType === 'topn'">
              <div class="space-y-3">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[12px] font-medium text-[#2e2f32]">Top N</span>
                </div>
                <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                  <div class="text-[11px] text-[#57585a] mb-1.5">排序字段</div>
                  <select v-model="topNField" class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]">
                    <option v-for="field in resultAnalysisFieldOptions" :key="field" :value="field">{{ field }}</option>
                  </select>
                  <div class="flex items-center gap-4 mb-2">
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input v-model="topNDirection" type="radio" value="DESC" class="accent-[#165DFF]" />
                      取最大前 N
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input v-model="topNDirection" type="radio" value="ASC" class="accent-[#165DFF]" />
                      取最小前 N
                    </label>
                  </div>
                  <div class="text-[11px] text-[#57585a] mb-1.5">显示数量</div>
                  <input v-model.number="resultTopN" type="number" min="1" max="10000" class="w-full px-2 py-1.5 border border-[#e3e5eb] rounded text-[12px] text-[#2e2f32] outline-none focus:border-[#165DFF] bg-white" />
                </div>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasRawResult || executing || !topNField"
                    @click="runTopNAnalysis"
                  >
                    开始分析
                  </button>
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasAnalyzedResult"
                    @click="cancelResultAnalysis"
                  >
                    取消分析
                  </button>
                </div>
              </div>
            </template>
            <template v-else-if="selectedAnalysisType === 'total'">
              <div class="space-y-3">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[12px] font-medium text-[#2e2f32]">合计</span>
                </div>
                <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                  <div class="text-[11px] text-[#57585a] leading-5">
                    基于当前显示结果追加一行“合计”，对数值列求和，不改 SQL
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasRawResult || executing"
                    @click="runTotalAnalysis"
                  >
                    开始分析
                  </button>
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasAnalyzedResult"
                    @click="cancelResultAnalysis"
                  >
                    取消分析
                  </button>
                </div>
              </div>
            </template>
            <template v-else-if="selectedAnalysisType === 'percent'">
              <div class="space-y-3">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[12px] font-medium text-[#2e2f32]">百分比</span>
                </div>
                <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                  <div class="text-[11px] text-[#57585a] mb-1.5">指标</div>
                  <select v-model="percentMetricField" class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]">
                    <option v-for="field in resultAnalysisMetricOptions" :key="field" :value="field">{{ field }}</option>
                  </select>
                  <div class="text-[11px] text-[#57585a] mb-1.5">计算范围</div>
                  <div class="flex items-center gap-4 mb-2">
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input v-model="percentScope" type="radio" value="total" class="accent-[#165DFF]" />
                      整体占比
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input v-model="percentScope" type="radio" value="group" class="accent-[#165DFF]" />
                      组内占比
                    </label>
                  </div>
                  <template v-if="percentScope === 'group'">
                    <div class="text-[11px] text-[#57585a] mb-1.5">分组维度</div>
                    <select v-model="percentGroupField" class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none text-[#2e2f32]">
                      <option v-for="field in resultAnalysisDimensionOptions" :key="field" :value="field">{{ field }}</option>
                    </select>
                  </template>
                </div>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasRawResult || executing || !percentMetricField || (percentScope === 'group' && !percentGroupField)"
                    @click="runPercentAnalysis"
                  >
                    开始分析
                  </button>
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasAnalyzedResult"
                    @click="cancelResultAnalysis"
                  >
                    取消分析
                  </button>
                </div>
              </div>
            </template>
            <template v-else-if="selectedAnalysisType === 'compare'">
              <div class="space-y-3">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-[12px] font-medium text-[#2e2f32]">对比</span>
                </div>
                <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                  <div class="text-[11px] text-[#57585a] mb-1.5">指标</div>
                  <select v-model="compareMetricField" class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none mb-2 text-[#2e2f32]">
                    <option v-for="field in resultAnalysisMetricOptions" :key="field" :value="field">{{ field }}</option>
                  </select>
                  <div class="text-[11px] text-[#57585a] mb-1.5">对比基准</div>
                  <select v-model="compareBaselineMode" class="w-full px-2 py-1 border border-[#e3e5eb] rounded text-[12px] bg-white cursor-pointer outline-none text-[#2e2f32]">
                    <option value="first">首行</option>
                    <option value="max">最大值</option>
                    <option value="min">最小值</option>
                    <option value="avg">平均值</option>
                  </select>
                </div>
                <div class="text-[11px] leading-5 text-[#939495]">
                  基于当前显示结果追加基准值、差值、差异率列，不改 SQL
                </div>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasRawResult || executing || !compareMetricField"
                    @click="runCompareBaselineAnalysis"
                  >
                    开始分析
                  </button>
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasAnalyzedResult"
                    @click="cancelResultAnalysis"
                  >
                    取消分析
                  </button>
                </div>
              </div>
            </template>
            <template v-else-if="selectedAnalysisType === 'yoy'">
              <div class="space-y-3">
                <div class="p-2 rounded bg-[#f8f9fb] border border-[#e3e5eb]">
                  <div class="text-[11px] text-[#57585a] mb-1.5">对比方式</div>
                  <div class="flex items-center gap-4">
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input type="radio" value="mom" v-model="compareType" class="accent-[#165DFF]" />
                      环比
                    </label>
                    <label class="flex items-center gap-1.5 cursor-pointer text-[12px] text-[#57585a]">
                      <input type="radio" value="yoy" v-model="compareType" class="accent-[#165DFF]" />
                      同比
                    </label>
                  </div>
                </div>
                <div class="text-[11px] leading-5 text-[#939495]">
                  当前为最小可用版本：仅支持基于当前查询结果做同/环比分析。支持年/季度/月/日；按周时分析入口已禁用。
                </div>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#165DFF] bg-[#165DFF] text-white text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasRawResult || executing || isCompareAnalysisDisabled"
                    @click="runCompareAnalysis"
                    :title="isCompareAnalysisDisabled ? '同环比结果分析暂不支持按周' : ''"
                  >
                    开始分析
                  </button>
                  <button
                    type="button"
                    class="h-[28px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#21252C] transition-colors hover:bg-[#f0f1f3] disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="!hasAnalyzedResult"
                    @click="cancelCompareAnalysis"
                  >
                    取消分析
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>
      </template>
    </div>
    <!-- 折叠时的竖向标签 -->
    <div v-if="!props.hideChartPanel && isChartPanelCollapsed" class="flex flex-col items-center border-r border-[#e3e5eb] bg-white shrink-0 w-10 pt-3 gap-1">
      <div
        class="writing-vertical text-[12px] py-2.5 px-1 rounded cursor-pointer transition-all select-none text-[#165DFF] font-medium bg-[#E8F3FF]"
        @click="isChartPanelCollapsed = false; chartPanelTab = 'chart'"
      >图表展示</div>
      <div
        class="writing-vertical text-[12px] py-2.5 px-1 rounded cursor-pointer transition-all select-none text-[#57585a] hover:text-[#57585a] hover:bg-[#f0f1f3]"
        @click="isChartPanelCollapsed = false; chartPanelTab = 'analysis'"
      >结果分析</div>
    </div>

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
                  :ref="(el: any) => { if (atState.activeRow === 'dimension') atInputRef = el }"
                  :value="atState.activeRow === 'dimension' ? atState.inputText : ''"
                  class="flex-1 min-w-[60px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: any) => { atState.inputText = e.target.value; handleAtInputChange('dimension') }"
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
                  <span class="relative inline-flex items-center cursor-pointer shrink-0">
                    <IconCaretDown class="mr-1 text-[#3772ff]" />
                    <span class="text-[12px]">{{ aggFunctions.find(f => f.value === a.func)?.label }}</span>
                    <select :value="a.func" class="absolute inset-0 opacity-0 cursor-pointer" @click.stop @change="handleAggregationFuncChange(a, ($event.target as HTMLSelectElement).value)">
                      <option v-for="opt in aggFunctions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
                    </select>
                  </span><span class="text-[12px]">({{ getMetricDisplayName(a) }})</span>
                  <button class="ml-1 text-[#2e2f3250] hover:text-[#2e2f32] transition-colors" @click.stop="removeAggregation(index)">
                    <IconClose />
                  </button>
                </span>
              </template>
              <span v-else class="text-[12px] text-[#93949580]">拖拽字段进行聚合</span>
                <!-- @mention 输入 -->
                <span class="text-[#e3e5eb] mx-1">|</span>
                <input
                  :ref="(el: any) => { if (atState.activeRow === 'aggregation') atInputRef = el }"
                  :value="atState.activeRow === 'aggregation' ? atState.inputText : ''"
                  class="flex-1 min-w-[60px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: any) => { atState.inputText = e.target.value; handleAtInputChange('aggregation') }"
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
                  :ref="(el: any) => { if (atState.activeRow === 'filter') atInputRef = el }"
                  :value="atState.activeRow === 'filter' ? atState.inputText : ''"
                  class="flex-1 min-w-[60px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: any) => { atState.inputText = e.target.value; handleAtInputChange('filter') }"
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
                    :ref="(el: any) => { if (atState.activeRow === 'order') atInputRef = el }"
                    :value="atState.activeRow === 'order' ? atState.inputText : ''"
                    class="min-w-[120px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                    placeholder="可@选择字段"
                    @input="(e: any) => { atState.inputText = e.target.value; handleAtInputChange('order') }"
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
                  :ref="(el: any) => { if (atState.activeRow === 'order') atInputRef = el }"
                  :value="atState.activeRow === 'order' ? atState.inputText : ''"
                  class="flex-1 min-w-[120px] h-[24px] border-none outline-none bg-transparent text-[12px] text-[#21252C] placeholder:text-[#93949580]"
                  placeholder="可@选择字段"
                  @input="(e: any) => { atState.inputText = e.target.value; handleAtInputChange('order') }"
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

  <el-dialog
    v-model="showDimensionManager"
    width="760px"
    :close-on-click-modal="false"
    title="维度管理"
  >
    <div class="space-y-3">
      <div class="flex items-center justify-between">
        <div class="text-[12px] text-[#6b7280]">批量管理当前查询已选维度，保存后生效。</div>
        <button
          type="button"
          class="h-[30px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#2e2f32] hover:bg-[#f5f7fb] disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!dimensionDrafts.some(item => item.selected)"
          @click="removeSelectedDimensionDrafts"
        >
          删除选中
        </button>
      </div>
      <div class="border border-[#e3e5eb] rounded-lg overflow-hidden">
        <div class="grid grid-cols-[44px_58px_1fr_160px_130px] h-[38px] items-center bg-[#f7f8fa] text-[12px] font-medium text-[#4b5563] border-b border-[#e3e5eb]">
          <div class="text-center">选择</div>
          <div>顺序</div>
          <div>维度</div>
          <div>展示名称</div>
          <div>时间粒度</div>
        </div>
        <div v-if="dimensionDrafts.length === 0" class="py-8 text-center text-[13px] text-[#9ca3af]">暂无维度</div>
        <div
          v-for="(item, index) in dimensionDrafts"
          :key="item.id"
          class="grid grid-cols-[44px_58px_1fr_160px_130px] min-h-[44px] items-center text-[13px] text-[#2e2f32] border-b border-[#eef0f4] last:border-b-0"
        >
          <div class="text-center">
            <input v-model="item.selected" type="checkbox" class="accent-[#165DFF]" />
          </div>
          <div class="flex items-center gap-1">
            <button type="button" class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30" :disabled="index === 0" @click="moveDimensionDraft(index, -1)">
              <i class="fa-solid fa-arrow-up text-[11px]"></i>
            </button>
            <button type="button" class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30" :disabled="index === dimensionDrafts.length - 1" @click="moveDimensionDraft(index, 1)">
              <i class="fa-solid fa-arrow-down text-[11px]"></i>
            </button>
          </div>
          <div class="pr-3 overflow-hidden">
            <div class="truncate" :title="getDimensionDisplayName(item)">{{ getDimensionDisplayName(item) }}</div>
            <div class="text-[11px] text-[#9ca3af] truncate" :title="`${item.table}.${item.column}`">{{ item.table }}.{{ item.column }}</div>
          </div>
          <div class="pr-3">
            <input
              v-model="item.displayName"
              class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] outline-none focus:border-[#165DFF]"
              :placeholder="item.column"
            />
          </div>
          <div class="pr-3">
            <select
              v-if="isDimensionDateField(item)"
              v-model="item.transform"
              class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] bg-white outline-none focus:border-[#165DFF]"
            >
              <option v-for="opt in transformOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </select>
            <div v-else class="h-[30px] inline-flex items-center px-2 rounded border border-[#eef0f4] bg-[#f8f9fb] text-[12px] text-[#9ca3af]">原始值</div>
          </div>
        </div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="showDimensionManager = false">取消</el-button>
        <el-button type="primary" @click="saveDimensionManager">保存</el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="showAggregationManager"
    width="780px"
    :close-on-click-modal="false"
    title="指标管理"
  >
    <div class="space-y-3">
      <div class="flex items-center justify-between">
        <div class="text-[12px] text-[#6b7280]">批量管理当前查询已选指标，保存后生效。</div>
        <button
          type="button"
          class="h-[30px] px-3 rounded border border-[#e3e5eb] bg-white text-[12px] text-[#2e2f32] hover:bg-[#f5f7fb] disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!aggregationDrafts.some(item => item.selected)"
          @click="removeSelectedAggregationDrafts"
        >
          删除选中
        </button>
      </div>
      <div class="border border-[#e3e5eb] rounded-lg overflow-hidden">
        <div class="grid grid-cols-[44px_58px_1fr_160px_140px] h-[38px] items-center bg-[#f7f8fa] text-[12px] font-medium text-[#4b5563] border-b border-[#e3e5eb]">
          <div class="text-center">选择</div>
          <div>顺序</div>
          <div>指标</div>
          <div>展示名称</div>
          <div>聚合函数</div>
        </div>
        <div v-if="aggregationDrafts.length === 0" class="py-8 text-center text-[13px] text-[#9ca3af]">暂无指标</div>
        <div
          v-for="(item, index) in aggregationDrafts"
          :key="item.id"
          class="grid grid-cols-[44px_58px_1fr_160px_140px] min-h-[44px] items-center text-[13px] text-[#2e2f32] border-b border-[#eef0f4] last:border-b-0"
        >
          <div class="text-center">
            <input v-model="item.selected" type="checkbox" class="accent-[#165DFF]" />
          </div>
          <div class="flex items-center gap-1">
            <button type="button" class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30" :disabled="index === 0" @click="moveAggregationDraft(index, -1)">
              <i class="fa-solid fa-arrow-up text-[11px]"></i>
            </button>
            <button type="button" class="w-6 h-6 rounded hover:bg-[#eef3ff] disabled:opacity-30" :disabled="index === aggregationDrafts.length - 1" @click="moveAggregationDraft(index, 1)">
              <i class="fa-solid fa-arrow-down text-[11px]"></i>
            </button>
          </div>
          <div class="pr-3 overflow-hidden">
            <div class="truncate" :title="getMetricDisplayName(item)">{{ getMetricDisplayName(item) }}</div>
            <div class="text-[11px] text-[#9ca3af] truncate" :title="`${item.table}.${item.column}`">{{ item.table }}.{{ item.column }}</div>
          </div>
          <div class="pr-3">
            <input
              v-model="item.displayName"
              class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] outline-none focus:border-[#165DFF]"
              :placeholder="item.column"
            />
          </div>
          <div class="pr-3">
            <select v-model="item.func" class="w-full h-[30px] px-2 border border-[#e3e5eb] rounded text-[12px] bg-white outline-none focus:border-[#165DFF]">
              <option v-for="opt in aggFunctions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
            </select>
          </div>
        </div>
      </div>
    </div>
    <template #footer>
      <div class="flex justify-end gap-2">
        <el-button @click="showAggregationManager = false">取消</el-button>
        <el-button type="primary" @click="saveAggregationManager">保存</el-button>
      </div>
    </template>
  </el-dialog>

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
            <FieldTypeIcon :type="(item.fieldType as any) || 'date'" class="text-[#165DFF]" />
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
