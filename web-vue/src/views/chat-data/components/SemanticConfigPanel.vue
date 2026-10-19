<script setup lang="ts">
/**
 * 语义配置面板
 * 支持管理指标、维度、表关系的完整配置
 */
import { ref, computed, watch, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { confirmSaveQualityFindings } from './semantic/qualityFindings'
import {
  getMySemanticConfigs,
  getAdminSemanticConfigs,
  createAdminSemanticConfig,
  createSemanticConfig,
  generateAdminBasicSemanticConfigs,
  updateAdminSemanticConfig,
  updateSemanticConfig,
  deleteAdminSemanticConfig,
  deleteSemanticConfig,
  toggleAdminSemanticConfig,
  toggleSemanticConfig,
  batchToggleAdminSemanticConfig,
  batchToggleSemanticConfig,
  getDatasourceTableColumnDicts,
  getAdminDatasourceTableColumnDicts,
  getAdminDatasourceSemanticHealth,
  validateAdminSemanticConfig,
  validateSemanticConfig,
} from '../api'
import {
  SQL_EXPRESSION_FUNCTION_KEYWORDS,
  buildMetricExpression,
  quoteIdentifier,
  formatSemanticConfigOptionLabel,
  getRelationshipConditions,
  normalizeSemanticRelationship,
  syncRelationshipColumnsFromConditions,
} from '../utils'
import type { DatasourceColumnDictPayload, DatasourceSemanticHealthResult, SemanticHistoryLearningSuggestion } from '../api'
import SemanticEvaluationDrawer from './semantic/SemanticEvaluationDrawer.vue'
import SemanticHealthModal from './semantic/SemanticHealthModal.vue'
import SemanticHistoryLearningDialog from './semantic/SemanticHistoryLearningDialog.vue'
import BusinessNotesDialog from './semantic/BusinessNotesDialog.vue'
import ColumnDictEditDialog from './semantic/ColumnDictEditDialog.vue'
import TablePreviewDialog from './semantic/TablePreviewDialog.vue'
import MetricExpandDialog from './semantic/MetricExpandDialog.vue'
import AddFieldDialog from './semantic/AddFieldDialog.vue'
import ExpressionEditorDialog from './semantic/ExpressionEditorDialog.vue'
import RelationshipEditorDialog from './semantic/RelationshipEditorDialog.vue'
import OssieImportDialog from './semantic/OssieImportDialog.vue'
import SemanticBatchToggleDialog from './semantic/SemanticBatchToggleDialog.vue'
import type {
  SemanticConfigItem,
  SemanticMetric,
  SemanticDimension,
  SemanticRelationship,
  TableInfo,
  MetricExpandOption,
  AddFieldForm,
  AddFieldTableOption,
} from '../types'

// 领域纯函数与共享类型已抽至 semantic/panelModelUtils.ts（单一来源）
import {
  AGG_DISPLAY_SUFFIX_MAP,
  EXPANDABLE_AGG_FUNCS,
  LOGICAL_TYPE_OPTIONS,
  atomicMetricColumn,
  buildAliasAwareDefaultDisplayName,
  buildConfigSignature,
  buildDimensionPreserveKey,
  buildFieldRowKey,
  buildFieldRows as buildFieldRowsForConfig,
  buildMetricExpandDisplayName,
  buildTablePrefixedDisplayName,
  canManageConfig,
  copyDimensionRuntimeConfig,
  deriveScopeFromConfig,
  extractExpressionColumns,
  formatConfigOperator,
  formatJoinTypeLabel,
  formatRelationshipTypeLabel,
  generateScopeAlias,
  getColumnDictKey,
  getFieldColumnDictKey,
  getFieldPhysicalColumn,
  getFieldRowAlias,
  getFieldTableAlias,
  getMetricDataType,
  getRelationshipLeftAlias,
  getRelationshipRightAlias,
  getScopeAlias,
  getScopePhysicalTable,
  hasColumnDictContent,
  hasColumnDictEnumValues,
  inferLogicalType,
  isFieldRowInScope,
  isNumericColumnType,
  isNumericPhysicalTableName,
  isRawColumnExpression,
  expressionUsesColumn,
  mergeFieldRowsWithDraft as mergeFieldRowsWithDraftUtil,
  normalizeExpressionColumn,
  normalizeFieldName,
  normalizeScopeEntries,
  normalizeSqlAliasBase,
  parseAggFuncFromExpression,
  parseSimpleMetricExpression,
  toLetterSuffix,
} from './semantic/panelModelUtils'
import type { FieldConfigRow, SemanticLogicalType, TableScopeEntry } from './semantic/panelModelUtils'

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
  configListChange: [configs: SemanticConfigItem[]]
  'update:selectedConfigId': [configId: string | null]
}>()

// 配置列表
const configs = ref<SemanticConfigItem[]>([])
const loading = ref(true)
const expandedId = ref<string | null>(null)
const savingId = ref<string | null>(null)
const togglingEnabledId = ref<string | null>(null)
const creatingConfig = ref(false)
const generatingBasicConfigs = ref(false)
const ossieImportVisible = ref(false)
const batchToggleVisible = ref(false)
const batchToggleEnabled = ref(true)
const batchToggling = ref(false)
const savedConfigSignatures = ref<Record<string, string>>({})

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

// 评测 / 历史学习入口开关：当前阶段暂停暴露（组件、composable 与后端 API 均保留，置 true 即恢复）
const SHOW_EVALUATION_ENTRY = false

// 语义模型健康校验（管理员入口，校验范围=当前数据源的缓存表结构 + 全部语义模型）
const checkingHealth = ref(false)
const showHealthModal = ref(false)
const healthResult = ref<DatasourceSemanticHealthResult | null>(null)

const handleCheckSemanticHealth = async () => {
  if (!props.datasourceId || checkingHealth.value) return
  checkingHealth.value = true
  try {
    const res = await getAdminDatasourceSemanticHealth(props.datasourceId)
    if (res.status !== 'success' || !res.data) {
      ElMessage.error(res.msg || '校验失败')
      return
    }
    healthResult.value = res.data
    showHealthModal.value = true
  } catch {
    ElMessage.error('校验失败')
  } finally {
    checkingHealth.value = false
  }
}

const semanticConfigListApi = computed(() => props.adminMode ? getAdminSemanticConfigs : getMySemanticConfigs)
const semanticConfigUpdateApi = computed(() => props.adminMode ? updateAdminSemanticConfig : updateSemanticConfig)
const semanticConfigDeleteApi = computed(() => props.adminMode ? deleteAdminSemanticConfig : deleteSemanticConfig)
const semanticConfigToggleApi = computed(() => props.adminMode ? toggleAdminSemanticConfig : toggleSemanticConfig)
const semanticConfigBatchToggleApi = computed(() => props.adminMode ? batchToggleAdminSemanticConfig : batchToggleSemanticConfig)
const columnDictListApi = computed(() => props.adminMode ? getAdminDatasourceTableColumnDicts : getDatasourceTableColumnDicts)

const getConfigIndex = (config: SemanticConfigItem) => {
  return configs.value.findIndex(item => item.id === config.id)
}

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
    // 派生/复合指标无物理表归属，不随表作用域收缩被清除
    metrics: config.metrics.filter(item => item.metric_kind === 'derived' || item.metric_kind === 'composite' || nextAliasSet.has(getFieldTableAlias(item))),
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
      configs.value = (res.data?.configs || []).map(config => ({
        ...config,
        relationships: (config.relationships || []).map(normalizeSemanticRelationship),
      }))
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
    const { value } = await ElMessageBox.prompt('请输入语义模型名称', '新建语义模型', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValue: '',
      inputPlaceholder: '请输入语义模型名称',
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

const generateBasicConfigs = async () => {
  if (!props.adminMode || generatingBasicConfigs.value || !props.datasourceId) return
  try {
    await ElMessageBox.confirm(
      `将根据当前 ${props.tables.length} 张物理表生成尚未存在的基础单表模型。新模型默认启用，可直接在会话中引用；生成结果只有字段骨架，建议复核后补充指标。已有模型不会被覆盖。`,
      '批量初始化',
      {
        confirmButtonText: '开始生成',
        cancelButtonText: '取消',
        type: 'warning',
        customClass: 'chatdata-message-box',
        modalClass: 'chatdata-message-box-overlay',
      },
    )
  } catch {
    return
  }

  generatingBasicConfigs.value = true
  try {
    const res = await generateAdminBasicSemanticConfigs(props.datasourceId)
    if (res.status !== 'success' || !res.data) {
      ElMessage.error(res.msg || '基础模型生成失败')
      return
    }
    await fetchConfigs()
    emit('configChange')
    const { created, skipped, failed } = res.data
    const message = `已生成 ${created} 个，跳过 ${skipped} 个，失败 ${failed} 个`
    if (failed > 0) {
      ElMessage.warning(message)
    } else {
      ElMessage.success(message)
    }
  } catch (err) {
    console.error('批量生成基础模型失败:', err)
    ElMessage.error('基础模型生成失败')
  } finally {
    generatingBasicConfigs.value = false
  }
}

// 保存配置：先校验（dry-run）后保存，与导入「先校验再确认」同一交互
const saveConfig = async (config: SemanticConfigItem) => {
  savingId.value = config.id
  try {
    // 1. 保存前校验：结构错误直接拦截；质量提示弹窗列出，由用户决定是否仍要保存
    const validateApi = props.adminMode ? validateAdminSemanticConfig : validateSemanticConfig
    const vres = await validateApi(props.datasourceId, config, config.id)
    if (vres.status !== 'success' || !vres.data) {
      ElMessage.error(vres.msg || '保存前校验失败')
      return
    }
    if (vres.data.structure_error) {
      ElMessage.error(vres.data.structure_error)
      return
    }
    const findings = vres.data.quality_findings || []
    // 与导入预览同一呈现（QualityFindingsList）；false=用户选择「去修改」
    if (findings.length && !(await confirmSaveQualityFindings(findings))) {
      return
    }
    // 2. 真正保存
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
  if (!(await ElMessageBox.confirm(`确定要删除"${configName}"吗？`, '提示', { type: 'warning' }).catch(() => false))) return
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

// 批量启动 / 批量暂停入口：目标状态由按钮决定，弹窗只负责选模型
const openBatchToggle = (enabled: boolean) => {
  if (batchToggling.value || configs.value.length === 0) return
  batchToggleEnabled.value = enabled
  batchToggleVisible.value = true
}

const applyBatchToggle = async (configIds: string[], enabled: boolean) => {
  if (batchToggling.value || !props.datasourceId || configIds.length === 0) return
  batchToggling.value = true
  try {
    const res = await semanticConfigBatchToggleApi.value(props.datasourceId, configIds, enabled)
    if (res.status !== 'success' || !res.data) {
      ElMessage.error(res.msg || '批量操作失败')
      return
    }
    await fetchConfigs()
    emit('configChange')
    const message = res.msg || (enabled ? '批量启用成功' : '批量禁用成功')
    if (res.data.updated_count > 0) {
      ElMessage.success(message)
    } else {
      ElMessage.warning(message)
    }
  } catch (err) {
    console.error('批量切换语义模型启用状态失败:', err)
    ElMessage.error('批量操作失败')
  } finally {
    batchToggling.value = false
  }
}

// 更新配置（本地）
const updateConfig = (configId: string, updates: Partial<SemanticConfigItem>) => {
  configs.value = configs.value.map(c => c.id === configId ? { ...c, ...updates } : c)
}

// 监听数据源变化
watch(() => props.datasourceId, fetchConfigs, { immediate: true })
// 模型列表同步给宿主（后台顶栏下拉选项）
watch(configs, (list) => {
  emit('configListChange', list)
}, { deep: true, immediate: true })
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
type EditingType = 'relationship' | null
const editingType = ref<EditingType>(null)
const editingIndex = ref(-1)
const editingConfigId = ref<string | null>(null)

// 编辑中的数据
const editingRelationship = ref<SemanticRelationship | null>(null)

const fieldRows = ref<FieldConfigRow[]>([])
const fieldRowsSnapshot = ref('')
const fieldRowsConfigId = ref<string | null>(null)
const showFieldPhysicalDetails = ref(false)

/** 已勾选参与维度的行数：维度区副标题「已选 X/Y」用；未勾选行不写入 dimensions，仅占位展示 */
const dimensionFieldCount = computed(() => fieldRows.value.filter(row => row.isDimension).length)

// 默认维度勾选：会话内记录「尚未持久化过维度配置」的表别名，其物理列默认勾选为维度；
// 应用字段设置后按持久化的 dimensions 推导勾选态，默认集清空（机制沿用 9772620 之前的口径）
const defaultDimensionScopeAliases = ref<Record<string, string[]>>({})
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

const showAddFieldDialog = ref(false)
// 弹窗作用域：field=字段配置（维度变体），metric=指标（原子/派生/复合）
const addFieldScope = ref<'field' | 'metric'>('field')
const dimensionSectionCollapsed = ref(false)
const metricSectionCollapsed = ref(false)
const previewingTableName = ref('')
const showTablePreviewDialog = ref(false)
const tablePreviewTarget = ref('')
const showColumnDictEditDialog = ref(false)
const columnDictTarget = ref({ tableName: '', columnName: '' })
const columnDictVisibleMap = ref<Record<string, boolean>>({})
const columnDictEnumVisibleMap = ref<Record<string, boolean>>({})
let columnDictLoadSeq = 0
const addFieldForm = ref<AddFieldForm>({ tableAlias: '', column: '', fieldName: '', displayName: '', mode: 'metric' })
const addFieldDefaultDisplayName = ref('')
const showExpressionEditorDialog = ref(false)
const editingExpressionRowKey = ref('')
const showMetricExpandDialog = ref(false)
const metricExpandSourceIndex = ref(-1)
const metricExpandOptions = ref<MetricExpandOption[]>([])
const showBusinessNotesDialog = ref(false)
const buildFieldRowsEditSignature = (rows: FieldConfigRow[]) => JSON.stringify(rows.map(row => ({
  key: row.key,
  table: row.table,
  tableAlias: row.tableAlias,
  column: row.column,
  sourceColumn: row.sourceColumn,
  displayName: row.displayName,
  description: row.description,
  metadataSupplement: row.metadataSupplement,
  logicalType: row.logicalType,
  expression: row.expression,
  isDimension: row.isDimension,
  isCustom: row.isCustom,
})))
const isFieldRowsDirty = computed(() => buildFieldRowsEditSignature(fieldRows.value) !== fieldRowsSnapshot.value)
const isSavingActiveConfig = computed(() => Boolean(activeConfig.value && savingId.value === activeConfig.value.id))

const getFieldRowEffectiveDisplayName = (row: FieldConfigRow) => {
  return ((row.displayName || '').trim() || row.column).trim()
}

const duplicateFieldDisplayNameSet = computed(() => {
  const nameCounts = new Map<string, number>()
  const countName = (value?: string) => {
    const name = (value || '').trim()
    if (!name) return
    nameCounts.set(name, (nameCounts.get(name) || 0) + 1)
  }
  // 仅统计参与维度的行：未勾选行不写入 dimensions，不参与重名冲突
  fieldRows.value.forEach(row => {
    if (row.isDimension) countName(getFieldRowEffectiveDisplayName(row))
  })
  ;(activeConfig.value?.metrics || []).forEach(metric => countName(metric.name))
  return new Set(
    Array.from(nameCounts.entries())
      .filter(([, count]) => count > 1)
      .map(([name]) => name)
  )
})

const getFieldRowDisplayNameError = (row: FieldConfigRow) => {
  const displayName = getFieldRowEffectiveDisplayName(row)
  if (!displayName) return '显示名称不能为空'
  return duplicateFieldDisplayNameSet.value.has(displayName) ? `显示名称重复：${displayName}` : ''
}

const activeFieldRowsValidationError = computed(() => {
  const duplicateName = Array.from(duplicateFieldDisplayNameSet.value)[0]
  return duplicateName ? `字段显示名称重复：${duplicateName}` : ''
})

const activeConfigSaveDisabledReason = computed(() => {
  if (!activeConfig.value) return '暂无语义模型'
  if (isSavingActiveConfig.value) return '正在保存'
  if (!activeConfig.value.name) return '请填写语义模型名称'
  if (editingType.value !== null) return '请先完成当前编辑'
  if (activeFieldRowsValidationError.value) return activeFieldRowsValidationError.value
  if (!isActiveConfigDirty.value && !isFieldRowsDirty.value) return '没有需要保存的修改'
  if (!canManageConfig(activeConfig.value)) return '没有权限修改该语义模型'
  return ''
})

const canSaveActiveConfig = computed(() => !activeConfigSaveDisabledReason.value)

const hasPhysicalColumn = (tableName: string, columnName: string) => {
  const table = props.tables.find(t => t.table_name === tableName)
  return !!table?.columns?.some(col => col.name === columnName)
}

const validateFieldRowsBeforeApply = () => {
  const usedNames = new Set<string>()
  for (const row of fieldRows.value) {
    if (!row.isDimension) continue
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
    if (row.logicalType === 'enum' && !columnDictEnumVisibleMap.value[getFieldColumnDictKey(row)]) {
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
  editingRelationship.value = null
}

// ============ 派生/复合指标操作（复用新增字段弹窗的 derived/composite 模式） ============
const editingDerivedMetric = ref<SemanticMetric | null>(null)
const editingDerivedMetricIndex = ref(-1)

const derivedMetricConfig = computed(() => configs.value.find(c => c.id === activeConfig.value?.id) || null)

const metricKindLabel = (metric: SemanticMetric) =>
  metric.metric_kind === 'composite' ? '复合指标' : metric.metric_kind === 'derived' ? '派生指标' : '原子指标'

const isAtomicMetric = (metric: SemanticMetric) => !metric.metric_kind

// 统一指标管理：原子/派生/复合全部列出
const metricEntries = computed(() => {
  const metrics = derivedMetricConfig.value?.metrics || []
  return metrics.map((metric, index) => ({ metric, index }))
})

const aggregateMetricCandidates = computed(() => {
  // 复合指标无聚合表达式，天然被表达式检查排除；派生指标显式排除
  return (derivedMetricConfig.value?.metrics || []).filter(m => {
    if (m.metric_kind === 'derived' || m.metric_kind === 'composite') return false
    return Boolean(parseAggFuncFromExpression(m.expression || ''))
  })
})

const timeDimensionCandidates = computed(() => {
  return (derivedMetricConfig.value?.dimensions || []).filter(d => d.logical_type === 'date' || d.logical_type === 'datetime')
})

const filterDimensionCandidates = computed(() => {
  return (derivedMetricConfig.value?.dimensions || []).map(d => ({ name: d.name }))
})

const derivedMetricDefinition = (metric: SemanticMetric) => {
  if (metric.metric_kind === 'composite') return metric.formula || ''
  const parts: string[] = []
  if (metric.base_metric) parts.push(`指标[${metric.base_metric}]`)
  const period = metric.period
  if (period && period.kind) {
    if (period.kind === 'last_n_days') parts.push(`近${period.n || 0}天`)
    else if (period.kind === 'between') parts.push(`${period.start || '?'}~${period.end || '?'}`)
    else if (period.kind === 'today') parts.push('今日')
    else if (period.kind === 'yesterday') parts.push('昨日')
    else if (period.kind === 'this_week') parts.push('本周')
    else if (period.kind === 'this_month') parts.push('本月')
  }
  ;(metric.filters || []).forEach(item => {
    const valueText = Array.isArray(item.value) ? item.value.join('、') : String(item.value ?? '')
    if (item.dimension) parts.push(`${item.dimension}${item.operator || '='}${valueText}`)
  })
  return parts.join(' · ')
}

const resetEditingDerivedState = () => {
  editingDerivedMetric.value = null
  editingDerivedMetricIndex.value = -1
}

const openEditDerivedMetric = (metric: SemanticMetric, index: number) => {
  editingDerivedMetric.value = metric
  editingDerivedMetricIndex.value = index
  addFieldScope.value = 'metric'
  addFieldForm.value.mode = metric.metric_kind === 'composite' ? 'composite' : 'derived'
  addFieldForm.value.displayName = metric.name || ''
  showAddFieldDialog.value = true
}

// ============ 原子指标编辑（走 AddFieldDialog 的 metric 模式，直写 config.metrics） ============
const editingAtomicMetric = ref<SemanticMetric | null>(null)
const editingAtomicMetricIndex = ref(-1)

const resetEditingAtomicState = () => {
  editingAtomicMetric.value = null
  editingAtomicMetricIndex.value = -1
}

const openEditAtomicMetric = (metric: SemanticMetric, index: number) => {
  editingAtomicMetric.value = metric
  editingAtomicMetricIndex.value = index
  addFieldScope.value = 'metric'
  addFieldForm.value.mode = 'metric'
  addFieldForm.value.tableAlias = getFieldTableAlias(metric)
  addFieldForm.value.column = atomicMetricColumn(metric)
  addFieldForm.value.fieldName = normalizeFieldName(metric.field_name || '')
  addFieldForm.value.expression = metric.expression || ''
  addFieldForm.value.displayName = metric.name || ''
  resetEditingDerivedState()
  showAddFieldDialog.value = true
}

const confirmAtomicMetric = () => {
  const config = derivedMetricConfig.value
  if (!config) return
  const entry = addFieldTables.value.find(item => item.alias === addFieldForm.value.tableAlias)
  const columnName = addFieldForm.value.column
  const fieldName = normalizeFieldName(addFieldForm.value.fieldName)
  const expression = (addFieldForm.value.expression || '').trim()
  if (!entry || !columnName) {
    ElMessage.warning('请选择表和字段')
    return
  }
  if (!expression) {
    ElMessage.warning('请填写表达式')
    return
  }
  const previousName = editingAtomicMetricIndex.value >= 0 ? editingAtomicMetric.value?.name || '' : ''
  const displayName = (addFieldForm.value.displayName || '').trim()
  if (!displayName) {
    ElMessage.warning('请填写指标名称')
    return
  }
  if (isDisplayNameTaken(displayName, previousName)) {
    ElMessage.warning(`指标名称重复：${displayName}`)
    return
  }
  const table = getScopeTableInfo(entry)
  const columnInfo = table?.columns?.find(c => c.name === columnName)
  const columnType = columnInfo?.mysql_type || columnInfo?.type || ''
  const metric: SemanticMetric = editingAtomicMetricIndex.value >= 0
    ? {
        ...editingAtomicMetric.value,
        name: displayName,
        field_name: fieldName,
        table: entry.table,
        table_alias: entry.alias,
        expression,
        data_type: getMetricDataType(columnType),
      }
    : {
        name: displayName,
        field_name: fieldName,
        table: entry.table,
        table_alias: entry.alias,
        expression,
        data_type: getMetricDataType(columnType),
        filter: '',
        metadata_supplement: '',
      }
  metric.description = (addFieldForm.value.description || '').trim() // 备注统一取弹窗表单；回填/重置由弹窗自理
  const isEdit = editingAtomicMetricIndex.value >= 0
  const newMetrics = [...config.metrics]
  if (isEdit) {
    newMetrics[editingAtomicMetricIndex.value] = metric
  } else {
    newMetrics.push(metric)
  }
  updateConfig(config.id, { metrics: newMetrics })
  resetEditingAtomicState()
  showAddFieldDialog.value = false
  ElMessage.success(isEdit ? '原子指标已更新' : '原子指标已创建，保存后生效')
}

const deleteMetric = async (index: number) => {
  const config = derivedMetricConfig.value
  if (!config) return
  const metric = config.metrics[index]
  if (!metric) return
  if (!(await ElMessageBox.confirm(`确定要删除这个${metricKindLabel(metric)}吗？`, '提示', { type: 'warning' }).catch(() => false))) return
  const newMetrics = [...config.metrics]
  newMetrics.splice(index, 1)
  updateConfig(config.id, { metrics: newMetrics })
}

const confirmDerivedField = (metric: SemanticMetric) => {
  const config = derivedMetricConfig.value
  if (!config) return
  const previousName = editingDerivedMetricIndex.value >= 0 ? editingDerivedMetric.value?.name || '' : ''
  const conflictingNames = [...config.metrics.map(m => m.name), ...config.dimensions.map(d => d.name)]
    .filter(n => n && n !== previousName)
  if (conflictingNames.includes(metric.name)) {
    ElMessage.warning(`指标名称重复：${metric.name}`)
    return
  }
  const newMetrics = [...config.metrics]
  if (editingDerivedMetricIndex.value === -1) {
    newMetrics.push(metric)
  } else {
    newMetrics[editingDerivedMetricIndex.value] = metric
  }
  updateConfig(config.id, { metrics: newMetrics })
  resetEditingDerivedState()
  ElMessage.success(`${metricKindLabel(metric)}已保存`)
}

// ============ 关系操作 ============
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
    relationship_type: 'one_to_one',
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
    relationship_type: normalizeSemanticRelationship(rel).relationship_type,
    left_table: leftEntry?.table || rel.left_table,
    left_alias: leftEntry?.alias || leftAlias,
    right_table: rightEntry?.table || rel.right_table,
    right_alias: rightEntry?.alias || rightAlias,
    join_conditions: getRelationshipConditions(rel).map(c => ({ ...c }))
  }
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

const relationshipEditorScopeEntries = computed(() => {
  const configId = editingConfigId.value || activeConfig.value?.id || ''
  return getScopeEntries(configId).map(entry => ({
    alias: entry.alias,
    table: entry.table,
    label: getScopeEntryLabel(entry),
  }))
})

const relationshipEditorColumnsByAlias = computed(() => {
  const configId = editingConfigId.value || activeConfig.value?.id || ''
  const result: Record<string, string[]> = {}
  getScopeEntries(configId).forEach(entry => {
    result[entry.alias] = getColumnsByScopeAlias(configId, entry.alias).map(col => col.name)
  })
  return result
})

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
    relationship_type: 'one_to_one',
    join_type: 'left',
  }
  showRelationshipEditor.value = true
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

const hasMultiTables = (configId: string) => {
  return getScopeEntries(configId).length > 1
}

const getFirstPhysicalColumnFromExpression = (tableName: string, expression: string) => {
  const columns = extractExpressionColumns(expression)
  return columns.find(column => hasPhysicalColumn(tableName, column)) || ''
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

const buildFieldRowDefaultDisplayName = (row: Pick<FieldConfigRow, 'table' | 'tableAlias' | 'column' | 'sourceColumn' | 'columnComment'>) => {
  const sourceColumn = row.sourceColumn || row.column
  return buildAliasAwareDefaultDisplayName({ table: row.table, alias: getFieldRowAlias(row) }, sourceColumn, row.columnComment || getTableColumnComment(row.table, sourceColumn))
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

// 指标域唯一性辅助：字段名以 config.metrics（原子指标）为准，显示名称覆盖维度+指标全域
const isMetricFieldNameTaken = (tableAlias: string, fieldName: string) => {
  const normalized = normalizeFieldName(fieldName)
  if (!normalized) return false
  return (derivedMetricConfig.value?.metrics || []).some(metric => {
    if (metric.metric_kind || getFieldTableAlias(metric) !== tableAlias) return false
    return normalizeFieldName(metric.field_name || '') === normalized
  })
}

const getUniqueMetricFieldName = (tableAlias: string, baseColumn: string) => {
  const base = normalizeFieldName(baseColumn) || 'metric'
  if (!isMetricFieldNameTaken(tableAlias, base)) return base
  let index = 1
  let candidate = `${base}_${index}`
  while (isMetricFieldNameTaken(tableAlias, candidate)) {
    index += 1
    candidate = `${base}_${index}`
  }
  return candidate
}

const isDisplayNameTaken = (displayName: string, exceptName = '') => {
  const name = displayName.trim()
  if (!name || name === exceptName) return false
  const config = derivedMetricConfig.value
  if (config) {
    if (config.metrics.some(metric => metric.name === name)) return true
    if (config.dimensions.some(dimension => dimension.name === name)) return true
  }
  return fieldRows.value.some(row => ((row.displayName || '').trim() || row.column) === name)
}

const addFieldTables = computed(() => {
  const configId = activeConfig.value?.id
  if (!configId) return []
  return getScopeEntries(configId)
})

const addFieldTableOptions = computed<AddFieldTableOption[]>(() =>
  addFieldTables.value.map(entry => {
    const table = getScopeTableInfo(entry)
    return {
      alias: entry.alias,
      table: entry.table,
      description: table?.description,
      columns: table?.columns || [],
    }
  })
)

const addFieldColumns = computed(() => {
  const entry = addFieldTableOptions.value.find(t => t.alias === addFieldForm.value.tableAlias)
  return entry?.columns || []
})

const openAddDimensionDialog = () => {
  if (addFieldTables.value.length === 0) {
    ElMessage.warning('请先在左侧选择数据表')
    return
  }
  const firstEntry = addFieldTables.value[0]
  if (!firstEntry) return
  addFieldScope.value = 'field'
  addFieldForm.value.tableAlias = firstEntry.alias
  addFieldForm.value.column = addFieldColumns.value[0]?.name || ''
  addFieldForm.value.fieldName = getUniqueCustomFieldName(addFieldForm.value.tableAlias, addFieldForm.value.column)
  const firstCol = addFieldColumns.value[0]
  addFieldDefaultDisplayName.value = firstCol ? buildAliasAwareDefaultDisplayName(firstEntry, firstCol.name, firstCol.description) : ''
  addFieldForm.value.displayName = addFieldDefaultDisplayName.value
  addFieldForm.value.mode = 'dimension'
  resetEditingDerivedState()
  resetEditingAtomicState()
  showAddFieldDialog.value = true
}

const openAddMetricDialog = () => {
  if (addFieldTables.value.length === 0) {
    ElMessage.warning('请先在左侧选择数据表')
    return
  }
  const firstEntry = addFieldTables.value[0]
  if (!firstEntry) return
  addFieldScope.value = 'metric'
  addFieldForm.value.tableAlias = firstEntry.alias
  addFieldForm.value.column = addFieldColumns.value[0]?.name || ''
  addFieldForm.value.fieldName = getUniqueMetricFieldName(addFieldForm.value.tableAlias, addFieldForm.value.column)
  addFieldForm.value.expression = addFieldForm.value.column
    ? defaultMetricExpression(firstEntry.table, addFieldForm.value.column)
    : ''
  const firstCol = addFieldColumns.value[0]
  addFieldDefaultDisplayName.value = firstCol ? buildAliasAwareDefaultDisplayName(firstEntry, firstCol.name, firstCol.description) : ''
  addFieldForm.value.displayName = ''
  addFieldForm.value.mode = 'metric'
  resetEditingDerivedState()
  resetEditingAtomicState()
  showAddFieldDialog.value = true
}

// 原子指标默认表达式：数字列 SUM、其余 COUNT
const defaultMetricExpression = (tableName: string, columnName: string) =>
  buildMetricExpression(isNumericColumnType(getTableColumnType(tableName, columnName)) ? 'SUM' : 'COUNT', columnName)

const handleAddFieldTableChange = () => {
  const entry = addFieldTables.value.find(item => item.alias === addFieldForm.value.tableAlias)
  addFieldForm.value.column = addFieldColumns.value[0]?.name || ''
  addFieldForm.value.fieldName = addFieldScope.value === 'metric'
    ? getUniqueMetricFieldName(addFieldForm.value.tableAlias, addFieldForm.value.column)
    : getUniqueCustomFieldName(addFieldForm.value.tableAlias, addFieldForm.value.column)
  if (addFieldScope.value === 'metric') {
    addFieldForm.value.expression = entry && addFieldForm.value.column
      ? defaultMetricExpression(entry.table, addFieldForm.value.column)
      : ''
  }
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
  addFieldForm.value.fieldName = addFieldScope.value === 'metric'
    ? getUniqueMetricFieldName(addFieldForm.value.tableAlias, col.name)
    : getUniqueCustomFieldName(addFieldForm.value.tableAlias, col.name)
  if (addFieldScope.value === 'metric') {
    addFieldForm.value.expression = defaultMetricExpression(entry.table, col.name)
  }
  // 仅当为空或仍是系统默认名时，跟随字段自动同步；人工改过的显示名不覆盖。
  if (!currentDisplayName || currentDisplayName === addFieldDefaultDisplayName.value) {
    addFieldForm.value.displayName = nextDefaultDisplayName
  }
  addFieldDefaultDisplayName.value = nextDefaultDisplayName
}

const confirmAddField = () => {
  if (addFieldScope.value === 'metric') {
    if (addFieldForm.value.mode === 'metric') {
      confirmAtomicMetric()
    }
    return
  }
  confirmAddDimension()
}

const confirmAddDimension = () => {
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
  if (isDisplayNameTaken(displayName)) {
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
    displayName,
    columnComment,
    description: '',
    metadataSupplement: '',
    logicalType: inferLogicalType(columnType),
    expression: quoteIdentifier(columnName),
    isDimension: true,
    isCustom: true,
  })
  void refreshFieldColumnDictVisibleMap()
  showAddFieldDialog.value = false
  addFieldForm.value.fieldName = ''
  addFieldForm.value.displayName = ''
  addFieldDefaultDisplayName.value = ''
}

const canExpandMetricEntry = (metric: SemanticMetric) => {
  if (metric.metric_kind) return false
  const parsed = parseSimpleMetricExpression(metric.expression || '')
  if (!parsed || !EXPANDABLE_AGG_FUNCS.includes(parsed.aggFunc)) return false
  return isNumericColumnType(getTableColumnType(metric.table, normalizeExpressionColumn(parsed.column)))
}

const metricAggExists = (tableAlias: string, sourceColumn: string, aggFunc: string) => {
  return (derivedMetricConfig.value?.metrics || []).some(metric => {
    if (metric.metric_kind || getFieldTableAlias(metric) !== tableAlias) return false
    const parsed = parseSimpleMetricExpression(metric.expression || '')
    if (!parsed) return false
    return parsed.aggFunc === aggFunc && normalizeExpressionColumn(parsed.column) === normalizeExpressionColumn(sourceColumn)
  })
}

const openMetricExpandDialog = (metric: SemanticMetric, index: number) => {
  if (!canExpandMetricEntry(metric)) {
    ElMessage.warning('仅支持数字字段的 SUM/AVG/MAX/MIN 简单指标扩展')
    return
  }
  const parsed = parseSimpleMetricExpression(metric.expression || '')
  const sourceColumn = normalizeExpressionColumn(parsed?.column || '')
  const baseDisplayName = (metric.name || getTableColumnDisplayName(metric.table, sourceColumn) || sourceColumn).trim()
  const metricAlias = getFieldTableAlias(metric)
  metricExpandOptions.value = EXPANDABLE_AGG_FUNCS
    .filter(aggFunc => aggFunc !== parsed?.aggFunc)
    .map(aggFunc => {
      const exists = metricAggExists(metricAlias, sourceColumn, aggFunc)
      return {
        aggFunc,
        label: `${aggFunc}(${sourceColumn})`,
        fieldName: getUniqueMetricFieldName(metricAlias, `${sourceColumn}_${aggFunc.toLowerCase()}`),
        displayName: buildMetricExpandDisplayName(baseDisplayName, aggFunc),
        expression: buildMetricExpression(aggFunc, sourceColumn),
        exists,
        selected: !exists,
      }
    })
  metricExpandSourceIndex.value = index
  showMetricExpandDialog.value = true
}

const confirmMetricExpand = () => {
  const config = derivedMetricConfig.value
  const sourceMetric = config?.metrics[metricExpandSourceIndex.value]
  if (!config || !sourceMetric) {
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
      ElMessage.warning('请填写指标名称')
      return
    }
  }
  const sourceAlias = getFieldTableAlias(sourceMetric)
  const sourceColumn = atomicMetricColumn(sourceMetric)
  const usedFieldNames = new Set<string>()
  for (const option of selectedOptions) {
    const fieldKey = `${sourceAlias}.${option.fieldName}`
    if (usedFieldNames.has(fieldKey)) {
      ElMessage.warning(`字段名重复：${fieldKey}`)
      return
    }
    usedFieldNames.add(fieldKey)
    if (isMetricFieldNameTaken(sourceAlias, option.fieldName)) {
      ElMessage.warning(`字段名重复：${fieldKey}`)
      return
    }
  }
  const selectedDisplayNames = new Set<string>()
  for (const option of selectedOptions) {
    if (selectedDisplayNames.has(option.displayName)) {
      ElMessage.warning(`指标名称重复：${option.displayName}`)
      return
    }
    selectedDisplayNames.add(option.displayName)
  }
  const duplicatedName = selectedOptions.find(option => isDisplayNameTaken(option.displayName))
  if (duplicatedName) {
    ElMessage.warning(`指标名称重复：${duplicatedName.displayName}`)
    return
  }
  const columnType = getTableColumnType(sourceMetric.table, sourceColumn)
  const newMetrics = [...config.metrics]
  selectedOptions.forEach(option => {
    newMetrics.push({
      name: option.displayName,
      field_name: option.fieldName,
      table: sourceMetric.table,
      table_alias: sourceAlias,
      expression: option.expression,
      data_type: getMetricDataType(columnType),
      filter: '',
      description: '',
      metadata_supplement: '',
    })
  })
  updateConfig(config.id, { metrics: newMetrics })
  void refreshFieldColumnDictVisibleMap()
  showMetricExpandDialog.value = false
  metricExpandSourceIndex.value = -1
  metricExpandOptions.value = []
  ElMessage.success(`已扩展 ${selectedOptions.length} 个原子指标，保存后生效`)
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
  if (isDisplayNameTaken(displayName)) {
    ElMessage.warning(`指标名称重复：${displayName}`)
    return
  }
  const config = derivedMetricConfig.value
  if (!config) {
    ElMessage.warning('未找到当前模型配置')
    return
  }
  const columnType = getTableColumnType(tableName, sourceColumn)
  const metric: SemanticMetric = {
    name: displayName,
    field_name: getUniqueMetricFieldName(entry.alias, fieldName),
    table: tableName,
    table_alias: entry.alias,
    expression: expression || buildMetricExpression(isNumericColumnType(columnType) ? 'SUM' : 'COUNT', sourceColumn),
    data_type: getMetricDataType(columnType),
    filter: '',
    description: '',
    metadata_supplement: '',
  }
  updateConfig(config.id, { metrics: [...config.metrics, metric] })
  void refreshFieldColumnDictVisibleMap()
  ElMessage.success('已采纳到指标列表，保存后生效')
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
    displayName,
    columnComment: getTableColumnComment(tableName, columnName),
    description: '',
    metadataSupplement: '',
    logicalType: suggestion.logical_type || inferLogicalType(columnType),
    expression: (suggestion.expression || quoteIdentifier(columnName)).trim(),
    isDimension: true,
    isCustom: true,
  })
  void refreshFieldColumnDictVisibleMap()
  ElMessage.success('已采纳到字段配置草稿，保存后生效')
}

const removeFieldRow = (row: FieldConfigRow) => {
  fieldRows.value = fieldRows.value.filter(item => item.key !== row.key)
}

const previewTableData = (tableName: string) => {
  if (!props.datasourceId || !tableName) return
  tablePreviewTarget.value = tableName
  showTablePreviewDialog.value = true
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
      const res = await columnDictListApi.value(datasourceId, tableName)
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

const openColumnDict = (row: FieldConfigRow) => {
  const tableName = String(row.table || '').trim()
  const columnName = getFieldPhysicalColumn(row)
  if (!props.datasourceId || !tableName || !columnName || row.isMissingPhysical) {
    ElMessage.warning('无法定位物理字段')
    return
  }
  columnDictTarget.value = { tableName, columnName }
  showColumnDictEditDialog.value = true
}

const handleColumnDictSaved = async () => {
  await refreshFieldColumnDictVisibleMap()
}

// 字段配置 = 维度勾选清单：作用域内每个物理列一行（一行=一列），勾选状态决定是否写入 dimensions；
// 勾选态 = 持久化的 dimensions 有该物理列条目，或该表别名尚未持久化过配置（会话内默认全勾）；
// 指标不回挂到字段行，统一在「指标」区管理。
// 字段行构建主体在 semantic/panelModelUtils.ts，此处注入组件上下文
const buildFieldRows = (configId: string) => buildFieldRowsForConfig(configId, {
  configs: configs.value,
  getScopeEntries,
  getScopeTableInfo,
  getDefaultDimensionAliasSet,
  getTableColumnComment,
  getTableColumnType,
})

const mergeFieldRowsWithDraft = (nextRows: FieldConfigRow[]) => {
  return mergeFieldRowsWithDraftUtil(nextRows, {
    scopeEntries: getScopeEntries(activeConfig.value?.id || ''),
    currentRows: fieldRows.value,
  })
}

const loadFieldRows = (configId: string) => {
  fieldRows.value = buildFieldRows(configId)
  fieldRowsConfigId.value = configId
  fieldRowsSnapshot.value = buildFieldRowsEditSignature(buildFieldRows(configId))
  void refreshFieldColumnDictVisibleMap()
}

// 应用字段设置：仅勾选行写入 dimensions，指标由「指标」区即时维护，此处不触碰 metrics
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
        expression: (r.expression || quoteIdentifier(r.sourceColumn || r.column)).trim(),
        logical_type: r.logicalType || 'text',
        description: r.description || '',
        metadata_supplement: (r.metadataSupplement || '').trim(),
      }
      return copyDimensionRuntimeConfig(dimension, existingDimensionMap.get(buildDimensionPreserveKey(dimension)))
    })

  updateConfig(configId, { dimensions: nextDimensions })
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

// 勾选/取消维度：指标由「指标」区独立管理，此处无 isMetric 互斥；补默认表达式兜底
const handleDimensionToggle = (row: FieldConfigRow, checked: boolean) => {
  row.isDimension = checked
  if (checked && !row.expression) {
    row.expression = quoteIdentifier(row.sourceColumn || row.column)
  }
}

const handleExpressionInput = (row: FieldConfigRow, expression: string) => {
  row.expression = expression
}

const openExpressionEditorDialog = (row: FieldConfigRow) => {
  if (row.isMissingPhysical) return
  editingExpressionRowKey.value = row.key
  showExpressionEditorDialog.value = true
}

const editingExpressionRow = computed(() => {
  return fieldRows.value.find(item => item.key === editingExpressionRowKey.value) || null
})

const saveExpressionEditor = (expression: string) => {
  const row = editingExpressionRow.value
  if (row) {
    handleExpressionInput(row, expression)
  }
  editingExpressionRowKey.value = ''
}

const openBusinessNotesDialog = () => {
  if (!activeConfig.value) return
  showBusinessNotesDialog.value = true
}

const saveBusinessNotes = (businessNotes: string) => {
  if (!activeConfig.value) return
  updateConfig(activeConfig.value.id, { business_notes: businessNotes })
}

const openOssieImport = () => {
  ossieImportVisible.value = true
}

// 宿主顶栏动作入口（批量初始化 / 批量启停 / 新建 / Ossie 导入）
defineExpose({
  generateBasicConfigs,
  openBatchToggle,
  createConfig,
  openOssieImport,
})

</script>

<template>
  <div class="h-full flex flex-col bg-white">
    <!-- 加载状态 -->
    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-400 text-2xl">
      <i class="fa-solid fa-spinner fa-spin"></i>
    </div>

    <!-- 配置列表 -->
    <div v-else class="flex-1 min-h-0 p-4">
      <div v-if="configs.length === 0" class="flex flex-col items-center justify-center p-12 border-2 border-dashed border-gray-200 rounded-xl text-center">
        <i class="fa-solid fa-cube text-4xl text-blue-500 opacity-30 mb-3"></i>
        <p class="text-sm text-gray-500 m-0 mb-1">暂无语义模型</p>
        <span class="text-xs text-gray-400">{{ props.adminMode ? '手动创建模型，或根据物理表一键初始化基础模型' : '点击上方加号创建第一个语义模型' }}</span>
        <button
          v-if="props.adminMode"
          class="mt-4 h-9 flex items-center gap-2 border border-blue-200 rounded-md bg-blue-50 px-4 text-[13px] text-blue-600 hover:bg-blue-100 disabled:opacity-60 disabled:cursor-not-allowed"
          :disabled="generatingBasicConfigs || props.tables.length === 0"
          @click="generateBasicConfigs"
        >
          <i :class="['fa-solid', generatingBasicConfigs ? 'fa-spinner fa-spin' : 'fa-layer-group']"></i>
          批量初始化
        </button>
      </div>

      <div v-else class="h-full min-h-0">
        <div v-if="activeConfig" class="h-full min-h-0 border border-gray-200 rounded-xl bg-white overflow-hidden flex flex-col">
          <div class="px-4 py-2 border-b border-gray-100 bg-gray-50">
            <!-- 单行：当前模型（名称 + 业务说明 + 状态与操作）；模型选择与批量管理由宿主顶栏承载 -->
            <div class="flex flex-wrap items-center gap-2">
              <input
                type="text"
                :value="activeConfig.name"
                class="max-w-full shrink-0 px-3 py-1.5 border border-gray-200 rounded-md bg-white text-[13px] text-gray-700 outline-none focus:border-blue-500"
                :class="sourceTablePanelCollapsed ? 'min-w-[200px] flex-1' : ''"
                :style="sourceTablePanelCollapsed ? undefined : { width: `${sourceTablePanelWidth + 20}px` }"
                placeholder="语义模型名称"
                :disabled="!canManageConfig(activeConfig)"
                @input="updateConfig(activeConfig.id, { name: ($event.target as HTMLInputElement).value })"
              />
              <button
                class="px-3 py-1.5 rounded-md text-xs whitespace-nowrap border transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed"
                :class="activeConfig.business_notes
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-600 hover:bg-emerald-100'
                  : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50'"
                :disabled="!canManageConfig(activeConfig)"
                title="业务说明：建模背景、口径边界等，会注入 agent 上下文"
                @click="openBusinessNotesDialog"
              >
                <i class="fa-regular fa-note-sticky mr-1"></i>业务说明{{ activeConfig.business_notes ? '' : '（未填写）' }}
              </button>
              <div class="flex flex-wrap items-center gap-2 ml-auto shrink-0">
                <button
                  v-if="props.adminMode"
                  class="px-3 py-1.5 rounded-md text-xs whitespace-nowrap border border-gray-200 bg-white text-gray-600 transition-all duration-150 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="checkingHealth"
                  title="校验语义模型（缓存表结构漂移 + 模型引用完整性）"
                  @click="handleCheckSemanticHealth"
                >
                  <i :class="['fa-solid mr-1', checkingHealth ? 'fa-spinner fa-spin' : 'fa-shield-halved']"></i>校验
                </button>
                <button
                  :class="[
                    'px-3 py-1.5 border-none rounded-md text-xs whitespace-nowrap transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
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
                  v-if="SHOW_EVALUATION_ENTRY"
                  class="px-3 py-1.5 rounded-md text-xs whitespace-nowrap border border-gray-200 bg-white text-gray-600 transition-all duration-150 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="!activeConfig || !canManageConfig(activeConfig)"
                  @click="openHistoryLearningDialog"
                >
                  <i class="fa-solid fa-graduation-cap mr-1"></i>历史学习
                </button>
                <button
                  v-if="SHOW_EVALUATION_ENTRY"
                  class="px-3 py-1.5 rounded-md text-xs whitespace-nowrap border border-gray-200 bg-white text-gray-600 transition-all duration-150 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="!canOpenEvaluation"
                  @click="openEvaluateDrawer"
                >
                  <i class="fa-solid fa-vial-circle-check mr-1"></i>评测
                </button>
                <button
                  :class="[
                    'px-3 py-1.5 border-none rounded-md text-xs whitespace-nowrap transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
                    canSaveActiveConfig ? 'bg-[#1677FF] text-white cursor-pointer hover:bg-[#0958D9]' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  ]"
                  :disabled="!canSaveActiveConfig"
                  :title="activeConfigSaveDisabledReason || '保存语义模型'"
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
                  class="px-3 py-1.5 rounded-md text-xs whitespace-nowrap border border-red-100 bg-white text-red-500 transition-all duration-150 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-white"
                  :disabled="!canManageConfig(activeConfig)"
                  title="删除语义模型"
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

              <div class="min-w-0 min-h-0 flex flex-col gap-3 overflow-y-auto">
                <div
                  class="border border-dashed border-gray-300 rounded-lg bg-gray-50/60 p-2.5 flex flex-col shrink-0"
                  @dragover.prevent
                  @drop.prevent="handleScopeDrop(activeConfig.id)"
                >
                  <div
                    class="flex items-center justify-between cursor-pointer select-none"
                    :class="modelConfigCollapsed ? '' : 'mb-1.5'"
                    :title="modelConfigCollapsed ? '展开模型配置' : '折叠模型配置'"
                    @click="modelConfigCollapsed = !modelConfigCollapsed"
                  >
                    <div class="text-xs font-medium text-gray-600">模型配置</div>
                    <div class="flex items-center gap-2">
                      <div class="text-[11px] text-gray-400">
                        {{ getScopeEntries(activeConfig.id).length }} 张表 · {{ activeConfig.relationships.length }} 条关系
                      </div>
                      <button
                        class="w-6 h-6 border border-gray-200 rounded bg-white text-gray-400 text-[11px] cursor-pointer hover:text-gray-600 hover:border-gray-300"
                        :title="modelConfigCollapsed ? '展开模型配置' : '折叠模型配置'"
                        @click.stop="modelConfigCollapsed = !modelConfigCollapsed"
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
                              <span class="shrink-0 pr-2 border-r border-blue-200 whitespace-nowrap">{{ formatRelationshipTypeLabel(item.rel.relationship_type) }}</span>
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

                <div class="border border-dashed border-gray-300 rounded-lg bg-gray-50/60 p-3 flex flex-col" :class="dimensionSectionCollapsed ? 'shrink-0' : ''">
                  <div class="flex items-center justify-between gap-3" :class="dimensionSectionCollapsed ? '' : 'sticky top-0 z-10 -mx-3 -mt-3 mb-2 px-3 pt-3 bg-gray-50 rounded-t-lg'">
                    <div
                      class="flex items-center gap-2 min-w-0 cursor-pointer select-none"
                      :title="dimensionSectionCollapsed ? '展开维度配置' : '折叠维度配置'"
                      @click="dimensionSectionCollapsed = !dimensionSectionCollapsed"
                    >
                      <div class="text-xs font-medium text-gray-600 shrink-0">维度配置</div>
                      <div class="text-[11px] text-gray-400 truncate">一行 = 一个物理字段，勾选决定是否参与维度 · 已选 {{ dimensionFieldCount }}/{{ fieldRows.length }}</div>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
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
                        class="h-7 px-2.5 border border-gray-200 rounded bg-white text-[11px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                        @click="openAddDimensionDialog"
                      >
                        <i class="fa-solid fa-plus mr-1"></i>新增维度
                      </button>
                      <button
                        class="w-6 h-6 border border-gray-200 rounded bg-white text-gray-400 text-[11px] cursor-pointer hover:text-gray-600 hover:border-gray-300"
                        :title="dimensionSectionCollapsed ? '展开维度配置' : '折叠维度配置'"
                        @click.stop="dimensionSectionCollapsed = !dimensionSectionCollapsed"
                      >
                        <i :class="dimensionSectionCollapsed ? 'fa-solid fa-chevron-down' : 'fa-solid fa-chevron-up'"></i>
                      </button>
                    </div>
                  </div>
                  <template v-if="!dimensionSectionCollapsed">
                  <div v-if="fieldRows.length === 0" class="min-h-[80px] border border-dashed border-gray-300 rounded-md bg-white flex flex-col items-center justify-center text-gray-400 text-xs">
                    <i class="fa-regular fa-table-cells-large mb-2"></i>
                    请先在左侧选择数据表
                  </div>
                  <div v-else class="overflow-auto rounded-md border border-gray-200 bg-white">
                    <table :class="[showFieldPhysicalDetails ? 'min-w-[2012px]' : 'min-w-[1612px]', 'w-full border-collapse text-[12px]']">
                      <thead class="bg-gray-50 text-gray-600">
                        <tr>
                          <th class="text-left px-3 py-2 border border-gray-200">表</th>
                          <th class="text-left px-3 py-2 border border-gray-200">字段</th>
                          <th v-if="showFieldPhysicalDetails" class="text-left px-3 py-2 border border-gray-200">类型</th>
                          <th v-if="showFieldPhysicalDetails" class="text-left px-3 py-2 border border-gray-200">列备注</th>
                          <th class="text-center px-3 py-2 border border-gray-200 w-[64px]" title="勾选后该字段参与维度；多表关联的重复字段（如两侧关联键）可取消勾选">维度</th>
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
                            <input
                              :checked="row.isDimension"
                              :disabled="row.isMissingPhysical"
                              type="checkbox"
                              class="cursor-pointer disabled:cursor-not-allowed"
                              :title="row.isMissingPhysical ? '物理字段不存在' : '是否将该字段作为维度参与查询'"
                              @change="handleDimensionToggle(row, ($event.target as HTMLInputElement).checked)"
                            />
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
                                :disabled="row.isMissingPhysical || !row.isDimension"
                                @input="handleExpressionInput(row, ($event.target as HTMLInputElement).value)"
                              />
                              <button
                                class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-500 hover:text-blue-500 hover:border-blue-300 disabled:bg-gray-50 disabled:text-gray-300 disabled:cursor-not-allowed"
                                :disabled="row.isMissingPhysical || !row.isDimension"
                                title="弹窗编辑表达式"
                                @click="openExpressionEditorDialog(row)"
                              >
                                <i class="fa-solid fa-pen"></i>
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
                              v-if="!row.isCustom"
                              class="h-7 w-20 border rounded bg-white text-[11px] disabled:bg-gray-50 disabled:text-gray-300 disabled:cursor-not-allowed"
                              :class="hasFieldColumnDict(row)
                                ? 'border-blue-200 text-blue-600 hover:border-blue-400 hover:bg-blue-50'
                                : 'border-gray-200 text-gray-600 hover:border-blue-200 hover:text-blue-600'"
                              :disabled="row.isMissingPhysical || !row.table || !getFieldPhysicalColumn(row)"
                              :title="row.isMissingPhysical
                                ? '物理字段不存在'
                                : props.adminMode
                                  ? (hasFieldColumnDict(row) ? `编辑 ${row.table}.${getFieldPhysicalColumn(row)} 的字段字典` : `为 ${row.table}.${getFieldPhysicalColumn(row)} 新增字段字典`)
                                  : `查看 ${row.table}.${getFieldPhysicalColumn(row)} 的字段字典`"
                              @click="openColumnDict(row)"
                            >
                              {{ props.adminMode ? (hasFieldColumnDict(row) ? '编辑' : '新增字典') : '查看' }}
                            </button>
                            <span v-else class="text-gray-300">-</span>
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <!-- 默认物理列行不可删：buildFieldRows 会按物理列无条件重建，删除无持久效果 -->
                            <button
                              v-if="row.isCustom || row.isMissingPhysical"
                              class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-400 hover:text-red-500 hover:border-red-200"
                              @click="removeFieldRow(row)"
                              :title="row.isCustom ? '删除自定义维度配置（该列还原为默认维度）' : '删除该维度配置'"
                            >
                              <i class="fa-solid fa-trash text-[11px]"></i>
                            </button>
                            <span v-else class="text-gray-300">-</span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  </template>
                </div>

                <div class="border border-dashed border-gray-300 rounded-lg bg-gray-50/60 p-2.5 flex flex-col shrink-0">
                  <div class="flex items-center justify-between gap-3" :class="metricSectionCollapsed ? '' : 'sticky top-0 z-10 -mx-2.5 -mt-2.5 mb-1.5 px-2.5 pt-2.5 bg-gray-50 rounded-t-lg'">
                    <div
                      class="flex items-center gap-2 min-w-0 cursor-pointer select-none"
                      :title="metricSectionCollapsed ? '展开指标管理' : '折叠指标管理'"
                      @click="metricSectionCollapsed = !metricSectionCollapsed"
                    >
                      <div class="text-xs font-medium text-gray-600 shrink-0">指标管理</div>
                      <div class="text-[11px] text-gray-400 truncate">原子 / 派生 / 复合指标统一管理 · {{ metricEntries.length }} 个</div>
                    </div>
                    <div class="flex items-center gap-2 shrink-0">
                      <button
                        class="h-7 px-2.5 border border-gray-200 rounded bg-white text-[11px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                        @click="openAddMetricDialog"
                      >
                        <i class="fa-solid fa-plus mr-1"></i>新建指标
                      </button>
                      <button
                        class="w-6 h-6 border border-gray-200 rounded bg-white text-gray-400 text-[11px] cursor-pointer hover:text-gray-600 hover:border-gray-300"
                        :title="metricSectionCollapsed ? '展开指标管理' : '折叠指标管理'"
                        @click.stop="metricSectionCollapsed = !metricSectionCollapsed"
                      >
                        <i :class="metricSectionCollapsed ? 'fa-solid fa-chevron-down' : 'fa-solid fa-chevron-up'"></i>
                      </button>
                    </div>
                  </div>
                  <div v-if="!metricSectionCollapsed">
                    <div v-if="metricEntries.length === 0" class="rounded-md border border-dashed border-gray-200 bg-white py-4 flex flex-col items-center justify-center text-gray-400 text-xs">
                      <i class="fa-regular fa-square-plus mb-1.5"></i>
                      暂无指标，点击「新建指标」创建原子 / 派生 / 复合指标
                    </div>
                    <div v-else class="overflow-auto rounded-md border border-gray-200 bg-white max-h-64">
                      <table class="w-full border-collapse text-[12px]">
                        <thead class="bg-gray-50 text-gray-600">
                          <tr>
                            <th class="text-left px-3 py-2 border border-gray-200">显示名称</th>
                            <th class="text-left px-3 py-2 border border-gray-200">口径定义</th>
                            <th class="text-left px-3 py-2 border border-gray-200">字段备注</th>
                            <th class="text-center px-3 py-2 border border-gray-200 w-[104px]">操作</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="entry in metricEntries" :key="`metric_${entry.index}`" class="hover:bg-blue-50/40">
                            <td class="px-3 py-2 border border-gray-200">
                              <div class="flex items-center gap-1.5">
                                <span class="text-gray-700">{{ entry.metric.name }}</span>
                                <span
                                  class="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                                  :class="entry.metric.metric_kind === 'composite'
                                    ? 'bg-violet-100 text-violet-600'
                                    : entry.metric.metric_kind === 'derived'
                                      ? 'bg-blue-100 text-blue-600'
                                      : 'bg-teal-100 text-teal-600'"
                                >{{ entry.metric.metric_kind === 'composite' ? '复合' : entry.metric.metric_kind === 'derived' ? '派生' : '原子' }}</span>
                              </div>
                            </td>
                            <td class="px-3 py-2 border border-gray-200">
                              <span
                                v-if="isAtomicMetric(entry.metric)"
                                class="text-gray-600 max-w-[520px] truncate block font-mono"
                                :title="entry.metric.expression"
                              >{{ entry.metric.expression || '-' }}</span>
                              <span
                                v-else
                                class="text-gray-600 max-w-[520px] truncate block"
                                :title="derivedMetricDefinition(entry.metric)"
                              >{{ derivedMetricDefinition(entry.metric) || '-' }}</span>
                            </td>
                            <td class="px-3 py-2 border border-gray-200">
                              <span class="text-gray-600 max-w-[200px] truncate block" :title="entry.metric.description || ''">{{ entry.metric.description || '-' }}</span>
                            </td>
                            <td class="px-3 py-2 border border-gray-200 text-center">
                              <div class="flex items-center justify-center gap-1">
                                <button
                                  v-if="canExpandMetricEntry(entry.metric)"
                                  class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-500 hover:text-blue-500 hover:border-blue-300"
                                  title="扩展聚合指标"
                                  @click="openMetricExpandDialog(entry.metric, entry.index)"
                                >
                                  <i class="fa-solid fa-layer-group text-[11px]"></i>
                                </button>
                                <button
                                  class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-500 hover:text-blue-500 hover:border-blue-300"
                                  :title="`编辑${metricKindLabel(entry.metric)}`"
                                  @click="isAtomicMetric(entry.metric)
                                    ? openEditAtomicMetric(entry.metric, entry.index)
                                    : openEditDerivedMetric(entry.metric, entry.index)"
                                >
                                  <i class="fa-solid fa-pen text-[11px]"></i>
                                </button>
                                <button
                                  class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-400 hover:text-red-500 hover:border-red-200"
                                  :title="`删除${metricKindLabel(entry.metric)}`"
                                  @click="deleteMetric(entry.index)"
                                >
                                  <i class="fa-solid fa-trash text-[11px]"></i>
                                </button>
                              </div>
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
    </div>

    <SemanticEvaluationDrawer
      v-model:visible="showEvaluateDrawer"
      :datasource-id="props.datasourceId"
      :config="activeConfig"
      :has-unsaved-semantic-config="hasUnsavedSemanticConfig"
      :admin-mode="props.adminMode"
    />

    <RelationshipEditorDialog
      v-model:visible="showRelationshipEditor"
      v-model:relationship="editingRelationship"
      :scope-entries="relationshipEditorScopeEntries"
      :columns-by-alias="relationshipEditorColumnsByAlias"
      @confirm="confirmRelationshipEditor"
      @cancel="closeRelationshipEditor"
    />

    <ExpressionEditorDialog
      v-model:visible="showExpressionEditorDialog"
      :row="editingExpressionRow"
      :datasource-type="props.datasourceType"
      scope="dimension"
      @save="saveExpressionEditor"
    />

    <BusinessNotesDialog
      v-model:visible="showBusinessNotesDialog"
      :config="activeConfig"
      :can-manage="canManageConfig(activeConfig)"
      @save="saveBusinessNotes"
    />

    <SemanticBatchToggleDialog
      v-model:visible="batchToggleVisible"
      :configs="configs"
      :enabled="batchToggleEnabled"
      @confirm="applyBatchToggle"
    />

    <SemanticHistoryLearningDialog
      v-model:visible="showHistoryLearningDialog"
      :datasource-id="props.datasourceId"
      :config="activeConfig"
      :admin-mode="props.adminMode"
      @accept-metric="acceptHistoryMetric"
      @accept-dimension="acceptHistoryDimension"
    />

    <AddFieldDialog
      v-model:visible="showAddFieldDialog"
      v-model:form="addFieldForm"
      :scope="addFieldScope"
      :tables="addFieldTableOptions"
      :aggregate-metrics="aggregateMetricCandidates"
      :time-dimensions="timeDimensionCandidates"
      :filter-dimensions="filterDimensionCandidates"
      :editing-derived="editingDerivedMetric"
      :editing-atomic="editingAtomicMetric"
      @table-change="handleAddFieldTableChange"
      @column-change="handleAddFieldColumnChange"
      @confirm="confirmAddField"
      @confirm-derived="confirmDerivedField"
    />

    <MetricExpandDialog
      v-model:visible="showMetricExpandDialog"
      v-model:options="metricExpandOptions"
      @confirm="confirmMetricExpand"
    />

    <TablePreviewDialog
      v-model:visible="showTablePreviewDialog"
      v-model:previewing-table-name="previewingTableName"
      :datasource-id="props.datasourceId"
      :table-name="tablePreviewTarget"
    />

    <ColumnDictEditDialog
      v-model:visible="showColumnDictEditDialog"
      :datasource-id="props.datasourceId"
      :table-name="columnDictTarget.tableName"
      :column-name="columnDictTarget.columnName"
      :admin-mode="props.adminMode"
      @saved="handleColumnDictSaved"
    />

    <OssieImportDialog
      v-model:visible="ossieImportVisible"
      :datasource-id="props.datasourceId"
      @imported="fetchConfigs"
    />

    <SemanticHealthModal v-model:visible="showHealthModal" :result="healthResult" />

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
  z-index: var(--z-page-overlay-popper) !important;
}

:global(.chatdata-semantic-config-select-popper .el-select-dropdown__item) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

</style>
