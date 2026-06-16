<script setup lang="ts">
/**
 * 语义配置面板
 * 支持管理指标、维度、表关系的完整配置
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getSemanticConfigs,
  createSemanticConfig,
  updateSemanticConfig,
  deleteSemanticConfig,
  toggleSemanticConfig,
  autoPrefillSemanticFields,
  parseSemanticPrefillContextFile,
  getAccuracyEvaluationCases,
  saveAccuracyEvaluationCases,
  generateAccuracyEvaluationCases,
  draftAccuracyEvaluationCase,
  runAccuracyEvaluationStream,
  getAccuracyEvaluationRuns,
  getAccuracyEvaluationRunDetail,
  executeSql,
  learnSemanticConfigFromHistory,
  getDatasourceColumnDict,
  getDatasourceTableColumnDicts,
} from '../api'
import type { AccuracyEvaluationCaseItem, AccuracyEvaluationReport, AccuracyEvaluationRunHistoryItem, AccuracyEvaluationRunCaseHistoryItem, DatasourceColumnDictPayload, SemanticHistoryLearningResult, SemanticHistoryLearningSuggestion } from '../api'

type EvaluationCaseRunStatus = 'idle' | 'queued' | 'running' | 'passed' | 'failed' | 'pending' | 'stopped'
interface EvaluationResultDetail extends Record<string, unknown> {
  id?: string
  question?: string
  gold_sql?: string
  gold_dsl?: unknown
  expected_dsl?: unknown
  predicted_dsl?: unknown
  generated_sql?: string
  agent_tool_sql?: string
  error?: string
}
interface EvaluationCaseRunRow extends AccuracyEvaluationCaseItem {
  selected: boolean
  runStatus: EvaluationCaseRunStatus
  runError: string
  runResult: EvaluationResultDetail | null
}
import type {
  SemanticConfigItem,
  SemanticMetric,
  SemanticDimension,
  SemanticRelationship,
  TableInfo,
} from '../types'

interface Props {
  datasourceId: string
  datasourceName: string
  datasourceType?: string
  tables: TableInfo[]
  embedded?: boolean
  selectedConfigId?: string | null
  hideDatasetSelector?: boolean
}

const props = defineProps<Props>()
const emit = defineEmits<{
  close: []
  configChange: []
  'update:selectedConfigId': [configId: string | null]
}>()

// 配置列表
const configs = ref<SemanticConfigItem[]>([])
const loading = ref(true)
const expandedId = ref<string | null>(null)
const savingId = ref<string | null>(null)
const creatingConfig = ref(false)
const savedConfigSignatures = ref<Record<string, string>>({})

interface TableScopeEntry {
  table: string
  alias: string
}

const tableScopes = ref<Record<string, TableScopeEntry[]>>({})
const draggingTableName = ref('')
const tableSearchKeyword = ref('')
const tableLabelMode = ref<'name' | 'description'>('name')
const semanticWorkspaceRef = ref<HTMLElement | null>(null)
const sourceTablePanelWidth = ref(250)
const sourceTablePanelCollapsed = ref(false)
const resizingSourcePanel = ref(false)
let resizeStartX = 0
let resizeStartWidth = 0
const showEvaluateDrawer = ref(false)
const evaluateDrawerTab = ref<'cases' | 'run' | 'history'>('cases')
const evaluationRunning = ref(false)
const evaluationCasesLoading = ref(false)
const evaluationCasesSaving = ref(false)
const evaluationCases = ref<AccuracyEvaluationCaseItem[]>([])
const evaluationBusinessDomain = ref('')
const evaluationSceneReady = ref(false)
const evaluationCasesDirty = ref(false)
const showCaseEditorDialog = ref(false)
const draftingCase = ref(false)
const editingCaseIndex = ref(-1)
const editingCase = ref<AccuracyEvaluationCaseItem>({
  id: '',
  nl: '',
  gold_dsl: {},
  gold_sql: '',
  query_type: 'aggregation',
  difficulty: 'easy',
  source: 'human',
  business_domain: '',
})
const editingCaseDslText = ref('{}')
const editingCaseDslError = ref('')
const evaluationReport = ref<AccuracyEvaluationReport | null>(null)
const evaluationForm = ref({
  limit: 10,
  skip_execution: true,
  local_compile: true,
  include_traces: false,
  request_timeout: 180,
})
const EVAL_HUMAN_TARGET = 50
const EVAL_MODEL_TARGET = 150
const evaluationCaseKeyword = ref('')
const canOpenEvaluation = computed(() => Boolean(activeConfig.value && props.datasourceId))
const hasUnsavedSemanticConfig = computed(() => isActiveConfigDirty.value || isFieldRowsDirty.value)
const canRunEvaluation = computed(() => (
  evaluationSceneReady.value
  && evaluationCases.value.length > 0
  && !evaluationCasesDirty.value
  && !hasUnsavedSemanticConfig.value
))
const evaluationWorkflowStep = computed(() => {
  if (evaluationCases.value.length === 0) return 0
  if (evaluationCasesDirty.value || !evaluationSceneReady.value) return 1
  if (evaluationReport.value) return 3
  return 2
})
const evaluationStepMeta = computed(() => [
  { title: '构建测试集', desc: '按指标/维度生成标准答案草稿' },
  { title: '校对并保存', desc: '编辑用例并生成标准 SQL' },
  { title: '运行评测', desc: '接口链路调用并查看评测结果' },
])
const filteredEvaluationCases = computed(() => {
  const keyword = evaluationCaseKeyword.value.trim().toLowerCase()
  if (!keyword) return evaluationCases.value
  return evaluationCases.value.filter(item => {
    const haystack = [item.id, item.nl, item.source, item.business_domain].join(' ').toLowerCase()
    return haystack.includes(keyword)
  })
})
const evaluationGoldSqlReadyCount = computed(() => evaluationCases.value.filter(item => Boolean(item.gold_sql?.trim())).length)
const evaluationResultFilter = ref<'all' | 'passed' | 'failed'>('all')
const selectedEvaluationResultId = ref('')
const evaluationRunRows = ref<EvaluationCaseRunRow[]>([])
const evaluationRunProgress = ref({ current: 0, total: 0 })
const evaluationLiveSummary = ref<AccuracyEvaluationReport['summary'] | null>(null)
const evaluationHistoryLoading = ref(false)
const evaluationHistoryDetailLoading = ref(false)
const evaluationHistoryRuns = ref<AccuracyEvaluationRunHistoryItem[]>([])
const evaluationHistoryTotal = ref(0)
const evaluationHistoryPage = ref(1)
const evaluationHistoryPageSize = ref(10)
const selectedEvaluationHistoryRunId = ref('')
const evaluationHistoryCases = ref<AccuracyEvaluationRunCaseHistoryItem[]>([])
const selectedEvaluationHistoryCaseId = ref('')
let evaluationRunAbortController: AbortController | null = null
const evaluationCaseMap = computed(() => {
  const map = new Map<string, AccuracyEvaluationCaseItem>()
  for (const item of evaluationCases.value) map.set(item.id, item)
  return map
})
const evaluationResultRows = computed<EvaluationResultDetail[]>(() => {
  const rows = evaluationReport.value?.results || []
  return rows.map(row => {
    const id = String(row.id || '')
    const gold = evaluationCaseMap.value.get(id)
    return {
      ...row,
      gold_sql: gold?.gold_sql || '',
      gold_dsl: gold?.gold_dsl || row.expected_dsl,
    }
  })
})
const filteredEvaluationResultRows = computed(() => {
  return evaluationResultRows.value.filter(row => {
    if (evaluationResultFilter.value === 'passed') return isEvaluationResultPassed(row)
    if (evaluationResultFilter.value === 'failed') return !isEvaluationResultPassed(row)
    return true
  })
})
const selectedEvaluationResult = computed<EvaluationResultDetail | null>(() => {
  const runRow = evaluationRunRows.value.find(row => row.id === selectedEvaluationResultId.value)
  if (runRow?.runResult) {
    return {
      ...runRow.runResult,
      id: runRow.id,
      question: runRow.nl,
      gold_sql: runRow.gold_sql,
      gold_dsl: runRow.gold_dsl,
      expected_dsl: runRow.runResult.expected_dsl || runRow.gold_dsl,
    }
  }
  if (!selectedEvaluationResultId.value) return filteredEvaluationResultRows.value[0] || null
  return evaluationResultRows.value.find(row => String(row.id) === selectedEvaluationResultId.value) || null
})
const evaluationResultStats = computed(() => {
  const rows = evaluationResultRows.value
  const passed = rows.filter(row => isEvaluationResultPassed(row)).length
  return { total: rows.length, passed, failed: rows.length - passed }
})
const activeConfig = computed(() => {
  if (configs.value.length === 0) return null
  const targetId = props.selectedConfigId || expandedId.value
  if (!targetId) return configs.value[0]
  return configs.value.find(c => String(c.id) === String(targetId)) || null
})
const evaluationActiveSummary = computed(() => evaluationLiveSummary.value || evaluationReport.value?.summary || null)
const evaluationSummaryCards = computed(() => {
  const summary = evaluationActiveSummary.value
  if (!summary) return []
  const debug = summary.debug_metrics || {}
  const passed = evaluationRunRows.value.filter(row => row.selected && row.runStatus === 'passed').length
  const failed = evaluationRunRows.value.filter(row => row.selected && ['failed', 'pending', 'stopped'].includes(row.runStatus)).length
  return [
    { label: '总用例', value: summary.total, tone: 'default' },
    { label: '通过', value: passed || evaluationResultStats.value.passed, tone: 'success' },
    { label: '未通过', value: failed || evaluationResultStats.value.failed, tone: 'danger' },
    { label: '语义得分', value: formatEvaluationPercent(summary.semantic_accuracy), tone: 'primary' },
    { label: '结构匹配', value: formatEvaluationPercent(debug.dsl_exact_match_rate), tone: 'default' },
    { label: 'SQL匹配', value: formatEvaluationPercent(debug.sql_exact_match_rate), tone: 'default' },
  ]
})
const evaluationMetricTips = {
  semantic: '按指标、维度、筛选、排序、分页等语义槽位逐项比较，反映模型是否理解了问题。',
  structure: '生成的语义结构与标准结构是否完全一致；标准集未要求排序时，不因模型多生成排序扣分。',
  sql: '生成 SQL 与标准 SQL 标准化后是否完全一致，主要用于排查最终 SQL 是否偏离。',
}
const evaluationMetricTipByLabel: Record<string, string> = {
  语义得分: evaluationMetricTips.semantic,
  结构匹配: evaluationMetricTips.structure,
  SQL匹配: evaluationMetricTips.sql,
}
const evaluationSelectedRows = computed(() => evaluationRunRows.value.filter(row => row.selected))
const evaluationSelectedCount = computed(() => evaluationSelectedRows.value.length)
const evaluationAllSelected = computed({
  get: () => evaluationRunRows.value.length > 0 && evaluationRunRows.value.every(row => row.selected),
  set: (value: boolean) => {
    evaluationRunRows.value = evaluationRunRows.value.map(row => ({ ...row, selected: value }))
  },
})
const evaluationRunRowsFiltered = computed(() => {
  if (evaluationResultFilter.value === 'all') return evaluationRunRows.value
  return evaluationRunRows.value.filter(row => {
    if (row.runStatus === 'idle' || row.runStatus === 'queued' || row.runStatus === 'running') return evaluationResultFilter.value === 'failed'
    const passed = row.runStatus === 'passed'
    return evaluationResultFilter.value === 'passed' ? passed : !passed
  })
})
const evaluationCaseStats = computed(() => {
  const total = evaluationCases.value.length
  const human = evaluationCases.value.filter(item => item.source === 'human').length
  const llm = evaluationCases.value.filter(item => item.source === 'llm').length
  return { total, human, llm }
})
const selectedEvaluationHistoryRun = computed(() => evaluationHistoryRuns.value.find(item => item.run_id === selectedEvaluationHistoryRunId.value) || null)
const selectedEvaluationHistoryCase = computed(() => {
  if (!selectedEvaluationHistoryCaseId.value) return evaluationHistoryCases.value[0]?.result || null
  return evaluationHistoryCases.value.find(item => item.case_id === selectedEvaluationHistoryCaseId.value)?.result || null
})
const evaluationHistoryTesterName = (row: AccuracyEvaluationRunHistoryItem) => {
  return String(row.tester_name || row.create_by_name || row.update_by_name || '—')
}
const filteredSourceTables = computed(() => {
  const keyword = tableSearchKeyword.value.trim().toLowerCase()
  if (!keyword) return props.tables
  return props.tables.filter(table =>
    table.table_name.toLowerCase().includes(keyword) ||
    (table.description || '').toLowerCase().includes(keyword) ||
    (table.business_desc || '').toLowerCase().includes(keyword)
  )
})
const getTableDisplayLabel = (table: TableInfo) => {
  if (tableLabelMode.value === 'description') {
    return table.description || table.table_name
  }
  return table.table_name
}
const getTableHoverTitle = (table: TableInfo) => {
  const businessDesc = table.business_desc ? `业务说明：${table.business_desc}` : ''
  if (tableLabelMode.value === 'description') {
    return [table.table_name, businessDesc].filter(Boolean).join('\n')
  }
  return [table.description || table.table_name, businessDesc].filter(Boolean).join('\n')
}

const buildConfigSignature = (config: SemanticConfigItem) => {
  return JSON.stringify({
    name: config.name || '',
    description: config.description || '',
    ontology_rules: config.ontology_rules || '',
    enabled: !!config.enabled,
    metrics: config.metrics || [],
    dimensions: config.dimensions || [],
    relationships: config.relationships || [],
  })
}

const isActiveConfigDirty = computed(() => {
  if (!activeConfig.value) return false
  const saved = savedConfigSignatures.value[activeConfig.value.id]
  if (!saved) return false
  return buildConfigSignature(activeConfig.value) !== saved
})

const normalizeSqlAliasBase = (tableName: string) => {
  const normalized = String(tableName || '').trim().replace(/[^\w]/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '')
  const base = normalized || 'table'
  return /^[A-Za-z_]/.test(base) ? base : `t_${base}`
}

const isNumericPhysicalTableName = (tableName: string) => /^\d+$/.test(String(tableName || '').trim())

const toLetterSuffix = (index: number) => {
  let value = Math.max(1, index)
  let suffix = ''
  while (value > 0) {
    value -= 1
    suffix = String.fromCharCode(97 + (value % 26)) + suffix
    value = Math.floor(value / 26)
  }
  return suffix
}

const getScopeAlias = (entry: TableScopeEntry | string | undefined | null) => {
  if (!entry) return ''
  if (typeof entry === 'string') return entry
  return entry.alias || entry.table
}

const getScopePhysicalTable = (entry: TableScopeEntry | string | undefined | null) => {
  if (!entry) return ''
  if (typeof entry === 'string') return entry
  return entry.table
}

const normalizeScopeEntries = (entries: Array<TableScopeEntry | string>) => {
  const seenAliases = new Set<string>()
  const result: TableScopeEntry[] = []
  entries.forEach(item => {
    const table = getScopePhysicalTable(item).trim()
    const alias = (getScopeAlias(item) || table).trim()
    if (!table || !alias || seenAliases.has(alias)) return
    seenAliases.add(alias)
    result.push({ table, alias })
  })
  return result
}

const getFieldTableAlias = (item: { table: string; table_alias?: string }) => {
  return String(item.table_alias || item.table || '').trim()
}

const getRelationshipLeftAlias = (rel: SemanticRelationship) => String(rel.left_alias || rel.left_table || '').trim()
const getRelationshipRightAlias = (rel: SemanticRelationship) => String(rel.right_alias || rel.right_table || '').trim()

const deriveScopeFromConfig = (config: SemanticConfigItem) => {
  const entries: TableScopeEntry[] = []
  const addEntry = (table: string, alias?: string) => {
    const physical = String(table || '').trim()
    const effectiveAlias = String(alias || physical).trim()
    if (!physical || !effectiveAlias || entries.some(item => item.alias === effectiveAlias)) return
    entries.push({ table: physical, alias: effectiveAlias })
  }
  config.dimensions.forEach(item => addEntry(item.table, getFieldTableAlias(item)))
  config.metrics.forEach(item => addEntry(item.table, getFieldTableAlias(item)))
  config.relationships.forEach(item => {
    addEntry(item.left_table, getRelationshipLeftAlias(item))
    addEntry(item.right_table, getRelationshipRightAlias(item))
  })
  return entries
}

const canManageConfig = (config?: SemanticConfigItem | null) => {
  return Boolean(config)
}

const formatConfigOperator = (value?: string) => {
  const text = String(value || '').trim()
  return text || '未知'
}

const getConfigCreatorName = (config: SemanticConfigItem) => {
  return formatConfigOperator(config.create_by_name)
}

const getConfigUpdaterName = (config: SemanticConfigItem) => {
  return formatConfigOperator(config.update_by_name)
}

const buildConfigMaintainerTitle = (config: SemanticConfigItem) => {
  const creator = getConfigCreatorName(config)
  const updater = getConfigUpdaterName(config)
  const createTime = config.create_time || '-'
  const updateTime = config.update_time || '-'
  return `创建人：${creator}\n创建时间：${createTime}\n最近维护人：${updater}\n维护时间：${updateTime}`
}

const showDatasetSelectorInHeader = computed(() => !props.embedded)
const showDatasetSelectorInBody = computed(() => !props.hideDatasetSelector && !showDatasetSelectorInHeader.value)

const syncTableScopes = () => {
  const next: Record<string, TableScopeEntry[]> = {}
  configs.value.forEach(config => {
    const existed = normalizeScopeEntries((tableScopes.value[config.id] || []) as Array<TableScopeEntry | string>)
    next[config.id] = existed.length > 0 ? existed : deriveScopeFromConfig(config)
  })
  tableScopes.value = next
}

const applyDefaultModelConfigCollapse = (configId?: string | null) => {
  const id = configId || ''
  const scope = id ? (tableScopes.value[id] || []) : []
  modelConfigCollapsed.value = scope.length > 0
}

const getScopeTables = (configId: string) => {
  const entries = normalizeScopeEntries((tableScopes.value[configId] || []) as Array<TableScopeEntry | string>)
  if (entries.length === 0) return []
  return entries
    .map(entry => props.tables.find(t => t.table_name === entry.table))
    .filter(Boolean) as TableInfo[]
}

const getScopeEntries = (configId: string) => {
  return normalizeScopeEntries((tableScopes.value[configId] || []) as Array<TableScopeEntry | string>)
}

const getScopeEntryByAlias = (configId: string, alias?: string) => {
  const targetAlias = String(alias || '').trim()
  if (!targetAlias) return null
  return getScopeEntries(configId).find(entry => entry.alias === targetAlias) || null
}

const getScopeTableInfo = (entry: TableScopeEntry | string | undefined | null) => {
  const tableName = getScopePhysicalTable(entry)
  return props.tables.find(t => t.table_name === tableName) || null
}

const getScopeEntryLabel = (entry: TableScopeEntry | string | undefined | null) => {
  const tableName = getScopePhysicalTable(entry)
  const alias = getScopeAlias(entry)
  if (!tableName) return ''
  return alias && alias !== tableName ? `${alias}（${tableName}）` : tableName
}

const getScopeCountByPhysicalTable = (configId: string, tableName: string) => {
  return getScopeEntries(configId).filter(entry => entry.table === tableName).length
}

const isFieldRowInScope = (row: Pick<FieldConfigRow, 'table' | 'tableAlias'>, scopeEntries: TableScopeEntry[]) => {
  const alias = getFieldRowAlias(row)
  return scopeEntries.some(entry => entry.alias === alias && entry.table === row.table)
}

const generateScopeAlias = (tableName: string, entries: TableScopeEntry[]) => {
  const base = normalizeSqlAliasBase(tableName)
  const usedAliases = new Set(entries.map(item => item.alias))
  if (!usedAliases.has(base)) return base
  if (isNumericPhysicalTableName(tableName)) {
    let index = 1
    let candidate = `${base}_${toLetterSuffix(index)}`
    while (usedAliases.has(candidate)) {
      index += 1
      candidate = `${base}_${toLetterSuffix(index)}`
    }
    return candidate
  }
  let index = 2
  while (usedAliases.has(`${base}_${index}`)) {
    index += 1
  }
  return `${base}_${index}`
}

const toggleScopeEntry = (configId: string, entry: TableScopeEntry, checked: boolean) => {
  const current = getScopeEntries(configId)
  const nextScope = checked
    ? normalizeScopeEntries([...current, entry])
    : current.filter(item => item.alias !== entry.alias)
  tableScopes.value = { ...tableScopes.value, [configId]: nextScope }
  const config = configs.value.find(c => c.id === configId)
  if (!config) return
  const nextAliasSet = new Set(nextScope.map(item => item.alias))
  updateConfig(configId, {
    metrics: config.metrics.filter(item => nextAliasSet.has(getFieldTableAlias(item))),
    dimensions: config.dimensions.filter(item => nextAliasSet.has(getFieldTableAlias(item))),
    relationships: config.relationships.filter(item => nextAliasSet.has(getRelationshipLeftAlias(item)) && nextAliasSet.has(getRelationshipRightAlias(item)))
  })
  if (fieldRowsConfigId.value === configId) {
    fieldRows.value = fieldRows.value.filter(row => isFieldRowInScope(row, nextScope))
    void refreshFieldColumnDictVisibleMap()
  }
}

// 拖拽开始：记录当前拖拽的数据表
const handleTableDragStart = (tableName: string) => {
  draggingTableName.value = tableName
}

// 拖拽结束：清空临时状态
const handleTableDragEnd = () => {
  draggingTableName.value = ''
}

// 添加数据表到作用域
const addScopeTable = (configId: string, tableName: string) => {
  const entries = getScopeEntries(configId)
  const alias = generateScopeAlias(tableName, entries)
  toggleScopeEntry(configId, { table: tableName, alias }, true)
}

// 移除数据表并级联清理关联配置
const removeScopeTable = (configId: string, alias: string) => {
  const entry = getScopeEntryByAlias(configId, alias)
  if (!entry) return
  toggleScopeEntry(configId, entry, false)
}

const removeScopeEntry = (configId: string, entry?: TableScopeEntry | null) => {
  if (!entry?.alias) return
  removeScopeTable(configId, entry.alias)
}

// 画布接收拖拽
const handleScopeDrop = (configId: string) => {
  if (!draggingTableName.value) return
  addScopeTable(configId, draggingTableName.value)
  modelConfigCollapsed.value = false
  draggingTableName.value = ''
}


// 加载配置列表
const fetchConfigs = async () => {
  if (!props.datasourceId) return
  loading.value = true
  try {
    const res = await getSemanticConfigs(props.datasourceId)
    if (res.status === 'success') {
      configs.value = res.data?.configs || []
      const nextSignatures: Record<string, string> = {}
      configs.value.forEach(cfg => {
        nextSignatures[cfg.id] = buildConfigSignature(cfg)
      })
      savedConfigSignatures.value = nextSignatures
      syncTableScopes()
      const selectedConfig = props.selectedConfigId
        ? configs.value.find(cfg => String(cfg.id) === String(props.selectedConfigId)) || null
        : null
      const nextConfig = selectedConfig || (!props.selectedConfigId
        ? (configs.value.find(cfg => String(cfg.id) === String(expandedId.value)) || configs.value[0] || null)
        : null)
      if (nextConfig) {
        expandedId.value = nextConfig.id
        if (!props.selectedConfigId) {
          emit('update:selectedConfigId', nextConfig.id)
        }
      } else if (props.selectedConfigId) {
        expandedId.value = props.selectedConfigId
      } else {
        expandedId.value = null
      }
      applyDefaultModelConfigCollapse(expandedId.value)
    }
  } catch (err) {
    console.error('获取语义配置失败:', err)
    ElMessage.error('获取配置失败')
  } finally {
    loading.value = false
  }
}

// 创建新配置
const createConfig = async () => {
  if (creatingConfig.value) return
  let datasetName = ''
  try {
    const { value } = await ElMessageBox.prompt('请输入数据集名称', '新建数据集', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValue: '',
      inputPlaceholder: '请输入数据集名称',
      inputPattern: /\S+/,
      inputErrorMessage: '名称不能为空',
      customClass: 'chatdata-message-box',
      modalClass: 'chatdata-message-box-overlay',
    })
    datasetName = String(value || '').trim()
  } catch (err) {
    return
  }
  if (!datasetName) return
  creatingConfig.value = true
  try {
    const res = await createSemanticConfig(props.datasourceId, {
      name: datasetName,
      enabled: true,
      metrics: [],
      dimensions: [],
      relationships: [],
    })
    if (res.status === 'success' && res.data) {
      configs.value = [...configs.value, res.data]
      savedConfigSignatures.value = {
        ...savedConfigSignatures.value,
        [res.data.id]: buildConfigSignature(res.data),
      }
      tableScopes.value = { ...tableScopes.value, [res.data.id]: [] }
      expandedId.value = res.data.id
      emit('update:selectedConfigId', res.data.id)
      emit('configChange')
      ElMessage.success('创建成功')
    }
  } catch (err) {
    ElMessage.error('创建失败')
  } finally {
    creatingConfig.value = false
  }
}

// 保存配置
const saveConfig = async (config: SemanticConfigItem) => {
  savingId.value = config.id
  try {
    const res = await updateSemanticConfig(props.datasourceId, config.id, config)
    if (res.status === 'success' && res.data) {
      configs.value = configs.value.map(c => c.id === config.id ? res.data! : c)
      emit('configChange')
      savedConfigSignatures.value = {
        ...savedConfigSignatures.value,
        [config.id]: buildConfigSignature(res.data),
      }
      ElMessage.success('保存成功')
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    ElMessage.error('保存失败')
  } finally {
    savingId.value = null
  }
}

// 打开当前指标集评测抽屉并加载场景测试集
const openEvaluateDrawer = async () => {
  if (!activeConfig.value) return
  evaluationReport.value = null
  evaluationCasesDirty.value = false
  showEvaluateDrawer.value = true
  await loadEvaluationCases()
  evaluateDrawerTab.value = evaluationSceneReady.value ? 'run' : 'cases'
  if (hasUnsavedSemanticConfig.value) {
    ElMessage.warning('当前指标集有未保存修改，请保存后再运行评测')
  }
}

// 格式化评测百分比
const formatEvaluationPercent = (value: unknown) => {
  if (typeof value !== 'number') return '-'
  return `${(value * 100).toFixed(1)}%`
}

const formatEvaluationBooleanRate = (value: unknown) => {
  if (typeof value !== 'boolean') return '-'
  return value ? '100.0%' : '0.0%'
}

// 格式化评测表格单元格
const formatEvaluationCell = (value: unknown) => {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  return String(value)
}

const formatEvaluationTime = (value: unknown) => {
  if (!value) return '-'
  return String(value)
}

const buildDefaultEvaluationDsl = (): Record<string, unknown> => ({
  metrics: [],
  dimensions: [],
  filters: [],
  having: [],
  order_by: [],
  limit: null,
  offset: null,
})

const withDefaultPaginationFields = (value: Record<string, unknown> | null | undefined): Record<string, unknown> => {
  const payload = value || {}
  return {
    ...payload,
    limit: Object.prototype.hasOwnProperty.call(payload, 'limit') ? payload.limit : null,
    offset: Object.prototype.hasOwnProperty.call(payload, 'offset') ? payload.offset : null,
  }
}

const formatDslText = (value: Record<string, unknown>) => JSON.stringify(withDefaultPaginationFields(value), null, 2)

const validateEvaluationDsl = (dsl: unknown): string => {
  if (!dsl || typeof dsl !== 'object' || Array.isArray(dsl)) return '标准语义结构必须是 JSON 对象'
  const payload = dsl as Record<string, unknown>
  if (Array.isArray(payload.limit)) return 'limit 必须是返回条数整数；LIMIT 5,3 请写成 {"limit": 3, "offset": 5}'
  if (Array.isArray(payload.offset)) return 'offset 必须是跳过条数整数'
  if (payload.limit !== undefined && payload.limit !== null && (!Number.isInteger(payload.limit) || Number(payload.limit) <= 0)) {
    return 'limit 必须是正整数或 null'
  }
  if (payload.offset !== undefined && payload.offset !== null && (!Number.isInteger(payload.offset) || Number(payload.offset) < 0)) {
    return 'offset 必须是非负整数或 null'
  }
  if (payload.offset !== undefined && payload.offset !== null && (payload.limit === undefined || payload.limit === null)) {
    return 'offset 必须配合 limit 使用'
  }
  return ''
}

const normalizeEditorDsl = (dsl: unknown): Record<string, unknown> | null => {
  if (!dsl || typeof dsl !== 'object' || Array.isArray(dsl)) return null
  return withDefaultPaginationFields(dsl as Record<string, unknown>)
}

const normalizeEvaluationCase = (item: any, businessDomain: string): AccuracyEvaluationCaseItem => ({
  ...item,
  gold_dsl: withDefaultPaginationFields(item?.gold_dsl || {}),
  source: item.source === 'model_generated' ? 'llm' : item.source,
  business_domain: businessDomain,
})

const buildDefaultCase = (): AccuracyEvaluationCaseItem => ({
  id: `${activeConfig.value?.id || 'scene'}_${Date.now()}`,
  nl: '',
  gold_dsl: buildDefaultEvaluationDsl(),
  gold_sql: '',
  query_type: 'aggregation',
  difficulty: 'easy',
  source: 'human',
  business_domain: activeConfig.value?.name || 'scene',
})

const loadEvaluationCases = async () => {
  if (!activeConfig.value || !props.datasourceId) return
  evaluationCasesLoading.value = true
  try {
    const res = await getAccuracyEvaluationCases(props.datasourceId, activeConfig.value.id)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '加载测试集失败')
      return
    }
    const businessDomain = res.data.business_domain || activeConfig.value.name
    evaluationCases.value = (res.data.cases || []).map((c: any) => normalizeEvaluationCase(c, businessDomain))
    evaluationBusinessDomain.value = businessDomain
    evaluationSceneReady.value = evaluationCases.value.length > 0
    evaluationCasesDirty.value = false
    syncEvaluationRunList()
  } catch (err) {
    console.error('加载测试集失败:', err)
    ElMessage.error('加载测试集失败')
  } finally {
    evaluationCasesLoading.value = false
  }
}

const generateEvaluationCasesDraft = async () => {
  if (!activeConfig.value || !props.datasourceId) return

  if (evaluationCases.value && evaluationCases.value.length > 0) {
    try {
      await ElMessageBox.confirm(
        '当前指标集已有测试集，继续生成将覆盖已有测试集，是否继续？',
        '确认生成',
        {
          confirmButtonText: '继续',
          cancelButtonText: '取消',
          type: 'warning',
          customClass: 'chatdata-message-box',
          modalClass: 'chatdata-message-box-overlay',
        }
      )
    } catch {
      return
    }
  }

  evaluationCasesLoading.value = true
  try {
    const res = await generateAccuracyEvaluationCases(props.datasourceId, activeConfig.value.id)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '生成测试集失败')
      return
    }
    const businessDomain = res.data.business_domain || activeConfig.value.name
    evaluationCases.value = (res.data.cases || []).map((c: any) => normalizeEvaluationCase(c, businessDomain))
    evaluationBusinessDomain.value = businessDomain
    evaluationSceneReady.value = evaluationCases.value.length > 0
    evaluationCasesDirty.value = evaluationCases.value.length > 0
    syncEvaluationRunList()
    evaluateDrawerTab.value = 'cases'
    ElMessage.success('已根据当前指标集生成测试集草稿，请校对后保存')
  } catch (err) {
    console.error('生成测试集失败:', err)
    ElMessage.error('生成测试集失败')
  } finally {
    evaluationCasesLoading.value = false
  }
}

const saveEvaluationCases = async () => {
  if (!activeConfig.value || !props.datasourceId) return
  if (!evaluationCasesDirty.value) {
    ElMessage.info('测试集没有未保存修改')
    return
  }
  evaluationCasesSaving.value = true
  try {
    const res = await saveAccuracyEvaluationCases(props.datasourceId, activeConfig.value.id, {
      business_domain: evaluationBusinessDomain.value || activeConfig.value.name,
      cases: evaluationCases.value,
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '保存测试集失败')
      return
    }
    const businessDomain = res.data.business_domain || activeConfig.value.name
    evaluationCases.value = (res.data.cases || []).map((c: any) => normalizeEvaluationCase(c, businessDomain))
    evaluationBusinessDomain.value = businessDomain
    evaluationSceneReady.value = evaluationCases.value.length > 0
    evaluationCasesDirty.value = false
    syncEvaluationRunList()
    ElMessage.success('测试集已保存，可以开始评测')
    evaluateDrawerTab.value = 'run'
  } catch (err) {
    console.error('保存测试集失败:', err)
    const message = err instanceof Error && err.message && err.message !== 'error' ? err.message : '保存测试集失败'
    ElMessage.error(message)
  } finally {
    evaluationCasesSaving.value = false
  }
}

const openNewCaseEditor = () => {
  editingCaseIndex.value = -1
  editingCase.value = buildDefaultCase()
  editingCaseDslText.value = formatDslText(editingCase.value.gold_dsl)
  editingCaseDslError.value = ''
  showCaseEditorDialog.value = true
}

const draftEditingCaseByLlm = async () => {
  if (!activeConfig.value || !props.datasourceId) return
  if (!editingCase.value.nl.trim()) {
    ElMessage.warning('请先输入自然语言问题或 SQL')
    return
  }
  draftingCase.value = true
  try {
    const res = await draftAccuracyEvaluationCase(props.datasourceId, activeConfig.value.id, {
      hint: editingCase.value.nl,
      existing_questions: evaluationCases.value.map(item => item.nl).filter(Boolean),
    })
    if (res.status !== 'success' || !res.data?.case) {
      ElMessage.error(res.msg || '生成用例初稿失败')
      return
    }
    const nextCase = normalizeEvaluationCase(res.data.case, activeConfig.value.name)
    editingCase.value = nextCase
    editingCaseDslText.value = formatDslText(nextCase.gold_dsl)
    editingCaseDslError.value = ''
    if (!evaluationBusinessDomain.value) evaluationBusinessDomain.value = nextCase.business_domain
    ElMessage.success('已生成标准语义结构初稿')
  } catch (err) {
    console.error('生成标准语义结构初稿失败:', err)
    const message = err instanceof Error && err.message && err.message !== 'error' ? err.message : '生成标准语义结构初稿失败'
    ElMessage.error(message)
  } finally {
    draftingCase.value = false
  }
}

const openEditCaseEditor = (index: number) => {
  const currentCase = evaluationCases.value[index]
  if (!currentCase) return
  editingCaseIndex.value = index
  editingCase.value = {
    ...currentCase,
    gold_dsl: { ...(currentCase.gold_dsl || {}) },
    business_domain: activeConfig.value?.name || 'scene'
  }
  editingCaseDslText.value = formatDslText(editingCase.value.gold_dsl)
  editingCaseDslError.value = ''
  showCaseEditorDialog.value = true
}

const removeEvaluationCase = (index: number) => {
  if (!confirm('确定删除这个评测用例吗？')) return
  evaluationCases.value.splice(index, 1)
  evaluationCasesDirty.value = true
}

const saveCaseEditor = () => {
  try {
    editingCaseDslError.value = ''
    const parsedDsl = JSON.parse(editingCaseDslText.value || '{}')
    if (!editingCase.value.id.trim() || !editingCase.value.nl.trim()) {
      ElMessage.warning('用例 ID 和问题不能为空')
      return
    }
    const dslError = validateEvaluationDsl(parsedDsl)
    if (dslError) {
      editingCaseDslError.value = dslError
      return
    }
    const normalizedDsl = normalizeEditorDsl(parsedDsl)
    if (!normalizedDsl) {
      editingCaseDslError.value = '标准语义结构必须是 JSON 对象'
      return
    }
    const nextCase: AccuracyEvaluationCaseItem = {
      ...editingCase.value,
      gold_dsl: normalizedDsl,
      business_domain: editingCase.value.business_domain || evaluationBusinessDomain.value || activeConfig.value?.name || 'scene',
    }
    if (editingCaseIndex.value >= 0) evaluationCases.value.splice(editingCaseIndex.value, 1, nextCase)
    else evaluationCases.value.unshift(nextCase)
    if (!evaluationBusinessDomain.value) evaluationBusinessDomain.value = nextCase.business_domain
    evaluationCasesDirty.value = true
    showCaseEditorDialog.value = false
  } catch (_err) {
    const message = '标准语义结构不是合法 JSON；LIMIT 5,3 请写成 {"limit": 3, "offset": 5}'
    editingCaseDslError.value = message
    ElMessage.error(message)
  }
}

const isEvaluationResultPassed = (row: Record<string, unknown>) => {
  if (row.status === 'failed') return false
  const semantic = typeof row.semantic_accuracy === 'number' ? row.semantic_accuracy : 0
  return semantic >= 1 && row.dsl_exact_match !== false && row.compile_success !== false
}

// 同步评测运行表格与测试集
const syncEvaluationRunList = () => {
  const prev = new Map(evaluationRunRows.value.map(row => [row.id, row]))
  evaluationRunRows.value = evaluationCases.value.map(item => {
    const old = prev.get(item.id)
    return {
      ...item,
      selected: old?.selected ?? true,
      runStatus: old?.runStatus ?? 'idle',
      runError: old?.runError ?? '',
      runResult: old?.runResult ?? null,
    }
  })
}

const findEvaluationRunRow = (caseId: string) => evaluationRunRows.value.find(row => row.id === caseId)

const applyEvaluationCaseResult = (caseId: string, result: EvaluationResultDetail) => {
  const row = findEvaluationRunRow(caseId)
  if (!row) return
  row.runResult = result
  row.runError = String(result.error || result.reason || '')
  if (result.status === 'pending') {
    row.runStatus = 'pending'
    return
  }
  if (result.status === 'failed' || row.runError) {
    row.runStatus = 'failed'
    return
  }
  row.runStatus = isEvaluationResultPassed(result) ? 'passed' : 'failed'
}

const failPendingEvaluationRows = (caseIds: string[], message: string) => {
  const pendingStatuses: EvaluationCaseRunStatus[] = ['queued', 'running']
  for (const caseId of caseIds) {
    const row = findEvaluationRunRow(caseId)
    if (!row || !pendingStatuses.includes(row.runStatus)) continue
    row.runStatus = 'failed'
    row.runError = message
    row.runResult = {
      status: 'failed',
      error: message,
    }
  }
}

const stopPendingEvaluationRows = () => {
  for (const row of evaluationRunRows.value) {
    if (row.runStatus !== 'queued' && row.runStatus !== 'running') continue
    row.runStatus = 'stopped'
    row.runError = '评测已停止'
    row.runResult = null
  }
}

const stopEvaluationRun = () => {
  stopPendingEvaluationRows()
  evaluationRunAbortController?.abort()
  evaluationRunAbortController = null
  evaluationRunning.value = false
}

// 流式执行选中用例评测
const runActiveEvaluation = async () => {
  if (!activeConfig.value) return
  if (!evaluationSceneReady.value || evaluationCases.value.length === 0) {
    ElMessage.warning('请先在「测试集」页根据当前指标集生成草稿并保存')
    evaluateDrawerTab.value = 'cases'
    return
  }
  if (evaluationCasesDirty.value) {
    ElMessage.warning('测试集有未保存修改，请先保存后再评测')
    evaluateDrawerTab.value = 'cases'
    return
  }
  if (hasUnsavedSemanticConfig.value) {
    ElMessage.warning('当前指标集有未保存修改，请先保存后再评测')
    return
  }
  const selectedIds = evaluationSelectedRows.value.map(row => row.id)
  if (selectedIds.length === 0) {
    ElMessage.warning('请至少勾选一个用例')
    return
  }
  evaluateDrawerTab.value = 'run'
  evaluationRunning.value = true
  evaluationLiveSummary.value = null
  evaluationReport.value = null
  evaluationRunProgress.value = { current: 0, total: selectedIds.length }
  evaluationRunAbortController?.abort()
  const runController = new AbortController()
  evaluationRunAbortController = runController
  for (const row of evaluationRunRows.value) {
    if (!row.selected) {
      row.runStatus = 'idle'
      row.runResult = null
      row.runError = ''
      continue
    }
    row.runStatus = 'queued'
    row.runResult = null
    row.runError = ''
  }
  try {
    await runAccuracyEvaluationStream(
      {
        ...evaluationForm.value,
        datasource_id: props.datasourceId,
        semantic_config_id: activeConfig.value.id,
        agent_id: 'chat_data',
        query_mode: 'query',
        case_ids: selectedIds,
        limit: selectedIds.length,
      },
      {
        onStart: (payload) => {
          evaluationRunProgress.value = { current: 0, total: payload.total }
        },
        onCaseStart: (payload) => {
          const row = findEvaluationRunRow(payload.id)
          if (row) row.runStatus = 'running'
        },
        onCaseResult: (payload) => {
          applyEvaluationCaseResult(payload.id, payload.result)
          evaluationRunProgress.value = { current: payload.index, total: payload.total }
          if (!selectedEvaluationResultId.value) selectedEvaluationResultId.value = payload.id
        },
        onProgress: (payload) => {
          evaluationLiveSummary.value = payload.summary
          evaluationRunProgress.value = { current: payload.index, total: payload.total }
        },
        onDone: (payload) => {
          evaluationReport.value = payload.report
          evaluationLiveSummary.value = payload.report.summary
          const firstFailed = evaluationRunRows.value.find(row => row.selected && row.runStatus === 'failed')
          selectedEvaluationResultId.value = firstFailed?.id || selectedIds[0] || ''
          ElMessage.success('评测完成')
        },
        onError: (message) => {
          const errorMessage = message || '评测失败'
          failPendingEvaluationRows(selectedIds, errorMessage)
          ElMessage.error(errorMessage)
        },
      },
      runController.signal,
    )
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      ElMessage.info('已停止评测')
      return
    }
    console.error('运行评测失败:', err)
    const message = err instanceof Error && err.message && err.message !== 'error' ? err.message : '评测失败，请查看后端日志'
    failPendingEvaluationRows(selectedIds, message)
    ElMessage.error(message)
  } finally {
    if (evaluationRunAbortController === runController) {
      evaluationRunning.value = false
      evaluationRunAbortController = null
    }
  }
}

const evaluationRunStatusLabel = (status: EvaluationCaseRunStatus) => {
  if (status === 'running') return '运行中'
  if (status === 'queued') return '排队中'
  if (status === 'passed') return '通过'
  if (status === 'failed') return '未通过'
  if (status === 'pending') return '待评分'
  if (status === 'stopped') return '已停止'
  return '待运行'
}

const evaluationRunStatusClass = (status: EvaluationCaseRunStatus) => {
  if (status === 'running' || status === 'queued') return 'text-blue-600 bg-blue-50'
  if (status === 'passed') return 'text-emerald-700 bg-emerald-50'
  if (status === 'failed') return 'text-red-600 bg-red-50'
  if (status === 'pending') return 'text-amber-700 bg-amber-50'
  if (status === 'stopped') return 'text-gray-600 bg-gray-100'
  return 'text-gray-500 bg-gray-100'
}

const openEvaluationRunDetail = (row: EvaluationCaseRunRow) => {
  selectedEvaluationResultId.value = row.id
}

const loadEvaluationHistoryRuns = async () => {
  if (!activeConfig.value || !props.datasourceId) return
  evaluationHistoryLoading.value = true
  try {
    const res = await getAccuracyEvaluationRuns({
      datasource_id: props.datasourceId,
      semantic_config_id: activeConfig.value.id,
      page: evaluationHistoryPage.value,
      page_size: evaluationHistoryPageSize.value,
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '获取评测历史失败')
      return
    }
    evaluationHistoryRuns.value = res.data?.runs || []
    evaluationHistoryTotal.value = Number(res.data?.total || 0)
    const firstRun = evaluationHistoryRuns.value[0]
    if (!selectedEvaluationHistoryRunId.value && firstRun) {
      await loadEvaluationHistoryDetail(firstRun.run_id)
    }
  } catch (err) {
    console.error('获取评测历史失败:', err)
    ElMessage.error('获取评测历史失败')
  } finally {
    evaluationHistoryLoading.value = false
  }
}

const loadEvaluationHistoryDetail = async (runId: string) => {
  if (!runId) return
  selectedEvaluationHistoryRunId.value = runId
  evaluationHistoryDetailLoading.value = true
  try {
    const res = await getAccuracyEvaluationRunDetail(runId)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '获取评测历史详情失败')
      return
    }
    evaluationHistoryCases.value = res.data?.cases || []
    selectedEvaluationHistoryCaseId.value = evaluationHistoryCases.value[0]?.case_id || ''
  } catch (err) {
    console.error('获取评测历史详情失败:', err)
    ElMessage.error('获取评测历史详情失败')
  } finally {
    evaluationHistoryDetailLoading.value = false
  }
}

const handleEvaluationHistoryPageChange = async (page: number) => {
  evaluationHistoryPage.value = page
  selectedEvaluationHistoryRunId.value = ''
  selectedEvaluationHistoryCaseId.value = ''
  evaluationHistoryCases.value = []
  await loadEvaluationHistoryRuns()
}

const goEvaluateStep = (step: number) => {
  if (step <= 1) evaluateDrawerTab.value = 'cases'
  else evaluateDrawerTab.value = 'run'
}

watch(evaluateDrawerTab, (tab) => {
  if (tab === 'history') {
    selectedEvaluationHistoryRunId.value = ''
    selectedEvaluationHistoryCaseId.value = ''
    evaluationHistoryCases.value = []
    loadEvaluationHistoryRuns()
  }
})

const progressPercent = (current: number, target: number) => Math.min(100, Math.round((current / Math.max(target, 1)) * 100))

const selectEvaluationResult = (row: EvaluationResultDetail) => {
  selectedEvaluationResultId.value = String(row.id || '')
}

const formatJsonPreview = (value: unknown) => {
  if (value === null || value === undefined) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch (_err) {
    return String(value)
  }
}

const evaluationSlotScoreItems = (row: EvaluationResultDetail | null) => {
  const slots = row?.slot_scores
  if (!slots || typeof slots !== 'object') return []
  const labels: Record<string, string> = {
    metrics: '指标',
    dimensions: '维度',
    filters: '筛选条件',
    having: '指标条件',
    order_by: '排序',
    limit: '返回条数',
    offset: '跳过条数',
    visualization: '展示方式',
  }
  return Object.entries(slots as Record<string, number>).map(([key, score]) => ({ key: labels[key] || key, score }))
}

const evaluationToolTraceItems = (row: EvaluationResultDetail | null) => {
  const traces = row?.tool_traces
  return Array.isArray(traces) ? traces as Array<Record<string, unknown>> : []
}

const evaluationHistoryCaseResult = (row: AccuracyEvaluationRunCaseHistoryItem): EvaluationResultDetail => row.result || {}

// 删除配置
const handleDeleteConfig = async (configId: string, configName: string) => {
  if (!confirm(`确定要删除"${configName}"吗？`)) return
  try {
    await deleteSemanticConfig(props.datasourceId, configId)
    const newConfigs = configs.value.filter(c => c.id !== configId)
    configs.value = newConfigs
    const nextSignatures = { ...savedConfigSignatures.value }
    delete nextSignatures[configId]
    savedConfigSignatures.value = nextSignatures
    const currentSelectedId = props.selectedConfigId || expandedId.value
    const nextSelectedId = currentSelectedId === configId
      ? newConfigs[0]?.id || null
      : currentSelectedId
    expandedId.value = nextSelectedId
    emit('update:selectedConfigId', nextSelectedId)
    emit('configChange')
    ElMessage.success('删除成功')
  } catch (err) {
    ElMessage.error('删除失败')
  }
}

// 切换启用状态
const handleToggleEnabled = async (config: SemanticConfigItem) => {
  try {
    await toggleSemanticConfig(props.datasourceId, config.id, !config.enabled)
    config.enabled = !config.enabled
    emit('configChange')
    savedConfigSignatures.value = {
      ...savedConfigSignatures.value,
      [config.id]: buildConfigSignature(config),
    }
  } catch (err) {
    ElMessage.error('操作失败')
  }
}

// 更新配置（本地）
const updateConfig = (configId: string, updates: Partial<SemanticConfigItem>) => {
  configs.value = configs.value.map(c => c.id === configId ? { ...c, ...updates } : c)
}

// 切换展开
const toggleExpand = (configId: string) => {
  expandedId.value = configId
  emit('update:selectedConfigId', configId)
}

// 监听数据源变化
watch(() => props.datasourceId, fetchConfigs, { immediate: true })
watch(configs, (list) => {
  if (list.length === 0) {
    expandedId.value = null
    if (!props.selectedConfigId) {
      emit('update:selectedConfigId', null)
    }
    return
  }
  if (props.selectedConfigId) {
    expandedId.value = props.selectedConfigId
    return
  }
  const nextId = expandedId.value
  if (!nextId || !list.some(item => String(item.id) === String(nextId))) {
    const firstConfig = list[0]
    if (!firstConfig) return
    expandedId.value = firstConfig.id
    emit('update:selectedConfigId', firstConfig.id)
  }
}, { deep: true })
watch(() => props.selectedConfigId, (configId) => {
  if (configId !== expandedId.value) {
    expandedId.value = configId || null
  }
})
watch(expandedId, () => {
  applyDefaultModelConfigCollapse(expandedId.value)
})
watch(() => props.datasourceId, () => {
  tableSearchKeyword.value = ''
})

// ============ 编辑状态 ============
type EditingType = 'metric' | 'dimension' | 'relationship' | null
const editingType = ref<EditingType>(null)
const editingIndex = ref(-1)
const editingConfigId = ref<string | null>(null)

// 编辑中的数据
const editingMetric = ref<SemanticMetric | null>(null)
const editingDimension = ref<SemanticDimension | null>(null)
const editingRelationship = ref<SemanticRelationship | null>(null)

interface FieldConfigRow {
  key: string
  table: string
  tableAlias: string
  column: string
  sourceColumn?: string
  columnType: string
  isDimension: boolean
  isMetric: boolean
  displayName: string
  columnComment?: string
  description?: string
  metadataSupplement?: string
  metricAggFunc: string
  expression: string
  isCustom?: boolean
  isMissingPhysical?: boolean
}

interface AddFieldForm {
  tableAlias: string
  column: string
  fieldName: string
  displayName: string
  mode: 'dimension' | 'metric'
}

interface MetricExpandOption {
  aggFunc: string
  label: string
  fieldName: string
  displayName: string
  expression: string
  exists: boolean
  selected: boolean
}

const fieldRows = ref<FieldConfigRow[]>([])
const fieldRowsSnapshot = ref('')
const fieldRowsConfigId = ref<string | null>(null)
const showAddFieldDialog = ref(false)
const autoPrefillLoading = ref(false)
const showAutoPrefillModeDialog = ref(false)
const autoPrefillBusinessContext = ref('')
const autoPrefillContextFileInputRef = ref<HTMLInputElement | null>(null)
const parsingPrefillContextFile = ref(false)
const previewingTableName = ref('')
const showTablePreviewDialog = ref(false)
const tablePreviewTitle = ref('')
const tablePreviewColumns = ref<string[]>([])
const tablePreviewRows = ref<Record<string, unknown>[]>([])
const tablePreviewError = ref('')
const tablePreviewMode = ref<'table' | 'json'>('table')
const showColumnDictDialog = ref(false)
const loadingColumnDict = ref(false)
const columnDictDialogTitle = ref('')
const columnDictPayload = ref<DatasourceColumnDictPayload | null>(null)
const columnDictError = ref('')
const columnDictVisibleMap = ref<Record<string, boolean>>({})
let columnDictLoadSeq = 0
const tablePreviewJsonText = computed(() => {
  if (tablePreviewRows.value.length === 0) return '[]'
  return formatJsonPreview(tablePreviewRows.value.length === 1 ? tablePreviewRows.value[0] : tablePreviewRows.value)
})
const addFieldForm = ref<AddFieldForm>({ tableAlias: '', column: '', fieldName: '', displayName: '', mode: 'metric' })
const addFieldDefaultDisplayName = ref('')
const showExpressionEditorDialog = ref(false)
const editingExpressionRowKey = ref('')
const editingExpressionText = ref('')
const showMetricExpandDialog = ref(false)
const metricExpandSourceRowKey = ref('')
const metricExpandOptions = ref<MetricExpandOption[]>([])
const showOntologyRulesDialog = ref(false)
const editingBusinessFlowText = ref('')
const showHistoryLearningDialog = ref(false)
const historyLearningLoading = ref(false)
const historyLearningForm = ref({
  days: 7,
  limit: 50,
  sources: ['agent', 'builder', 'sql'] as Array<'agent' | 'builder' | 'sql'>,
  include_failed: true,
})
const historyLearningResult = ref<SemanticHistoryLearningResult | null>(null)

const AGG_FUNCTION_OPTIONS = [
  { value: 'COUNT', label: 'COUNT', desc: '计数' },
  { value: 'COUNT_DISTINCT', label: 'COUNT DISTINCT', desc: '去重计数' },
  { value: 'SUM', label: 'SUM', desc: '求和' },
  { value: 'AVG', label: 'AVG', desc: '平均值' },
  { value: 'MAX', label: 'MAX', desc: '最大值' },
  { value: 'MIN', label: 'MIN', desc: '最小值' },
]

const COMMON_FUNCTION_OPTIONS = [
  { value: 'RAW', label: '原始列', desc: '`col`' },
  { value: 'CASE_WHEN', label: 'CASE WHEN', desc: '条件分组' },
]

const DATE_FUNCTION_OPTIONS = [
  { value: 'DATE', label: 'DATE', desc: '按日' },
  { value: 'DATE_FORMAT_MONTH', label: 'DATE_FORMAT(月)', desc: '按月' },
  { value: 'YEAR', label: 'YEAR', desc: '按年' },
]
const HIVE_DATE_FUNCTION_OPTIONS = [
  { value: 'HIVE_DATE_FORMAT_DAY', label: '日期格式化(日)', desc: 'yyyy-MM-dd' },
  { value: 'HIVE_DATE_FORMAT_MONTH', label: '日期格式化(月)', desc: 'yyyy-MM' },
  { value: 'HIVE_DATE_FORMAT_YEAR', label: '日期格式化(年)', desc: 'yyyy' },
]
const visibleDateFunctionOptions = computed(() => {
  const sourceType = String(props.datasourceType || '').trim().toLowerCase()
  if (sourceType === 'mysql') {
    return [
      ...DATE_FUNCTION_OPTIONS,
      { value: 'STR_TO_DATE', label: 'STR_TO_DATE', desc: '字符串转日期' },
    ]
  }
  if (sourceType === 'hive') {
    return HIVE_DATE_FUNCTION_OPTIONS
  }
  return DATE_FUNCTION_OPTIONS
})

const STRING_FUNCTION_OPTIONS = [
  { value: 'LOWER', label: 'LOWER', desc: '转小写' },
  { value: 'UPPER', label: 'UPPER', desc: '转大写' },
  { value: 'TRIM', label: 'TRIM', desc: '去首尾空格' },
  { value: 'CONCAT', label: 'CONCAT', desc: '拼接字符串' },
  { value: 'SUBSTRING', label: 'SUBSTRING', desc: '截取子串' },
  { value: 'LEFT', label: 'LEFT', desc: '左截取' },
  { value: 'RIGHT', label: 'RIGHT', desc: '右截取' },
  { value: 'LENGTH', label: 'LENGTH', desc: '字符串长度' },
  { value: 'REPLACE', label: 'REPLACE', desc: '替换字符' },
]
const buildFieldRowsEditSignature = (rows: FieldConfigRow[]) => JSON.stringify(rows.map(row => ({
  key: row.key,
  table: row.table,
  tableAlias: row.tableAlias,
  column: row.column,
  sourceColumn: row.sourceColumn,
  isDimension: row.isDimension,
  isMetric: row.isMetric,
  displayName: row.displayName,
  description: row.description,
  metadataSupplement: row.metadataSupplement,
  metricAggFunc: row.metricAggFunc,
  expression: row.expression,
  isCustom: row.isCustom,
})))
const isFieldRowsDirty = computed(() => buildFieldRowsEditSignature(fieldRows.value) !== fieldRowsSnapshot.value)

const quoteIdentifier = (name: string) => {
  const safe = (name || '').replace(/`/g, '``')
  return `\`${safe}\``
}

const quoteTableIdentifier = (name: string) => {
  return (name || '')
    .split('.')
    .map(part => quoteIdentifier(part.trim()))
    .join('.')
}

const normalizeExpressionColumn = (value: string) => {
  const cleaned = (value || '').trim().replace(/`/g, '')
  if (!cleaned) return ''
  const parts = cleaned.split('.')
  return parts[parts.length - 1]
}

const isCountAllExpression = (expression: string) => {
  return ['COUNT(*)', 'COUNT(1)'].includes((expression || '').replace(/\s+/g, '').toUpperCase())
}

const hasPhysicalColumn = (tableName: string, columnName: string) => {
  const table = props.tables.find(t => t.table_name === tableName)
  return !!table?.columns?.some(col => col.name === columnName)
}

const expressionUsesColumn = (expression: string, column: string) => {
  const expr = (expression || '').trim().replace(/`/g, '')
  const col = (column || '').trim()
  if (!expr || !col) return false

  const escapedCol = col.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const directPattern = new RegExp(`(^|[^A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*\\.)?${escapedCol}([^A-Za-z0-9_]|$)`, 'i')
  if (directPattern.test(expr)) return true

  const aggMatch = expr.match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
  if (aggMatch?.[3]) {
    return normalizeExpressionColumn(aggMatch[3]) === col
  }

  return normalizeExpressionColumn(expr) === col
}

const isRawColumnExpression = (expression: string | undefined, column: string) => {
  const expr = (expression || '').trim().replace(/`/g, '').replace(/\s+/g, '')
  const col = (column || '').trim()
  if (!col) return false
  if (!expr) return true
  const escapedCol = col.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return new RegExp(`^(?:[A-Za-z_][A-Za-z0-9_]*\\.)?${escapedCol}$`, 'i').test(expr)
}

const validateFieldRowsBeforeApply = () => {
  const usedNames = new Set<string>()
  for (const row of fieldRows.value) {
    if (!row.isDimension && !row.isMetric) continue
    const sourceColumn = row.sourceColumn || row.column
    if (!row.table || !sourceColumn) {
      return `字段 ${row.key} 缺少表或字段信息`
    }
    if (row.isMissingPhysical || !hasPhysicalColumn(row.table, sourceColumn)) {
      return `字段 ${row.table}.${sourceColumn} 已不在当前表结构中，请先删除该字段配置`
    }
    const displayName = ((row.displayName || '').trim() || row.column).trim()
    if (!displayName) {
      return `字段 ${row.table}.${row.column} 缺少显示名称`
    }
    if (usedNames.has(displayName)) {
      return `字段显示名称重复：${displayName}`
    }
    usedNames.add(displayName)
    if (!expressionUsesColumn(row.expression, sourceColumn)) {
      return `字段 ${row.table}.${row.column} 的表达式必须基于该物理字段`
    }
  }
  return ''
}

// 取消编辑
const cancelEditing = () => {
  editingType.value = null
  editingIndex.value = -1
  editingConfigId.value = null
  editingMetric.value = null
  editingDimension.value = null
  editingRelationship.value = null
}

// ============ 指标操作 ============
const addMetric = (configId: string) => {
  const scope = getScopeEntries(configId)
  if (scope.length === 0) {
    ElMessage.warning('请先在上方选择数据表')
    return
  }
  editingType.value = 'metric'
  editingIndex.value = -1
  editingConfigId.value = configId
  editingMetric.value = {
    name: '',
    expression: 'COUNT(*)',
    table: scope[0]?.table || '',
    table_alias: scope[0]?.alias || '',
    data_type: 'bigint',
    filter: '',
    description: '',
  }
}

const editMetric = (configId: string, metric: SemanticMetric, index: number) => {
  editingType.value = 'metric'
  editingIndex.value = index
  editingConfigId.value = configId
  editingMetric.value = { ...metric }
}

const deleteMetric = (configId: string, index: number) => {
  if (!confirm('确定要删除这个指标吗？')) return
  const config = configs.value.find(c => c.id === configId)
  if (!config) return
  const newMetrics = [...config.metrics]
  newMetrics.splice(index, 1)
  updateConfig(configId, { metrics: newMetrics })
}

const saveMetric = () => {
  if (!editingMetric.value || !editingConfigId.value) return
  if (!editingMetric.value.name || !editingMetric.value.expression || !editingMetric.value.table) {
    ElMessage.warning('请填写必填字段')
    return
  }
  const config = configs.value.find(c => c.id === editingConfigId.value)
  if (!config) return
  const newMetrics = [...config.metrics]
  const normalizedMetric: SemanticMetric = {
    ...editingMetric.value,
    data_type: inferMetricDataType(editingMetric.value),
  }
  if (editingIndex.value === -1) {
    newMetrics.push(normalizedMetric)
  } else {
    newMetrics[editingIndex.value] = normalizedMetric
  }
  updateConfig(editingConfigId.value, { metrics: newMetrics })
  cancelEditing()
}

// ============ 维度操作 ============
const addDimension = (configId: string) => {
  const scope = getScopeEntries(configId)
  if (scope.length === 0) {
    ElMessage.warning('请先在上方选择数据表')
    return
  }
  editingType.value = 'dimension'
  editingIndex.value = -1
  editingConfigId.value = configId
  editingDimension.value = {
    name: '',
    column: '',
    expression: '',
    table: scope[0]?.table || '',
    table_alias: scope[0]?.alias || '',
    type: 'categorical',
    description: '',
  }
}

const editDimension = (configId: string, dim: SemanticDimension, index: number) => {
  editingType.value = 'dimension'
  editingIndex.value = index
  editingConfigId.value = configId
  editingDimension.value = { ...dim }
}

const deleteDimension = (configId: string, index: number) => {
  if (!confirm('确定要删除这个维度吗？')) return
  const config = configs.value.find(c => c.id === configId)
  if (!config) return
  const newDimensions = [...config.dimensions]
  newDimensions.splice(index, 1)
  updateConfig(configId, { dimensions: newDimensions })
}

const saveDimension = () => {
  if (!editingDimension.value || !editingConfigId.value) return
  if (!editingDimension.value.name || !editingDimension.value.column || !editingDimension.value.table) {
    ElMessage.warning('请填写必填字段')
    return
  }
  const config = configs.value.find(c => c.id === editingConfigId.value)
  if (!config) return
  if (!editingDimension.value.expression?.trim()) {
    editingDimension.value.expression = quoteIdentifier(editingDimension.value.column)
  }
  const newDimensions = [...config.dimensions]
  if (editingIndex.value === -1) {
    newDimensions.push(editingDimension.value)
  } else {
    newDimensions[editingIndex.value] = editingDimension.value
  }
  updateConfig(editingConfigId.value, { dimensions: newDimensions })
  cancelEditing()
}

// ============ 关系操作 ============
const getRelationshipConditions = (rel: SemanticRelationship) => {
  if (rel.join_conditions && rel.join_conditions.length > 0) {
    return rel.join_conditions
  }
  return [{ left_column: rel.left_column || '', right_column: rel.right_column || '' }]
}

const syncRelationshipColumnsFromConditions = (rel: SemanticRelationship) => {
  const first = (rel.join_conditions || [])[0]
  rel.left_column = first?.left_column || ''
  rel.right_column = first?.right_column || ''
}

const handleRelationshipLeftAliasChange = () => {
  if (!editingRelationship.value || !editingConfigId.value) return
  const entry = getScopeEntryByAlias(editingConfigId.value, editingRelationship.value.left_alias)
  editingRelationship.value.left_table = entry?.table || ''
  ;(editingRelationship.value.join_conditions || []).forEach(c => { c.left_column = '' })
}

const handleRelationshipRightAliasChange = () => {
  if (!editingRelationship.value || !editingConfigId.value) return
  const entry = getScopeEntryByAlias(editingConfigId.value, editingRelationship.value.right_alias)
  editingRelationship.value.right_table = entry?.table || ''
  ;(editingRelationship.value.join_conditions || []).forEach(c => { c.right_column = '' })
}

const addRelationship = (configId: string) => {
  const scope = getScopeEntries(configId)
  if (scope.length < 2) {
    ElMessage.warning('请先选择至少2张数据表再配置关系')
    return
  }
  editingType.value = 'relationship'
  editingIndex.value = -1
  editingConfigId.value = configId
  editingRelationship.value = {
    left_table: scope[0]?.table || '',
    left_alias: scope[0]?.alias || '',
    right_table: scope[1]?.table || '',
    right_alias: scope[1]?.alias || '',
    left_column: '',
    right_column: '',
    join_conditions: [{ left_column: '', right_column: '' }],
    relationship_type: 'one_to_many',
    join_type: 'left',
  }
}

const editRelationship = (configId: string, rel: SemanticRelationship, index: number) => {
  const leftAlias = getRelationshipLeftAlias(rel)
  const rightAlias = getRelationshipRightAlias(rel)
  const leftEntry = getScopeEntryByAlias(configId, leftAlias)
  const rightEntry = getScopeEntryByAlias(configId, rightAlias)
  editingType.value = 'relationship'
  editingIndex.value = index
  editingConfigId.value = configId
  editingRelationship.value = {
    ...rel,
    left_table: leftEntry?.table || rel.left_table,
    left_alias: leftEntry?.alias || leftAlias,
    right_table: rightEntry?.table || rel.right_table,
    right_alias: rightEntry?.alias || rightAlias,
    join_conditions: getRelationshipConditions(rel).map(c => ({ ...c }))
  }
}

const deleteRelationship = (configId: string, index: number) => {
  if (!confirm('确定要删除这个关系吗？')) return
  const config = configs.value.find(c => c.id === configId)
  if (!config) return
  const newRelationships = [...config.relationships]
  newRelationships.splice(index, 1)
  updateConfig(configId, { relationships: newRelationships })
}

const saveRelationship = () => {
  if (!editingRelationship.value || !editingConfigId.value) return
  const conditions = getRelationshipConditions(editingRelationship.value)
    .map(c => ({ left_column: (c.left_column || '').trim(), right_column: (c.right_column || '').trim() }))
    .filter(c => c.left_column || c.right_column)
  if (!editingRelationship.value.left_table || !editingRelationship.value.right_table || conditions.length === 0) {
    ElMessage.warning('请填写必填字段')
    return
  }
  if (conditions.some(c => !c.left_column || !c.right_column)) {
    ElMessage.warning('请完整填写所有关联条件')
    return
  }
  const leftAlias = getRelationshipLeftAlias(editingRelationship.value)
  const rightAlias = getRelationshipRightAlias(editingRelationship.value)
  const leftEntry = getScopeEntryByAlias(editingConfigId.value, leftAlias)
  const rightEntry = getScopeEntryByAlias(editingConfigId.value, rightAlias)
  if (!leftEntry || !rightEntry) {
    ElMessage.warning('关联表已不在模型配置中')
    return
  }
  editingRelationship.value.left_table = leftEntry.table
  editingRelationship.value.left_alias = leftEntry.alias
  editingRelationship.value.right_table = rightEntry.table
  editingRelationship.value.right_alias = rightEntry.alias
  if (leftAlias === rightAlias) {
    ElMessage.warning('关联两侧不能是同一个表角色')
    return
  }
  const config = configs.value.find(c => c.id === editingConfigId.value)
  if (!config) return
  const duplicatePairIndex = config.relationships.findIndex((rel, idx) => {
    if (idx === editingIndex.value) return false
    const relLeftAlias = getRelationshipLeftAlias(rel)
    const relRightAlias = getRelationshipRightAlias(rel)
    const sameDirection = relLeftAlias === leftAlias && relRightAlias === rightAlias
    const reverseDirection = relLeftAlias === rightAlias && relRightAlias === leftAlias
    return sameDirection || reverseDirection
  })
  if (duplicatePairIndex >= 0) {
    ElMessage.warning('该表关系已存在，请直接编辑现有关系')
    return
  }
  editingRelationship.value.join_conditions = conditions
  syncRelationshipColumnsFromConditions(editingRelationship.value)
  const newRelationships = [...config.relationships]
  if (editingIndex.value === -1) {
    newRelationships.push(editingRelationship.value)
  } else {
    newRelationships[editingIndex.value] = editingRelationship.value
  }
  updateConfig(editingConfigId.value, { relationships: newRelationships })
  cancelEditing()
}

const showRelationshipEditor = ref(false)

const openEditRelationshipEditor = (configId: string, rel: SemanticRelationship, index: number) => {
  editRelationship(configId, rel, index)
  showRelationshipEditor.value = true
}

const openAddRelationshipEditor = (configId: string) => {
  addRelationship(configId)
  if (editingType.value === 'relationship') {
    showRelationshipEditor.value = true
  }
}

const closeRelationshipEditor = () => {
  showRelationshipEditor.value = false
  if (editingType.value === 'relationship') {
    cancelEditing()
  }
}

const confirmRelationshipEditor = () => {
  saveRelationship()
  if (editingType.value !== 'relationship') {
    showRelationshipEditor.value = false
  }
}

const activeRelationshipCards = computed(() => {
  if (!activeConfig.value) return [] as Array<{ rel: SemanticRelationship; relIndex: number; nextEntry: TableScopeEntry }>
  const scope = getScopeEntries(activeConfig.value.id)
  const scopeMap = new Map(scope.map(entry => [entry.alias, entry]))
  const rootEntry = scope[0]
  if (!rootEntry) return []

  const visited = new Set<string>([rootEntry.alias])
  const result: Array<{ rel: SemanticRelationship; relIndex: number; nextEntry: TableScopeEntry }> = []
  const renderedRelIndexes = new Set<number>()
  let appended = true
  while (appended) {
    appended = false
    activeConfig.value.relationships.forEach((rel, relIndex) => {
      if (renderedRelIndexes.has(relIndex)) return
      const leftAlias = getRelationshipLeftAlias(rel)
      const rightAlias = getRelationshipRightAlias(rel)
      const nextAlias = visited.has(leftAlias) && !visited.has(rightAlias)
        ? rightAlias
        : visited.has(rightAlias) && !visited.has(leftAlias)
          ? leftAlias
          : ''
      const nextEntry = scopeMap.get(nextAlias)
      if (!nextEntry) return
      result.push({ rel, relIndex, nextEntry })
      renderedRelIndexes.add(relIndex)
      visited.add(nextAlias)
      appended = true
    })
  }
  activeConfig.value.relationships.forEach((rel, relIndex) => {
    if (renderedRelIndexes.has(relIndex)) return
    const leftAlias = getRelationshipLeftAlias(rel)
    const rightAlias = getRelationshipRightAlias(rel)
    const leftEntry = scopeMap.get(leftAlias)
    const rightEntry = scopeMap.get(rightAlias)
    const nextEntry = visited.has(rightAlias) && leftEntry ? leftEntry : rightEntry || leftEntry
    if (!nextEntry) return
    result.push({ rel, relIndex, nextEntry })
    visited.add(nextEntry.alias)
  })
  return result
})

const formatJoinTypeLabel = (joinType?: string) => {
  if (joinType === 'inner') return '内连接'
  if (joinType === 'right') return '右连接'
  return '左连接'
}

const activeUnlinkedTables = computed(() => {
  if (!activeConfig.value) return [] as TableScopeEntry[]
  const scope = getScopeEntries(activeConfig.value.id)
  if (scope.length === 0) return []
  const rootEntry = scope[0]
  if (!rootEntry) return []
  const scopeAliasSet = new Set(scope.map(entry => entry.alias))
  const adjacency = new Map<string, Set<string>>()
  scope.forEach(entry => adjacency.set(entry.alias, new Set()))
  activeConfig.value.relationships.forEach(rel => {
    const leftAlias = getRelationshipLeftAlias(rel)
    const rightAlias = getRelationshipRightAlias(rel)
    if (!scopeAliasSet.has(leftAlias) || !scopeAliasSet.has(rightAlias)) return
    adjacency.get(leftAlias)?.add(rightAlias)
    adjacency.get(rightAlias)?.add(leftAlias)
  })
  const visited = new Set<string>([rootEntry.alias])
  const queue = [rootEntry.alias]
  while (queue.length > 0) {
    const current = queue.shift() as string
    ;(adjacency.get(current) || new Set()).forEach(next => {
      if (visited.has(next)) return
      visited.add(next)
      queue.push(next)
    })
  }
  return scope.filter(entry => !visited.has(entry.alias))
})

const modelConfigVisibleRows = computed(() => {
  if (!activeConfig.value) return 1
  const scope = getScopeEntries(activeConfig.value.id)
  if (scope.length === 0) return 1
  const totalRows = 1 + activeRelationshipCards.value.length + activeUnlinkedTables.value.length
  return Math.max(1, Math.min(3, totalRows))
})

const modelConfigContentStyle = computed(() => {
  const rowHeight = 66
  const basePadding = 12
  return { maxHeight: `${modelConfigVisibleRows.value * rowHeight + basePadding}px` }
})
const modelConfigCollapsed = ref(false)
const semanticWorkspaceStyle = computed(() => ({
  gridTemplateColumns: sourceTablePanelCollapsed.value
    ? '44px minmax(0, 1fr)'
    : `${sourceTablePanelWidth.value}px 8px minmax(0, 1fr)`,
}))

const stopResizeSourcePanel = () => {
  if (!resizingSourcePanel.value) return
  resizingSourcePanel.value = false
  document.removeEventListener('mousemove', handleResizeSourcePanelMove)
  document.removeEventListener('mouseup', stopResizeSourcePanel)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
}

const handleResizeSourcePanelMove = (event: MouseEvent) => {
  if (!resizingSourcePanel.value) return
  const containerWidth = semanticWorkspaceRef.value?.clientWidth || 0
  const maxWidth = containerWidth > 0 ? Math.min(520, Math.max(180, containerWidth - 560)) : 520
  const nextWidth = resizeStartWidth + event.clientX - resizeStartX
  sourceTablePanelWidth.value = Math.min(maxWidth, Math.max(180, nextWidth))
}

const startResizeSourcePanel = (event: MouseEvent) => {
  if (sourceTablePanelCollapsed.value) return
  resizeStartX = event.clientX
  resizeStartWidth = sourceTablePanelWidth.value
  resizingSourcePanel.value = true
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  document.addEventListener('mousemove', handleResizeSourcePanelMove)
  document.addEventListener('mouseup', stopResizeSourcePanel)
}

const resetSourcePanelWidth = () => {
  sourceTablePanelWidth.value = 250
}

const toggleSourceTablePanelCollapsed = () => {
  if (sourceTablePanelCollapsed.value) {
    sourceTablePanelCollapsed.value = false
    return
  }
  stopResizeSourcePanel()
  sourceTablePanelCollapsed.value = true
}

onUnmounted(() => {
  stopResizeSourcePanel()
})

const openLinkTableEditor = (configId: string, targetAlias: string) => {
  const scope = getScopeEntries(configId)
  const mainTable = scope[0]
  const targetEntry = getScopeEntryByAlias(configId, targetAlias)
  if (!mainTable || !targetEntry || mainTable.alias === targetAlias) {
    openAddRelationshipEditor(configId)
    return
  }
  editingType.value = 'relationship'
  editingIndex.value = -1
  editingConfigId.value = configId
  editingRelationship.value = {
    left_table: mainTable.table,
    left_alias: mainTable.alias,
    right_table: targetEntry.table,
    right_alias: targetEntry.alias,
    left_column: '',
    right_column: '',
    join_conditions: [{ left_column: '', right_column: '' }],
    relationship_type: 'one_to_many',
    join_type: 'left',
  }
  showRelationshipEditor.value = true
}

const addJoinCondition = () => {
  if (!editingRelationship.value) return
  const next = getRelationshipConditions(editingRelationship.value).map(c => ({ ...c }))
  next.push({ left_column: '', right_column: '' })
  editingRelationship.value.join_conditions = next
  syncRelationshipColumnsFromConditions(editingRelationship.value)
}

const removeJoinCondition = (index: number) => {
  if (!editingRelationship.value) return
  const current = getRelationshipConditions(editingRelationship.value).map(c => ({ ...c }))
  if (current.length <= 1) return
  current.splice(index, 1)
  editingRelationship.value.join_conditions = current
  syncRelationshipColumnsFromConditions(editingRelationship.value)
}

// 获取表的字段列表
const getTableColumns = (tableName: string) => {
  const table = props.tables.find(t => t.table_name === tableName)
  return table?.columns || []
}

const getColumnsByScopeAlias = (configId: string, alias?: string) => {
  const entry = getScopeEntryByAlias(configId, alias)
  return entry ? getTableColumns(entry.table) : []
}

// 是否正在编辑某个配置的某个类型
const isEditing = (configId: string, type: EditingType) => {
  return editingConfigId.value === configId && editingType.value === type
}

const hasMultiTables = (configId: string) => {
  return getScopeEntries(configId).length > 1
}

const buildMetricExpression = (aggFunc: string, column: string) => {
  const quotedColumn = quoteIdentifier(column)
  if (aggFunc === 'COUNT_DISTINCT') return `COUNT(DISTINCT ${quotedColumn})`
  return `${aggFunc}(${quotedColumn})`
}

const AGG_DISPLAY_SUFFIX_MAP: Record<string, string> = {
  COUNT: '数量',
  SUM: '合计',
  AVG: '平均值',
  MAX: '最大值',
  MIN: '最小值',
}

const EXPANDABLE_AGG_FUNCS = ['SUM', 'AVG', 'MAX', 'MIN']

const parseAggFuncFromExpression = (expression: string) => {
  const match = (expression || '').trim().match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
  if (!match) return 'COUNT'
  if (match[1]?.toUpperCase() === 'COUNT' && match[2]) return 'COUNT_DISTINCT'
  return (match[1] || 'COUNT').toUpperCase()
}

const parseSimpleMetricExpression = (expression: string) => {
  const match = (expression || '').trim().match(/^(COUNT|SUM|AVG|MAX|MIN)\(\s*(?:`?([A-Za-z_][A-Za-z0-9_]*)`?|`?([A-Za-z_][A-Za-z0-9_]*)`?\.`?([A-Za-z_][A-Za-z0-9_]*)`?)\s*\)$/i)
  if (!match) return null
  return {
    aggFunc: (match[1] || 'COUNT').toUpperCase(),
    column: match[4] || match[2] || '',
  }
}

const extractExpressionColumns = (expression: string) => {
  const text = (expression || '').trim()
  if (!text) return []
  const keywords = new Set([
    'case', 'when', 'then', 'else', 'end', 'filter', 'where', 'distinct', 'count', 'sum', 'avg', 'max', 'min',
    'and', 'or', 'not', 'null', 'is', 'in', 'as', 'on', 'if', 'cast', 'char', 'date', 'date_format', 'str_to_date',
    'unix_timestamp', 'from_unixtime', 'year', 'month', 'day'
  ])
  const result = new Set<string>()
  const pattern = /`([^`]+)`|(?<!['"])\b([A-Za-z_][A-Za-z0-9_]*)\b(?!['"])/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    if (text.slice(match.index + match[0].length).trimStart().startsWith('.')) continue
    const token = (match[1] || match[2] || '').trim().split('.').pop() || ''
    if (!token || keywords.has(token.toLowerCase())) continue
    result.add(token)
  }
  return Array.from(result)
}

const getFirstPhysicalColumnFromExpression = (tableName: string, expression: string) => {
  const columns = extractExpressionColumns(expression)
  return columns.find(column => hasPhysicalColumn(tableName, column)) || ''
}

const isNumericColumnType = (columnType: string) => {
  return /int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/i.test(columnType || '')
}

const getMetricDataType = (columnType: string) => {
  const normalized = (columnType || '').trim()
  if (!normalized) return undefined
  return normalized
}

const getTableColumnType = (tableName: string, columnName: string) => {
  const normalizedColumn = (columnName || '').trim()
  if (!tableName || !normalizedColumn) return ''
  const table = props.tables.find(t => t.table_name === tableName)
  const column = table?.columns?.find(c => c.name === normalizedColumn)
  return column?.mysql_type || column?.hive_type || column?.type || ''
}

const getTableColumnComment = (tableName: string, columnName: string) => {
  const table = props.tables.find(t => t.table_name === tableName)
  const column = table?.columns?.find(c => c.name === columnName)
  return (column?.description || '').trim()
}

const getTableColumnDisplayName = (tableName: string, columnName: string) => {
  return getTableColumnComment(tableName, columnName) || (columnName || '').trim()
}

const getFieldRowAlias = (row: Pick<FieldConfigRow, 'table' | 'tableAlias'>) => row.tableAlias || row.table

const buildFieldRowKey = (tableAlias: string, column: string, suffix = '') => {
  const base = `${tableAlias}.${column}`
  return suffix ? `${base}${suffix}` : base
}

const buildTablePrefixedDisplayName = (tableName: string, displayName: string) => {
  const normalizedTable = (tableName || '').trim()
  let normalizedName = (displayName || '').trim()
  if (!normalizedTable || !normalizedName) return normalizedName
  const repeatedPrefix = new RegExp(`^(?:${normalizedTable.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[_.])+`)
  normalizedName = normalizedName.replace(repeatedPrefix, '')
  return `${normalizedTable}_${normalizedName}`
}

const buildAliasAwareDefaultDisplayName = (entry: TableScopeEntry, columnName: string, columnComment?: string) => {
  const baseName = (columnComment || columnName || '').trim()
  if (!baseName) return ''
  return entry.alias && entry.alias !== entry.table ? buildTablePrefixedDisplayName(entry.alias, baseName) : baseName
}

const buildFieldRowDefaultDisplayName = (row: Pick<FieldConfigRow, 'table' | 'tableAlias' | 'column' | 'sourceColumn' | 'columnComment'>) => {
  const sourceColumn = row.sourceColumn || row.column
  return buildAliasAwareDefaultDisplayName({ table: row.table, alias: getFieldRowAlias(row) }, sourceColumn, row.columnComment || getTableColumnComment(row.table, sourceColumn))
}

const isGeneratedFieldDisplayName = (row: FieldConfigRow) => {
  const current = (row.displayName || '').trim()
  if (!current) return true
  const sourceColumn = row.sourceColumn || row.column
  const columnComment = row.columnComment || getTableColumnComment(row.table, sourceColumn)
  const baseName = (columnComment || sourceColumn || '').trim()
  const generatedNames = new Set([
    sourceColumn,
    baseName,
    buildAliasAwareDefaultDisplayName({ table: row.table, alias: getFieldRowAlias(row) }, sourceColumn, columnComment),
    buildTablePrefixedDisplayName(row.table, baseName),
    buildTablePrefixedDisplayName(getFieldRowAlias(row), baseName),
  ].filter(Boolean))
  return generatedNames.has(current)
}

const inferMetricDataType = (metric: SemanticMetric) => {
  if (metric.data_type) return metric.data_type

  const expression = (metric.expression || '').trim()
  const fieldName = (metric.field_name || '').trim()
  const quotedField = fieldName ? quoteIdentifier(fieldName) : ''

  if (fieldName && expression === quotedField) {
    return getMetricDataType(getTableColumnType(metric.table, fieldName))
  }

  const simpleColumnMatch = expression.match(/^`?([A-Za-z_][A-Za-z0-9_]*)`?$/)
  if (simpleColumnMatch?.[1]) {
    return getMetricDataType(getTableColumnType(metric.table, simpleColumnMatch[1]))
  }

  const aggregateMatch = expression.match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?\)$/i)
  if (aggregateMatch?.[3]) {
    const sourceType = getTableColumnType(metric.table, aggregateMatch[3])
    if (/^COUNT$/i.test(aggregateMatch[1] || '')) return 'bigint'
    return getMetricDataType(sourceType)
  }

  return undefined
}

const normalizeFieldName = (value: string) => (value || '').trim()

const getUniqueCustomFieldName = (tableAlias: string, baseColumn: string) => {
  const base = normalizeFieldName(baseColumn) || 'field'
  const used = new Set(
    fieldRows.value
      .filter(row => getFieldRowAlias(row) === tableAlias)
      .map(row => normalizeFieldName(row.column))
      .filter(Boolean)
  )
  if (!used.has(base)) return base
  let index = 1
  let candidate = `${base}_${index}`
  while (used.has(candidate)) {
    index += 1
    candidate = `${base}_${index}`
  }
  return candidate
}

const addFieldTables = computed(() => {
  const configId = activeConfig.value?.id
  if (!configId) return []
  return getScopeEntries(configId)
})

const addFieldLabelMode = ref<'name' | 'description'>('name')
const addFieldTableKeyword = ref('')
const addFieldColumnKeyword = ref('')

const addFieldColumns = computed(() => {
  const entry = addFieldTables.value.find(t => t.alias === addFieldForm.value.tableAlias)
  const table = entry ? getScopeTableInfo(entry) : null
  return table?.columns || []
})

const filteredAddFieldTables = computed(() => {
  const keyword = addFieldTableKeyword.value.trim().toLowerCase()
  if (!keyword) return addFieldTables.value
  return addFieldTables.value.filter(entry => {
    const table = getScopeTableInfo(entry)
    return entry.alias.toLowerCase().includes(keyword) ||
      entry.table.toLowerCase().includes(keyword) ||
      (table?.description || '').toLowerCase().includes(keyword)
  })
})

const filteredAddFieldColumns = computed(() => {
  const keyword = addFieldColumnKeyword.value.trim().toLowerCase()
  if (!keyword) return addFieldColumns.value
  return addFieldColumns.value.filter(c =>
    c.name.toLowerCase().includes(keyword) ||
    (c.description || '').toLowerCase().includes(keyword)
  )
})

const getAddFieldTableLabel = (entry: TableScopeEntry) => {
  const table = getScopeTableInfo(entry)
  const base = addFieldLabelMode.value === 'description' ? (table?.description || entry.table) : entry.alias
  return entry.alias === entry.table ? base : `${base}（${entry.table}）`
}

const getAddFieldColumnLabel = (col: { name: string; description?: string }) => {
  if (addFieldLabelMode.value === 'description') return col.description || col.name
  return col.name
}

const handleAddFieldTableFilter = (keyword: string) => {
  addFieldTableKeyword.value = keyword
}

const handleAddFieldColumnFilter = (keyword: string) => {
  addFieldColumnKeyword.value = keyword
}

const openAddFieldDialog = () => {
  if (addFieldTables.value.length === 0) {
    ElMessage.warning('请先在左侧选择数据表')
    return
  }
  const firstEntry = addFieldTables.value[0]
  if (!firstEntry) return
  addFieldTableKeyword.value = ''
  addFieldColumnKeyword.value = ''
  addFieldForm.value.tableAlias = firstEntry.alias
  addFieldForm.value.column = addFieldColumns.value[0]?.name || ''
  addFieldForm.value.fieldName = getUniqueCustomFieldName(addFieldForm.value.tableAlias, addFieldForm.value.column)
  const firstCol = addFieldColumns.value[0]
  addFieldDefaultDisplayName.value = firstCol ? buildAliasAwareDefaultDisplayName(firstEntry, firstCol.name, firstCol.description) : ''
  addFieldForm.value.displayName = addFieldDefaultDisplayName.value
  addFieldForm.value.mode = 'metric'
  showAddFieldDialog.value = true
}

const handleAddFieldTableChange = () => {
  const entry = addFieldTables.value.find(item => item.alias === addFieldForm.value.tableAlias)
  addFieldColumnKeyword.value = ''
  addFieldForm.value.column = addFieldColumns.value[0]?.name || ''
  addFieldForm.value.fieldName = getUniqueCustomFieldName(addFieldForm.value.tableAlias, addFieldForm.value.column)
  const firstCol = addFieldColumns.value[0]
  addFieldDefaultDisplayName.value = entry && firstCol ? buildAliasAwareDefaultDisplayName(entry, firstCol.name, firstCol.description) : ''
  addFieldForm.value.displayName = addFieldDefaultDisplayName.value
}

const handleAddFieldColumnChange = () => {
  const entry = addFieldTables.value.find(item => item.alias === addFieldForm.value.tableAlias)
  const col = addFieldColumns.value.find(c => c.name === addFieldForm.value.column)
  if (!entry || !col) return
  const currentDisplayName = addFieldForm.value.displayName.trim()
  const nextDefaultDisplayName = buildAliasAwareDefaultDisplayName(entry, col.name, col.description)
  addFieldForm.value.fieldName = getUniqueCustomFieldName(addFieldForm.value.tableAlias, col.name)
  // 仅当为空或仍是系统默认名时，跟随字段自动同步；人工改过的显示名不覆盖。
  if (!currentDisplayName || currentDisplayName === addFieldDefaultDisplayName.value) {
    addFieldForm.value.displayName = nextDefaultDisplayName
  }
  addFieldDefaultDisplayName.value = nextDefaultDisplayName
}

const confirmAddField = () => {
  const entry = addFieldTables.value.find(item => item.alias === addFieldForm.value.tableAlias)
  const columnName = addFieldForm.value.column
  const fieldName = normalizeFieldName(addFieldForm.value.fieldName)
  if (!entry || !columnName) {
    ElMessage.warning('请选择表和字段')
    return
  }
  const tableName = entry.table
  const tableAlias = entry.alias
  if (!fieldName) {
    ElMessage.warning('请填写字段名')
    return
  }
  const table = getScopeTableInfo(entry)
  const columnInfo = table?.columns?.find(c => c.name === columnName)
  const columnType = columnInfo?.mysql_type || columnInfo?.type || ''
  const isMetric = addFieldForm.value.mode === 'metric'
  const metricAggFunc = isMetric ? (isNumericColumnType(columnType) ? 'SUM' : 'COUNT') : 'COUNT'
  const expression = isMetric ? buildMetricExpression(metricAggFunc, columnName) : quoteIdentifier(columnName)
  const columnComment = (columnInfo?.description || '').trim()
  const displayName = (addFieldForm.value.displayName || '').trim() || buildAliasAwareDefaultDisplayName(entry, columnName, columnComment)
  if (!displayName) {
    ElMessage.warning('请填写显示名称')
    return
  }
  const duplicatedField = fieldRows.value.some(row => getFieldRowAlias(row) === tableAlias && normalizeFieldName(row.column) === fieldName)
  if (duplicatedField) {
    ElMessage.warning(`字段名重复：${tableName}.${fieldName}`)
    return
  }
  const duplicated = fieldRows.value.some(row => ((row.displayName || '').trim() || row.column) === displayName)
  if (duplicated) {
    ElMessage.warning(`显示名称重复：${displayName}`)
    return
  }

  fieldRows.value.push({
    key: buildFieldRowKey(tableAlias, fieldName, `#extra_${Date.now()}`),
    table: tableName,
    tableAlias,
    column: fieldName,
    sourceColumn: columnName,
    columnType,
    isDimension: !isMetric,
    isMetric,
    displayName,
    columnComment,
    description: '',
    metadataSupplement: '',
    metricAggFunc,
    expression,
    isCustom: true,
  })
  void refreshFieldColumnDictVisibleMap()
  showAddFieldDialog.value = false
  addFieldForm.value.fieldName = ''
  addFieldForm.value.displayName = ''
  addFieldDefaultDisplayName.value = ''
}

const canExpandMetricRow = (row: FieldConfigRow) => {
  if (row.isMissingPhysical || !row.isMetric || !isNumericColumnType(row.columnType)) return false
  const parsed = parseSimpleMetricExpression(row.expression)
  if (!parsed || !EXPANDABLE_AGG_FUNCS.includes(parsed.aggFunc)) return false
  return normalizeExpressionColumn(parsed.column) === normalizeExpressionColumn(row.sourceColumn || row.column)
}

const metricAggExists = (tableAlias: string, sourceColumn: string, aggFunc: string) => {
  return fieldRows.value.some(row => {
    if (!row.isMetric || getFieldRowAlias(row) !== tableAlias) return false
    const parsed = parseSimpleMetricExpression(row.expression)
    if (!parsed) return false
    return parsed.aggFunc === aggFunc && normalizeExpressionColumn(parsed.column) === normalizeExpressionColumn(sourceColumn)
  })
}

const buildMetricExpandDisplayName = (baseName: string, aggFunc: string) => {
  const normalizedBase = (baseName || '').trim()
  const suffix = AGG_DISPLAY_SUFFIX_MAP[aggFunc] || aggFunc
  if (!normalizedBase) return suffix
  if (normalizedBase.endsWith(suffix)) return normalizedBase
  return `${normalizedBase}${suffix}`
}

const openMetricExpandDialog = (row: FieldConfigRow) => {
  if (!canExpandMetricRow(row)) {
    ElMessage.warning('仅支持数字字段的 SUM/AVG/MAX/MIN 简单指标扩展')
    return
  }
  const parsed = parseSimpleMetricExpression(row.expression)
  const sourceColumn = parsed?.column || row.sourceColumn || row.column
  const baseDisplayName = (row.displayName || getTableColumnDisplayName(row.table, sourceColumn) || sourceColumn).trim()
  const rowAlias = getFieldRowAlias(row)
  metricExpandOptions.value = EXPANDABLE_AGG_FUNCS
    .filter(aggFunc => aggFunc !== parsed?.aggFunc)
    .map(aggFunc => {
      const exists = metricAggExists(rowAlias, sourceColumn, aggFunc)
      return {
        aggFunc,
        label: `${aggFunc}(${sourceColumn})`,
        fieldName: getUniqueCustomFieldName(rowAlias, `${sourceColumn}_${aggFunc.toLowerCase()}`),
        displayName: buildMetricExpandDisplayName(baseDisplayName, aggFunc),
        expression: buildMetricExpression(aggFunc, sourceColumn),
        exists,
        selected: !exists,
      }
    })
  metricExpandSourceRowKey.value = row.key
  showMetricExpandDialog.value = true
}

const confirmMetricExpand = () => {
  const sourceRow = fieldRows.value.find(row => row.key === metricExpandSourceRowKey.value)
  if (!sourceRow) {
    showMetricExpandDialog.value = false
    return
  }
  const selectedOptions = metricExpandOptions.value.filter(item => item.selected && !item.exists)
  if (selectedOptions.length === 0) {
    ElMessage.warning('请选择需要扩展的指标')
    return
  }
  for (const option of selectedOptions) {
    option.fieldName = normalizeFieldName(option.fieldName)
    option.displayName = (option.displayName || '').trim()
    if (!option.fieldName) {
      ElMessage.warning('请填写字段名')
      return
    }
    if (!option.displayName) {
      ElMessage.warning('请填写显示名称')
      return
    }
  }
  const selectedFieldNames = new Set<string>()
  const sourceAlias = getFieldRowAlias(sourceRow)
  for (const option of selectedOptions) {
    const fieldKey = `${sourceAlias}.${option.fieldName}`
    if (selectedFieldNames.has(fieldKey)) {
      ElMessage.warning(`字段名重复：${fieldKey}`)
      return
    }
    selectedFieldNames.add(fieldKey)
    if (fieldRows.value.some(row => getFieldRowAlias(row) === sourceAlias && normalizeFieldName(row.column) === option.fieldName)) {
      ElMessage.warning(`字段名重复：${fieldKey}`)
      return
    }
  }
  const selectedDisplayNames = new Set<string>()
  for (const option of selectedOptions) {
    if (selectedDisplayNames.has(option.displayName)) {
      ElMessage.warning(`显示名称重复：${option.displayName}`)
      return
    }
    selectedDisplayNames.add(option.displayName)
  }
  const duplicatedName = selectedOptions.find(option =>
    fieldRows.value.some(row => ((row.displayName || '').trim() || row.column) === option.displayName)
  )
  if (duplicatedName) {
    ElMessage.warning(`显示名称重复：${duplicatedName.displayName}`)
    return
  }
  const sourceColumn = sourceRow.sourceColumn || sourceRow.column
  selectedOptions.forEach(option => {
    fieldRows.value.push({
      key: buildFieldRowKey(sourceAlias, option.fieldName, `#expand_${Date.now()}_${option.aggFunc}`),
      table: sourceRow.table,
      tableAlias: sourceAlias,
      column: option.fieldName,
      sourceColumn,
      columnType: sourceRow.columnType,
      isDimension: false,
      isMetric: true,
      displayName: option.displayName,
      columnComment: sourceRow.columnComment || getTableColumnComment(sourceRow.table, sourceColumn),
      description: '',
      metadataSupplement: sourceRow.metadataSupplement || '',
      metricAggFunc: option.aggFunc,
      expression: option.expression,
      isCustom: true,
    })
  })
  void refreshFieldColumnDictVisibleMap()
  showMetricExpandDialog.value = false
  metricExpandSourceRowKey.value = ''
  metricExpandOptions.value = []
  ElMessage.success(`已扩展 ${selectedOptions.length} 个指标，保存后生效`)
}

const appendAutoPrefillBusinessContext = (text: string, filename?: string) => {
  const normalized = (text || '').trim()
  if (!normalized) return
  const prefix = filename ? `【${filename}】\n` : ''
  const current = autoPrefillBusinessContext.value.trim()
  autoPrefillBusinessContext.value = (current ? `${current}\n\n${prefix}${normalized}` : `${prefix}${normalized}`).slice(0, 20000)
}

const selectAutoPrefillContextFiles = () => {
  autoPrefillContextFileInputRef.value?.click()
}

const handleAutoPrefillContextFilesChange = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (files.length === 0) return
  const oversized = files.find(file => file.size > 10 * 1024 * 1024)
  if (oversized) {
    ElMessage.warning(`文件不能超过 10MB：${oversized.name}`)
    return
  }
  parsingPrefillContextFile.value = true
  try {
    let successCount = 0
    for (const file of files) {
      const res = await parseSemanticPrefillContextFile(file)
      if (res.status !== 'success') {
        ElMessage.warning(res.msg || `解析失败：${file.name}`)
        continue
      }
      appendAutoPrefillBusinessContext(res.data?.text || '', res.data?.filename || file.name)
      successCount += 1
    }
    if (successCount > 0) {
      ElMessage.success(`已上传并解析 ${successCount} 个文件`)
    }
  } catch (err) {
    ElMessage.error('解析文件失败')
  } finally {
    parsingPrefillContextFile.value = false
  }
}

const buildAutoPrefillMetricFieldName = (tableAlias: string, sourceColumn: string, aggFunc: string, fieldName?: string) => {
  const normalized = normalizeFieldName(fieldName || `${sourceColumn}_${aggFunc.toLowerCase()}`)
  return getUniqueCustomFieldName(tableAlias, normalized)
}

const buildAutoPrefillMetricDisplayName = (entry: TableScopeEntry, sourceColumn: string, aggFunc: string, name?: string) => {
  const used = new Set(fieldRows.value.map(row => ((row.displayName || '').trim() || row.column)).filter(Boolean))
  const columnComment = getTableColumnComment(entry.table, sourceColumn)
  const columnDisplayName = buildAliasAwareDefaultDisplayName(entry, sourceColumn, columnComment) || sourceColumn
  const baseName = (name || buildMetricExpandDisplayName(columnDisplayName, aggFunc)).trim()
  if (!used.has(baseName)) return baseName
  const suffixedName = buildMetricExpandDisplayName(baseName, aggFunc)
  if (!used.has(suffixedName)) return suffixedName
  let index = 1
  let candidate = `${suffixedName}_${index}`
  while (used.has(candidate)) {
    index += 1
    candidate = `${suffixedName}_${index}`
  }
  return candidate
}

const addAutoPrefillMetricRow = (item: { table: string; column: string; source_column?: string; expression?: string; name?: string; field_name?: string; agg?: string }, entry: TableScopeEntry) => {
  const tableName = (item.table || '').trim()
  const tableAlias = entry.alias
  const sourceColumn = normalizeExpressionColumn(item.source_column || item.column || '')
  if (!tableName || !tableAlias || !sourceColumn || !hasPhysicalColumn(tableName, sourceColumn)) return false
  const aggFunc = (item.agg || parseAggFuncFromExpression(item.expression || '') || 'SUM').toUpperCase()
  const expression = (item.expression || buildMetricExpression(aggFunc, sourceColumn)).trim()
  if (metricAggExists(tableAlias, sourceColumn, aggFunc)) return false
  const displayName = buildAutoPrefillMetricDisplayName(entry, sourceColumn, aggFunc, item.name)
  const columnType = getTableColumnType(tableName, sourceColumn)
  const columnComment = getTableColumnComment(tableName, sourceColumn)
  const fieldName = buildAutoPrefillMetricFieldName(tableAlias, sourceColumn, aggFunc, item.field_name)
  fieldRows.value.push({
    key: buildFieldRowKey(tableAlias, fieldName, `#prefill_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`),
    table: tableName,
    tableAlias,
    column: fieldName,
    sourceColumn,
    columnType,
    isDimension: false,
    isMetric: true,
    displayName,
    columnComment,
    description: '',
    metadataSupplement: '',
    metricAggFunc: aggFunc,
    expression,
    isCustom: true,
  })
  return true
}

const openAutoPrefillModeDialog = () => {
  if (!activeConfig.value) return
  const selectedTables = getScopeEntries(activeConfig.value.id)
  if (selectedTables.length === 0) {
    ElMessage.warning('请先在左侧选择数据表')
    return
  }
  autoPrefillBusinessContext.value = ''
  showAutoPrefillModeDialog.value = true
}

const handleAutoPrefillFields = async () => {
  if (!activeConfig.value) return
  const selectedTables = getScopeEntries(activeConfig.value.id)
  if (selectedTables.length === 0) {
    ElMessage.warning('请先在左侧选择数据表')
    return
  }
  showAutoPrefillModeDialog.value = false
  autoPrefillLoading.value = true
  try {
    const targetTables = selectedTables
      .map(entry => {
        const table = props.tables.find(item => item.table_name === entry.table)
        if (!table) return null
        return {
          table_name: table.table_name,
          table_alias: entry.alias,
          description: table.description,
          columns: (table.columns || []).map(column => ({
            name: column.name,
            type: column.type,
            mysql_type: column.mysql_type,
            hive_type: column.hive_type,
            description: column.description,
          }))
        }
      })
      .filter(Boolean) as Array<{
        table_name: string
        table_alias: string
        description?: string
        columns: Array<{ name: string; type: string; mysql_type?: string; hive_type?: string; description?: string }>
      }>
    const res = await autoPrefillSemanticFields({
      datasource_id: props.datasourceId,
      config_id: activeConfig.value.id,
      table_model_type: 'detail',
      business_context: autoPrefillBusinessContext.value.trim(),
      tables: targetTables,
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '自动预填失败')
      return
    }
    const fields = res.data?.fields || []
    const dimensionNameMap = new Map(
      fields
        .filter(item => item.role === 'dimension' && item.name)
        .map(item => [buildFieldRowKey(item.table_alias || item.table, item.source_column || item.column), item.name as string])
    )
    const selectedAliasSet = new Set(selectedTables.map(entry => entry.alias))
    const selectedColumnCounts = new Map<string, number>()
    selectedTables.forEach(entry => {
      const table = props.tables.find(item => item.table_name === entry.table)
      ;(table?.columns || []).forEach(column => {
        selectedColumnCounts.set(column.name, (selectedColumnCounts.get(column.name) || 0) + 1)
      })
    })
    fieldRows.value.forEach(row => {
      if (row.isCustom) return
      if (!selectedAliasSet.has(getFieldRowAlias(row))) return
      if (row.isMetric) return
      row.isDimension = true
      row.isMetric = false
      row.expression = quoteIdentifier(row.sourceColumn || row.column)
      const prefilledName = dimensionNameMap.get(buildFieldRowKey(getFieldRowAlias(row), row.sourceColumn || row.column))
      if (prefilledName && isGeneratedFieldDisplayName(row)) {
        row.displayName = prefilledName
      } else if ((selectedColumnCounts.get(row.sourceColumn || row.column) || 0) > 1 && isGeneratedFieldDisplayName(row)) {
        row.displayName = buildFieldRowDefaultDisplayName(row)
      }
    })
    const metricCount = fields.filter(item => item.role === 'metric').reduce((count, item) => {
      const matchedEntries = selectedTables.filter(entry => entry.table === item.table && (!item.table_alias || entry.alias === item.table_alias))
      return count + matchedEntries.reduce((innerCount, entry) => innerCount + (addAutoPrefillMetricRow(item, entry) ? 1 : 0), 0)
    }, 0)
    if (metricCount > 0) {
      void refreshFieldColumnDictVisibleMap()
    }
    ElMessage.success(`已预填物理字段为维度${metricCount > 0 ? `，并推荐 ${metricCount} 个指标` : ''}`)
  } catch (err) {
    ElMessage.error('自动预填失败')
  } finally {
    autoPrefillLoading.value = false
  }
}

const openHistoryLearningDialog = () => {
  if (!activeConfig.value) return
  historyLearningResult.value = null
  historyLearningForm.value = {
    days: 7,
    limit: 50,
    sources: ['agent', 'builder', 'sql'],
    include_failed: true,
  }
  showHistoryLearningDialog.value = true
}

const runHistoryLearning = async () => {
  if (!activeConfig.value) return
  if (historyLearningForm.value.sources.length === 0) {
    ElMessage.warning('请至少选择一个学习来源')
    return
  }
  historyLearningLoading.value = true
  try {
    const res = await learnSemanticConfigFromHistory(props.datasourceId, activeConfig.value.id, {
      days: historyLearningForm.value.days,
      limit: historyLearningForm.value.limit,
      sources: historyLearningForm.value.sources,
      include_failed: historyLearningForm.value.include_failed,
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '历史学习失败')
      return
    }
    historyLearningResult.value = res.data
    ElMessage.success('历史学习完成')
  } catch (err) {
    const apiMsg = (err as any)?.response?.data?.msg
    ElMessage.error(apiMsg || '历史学习失败')
  } finally {
    historyLearningLoading.value = false
  }
}

const getHistorySampleIntent = (sample: { query_text?: string; conversation_context?: { user_turns?: Array<{ content: string }> } }) => {
  const latestUserTurn = sample.conversation_context?.user_turns?.slice(-1)[0]?.content
  return latestUserTurn || sample.query_text || '-'
}

const getHistorySampleSignal = (sample: { error_message?: string; sql?: string; conversation_context?: { negative_feedback?: string[]; tool_errors?: Array<{ content: string }> } }) => {
  const feedback = sample.conversation_context?.negative_feedback?.slice(-1)[0]
  if (feedback) return `用户反馈：${feedback}`
  const toolError = sample.conversation_context?.tool_errors?.slice(-1)[0]?.content
  if (toolError) return `工具错误：${toolError}`
  return sample.error_message || sample.sql || '-'
}

const getHistorySampleSignalType = (sample: { error_message?: string; conversation_context?: { negative_feedback?: string[]; tool_errors?: Array<{ content: string }> } }) => {
  if (sample.conversation_context?.negative_feedback?.length) return '用户反馈'
  if (sample.conversation_context?.tool_errors?.length) return '工具错误'
  if (sample.error_message) return '执行错误'
  return 'SQL'
}

const getFirstScopeEntryByPhysicalTable = (tableName: string) => {
  const configId = activeConfig.value?.id
  if (!configId) return null
  return getScopeEntries(configId).find(entry => entry.table === tableName) || null
}

const buildHistoryFieldKey = (entry: TableScopeEntry, suggestion: SemanticHistoryLearningSuggestion, mode: 'metric' | 'dimension') => {
  const fieldName = normalizeFieldName(suggestion.field_name || suggestion.column || suggestion.name || '')
  return buildFieldRowKey(entry.alias, fieldName, `#learn_${mode}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`)
}

const acceptHistoryMetric = (suggestion: SemanticHistoryLearningSuggestion) => {
  const tableName = (suggestion.table || '').trim()
  const fieldName = normalizeFieldName(suggestion.field_name || suggestion.name || '')
  const displayName = (suggestion.name || '').trim()
  const expression = (suggestion.expression || '').trim()
  const sourceColumn = getFirstPhysicalColumnFromExpression(tableName, expression)
  if (!displayName) {
    ElMessage.warning('候选指标缺少名称')
    return
  }
  if (suggestion.needs_review || !tableName || !fieldName || !sourceColumn) {
    ElMessage.warning(suggestion.review_reason || '候选指标缺少表或表达式物理字段，请人工确认后再采纳')
    return
  }
  const entry = getFirstScopeEntryByPhysicalTable(tableName)
  if (!entry) {
    ElMessage.warning('候选指标所属表不在当前模型配置中')
    return
  }
  if (fieldRows.value.some(row => ((row.displayName || '').trim() || row.column) === displayName)) {
    ElMessage.warning(`显示名称重复：${displayName}`)
    return
  }
  const columnType = getTableColumnType(tableName, sourceColumn)
  fieldRows.value.push({
    key: buildHistoryFieldKey(entry, suggestion, 'metric'),
    table: tableName,
    tableAlias: entry.alias,
    column: getUniqueCustomFieldName(entry.alias, fieldName),
    sourceColumn,
    columnType,
    isDimension: false,
    isMetric: true,
    displayName,
    columnComment: getTableColumnComment(tableName, sourceColumn),
    description: '',
    metadataSupplement: '',
    metricAggFunc: parseAggFuncFromExpression(suggestion.expression || 'COUNT(*)'),
    expression: expression || buildMetricExpression(isNumericColumnType(columnType) ? 'SUM' : 'COUNT', sourceColumn),
    isCustom: true,
  })
  void refreshFieldColumnDictVisibleMap()
  ElMessage.success('已采纳到字段配置草稿，保存后生效')
}

const acceptHistoryDimension = (suggestion: SemanticHistoryLearningSuggestion) => {
  const tableName = (suggestion.table || '').trim()
  const columnName = normalizeFieldName(suggestion.column || suggestion.field_name || '')
  const displayName = (suggestion.name || '').trim()
  if (!displayName) {
    ElMessage.warning('候选维度缺少名称')
    return
  }
  if (suggestion.needs_review || !tableName || !columnName) {
    ElMessage.warning(suggestion.review_reason || '候选维度缺少表或字段，请人工确认后再采纳')
    return
  }
  const entry = getFirstScopeEntryByPhysicalTable(tableName)
  if (!entry) {
    ElMessage.warning('候选维度所属表不在当前模型配置中')
    return
  }
  if (fieldRows.value.some(row => ((row.displayName || '').trim() || row.column) === displayName)) {
    ElMessage.warning(`显示名称重复：${displayName}`)
    return
  }
  const columnType = getTableColumnType(tableName, columnName)
  fieldRows.value.push({
    key: buildHistoryFieldKey(entry, suggestion, 'dimension'),
    table: tableName,
    tableAlias: entry.alias,
    column: getUniqueCustomFieldName(entry.alias, columnName),
    sourceColumn: columnName,
    columnType,
    isDimension: true,
    isMetric: false,
    displayName,
    columnComment: getTableColumnComment(tableName, columnName),
    description: '',
    metadataSupplement: '',
    metricAggFunc: 'COUNT',
    expression: (suggestion.expression || quoteIdentifier(columnName)).trim(),
    isCustom: true,
  })
  void refreshFieldColumnDictVisibleMap()
  ElMessage.success('已采纳到字段配置草稿，保存后生效')
}

const removeFieldRow = (row: FieldConfigRow) => {
  fieldRows.value = fieldRows.value.filter(item => item.key !== row.key)
}

const previewTableData = async (tableName: string) => {
  if (!props.datasourceId || !tableName) return
  previewingTableName.value = tableName
  tablePreviewTitle.value = tableName
  tablePreviewColumns.value = []
  tablePreviewRows.value = []
  tablePreviewError.value = ''
  tablePreviewMode.value = 'table'
  showTablePreviewDialog.value = true
  try {
    const res = await executeSql({
      datasource_id: props.datasourceId,
      sql: `SELECT * FROM ${quoteTableIdentifier(tableName)} LIMIT 1`,
      limit: 1,
    })
    if (res.status !== 'success' || !res.data) {
      tablePreviewError.value = res.msg || '查询失败'
      return
    }
    const firstResult = Array.isArray(res.data.results) && res.data.results.length > 0
      ? res.data.results[0]
      : res.data
    if (firstResult && firstResult.success === false) {
      tablePreviewError.value = firstResult.error || '查询失败'
      return
    }
    const rows = firstResult?.data || []
    const columns = firstResult?.columns?.length
      ? firstResult.columns
      : Object.keys(rows[0] || {})
    tablePreviewColumns.value = columns
    tablePreviewRows.value = rows.slice(0, 1)
  } catch (err) {
    const apiMsg = (err as any)?.response?.data?.msg
    const apiDetail = (err as any)?.response?.data?.detail
    tablePreviewError.value = apiMsg || apiDetail || (err instanceof Error ? err.message : '查询失败')
  } finally {
    previewingTableName.value = ''
  }
}

const getFieldPhysicalColumn = (row: FieldConfigRow) => String(row.sourceColumn || row.column || '').trim()

const getColumnDictKey = (tableName: string, columnName: string) => `${tableName}.${columnName}`

const getFieldColumnDictKey = (row: FieldConfigRow) => {
  const tableName = String(row.table || '').trim()
  const columnName = getFieldPhysicalColumn(row)
  return tableName && columnName ? getColumnDictKey(tableName, columnName) : ''
}

const hasColumnDictContent = (dict?: DatasourceColumnDictPayload | null) => {
  if (!dict) return false
  return Boolean(
    (dict.enum_values || []).length > 0 ||
    (dict.storage_format || '').trim() ||
    (dict.unit || '').trim()
  )
}

const hasFieldColumnDict = (row: FieldConfigRow) => {
  const key = getFieldColumnDictKey(row)
  return Boolean(key && columnDictVisibleMap.value[key])
}

const refreshFieldColumnDictVisibleMap = async () => {
  const datasourceId = props.datasourceId
  const seq = ++columnDictLoadSeq
  if (!datasourceId || fieldRows.value.length === 0) {
    columnDictVisibleMap.value = {}
    return
  }
  const targets = Array.from(new Set(
    fieldRows.value
      .filter(row => !row.isMissingPhysical)
      .map(row => {
        const tableName = String(row.table || '').trim()
        const columnName = getFieldPhysicalColumn(row)
        return tableName && columnName ? `${tableName}::${columnName}` : ''
      })
      .filter(Boolean)
  ))
  if (targets.length === 0) {
    columnDictVisibleMap.value = {}
    return
  }
  const tableTargets = new Map<string, Set<string>>()
  targets.forEach(target => {
    const [tableName, columnName] = target.split('::')
    if (!tableName || !columnName) return
    if (!tableTargets.has(tableName)) tableTargets.set(tableName, new Set())
    tableTargets.get(tableName)?.add(columnName)
  })
  const entries = await Promise.all(Array.from(tableTargets.entries()).map(async ([tableName, columnNames]) => {
    try {
      const res = await getDatasourceTableColumnDicts(datasourceId, tableName)
      const dicts = res.status === 'success' ? (res.data?.dicts || {}) : {}
      return Array.from(columnNames).map(columnName => [getColumnDictKey(tableName, columnName), hasColumnDictContent(dicts[columnName])] as const)
    } catch {
      return Array.from(columnNames).map(columnName => [getColumnDictKey(tableName, columnName), false] as const)
    }
  }))
  if (seq !== columnDictLoadSeq) return
  columnDictVisibleMap.value = Object.fromEntries(entries.flat())
}

const viewColumnDict = async (row: FieldConfigRow) => {
  const tableName = String(row.table || '').trim()
  const columnName = getFieldPhysicalColumn(row)
  if (!props.datasourceId || !tableName || !columnName || row.isMissingPhysical) {
    ElMessage.warning('无法定位物理字段')
    return
  }
  columnDictDialogTitle.value = `${tableName}.${columnName}`
  columnDictPayload.value = null
  columnDictError.value = ''
  showColumnDictDialog.value = true
  loadingColumnDict.value = true
  try {
    const res = await getDatasourceColumnDict(props.datasourceId, tableName, columnName)
    if (res.status !== 'success') {
      columnDictError.value = res.msg || '获取字段字典失败'
      return
    }
    columnDictPayload.value = res.data?.dict || null
  } catch (err) {
    const apiMsg = (err as any)?.response?.data?.msg
    const apiDetail = (err as any)?.response?.data?.detail
    columnDictError.value = apiMsg || apiDetail || (err instanceof Error ? err.message : '获取字段字典失败')
  } finally {
    loadingColumnDict.value = false
  }
}

const buildDimensionExpression = (func: string, column: string) => {
  const quoted = quoteIdentifier(column)
  if (func === 'CASE_WHEN') return `CASE\n  WHEN ${quoted} = '条件值1' THEN '分类1'\n  WHEN ${quoted} = '条件值2' THEN '分类2'\n  ELSE '其他'\nEND`
  if (func === 'DATE') return `DATE(${quoted})`
  if (func === 'DATE_FORMAT_MONTH') return `DATE_FORMAT(${quoted}, '%Y-%m')`
  if (func === 'STR_TO_DATE') return `STR_TO_DATE(CAST(${quoted} AS CHAR), '%Y-%m-%d')`
  if (func === 'HIVE_DATE_FORMAT_DAY') return `from_unixtime(unix_timestamp(${quoted}, 'yyyyMMdd'), 'yyyy-MM-dd')`
  if (func === 'HIVE_DATE_FORMAT_MONTH') return `from_unixtime(unix_timestamp(${quoted}, 'yyyyMMdd'), 'yyyy-MM')`
  if (func === 'HIVE_DATE_FORMAT_YEAR') return `from_unixtime(unix_timestamp(${quoted}, 'yyyyMMdd'), 'yyyy')`
  if (func === 'YEAR') return `YEAR(${quoted})`
  if (func === 'LOWER') return `LOWER(${quoted})`
  if (func === 'UPPER') return `UPPER(${quoted})`
  if (func === 'TRIM') return `TRIM(${quoted})`
  if (func === 'CONCAT') return `CONCAT(${quoted}, '_suffix')`
  if (func === 'SUBSTRING') return `SUBSTRING(${quoted}, 1, 10)`
  if (func === 'LEFT') return `LEFT(${quoted}, 10)`
  if (func === 'RIGHT') return `RIGHT(${quoted}, 10)`
  if (func === 'LENGTH') return `LENGTH(${quoted})`
  if (func === 'REPLACE') return `REPLACE(${quoted}, '旧值', '新值')`
  return quoted
}

const buildFieldRows = (configId: string) => {
  const config = configs.value.find(c => c.id === configId)
  if (!config) return []
  const scopeEntries = getScopeEntries(configId)
  const scopeAliasSet = new Set(scopeEntries.map(entry => entry.alias))

  const dimensionSet = new Set<string>()
  const dimensionByColumnMap = new Map<string, SemanticDimension>()
  const metricByColumnMap = new Map<string, { agg: string; expression: string; name: string; fieldName?: string; description?: string; metadataSupplement?: string }>()
  const customRowsMap = new Map<string, FieldConfigRow>()
  const configuredKeys = new Set<string>()

  config.dimensions.forEach(d => {
    const tableAlias = getFieldTableAlias(d)
    if (!scopeAliasSet.has(tableAlias)) return
    const physicalKey = `${tableAlias}.${d.column}`
    const fieldName = (d.field_name || d.column || '').trim()
    const expression = (d.expression || quoteIdentifier(d.column)).trim()
    configuredKeys.add(physicalKey)
    if (fieldName && d.column && fieldName !== d.column && !isRawColumnExpression(expression, d.column)) {
      const customKey = buildFieldRowKey(tableAlias, fieldName)
      const existed = customRowsMap.get(customKey)
      customRowsMap.set(customKey, {
        key: customKey,
        table: d.table,
        tableAlias,
        column: fieldName,
        sourceColumn: d.column,
        columnType: existed?.columnType || '',
        isDimension: true,
        isMetric: existed?.isMetric || false,
        displayName: existed?.displayName || d.name || fieldName,
        columnComment: getTableColumnComment(d.table, d.column),
        description: existed?.description || d.description || '',
        metadataSupplement: existed?.metadataSupplement || d.metadata_supplement || '',
        metricAggFunc: existed?.metricAggFunc || 'COUNT',
        expression: existed?.expression || expression,
        isCustom: true,
      })
      return
    }
    dimensionSet.add(physicalKey)
    if (!dimensionByColumnMap.has(physicalKey)) {
      dimensionByColumnMap.set(physicalKey, d)
    }
  })

  config.metrics.forEach(m => {
    const tableAlias = getFieldTableAlias(m)
    if (!scopeAliasSet.has(tableAlias)) return
    const match = m.expression.match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
    const expression = (m.expression || '').trim()
    const exprColumn = normalizeExpressionColumn(match?.[3]?.trim() || expression)
    const agg = match ? (match?.[2] ? 'COUNT_DISTINCT' : (match?.[1] || 'COUNT').toUpperCase()) : 'COUNT'
    if (!exprColumn || isCountAllExpression(expression)) return
    const fieldName = (m.field_name || exprColumn || '').trim()
    configuredKeys.add(`${tableAlias}.${exprColumn}`)
    if (fieldName && exprColumn && fieldName !== exprColumn) {
      const customKey = buildFieldRowKey(tableAlias, fieldName)
      const existed = customRowsMap.get(customKey)
      customRowsMap.set(customKey, {
        key: customKey,
        table: m.table,
        tableAlias,
        column: fieldName,
        sourceColumn: exprColumn,
        columnType: existed?.columnType || '',
        isDimension: existed?.isDimension || false,
        isMetric: true,
        displayName: m.name || fieldName,
        columnComment: getTableColumnComment(m.table, exprColumn),
        description: m.description || existed?.description || '',
        metadataSupplement: m.metadata_supplement || existed?.metadataSupplement || '',
        metricAggFunc: agg,
        expression: expression || existed?.expression || quoteIdentifier(exprColumn),
        isCustom: true,
      })
      return
    }
    const physicalKey = `${tableAlias}.${exprColumn}`
    if (!metricByColumnMap.has(physicalKey)) {
      metricByColumnMap.set(physicalKey, { agg, expression, name: m.name, fieldName: m.field_name, description: m.description, metadataSupplement: m.metadata_supplement })
    }
  })

  const rows: FieldConfigRow[] = []
  scopeEntries.forEach(entry => {
    const table = getScopeTableInfo(entry)
    if (!table) return
    table.columns?.forEach(col => {
      const key = buildFieldRowKey(entry.alias, col.name)
      const dimInfo = dimensionByColumnMap.get(key)
      const metricInfo = metricByColumnMap.get(key)
      const metricAggFunc = metricInfo?.agg || 'COUNT'
      const expression = metricInfo?.expression || dimInfo?.expression || quoteIdentifier(col.name)
      const columnComment = (col.description || '').trim()
      const displayName = metricInfo?.name || dimInfo?.name || buildAliasAwareDefaultDisplayName(entry, col.name, columnComment)
      const description = metricInfo?.description || dimInfo?.description || ''
      const metadataSupplement = metricInfo?.metadataSupplement || dimInfo?.metadata_supplement || ''
      rows.push({
        key,
        table: table.table_name,
        tableAlias: entry.alias,
        column: col.name,
        sourceColumn: col.name,
        columnType: col.mysql_type || col.type || '',
        isDimension: dimensionSet.has(key),
        isMetric: !!metricInfo,
        displayName,
        columnComment,
        description,
        metadataSupplement,
        metricAggFunc,
        expression,
      })
    })
  })
  customRowsMap.forEach(row => {
    const tableInfo = getScopeTableInfo({ table: row.table, alias: row.tableAlias })
    const sourceInfo = tableInfo?.columns?.find(c => c.name === (row.sourceColumn || row.column))
    rows.push({
      ...row,
      columnType: row.columnType || sourceInfo?.mysql_type || sourceInfo?.type || '',
      expression: row.expression || quoteIdentifier(row.sourceColumn || row.column),
      isCustom: true,
      isMissingPhysical: !sourceInfo,
    })
  })
  config.dimensions.forEach(d => {
    const tableAlias = getFieldTableAlias(d)
    if (!scopeAliasSet.has(tableAlias)) return
    const key = `${tableAlias}.${d.column}`
    if (!configuredKeys.has(key) || rows.some(row => getFieldRowAlias(row) === tableAlias && (row.sourceColumn || row.column) === d.column)) return
    rows.push({
      key,
      table: d.table,
      tableAlias,
      column: d.field_name || d.column,
      sourceColumn: d.column,
      columnType: '',
      isDimension: true,
      isMetric: false,
      displayName: d.name || d.field_name || d.column,
      columnComment: getTableColumnComment(d.table, d.column),
      description: d.description || '',
      metadataSupplement: d.metadata_supplement || '',
      metricAggFunc: 'COUNT',
      expression: d.expression || quoteIdentifier(d.column),
      isMissingPhysical: true,
    })
  })
  config.metrics.forEach(m => {
    const tableAlias = getFieldTableAlias(m)
    if (!scopeAliasSet.has(tableAlias)) return
    const match = m.expression.match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
    const expression = (m.expression || '').trim()
    const exprColumn = normalizeExpressionColumn(match?.[3]?.trim() || expression)
    if (!exprColumn || isCountAllExpression(expression)) return
    const key = `${tableAlias}.${exprColumn}`
    if (!configuredKeys.has(key) || rows.some(row => getFieldRowAlias(row) === tableAlias && (row.sourceColumn || row.column) === exprColumn)) return
    const agg = match ? (match?.[2] ? 'COUNT_DISTINCT' : (match?.[1] || 'COUNT').toUpperCase()) : 'COUNT'
    rows.push({
      key,
      table: m.table,
      tableAlias,
      column: m.field_name || exprColumn,
      sourceColumn: exprColumn,
      columnType: '',
      isDimension: false,
      isMetric: true,
      displayName: m.name || m.field_name || exprColumn,
      columnComment: getTableColumnComment(m.table, exprColumn),
      description: m.description || '',
      metadataSupplement: m.metadata_supplement || '',
      metricAggFunc: agg,
      expression,
      isMissingPhysical: true,
    })
  })
  return rows
}

const mergeFieldRowsWithDraft = (nextRows: FieldConfigRow[]) => {
  const scopeEntries = getScopeEntries(activeConfig.value?.id || '')
  const currentMap = new Map(fieldRows.value.map(row => [row.key, row]))
  const nextKeySet = new Set(nextRows.map(row => row.key))
  const mergedRows = nextRows.map(row => {
    const draft = currentMap.get(row.key)
    if (!draft) return row
    return {
      ...row,
      isDimension: draft.isDimension,
      isMetric: draft.isMetric,
      displayName: draft.displayName,
      description: draft.description ?? row.description,
      metadataSupplement: draft.metadataSupplement,
      metricAggFunc: draft.metricAggFunc,
      expression: draft.expression,
    }
  })
  fieldRows.value
    .filter(row => row.isCustom && !nextKeySet.has(row.key) && isFieldRowInScope(row, scopeEntries))
    .forEach(row => {
      mergedRows.push({ ...row })
    })
  return mergedRows
}

const loadFieldRows = (configId: string) => {
  fieldRows.value = buildFieldRows(configId)
  fieldRowsConfigId.value = configId
  fieldRowsSnapshot.value = buildFieldRowsEditSignature(fieldRows.value)
  void refreshFieldColumnDictVisibleMap()
}

const applyFieldSetting = (showToast = true) => {
  const configId = activeConfig.value?.id
  if (!configId) return false
  const config = configs.value.find(c => c.id === configId)
  if (!config) return false

  const validationError = validateFieldRowsBeforeApply()
  if (validationError) {
    ElMessage.warning(validationError)
    return false
  }

  // 兜底互斥：维度/指标只能二选一
  fieldRows.value.forEach(row => {
    if (row.isDimension && row.isMetric) {
      row.isMetric = false
    }
  })

  const nextDimensions: SemanticDimension[] = fieldRows.value
    .filter(r => r.isDimension)
    .map(r => ({
      name: ((r.displayName || '').trim() || r.column),
      field_name: r.column,
      table: r.table,
      table_alias: getFieldRowAlias(r),
      column: r.sourceColumn || r.column,
      expression: (r.isMetric ? quoteIdentifier(r.sourceColumn || r.column) : (r.expression || quoteIdentifier(r.sourceColumn || r.column))).trim(),
      type: /date|time|timestamp|datetime|year/i.test(r.columnType) ? 'time' : /int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/i.test(r.columnType) ? 'number' : 'categorical',
      description: r.description || '',
      metadata_supplement: (r.metadataSupplement || '').trim(),
    }))

  const nextMetrics: SemanticMetric[] = fieldRows.value
    .filter(r => r.isMetric)
    .map(r => ({
      name: ((r.displayName || '').trim() || r.column),
      field_name: r.column,
      table: r.table,
      table_alias: getFieldRowAlias(r),
      expression: (r.expression || quoteIdentifier(r.sourceColumn || r.column)).trim(),
      data_type: getMetricDataType(r.columnType),
      filter: '',
      description: r.description || '',
      metadata_supplement: (r.metadataSupplement || '').trim(),
    }))

  updateConfig(configId, {
    dimensions: nextDimensions,
    metrics: nextMetrics
  })
  fieldRowsSnapshot.value = buildFieldRowsEditSignature(fieldRows.value)
  if (showToast) {
    ElMessage.success('字段设置已应用')
  }
  return true
}

const saveActiveConfig = async () => {
  if (!activeConfig.value) return
  if (isFieldRowsDirty.value) {
    const applied = applyFieldSetting(false)
    if (!applied) return
  }
  if (!validateRelationshipIntegrity(activeConfig.value.id)) {
    return
  }
  await saveConfig(activeConfig.value)
}

const validateRelationshipIntegrity = (configId: string) => {
  const config = configs.value.find(c => c.id === configId)
  if (!config) return true
  const scope = getScopeEntries(configId)
  if (scope.length <= 1) return true
  const rootEntry = scope[0]
  if (!rootEntry) return true

  const scopeAliasSet = new Set(scope.map(entry => entry.alias))
  const adjacency = new Map<string, Set<string>>()
  scope.forEach(entry => adjacency.set(entry.alias, new Set()))
  config.relationships.forEach(rel => {
    const leftAlias = getRelationshipLeftAlias(rel)
    const rightAlias = getRelationshipRightAlias(rel)
    if (!scopeAliasSet.has(leftAlias) || !scopeAliasSet.has(rightAlias)) return
    adjacency.get(leftAlias)?.add(rightAlias)
    adjacency.get(rightAlias)?.add(leftAlias)
  })

  const visited = new Set<string>()
  const queue: string[] = [rootEntry.alias]
  visited.add(rootEntry.alias)
  while (queue.length > 0) {
    const current = queue.shift() as string
    ;(adjacency.get(current) || new Set()).forEach(next => {
      if (visited.has(next)) return
      visited.add(next)
      queue.push(next)
    })
  }

  const unlinked = scope.filter(entry => !visited.has(entry.alias)).map(entry => getScopeEntryLabel(entry))
  if (unlinked.length > 0) {
    ElMessage.warning(`存在未关联表：${unlinked.join('、')}，请先配置关联或移除这些表`)
    return false
  }
  return true
}

watch(
  () => {
    const configId = activeConfig.value?.id || ''
    const scope = configId ? getScopeEntries(configId) : []
    return `${configId}::${scope.map(entry => `${entry.alias}:${entry.table}`).join(',')}`
  },
  signature => {
    if (!signature || signature.startsWith('::')) {
      fieldRows.value = []
      fieldRowsConfigId.value = null
      fieldRowsSnapshot.value = buildFieldRowsEditSignature([])
      columnDictVisibleMap.value = {}
      return
    }
    const configId = signature.split('::')[0] || ''
    if (!configId) return
    if (fieldRowsConfigId.value === configId && isFieldRowsDirty.value) {
      fieldRows.value = mergeFieldRowsWithDraft(buildFieldRows(configId))
      void refreshFieldColumnDictVisibleMap()
      return
    }
    loadFieldRows(configId)
  },
  { immediate: true }
)

const handleDimensionToggle = (row: FieldConfigRow, checked: boolean) => {
  const wasMetric = row.isMetric
  row.isDimension = checked
    if (checked) {
      row.isMetric = false
      if (wasMetric || !row.expression) {
      row.expression = quoteIdentifier(row.sourceColumn || row.column)
      }
    }
}

const handleMetricToggle = (row: FieldConfigRow, checked: boolean) => {
  row.isMetric = checked
  if (checked) {
    row.isDimension = false
    if (!row.metricAggFunc) row.metricAggFunc = 'COUNT'
    if (!row.expression) {
      row.expression = quoteIdentifier(row.sourceColumn || row.column)
    }
  }
}

const handleAggFuncChange = (row: FieldConfigRow, aggFunc: string) => {
  row.metricAggFunc = aggFunc
  row.expression = buildMetricExpression(aggFunc, row.sourceColumn || row.column)
}

const handleExpressionInput = (row: FieldConfigRow, expression: string) => {
  row.expression = expression
  row.metricAggFunc = parseAggFuncFromExpression(expression)
}

const openExpressionEditorDialog = (row: FieldConfigRow) => {
  if (row.isMissingPhysical || (!row.isMetric && !row.isDimension)) return
  editingExpressionRowKey.value = row.key
  editingExpressionText.value = row.expression || quoteIdentifier(row.sourceColumn || row.column)
  showExpressionEditorDialog.value = true
}

const editingExpressionRow = computed(() => {
  return fieldRows.value.find(item => item.key === editingExpressionRowKey.value) || null
})

const saveExpressionEditor = () => {
  const row = editingExpressionRow.value
  if (row) {
    handleExpressionInput(row, editingExpressionText.value.trim())
  }
  showExpressionEditorDialog.value = false
  editingExpressionRowKey.value = ''
  editingExpressionText.value = ''
}

const insertExpressionFromEditor = (expression: string) => {
  editingExpressionText.value = expression
}

const splitOntologyRules = (text: string) => {
  const content = (text || '').trim()
  if (!content) return { businessFlow: '' }
  const businessMatch = content.match(/【业务运转说明】\s*([\s\S]*?)(?=【元数据补充】|$)/)
  if (businessMatch) {
    return {
      businessFlow: (businessMatch?.[1] || '').trim(),
    }
  }
  if (content.includes('【元数据补充】')) {
    return { businessFlow: '' }
  }
  return { businessFlow: content }
}

const buildOntologyRulesTextFromParts = (businessFlowText: string) => {
  const businessFlow = businessFlowText.trim()
  const sections: string[] = []
  if (businessFlow) sections.push(`【业务运转说明】\n${businessFlow}`)
  return sections.join('\n\n')
}

const buildOntologyRulesText = () => {
  return buildOntologyRulesTextFromParts(editingBusinessFlowText.value)
}

const openOntologyRulesDialog = () => {
  if (!activeConfig.value) return
  const parsed = splitOntologyRules(activeConfig.value.ontology_rules || '')
  editingBusinessFlowText.value = parsed.businessFlow
  showOntologyRulesDialog.value = true
}

const saveOntologyRules = () => {
  if (!activeConfig.value) return
  updateConfig(activeConfig.value.id, { ontology_rules: buildOntologyRulesText() })
  showOntologyRulesDialog.value = false
}

</script>

<template>
  <div class="h-full flex flex-col bg-white">
    <!-- 头部 -->
    <div v-if="!embedded" class="h-16 shrink-0 flex items-center gap-4 px-4 border-b border-gray-200 bg-white">
      <div class="flex items-center gap-3 shrink-0">
        <button
          class="w-8 h-8 flex items-center justify-center border-none rounded-lg bg-transparent text-gray-500 cursor-pointer transition-all duration-200 hover:bg-gray-100 hover:text-gray-700"
          title="返回"
          @click="emit('close')"
        >
          <i class="fa-solid fa-arrow-left"></i>
        </button>
        <div class="text-sm font-semibold text-gray-800">数据集管理</div>
      </div>
      <div class="flex min-w-0 flex-1 items-center gap-3">
        <span class="text-[13px] font-medium text-slate-700">数据源组</span>
        <div class="h-9 min-w-[220px] max-w-[360px] flex items-center rounded-lg border border-slate-200 bg-slate-50 px-3 text-[13px] text-slate-700 truncate">
          {{ datasourceName || '暂无数据源' }}
        </div>
        <span class="ml-2 text-[13px] font-medium text-slate-700">数据集</span>
        <el-select
          :model-value="activeConfig?.id || ''"
          class="min-w-[260px] max-w-[460px]"
          filterable
          :disabled="loading || configs.length === 0"
          placeholder="请选择数据集"
          fit-input-width
          popper-class="chatdata-semantic-config-select-popper"
          @change="toggleExpand"
        >
          <el-option v-if="loading" value="" label="加载中" />
          <el-option v-else-if="configs.length === 0" value="" label="暂无数据集" />
          <el-option
            v-for="config in configs"
            :key="config.id"
            :value="config.id"
            :label="`${config.name}（${config.metrics?.length || 0}指标 · ${config.dimensions?.length || 0}维度）`"
          />
        </el-select>
        <button
          class="h-9 w-9 shrink-0 flex items-center justify-center border-none rounded-lg bg-blue-500 text-white text-[13px] transition-all duration-150 hover:bg-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
          title="新建数据集"
          :disabled="creatingConfig"
          @click="createConfig"
        >
          <i :class="['fa-solid', creatingConfig ? 'fa-spinner fa-spin' : 'fa-plus']"></i>
        </button>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-400 text-2xl">
      <i class="fa-solid fa-spinner fa-spin"></i>
    </div>

    <!-- 配置列表 -->
    <div v-else class="flex-1 min-h-0 p-4">
      <div v-if="configs.length === 0" class="flex flex-col items-center justify-center p-12 border-2 border-dashed border-gray-200 rounded-xl text-center">
        <i class="fa-solid fa-cube text-4xl text-blue-500 opacity-30 mb-3"></i>
        <p class="text-sm text-gray-500 m-0 mb-1">暂无数据集</p>
        <span class="text-xs text-gray-400">点击上方加号创建第一个数据集</span>
      </div>

      <div v-else class="h-full min-h-0">
        <div v-if="activeConfig" class="h-full min-h-0 border border-gray-200 rounded-xl bg-white overflow-hidden flex flex-col">
          <div class="px-4 py-3 border-b border-gray-100 bg-gray-50">
            <div
              class="grid gap-3 items-center"
              :class="showDatasetSelectorInBody ? 'grid-cols-[260px_220px_1fr_auto]' : 'grid-cols-[220px_1fr_auto]'"
            >
              <div v-if="showDatasetSelectorInBody" class="flex items-center gap-2 min-w-0">
                <el-select
                  :model-value="activeConfig.id"
                  class="min-w-0 flex-1"
                  filterable
                  placeholder="请选择数据集"
                  fit-input-width
                  popper-class="chatdata-semantic-config-select-popper"
                  @change="toggleExpand"
                >
                  <el-option
                    v-for="config in configs"
                    :key="config.id"
                    :value="config.id"
                    :label="`${config.name}（${config.metrics?.length || 0}指标 · ${config.dimensions?.length || 0}维度）`"
                  />
                </el-select>
                <button
                  class="h-9 w-9 shrink-0 flex items-center justify-center border-none rounded-md bg-blue-500 text-white text-[12px] cursor-pointer transition-all duration-150 hover:bg-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
                  title="新建数据集"
                  :disabled="creatingConfig"
                  @click="createConfig"
                >
                  <i :class="['fa-solid', creatingConfig ? 'fa-spinner fa-spin' : 'fa-plus']"></i>
                </button>
              </div>
              <input
                type="text"
                :value="activeConfig.name"
                class="px-3 py-2 border border-gray-200 rounded-md text-[13px] text-gray-700 outline-none focus:border-blue-500"
                placeholder="数据集名称"
                :disabled="!canManageConfig(activeConfig)"
                @input="updateConfig(activeConfig.id, { name: ($event.target as HTMLInputElement).value })"
              />
              <input
                type="text"
                :value="activeConfig.description"
                placeholder="数据集描述：简要说明这个数据集是什么"
                class="px-3 py-2 border border-gray-200 rounded-md text-[13px] text-gray-500 outline-none focus:border-blue-500"
                :disabled="!canManageConfig(activeConfig)"
                @input="updateConfig(activeConfig.id, { description: ($event.target as HTMLInputElement).value })"
              />
              <div class="flex items-center gap-2 justify-end">
                <div
                  class="h-8 max-w-[168px] shrink-0 inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2 text-[12px] text-gray-500"
                  :title="buildConfigMaintainerTitle(activeConfig)"
                >
                  <i class="fa-regular fa-user text-[11px] text-gray-400"></i>
                  <span class="shrink-0 text-gray-400">创建人</span>
                  <span class="truncate">{{ getConfigCreatorName(activeConfig) }}</span>
                </div>
                <button
                  :class="[
                    'px-3 py-2 border-none rounded-md text-xs transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
                    activeConfig.enabled ? 'bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/15' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                  ]"
                  :disabled="!canManageConfig(activeConfig)"
                  @click="handleToggleEnabled(activeConfig)"
                >
                  <i :class="['fa-solid mr-1', activeConfig.enabled ? 'fa-circle-check' : 'fa-circle-pause']"></i>{{ activeConfig.enabled ? '已启用' : '已禁用' }}
                </button>
                <button
                  class="px-3 py-2 rounded-md text-xs border border-blue-200 bg-white text-blue-600 transition-all duration-150 hover:bg-blue-50 hover:border-blue-300 disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="!activeConfig || !canManageConfig(activeConfig)"
                  @click="openHistoryLearningDialog"
                >
                  <i class="fa-solid fa-graduation-cap mr-1"></i>历史学习
                </button>
                <button
                  class="px-3 py-2 rounded-md text-xs border border-indigo-200 bg-indigo-50 text-indigo-600 transition-all duration-150 hover:bg-indigo-100 hover:border-indigo-300 disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="!canOpenEvaluation"
                  @click="openEvaluateDrawer"
                >
                  <i class="fa-solid fa-vial-circle-check mr-1"></i>评测
                </button>
                <button
                  :class="[
                    'px-3 py-2 border-none rounded-md text-xs transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
                    (isActiveConfigDirty || isFieldRowsDirty) ? 'bg-[#1677FF] text-white cursor-pointer hover:bg-[#0958D9]' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  ]"
                  :disabled="savingId === activeConfig.id || !activeConfig.name || editingType !== null || (!isActiveConfigDirty && !isFieldRowsDirty) || !canManageConfig(activeConfig)"
                  @click="saveActiveConfig"
                >
                  <template v-if="savingId === activeConfig.id">
                    <i class="fa-solid fa-spinner fa-spin mr-1"></i>保存中
                  </template>
                  <template v-else>
                    <i class="fa-solid fa-save mr-1"></i>保存
                  </template>
                </button>
                <button
                  class="px-3 py-2 rounded-md text-xs border border-red-100 bg-white text-red-500 transition-all duration-150 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white"
                  :disabled="!canManageConfig(activeConfig)"
                  title="删除数据集"
                  @click="handleDeleteConfig(activeConfig.id, activeConfig.name)"
                >
                  <i class="fa-solid fa-trash mr-1"></i>删除
                </button>
              </div>
            </div>
          </div>

          <div class="flex-1 min-h-0 px-4 py-3">
            <div ref="semanticWorkspaceRef" class="h-full min-h-0 grid gap-3" :style="semanticWorkspaceStyle">
              <div class="border border-gray-200 rounded-lg bg-white overflow-hidden flex flex-col">
                <div v-if="sourceTablePanelCollapsed" class="h-full flex flex-col items-center gap-4 py-2">
                  <button
                    type="button"
                    class="h-7 w-7 flex items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 hover:border-blue-200 hover:text-blue-600"
                    title="展开数据源表"
                    @click="toggleSourceTablePanelCollapsed"
                  >
                    <i class="fa-solid fa-chevron-right text-[11px]"></i>
                  </button>
                  <div class="flex flex-col items-center gap-2 text-[11px] text-gray-400">
                    <span class="writing-vertical">数据源表</span>
                    <span class="rounded bg-blue-50 px-1.5 py-0.5 text-blue-500">{{ getScopeEntries(activeConfig.id).length }}</span>
                  </div>
                </div>
                <div v-else class="px-3 py-2 border-b border-gray-100">
                  <div class="flex items-center justify-between gap-2 mb-2">
                    <div class="text-xs font-medium text-gray-600">数据源表（拖拽到右侧）</div>
                    <button
                      type="button"
                      class="h-6 w-6 flex items-center justify-center rounded border border-gray-200 bg-white text-gray-500 hover:border-blue-200 hover:text-blue-600"
                      title="折叠数据源表"
                      @click="toggleSourceTablePanelCollapsed"
                    >
                      <i class="fa-solid fa-chevron-left text-[10px]"></i>
                    </button>
                  </div>
                  <div class="relative">
                    <i class="fa-solid fa-search absolute left-2.5 top-1/2 -translate-y-1/2 text-[11px] text-gray-400"></i>
                    <input
                      v-model="tableSearchKeyword"
                      type="text"
                      placeholder="搜索表名或表备注"
                      class="w-full pl-7 pr-12 py-1.5 border border-gray-200 rounded-md text-[12px] text-gray-600 outline-none focus:border-blue-500"
                    />
                    <button
                      type="button"
                      class="absolute right-1.5 top-1/2 -translate-y-1/2 h-6 rounded border border-gray-200 bg-white px-1.5 text-[11px] font-medium text-gray-500 hover:text-blue-600 hover:border-blue-200 transition-colors"
                      :title="tableLabelMode === 'name' ? '当前显示：表名，点击切换为备注' : '当前显示：备注，点击切换为表名'"
                      @click="tableLabelMode = tableLabelMode === 'name' ? 'description' : 'name'"
                    >
                      中/英
                    </button>
                  </div>
                </div>
                <div v-if="!sourceTablePanelCollapsed" class="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-1.5">
                  <div
                    v-for="table in filteredSourceTables"
                    :key="`${activeConfig.id}-all-${table.table_name}`"
                    class="w-full text-left px-2.5 py-2 rounded-md border border-gray-200 bg-white text-[12px] text-gray-700 cursor-move transition-all duration-150 hover:border-blue-300 hover:bg-blue-50 disabled:opacity-60 disabled:cursor-not-allowed"
                    :title="getTableHoverTitle(table)"
                    draggable="true"
                    @dragstart="handleTableDragStart(table.table_name)"
                    @dragend="handleTableDragEnd"
                  >
                    <div class="flex items-center justify-between gap-2">
                      <span class="truncate">{{ getTableDisplayLabel(table) }}</span>
                      <span class="flex items-center gap-1">
                        <button
                          v-if="getScopeCountByPhysicalTable(activeConfig.id, table.table_name) > 0"
                          type="button"
                          class="flex h-5 w-5 items-center justify-center rounded border border-gray-200 bg-white text-[10px] text-gray-500 hover:border-blue-200 hover:text-blue-600 disabled:cursor-wait disabled:opacity-60"
                          title="查看1条表数据"
                          :disabled="!!previewingTableName"
                          @click.stop="previewTableData(table.table_name)"
                        >
                          <i :class="['fa-solid', previewingTableName === table.table_name ? 'fa-spinner fa-spin' : 'fa-eye']"></i>
                        </button>
                        <span
                          v-if="getScopeCountByPhysicalTable(activeConfig.id, table.table_name) > 0"
                          class="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-600"
                        >已选 {{ getScopeCountByPhysicalTable(activeConfig.id, table.table_name) }}</span>
                      </span>
                    </div>
                  </div>
                  <div v-if="filteredSourceTables.length === 0" class="px-2 py-5 text-center text-[12px] text-gray-400">
                    未找到匹配的数据表
                  </div>
                </div>
              </div>

              <div
                v-if="!sourceTablePanelCollapsed"
                :class="[
                  'group flex h-full items-center justify-center cursor-col-resize select-none',
                  resizingSourcePanel ? 'is-resizing' : ''
                ]"
                title="拖动调整左右宽度，双击恢复默认"
                @mousedown.prevent="startResizeSourcePanel"
                @dblclick.prevent="resetSourcePanelWidth"
              >
                <div class="h-full w-px rounded-full bg-gray-200 transition-colors group-hover:bg-blue-300 group-[.is-resizing]:bg-blue-400"></div>
              </div>

              <div class="min-w-0 min-h-0 flex flex-col gap-3">
                <div
                  class="border border-dashed border-gray-300 rounded-lg bg-gray-50/60 p-2.5 flex flex-col shrink-0"
                  @dragover.prevent
                  @drop.prevent="handleScopeDrop(activeConfig.id)"
                >
                  <div class="flex items-center justify-between mb-1.5">
                    <div class="text-xs font-medium text-gray-600">模型配置</div>
                    <div class="flex items-center gap-2">
                      <div class="text-[11px] text-gray-400">
                        {{ getScopeEntries(activeConfig.id).length }} 张表 · {{ activeConfig.relationships.length }} 条关系
                      </div>
                      <button
                        class="w-6 h-6 border border-gray-200 rounded bg-white text-gray-400 text-[11px] cursor-pointer hover:text-gray-600 hover:border-gray-300"
                        :title="modelConfigCollapsed ? '展开模型配置' : '折叠模型配置'"
                        @click="modelConfigCollapsed = !modelConfigCollapsed"
                      >
                        <i :class="modelConfigCollapsed ? 'fa-solid fa-chevron-down' : 'fa-solid fa-chevron-up'"></i>
                      </button>
                    </div>
                  </div>
                  <div
                    v-if="!modelConfigCollapsed && getScopeEntries(activeConfig.id).length === 0"
                    class="h-[88px] border border-dashed border-gray-300 rounded-md bg-white flex flex-col items-center justify-center text-gray-400 text-xs"
                  >
                    <i class="fa-solid fa-up-right-and-down-left-from-center mb-2"></i>
                    拖动左侧数据表到这里
                  </div>
                  <div
                    v-if="modelConfigCollapsed"
                    class="h-[44px] border border-dashed border-gray-300 rounded-md bg-white flex items-center px-3 text-xs text-gray-400 cursor-pointer transition-all duration-150 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-500"
                    title="点击展开模型配置"
                    @click="modelConfigCollapsed = false"
                  >
                    模型配置已折叠
                  </div>
                  <div
                    v-if="!modelConfigCollapsed && getScopeEntries(activeConfig.id).length > 0"
                    class="min-h-0 overflow-auto rounded-md border border-gray-200 bg-white p-1.5"
                    :style="modelConfigContentStyle"
                  >
                    <div class="space-y-1.5">
                      <div class="rounded-md border border-gray-200 bg-white px-2 py-1.5 flex items-center justify-between gap-2">
                        <div class="min-w-0">
                          <div class="text-[12px] font-semibold text-gray-800 truncate">{{ getScopeEntryLabel(getScopeEntries(activeConfig.id)[0]) }}</div>
                        </div>
                        <button
                          class="w-5 h-5 border-none rounded bg-gray-100 text-gray-400 text-[10px] cursor-pointer hover:bg-red-50 hover:text-red-500"
                          @click="removeScopeEntry(activeConfig.id, getScopeEntries(activeConfig.id)[0])"
                        >
                          <i class="fa-solid fa-xmark"></i>
                        </button>
                      </div>

                      <div v-if="activeRelationshipCards.length === 0" class="text-[11px] text-gray-400 px-1 py-1.5 border-t border-gray-100">
                        暂无关联，请在“待关联表”中点击“配置关联”
                      </div>
                      <div v-else class="space-y-1.5">
                        <div
                          v-for="item in activeRelationshipCards"
                          :key="`rel-card-${activeConfig.id}-${item.relIndex}`"
                          class="pt-0"
                        >
                          <button
                            class="w-full max-w-full text-left px-2.5 py-1 rounded-md border border-blue-200 bg-blue-50 text-blue-600 text-[11px] font-medium cursor-pointer hover:bg-blue-100"
                            @click="openEditRelationshipEditor(activeConfig.id, item.rel, item.relIndex)"
                          >
                            <span class="flex items-center gap-2 min-w-0">
                              <span class="shrink-0 pr-2 border-r border-blue-200 whitespace-nowrap">{{ formatJoinTypeLabel(item.rel.join_type) }}</span>
                                <span class="block flex-1 min-w-0 overflow-x-auto overflow-y-hidden whitespace-nowrap">
                                  <span class="inline-block whitespace-nowrap pr-1">
                                  {{
                                    getRelationshipConditions(item.rel)
                                      .map(cond => `${getRelationshipLeftAlias(item.rel)}.${cond.left_column} = ${getRelationshipRightAlias(item.rel)}.${cond.right_column}`)
                                      .join(' AND ')
                                  }}
                                  </span>
                                </span>
                              </span>
                            </button>
                          <div class="mt-1 rounded-md border border-gray-200 bg-white px-2 py-1.5 flex items-center justify-between gap-2">
                            <div class="min-w-0">
                              <div class="flex items-baseline gap-2 min-w-0">
                                <span class="text-[12px] font-semibold text-gray-800 truncate">{{ getScopeEntryLabel(item.nextEntry) }}</span>
                                <span class="text-[11px] text-gray-400 truncate">{{ tables.find(t => t.table_name === item.nextEntry.table)?.description || '未填写描述' }}</span>
                              </div>
                            </div>
                            <button
                              class="w-5 h-5 border-none rounded bg-gray-100 text-gray-400 text-[10px] cursor-pointer hover:bg-red-50 hover:text-red-500"
                              @click="removeScopeTable(activeConfig.id, item.nextEntry.alias)"
                            >
                              <i class="fa-solid fa-xmark"></i>
                            </button>
                          </div>
                        </div>
                      </div>

                      <div v-if="activeUnlinkedTables.length > 0" class="pt-1.5 border-t border-gray-100">
                        <div class="flex items-center justify-between mb-1">
                          <div class="text-[11px] text-red-500 font-medium">待关联表</div>
                          <div class="text-[11px] text-red-400">未关联 {{ activeUnlinkedTables.length }} 张</div>
                        </div>
                        <div class="mb-1 text-[11px] text-red-400">存在游离表，请先配置关联后再保存</div>
                        <div class="space-y-1">
                          <div
                            v-for="entry in activeUnlinkedTables"
                            :key="`unlinked-${activeConfig.id}-${entry.alias}`"
                            class="rounded-md border border-dashed border-red-200 bg-red-50/70 px-2 py-1.5 flex items-center justify-between gap-2"
                          >
                            <div class="min-w-0">
                              <div class="flex items-baseline gap-2 min-w-0">
                                <span class="text-[12px] font-semibold text-gray-800 truncate">{{ getScopeEntryLabel(entry) }}</span>
                                <span class="text-[11px] text-gray-400 truncate">{{ tables.find(t => t.table_name === entry.table)?.description || '未填写描述' }}</span>
                              </div>
                              <div class="text-[11px] text-red-400 truncate">未配置关联条件</div>
                            </div>
                            <div class="flex items-center gap-1">
                              <button
                                class="px-2 py-1 rounded border border-red-200 bg-white text-red-500 text-[11px] cursor-pointer hover:bg-red-50"
                                @click="openLinkTableEditor(activeConfig.id, entry.alias)"
                              >
                                配置关联
                              </button>
                              <button
                                class="w-5 h-5 border-none rounded bg-gray-100 text-gray-400 text-[10px] cursor-pointer hover:bg-red-50 hover:text-red-500"
                                @click="removeScopeTable(activeConfig.id, entry.alias)"
                              >
                                <i class="fa-solid fa-xmark"></i>
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <div class="border border-dashed border-gray-300 rounded-lg bg-gray-50/60 p-3 flex flex-col flex-1 min-h-0">
                  <div class="flex items-center justify-between mb-2">
                    <div class="text-xs font-medium text-gray-600">字段配置</div>
                    <div class="flex items-center gap-2">
                      <button
                        class="h-7 px-2.5 border border-gray-200 rounded bg-white text-[11px] text-gray-600 hover:border-blue-200 hover:text-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
                        :disabled="autoPrefillLoading || fieldRows.length === 0"
                        @click="openAutoPrefillModeDialog"
                      >
                        <i v-if="autoPrefillLoading" class="fa-solid fa-spinner fa-spin mr-1"></i>
                        <i v-else class="fa-solid fa-wand-magic-sparkles mr-1"></i>
                        自动预填
                      </button>
                      <button
                        :class="[
                          'h-7 px-2.5 rounded border bg-white text-[11px] transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
                          activeConfig.ontology_rules ? 'border-emerald-200 text-emerald-600 bg-emerald-50 hover:bg-emerald-100' : 'border-gray-200 text-gray-600 hover:border-blue-200 hover:text-blue-600'
                        ]"
                        @click="openOntologyRulesDialog"
                      >
                        <i class="fa-regular fa-note-sticky mr-1"></i>业务说明
                      </button>
                      <button
                        class="h-7 px-2.5 border border-gray-200 rounded bg-white text-[11px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                        @click="openAddFieldDialog"
                      >
                        <i class="fa-solid fa-plus mr-1"></i>新增字段
                      </button>
                    </div>
                  </div>
                  <div v-if="fieldRows.length === 0" class="flex-1 border border-dashed border-gray-300 rounded-md bg-white flex flex-col items-center justify-center text-gray-400 text-xs">
                    <i class="fa-regular fa-table-cells-large mb-2"></i>
                    请先在左侧选择数据表
                  </div>
                  <div v-else class="flex-1 overflow-auto rounded-md border border-gray-200 bg-white">
                    <table class="min-w-[1820px] w-full border-collapse text-[12px]">
                      <thead class="bg-gray-50 text-gray-600">
                        <tr>
                          <th class="text-left px-3 py-2 border border-gray-200">表</th>
                          <th class="text-left px-3 py-2 border border-gray-200">字段</th>
                          <th class="text-left px-3 py-2 border border-gray-200">类型</th>
                          <th class="text-left px-3 py-2 border border-gray-200">列备注</th>
                          <th class="text-center px-3 py-2 border border-gray-200">维度</th>
                          <th class="text-center px-3 py-2 border border-gray-200">指标</th>
                          <th class="text-left px-3 py-2 border border-gray-200">表达式</th>
                          <th class="text-left px-3 py-2 border border-gray-200">显示名称</th>
                          <th class="text-left px-3 py-2 border border-gray-200">字段备注</th>
                          <th class="text-center px-3 py-2 border border-gray-200 w-[96px]">字段字典</th>
                          <th class="text-center px-3 py-2 border border-gray-200 w-[72px]">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="row in fieldRows" :key="row.key" :class="['hover:bg-gray-50', row.isMissingPhysical ? 'bg-red-50/40' : '']">
                          <td class="px-3 py-2 border border-gray-200 text-gray-700">
                            <div class="leading-tight">
                              <div>{{ getFieldRowAlias(row) }}</div>
                              <div v-if="getFieldRowAlias(row) !== row.table" class="text-[10px] text-gray-400">{{ row.table }}</div>
                            </div>
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-gray-700" :title="row.isCustom ? `物理字段：${row.sourceColumn || row.column}` : row.isMissingPhysical ? `物理字段不存在：${row.sourceColumn || row.column}` : ''">
                            <div class="flex items-center gap-1.5">
                              <span>{{ row.column }}</span>
                              <span v-if="row.isMissingPhysical" class="rounded bg-red-100 px-1.5 py-0.5 text-[10px] text-red-600">字段已删除</span>
                            </div>
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-gray-500">{{ row.columnType || '-' }}</td>
                          <td class="px-3 py-2 border border-gray-200 text-gray-500">
                            <div class="max-w-[220px] truncate" :title="row.columnComment || '-'">{{ row.columnComment || '-' }}</div>
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <input :checked="row.isDimension" :disabled="row.isMissingPhysical" type="checkbox" @change="handleDimensionToggle(row, ($event.target as HTMLInputElement).checked)" />
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <input :checked="row.isMetric" :disabled="row.isMissingPhysical" type="checkbox" @change="handleMetricToggle(row, ($event.target as HTMLInputElement).checked)" />
                          </td>
                          <td class="px-3 py-2 border border-gray-200">
                            <div class="flex items-center gap-1">
                              <input
                                :value="row.expression"
                                type="text"
                                class="flex-1 px-2 py-1 border border-gray-200 rounded text-[12px] font-mono disabled:bg-gray-50 disabled:text-gray-400"
                                :disabled="row.isMissingPhysical || (!row.isMetric && !row.isDimension)"
                                @input="handleExpressionInput(row, ($event.target as HTMLInputElement).value)"
                              />
                              <button
                                class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-500 hover:text-blue-500 hover:border-blue-300 disabled:bg-gray-50 disabled:text-gray-300 disabled:cursor-not-allowed"
                                :disabled="row.isMissingPhysical || (!row.isMetric && !row.isDimension)"
                                title="弹窗编辑表达式"
                                @click="openExpressionEditorDialog(row)"
                              >
                                <i class="fa-solid fa-pen"></i>
                              </button>
                              <button
                                v-if="canExpandMetricRow(row)"
                                class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-500 hover:text-blue-500 hover:border-blue-300 disabled:bg-gray-50 disabled:text-gray-300 disabled:cursor-not-allowed"
                                title="扩展聚合指标"
                                @click="openMetricExpandDialog(row)"
                              >
                                <i class="fa-solid fa-layer-group"></i>
                              </button>
                            </div>
                          </td>
                          <td class="px-3 py-2 border border-gray-200">
                            <input
                              v-model="row.displayName"
                              type="text"
                              class="w-full px-2 py-1 border border-gray-200 rounded text-[12px]"
                              placeholder="默认字段名"
                            />
                          </td>
                          <td class="px-3 py-2 border border-gray-200">
                            <input
                              v-model="row.description"
                              type="text"
                              class="w-full px-2 py-1 border border-gray-200 rounded text-[12px]"
                              placeholder="补充业务含义"
                            />
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <button
                              v-if="hasFieldColumnDict(row)"
                              class="h-7 px-2 border border-gray-200 rounded bg-white text-[11px] text-gray-600 hover:border-blue-200 hover:text-blue-600 disabled:bg-gray-50 disabled:text-gray-300 disabled:cursor-not-allowed"
                              :disabled="row.isMissingPhysical || !row.table || !getFieldPhysicalColumn(row)"
                              :title="row.isMissingPhysical ? '物理字段不存在' : `查看 ${row.table}.${getFieldPhysicalColumn(row)} 的字段字典`"
                              @click="viewColumnDict(row)"
                            >
                              查看
                            </button>
                            <span v-else class="text-gray-300">-</span>
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <button
                              class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-400 hover:text-red-500 hover:border-red-200"
                              @click="removeFieldRow(row)"
                              title="删除字段配置"
                            >
                              <i class="fa-solid fa-trash text-[11px]"></i>
                            </button>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>

    <el-drawer v-model="showEvaluateDrawer" class="chatdata-evaluation-drawer" direction="ltr" size="82%" :with-header="false" append-to-body :z-index="4200">
      <div class="h-full min-h-0 bg-[#f5f7fb] flex flex-col">
        <div class="h-12 px-4 border-b border-gray-200 bg-white flex items-center justify-between shrink-0">
          <div class="min-w-0">
            <div class="text-sm font-semibold text-gray-800">智能问数评测 - {{ activeConfig?.name || '当前指标集' }}</div>
          </div>
          <div class="flex items-center gap-2">
            <button class="w-8 h-8 cursor-pointer border border-gray-200 rounded-md bg-white text-gray-500 hover:border-gray-300 hover:bg-gray-50 hover:text-gray-800" @click="showEvaluateDrawer = false">
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
        </div>

        <div class="px-4 pt-3 bg-white border-b border-gray-200 shrink-0">
          <el-segmented
            v-model="evaluateDrawerTab"
            :options="[
              { label: '1. 测试集', value: 'cases' },
              { label: '2. 评测', value: 'run' },
              { label: '3. 历史', value: 'history' },
            ]"
          />
          <div class="py-2 text-xs text-gray-500">
            每个指标集需先维护专属测试集：根据当前指标/维度生成草稿 → 校对并保存 → 再跑接口链路评测。
            <span v-if="evaluationCasesDirty" class="text-amber-600 ml-1">（有未保存修改）</span>
          </div>
        </div>

        <div class="flex-1 min-h-0 p-4 overflow-auto">
          <div v-if="evaluateDrawerTab === 'cases'" class="bg-white border border-gray-200 rounded-lg overflow-hidden">
            <div class="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center justify-between gap-2">
              <div class="flex items-center gap-4 text-xs text-gray-600">
                <span>共 {{ evaluationCaseStats.total }} 条</span>
                <span>人工 {{ evaluationCaseStats.human }}</span>
                <span>LLM {{ evaluationCaseStats.llm }}</span>
                <span v-if="evaluationBusinessDomain">业务域 {{ evaluationBusinessDomain }}</span>
              </div>
              <div class="flex items-center gap-2">
                <button
                  class="px-3 py-1.5 cursor-pointer rounded-md border border-gray-200 bg-white text-xs text-gray-700 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-60"
                  :disabled="evaluationCasesLoading"
                  @click="loadEvaluationCases"
                >
                  刷新
                </button>
                <button
                  class="px-3 py-1.5 cursor-pointer rounded-md border border-[#1677FF] bg-[#E6F4FF] text-xs text-[#1677FF] hover:bg-[#D8ECFF] disabled:cursor-not-allowed disabled:opacity-60"
                  :disabled="evaluationCasesLoading"
                  @click="generateEvaluationCasesDraft"
                >
                  <i :class="evaluationCasesLoading ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-wand-magic-sparkles mr-1'"></i>生成测试集草稿
                </button>
                <button
                  class="px-3 py-1.5 cursor-pointer rounded-md border-none bg-[#1677FF] text-xs text-white hover:bg-[#0958D9] disabled:cursor-not-allowed disabled:opacity-60"
                  :disabled="evaluationCasesSaving || evaluationCases.length === 0 || !evaluationCasesDirty"
                  :title="evaluationCasesDirty ? '保存测试集修改' : '测试集没有未保存修改'"
                  @click="saveEvaluationCases"
                >
                  <i :class="evaluationCasesSaving ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-floppy-disk mr-1'"></i>保存测试集
                </button>
                <button
                  class="px-3 py-1.5 cursor-pointer rounded-md border border-gray-200 bg-white text-xs text-gray-700 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-600"
                  @click="openNewCaseEditor"
                >
                  新增用例
                </button>
              </div>
            </div>
            <el-table v-loading="evaluationCasesLoading" :data="evaluationCases" height="620" size="small" border>
              <el-table-column prop="id" label="用例 ID" min-width="220" show-overflow-tooltip />
              <el-table-column prop="nl" label="自然语言问题" min-width="240" show-overflow-tooltip />
              <el-table-column prop="difficulty" label="难度" width="80" />
              <el-table-column label="来源" width="80">
                <template #default="{ row }">{{ row.source === 'llm' ? 'LLM' : row.source === 'human' ? '人工' : row.source }}</template>
              </el-table-column>
              <el-table-column label="标准 SQL" width="90">
                <template #default="{ row }">{{ row.gold_sql ? '已生成' : '待生成' }}</template>
              </el-table-column>
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ $index }">
                  <el-button type="primary" link size="small" @click.stop="openEditCaseEditor($index)">编辑</el-button>
                  <el-button type="danger" link size="small" @click.stop="removeEvaluationCase($index)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div v-if="!evaluationCasesLoading && evaluationCases.length === 0" class="py-16 text-center text-sm text-gray-400">
              当前指标集还没有测试集。请点击「生成测试集草稿」，系统会按本指标集的指标、维度和表关系自动生成评测用例并生成标准 SQL。
            </div>
          </div>

          <div v-else-if="evaluateDrawerTab === 'run'" class="flex flex-col gap-4 min-h-full">
            <div class="flex flex-wrap items-center justify-between gap-3 text-xs text-gray-600">
              <div class="flex flex-wrap items-center gap-3">
                <span>超时</span>
                <el-input-number v-model="evaluationForm.request_timeout" :min="10" :max="600" size="small" style="width:100px" />
                <span>秒</span>
                <el-checkbox v-model="evaluationForm.skip_execution" size="small">跳过 SQL 执行</el-checkbox>
                <el-checkbox v-model="evaluationForm.include_traces" size="small">工具轨迹</el-checkbox>
                <span class="text-gray-400 ml-1">保存工具调用明细用于排查；评测始终按用例流式更新</span>
              </div>
              <div class="flex items-center gap-2">
                <button
                  v-if="evaluationRunning"
                  class="px-3 py-1.5 rounded-md border border-red-200 bg-white text-xs text-red-600 hover:bg-red-50"
                  @click="stopEvaluationRun"
                >
                  <i class="fa-solid fa-stop mr-1"></i>停止评测
                </button>
                <button
                  v-else
                  class="px-3 py-1.5 rounded-md border-none bg-[#1677FF] text-xs text-white disabled:opacity-60"
                  :disabled="evaluationSelectedCount === 0"
                  :title="evaluationSelectedCount === 0 ? '请勾选要运行的用例' : '运行选中的评测用例'"
                  @click="runActiveEvaluation"
                >
                  <i class="fa-solid fa-play mr-1"></i>运行选中 ({{ evaluationSelectedCount }})
                </button>
              </div>
            </div>

            <div v-if="evaluationActiveSummary || evaluationRunning" class="grid grid-cols-6 gap-3">
              <div
                v-for="card in evaluationSummaryCards"
                :key="card.label"
                class="bg-white border border-gray-200 rounded-lg px-3 py-3"
                :class="card.tone === 'success' ? 'border-emerald-200' : card.tone === 'danger' ? 'border-red-200' : card.tone === 'primary' ? 'border-blue-200' : ''"
              >
                <div class="text-xs text-gray-500">
                  <span class="evaluation-metric-header">
                    {{ card.label }}
                    <el-tooltip v-if="evaluationMetricTipByLabel[card.label]" :content="evaluationMetricTipByLabel[card.label]" placement="top" popper-class="chatdata-evaluation-tooltip">
                      <i class="fa-regular fa-circle-question"></i>
                    </el-tooltip>
                  </span>
                </div>
                <div
                  class="mt-1 text-lg font-semibold"
                  :class="card.tone === 'success' ? 'text-emerald-700' : card.tone === 'danger' ? 'text-red-600' : card.tone === 'primary' ? 'text-[#1677FF]' : 'text-gray-800'"
                >
                  {{ card.value }}
                </div>
              </div>
            </div>

            <div class="grid grid-cols-[minmax(560px,1.05fr)_minmax(440px,0.95fr)] gap-4 min-h-[620px]">
              <div class="bg-white border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
                <div class="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center justify-between gap-3 shrink-0">
                  <div class="flex items-center gap-3">
                    <span class="text-xs text-gray-500">已选 {{ evaluationSelectedCount }} / {{ evaluationRunRows.length }} 条</span>
                    <el-segmented
                      v-model="evaluationResultFilter"
                      size="small"
                      :options="[
                        { label: '全部', value: 'all' },
                        { label: '通过', value: 'passed' },
                        { label: '未通过', value: 'failed' },
                      ]"
                    />
                  </div>
                  <div class="flex items-center gap-3 text-xs text-gray-500">
                    <span v-if="evaluationRunning || evaluationRunProgress.total > 0">
                      进度 {{ evaluationRunProgress.current }} / {{ evaluationRunProgress.total }}
                    </span>
                    <span v-if="evaluationRunning" class="text-[#1677FF]">
                      <i class="fa-solid fa-spinner fa-spin mr-1"></i>流式评测中…
                    </span>
                  </div>
                </div>

                <el-table
                  :data="evaluationRunRowsFiltered"
                  height="560"
                  size="small"
                  border
                  highlight-current-row
                  row-key="id"
                  :current-row-key="selectedEvaluationResultId"
                  @row-click="(row: EvaluationCaseRunRow) => openEvaluationRunDetail(row)"
                >
                  <el-table-column width="44" align="center">
                    <template #header>
                      <el-checkbox v-model="evaluationAllSelected" :disabled="evaluationRunning" />
                    </template>
                    <template #default="{ row }">
                      <el-checkbox v-model="row.selected" :disabled="evaluationRunning" @click.stop />
                    </template>
                  </el-table-column>
                  <el-table-column label="状态" width="92" align="center">
                    <template #default="{ row }">
                      <span class="text-[11px] px-2 py-0.5 rounded inline-flex items-center gap-1" :class="evaluationRunStatusClass(row.runStatus)">
                        <i v-if="row.runStatus === 'running'" class="fa-solid fa-spinner fa-spin"></i>
                        {{ evaluationRunStatusLabel(row.runStatus) }}
                      </span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="id" label="用例 ID" min-width="220" show-overflow-tooltip />
                  <el-table-column prop="nl" label="自然语言问题" min-width="180" show-overflow-tooltip />
                  <el-table-column width="104" align="center">
                    <template #header>
                      <span class="evaluation-metric-header">
                        语义得分
                        <el-tooltip :content="evaluationMetricTips.semantic" placement="top" popper-class="chatdata-evaluation-tooltip">
                          <i class="fa-regular fa-circle-question"></i>
                        </el-tooltip>
                      </span>
                    </template>
                    <template #default="{ row }">
                      {{ row.runResult ? formatEvaluationPercent(row.runResult.semantic_accuracy) : '—' }}
                    </template>
                  </el-table-column>
                  <el-table-column width="104" align="center">
                    <template #header>
                      <span class="evaluation-metric-header">
                        结构匹配
                        <el-tooltip :content="evaluationMetricTips.structure" placement="top" popper-class="chatdata-evaluation-tooltip">
                          <i class="fa-regular fa-circle-question"></i>
                        </el-tooltip>
                      </span>
                    </template>
                    <template #default="{ row }">
                      {{ row.runResult ? formatEvaluationBooleanRate(row.runResult.dsl_exact_match) : '—' }}
                    </template>
                  </el-table-column>
                  <el-table-column width="96" align="center">
                    <template #header>
                      <span class="evaluation-metric-header">
                        SQL匹配
                        <el-tooltip :content="evaluationMetricTips.sql" placement="top" popper-class="chatdata-evaluation-tooltip">
                          <i class="fa-regular fa-circle-question"></i>
                        </el-tooltip>
                      </span>
                    </template>
                    <template #default="{ row }">
                      {{ row.runResult ? formatEvaluationBooleanRate(row.runResult.sql_exact_match) : '—' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="耗时" width="74" align="center">
                    <template #default="{ row }">
                      {{ row.runResult?.elapsed_ms != null ? `${(row.runResult.elapsed_ms / 1000).toFixed(1)}秒` : '—' }}
                    </template>
                  </el-table-column>
                </el-table>
              </div>

              <div class="bg-white border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0">
                <div class="px-4 py-3 border-b border-gray-100 shrink-0">
                  <div class="text-sm font-semibold text-gray-800 truncate">
                    {{ selectedEvaluationResult?.question || selectedEvaluationResultId || '评测详情' }}
                  </div>
                  <div v-if="selectedEvaluationResult" class="mt-2 flex flex-wrap gap-2">
                    <span
                      v-for="slot in evaluationSlotScoreItems(selectedEvaluationResult)"
                      :key="slot.key"
                      class="text-[11px] px-2 py-0.5 rounded"
                      :class="slot.score >= 1 ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'"
                      :title="slot.score >= 1 ? '通过' : '未通过'"
                    >
                      {{ slot.key }}
                    </span>
                  </div>
                </div>

                <div v-if="selectedEvaluationResult" class="flex-1 min-h-0 overflow-auto bg-[#fafbfc] p-4">
                  <div class="grid grid-cols-2 gap-3">
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">标准语义结构</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationResult.expected_dsl || selectedEvaluationResult.gold_dsl) }}</pre>
                    </div>
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">模型生成语义结构</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationResult.predicted_dsl) }}</pre>
                    </div>
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">标准 SQL</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationResult.gold_sql || '—' }}</pre>
                    </div>
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">生成 SQL</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationResult.generated_sql || selectedEvaluationResult.agent_tool_sql || '—' }}</pre>
                    </div>
                  </div>
                  <div v-if="selectedEvaluationResult.error" class="mt-3 rounded border border-red-100 bg-red-50 p-2 text-xs text-red-600 whitespace-pre-wrap">
                    {{ selectedEvaluationResult.error }}
                  </div>
                  <div v-if="evaluationToolTraceItems(selectedEvaluationResult).length" class="mt-3">
                    <el-collapse>
                      <el-collapse-item :title="`工具轨迹（${evaluationToolTraceItems(selectedEvaluationResult).length}）`" name="tool-traces">
                        <div class="space-y-2">
                          <div
                            v-for="(trace, traceIndex) in evaluationToolTraceItems(selectedEvaluationResult)"
                            :key="traceIndex"
                            class="rounded border border-gray-200 bg-white p-2"
                          >
                            <div class="mb-1 flex items-center gap-2 text-xs">
                              <span class="font-semibold text-gray-700">{{ trace.name || `tool_${traceIndex + 1}` }}</span>
                              <span class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500">{{ trace.status || '-' }}</span>
                            </div>
                            <div class="grid grid-cols-2 gap-2">
                              <pre class="max-h-28 overflow-auto rounded bg-gray-50 p-2 text-[11px]">{{ formatJsonPreview(trace.args) }}</pre>
                              <pre class="max-h-28 overflow-auto rounded bg-gray-50 p-2 text-[11px] whitespace-pre-wrap">{{ trace.content || '—' }}</pre>
                            </div>
                          </div>
                        </div>
                      </el-collapse-item>
                    </el-collapse>
                  </div>
                </div>
                <div v-else class="flex-1 flex items-center justify-center bg-[#fafbfc] px-6 text-center text-xs text-gray-400">
                  勾选用例并点击「运行选中」，完成后点击左侧用例查看 DSL/SQL 对比详情
                </div>
              </div>
            </div>
          </div>

          <div v-else class="flex flex-col gap-4 min-h-full">
            <div class="border border-gray-200 rounded-lg overflow-hidden flex flex-col bg-white shrink-0">
              <div class="px-3 py-2 border-b border-gray-100 flex items-center justify-between shrink-0">
                <span class="text-sm font-semibold text-gray-700">运行批次</span>
                <button class="px-2.5 py-1 rounded border border-gray-200 bg-white text-xs text-gray-600" :disabled="evaluationHistoryLoading" @click="loadEvaluationHistoryRuns">
                  <i :class="evaluationHistoryLoading ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-rotate-right mr-1'"></i>刷新
                </button>
              </div>
              <el-table
                v-loading="evaluationHistoryLoading"
                :data="evaluationHistoryRuns"
                height="220"
                size="small"
                border
                highlight-current-row
                row-key="run_id"
                :current-row-key="selectedEvaluationHistoryRunId"
                @row-click="(row: AccuracyEvaluationRunHistoryItem) => loadEvaluationHistoryDetail(row.run_id)"
              >
                <el-table-column label="时间" min-width="160">
                  <template #default="{ row }">{{ formatEvaluationTime(row.started_time || row.create_time) }}</template>
                </el-table-column>
                <el-table-column label="测试人员" min-width="120" show-overflow-tooltip>
                  <template #default="{ row }">{{ evaluationHistoryTesterName(row) }}</template>
                </el-table-column>
                <el-table-column label="状态" width="110" align="center">
                  <template #default="{ row }">{{ row.run_status }}</template>
                </el-table-column>
                <el-table-column label="语义得分" width="110" align="center">
                  <template #default="{ row }">{{ formatEvaluationPercent(row.semantic_accuracy) }}</template>
                </el-table-column>
                <el-table-column label="通过" width="90" align="center">
                  <template #default="{ row }">{{ row.passed_count }}</template>
                </el-table-column>
                <el-table-column label="未通过" width="90" align="center">
                  <template #default="{ row }">{{ row.failed_count }}</template>
                </el-table-column>
                <el-table-column label="用例数" width="90" align="center">
                  <template #default="{ row }">{{ row.scored_count || row.total_count }}</template>
                </el-table-column>
                <el-table-column prop="run_id" label="运行 ID" min-width="260" show-overflow-tooltip />
              </el-table>
              <div class="px-3 py-2 border-t border-gray-100 shrink-0">
                <el-pagination
                  size="small"
                  layout="prev, pager, next"
                  :current-page="evaluationHistoryPage"
                  :page-size="evaluationHistoryPageSize"
                  :total="evaluationHistoryTotal"
                  @current-change="handleEvaluationHistoryPageChange"
                />
              </div>
            </div>

            <div class="grid grid-cols-[minmax(420px,0.8fr)_minmax(620px,1.2fr)] gap-4 min-h-[440px] flex-1">
              <div class="border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0 bg-white">
                <div class="px-3 py-2 border-b border-gray-100 text-sm font-semibold text-gray-700 shrink-0">
                  用例结果
                  <span v-if="selectedEvaluationHistoryRun" class="ml-2 text-xs font-normal text-gray-400">
                    {{ selectedEvaluationHistoryRun.passed_count }} 通过 / {{ selectedEvaluationHistoryRun.failed_count }} 未通过
                  </span>
                </div>
                <el-table
                  v-loading="evaluationHistoryDetailLoading"
                  :data="evaluationHistoryCases"
                  height="390"
                  size="small"
                  border
                  highlight-current-row
                  row-key="case_id"
                  :current-row-key="selectedEvaluationHistoryCaseId"
                  @row-click="(row: AccuracyEvaluationRunCaseHistoryItem) => selectedEvaluationHistoryCaseId = row.case_id"
                >
                  <el-table-column label="状态" width="76" align="center">
                    <template #default="{ row }">{{ row.case_status === 'passed' ? '通过' : '未通过' }}</template>
                  </el-table-column>
                  <el-table-column prop="case_id" label="用例 ID" min-width="220" show-overflow-tooltip />
                  <el-table-column width="104" align="center">
                    <template #header>
                      <span class="evaluation-metric-header">
                        语义得分
                        <el-tooltip :content="evaluationMetricTips.semantic" placement="top" popper-class="chatdata-evaluation-tooltip">
                          <i class="fa-regular fa-circle-question"></i>
                        </el-tooltip>
                      </span>
                    </template>
                    <template #default="{ row }">{{ formatEvaluationPercent(row.semantic_accuracy) }}</template>
                  </el-table-column>
                  <el-table-column width="104" align="center">
                    <template #header>
                      <span class="evaluation-metric-header">
                        结构匹配
                        <el-tooltip :content="evaluationMetricTips.structure" placement="top" popper-class="chatdata-evaluation-tooltip">
                          <i class="fa-regular fa-circle-question"></i>
                        </el-tooltip>
                      </span>
                    </template>
                    <template #default="{ row }">{{ formatEvaluationBooleanRate(evaluationHistoryCaseResult(row).dsl_exact_match) }}</template>
                  </el-table-column>
                  <el-table-column width="96" align="center">
                    <template #header>
                      <span class="evaluation-metric-header">
                        SQL匹配
                        <el-tooltip :content="evaluationMetricTips.sql" placement="top" popper-class="chatdata-evaluation-tooltip">
                          <i class="fa-regular fa-circle-question"></i>
                        </el-tooltip>
                      </span>
                    </template>
                    <template #default="{ row }">{{ formatEvaluationBooleanRate(evaluationHistoryCaseResult(row).sql_exact_match) }}</template>
                  </el-table-column>
                  <el-table-column label="耗时" width="76" align="center">
                    <template #default="{ row }">{{ row.elapsed_ms != null ? `${(row.elapsed_ms / 1000).toFixed(1)}秒` : '—' }}</template>
                  </el-table-column>
                </el-table>
              </div>

              <div class="border border-gray-200 rounded-lg overflow-hidden flex flex-col min-h-0 bg-white">
                <div class="px-4 py-3 border-b border-gray-100 shrink-0">
                  <div class="text-sm font-semibold text-gray-800 truncate">
                    {{ selectedEvaluationHistoryCase?.question || selectedEvaluationHistoryCaseId || '评测详情' }}
                  </div>
                  <div v-if="selectedEvaluationHistoryCase" class="mt-2 flex flex-wrap gap-2">
                    <span
                      v-for="slot in evaluationSlotScoreItems(selectedEvaluationHistoryCase)"
                    :key="slot.key"
                    class="text-[11px] px-2 py-0.5 rounded"
                    :class="slot.score >= 1 ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'"
                    :title="slot.score >= 1 ? '通过' : '未通过'"
                  >
                    {{ slot.key }}
                  </span>
                  </div>
                </div>
                <div v-if="selectedEvaluationHistoryCase" class="flex-1 min-h-0 overflow-auto bg-[#fafbfc] p-4">
                  <div class="grid grid-cols-2 gap-3">
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">标准语义结构</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationHistoryCase.expected_dsl || selectedEvaluationHistoryCase.gold_dsl) }}</pre>
                    </div>
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">模型生成语义结构</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-44">{{ formatJsonPreview(selectedEvaluationHistoryCase.predicted_dsl) }}</pre>
                    </div>
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">标准 SQL</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationHistoryCase.gold_sql || '—' }}</pre>
                    </div>
                    <div>
                      <div class="text-xs font-medium text-gray-600 mb-1">生成 SQL</div>
                      <pre class="text-[11px] bg-white border border-gray-200 rounded p-2 overflow-auto h-36 whitespace-pre-wrap">{{ selectedEvaluationHistoryCase.generated_sql || selectedEvaluationHistoryCase.agent_tool_sql || '—' }}</pre>
                    </div>
                  </div>
                  <div v-if="selectedEvaluationHistoryCase.error" class="mt-3 rounded border border-red-100 bg-red-50 p-2 text-xs text-red-600 whitespace-pre-wrap">
                    {{ selectedEvaluationHistoryCase.error }}
                  </div>
                </div>
                <div v-else class="flex-1 flex items-center justify-center bg-[#fafbfc] px-6 text-center text-xs text-gray-400">
                  选择左侧评测批次和用例查看详情
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>

    <el-dialog v-model="showCaseEditorDialog" width="720px" title="编辑评测用例" append-to-body :z-index="4300">
      <el-form label-position="top" size="small">
        <div class="grid grid-cols-2 gap-3">
          <el-form-item label="用例 ID">
            <el-input v-model="editingCase.id" />
          </el-form-item>
          <el-form-item label="业务域">
            <el-input v-model="editingCase.business_domain" disabled />
          </el-form-item>
        </div>
        <el-form-item label="自然语言问题">
          <div class="w-full">
            <el-input v-model="editingCase.nl" type="textarea" :rows="3" placeholder="请输入自然语言问题，也可以直接粘贴 SQL 生成标准语义结构" />
          </div>
        </el-form-item>
        <div class="grid grid-cols-2 gap-3">
          <el-form-item label="难度">
            <el-select v-model="editingCase.difficulty" class="w-full">
              <el-option label="easy" value="easy" />
              <el-option label="medium" value="medium" />
              <el-option label="hard" value="hard" />
            </el-select>
          </el-form-item>
          <el-form-item label="来源">
            <el-select v-model="editingCase.source" class="w-full">
              <el-option label="人工" value="human" />
              <el-option label="LLM" value="llm" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item>
          <template #label>
            <div class="flex items-center gap-2">
              <span>标准语义结构（JSON）</span>
              <button
                type="button"
                class="h-7 px-2.5 rounded border border-[#1677FF] bg-[#E6F4FF] text-xs text-[#1677FF] disabled:opacity-60"
                :disabled="draftingCase"
                @click="draftEditingCaseByLlm"
              >
                <i :class="draftingCase ? 'fa-solid fa-spinner fa-spin mr-1' : 'fa-solid fa-wand-magic-sparkles mr-1'"></i>LLM 生成语义结构
              </button>
            </div>
          </template>
          <el-input v-model="editingCaseDslText" type="textarea" :rows="10" class="font-mono text-xs" />
          <div v-if="editingCaseDslError" class="mt-1 text-xs text-red-500">
            {{ editingCaseDslError }}
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <button class="px-3 py-1.5 border border-gray-200 rounded bg-white text-xs text-gray-600" @click="showCaseEditorDialog = false">取消</button>
        <button class="ml-2 px-3 py-1.5 border-none rounded bg-[#1677FF] text-xs text-white" @click="saveCaseEditor">确定</button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRelationshipEditor" width="820px" title="连接方式" @close="closeRelationshipEditor">
      <div v-if="editingRelationship" class="overflow-x-auto">
        <div class="min-w-[680px] space-y-4">
        <div class="flex items-center gap-2">
          <button
            :class="[
              'px-3 py-1.5 rounded border text-xs flex items-center gap-1.5',
              editingRelationship.join_type === 'inner' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 text-gray-600'
            ]"
            @click="editingRelationship.join_type = 'inner'"
          >
            <span>内连接</span>
            <span class="text-[10px] opacity-70">INNER</span>
          </button>
          <button
            :class="[
              'px-3 py-1.5 rounded border text-xs flex items-center gap-1.5',
              editingRelationship.join_type === 'left' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 text-gray-600'
            ]"
            @click="editingRelationship.join_type = 'left'"
          >
            <span>左连接</span>
            <span class="text-[10px] opacity-70">LEFT</span>
          </button>
          <button
            :class="[
              'px-3 py-1.5 rounded border text-xs flex items-center gap-1.5',
              editingRelationship.join_type === 'right' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-gray-200 text-gray-600'
            ]"
            @click="editingRelationship.join_type = 'right'"
          >
            <span>右连接</span>
            <span class="text-[10px] opacity-70">RIGHT</span>
          </button>
        </div>
        <div class="grid grid-cols-[1fr_auto_1fr] gap-2 items-center">
          <select
            v-model="editingRelationship.left_alias"
            class="px-2.5 py-2 border border-gray-200 rounded text-sm"
            @change="handleRelationshipLeftAliasChange"
          >
            <option v-for="entry in getScopeEntries(editingConfigId || activeConfig?.id || '')" :key="entry.alias" :value="entry.alias">{{ getScopeEntryLabel(entry) }}</option>
          </select>
          <div class="text-sm text-gray-400">→</div>
          <select
            v-model="editingRelationship.right_alias"
            class="px-2.5 py-2 border border-gray-200 rounded text-sm"
            @change="handleRelationshipRightAliasChange"
          >
            <option v-for="entry in getScopeEntries(editingConfigId || activeConfig?.id || '')" :key="entry.alias" :value="entry.alias">{{ getScopeEntryLabel(entry) }}</option>
          </select>
        </div>
        <div class="rounded-md border border-gray-200 bg-gray-50/50 p-3">
          <div class="mb-2 flex items-center justify-between">
            <div class="text-xs font-medium text-gray-600">关联字段</div>
            <button
              class="px-2 py-1 border border-gray-200 rounded bg-white text-gray-600 text-xs hover:border-blue-200 hover:text-blue-600"
              @click="addJoinCondition"
            >
              <i class="fa-solid fa-plus mr-1"></i>新增条件
            </button>
          </div>
          <div
            v-for="(cond, condIndex) in (editingRelationship.join_conditions || [])"
            :key="`join-cond-${condIndex}`"
            class="space-y-1.5"
          >
            <div v-if="condIndex > 0" class="text-[11px] font-semibold text-blue-500">AND</div>
            <div class="grid grid-cols-[1fr_auto_1fr_auto] gap-2 items-center">
              <select v-model="cond.left_column" class="px-2.5 py-2 border border-gray-200 rounded text-sm bg-white">
                <option value="">请选择左表字段</option>
                <option v-for="col in getColumnsByScopeAlias(editingConfigId || activeConfig?.id || '', editingRelationship.left_alias)" :key="col.name" :value="col.name">{{ col.name }}</option>
              </select>
              <div class="text-sm font-semibold text-gray-500">=</div>
              <select v-model="cond.right_column" class="px-2.5 py-2 border border-gray-200 rounded text-sm bg-white">
                <option value="">请选择右表字段</option>
                <option v-for="col in getColumnsByScopeAlias(editingConfigId || activeConfig?.id || '', editingRelationship.right_alias)" :key="col.name" :value="col.name">{{ col.name }}</option>
              </select>
              <button
                class="w-8 h-8 border border-gray-200 rounded bg-white text-gray-400 hover:text-red-500 hover:border-red-200 disabled:opacity-40 disabled:cursor-not-allowed"
                :disabled="(editingRelationship.join_conditions || []).length <= 1"
                @click="removeJoinCondition(condIndex)"
              >
                <i class="fa-solid fa-minus text-[11px]"></i>
              </button>
            </div>
          </div>
        </div>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <button class="px-3 py-1.5 rounded border border-gray-200 bg-white text-gray-600" @click="closeRelationshipEditor">取消</button>
          <button class="px-3 py-1.5 rounded border-none bg-blue-500 text-white" @click="confirmRelationshipEditor">确定</button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showExpressionEditorDialog" width="760px" title="编辑表达式">
      <div class="space-y-3">
        <div v-if="editingExpressionRow" class="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500">
          {{ editingExpressionRow.table }}.{{ editingExpressionRow.sourceColumn || editingExpressionRow.column }}
        </div>
        <textarea
          v-model="editingExpressionText"
          rows="8"
          class="w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-[13px] leading-relaxed text-gray-700 outline-none focus:border-blue-500"
          placeholder="请输入字段表达式"
        ></textarea>
        <div
          v-if="editingExpressionRow"
          :class="[
            'grid gap-3',
            editingExpressionRow.isMetric && editingExpressionRow.isDimension ? 'grid-cols-2' : 'grid-cols-1'
          ]"
        >
          <div v-if="editingExpressionRow.isMetric" class="rounded-md border border-gray-200 p-3">
            <div class="mb-1.5 text-xs font-medium text-gray-600">聚合函数</div>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="fn in AGG_FUNCTION_OPTIONS"
                :key="`expr-agg-${fn.value}`"
                class="rounded border border-gray-200 bg-white px-2 py-1 text-[12px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                @click="insertExpressionFromEditor(buildMetricExpression(fn.value, editingExpressionRow.sourceColumn || editingExpressionRow.column))"
              >
                {{ fn.label }}
              </button>
            </div>
          </div>
          <div v-if="editingExpressionRow.isDimension" class="rounded-md border border-gray-200 p-3">
            <div class="mb-1.5 text-xs font-medium text-gray-600">常用函数</div>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="fn in [...COMMON_FUNCTION_OPTIONS, ...visibleDateFunctionOptions, ...STRING_FUNCTION_OPTIONS]"
                :key="`expr-dim-${fn.value}`"
                class="rounded border border-gray-200 bg-white px-2 py-1 text-[12px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                @click="insertExpressionFromEditor(buildDimensionExpression(fn.value, editingExpressionRow.sourceColumn || editingExpressionRow.column))"
              >
                {{ fn.label }}
              </button>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <button
          class="px-3 py-1.5 border border-gray-200 rounded bg-white text-xs text-gray-600 hover:bg-gray-50"
          @click="showExpressionEditorDialog = false"
        >
          取消
        </button>
        <button
          class="ml-2 px-3 py-1.5 border-none rounded bg-[#1677FF] text-xs text-white hover:bg-[#0958D9]"
          @click="saveExpressionEditor"
        >
          保存
        </button>
      </template>
    </el-dialog>

    <el-dialog v-model="showOntologyRulesDialog" width="760px" title="业务说明">
      <div class="space-y-2">
        <div class="text-[13px] font-medium text-gray-700">业务运转说明</div>
        <div class="min-h-[40px] text-[12px] leading-5 text-gray-500">描述这个业务是怎么工作的，例如订单、支付、发货、退款等业务过程和对象关系。</div>
        <textarea
          v-model="editingBusinessFlowText"
          wrap="off"
          class="h-[320px] w-full overflow-auto whitespace-pre px-3 py-2 border border-gray-200 rounded-md text-[13px] leading-relaxed text-gray-700 outline-none resize-none focus:border-blue-500"
          placeholder="例如：用户下单后生成 orders 记录，订单可包含多个 order_items；支付成功后记录支付时间，退款订单不计入有效成交。"
        ></textarea>
      </div>
      <template #footer>
        <button
          class="px-3 py-1.5 border border-gray-200 rounded bg-white text-xs text-gray-600 hover:bg-gray-50"
          @click="showOntologyRulesDialog = false"
        >
          取消
        </button>
        <button
          class="ml-2 px-3 py-1.5 border-none rounded bg-[#1677FF] text-xs text-white hover:bg-[#0958D9] disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!canManageConfig(activeConfig)"
          title="保存业务说明"
          @click="saveOntologyRules"
        >
          保存
        </button>
      </template>
    </el-dialog>

    <el-dialog v-model="showHistoryLearningDialog" width="1080px" title="历史学习">
      <div class="space-y-4">
        <div class="rounded-md border border-gray-200 bg-gray-50 px-3 py-3">
          <div class="grid grid-cols-[150px_150px_1fr_auto] gap-3 items-end">
            <div>
              <div class="text-xs text-gray-600 mb-1">最近天数</div>
              <el-input-number v-model="historyLearningForm.days" :min="1" :max="90" :step="1" size="small" />
            </div>
            <div>
              <div class="text-xs text-gray-600 mb-1">最大数量</div>
              <el-input-number v-model="historyLearningForm.limit" :min="1" :max="200" :step="10" size="small" />
            </div>
            <div>
              <div class="text-xs text-gray-600 mb-1">学习来源</div>
              <el-checkbox-group v-model="historyLearningForm.sources" size="small">
                <el-checkbox-button label="agent">智能问数</el-checkbox-button>
                <el-checkbox-button label="builder">可视化查询</el-checkbox-button>
                <el-checkbox-button label="sql">SQL查询</el-checkbox-button>
              </el-checkbox-group>
            </div>
            <div class="flex items-center gap-3">
              <el-checkbox v-model="historyLearningForm.include_failed">包含失败记录</el-checkbox>
              <el-button type="primary" :loading="historyLearningLoading" @click="runHistoryLearning">
                开始学习
              </el-button>
            </div>
          </div>
          <div class="mt-2 text-xs text-gray-500">
            智能问数学用户对话和纠错反馈；可视化查询学用户配置的维度、指标和过滤；SQL查询学用户编写的SQL。仅分析当前指标集相关历史，采纳后仍需保存当前指标集。
          </div>
          <div class="mt-1 text-xs text-gray-500">
            执行时先取最近 {{ historyLearningForm.days }} 天历史，最多扫描 {{ historyLearningForm.limit * 5 }} 条用于筛选，最终最多学习 {{ historyLearningForm.limit }} 条。
          </div>
        </div>

        <div v-if="historyLearningResult" class="grid grid-cols-6 gap-2 text-xs">
          <div class="rounded border border-gray-200 bg-white px-3 py-2" :title="`最近 ${historyLearningResult.days} 天内最多扫描 ${historyLearningResult.scan_limit || historyLearningResult.limit * 5} 条历史`">
            扫描历史 {{ historyLearningResult.scanned_count }} 条
          </div>
          <div class="rounded border border-gray-200 bg-white px-3 py-2">
            参与学习 {{ historyLearningResult.matched_count }} 条
          </div>
          <div class="rounded border border-gray-200 bg-white px-3 py-2">
            智能问数 {{ historyLearningResult.source_stats?.agent?.matched || 0 }} 条
          </div>
          <div class="rounded border border-gray-200 bg-white px-3 py-2">
            可视化查询 {{ historyLearningResult.source_stats?.builder?.matched || 0 }} 条
          </div>
          <div class="rounded border border-gray-200 bg-white px-3 py-2">
            SQL查询 {{ historyLearningResult.source_stats?.sql?.matched || 0 }} 条
          </div>
          <div class="rounded border border-gray-200 bg-white px-3 py-2" :title="`过滤原因包括：来源未选、非当前指标集、SQL表范围不匹配、SQL解析失败、智能问数缺少对话上下文等`">
            过滤 {{ Math.max(0, historyLearningResult.scanned_count - historyLearningResult.matched_count) }} 条
          </div>
        </div>

        <el-tabs v-if="historyLearningResult" class="history-learning-tabs">
          <el-tab-pane label="候选指标">
            <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200">
              <table class="min-w-full border-collapse text-[12px]">
                <thead class="sticky top-0 bg-gray-50 text-gray-600">
                  <tr>
                    <th class="border border-gray-200 px-3 py-2 text-left">名称</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">表/字段</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">表达式</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">原因</th>
                    <th class="border border-gray-200 px-3 py-2 text-center w-[90px]">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in historyLearningResult.suggestions.metrics" :key="`learn-metric-${item.name}-${item.table}`" class="hover:bg-gray-50">
                    <td class="border border-gray-200 px-3 py-2">{{ item.name }}</td>
                    <td class="border border-gray-200 px-3 py-2">{{ item.table || '-' }} / {{ item.field_name || '-' }}</td>
                    <td class="border border-gray-200 px-3 py-2 font-mono">{{ item.expression || '-' }}</td>
                    <td class="border border-gray-200 px-3 py-2">
                      <div>{{ item.reason }}</div>
                      <div v-if="item.needs_review" class="mt-1 text-red-500">{{ item.review_reason }}</div>
                    </td>
                    <td class="border border-gray-200 px-3 py-2 text-center">
                      <button class="px-2 py-1 rounded border border-blue-200 bg-white text-blue-600 hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed" :disabled="item.needs_review" @click="acceptHistoryMetric(item)">采纳</button>
                    </td>
                  </tr>
                  <tr v-if="historyLearningResult.suggestions.metrics.length === 0">
                    <td colspan="5" class="border border-gray-200 px-3 py-8 text-center text-gray-400">暂无候选指标</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </el-tab-pane>
          <el-tab-pane label="候选维度">
            <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200">
              <table class="min-w-full border-collapse text-[12px]">
                <thead class="sticky top-0 bg-gray-50 text-gray-600">
                  <tr>
                    <th class="border border-gray-200 px-3 py-2 text-left">名称</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">表/字段</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">类型</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">原因</th>
                    <th class="border border-gray-200 px-3 py-2 text-center w-[90px]">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in historyLearningResult.suggestions.dimensions" :key="`learn-dim-${item.name}-${item.table}`" class="hover:bg-gray-50">
                    <td class="border border-gray-200 px-3 py-2">{{ item.name }}</td>
                    <td class="border border-gray-200 px-3 py-2">{{ item.table || '-' }} / {{ item.column || item.field_name || '-' }}</td>
                    <td class="border border-gray-200 px-3 py-2">{{ item.type || 'categorical' }}</td>
                    <td class="border border-gray-200 px-3 py-2">
                      <div>{{ item.reason }}</div>
                      <div v-if="item.needs_review" class="mt-1 text-red-500">{{ item.review_reason }}</div>
                    </td>
                    <td class="border border-gray-200 px-3 py-2 text-center">
                      <button class="px-2 py-1 rounded border border-blue-200 bg-white text-blue-600 hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed" :disabled="item.needs_review" @click="acceptHistoryDimension(item)">采纳</button>
                    </td>
                  </tr>
                  <tr v-if="historyLearningResult.suggestions.dimensions.length === 0">
                    <td colspan="5" class="border border-gray-200 px-3 py-8 text-center text-gray-400">暂无候选维度</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </el-tab-pane>
          <el-tab-pane label="配置问题">
            <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200 p-2">
              <div v-for="item in historyLearningResult.suggestions.issues" :key="`learn-issue-${item.title}`" class="mb-2 rounded border border-amber-200 bg-amber-50 p-2">
                <div class="font-medium text-amber-800">{{ item.title }}</div>
                <div class="mt-1 whitespace-pre-wrap text-amber-700">{{ item.content }}</div>
                <div class="mt-1 text-amber-600">{{ item.reason }}</div>
              </div>
              <div v-if="historyLearningResult.suggestions.issues.length === 0" class="py-12 text-center text-xs text-gray-400">
                暂无配置问题
              </div>
            </div>
          </el-tab-pane>
          <el-tab-pane label="历史样例">
            <div class="max-h-[360px] overflow-auto rounded-md border border-gray-200">
              <table class="min-w-full border-collapse text-[12px]">
                <thead class="sticky top-0 bg-gray-50 text-gray-600">
                  <tr>
                    <th class="border border-gray-200 px-3 py-2 text-left">来源</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">状态</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">用户意图</th>
                    <th class="border border-gray-200 px-3 py-2 text-left">学习信号/SQL</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in historyLearningResult.samples" :key="item.execution_id" class="hover:bg-gray-50">
                    <td class="border border-gray-200 px-3 py-2">{{ item.source === 'agent' ? '智能问数' : item.source === 'builder' ? '可视化查询' : 'SQL查询' }}</td>
                    <td class="border border-gray-200 px-3 py-2">{{ item.status }}</td>
                    <td class="border border-gray-200 px-3 py-2 max-w-[320px]">
                      <div class="truncate" :title="getHistorySampleIntent(item)">{{ getHistorySampleIntent(item) }}</div>
                      <div v-if="item.conversation_id" class="mt-1 text-[11px] text-gray-400">会话 {{ item.conversation_id }}</div>
                    </td>
                    <td class="border border-gray-200 px-3 py-2 max-w-[520px]">
                      <div class="mb-1 text-[11px] text-gray-400">{{ getHistorySampleSignalType(item) }}</div>
                      <div class="truncate font-mono" :title="getHistorySampleSignal(item)">{{ getHistorySampleSignal(item) }}</div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-dialog>

    <el-dialog v-model="showAddFieldDialog" width="560px" title="新增字段">
      <div class="space-y-3">
        <div class="flex items-center justify-end">
          <el-radio-group v-model="addFieldLabelMode" size="small">
            <el-radio-button label="description">中文</el-radio-button>
            <el-radio-button label="name">英文</el-radio-button>
          </el-radio-group>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="text-xs text-gray-600 mb-1">表</div>
            <el-select
              v-model="addFieldForm.tableAlias"
              filterable
              placeholder="请选择表"
              :filter-method="handleAddFieldTableFilter"
              @change="handleAddFieldTableChange"
            >
              <el-option
                v-for="entry in filteredAddFieldTables"
                :key="entry.alias"
                :label="getAddFieldTableLabel(entry)"
                :value="entry.alias"
              />
            </el-select>
          </div>
          <div>
            <div class="text-xs text-gray-600 mb-1">字段</div>
            <el-select
              v-model="addFieldForm.column"
              filterable
              placeholder="请选择字段"
              :filter-method="handleAddFieldColumnFilter"
              @change="handleAddFieldColumnChange"
            >
              <el-option
                v-for="c in filteredAddFieldColumns"
                :key="c.name"
                :label="getAddFieldColumnLabel(c)"
                :value="c.name"
              />
            </el-select>
          </div>
        </div>
        <div>
          <div class="text-xs text-gray-600 mb-1">字段名</div>
          <el-input v-model="addFieldForm.fieldName" placeholder="请输入逻辑字段名（同表内唯一）" />
        </div>
        <div>
          <div class="text-xs text-gray-600 mb-1">显示名称</div>
          <el-input v-model="addFieldForm.displayName" placeholder="请输入显示名称（同数据集内唯一）" />
        </div>
        <div>
          <div class="text-xs text-gray-600 mb-1">新增类型</div>
          <el-radio-group v-model="addFieldForm.mode">
            <el-radio-button label="metric">指标</el-radio-button>
            <el-radio-button label="dimension">维度</el-radio-button>
          </el-radio-group>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <el-button @click="showAddFieldDialog = false">取消</el-button>
          <el-button type="primary" @click="confirmAddField">确定</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showMetricExpandDialog" width="640px" title="扩展聚合指标">
      <div class="space-y-3">
        <div class="text-xs leading-5 text-gray-500">
          基于当前物理字段生成其他聚合指标，已存在的同字段同聚合不会重复生成。
        </div>
        <div class="overflow-hidden rounded-md border border-gray-200">
          <table class="w-full border-collapse text-[12px]">
            <thead class="bg-gray-50 text-gray-600">
              <tr>
                <th class="w-[52px] border border-gray-200 px-3 py-2 text-center">选择</th>
                <th class="border border-gray-200 px-3 py-2 text-left">表达式</th>
                <th class="border border-gray-200 px-3 py-2 text-left">字段名</th>
                <th class="border border-gray-200 px-3 py-2 text-left">显示名称</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="option in metricExpandOptions" :key="option.aggFunc" :class="option.exists ? 'bg-gray-50 text-gray-400' : ''">
                <td class="border border-gray-200 px-3 py-2 text-center">
                  <input v-model="option.selected" type="checkbox" :disabled="option.exists" />
                </td>
                <td class="border border-gray-200 px-3 py-2 font-mono">{{ option.label }}</td>
                <td class="border border-gray-200 px-3 py-2">
                  <el-input v-model="option.fieldName" size="small" :disabled="option.exists" />
                </td>
                <td class="border border-gray-200 px-3 py-2">
                  <div class="flex items-center gap-2">
                    <el-input v-model="option.displayName" size="small" :disabled="option.exists" />
                    <span v-if="option.exists" class="shrink-0 rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">已存在</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <el-button @click="showMetricExpandDialog = false">取消</el-button>
          <el-button type="primary" @click="confirmMetricExpand">确定</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showAutoPrefillModeDialog" width="820px" title="自动预填">
      <div class="space-y-4">
        <div class="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-sm leading-6 text-blue-700">
          将所选表的物理字段预填为维度，并结合字段说明和业务补充推荐最多 10 个常用原子指标。
        </div>
        <div>
          <div class="mb-2 flex items-center justify-between">
            <span class="text-sm font-medium text-gray-700">业务补充</span>
            <div class="flex items-center gap-2">
              <input
                ref="autoPrefillContextFileInputRef"
                class="hidden"
                type="file"
                multiple
                accept=".txt,.docx,.pdf"
                @change="handleAutoPrefillContextFilesChange"
              />
              <el-button size="small" :loading="parsingPrefillContextFile" @click="selectAutoPrefillContextFiles">
                <i class="fa-solid fa-paperclip mr-1"></i>
                上传文件
              </el-button>
            </div>
          </div>
          <el-input
            v-model="autoPrefillBusinessContext"
            type="textarea"
            :rows="8"
            maxlength="20000"
            show-word-limit
            placeholder="可补充业务口径、常分析的字段、金额/数量等指标习惯。支持上传 txt、docx、pdf 文件后追加到这里。"
          />
          <div class="mt-1 text-xs text-gray-400">文件仅用于本次解析，不会保存；单个文件不能超过 10MB。</div>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <el-button @click="showAutoPrefillModeDialog = false">取消</el-button>
          <el-button type="primary" :loading="autoPrefillLoading" @click="handleAutoPrefillFields">开始预填</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showTablePreviewDialog" width="820px" :title="`表数据预览 - ${tablePreviewTitle}`">
      <div v-if="previewingTableName" class="flex h-32 items-center justify-center text-sm text-gray-400">
        <i class="fa-solid fa-spinner fa-spin mr-2"></i>查询中...
      </div>
      <div v-else-if="tablePreviewError" class="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
        {{ tablePreviewError }}
      </div>
      <div v-else-if="tablePreviewRows.length === 0" class="flex h-32 items-center justify-center text-sm text-gray-400">
        暂无数据
      </div>
      <div v-else class="space-y-2">
        <div class="flex justify-end">
          <div class="inline-flex rounded-md border border-gray-200 bg-white p-0.5 text-[12px]">
            <button
              :class="[
                'rounded px-2.5 py-1 transition-colors',
                tablePreviewMode === 'table' ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:text-blue-600'
              ]"
              @click="tablePreviewMode = 'table'"
            >
              表格
            </button>
            <button
              :class="[
                'rounded px-2.5 py-1 transition-colors',
                tablePreviewMode === 'json' ? 'bg-blue-50 text-blue-600' : 'text-gray-500 hover:text-blue-600'
              ]"
              @click="tablePreviewMode = 'json'"
            >
              JSON
            </button>
          </div>
        </div>
        <div v-if="tablePreviewMode === 'table'" class="max-h-[420px] overflow-auto rounded-md border border-gray-200">
          <table class="min-w-full border-collapse text-[12px]">
            <thead class="sticky top-0 bg-gray-50 text-gray-600">
              <tr>
                <th
                  v-for="column in tablePreviewColumns"
                  :key="`preview-head-${column}`"
                  class="border border-gray-200 px-3 py-2 text-left font-medium whitespace-nowrap"
                >
                  {{ column }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rowIndex) in tablePreviewRows" :key="`preview-row-${rowIndex}`">
                <td
                  v-for="column in tablePreviewColumns"
                  :key="`preview-cell-${rowIndex}-${column}`"
                  class="max-w-[260px] truncate border border-gray-200 px-3 py-2 text-gray-700"
                  :title="String(row[column] ?? '')"
                >
                  {{ row[column] ?? '' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <pre v-else class="max-h-[420px] overflow-auto rounded-md border border-gray-200 bg-gray-50 p-3 text-[12px] leading-relaxed text-gray-700">{{ tablePreviewJsonText }}</pre>
      </div>
    </el-dialog>

    <el-dialog v-model="showColumnDictDialog" width="620px" :title="`字段字典 - ${columnDictDialogTitle}`">
      <div v-if="loadingColumnDict" class="flex h-32 items-center justify-center text-sm text-gray-400">
        <i class="fa-solid fa-spinner fa-spin mr-2"></i>加载中...
      </div>
      <div v-else-if="columnDictError" class="rounded-md border border-red-100 bg-red-50 px-3 py-2 text-sm text-red-600">
        {{ columnDictError }}
      </div>
      <div v-else-if="!hasColumnDictContent(columnDictPayload)" class="flex h-32 items-center justify-center text-sm text-gray-400">
        暂无字段字典
      </div>
      <div v-else class="max-h-[460px] space-y-3 overflow-auto text-[12px]">
        <div v-if="columnDictPayload?.enum_values?.length" class="rounded-md border border-gray-200">
          <div class="border-b border-gray-200 bg-gray-50 px-3 py-2 font-medium text-gray-600">枚举值</div>
          <table class="min-w-full border-collapse">
            <thead class="bg-gray-50 text-gray-500">
              <tr>
                <th class="border border-gray-200 px-3 py-2 text-left font-medium">枚举值</th>
                <th class="border border-gray-200 px-3 py-2 text-left font-medium">枚举含义</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in columnDictPayload.enum_values" :key="item.value">
                <td class="border border-gray-200 px-3 py-2 font-mono text-gray-700">{{ item.value }}</td>
                <td class="border border-gray-200 px-3 py-2 text-gray-700">{{ item.label || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="columnDictPayload?.storage_format || columnDictPayload?.unit" class="grid grid-cols-2 gap-3">
          <div v-if="columnDictPayload?.storage_format" class="rounded-md border border-gray-200">
            <div class="border-b border-gray-200 bg-gray-50 px-3 py-2 font-medium text-gray-600">数据存储格式</div>
            <div class="px-3 py-2 text-gray-700">{{ columnDictPayload.storage_format }}</div>
          </div>
          <div v-if="columnDictPayload?.unit" class="rounded-md border border-gray-200">
            <div class="border-b border-gray-200 bg-gray-50 px-3 py-2 font-medium text-gray-600">单位</div>
            <div class="px-3 py-2 text-gray-700">{{ columnDictPayload.unit }}</div>
          </div>
        </div>
      </div>
    </el-dialog>

  </div>
</template>

<style scoped>
button:not(:disabled) {
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
}

input::placeholder,
textarea::placeholder {
  color: #cbd5e1;
  opacity: 1;
}

:deep(.el-input__inner::placeholder),
:deep(.el-textarea__inner::placeholder) {
  color: #cbd5e1;
  opacity: 1;
}

.writing-vertical {
  writing-mode: vertical-rl;
}

:global(.chatdata-evaluation-drawer .el-drawer__body) {
  padding: 0;
  overflow: hidden;
}

:global(.chatdata-evaluation-tooltip) {
  z-index: 4400 !important;
}

:global(.chatdata-semantic-config-select-popper) {
  z-index: 2060 !important;
}

:global(.chatdata-semantic-config-select-popper .el-select-dropdown__item) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evaluation-metric-header {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  white-space: nowrap;
}

.evaluation-metric-header i {
  color: #9ca3af;
  cursor: help;
  font-size: 12px;
}

.evaluation-metric-header i:hover {
  color: #64748b;
}
</style>
