<script setup lang="ts">
/**
 * 语义配置面板
 * 支持管理指标、维度、表关系的完整配置
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getMySemanticConfigs,
  getAdminSemanticConfigs,
  createAdminSemanticConfig,
  createSemanticConfig,
  updateAdminSemanticConfig,
  updateSemanticConfig,
  deleteAdminSemanticConfig,
  deleteSemanticConfig,
  toggleAdminSemanticConfig,
  toggleSemanticConfig,
  autoPrefillSemanticFields,
  parseSemanticPrefillContextFile,
  createQueryTask,
  getQueryTask,
  getDatasourceColumnDict,
  getDatasourceTableColumnDicts,
} from '../api'
import {
  SQL_EXPRESSION_FUNCTION_KEYWORDS,
  SQL_FUNCTION_GROUPS,
  buildSqlFunctionExpression,
  getSqlFunctionOptions,
  formatSemanticConfigOptionLabel,
  isQuerySucceeded,
} from '../utils'
import type { DatasourceColumnDictPayload, SemanticHistoryLearningSuggestion } from '../api'
import SemanticEvaluationDrawer from './semantic/SemanticEvaluationDrawer.vue'
import SemanticHistoryLearningDialog from './semantic/SemanticHistoryLearningDialog.vue'
import type {
  SemanticConfigItem,
  SemanticMetric,
  SemanticDimension,
  SemanticRelationship,
  TableInfo,
} from '../types'

type SemanticLogicalType = NonNullable<SemanticDimension['logical_type']>

interface Props {
  datasourceId: string
  datasourceName: string
  datasourceType?: string
  tables: TableInfo[]
  embedded?: boolean
  selectedConfigId?: string | null
  hideDatasetSelector?: boolean
  adminMode?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  adminMode: false,
})
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
const togglingEnabledId = ref<string | null>(null)
const creatingConfig = ref(false)
const savedConfigSignatures = ref<Record<string, string>>({})

interface TableScopeEntry {
  table: string
  alias: string
}

const tableScopes = ref<Record<string, TableScopeEntry[]>>({})
const defaultDimensionScopeAliases = ref<Record<string, string[]>>({})
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
const showHistoryLearningDialog = ref(false)
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

const activeConfig = computed<SemanticConfigItem | null>(() => {
  if (configs.value.length === 0) return null
  const targetId = props.selectedConfigId || expandedId.value
  if (!targetId) return configs.value[0] || null
  return configs.value.find(c => String(c.id) === String(targetId)) || null
})

const isActiveConfigDirty = computed(() => {
  if (!activeConfig.value) return false
  const saved = savedConfigSignatures.value[activeConfig.value.id]
  if (!saved) return false
  return buildConfigSignature(activeConfig.value) !== saved
})

const hasUnsavedSemanticConfig = computed(() => isActiveConfigDirty.value || isFieldRowsDirty.value)
const canOpenEvaluation = computed(() => Boolean(activeConfig.value && props.datasourceId))

const formatJsonPreview = (value: unknown) => {
  if (value === null || value === undefined) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch (_err) {
    return String(value)
  }
}

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
  if (!config) return false
  return typeof config.can_manage === 'boolean' ? config.can_manage : true
}

const semanticConfigListApi = computed(() => props.adminMode ? getAdminSemanticConfigs : getMySemanticConfigs)
const semanticConfigUpdateApi = computed(() => props.adminMode ? updateAdminSemanticConfig : updateSemanticConfig)
const semanticConfigDeleteApi = computed(() => props.adminMode ? deleteAdminSemanticConfig : deleteSemanticConfig)
const semanticConfigToggleApi = computed(() => props.adminMode ? toggleAdminSemanticConfig : toggleSemanticConfig)

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

const getConfigIndex = (config: SemanticConfigItem) => {
  return configs.value.findIndex(item => item.id === config.id)
}

const formatConfigOptionLabel = (config: SemanticConfigItem) => {
  return formatSemanticConfigOptionLabel(config, getConfigIndex(config))
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
  const nextDefaultAliases: Record<string, string[]> = {}
  Object.entries(defaultDimensionScopeAliases.value).forEach(([configId, aliases]) => {
    const scopeEntries = next[configId]
    if (!scopeEntries) return
    const scopeAliasSet = new Set(scopeEntries.map(entry => entry.alias))
    const keptAliases = aliases.filter(alias => scopeAliasSet.has(alias))
    if (keptAliases.length > 0) {
      nextDefaultAliases[configId] = keptAliases
    }
  })
  defaultDimensionScopeAliases.value = nextDefaultAliases
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

const getDefaultDimensionAliasSet = (configId: string) => {
  return new Set(defaultDimensionScopeAliases.value[configId] || [])
}

const markDefaultDimensionScopeAlias = (configId: string, alias: string) => {
  if (!configId || !alias) return
  const current = getDefaultDimensionAliasSet(configId)
  if (current.has(alias)) return
  defaultDimensionScopeAliases.value = {
    ...defaultDimensionScopeAliases.value,
    [configId]: [...current, alias],
  }
}

const clearDefaultDimensionScopeAliases = (configId: string, aliases?: string[]) => {
  if (!configId) return
  const current = getDefaultDimensionAliasSet(configId)
  const nextMap = { ...defaultDimensionScopeAliases.value }
  if (!aliases) {
    delete nextMap[configId]
  } else {
    aliases.forEach(alias => current.delete(alias))
    if (current.size > 0) {
      nextMap[configId] = [...current]
    } else {
      delete nextMap[configId]
    }
  }
  defaultDimensionScopeAliases.value = nextMap
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
  markDefaultDimensionScopeAlias(configId, alias)
  toggleScopeEntry(configId, { table: tableName, alias }, true)
}

// 移除数据表并级联清理关联配置
const removeScopeTable = (configId: string, alias: string) => {
  const entry = getScopeEntryByAlias(configId, alias)
  if (!entry) return
  clearDefaultDimensionScopeAliases(configId, [entry.alias])
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
    const res = await semanticConfigListApi.value(props.datasourceId)
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
    } else {
      configs.value = []
      expandedId.value = null
      ElMessage.error(res.msg || '获取配置失败')
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
    const { value } = await ElMessageBox.prompt('请输入指标集名称', '新建指标集', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValue: '',
      inputPlaceholder: '请输入指标集名称',
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
    const createApi = props.adminMode ? createAdminSemanticConfig : createSemanticConfig
    const res = await createApi(props.datasourceId, {
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
    } else {
      ElMessage.error(res.msg || '创建失败')
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
    const res = await semanticConfigUpdateApi.value(props.datasourceId, config.id, config)
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

const openEvaluateDrawer = () => {
  if (!activeConfig.value) return
  showEvaluateDrawer.value = true
}

// 删除配置
const handleDeleteConfig = async (configId: string, configName: string) => {
  if (!confirm(`确定要删除"${configName}"吗？`)) return
  try {
    const res = await semanticConfigDeleteApi.value(props.datasourceId, configId)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '删除失败')
      return
    }
    const newConfigs = configs.value.filter(c => c.id !== configId)
    configs.value = newConfigs
    const nextSignatures = { ...savedConfigSignatures.value }
    delete nextSignatures[configId]
    savedConfigSignatures.value = nextSignatures
    clearDefaultDimensionScopeAliases(configId)
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
  if (togglingEnabledId.value) return
  togglingEnabledId.value = config.id
  try {
    const res = await semanticConfigToggleApi.value(props.datasourceId, config.id, !config.enabled)
    if (res.status !== 'success' || !res.data) {
      ElMessage.error(res.msg || '操作失败')
      return
    }
    configs.value = configs.value.map(c => c.id === config.id ? res.data! : c)
    emit('configChange')
    savedConfigSignatures.value = {
      ...savedConfigSignatures.value,
      [config.id]: buildConfigSignature(res.data),
    }
  } catch (err) {
    ElMessage.error('操作失败')
  } finally {
    togglingEnabledId.value = null
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
  logicalType: SemanticLogicalType
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
const showFieldPhysicalDetails = ref(false)
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
const columnDictEnumVisibleMap = ref<Record<string, boolean>>({})
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
const AGG_FUNCTION_OPTIONS = [
  { value: 'COUNT', label: 'COUNT', desc: '计数' },
  { value: 'COUNT_DISTINCT', label: 'COUNT DISTINCT', desc: '去重计数' },
  { value: 'SUM', label: 'SUM', desc: '求和' },
  { value: 'AVG', label: 'AVG', desc: '平均值' },
  { value: 'MAX', label: 'MAX', desc: '最大值' },
  { value: 'MIN', label: 'MIN', desc: '最小值' },
]
const LOGICAL_TYPE_OPTIONS: Array<{ value: SemanticLogicalType; label: string }> = [
  { value: 'text', label: '文本' },
  { value: 'number', label: '数值' },
  { value: 'date', label: '日期' },
  { value: 'datetime', label: '日期时间' },
  { value: 'enum', label: '枚举' },
]

const dimensionFunctionOptions = computed(() => (
  getSqlFunctionOptions(props.datasourceType, true).filter(item => item.usesExpression !== false)
))
const visibleDimensionFunctionGroups = computed(() => {
  return SQL_FUNCTION_GROUPS
    .map(group => ({
      ...group,
      items: dimensionFunctionOptions.value.filter(item => item.group === group.key),
    }))
    .filter(group => group.items.length > 0)
})
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
  logicalType: row.logicalType,
  metricAggFunc: row.metricAggFunc,
  expression: row.expression,
  isCustom: row.isCustom,
})))
const isFieldRowsDirty = computed(() => buildFieldRowsEditSignature(fieldRows.value) !== fieldRowsSnapshot.value)
const isSavingActiveConfig = computed(() => Boolean(activeConfig.value && savingId.value === activeConfig.value.id))

const getFieldRowEffectiveDisplayName = (row: FieldConfigRow) => {
  return ((row.displayName || '').trim() || row.column).trim()
}

const duplicateFieldDisplayNameSet = computed(() => {
  const nameCounts = new Map<string, number>()
  fieldRows.value.forEach(row => {
    if (!row.isDimension && !row.isMetric) return
    const displayName = getFieldRowEffectiveDisplayName(row)
    if (!displayName) return
    nameCounts.set(displayName, (nameCounts.get(displayName) || 0) + 1)
  })
  return new Set(
    Array.from(nameCounts.entries())
      .filter(([, count]) => count > 1)
      .map(([name]) => name)
  )
})

const getFieldRowDisplayNameError = (row: FieldConfigRow) => {
  if (!row.isDimension && !row.isMetric) return ''
  const displayName = getFieldRowEffectiveDisplayName(row)
  if (!displayName) return '显示名称不能为空'
  return duplicateFieldDisplayNameSet.value.has(displayName) ? `显示名称重复：${displayName}` : ''
}

const activeFieldRowsValidationError = computed(() => {
  const duplicateName = Array.from(duplicateFieldDisplayNameSet.value)[0]
  return duplicateName ? `字段显示名称重复：${duplicateName}` : ''
})

const activeConfigSaveDisabledReason = computed(() => {
  if (!activeConfig.value) return '暂无指标集'
  if (isSavingActiveConfig.value) return '正在保存'
  if (!activeConfig.value.name) return '请填写指标集名称'
  if (editingType.value !== null) return '请先完成当前编辑'
  if (activeFieldRowsValidationError.value) return activeFieldRowsValidationError.value
  if (!isActiveConfigDirty.value && !isFieldRowsDirty.value) return '没有需要保存的修改'
  if (!canManageConfig(activeConfig.value)) return '没有权限修改该指标集'
  return ''
})

const canSaveActiveConfig = computed(() => !activeConfigSaveDisabledReason.value)

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
    if (row.isDimension && row.logicalType === 'enum' && !columnDictEnumVisibleMap.value[getFieldColumnDictKey(row)]) {
      return `字段 ${row.table}.${sourceColumn} 选择了枚举逻辑类型，请先维护字段字典枚举值`
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
    logical_type: 'text',
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
    'unix_timestamp', 'from_unixtime', 'year', 'month', 'day',
    ...SQL_EXPRESSION_FUNCTION_KEYWORDS,
  ])
  const result = new Set<string>()
  const pattern = /`([^`]+)`|(?<!['"])\b([A-Za-z_][A-Za-z0-9_]*)\b(?!['"])/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    if (text.slice(match.index + match[0].length).trimStart().startsWith('.')) continue
    const isQuotedIdentifier = Boolean(match[1])
    const token = (match[1] || match[2] || '').trim().split('.').pop() || ''
    if (!token || (!isQuotedIdentifier && keywords.has(token.toLowerCase()))) continue
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

const inferLogicalType = (columnType: string): SemanticLogicalType => {
  const normalized = (columnType || '').toLowerCase()
  if (/timestamp|datetime/.test(normalized)) return 'datetime'
  if (/date|year/.test(normalized)) return 'date'
  if (isNumericColumnType(normalized)) return 'number'
  return 'text'
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

const buildDimensionPreserveKey = (dimension: Pick<SemanticDimension, 'table' | 'table_alias' | 'column' | 'field_name'>) => {
  const tableAlias = getFieldTableAlias(dimension)
  return `${tableAlias}.${dimension.field_name || dimension.column || ''}.${dimension.column || ''}`
}

const copyDimensionRuntimeConfig = (dimension: SemanticDimension, source?: SemanticDimension) => {
  if (!source) return dimension
  const next = { ...dimension }
  if (source.allowed_values) next.allowed_values = [...source.allowed_values]
  if (source.mapping) next.mapping = { ...source.mapping }
  if (source.allowed_operators) next.allowed_operators = [...source.allowed_operators]
  return next
}

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
    logicalType: inferLogicalType(columnType),
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
      logicalType: sourceRow.logicalType,
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
    logicalType: inferLogicalType(columnType),
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
    }, { adminMode: props.adminMode })
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
  showHistoryLearningDialog.value = true
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
    logicalType: inferLogicalType(columnType),
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
    logicalType: suggestion.logical_type || inferLogicalType(columnType),
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

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))

const waitTablePreviewTask = async (taskId: string, pollIntervalMs = 1500) => {
  const deadline = Date.now() + 60_000
  while (Date.now() < deadline) {
    const response = await getQueryTask(taskId)
    const task = response.data
    if (!task) {
      throw new Error(response.msg || '查询任务不存在')
    }
    if (task.status === 'succeeded' || task.status === 'partial_success') {
      return task
    }
    if (task.status === 'failed' || task.status === 'cancelled') {
      throw new Error(task.error_message || '查询失败')
    }
    await sleep(Math.max(500, pollIntervalMs))
  }
  throw new Error('查询超时，请稍后重试')
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
    const createResp = await createQueryTask({
      datasource_id: props.datasourceId,
      sql: `SELECT * FROM ${quoteTableIdentifier(tableName)} LIMIT 1`,
      limit: 1,
      query_type: 'sql',
      query_text: '表数据预览',
    })
    const taskId = createResp.data?.task_id
    if (createResp.status !== 'success' || !taskId) {
      tablePreviewError.value = createResp.msg || '查询任务创建失败'
      return
    }
    const task = await waitTablePreviewTask(taskId, createResp.data?.poll_interval_ms)
    const taskResults = Array.isArray(task.results) ? task.results : []
    const firstResult = taskResults.find(result => isQuerySucceeded(result)) || taskResults[0] || null
    if (!firstResult) {
      tablePreviewError.value = task.error_message || '查询结果为空'
      return
    }
    if (!isQuerySucceeded(firstResult)) {
      tablePreviewError.value = firstResult.error || '查询失败'
      return
    }
    const rows = firstResult.data || []
    const cols = (firstResult.columns && firstResult.columns.length > 0)
      ? firstResult.columns
      : (rows.length > 0 ? Object.keys(rows[0]) : [])
    tablePreviewColumns.value = cols
    tablePreviewRows.value = rows
  } catch (err: any) {
    tablePreviewError.value = err?.message || '查询失败'
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

const hasColumnDictEnumValues = (dict?: DatasourceColumnDictPayload | null) => {
  return Boolean(dict?.enum_values?.some(item => String(item.value || '').trim()))
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
    columnDictEnumVisibleMap.value = {}
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
    columnDictEnumVisibleMap.value = {}
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
      return Array.from(columnNames).map(columnName => {
        const dict = dicts[columnName]
        return [
          getColumnDictKey(tableName, columnName),
          { visible: hasColumnDictContent(dict), enumVisible: hasColumnDictEnumValues(dict) },
        ] as const
      })
    } catch {
      return Array.from(columnNames).map(columnName => [
        getColumnDictKey(tableName, columnName),
        { visible: false, enumVisible: false },
      ] as const)
    }
  }))
  if (seq !== columnDictLoadSeq) return
  const flattened = entries.flat()
  columnDictVisibleMap.value = Object.fromEntries(flattened.map(([key, value]) => [key, value.visible]))
  columnDictEnumVisibleMap.value = Object.fromEntries(flattened.map(([key, value]) => [key, value.enumVisible]))
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
  return buildSqlFunctionExpression(func, quoted, props.datasourceType)
}

const buildFieldRows = (configId: string, applyDefaultDimensions = true) => {
  const config = configs.value.find(c => c.id === configId)
  if (!config) return []
  const scopeEntries = getScopeEntries(configId)
  const scopeAliasSet = new Set(scopeEntries.map(entry => entry.alias))
  const defaultDimensionAliasSet = applyDefaultDimensions ? getDefaultDimensionAliasSet(configId) : new Set<string>()

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
        logicalType: existed?.logicalType || d.logical_type || inferLogicalType(existed?.columnType || getTableColumnType(d.table, d.column)),
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
        logicalType: existed?.logicalType || inferLogicalType(existed?.columnType || getTableColumnType(m.table, exprColumn)),
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
      const columnType = col.mysql_type || col.hive_type || col.type || ''
      rows.push({
        key,
        table: table.table_name,
        tableAlias: entry.alias,
        column: col.name,
        sourceColumn: col.name,
        columnType,
        isDimension: dimensionSet.has(key) || defaultDimensionAliasSet.has(entry.alias),
        isMetric: !!metricInfo,
        displayName,
        columnComment,
        description,
        metadataSupplement,
        logicalType: dimInfo?.logical_type || inferLogicalType(columnType),
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
      logicalType: row.logicalType || inferLogicalType(row.columnType || sourceInfo?.mysql_type || sourceInfo?.hive_type || sourceInfo?.type || ''),
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
      logicalType: d.logical_type || inferLogicalType(getTableColumnType(d.table, d.column)),
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
      logicalType: inferLogicalType(getTableColumnType(m.table, exprColumn)),
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
      logicalType: draft.logicalType,
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
  fieldRowsSnapshot.value = buildFieldRowsEditSignature(buildFieldRows(configId, false))
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

  const existingDimensionMap = new Map(config.dimensions.map(item => [buildDimensionPreserveKey(item), item]))
  const nextDimensions: SemanticDimension[] = fieldRows.value
    .filter(r => r.isDimension)
    .map(r => {
      const dimension: SemanticDimension = {
        name: ((r.displayName || '').trim() || r.column),
        field_name: r.column,
        table: r.table,
        table_alias: getFieldRowAlias(r),
        column: r.sourceColumn || r.column,
        expression: (r.isMetric ? quoteIdentifier(r.sourceColumn || r.column) : (r.expression || quoteIdentifier(r.sourceColumn || r.column))).trim(),
        logical_type: r.logicalType || 'text',
        description: r.description || '',
        metadata_supplement: (r.metadataSupplement || '').trim(),
      }
      return copyDimensionRuntimeConfig(dimension, existingDimensionMap.get(buildDimensionPreserveKey(dimension)))
    })

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
  clearDefaultDimensionScopeAliases(configId)
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
        <div class="text-sm font-semibold text-gray-800">指标集管理</div>
      </div>
      <div class="flex min-w-0 flex-1 flex-nowrap items-center gap-3">
        <span class="shrink-0 whitespace-nowrap text-[13px] font-medium text-slate-700">数据源组</span>
        <div class="h-9 w-[280px] max-w-[24vw] shrink-0 flex items-center rounded-lg border border-slate-200 bg-slate-50 px-3 text-[13px] text-slate-700 truncate">
          {{ datasourceName || '暂无数据源' }}
        </div>
        <span class="ml-2 shrink-0 whitespace-nowrap text-[13px] font-medium text-slate-700">指标集</span>
        <el-select
          :model-value="activeConfig?.id || ''"
          class="min-w-0 flex-1"
          filterable
          :disabled="loading || configs.length === 0"
          :title="activeConfig ? formatConfigOptionLabel(activeConfig) : ''"
          placeholder="请选择指标集"
          fit-input-width
          popper-class="chatdata-semantic-config-select-popper"
          @change="toggleExpand"
        >
          <el-option v-if="loading" value="" label="加载中" />
          <el-option v-else-if="configs.length === 0" value="" label="暂无指标集" />
          <el-option
            v-for="(config, configIndex) in configs"
            :key="config.id"
            :value="config.id"
            :label="formatSemanticConfigOptionLabel(config, configIndex)"
            :title="formatSemanticConfigOptionLabel(config, configIndex)"
          />
        </el-select>
        <button
          class="h-9 w-9 shrink-0 flex items-center justify-center border-none rounded-lg bg-blue-500 text-white text-[13px] transition-all duration-150 hover:bg-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
          title="新建指标集"
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
        <p class="text-sm text-gray-500 m-0 mb-1">暂无指标集</p>
        <span class="text-xs text-gray-400">点击上方加号创建第一个指标集</span>
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
                  :title="formatConfigOptionLabel(activeConfig)"
                  placeholder="请选择指标集"
                  fit-input-width
                  popper-class="chatdata-semantic-config-select-popper"
                  @change="toggleExpand"
                >
                  <el-option
                    v-for="(config, configIndex) in configs"
                    :key="config.id"
                    :value="config.id"
                    :label="formatSemanticConfigOptionLabel(config, configIndex)"
                    :title="formatSemanticConfigOptionLabel(config, configIndex)"
                  />
                </el-select>
                <button
                  class="h-9 w-9 shrink-0 flex items-center justify-center border-none rounded-md bg-blue-500 text-white text-[12px] cursor-pointer transition-all duration-150 hover:bg-blue-600 disabled:opacity-60 disabled:cursor-not-allowed"
                  title="新建指标集"
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
                placeholder="指标集名称"
                :disabled="!canManageConfig(activeConfig)"
                @input="updateConfig(activeConfig.id, { name: ($event.target as HTMLInputElement).value })"
              />
              <input
                type="text"
                :value="activeConfig.description"
                placeholder="指标集描述：简要说明这个指标集是什么"
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
                  :disabled="togglingEnabledId === activeConfig.id || !canManageConfig(activeConfig)"
                  @click="handleToggleEnabled(activeConfig)"
                >
                  <template v-if="togglingEnabledId === activeConfig.id">
                    <i class="fa-solid fa-spinner fa-spin mr-1"></i>处理中
                  </template>
                  <template v-else>
                    <i :class="['fa-solid mr-1', activeConfig.enabled ? 'fa-circle-check' : 'fa-circle-pause']"></i>{{ activeConfig.enabled ? '已启用' : '已禁用' }}
                  </template>
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
                    canSaveActiveConfig ? 'bg-[#1677FF] text-white cursor-pointer hover:bg-[#0958D9]' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  ]"
                  :disabled="!canSaveActiveConfig"
                  :title="activeConfigSaveDisabledReason || '保存指标集'"
                  @click="saveActiveConfig"
                >
                  <template v-if="isSavingActiveConfig">
                    <i class="fa-solid fa-spinner fa-spin mr-1"></i>保存中
                  </template>
                  <template v-else>
                    <i class="fa-solid fa-save mr-1"></i>保存
                  </template>
                </button>
                <button
                  class="px-3 py-2 rounded-md text-xs border border-red-100 bg-white text-red-500 transition-all duration-150 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white"
                  :disabled="!canManageConfig(activeConfig)"
                  title="删除指标集"
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
                        :class="[
                          'h-7 px-2.5 rounded border bg-white text-[11px] transition-all duration-150',
                          showFieldPhysicalDetails ? 'border-blue-200 text-blue-600 bg-blue-50 hover:bg-blue-100' : 'border-gray-200 text-gray-600 hover:border-blue-200 hover:text-blue-600'
                        ]"
                        @click="showFieldPhysicalDetails = !showFieldPhysicalDetails"
                      >
                        <i class="fa-regular fa-eye mr-1"></i>字段详情
                      </button>
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
                    <table :class="[showFieldPhysicalDetails ? 'min-w-[1940px]' : 'min-w-[1540px]', 'w-full border-collapse text-[12px]']">
                      <thead class="bg-gray-50 text-gray-600">
                        <tr>
                          <th class="text-left px-3 py-2 border border-gray-200">表</th>
                          <th class="text-left px-3 py-2 border border-gray-200">字段</th>
                          <th v-if="showFieldPhysicalDetails" class="text-left px-3 py-2 border border-gray-200">类型</th>
                          <th v-if="showFieldPhysicalDetails" class="text-left px-3 py-2 border border-gray-200">列备注</th>
                          <th class="text-center px-3 py-2 border border-gray-200">维度</th>
                          <th class="text-center px-3 py-2 border border-gray-200">指标</th>
                          <th class="text-left px-3 py-2 border border-gray-200 w-[118px]">逻辑类型</th>
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
                          <td v-if="showFieldPhysicalDetails" class="px-3 py-2 border border-gray-200 text-gray-500">{{ row.columnType || '-' }}</td>
                          <td v-if="showFieldPhysicalDetails" class="px-3 py-2 border border-gray-200 text-gray-500">
                            <div class="max-w-[220px] truncate" :title="row.columnComment || '-'">{{ row.columnComment || '-' }}</div>
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <input :checked="row.isDimension" :disabled="row.isMissingPhysical" type="checkbox" @change="handleDimensionToggle(row, ($event.target as HTMLInputElement).checked)" />
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <input :checked="row.isMetric" :disabled="row.isMissingPhysical" type="checkbox" @change="handleMetricToggle(row, ($event.target as HTMLInputElement).checked)" />
                          </td>
                          <td class="px-3 py-2 border border-gray-200">
                            <select
                              v-model="row.logicalType"
                              class="w-full px-2 py-1 border border-gray-200 rounded text-[12px] bg-white disabled:bg-gray-50 disabled:text-gray-400"
                              :disabled="row.isMissingPhysical || !row.isDimension"
                            >
                              <option v-for="option in LOGICAL_TYPE_OPTIONS" :key="option.value" :value="option.value">
                                {{ option.label }}
                              </option>
                            </select>
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
                              :class="[
                                'w-full px-2 py-1 border rounded text-[12px] outline-none',
                                getFieldRowDisplayNameError(row)
                                  ? 'border-red-400 bg-red-50 text-red-700 focus:border-red-500'
                                  : 'border-gray-200 focus:border-blue-500'
                              ]"
                              :title="getFieldRowDisplayNameError(row) || ''"
                              placeholder="默认字段名"
                            />
                            <div v-if="getFieldRowDisplayNameError(row)" class="mt-1 text-[11px] leading-4 text-red-500">
                              {{ getFieldRowDisplayNameError(row) }}
                            </div>
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

    <SemanticEvaluationDrawer
      v-model:visible="showEvaluateDrawer"
      :datasource-id="props.datasourceId"
      :config="activeConfig"
      :has-unsaved-semantic-config="hasUnsavedSemanticConfig"
      :admin-mode="props.adminMode"
    />

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
            <div class="max-h-[220px] overflow-auto pr-1">
              <div
                v-for="group in visibleDimensionFunctionGroups"
                :key="`expr-dim-group-${group.key}`"
                class="mb-2 last:mb-0"
              >
                <div class="mb-1 text-[11px] font-medium text-gray-400">{{ group.label }}</div>
                <div class="flex flex-wrap gap-1.5">
                  <button
                    v-for="fn in group.items"
                    :key="`expr-dim-${fn.value}`"
                    class="rounded border border-gray-200 bg-white px-2 py-1 text-[12px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                    :title="fn.desc"
                    @click="insertExpressionFromEditor(buildDimensionExpression(fn.value, editingExpressionRow.sourceColumn || editingExpressionRow.column))"
                  >
                    {{ fn.label }}
                  </button>
                </div>
              </div>
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

    <SemanticHistoryLearningDialog
      v-model:visible="showHistoryLearningDialog"
      :datasource-id="props.datasourceId"
      :config="activeConfig"
      :admin-mode="props.adminMode"
      @accept-metric="acceptHistoryMetric"
      @accept-dimension="acceptHistoryDimension"
    />

    <el-dialog v-model="showAddFieldDialog" width="560px" title="新增字段">
      <div class="space-y-3">
        <div class="flex items-center justify-end">
          <el-radio-group v-model="addFieldLabelMode" size="small">
            <el-radio-button value="description">中文</el-radio-button>
            <el-radio-button value="name">英文</el-radio-button>
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
          <el-input v-model="addFieldForm.displayName" placeholder="请输入显示名称（同指标集内唯一）" />
        </div>
        <div>
          <div class="text-xs text-gray-600 mb-1">新增类型</div>
          <el-radio-group v-model="addFieldForm.mode">
            <el-radio-button value="metric">指标</el-radio-button>
            <el-radio-button value="dimension">维度</el-radio-button>
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


:global(.chatdata-semantic-config-select-popper) {
  z-index: 2060 !important;
}

:global(.chatdata-semantic-config-select-popper .el-select-dropdown__item) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

</style>
