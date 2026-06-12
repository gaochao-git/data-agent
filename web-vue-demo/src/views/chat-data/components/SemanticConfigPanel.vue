<script setup lang="ts">
/**
 * 语义配置面板
 * 支持管理指标、维度、表关系的完整配置
 */
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getSemanticConfigs,
  createSemanticConfig,
  updateSemanticConfig,
  deleteSemanticConfig,
  toggleSemanticConfig,
  autoPrefillSemanticFields,
} from '../api'
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
  tables: TableInfo[]
}

const props = defineProps<Props>()
const emit = defineEmits<{
  close: []
}>()

// 配置列表
const configs = ref<SemanticConfigItem[]>([])
const loading = ref(true)
const expandedId = ref<string | null>(null)
const savingId = ref<string | null>(null)
const savedConfigSignatures = ref<Record<string, string>>({})
const tableScopes = ref<Record<string, string[]>>({})
const draggingTableName = ref('')
const tableSearchKeyword = ref('')
const tableLabelMode = ref<'name' | 'description'>('name')
const isDatasetListCollapsed = ref(false)
const activeConfig = computed(() => {
  if (configs.value.length === 0) return null
  return configs.value.find(c => c.id === expandedId.value) || configs.value[0]
})
const filteredSourceTables = computed(() => {
  const keyword = tableSearchKeyword.value.trim().toLowerCase()
  if (!keyword) return props.tables
  return props.tables.filter(table =>
    table.table_name.toLowerCase().includes(keyword) ||
    (table.description || '').toLowerCase().includes(keyword)
  )
})
const getTableDisplayLabel = (table: TableInfo) => {
  if (tableLabelMode.value === 'description') {
    return table.description || table.table_name
  }
  return table.table_name
}
const getTableHoverTitle = (table: TableInfo) => {
  if (tableLabelMode.value === 'description') {
    return table.table_name
  }
  return table.description || table.table_name
}

const buildConfigSignature = (config: SemanticConfigItem) => {
  return JSON.stringify({
    name: config.name || '',
    description: config.description || '',
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

const deriveScopeFromConfig = (config: SemanticConfigItem) => {
  const names = new Set<string>()
  config.dimensions.forEach(item => names.add(item.table))
  config.metrics.forEach(item => names.add(item.table))
  config.relationships.forEach(item => {
    names.add(item.left_table)
    names.add(item.right_table)
  })
  return Array.from(names)
}

const syncTableScopes = () => {
  const next: Record<string, string[]> = {}
  configs.value.forEach(config => {
    const existed = tableScopes.value[config.id]
    next[config.id] = existed && existed.length > 0 ? existed : deriveScopeFromConfig(config)
  })
  tableScopes.value = next
}

const getScopeTables = (configId: string) => {
  const names = tableScopes.value[configId] || []
  if (names.length === 0) return []
  return props.tables.filter(t => names.includes(t.table_name))
}

const toggleScopeTable = (configId: string, tableName: string, checked: boolean) => {
  const set = new Set(tableScopes.value[configId] || [])
  if (checked) set.add(tableName)
  else set.delete(tableName)
  const nextScope = Array.from(set)
  tableScopes.value = { ...tableScopes.value, [configId]: nextScope }
  const config = configs.value.find(c => c.id === configId)
  if (!config) return
  updateConfig(configId, {
    metrics: config.metrics.filter(item => nextScope.includes(item.table)),
    dimensions: config.dimensions.filter(item => nextScope.includes(item.table)),
    relationships: config.relationships.filter(item => nextScope.includes(item.left_table) && nextScope.includes(item.right_table))
  })
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
  const scope = new Set(tableScopes.value[configId] || [])
  if (scope.has(tableName)) return
  toggleScopeTable(configId, tableName, true)
}

// 移除数据表并级联清理关联配置
const removeScopeTable = (configId: string, tableName: string) => {
  toggleScopeTable(configId, tableName, false)
}

// 画布接收拖拽
const handleScopeDrop = (configId: string) => {
  if (!draggingTableName.value) return
  addScopeTable(configId, draggingTableName.value)
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
      if (configs.value.length > 0 && !expandedId.value) {
        expandedId.value = configs.value[0].id
      }
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
  try {
    const res = await createSemanticConfig(props.datasourceId, {
      name: `数据集 ${configs.value.length + 1}`,
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
      ElMessage.success('创建成功')
    }
  } catch (err) {
    ElMessage.error('创建失败')
  }
}

// 保存配置
const saveConfig = async (config: SemanticConfigItem) => {
  savingId.value = config.id
  try {
    const res = await updateSemanticConfig(props.datasourceId, config.id, config)
    if (res.status === 'success' && res.data) {
      configs.value = configs.value.map(c => c.id === config.id ? res.data! : c)
      savedConfigSignatures.value = {
        ...savedConfigSignatures.value,
        [config.id]: buildConfigSignature(res.data),
      }
      ElMessage.success('保存成功')
    }
  } catch (err) {
    ElMessage.error('保存失败')
  } finally {
    savingId.value = null
  }
}

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
    if (expandedId.value === configId) {
      expandedId.value = newConfigs[0]?.id || null
    }
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
}

// 监听数据源变化
watch(() => props.datasourceId, fetchConfigs, { immediate: true })
watch(configs, (list) => {
  if (list.length === 0) {
    expandedId.value = null
    return
  }
  if (!expandedId.value || !list.some(item => item.id === expandedId.value)) {
    expandedId.value = list[0].id
  }
}, { deep: true })
watch(expandedId, () => {
  activeAggRowKey.value = null
  modelConfigCollapsed.value = false
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
  column: string
  sourceColumn?: string
  columnType: string
  isDimension: boolean
  isMetric: boolean
  displayName: string
  description: string
  metricAggFunc: string
  expression: string
  isCustom?: boolean
}

interface AddFieldForm {
  table: string
  column: string
  fieldName: string
  displayName: string
  mode: 'dimension' | 'metric'
}

const fieldRows = ref<FieldConfigRow[]>([])
const fieldRowsSnapshot = ref('')
const fieldRowsConfigId = ref<string | null>(null)
const activeAggRowKey = ref<string | null>(null)
const showAddFieldDialog = ref(false)
const autoPrefillLoading = ref(false)
const addFieldForm = ref<AddFieldForm>({ table: '', column: '', fieldName: '', displayName: '', mode: 'metric' })

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
const expandedFunctionGroups = ref<Set<string>>(new Set(['agg', 'common', 'date', 'string']))

const isFieldRowsDirty = computed(() => JSON.stringify(fieldRows.value) !== fieldRowsSnapshot.value)

const quoteIdentifier = (name: string) => {
  const safe = (name || '').replace(/`/g, '``')
  return `\`${safe}\``
}

const normalizeExpressionColumn = (value: string) => {
  const cleaned = (value || '').trim().replace(/`/g, '')
  if (!cleaned) return ''
  const parts = cleaned.split('.')
  return parts[parts.length - 1]
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

const validateFieldRowsBeforeApply = () => {
  const usedNames = new Set<string>()
  for (const row of fieldRows.value) {
    if (!row.isDimension && !row.isMetric) continue
    const sourceColumn = row.sourceColumn || row.column
    if (!row.table || !sourceColumn) {
      return `字段 ${row.key} 缺少表或字段信息`
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
  const scope = getScopeTables(configId)
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
    table: scope[0]?.table_name || '',
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
  const scope = getScopeTables(configId)
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
    table: scope[0]?.table_name || '',
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
  return [{ left_column: '', right_column: '' }]
}

const syncRelationshipColumnsFromConditions = (rel: SemanticRelationship) => {
  const first = (rel.join_conditions || [])[0]
  rel.left_column = first?.left_column || ''
  rel.right_column = first?.right_column || ''
}

const addRelationship = (configId: string) => {
  const scope = getScopeTables(configId)
  if (scope.length < 2) {
    ElMessage.warning('请先选择至少2张数据表再配置关系')
    return
  }
  editingType.value = 'relationship'
  editingIndex.value = -1
  editingConfigId.value = configId
  editingRelationship.value = {
    left_table: scope[0]?.table_name || '',
    right_table: scope[1]?.table_name || '',
    left_column: '',
    right_column: '',
    join_conditions: [{ left_column: '', right_column: '' }],
    relationship_type: 'one_to_many',
    join_type: 'left',
  }
}

const editRelationship = (configId: string, rel: SemanticRelationship, index: number) => {
  editingType.value = 'relationship'
  editingIndex.value = index
  editingConfigId.value = configId
  editingRelationship.value = { ...rel, join_conditions: getRelationshipConditions(rel).map(c => ({ ...c })) }
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
  if (editingRelationship.value.left_table === editingRelationship.value.right_table) {
    ElMessage.warning('关联两侧不能是同一张表')
    return
  }
  const config = configs.value.find(c => c.id === editingConfigId.value)
  if (!config) return
  const duplicatePairIndex = config.relationships.findIndex((rel, idx) => {
    if (idx === editingIndex.value) return false
    const sameDirection = rel.left_table === editingRelationship.value!.left_table && rel.right_table === editingRelationship.value!.right_table
    const reverseDirection = rel.left_table === editingRelationship.value!.right_table && rel.right_table === editingRelationship.value!.left_table
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
  if (!activeConfig.value) return [] as Array<{ rel: SemanticRelationship; relIndex: number }>
  return activeConfig.value.relationships.map((rel, relIndex) => ({ rel, relIndex }))
})

const formatJoinTypeLabel = (joinType?: string) => {
  if (joinType === 'inner') return '内连接'
  if (joinType === 'right') return '右连接'
  return '左连接'
}

const activeUnlinkedTables = computed(() => {
  if (!activeConfig.value) return [] as string[]
  const scope = tableScopes.value[activeConfig.value.id] || []
  if (scope.length === 0) return []
  const used = new Set<string>()
  used.add(scope[0])
  activeConfig.value.relationships.forEach(rel => {
    used.add(rel.left_table)
    used.add(rel.right_table)
  })
  return scope.filter(name => !used.has(name))
})

const modelConfigVisibleRows = computed(() => {
  if (!activeConfig.value) return 1
  const scope = tableScopes.value[activeConfig.value.id] || []
  if (scope.length === 0) return 1
  const totalRows = 1 + activeRelationshipCards.value.length + activeUnlinkedTables.value.length
  return Math.max(1, Math.min(3, totalRows))
})

const modelConfigContentStyle = computed(() => {
  const rowHeight = 94
  const basePadding = 20
  return { maxHeight: `${modelConfigVisibleRows.value * rowHeight + basePadding}px` }
})
const modelConfigCollapsed = ref(false)

const openLinkTableEditor = (configId: string, tableName: string) => {
  const scope = tableScopes.value[configId] || []
  const mainTable = scope[0]
  if (!mainTable || mainTable === tableName) {
    openAddRelationshipEditor(configId)
    return
  }
  editingType.value = 'relationship'
  editingIndex.value = -1
  editingConfigId.value = configId
  editingRelationship.value = {
    left_table: mainTable,
    right_table: tableName,
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

// 是否正在编辑某个配置的某个类型
const isEditing = (configId: string, type: EditingType) => {
  return editingConfigId.value === configId && editingType.value === type
}

const hasMultiTables = (configId: string) => {
  return getScopeTables(configId).length > 1
}

const buildMetricExpression = (aggFunc: string, column: string) => {
  const quotedColumn = quoteIdentifier(column)
  if (aggFunc === 'COUNT_DISTINCT') return `COUNT(DISTINCT ${quotedColumn})`
  return `${aggFunc}(${quotedColumn})`
}

const parseAggFuncFromExpression = (expression: string) => {
  const match = (expression || '').trim().match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
  if (!match) return 'COUNT'
  if (match[1]?.toUpperCase() === 'COUNT' && match[2]) return 'COUNT_DISTINCT'
  return match[1].toUpperCase()
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
    if (/^COUNT$/i.test(aggregateMatch[1])) return 'bigint'
    return getMetricDataType(sourceType)
  }

  return undefined
}

const normalizeFieldName = (value: string) => (value || '').trim()

const getUniqueCustomFieldName = (tableName: string, baseColumn: string) => {
  const base = normalizeFieldName(baseColumn) || 'field'
  const used = new Set(
    fieldRows.value
      .filter(row => row.table === tableName)
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
  return getScopeTables(configId)
})

const addFieldLabelMode = ref<'name' | 'description'>('name')
const addFieldTableKeyword = ref('')
const addFieldColumnKeyword = ref('')

const addFieldColumns = computed(() => {
  const table = addFieldTables.value.find(t => t.table_name === addFieldForm.value.table)
  return table?.columns || []
})

const filteredAddFieldTables = computed(() => {
  const keyword = addFieldTableKeyword.value.trim().toLowerCase()
  if (!keyword) return addFieldTables.value
  return addFieldTables.value.filter(t =>
    t.table_name.toLowerCase().includes(keyword) ||
    (t.description || '').toLowerCase().includes(keyword)
  )
})

const filteredAddFieldColumns = computed(() => {
  const keyword = addFieldColumnKeyword.value.trim().toLowerCase()
  if (!keyword) return addFieldColumns.value
  return addFieldColumns.value.filter(c =>
    c.name.toLowerCase().includes(keyword) ||
    (c.description || '').toLowerCase().includes(keyword)
  )
})

const getAddFieldTableLabel = (table: TableInfo) => {
  if (addFieldLabelMode.value === 'description') return table.description || table.table_name
  return table.table_name
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
  addFieldTableKeyword.value = ''
  addFieldColumnKeyword.value = ''
  addFieldForm.value.table = addFieldTables.value[0].table_name
  addFieldForm.value.column = addFieldTables.value[0].columns?.[0]?.name || ''
  addFieldForm.value.fieldName = getUniqueCustomFieldName(addFieldForm.value.table, addFieldForm.value.column)
  const firstCol = addFieldTables.value[0].columns?.[0]
  addFieldForm.value.displayName = firstCol?.description || firstCol?.name || ''
  addFieldForm.value.mode = 'metric'
  showAddFieldDialog.value = true
}

const handleAddFieldTableChange = () => {
  addFieldColumnKeyword.value = ''
  addFieldForm.value.column = addFieldColumns.value[0]?.name || ''
  addFieldForm.value.fieldName = getUniqueCustomFieldName(addFieldForm.value.table, addFieldForm.value.column)
  const firstCol = addFieldColumns.value[0]
  addFieldForm.value.displayName = firstCol?.description || firstCol?.name || ''
}

const handleAddFieldColumnChange = () => {
  const col = addFieldColumns.value.find(c => c.name === addFieldForm.value.column)
  if (!col) return
  addFieldForm.value.fieldName = getUniqueCustomFieldName(addFieldForm.value.table, col.name)
  // 仅当为空或仍是旧列默认名时，自动同步
  if (!addFieldForm.value.displayName.trim()) {
    addFieldForm.value.displayName = col.description || col.name
  }
}

const confirmAddField = () => {
  const tableName = addFieldForm.value.table
  const columnName = addFieldForm.value.column
  const fieldName = normalizeFieldName(addFieldForm.value.fieldName)
  if (!tableName || !columnName) {
    ElMessage.warning('请选择表和字段')
    return
  }
  if (!fieldName) {
    ElMessage.warning('请填写字段名')
    return
  }
  const table = addFieldTables.value.find(t => t.table_name === tableName)
  const columnInfo = table?.columns?.find(c => c.name === columnName)
  const columnType = columnInfo?.mysql_type || columnInfo?.type || ''
  const isMetric = addFieldForm.value.mode === 'metric'
  const metricAggFunc = isMetric ? (isNumericColumnType(columnType) ? 'SUM' : 'COUNT') : 'COUNT'
  const expression = isMetric ? buildMetricExpression(metricAggFunc, columnName) : quoteIdentifier(columnName)
  const displayName = (addFieldForm.value.displayName || '').trim() || columnInfo?.description || columnName
  if (!displayName) {
    ElMessage.warning('请填写显示名称')
    return
  }
  const duplicatedField = fieldRows.value.some(row => row.table === tableName && normalizeFieldName(row.column) === fieldName)
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
    key: `${tableName}.${fieldName}#extra_${Date.now()}`,
    table: tableName,
    column: fieldName,
    sourceColumn: columnName,
    columnType,
    isDimension: !isMetric,
    isMetric,
    displayName,
    description: '',
    metricAggFunc,
    expression,
    isCustom: true,
  })
  showAddFieldDialog.value = false
  addFieldForm.value.fieldName = ''
  addFieldForm.value.displayName = ''
}

const handleAutoPrefillFields = async () => {
  if (!activeConfig.value) return
  const selectedTables = tableScopes.value[activeConfig.value.id] || []
  if (selectedTables.length === 0) {
    ElMessage.warning('请先在左侧选择数据表')
    return
  }
  autoPrefillLoading.value = true
  try {
    const targetTables = props.tables
      .filter(table => selectedTables.includes(table.table_name))
      .map(table => ({
        table_name: table.table_name,
        description: table.description,
        columns: (table.columns || []).map(column => ({
          name: column.name,
          type: column.type,
          mysql_type: column.mysql_type,
          hive_type: column.hive_type,
          description: column.description,
        }))
      }))

    const res = await autoPrefillSemanticFields({
      datasource_id: props.datasourceId,
      config_id: activeConfig.value.id,
      tables: targetTables,
    })
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '自动预填失败')
      return
    }
    const fields = res.data?.fields || []
    const fieldMap = new Map(fields.map(item => [`${item.table}.${item.column}`, item]))
    fieldRows.value.forEach(row => {
      if (row.isCustom) return
      const matched = fieldMap.get(`${row.table}.${row.column}`)
      if (!matched) return
      row.isDimension = !!matched.is_dimension
      row.isMetric = !!matched.is_metric
      row.expression = (matched.expression || quoteIdentifier(row.sourceColumn || row.column)).trim()
    })
    activeAggRowKey.value = null
    ElMessage.success('已按规则预填维度/指标/表达式')
  } catch (err) {
    ElMessage.error('自动预填失败')
  } finally {
    autoPrefillLoading.value = false
  }
}

const removeCustomFieldRow = (rowKey: string) => {
  fieldRows.value = fieldRows.value.filter(row => row.key !== rowKey)
  if (activeAggRowKey.value === rowKey) {
    activeAggRowKey.value = null
  }
}

const buildDimensionExpression = (func: string, column: string) => {
  const quoted = quoteIdentifier(column)
  if (func === 'CASE_WHEN') return `CASE\n  WHEN ${quoted} = '条件值1' THEN '分类1'\n  WHEN ${quoted} = '条件值2' THEN '分类2'\n  ELSE '其他'\nEND`
  if (func === 'DATE') return `DATE(${quoted})`
  if (func === 'DATE_FORMAT_MONTH') return `DATE_FORMAT(${quoted}, '%Y-%m')`
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
  const scopeTables = getScopeTables(configId)

  const dimensionSet = new Set<string>()
  const dimensionByColumnMap = new Map<string, SemanticDimension>()
  const metricByColumnMap = new Map<string, { agg: string; expression: string; name: string; description: string; fieldName?: string }>()
  const customRowsMap = new Map<string, FieldConfigRow>()

  config.dimensions.forEach(d => {
    const physicalKey = `${d.table}.${d.column}`
    const fieldName = (d.field_name || d.column || '').trim()
    if (fieldName && d.column && fieldName !== d.column) {
      const customKey = `${d.table}.${fieldName}`
      const existed = customRowsMap.get(customKey)
      customRowsMap.set(customKey, {
        key: customKey,
        table: d.table,
        column: fieldName,
        sourceColumn: d.column,
        columnType: existed?.columnType || '',
        isDimension: true,
        isMetric: existed?.isMetric || false,
        displayName: existed?.displayName || d.name || fieldName,
        description: existed?.description || d.description || '',
        metricAggFunc: existed?.metricAggFunc || 'COUNT',
        expression: existed?.expression || d.expression || quoteIdentifier(d.column),
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
    const match = m.expression.match(/^(COUNT|SUM|AVG|MAX|MIN)\((DISTINCT\s+)?(.+)\)$/i)
    const expression = (m.expression || '').trim()
    const exprColumn = normalizeExpressionColumn(match?.[3]?.trim() || expression)
    const agg = match ? (match?.[2] ? 'COUNT_DISTINCT' : (match?.[1] || 'COUNT').toUpperCase()) : 'COUNT'
    const fieldName = (m.field_name || exprColumn || '').trim()
    if (fieldName && exprColumn && fieldName !== exprColumn) {
      const customKey = `${m.table}.${fieldName}`
      const existed = customRowsMap.get(customKey)
      customRowsMap.set(customKey, {
        key: customKey,
        table: m.table,
        column: fieldName,
        sourceColumn: exprColumn,
        columnType: existed?.columnType || '',
        isDimension: existed?.isDimension || false,
        isMetric: true,
        displayName: m.name || fieldName,
        description: m.description || existed?.description || '',
        metricAggFunc: agg,
        expression: expression || existed?.expression || quoteIdentifier(exprColumn),
        isCustom: true,
      })
      return
    }
    const physicalKey = `${m.table}.${exprColumn}`
    if (!metricByColumnMap.has(physicalKey)) {
      metricByColumnMap.set(physicalKey, { agg, expression, name: m.name, description: m.description || '', fieldName: m.field_name })
    }
  })

  const rows: FieldConfigRow[] = []
  scopeTables.forEach(table => {
    table.columns?.forEach(col => {
      const key = `${table.table_name}.${col.name}`
      const dimInfo = dimensionByColumnMap.get(key)
      const metricInfo = metricByColumnMap.get(key)
      const metricAggFunc = metricInfo?.agg || 'COUNT'
      const expression = metricInfo?.expression || dimInfo?.expression || quoteIdentifier(col.name)
      const displayName = metricInfo?.name || dimInfo?.name || col.description || col.name
      const description = metricInfo?.description || dimInfo?.description || ''
      rows.push({
        key,
        table: table.table_name,
        column: col.name,
        sourceColumn: col.name,
        columnType: col.mysql_type || col.type || '',
        isDimension: dimensionSet.has(key),
        isMetric: !!metricInfo,
        displayName,
        description,
        metricAggFunc,
        expression,
      })
    })
  })
  customRowsMap.forEach(row => {
    const tableInfo = scopeTables.find(t => t.table_name === row.table)
    const sourceInfo = tableInfo?.columns?.find(c => c.name === (row.sourceColumn || row.column))
    rows.push({
      ...row,
      columnType: row.columnType || sourceInfo?.mysql_type || sourceInfo?.type || '',
      expression: row.expression || quoteIdentifier(row.sourceColumn || row.column),
      isCustom: true,
    })
  })
  return rows
}

const mergeFieldRowsWithDraft = (nextRows: FieldConfigRow[]) => {
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
      description: draft.description,
      metricAggFunc: draft.metricAggFunc,
      expression: draft.expression,
    }
  })
  fieldRows.value
    .filter(row => row.isCustom && !nextKeySet.has(row.key))
    .forEach(row => {
      mergedRows.push({ ...row })
    })
  return mergedRows
}

const loadFieldRows = (configId: string) => {
  fieldRows.value = buildFieldRows(configId)
  fieldRowsConfigId.value = configId
  fieldRowsSnapshot.value = JSON.stringify(fieldRows.value)
  activeAggRowKey.value = null
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
      column: r.sourceColumn || r.column,
      expression: (r.isMetric ? quoteIdentifier(r.sourceColumn || r.column) : (r.expression || quoteIdentifier(r.sourceColumn || r.column))).trim(),
      type: /date|time|timestamp|datetime|year/i.test(r.columnType) ? 'time' : /int|float|double|decimal|numeric|number|bigint|tinyint|smallint|mediumint/i.test(r.columnType) ? 'number' : 'categorical',
      description: (r.description || '').trim(),
    }))

  const nextMetrics: SemanticMetric[] = fieldRows.value
    .filter(r => r.isMetric)
    .map(r => ({
      name: ((r.displayName || '').trim() || r.column),
      field_name: r.column,
      table: r.table,
      expression: (r.expression || quoteIdentifier(r.sourceColumn || r.column)).trim(),
      data_type: getMetricDataType(r.columnType),
      filter: '',
      description: (r.description || '').trim(),
    }))

  updateConfig(configId, {
    dimensions: nextDimensions,
    metrics: nextMetrics
  })
  fieldRowsSnapshot.value = JSON.stringify(fieldRows.value)
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
  const scope = tableScopes.value[configId] || []
  if (scope.length <= 1) return true

  const scopeSet = new Set(scope)
  const adjacency = new Map<string, Set<string>>()
  scope.forEach(name => adjacency.set(name, new Set()))
  config.relationships.forEach(rel => {
    if (!scopeSet.has(rel.left_table) || !scopeSet.has(rel.right_table)) return
    adjacency.get(rel.left_table)?.add(rel.right_table)
    adjacency.get(rel.right_table)?.add(rel.left_table)
  })

  const visited = new Set<string>()
  const queue: string[] = [scope[0]]
  visited.add(scope[0])
  while (queue.length > 0) {
    const current = queue.shift() as string
    ;(adjacency.get(current) || new Set()).forEach(next => {
      if (visited.has(next)) return
      visited.add(next)
      queue.push(next)
    })
  }

  const unlinked = scope.filter(name => !visited.has(name))
  if (unlinked.length > 0) {
    ElMessage.warning(`存在未关联表：${unlinked.join('、')}，请先配置关联或移除这些表`)
    return false
  }
  return true
}

watch(
  () => {
    const configId = activeConfig.value?.id || ''
    const scope = configId ? (tableScopes.value[configId] || []) : []
    return `${configId}::${scope.join(',')}`
  },
  signature => {
    if (!signature || signature.startsWith('::')) {
      fieldRows.value = []
      fieldRowsConfigId.value = null
      fieldRowsSnapshot.value = JSON.stringify([])
      return
    }
    const configId = signature.split('::')[0]
    if (fieldRowsConfigId.value === configId && isFieldRowsDirty.value) {
      fieldRows.value = mergeFieldRowsWithDraft(buildFieldRows(configId))
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
  } else {
    if (activeAggRowKey.value === row.key) {
      activeAggRowKey.value = null
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

const toggleAggSelector = (row: FieldConfigRow) => {
  if (!row.isMetric && !row.isDimension) return
  activeAggRowKey.value = activeAggRowKey.value === row.key ? null : row.key
}

const closeAggSelector = () => {
  activeAggRowKey.value = null
}

const applyAggFunc = (row: FieldConfigRow, aggFunc: string) => {
  handleAggFuncChange(row, aggFunc)
  activeAggRowKey.value = null
}

const applyDimensionFunc = (row: FieldConfigRow, func: string) => {
  row.expression = buildDimensionExpression(func, row.sourceColumn || row.column)
  activeAggRowKey.value = null
}

const toggleFunctionGroup = (group: string) => {
  const next = new Set(expandedFunctionGroups.value)
  if (next.has(group)) next.delete(group)
  else next.add(group)
  expandedFunctionGroups.value = next
}

const toggleDatasetListCollapsed = () => {
  isDatasetListCollapsed.value = !isDatasetListCollapsed.value
}
</script>

<template>
  <div class="h-full flex flex-col bg-white">
    <!-- 头部 -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-gradient-to-r from-blue-50 to-white">
      <div class="flex items-center gap-3">
        <button
          class="w-8 h-8 flex items-center justify-center border-none rounded-lg bg-transparent text-gray-500 cursor-pointer transition-all duration-200 hover:bg-gray-100 hover:text-gray-700"
          @click="emit('close')"
        >
          <i class="fa-solid fa-arrow-left"></i>
        </button>
        <div>
          <h2 class="text-sm font-semibold text-gray-800 m-0">数据集管理</h2>
          <p class="text-xs text-gray-500 m-0">{{ datasourceName }}</p>
        </div>
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
        <span class="text-xs text-gray-400 mb-4">点击"新建数据集"创建第一个数据集</span>
        <button
          class="flex items-center gap-1.5 px-3 py-2 border-none rounded-md bg-[#1677FF] text-white text-[12px] font-medium cursor-pointer transition-all duration-150 hover:bg-[#0958D9]"
          @click="createConfig"
        >
          <i class="fa-solid fa-plus"></i>
          新建数据集
        </button>
      </div>

      <div
        v-else
        class="h-full min-h-0 grid gap-4"
        :style="{ gridTemplateColumns: isDatasetListCollapsed ? '48px 1fr' : '320px 1fr' }"
      >
        <div class="h-full min-h-0 border border-gray-200 rounded-xl bg-white overflow-hidden flex flex-col">
          <div class="px-3 py-2.5 border-b border-gray-100 flex items-center justify-between gap-2">
            <button
              class="w-6 h-6 flex items-center justify-center border-none rounded bg-transparent text-gray-500 cursor-pointer transition-all duration-150 hover:bg-gray-100 hover:text-gray-700"
              :title="isDatasetListCollapsed ? '展开数据集列表' : '折叠数据集列表'"
              @click="toggleDatasetListCollapsed"
            >
              <i :class="['fa-solid text-[11px]', isDatasetListCollapsed ? 'fa-chevron-right' : 'fa-chevron-left']"></i>
            </button>
            <template v-if="!isDatasetListCollapsed">
              <div class="text-xs font-semibold text-gray-600">数据集列表</div>
              <button
                class="flex items-center gap-1 px-2.5 py-1.5 border-none rounded-md bg-blue-500 text-white text-[12px] font-medium cursor-pointer transition-all duration-200 hover:bg-blue-600"
                @click="createConfig"
              >
                <i class="fa-solid fa-plus"></i>
                新建
              </button>
            </template>
          </div>
          <div v-if="isDatasetListCollapsed" class="flex-1 min-h-0 flex items-center justify-center text-[11px] text-gray-400">
            {{ configs.length }}
          </div>
          <div v-else class="flex-1 min-h-0 overflow-y-auto p-2">
            <button
              v-for="config in configs"
              :key="config.id"
              type="button"
              :class="[
                'w-full mb-2 px-3 py-2.5 rounded-lg border text-left transition-all duration-150',
                expandedId === config.id ? 'border-blue-400 bg-blue-50 shadow-sm' : 'border-gray-200 bg-white hover:border-gray-300'
              ]"
              @click="toggleExpand(config.id)"
            >
              <div class="flex items-center justify-between gap-2">
                <div class="min-w-0">
                  <div class="flex items-center gap-2">
                    <span :class="['w-2 h-2 rounded-full', config.enabled ? 'bg-emerald-500' : 'bg-gray-300']"></span>
                    <span class="text-[13px] font-semibold text-gray-800 truncate">{{ config.name }}</span>
                  </div>
                  <div class="text-[11px] text-gray-400 mt-1">
                    {{ config.metrics?.length || 0 }}指标 · {{ config.dimensions?.length || 0 }}维度 · {{ config.relationships?.length || 0 }}关系
                  </div>
                </div>
                <div class="flex items-center gap-1.5" @click.stop>
                  <button
                    :class="[
                      'px-2 py-0.5 border-none rounded text-[11px] cursor-pointer',
                      config.enabled ? 'bg-emerald-500/10 text-emerald-500' : 'bg-gray-100 text-gray-500'
                    ]"
                    @click="handleToggleEnabled(config)"
                  >
                    {{ config.enabled ? '已启用' : '已禁用' }}
                  </button>
                  <button
                    class="w-6 h-6 flex items-center justify-center border-none rounded bg-transparent text-red-500 cursor-pointer transition-all duration-150 hover:bg-red-500/10"
                    @click="handleDeleteConfig(config.id, config.name)"
                  >
                    <i class="fa-solid fa-trash text-[11px]"></i>
                  </button>
                </div>
              </div>
            </button>
          </div>
        </div>

        <div v-if="activeConfig" class="h-full min-h-0 border border-gray-200 rounded-xl bg-white overflow-hidden flex flex-col">
          <div class="px-4 py-3 border-b border-gray-100 bg-gray-50">
            <div class="grid grid-cols-[240px_1fr_auto] gap-3 items-center">
              <input
                type="text"
                :value="activeConfig.name"
                class="px-3 py-2 border border-gray-200 rounded-md text-[13px] text-gray-700 outline-none focus:border-blue-500"
                placeholder="数据集名称"
                @input="updateConfig(activeConfig.id, { name: ($event.target as HTMLInputElement).value })"
              />
              <input
                type="text"
                :value="activeConfig.description"
                placeholder="配置描述（帮助 AI 理解这个配置的用途）"
                class="px-3 py-2 border border-gray-200 rounded-md text-[13px] text-gray-500 outline-none focus:border-blue-500"
                @input="updateConfig(activeConfig.id, { description: ($event.target as HTMLInputElement).value })"
              />
              <div class="flex items-center gap-2 justify-end">
                <button
                  :class="[
                    'px-3 py-2 border-none rounded-md text-xs transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed',
                    (isActiveConfigDirty || isFieldRowsDirty) ? 'bg-[#1677FF] text-white cursor-pointer hover:bg-[#0958D9]' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                  ]"
                  :disabled="savingId === activeConfig.id || !activeConfig.name || editingType !== null || (!isActiveConfigDirty && !isFieldRowsDirty)"
                  @click="saveActiveConfig"
                >
                  <template v-if="savingId === activeConfig.id">
                    <i class="fa-solid fa-spinner fa-spin mr-1"></i>保存中
                  </template>
                  <template v-else>
                    <i class="fa-solid fa-save mr-1"></i>保存
                  </template>
                </button>
              </div>
            </div>
          </div>

          <div class="flex-1 min-h-0 px-4 py-3">
            <div class="h-full min-h-0 grid grid-cols-[250px_1fr] gap-3">
              <div class="border border-gray-200 rounded-lg bg-white overflow-hidden flex flex-col">
                <div class="px-3 py-2 border-b border-gray-100">
                  <div class="flex items-center justify-between gap-2 mb-2">
                    <div class="text-xs font-medium text-gray-600">数据源表（拖拽到右侧）</div>
                  </div>
                  <div class="relative">
                    <i class="fa-solid fa-search absolute left-2.5 top-1/2 -translate-y-1/2 text-[11px] text-gray-400"></i>
                    <input
                      v-model="tableSearchKeyword"
                      type="text"
                      placeholder="搜索表名或表备注"
                      class="w-full pl-7 pr-8 py-1.5 border border-gray-200 rounded-md text-[12px] text-gray-600 outline-none focus:border-blue-500"
                    />
                    <button
                      type="button"
                      class="absolute right-1.5 top-1/2 -translate-y-1/2 w-5.5 h-5.5 border border-gray-200 rounded bg-white text-gray-500 hover:text-blue-600 hover:border-blue-200 transition-colors"
                      :title="tableLabelMode === 'name' ? '当前显示：表名，点击切换为备注' : '当前显示：备注，点击切换为表名'"
                      @click="tableLabelMode = tableLabelMode === 'name' ? 'description' : 'name'"
                    >
                      <svg viewBox="0 0 16 16" class="w-[10px] h-[10px]" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M2 5.25H12.5L10.6 3.65" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                        <path d="M14 10.75H3.5L5.4 12.35" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                    </button>
                  </div>
                </div>
                <div class="flex-1 min-h-0 overflow-y-auto p-2 flex flex-col gap-1.5">
                  <button
                    v-for="table in filteredSourceTables"
                    :key="`${activeConfig.id}-all-${table.table_name}`"
                    type="button"
                    class="w-full text-left px-2.5 py-2 rounded-md border border-gray-200 bg-white text-[12px] text-gray-700 cursor-move transition-all duration-150 hover:border-blue-300 hover:bg-blue-50 disabled:opacity-60 disabled:cursor-not-allowed"
                    :title="getTableHoverTitle(table)"
                    :draggable="!(tableScopes[activeConfig.id] || []).includes(table.table_name)"
                    :disabled="(tableScopes[activeConfig.id] || []).includes(table.table_name)"
                    @dragstart="handleTableDragStart(table.table_name)"
                    @dragend="handleTableDragEnd"
                  >
                    <div class="flex items-center justify-between gap-2">
                      <span class="truncate">{{ getTableDisplayLabel(table) }}</span>
                      <span
                        v-if="(tableScopes[activeConfig.id] || []).includes(table.table_name)"
                        class="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-600"
                      >已选</span>
                    </div>
                  </button>
                  <div v-if="filteredSourceTables.length === 0" class="px-2 py-5 text-center text-[12px] text-gray-400">
                    未找到匹配的数据表
                  </div>
                </div>
              </div>

              <div class="min-h-0 flex flex-col gap-3">
                <div
                  class="border border-dashed border-gray-300 rounded-lg bg-gray-50/60 p-3 flex flex-col shrink-0"
                  @dragover.prevent
                  @drop.prevent="handleScopeDrop(activeConfig.id)"
                >
                  <div class="flex items-center justify-between mb-2">
                    <div class="text-xs font-medium text-gray-600">模型配置</div>
                    <div class="flex items-center gap-2">
                      <div class="text-[11px] text-gray-400">
                        {{ (tableScopes[activeConfig.id] || []).length }} 张表 · {{ activeConfig.relationships.length }} 条关系
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
                    v-if="!modelConfigCollapsed && (tableScopes[activeConfig.id] || []).length === 0"
                    class="h-[120px] border border-dashed border-gray-300 rounded-md bg-white flex flex-col items-center justify-center text-gray-400 text-xs"
                  >
                    <i class="fa-solid fa-up-right-and-down-left-from-center mb-2"></i>
                    拖动左侧数据表到这里
                  </div>
                  <div v-if="modelConfigCollapsed" class="h-[44px] border border-dashed border-gray-300 rounded-md bg-white flex items-center px-3 text-xs text-gray-400">
                    模型配置已折叠
                  </div>
                  <div
                    v-if="!modelConfigCollapsed && (tableScopes[activeConfig.id] || []).length > 0"
                    class="min-h-0 overflow-auto rounded-md border border-gray-200 bg-white p-2"
                    :style="modelConfigContentStyle"
                  >
                    <div class="space-y-2.5">
                      <div class="rounded-md border border-gray-200 bg-white px-2.5 py-2 flex items-center justify-between gap-2">
                        <div class="min-w-0">
                          <div class="text-[12px] font-semibold text-gray-800 truncate">{{ (tableScopes[activeConfig.id] || [])[0] }}</div>
                        </div>
                        <button
                          class="w-5 h-5 border-none rounded bg-gray-100 text-gray-400 text-[10px] cursor-pointer hover:bg-red-50 hover:text-red-500"
                          @click="removeScopeTable(activeConfig.id, (tableScopes[activeConfig.id] || [])[0])"
                        >
                          <i class="fa-solid fa-xmark"></i>
                        </button>
                      </div>

                      <div v-if="activeRelationshipCards.length === 0" class="text-[11px] text-gray-400 px-1 py-1.5 border-t border-gray-100">
                        暂无关联，请在“待关联表”中点击“配置关联”
                      </div>
                      <div v-else class="space-y-2.5">
                        <div
                          v-for="item in activeRelationshipCards"
                          :key="`rel-card-${activeConfig.id}-${item.relIndex}`"
                          class="pt-1"
                        >
                          <button
                            class="w-full max-w-full text-left px-3 py-1.5 rounded-full border border-blue-200 bg-blue-50 text-blue-600 text-[11px] font-medium cursor-pointer hover:bg-blue-100"
                            @click="openEditRelationshipEditor(activeConfig.id, item.rel, item.relIndex)"
                          >
                            <span class="flex items-center gap-2 min-w-0">
                              <span class="shrink-0 pr-2 border-r border-blue-200 whitespace-nowrap">{{ formatJoinTypeLabel(item.rel.join_type) }}</span>
                                <span class="block flex-1 min-w-0 overflow-x-auto overflow-y-hidden whitespace-nowrap">
                                  <span class="inline-block whitespace-nowrap pr-1">
                                  {{
                                    getRelationshipConditions(item.rel)
                                      .map(cond => `${item.rel.left_table}.${cond.left_column} = ${item.rel.right_table}.${cond.right_column}`)
                                      .join(' AND ')
                                  }}
                                  </span>
                                </span>
                              </span>
                            </button>
                          <div class="mt-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-2 flex items-center justify-between gap-2">
                            <div class="min-w-0">
                              <div class="flex items-baseline gap-2 min-w-0">
                                <span class="text-[12px] font-semibold text-gray-800 truncate">{{ item.rel.right_table }}</span>
                                <span class="text-[11px] text-gray-400 truncate">{{ tables.find(t => t.table_name === item.rel.right_table)?.description || '未填写描述' }}</span>
                              </div>
                            </div>
                            <button
                              class="w-5 h-5 border-none rounded bg-gray-100 text-gray-400 text-[10px] cursor-pointer hover:bg-red-50 hover:text-red-500"
                              @click="removeScopeTable(activeConfig.id, item.rel.right_table)"
                            >
                              <i class="fa-solid fa-xmark"></i>
                            </button>
                          </div>
                        </div>
                      </div>

                      <div v-if="activeUnlinkedTables.length > 0" class="pt-2 border-t border-gray-100">
                        <div class="flex items-center justify-between mb-1.5">
                          <div class="text-[11px] text-red-500 font-medium">待关联表</div>
                          <div class="text-[11px] text-red-400">未关联 {{ activeUnlinkedTables.length }} 张</div>
                        </div>
                        <div class="mb-1.5 text-[11px] text-red-400">存在游离表，请先配置关联后再保存</div>
                        <div class="space-y-1.5">
                          <div
                            v-for="tableName in activeUnlinkedTables"
                            :key="`unlinked-${activeConfig.id}-${tableName}`"
                            class="rounded-md border border-dashed border-red-200 bg-red-50/70 px-2.5 py-2 flex items-center justify-between gap-2"
                          >
                            <div class="min-w-0">
                              <div class="flex items-baseline gap-2 min-w-0">
                                <span class="text-[12px] font-semibold text-gray-800 truncate">{{ tableName }}</span>
                                <span class="text-[11px] text-gray-400 truncate">{{ tables.find(t => t.table_name === tableName)?.description || '未填写描述' }}</span>
                              </div>
                              <div class="text-[11px] text-red-400 truncate">未配置关联条件</div>
                            </div>
                            <div class="flex items-center gap-1">
                              <button
                                class="px-2 py-1 rounded border border-red-200 bg-white text-red-500 text-[11px] cursor-pointer hover:bg-red-50"
                                @click="openLinkTableEditor(activeConfig.id, tableName)"
                              >
                                配置关联
                              </button>
                              <button
                                class="w-5 h-5 border-none rounded bg-gray-100 text-gray-400 text-[10px] cursor-pointer hover:bg-red-50 hover:text-red-500"
                                @click="removeScopeTable(activeConfig.id, tableName)"
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
                        @click="handleAutoPrefillFields"
                      >
                        <i v-if="autoPrefillLoading" class="fa-solid fa-spinner fa-spin mr-1"></i>
                        <i v-else class="fa-solid fa-wand-magic-sparkles mr-1"></i>
                        自动预填
                      </button>
                      <button
                        class="h-7 px-2.5 border border-gray-200 rounded bg-white text-[11px] text-gray-600 hover:border-blue-200 hover:text-blue-600"
                        @click="openAddFieldDialog"
                      >
                        <i class="fa-solid fa-plus mr-1"></i>新增字段
                      </button>
                      <div class="text-[11px] text-gray-400">{{ fieldRows.length }} 个字段</div>
                    </div>
                  </div>
                  <div v-if="fieldRows.length === 0" class="flex-1 border border-dashed border-gray-300 rounded-md bg-white flex flex-col items-center justify-center text-gray-400 text-xs">
                    <i class="fa-regular fa-table-cells-large mb-2"></i>
                    请先在左侧选择数据表
                  </div>
                  <div v-else class="flex-1 overflow-auto rounded-md border border-gray-200 bg-white">
                    <table class="w-full border-collapse text-[12px]">
                      <thead class="bg-gray-50 text-gray-600">
                        <tr>
                          <th class="text-left px-3 py-2 border border-gray-200">表</th>
                          <th class="text-left px-3 py-2 border border-gray-200">字段</th>
                          <th class="text-left px-3 py-2 border border-gray-200">类型</th>
                          <th class="text-center px-3 py-2 border border-gray-200">维度</th>
                          <th class="text-center px-3 py-2 border border-gray-200">指标</th>
                          <th class="text-left px-3 py-2 border border-gray-200">表达式</th>
                          <th class="text-left px-3 py-2 border border-gray-200">显示名称</th>
                          <th class="text-left px-3 py-2 border border-gray-200">描述</th>
                          <th class="text-center px-3 py-2 border border-gray-200 w-[72px]">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="row in fieldRows" :key="row.key" class="hover:bg-gray-50">
                          <td class="px-3 py-2 border border-gray-200 text-gray-700">{{ row.table }}</td>
                          <td class="px-3 py-2 border border-gray-200 text-gray-700" :title="row.isCustom ? `物理字段：${row.sourceColumn || row.column}` : ''">
                            {{ row.column }}
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-gray-500">{{ row.columnType || '-' }}</td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <input :checked="row.isDimension" type="checkbox" @change="handleDimensionToggle(row, ($event.target as HTMLInputElement).checked)" />
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <input :checked="row.isMetric" type="checkbox" @change="handleMetricToggle(row, ($event.target as HTMLInputElement).checked)" />
                          </td>
                          <td class="px-3 py-2 border border-gray-200">
                            <div class="flex items-center gap-1">
                              <input
                                :value="row.expression"
                                type="text"
                                class="flex-1 px-2 py-1 border border-gray-200 rounded text-[12px] font-mono disabled:bg-gray-50 disabled:text-gray-400"
                                :disabled="!row.isMetric && !row.isDimension"
                                @input="handleExpressionInput(row, ($event.target as HTMLInputElement).value)"
                              />
                              <el-popover
                                placement="bottom-end"
                                width="280"
                                trigger="click"
                                :visible="activeAggRowKey === row.key"
                                :teleported="false"
                              >
                                <template #reference>
                                  <button
                                    class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-500 hover:text-blue-500 hover:border-blue-300 disabled:bg-gray-50 disabled:text-gray-300 disabled:cursor-not-allowed"
                                    :disabled="!row.isMetric && !row.isDimension"
                                    @click="toggleAggSelector(row)"
                                  >
                                    <i class="fa-solid fa-pen"></i>
                                  </button>
                                </template>
                                <div class="max-h-[420px] overflow-y-auto pr-1">
                                  <div class="mb-1 flex items-center justify-end">
                                    <button
                                      class="w-6 h-6 flex items-center justify-center border-none rounded bg-transparent text-gray-400 cursor-pointer transition-all duration-150 hover:bg-gray-100 hover:text-gray-600"
                                      title="关闭"
                                      @click="closeAggSelector"
                                    >
                                      <i class="fa-solid fa-xmark text-[12px]"></i>
                                    </button>
                                  </div>
                                  <div class="flex flex-col gap-1">
                                  <button class="flex items-center gap-1 px-1.5 py-1 text-left text-[12px] text-gray-700 rounded hover:bg-gray-50" @click="toggleFunctionGroup('agg')">
                                    <i :class="['fa-solid text-[10px] text-gray-400', expandedFunctionGroups.has('agg') ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
                                    <i class="fa-regular fa-folder text-[12px] text-blue-500"></i>
                                    <span>聚合函数</span>
                                  </button>
                                  <div v-if="expandedFunctionGroups.has('agg')" class="pl-5 flex flex-col gap-1">
                                    <button
                                      v-for="fn in AGG_FUNCTION_OPTIONS"
                                      :key="fn.value"
                                      class="px-2 py-1.5 text-left text-[12px] rounded border border-transparent bg-white text-gray-700 hover:bg-blue-50 hover:text-blue-600 disabled:text-gray-300 disabled:bg-transparent disabled:cursor-not-allowed whitespace-nowrap"
                                      :disabled="!row.isMetric"
                                      @click="applyAggFunc(row, fn.value)"
                                    >
                                      <span class="font-mono">{{ fn.label }}</span>
                                      <span class="text-gray-400">（{{ fn.desc }}）</span>
                                    </button>
                                  </div>

                                  <button class="flex items-center gap-1 px-1.5 py-1 text-left text-[12px] text-gray-700 rounded hover:bg-gray-50" @click="toggleFunctionGroup('common')">
                                    <i :class="['fa-solid text-[10px] text-gray-400', expandedFunctionGroups.has('common') ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
                                    <i class="fa-regular fa-folder text-[12px] text-blue-500"></i>
                                    <span>普通函数</span>
                                  </button>
                                  <div v-if="expandedFunctionGroups.has('common')" class="pl-5 flex flex-col gap-1">
                                    <button
                                      v-for="fn in COMMON_FUNCTION_OPTIONS"
                                      :key="fn.value"
                                      class="px-2 py-1.5 text-left text-[12px] rounded border border-transparent bg-white text-gray-700 hover:bg-blue-50 hover:text-blue-600 disabled:text-gray-300 disabled:bg-transparent disabled:cursor-not-allowed whitespace-nowrap"
                                      :disabled="!row.isDimension"
                                      @click="applyDimensionFunc(row, fn.value)"
                                    >
                                      <span class="font-mono">{{ fn.label }}</span>
                                      <span class="text-gray-400">（{{ fn.desc }}）</span>
                                    </button>
                                  </div>

                                  <button class="flex items-center gap-1 px-1.5 py-1 text-left text-[12px] text-gray-700 rounded hover:bg-gray-50" @click="toggleFunctionGroup('date')">
                                    <i :class="['fa-solid text-[10px] text-gray-400', expandedFunctionGroups.has('date') ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
                                    <i class="fa-regular fa-folder text-[12px] text-blue-500"></i>
                                    <span>日期函数</span>
                                  </button>
                                  <div v-if="expandedFunctionGroups.has('date')" class="pl-5 flex flex-col gap-1">
                                    <button
                                      v-for="fn in DATE_FUNCTION_OPTIONS"
                                      :key="fn.value"
                                      class="px-2 py-1.5 text-left text-[12px] rounded border border-transparent bg-white text-gray-700 hover:bg-blue-50 hover:text-blue-600 disabled:text-gray-300 disabled:bg-transparent disabled:cursor-not-allowed whitespace-nowrap"
                                      :disabled="!row.isDimension"
                                      @click="applyDimensionFunc(row, fn.value)"
                                    >
                                      <span class="font-mono">{{ fn.label }}</span>
                                      <span class="text-gray-400">（{{ fn.desc }}）</span>
                                    </button>
                                  </div>

                                  <button class="flex items-center gap-1 px-1.5 py-1 text-left text-[12px] text-gray-700 rounded hover:bg-gray-50" @click="toggleFunctionGroup('string')">
                                    <i :class="['fa-solid text-[10px] text-gray-400', expandedFunctionGroups.has('string') ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
                                    <i class="fa-regular fa-folder text-[12px] text-blue-500"></i>
                                    <span>字符串函数</span>
                                  </button>
                                  <div v-if="expandedFunctionGroups.has('string')" class="pl-5 flex flex-col gap-1">
                                    <button
                                      v-for="fn in STRING_FUNCTION_OPTIONS"
                                      :key="fn.value"
                                      class="px-2 py-1.5 text-left text-[12px] rounded border border-transparent bg-white text-gray-700 hover:bg-blue-50 hover:text-blue-600 disabled:text-gray-300 disabled:bg-transparent disabled:cursor-not-allowed whitespace-nowrap"
                                      :disabled="!row.isDimension"
                                      @click="applyDimensionFunc(row, fn.value)"
                                    >
                                      <span class="font-mono">{{ fn.label }}</span>
                                      <span class="text-gray-400">（{{ fn.desc }}）</span>
                                    </button>
                                  </div>
                                  </div>
                                </div>
                              </el-popover>
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
                              placeholder="可选描述"
                            />
                          </td>
                          <td class="px-3 py-2 border border-gray-200 text-center">
                            <button
                              v-if="row.isCustom"
                              class="w-7 h-7 border border-gray-200 rounded bg-white text-gray-400 hover:text-red-500 hover:border-red-200"
                              @click="removeCustomFieldRow(row.key)"
                              title="删除新增字段"
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

    <el-dialog v-model="showRelationshipEditor" width="640px" title="连接方式" @close="closeRelationshipEditor">
      <div v-if="editingRelationship" class="overflow-x-auto">
        <div class="min-w-[720px] space-y-4">
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
            v-model="editingRelationship.left_table"
            class="px-2.5 py-2 border border-gray-200 rounded text-sm"
            @change="(editingRelationship.join_conditions || []).forEach(c => { c.left_column = '' })"
          >
            <option v-for="t in getScopeTables(editingConfigId || activeConfig?.id || '')" :key="t.table_name" :value="t.table_name">{{ t.table_name }}</option>
          </select>
          <div class="text-sm text-gray-400">→</div>
          <select
            v-model="editingRelationship.right_table"
            class="px-2.5 py-2 border border-gray-200 rounded text-sm"
            @change="(editingRelationship.join_conditions || []).forEach(c => { c.right_column = '' })"
          >
            <option v-for="t in getScopeTables(editingConfigId || activeConfig?.id || '')" :key="t.table_name" :value="t.table_name">{{ t.table_name }}</option>
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
                <option v-for="col in getTableColumns(editingRelationship.left_table)" :key="col.name" :value="col.name">{{ col.name }}</option>
              </select>
              <div class="text-sm font-semibold text-gray-500">=</div>
              <select v-model="cond.right_column" class="px-2.5 py-2 border border-gray-200 rounded text-sm bg-white">
                <option value="">请选择右表字段</option>
                <option v-for="col in getTableColumns(editingRelationship.right_table)" :key="col.name" :value="col.name">{{ col.name }}</option>
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
              v-model="addFieldForm.table"
              filterable
              placeholder="请选择表"
              :filter-method="handleAddFieldTableFilter"
              @change="handleAddFieldTableChange"
            >
              <el-option
                v-for="t in filteredAddFieldTables"
                :key="t.table_name"
                :label="getAddFieldTableLabel(t)"
                :value="t.table_name"
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

  </div>
</template>
