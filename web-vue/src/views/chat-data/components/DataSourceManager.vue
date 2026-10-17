<script setup lang="ts">
/**
 * 数据源管理组件
 * 支持数据源列表展示、新建数据源组、删除、设为默认
 */
import { ref, computed, onMounted, h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import AdminPanelToolbar from './AdminPanelToolbar.vue'
import {
  getAdminDatasources,
  deleteAdminDatasource,
  getAdminDbTables,
  setAdminDefaultDatasource,
  unsetAdminDefaultDatasource,
  updateAdminMappedModules,
  createAdminDatasourceGroup,
  updateAdminDatasourceGroup,
  getAdminDatasourceTableGroups,
  updateAdminDatasourceTableGroups,
  updateAdminDatasourceTableBusinessDesc,
  getAdminDatasourceTableColumnDicts,
  syncAdminDatasourceMetadata,
} from '../api'
import type { ColumnInfo, DataSource, TableInfo } from '../types'
import type {
  DatasourceColumnDictPayload,
  DatasourceTableGroupItem,
} from '../api'
import TableIndexDialog from './TableIndexDialog.vue'
import ColumnDictEditDialog from './semantic/ColumnDictEditDialog.vue'

const emit = defineEmits<{
  (e: 'datasources-updated'): void
}>()

// 数据源列表
const datasources = ref<DataSource[]>([])
const datasourcesLoading = ref(false)
const expandedDatasources = ref<Set<string>>(new Set())
const expandedTables = ref<Set<string>>(new Set())
const collapsedFieldGroups = ref<Set<string>>(new Set())
const collapsedIndexGroups = ref<Set<string>>(new Set())
const datasourceTableSearchKeywords = ref<Record<string, string>>({})

// 数据源组
const showGroupModal = ref(false)
const creatingGroup = ref(false)
const editingGroupId = ref<string | null>(null)
const isEditingGroup = computed(() => !!editingGroupId.value)
const loadingGroupTables = ref(false)
const syncingGroupMetadata = ref(false)
const groupDbTables = ref<string[]>([])
const selectedGroupTables = ref<Set<string>>(new Set())
const groupTableSearchKeyword = ref('')
const groupTableSnapshotKey = ref('')
const savedGroupTemplateSnapshotKey = ref('')
const savedGroupConfigSnapshotKey = ref('')
const groupSourceTextareaRef = ref<HTMLTextAreaElement | null>(null)
const groupSourceScrollTop = ref(0)
const groupForm = ref({
  group_name: '',
  group_desc: '',
  query_context: '',
  recommended_questions_text: '',
  source_type: 'hive' as 'mysql' | 'hive',
  datasource_sources_text: '',
})

type GroupDatasourceSource = {
  org_name: string
  org_code: string
  query_executor_type: 'direct' | 'http'
  host?: string
  port?: number
  database: string
}

// 设置默认
const settingDefaultId = ref<string | null>(null)
// 删除确认状态
const confirmingDeleteDatasourceId = ref<string | null>(null)
// 删除状态
const deletingDatasourceId = ref<string | null>(null)
// 重命名
const renamingDatasourceId = ref<string | null>(null)

// 模块映射编辑
const mappingDsId = ref<string | null>(null)
const mappingModules = ref<string[]>([])
const mappingInput = ref('')
const savingMapping = ref(false)

// 表分组管理
const showTableGroupModal = ref(false)
const tableGroupDs = ref<DataSource | null>(null)
const tableGroupRows = ref<DatasourceTableGroupItem[]>([])
const tableGroupNameInput = ref('')
const tableGroupSearchKeyword = ref('')
const activeTableGroupName = ref('')
const editingTableGroupName = ref('')
const editingTableGroupInput = ref('')
const loadingTableGroups = ref(false)
const savingTableGroups = ref(false)

// 表索引管理
const showIndexModal = ref(false)
const indexContext = ref<{ datasource: DataSource; table: TableInfo } | null>(null)

// 表业务说明编辑
const editingTableBusinessDescKey = ref('')
const tableBusinessDescInput = ref('')
const savingTableBusinessDescKey = ref('')

// 字段数据字典维护（弹窗能力统一由 ColumnDictEditDialog 承载，本组件只管入口与维护状态标记）
const showColumnDictModal = ref(false)
const columnDictContext = ref<{ datasource: DataSource; table: TableInfo; column: ColumnInfo } | null>(null)
const columnDictStatusMap = ref<Record<string, boolean>>({})
const columnDictStatusLoadedKeys = ref<Set<string>>(new Set())
const columnDictStatusLoadingKeys = ref<Set<string>>(new Set())

const hasColumnDictContent = (dict?: Partial<DatasourceColumnDictPayload> | null) => {
  if (!dict) return false
  return Boolean(
    String(dict.enum_ref || '').trim() ||
    (dict.enum_values || []).length > 0 ||
    String(dict.storage_format || '').trim() ||
    String(dict.unit || '').trim()
  )
}

// 指标集健康校验已移入「模型管理」面板（SemanticModelAdminPanel + SemanticHealthModal）

const openMappingPopover = (ds: DataSource) => {
  mappingDsId.value = ds.id
  mappingModules.value = [...(ds.mapped_modules || [])]
  mappingInput.value = ''
}

const closeMappingPopover = () => {
  mappingDsId.value = null
  mappingModules.value = []
  mappingInput.value = ''
}

const addMappingModule = () => {
  const val = mappingInput.value.trim()
  if (val && !mappingModules.value.includes(val)) {
    mappingModules.value.push(val)
  }
  mappingInput.value = ''
}

const removeMappingModule = (index: number) => {
  mappingModules.value.splice(index, 1)
}

const saveMappingModules = async () => {
  if (!mappingDsId.value) return
  savingMapping.value = true
  try {
    const res = await updateAdminMappedModules(mappingDsId.value, mappingModules.value)
    if (res.status === 'success') {
      ElMessage.success('映射模块已保存')
      closeMappingPopover()
      fetchDatasources(true)
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch {
    ElMessage.error('保存失败')
  } finally {
    savingMapping.value = false
  }
}

const tableGroupTables = computed(() => tableGroupDs.value?.ds_tables || [])
const filteredTableGroupTables = computed(() => {
  const keyword = tableGroupSearchKeyword.value.trim().toLowerCase()
  if (!keyword) return tableGroupTables.value
  return tableGroupTables.value.filter(table =>
    table.table_name.toLowerCase().includes(keyword) ||
    table.description?.toLowerCase().includes(keyword) ||
    table.business_desc?.toLowerCase().includes(keyword)
  )
})
const activeTableGroup = computed(() =>
  tableGroupRows.value.find(item => item.table_group_name === activeTableGroupName.value) || null
)
const activeTableGroupTableNames = computed(() =>
  new Set(activeTableGroup.value?.table_names || [])
)
const getDatasourceTableSearchKeyword = (datasourceId: string) => datasourceTableSearchKeywords.value[datasourceId] || ''

const setDatasourceTableSearchKeyword = (datasourceId: string, keyword: string) => {
  datasourceTableSearchKeywords.value = { ...datasourceTableSearchKeywords.value, [datasourceId]: keyword }
}

const getFilteredDatasourceTables = (ds: DataSource) => {
  const tables = ds.ds_tables || []
  const keyword = getDatasourceTableSearchKeyword(ds.id).trim().toLowerCase()
  if (!keyword) return tables
  return tables.filter(table =>
    table.table_name.toLowerCase().includes(keyword) ||
    (table.description || '').toLowerCase().includes(keyword) ||
    (table.business_desc || '').toLowerCase().includes(keyword)
  )
}

const normalizeTableGroupRows = (groups: DatasourceTableGroupItem[]) => {
  const map = new Map<string, Set<string>>()
  groups.forEach(group => {
    const groupName = group.table_group_name.trim()
    if (!groupName) return
    if (!map.has(groupName)) map.set(groupName, new Set())
    ;(group.table_names || []).forEach(tableName => {
      const normalizedTableName = String(tableName || '').trim()
      if (normalizedTableName) map.get(groupName)?.add(normalizedTableName)
    })
  })
  return Array.from(map.entries())
    .map(([table_group_name, tableNames]) => ({ table_group_name, table_names: Array.from(tableNames).sort((a, b) => a.localeCompare(b)) }))
    .sort((a, b) => a.table_group_name.localeCompare(b.table_group_name))
}

const closeTableGroupModal = () => {
  showTableGroupModal.value = false
  tableGroupDs.value = null
  tableGroupRows.value = []
  tableGroupNameInput.value = ''
  tableGroupSearchKeyword.value = ''
  activeTableGroupName.value = ''
  editingTableGroupName.value = ''
  editingTableGroupInput.value = ''
}

const openTableGroupModal = async (ds: DataSource) => {
  tableGroupDs.value = ds
  showTableGroupModal.value = true
  tableGroupRows.value = []
  tableGroupNameInput.value = ''
  tableGroupSearchKeyword.value = ''
  activeTableGroupName.value = ''
  editingTableGroupName.value = ''
  editingTableGroupInput.value = ''
  loadingTableGroups.value = true
  try {
    const res = await getAdminDatasourceTableGroups(ds.id)
    if (res.status === 'success') {
      tableGroupRows.value = normalizeTableGroupRows(res.data?.groups || [])
      activeTableGroupName.value = tableGroupRows.value[0]?.table_group_name || ''
    } else {
      ElMessage.error(res.msg || '获取表分组失败')
    }
  } catch {
    ElMessage.error('获取表分组失败')
  } finally {
    loadingTableGroups.value = false
  }
}

const addTableGroup = () => {
  const groupName = tableGroupNameInput.value.trim()
  if (!groupName) {
    ElMessage.warning('请输入分组名称')
    return
  }
  if (tableGroupRows.value.some(item => item.table_group_name === groupName)) {
    ElMessage.warning('分组名称已存在')
    return
  }
  tableGroupRows.value = [...tableGroupRows.value, { table_group_name: groupName, table_names: [] }]
    .sort((a, b) => a.table_group_name.localeCompare(b.table_group_name))
  activeTableGroupName.value = groupName
  tableGroupNameInput.value = ''
}

const startRenameTableGroup = (groupName: string) => {
  editingTableGroupName.value = groupName
  editingTableGroupInput.value = groupName
  activeTableGroupName.value = groupName
}

const cancelRenameTableGroup = () => {
  editingTableGroupName.value = ''
  editingTableGroupInput.value = ''
}

const confirmRenameTableGroup = (oldGroupName: string) => {
  const nextGroupName = editingTableGroupInput.value.trim()
  if (!nextGroupName) {
    ElMessage.warning('请输入分组名称')
    return
  }
  if (nextGroupName === oldGroupName) {
    cancelRenameTableGroup()
    return
  }
  if (tableGroupRows.value.some(item => item.table_group_name === nextGroupName)) {
    ElMessage.warning('分组名称已存在')
    return
  }
  tableGroupRows.value = tableGroupRows.value
    .map(group => group.table_group_name === oldGroupName ? { ...group, table_group_name: nextGroupName } : group)
    .sort((a, b) => a.table_group_name.localeCompare(b.table_group_name))
  if (activeTableGroupName.value === oldGroupName) {
    activeTableGroupName.value = nextGroupName
  }
  cancelRenameTableGroup()
}

const deleteTableGroup = async (groupName: string) => {
  try {
    await ElMessageBox.confirm(`确定删除分组"${groupName}"吗？`, '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  tableGroupRows.value = tableGroupRows.value.filter(item => item.table_group_name !== groupName)
  if (activeTableGroupName.value === groupName) {
    activeTableGroupName.value = tableGroupRows.value[0]?.table_group_name || ''
  }
}

const toggleActiveTableGroupTable = (tableName: string) => {
  if (!activeTableGroup.value) return
  const nextRows = tableGroupRows.value.map(group => {
    if (group.table_group_name !== activeTableGroupName.value) return group
    const tableNames = new Set(group.table_names || [])
    tableNames.has(tableName) ? tableNames.delete(tableName) : tableNames.add(tableName)
    return { ...group, table_names: Array.from(tableNames).sort((a, b) => a.localeCompare(b)) }
  })
  tableGroupRows.value = nextRows
}

const saveTableGroups = async () => {
  if (!tableGroupDs.value) return
  savingTableGroups.value = true
  try {
    const payload = normalizeTableGroupRows(tableGroupRows.value)
      .filter(group => group.table_group_name.trim() && group.table_names.length > 0)
    const res = await updateAdminDatasourceTableGroups(tableGroupDs.value.id, payload)
    if (res.status === 'success') {
      ElMessage.success('表分组已保存')
      await fetchDatasources(true)
      closeTableGroupModal()
    } else {
      ElMessage.error(res.msg || '保存表分组失败')
    }
  } catch {
    ElMessage.error('保存表分组失败')
  } finally {
    savingTableGroups.value = false
  }
}

const buildTableKey = (datasourceId: string, tableName: string) => `${datasourceId}-${tableName}`
const buildColumnDictKey = (datasourceId: string, tableName: string, columnName: string) => `${datasourceId}::${tableName}::${columnName}`

const isColumnDictMaintained = (datasourceId: string, tableName: string, columnName: string) =>
  Boolean(columnDictStatusMap.value[buildColumnDictKey(datasourceId, tableName, columnName)])

const setColumnDictMaintained = (datasourceId: string, tableName: string, columnName: string, maintained: boolean) => {
  const key = buildColumnDictKey(datasourceId, tableName, columnName)
  columnDictStatusMap.value = { ...columnDictStatusMap.value, [key]: maintained }
}

const isColumnDictStatusLoading = (datasourceId: string, tableName: string) =>
  columnDictStatusLoadingKeys.value.has(buildTableKey(datasourceId, tableName))

const loadColumnDictStatusForTable = async (ds: DataSource, table: TableInfo) => {
  const tableKey = buildTableKey(ds.id, table.table_name)
  if (columnDictStatusLoadedKeys.value.has(tableKey) || columnDictStatusLoadingKeys.value.has(tableKey)) return

  columnDictStatusLoadingKeys.value = new Set([...columnDictStatusLoadingKeys.value, tableKey])
  try {
    let dicts: Record<string, DatasourceColumnDictPayload> = {}
    try {
      const res = await getAdminDatasourceTableColumnDicts(ds.id, table.table_name)
      dicts = res.status === 'success' ? (res.data?.dicts || {}) : {}
    } catch {
      dicts = {}
    }
    const entries = (table.columns || []).map(col => {
      const key = buildColumnDictKey(ds.id, table.table_name, col.name)
      return [key, hasColumnDictContent(dicts[col.name])] as const
    })
    columnDictStatusMap.value = { ...columnDictStatusMap.value, ...Object.fromEntries(entries) }
    columnDictStatusLoadedKeys.value = new Set([...columnDictStatusLoadedKeys.value, tableKey])
  } finally {
    const loadingKeys = new Set(columnDictStatusLoadingKeys.value)
    loadingKeys.delete(tableKey)
    columnDictStatusLoadingKeys.value = loadingKeys
  }
}

const startEditTableBusinessDesc = (ds: DataSource, table: TableInfo) => {
  editingTableBusinessDescKey.value = buildTableKey(ds.id, table.table_name)
  tableBusinessDescInput.value = table.business_desc || ''
}

const cancelEditTableBusinessDesc = () => {
  editingTableBusinessDescKey.value = ''
  tableBusinessDescInput.value = ''
}

const applyTableBusinessDesc = (datasourceId: string, tableName: string, businessDesc: string) => {
  datasources.value = datasources.value.map(ds => {
    if (ds.id !== datasourceId) return ds
    return {
      ...ds,
      ds_tables: (ds.ds_tables || []).map(table =>
        table.table_name === tableName ? { ...table, business_desc: businessDesc } : table
      ),
    }
  })
  emit('datasources-updated')
}

const saveTableBusinessDesc = async (ds: DataSource, table: TableInfo) => {
  const tableKey = buildTableKey(ds.id, table.table_name)
  const businessDesc = tableBusinessDescInput.value.trim()
  savingTableBusinessDescKey.value = tableKey
  try {
    const res = await updateAdminDatasourceTableBusinessDesc(ds.id, table.table_name, businessDesc)
    if (res.status === 'success') {
      applyTableBusinessDesc(ds.id, table.table_name, res.data?.table?.business_desc || businessDesc)
      ElMessage.success('表业务说明已保存')
      cancelEditTableBusinessDesc()
    } else {
      ElMessage.error(res.msg || '保存表业务说明失败')
    }
  } catch {
    ElMessage.error('保存表业务说明失败')
  } finally {
    savingTableBusinessDescKey.value = ''
  }
}

const openColumnDictModal = (ds: DataSource, table: TableInfo, column: ColumnInfo) => {
  columnDictContext.value = { datasource: ds, table, column }
  showColumnDictModal.value = true
}

const handleColumnDictSaved = (payload: DatasourceColumnDictPayload) => {
  const context = columnDictContext.value
  if (!context) return
  setColumnDictMaintained(
    context.datasource.id,
    context.table.table_name,
    context.column.name,
    hasColumnDictContent(payload)
  )
}

const memberSourceLineCount = computed(() =>
  groupForm.value.datasource_sources_text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean).length
)
const groupSourceLineNumbers = computed(() => {
  const lines = groupForm.value.datasource_sources_text.split('\n').length
  const total = Math.max(lines, 1)
  return Array.from({ length: total }, (_, index) => index + 1)
})
const filteredGroupTables = computed(() =>
  groupDbTables.value.filter(t => t.toLowerCase().includes(groupTableSearchKeyword.value.toLowerCase()))
)
const filteredSelectedGroupCount = computed(() =>
  filteredGroupTables.value.filter(table => selectedGroupTables.value.has(table)).length
)
const hasGroupTableSearchKeyword = computed(() => groupTableSearchKeyword.value.trim().length > 0)
const groupSelectionSummaryText = computed(() =>
  hasGroupTableSearchKeyword.value
    ? `${filteredSelectedGroupCount.value}/${filteredGroupTables.value.length}（筛选结果）`
    : `${selectedGroupTables.value.size}/${groupDbTables.value.length}`
)
const groupSelectAllButtonText = computed(() => hasGroupTableSearchKeyword.value ? '全选筛选结果' : '全选')
const groupDeselectAllButtonText = computed(() => hasGroupTableSearchKeyword.value ? '清空筛选结果' : '清空')
const groupSourcePlaceholder = computed(() => {
  if (groupForm.value.source_type === 'hive') {
    return '每行一个数据源，格式：\nHTTP：机构名称|机构编码|http|db_name\n直连：机构名称|机构编码|direct|ip:port:db_name\n机构编码用于权限匹配和 HTTP 执行标识，第一行默认作为模板数据源，用于拉取表结构\n例如：\n机构名称xx|机构编码xx|http|default\n机构名称yy|机构编码yy|direct|10.0.0.10:10000:dw_prod'
  }
  return '每行一个数据源，格式：\nHTTP：机构名称|机构编码|http|db_name\n直连：机构名称|机构编码|direct|ip:port:db_name\n机构编码用于权限匹配和 HTTP 执行标识，第一行默认作为模板数据源，用于拉取表结构\n例如：\n机构名称xx|机构编码xx|direct|db-host-1:3306:database_a\n机构名称yy|机构编码yy|http|database_b'
})

const parseRecommendedQuestions = () => {
  const questions: string[] = []
  const seen = new Set<string>()
  const lines = groupForm.value.recommended_questions_text.split('\n')
  for (let index = 0; index < lines.length; index += 1) {
    const question = (lines[index] || '').trim()
    if (!question) continue
    if (question.length > 50) {
      throw new Error(`推荐问题第 ${index + 1} 行不能超过 50 个字`)
    }
    if (seen.has(question)) continue
    seen.add(question)
    questions.push(question)
    if (questions.length > 50) {
      throw new Error('推荐问题不能超过 50 条')
    }
  }
  return questions
}

const parseTemplateSourceForSnapshot = () => {
  const firstLine = groupForm.value.datasource_sources_text
    .split('\n')
    .map(line => line.trim())
    .find(Boolean)
  if (!firstLine) return ''

  const segments = firstLine.split('|').map(item => item.trim())
  if (segments.length !== 4) {
    return ''
  }
  const executorType = segments[2] || ''
  const sourceText = segments[3] || ''

  if (executorType === 'http') {
    const database = sourceText.trim()
    return database ? `${groupForm.value.source_type}|http|${segments[1] || ''}|${database}` : ''
  }
  if (executorType !== 'direct') return ''
  const parts = sourceText.split(':')
  if (parts.length !== 3) return ''
  const host = parts[0]?.trim() || ''
  const port = parts[1]?.trim() || ''
  const database = parts[2]?.trim() || ''
  if (!host || !port || !database) return ''
  return `${groupForm.value.source_type}|${host}:${port}:${database}`
}

const buildGroupSourceSnapshotKey = () => parseTemplateSourceForSnapshot()

const buildGroupConfigSnapshotKey = () => JSON.stringify({
  group_name: groupForm.value.group_name.trim(),
  group_desc: groupForm.value.group_desc.trim(),
  query_context: groupForm.value.query_context.trim(),
  recommended_questions_text: groupForm.value.recommended_questions_text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .join('\n'),
  source_type: groupForm.value.source_type,
  datasource_sources_text: groupForm.value.datasource_sources_text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .join('\n'),
})

const hasGroupTemplateSourceChanged = computed(() => {
  if (!showGroupModal.value || !groupTableSnapshotKey.value) return false
  const currentTemplateSnapshotKey = buildGroupSourceSnapshotKey()
  if (!currentTemplateSnapshotKey) return false
  return currentTemplateSnapshotKey !== groupTableSnapshotKey.value
})
const hasUnsavedGroupConfig = computed(() =>
  Boolean(
    isEditingGroup.value &&
    savedGroupConfigSnapshotKey.value &&
    buildGroupConfigSnapshotKey() !== savedGroupConfigSnapshotKey.value
  )
)
const canSyncGroupMetadata = computed(() =>
  Boolean(
    isEditingGroup.value &&
    selectedGroupTables.value.size > 0 &&
    groupTableSnapshotKey.value &&
    savedGroupTemplateSnapshotKey.value &&
    groupTableSnapshotKey.value === savedGroupTemplateSnapshotKey.value &&
    !hasUnsavedGroupConfig.value &&
    !hasGroupTemplateSourceChanged.value
  )
)
const canSaveGroupConfig = computed(() => !isEditingGroup.value || hasUnsavedGroupConfig.value)

const resetGroupTableSelector = () => {
  groupDbTables.value = []
  selectedGroupTables.value = new Set()
  groupTableSearchKeyword.value = ''
  groupTableSnapshotKey.value = ''
  savedGroupTemplateSnapshotKey.value = ''
}

const emitDatasourcesUpdated = () => {
  emit('datasources-updated')
}

const normalizeDatasourceList = (items: DataSource[] = []) => {
  return items.filter(ds => ds.group_name && ds.group_name.trim() !== '')
}

const applyDatasourceList = (items: DataSource[]) => {
  datasources.value = normalizeDatasourceList(items)
  columnDictStatusMap.value = {}
  columnDictStatusLoadedKeys.value = new Set()
  columnDictStatusLoadingKeys.value = new Set()
}

const replaceDatasourceInList = (updatedDatasource?: DataSource | null, keepCurrentTables = false, notifyUpdated = true) => {
  if (!updatedDatasource?.id) return
  const currentIndex = datasources.value.findIndex(ds => ds.id === updatedDatasource.id)
  const currentDatasource = currentIndex >= 0 ? datasources.value[currentIndex] : null
  const nextDatasource = {
    ...(currentDatasource || {}),
    ...updatedDatasource,
    ds_tables: keepCurrentTables && currentDatasource?.ds_tables
      ? currentDatasource.ds_tables
      : (updatedDatasource.ds_tables || currentDatasource?.ds_tables || []),
  } as DataSource

  if (!nextDatasource.group_name?.trim()) return
  const nextDatasources = [...datasources.value]
  if (currentIndex >= 0) nextDatasources.splice(currentIndex, 1, nextDatasource)
  else nextDatasources.unshift(nextDatasource)
  datasources.value = nextDatasources
  if (notifyUpdated) emitDatasourcesUpdated()
}

// 加载数据源
const fetchDatasources = async (notifyUpdated = false) => {
  datasourcesLoading.value = true
  try {
    const res = await getAdminDatasources()
    if (res.status === 'success') {
      applyDatasourceList(res.data.datasources || [])
      if (notifyUpdated) emitDatasourcesUpdated()
    }
  } catch (err) {
    console.error('获取数据源失败:', err)
  } finally {
    datasourcesLoading.value = false
  }
}

// 展开/折叠
const toggleDatasource = (dsId: string) => {
  const s = new Set(expandedDatasources.value)
  s.has(dsId) ? s.delete(dsId) : s.add(dsId)
  expandedDatasources.value = s
}

const toggleTable = (ds: DataSource, table: TableInfo) => {
  const tableKey = buildTableKey(ds.id, table.table_name)
  const s = new Set(expandedTables.value)
  const shouldExpand = !s.has(tableKey)
  shouldExpand ? s.add(tableKey) : s.delete(tableKey)
  expandedTables.value = s
  if (shouldExpand) {
    void loadColumnDictStatusForTable(ds, table)
  }
}

const toggleFieldGroup = (dsId: string, tableName: string) => {
  const key = buildTableKey(dsId, tableName)
  const s = new Set(collapsedFieldGroups.value)
  s.has(key) ? s.delete(key) : s.add(key)
  collapsedFieldGroups.value = s
}

const toggleIndexGroup = (dsId: string, tableName: string) => {
  const key = buildTableKey(dsId, tableName)
  const s = new Set(collapsedIndexGroups.value)
  s.has(key) ? s.delete(key) : s.add(key)
  collapsedIndexGroups.value = s
}

// 删除数据源
const handleDelete = async (ds: DataSource) => {
  if (confirmingDeleteDatasourceId.value || deletingDatasourceId.value) return
  confirmingDeleteDatasourceId.value = ds.id
  try {
    await ElMessageBox.confirm(`确定要删除数据源"${ds.group_name}"吗？`, '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  } finally {
    confirmingDeleteDatasourceId.value = null
  }

  deletingDatasourceId.value = ds.id
  try {
    const res = await deleteAdminDatasource(ds.id)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      await fetchDatasources(true)
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch {
    ElMessage.error('删除失败')
  } finally {
    deletingDatasourceId.value = null
  }
}

// 设置/取消默认
const handleToggleDefault = async (ds: DataSource) => {
  settingDefaultId.value = ds.id
  try {
    const isDefault = ds.is_default
    const res = isDefault
      ? await unsetAdminDefaultDatasource(ds.id)
      : await setAdminDefaultDatasource(ds.id)
    if (res.status === 'success') {
      ElMessage.success(isDefault ? '已取消默认' : '已设为默认')
      fetchDatasources(true)
    } else {
      ElMessage.error(res.msg || '操作失败')
    }
  } catch {
    ElMessage.error('操作失败')
  } finally {
    settingDefaultId.value = null
  }
}

const handleEditDatasource = (ds: DataSource) => {
  openEditGroupModal(ds)
}

const openGroupModal = () => {
  editingGroupId.value = null
  savedGroupConfigSnapshotKey.value = ''
  groupForm.value = {
    group_name: '',
    group_desc: '',
    query_context: '',
    recommended_questions_text: '',
    source_type: 'hive',
    datasource_sources_text: '',
  }
  resetGroupTableSelector()
  showGroupModal.value = true
}

const closeGroupModal = () => {
  showGroupModal.value = false
  editingGroupId.value = null
  savedGroupConfigSnapshotKey.value = ''
}

const formatGroupSourceLine = (item: Partial<GroupDatasourceSource>) => {
  const executorType = item.query_executor_type || 'direct'
  const database = String(item.database || '').trim()
  if (executorType === 'http') {
    return `${item.org_name || ''}|${item.org_code || ''}|http|${database}`
  }
  return `${item.org_name || ''}|${item.org_code || ''}|direct|${item.host || ''}:${item.port || ''}:${database}`
}

const openEditGroupModal = (ds: DataSource) => {
  editingGroupId.value = ds.id
  const sourceType = ((ds.source_type || 'mysql') as 'mysql' | 'hive')
  const memberSourcesText = String((ds as DataSource & { member_sources_text?: string }).member_sources_text || '').trim()
  groupForm.value = {
    group_name: ds.group_name || '',
    group_desc: ds.ds_description || '',
    query_context: ds.query_context || '',
    recommended_questions_text: (ds.recommended_questions || []).join('\n'),
    source_type: sourceType,
    datasource_sources_text: memberSourcesText
      ? memberSourcesText
      : (ds.member_sources || []).map(formatGroupSourceLine).join('\n'),
  }
  groupDbTables.value = (ds.ds_tables || []).map(table => table.table_name).filter(Boolean)
  selectedGroupTables.value = new Set(groupDbTables.value)
  groupTableSearchKeyword.value = ''
  groupTableSnapshotKey.value = buildGroupSourceSnapshotKey()
  savedGroupTemplateSnapshotKey.value = groupTableSnapshotKey.value
  savedGroupConfigSnapshotKey.value = buildGroupConfigSnapshotKey()
  showGroupModal.value = true
}

const handleGroupSourceTypeChange = (sourceType: 'hive' | 'mysql') => {
  groupForm.value.source_type = sourceType
}

const handleGroupSourceScroll = (event: Event) => {
  groupSourceScrollTop.value = (event.target as HTMLTextAreaElement).scrollTop || 0
}

const parseMemberSources = () => {
  const lines = groupForm.value.datasource_sources_text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)

  const members: GroupDatasourceSource[] = []
  const nameSet = new Set<string>()
  const orgCodeSet = new Set<string>()
  const identitySet = new Set<string>()
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] || ''
    const segments = line.split('|').map(item => item.trim())
    let orgName = ''
    let orgCode = ''
    let queryExecutorType: 'direct' | 'http' = 'direct'
    let sourceText = ''

    if (groupForm.value.source_type === 'hive' || groupForm.value.source_type === 'mysql') {
      if (segments.length !== 4) {
        throw new Error(`第 ${index + 1} 行格式错误，请使用固定格式：HTTP 为 机构名称|机构编码|http|db_name，直连为 机构名称|机构编码|direct|ip:port:db_name`)
      }
      orgName = segments[0] || ''
      orgCode = segments[1] || ''
      queryExecutorType = segments[2] === 'http' ? 'http' : 'direct'
      sourceText = segments[3] || ''
      if (!orgName) {
        throw new Error(`第 ${index + 1} 行缺少机构名称`)
      }
      if (!orgCode) {
        throw new Error(`第 ${index + 1} 行缺少机构编码`)
      }
      if (!['direct', 'http'].includes(segments[2] || '')) {
        throw new Error(`第 ${index + 1} 行查询方式错误，仅支持 direct 或 http`)
      }
    }

    let host = ''
    let port: number | undefined
    let database = ''
    if (queryExecutorType === 'http') {
      if (sourceText.includes(':')) {
        throw new Error(`第 ${index + 1} 行格式错误，HTTP 模式只填写数据库名，不填写 IP 和端口`)
      }
      database = sourceText.trim()
      if (!database) {
        throw new Error(`第 ${index + 1} 行缺少数据库名`)
      }
    } else {
      const parts = sourceText.split(':')
      if (parts.length !== 3) {
        throw new Error(`第 ${index + 1} 行格式错误，请检查直连连接串`)
      }
      host = parts[0]?.trim() || ''
      port = Number(parts[1]?.trim() || '')
      database = parts[2]?.trim() || ''
      if (!host || !Number.isInteger(port) || port <= 0 || !database) {
        throw new Error(`第 ${index + 1} 行格式错误，请检查直连连接串`)
      }
    }
    if (orgName) {
      if (nameSet.has(orgName)) {
        throw new Error(`第 ${index + 1} 行名称重复：${orgName}`)
      }
      nameSet.add(orgName)
    }
    if (orgCodeSet.has(orgCode)) {
      throw new Error(`第 ${index + 1} 行机构编码重复：${orgCode}`)
    }
    orgCodeSet.add(orgCode)
    const identityKey = orgCode
    if (identitySet.has(identityKey)) {
      throw new Error(`第 ${index + 1} 行机构编码重复：${orgCode}`)
    }
    identitySet.add(identityKey)
    const source: GroupDatasourceSource = {
      org_name: orgName,
      org_code: orgCode,
      query_executor_type: queryExecutorType,
      database,
    }
    if (queryExecutorType === 'direct') {
      source.host = host
      source.port = port
    }
    members.push(source)
  }
  return members
}

const fetchGroupDbTables = async () => {
  if (!isEditingGroup.value) {
    ElMessage.warning('请先保存配置后再拉取表信息')
    return
  }
  if (hasUnsavedGroupConfig.value) {
    ElMessage.warning('配置已变更，请先保存配置后再拉取表信息')
    return
  }
  let datasourceSources: GroupDatasourceSource[] = []
  try {
    datasourceSources = parseMemberSources()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '数据源格式错误')
    return
  }
  if (datasourceSources.length === 0) {
    ElMessage.error('请至少输入一个数据源，第一行作为模板数据源')
    return
  }

  const templateSource = datasourceSources[0]
  if (!templateSource) {
    ElMessage.error('请至少输入一个数据源，第一行作为模板数据源')
    return
  }
  loadingGroupTables.value = true
  resetGroupTableSelector()
  try {
    const payload: {
      source_type: 'mysql' | 'hive'
      query_executor_type: 'direct' | 'http'
      org_code: string
      org_name: string
      database: string
      host?: string
      port?: number
    } = {
      source_type: groupForm.value.source_type,
      query_executor_type: templateSource.query_executor_type || 'direct',
      org_code: templateSource.org_code || '',
      org_name: templateSource.org_name || '',
      database: templateSource.database,
    }
    if ((templateSource.query_executor_type || 'direct') === 'direct') {
      payload.host = templateSource.host || ''
      payload.port = templateSource.port
    }
    const res = await getAdminDbTables(payload)
    if (res.status === 'success') {
      const tables = res.data.tables || []
      groupDbTables.value = tables
      selectedGroupTables.value = new Set(tables)
      groupTableSnapshotKey.value = buildGroupSourceSnapshotKey()
      savedGroupTemplateSnapshotKey.value = groupTableSnapshotKey.value
      ElMessage.success(`获取成功，共 ${tables.length} 个表`)
    } else {
      ElMessage.error(res.msg || '获取表列表失败')
    }
  } catch {
    ElMessage.error('连接失败')
  } finally {
    loadingGroupTables.value = false
  }
}

const toggleGroupTableSelection = (tableName: string) => {
  const nextSelected = new Set(selectedGroupTables.value)
  if (nextSelected.has(tableName)) nextSelected.delete(tableName)
  else nextSelected.add(tableName)
  selectedGroupTables.value = nextSelected
}

const selectAllGroupTables = () => {
  selectedGroupTables.value = new Set([...selectedGroupTables.value, ...filteredGroupTables.value])
}

const deselectAllGroupTables = () => {
  const nextSelected = new Set(selectedGroupTables.value)
  filteredGroupTables.value.forEach(table => nextSelected.delete(table))
  selectedGroupTables.value = nextSelected
}

const buildGroupConfigPayload = (templateSource: GroupDatasourceSource, memberSources: GroupDatasourceSource[], recommendedQuestions: string[]) => ({
  group_name: groupForm.value.group_name.trim(),
  group_desc: groupForm.value.group_desc.trim(),
  query_context: groupForm.value.query_context.trim(),
  recommended_questions: recommendedQuestions,
  source_type: groupForm.value.source_type,
  template_source: templateSource,
  member_sources: memberSources,
})

const handleCreateDatasourceGroup = async () => {
  const groupName = groupForm.value.group_name.trim()
  if (!groupName) {
    ElMessage.error('请输入数据源组名称')
    return
  }

  let datasourceSources: GroupDatasourceSource[] = []
  try {
    datasourceSources = parseMemberSources()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '数据源格式错误')
    return
  }
  if (datasourceSources.length === 0) {
    ElMessage.error('请至少输入一个数据源，第一行作为模板数据源')
    return
  }

  let recommendedQuestions: string[] = []
  try {
    recommendedQuestions = parseRecommendedQuestions()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '推荐问题格式错误')
    return
  }

  const currentTemplateSnapshotKey = buildGroupSourceSnapshotKey()
  if (!currentTemplateSnapshotKey) {
    ElMessage.error('模板数据源格式错误，请先按固定格式修正第一行')
    return
  }
  if (groupDbTables.value.length > 0 && groupTableSnapshotKey.value && groupTableSnapshotKey.value !== currentTemplateSnapshotKey) {
    ElMessage.warning('模板数据源已变化，保存配置后请重新拉取表信息并同步元数据')
  }
  const shouldResetTableSelectorAfterSave = !editingGroupId.value || groupTableSnapshotKey.value !== currentTemplateSnapshotKey

  const [templateSource, ...memberSources] = datasourceSources
  if (!templateSource) {
    ElMessage.error('请至少输入一个数据源，第一行作为模板数据源')
    return
  }

  creatingGroup.value = true
  try {
    const payload = buildGroupConfigPayload(templateSource, memberSources, recommendedQuestions)
    const res = editingGroupId.value
      ? await updateAdminDatasourceGroup(editingGroupId.value, payload)
      : await createAdminDatasourceGroup(payload)
    if (res.status === 'success') {
      const wasEditing = Boolean(editingGroupId.value)
      ElMessage.success(wasEditing ? '配置已保存' : '数据源组创建成功')
      replaceDatasourceInList(res.data, true, false)
      if (res.data?.id) {
        editingGroupId.value = res.data.id
      }
      await fetchDatasources(true)
      savedGroupConfigSnapshotKey.value = buildGroupConfigSnapshotKey()
      if (shouldResetTableSelectorAfterSave) {
        resetGroupTableSelector()
      } else {
        savedGroupTemplateSnapshotKey.value = currentTemplateSnapshotKey
      }
    } else {
      ElMessage.error(res.msg || (editingGroupId.value ? '数据源组更新失败' : '数据源组创建失败'))
    }
  } catch {
    ElMessage.error(editingGroupId.value ? '数据源组更新失败' : '数据源组创建失败')
  } finally {
    creatingGroup.value = false
  }
}

const handleSyncGroupMetadata = async () => {
  if (!editingGroupId.value) return
  let datasourceSources: GroupDatasourceSource[] = []
  try {
    datasourceSources = parseMemberSources()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '数据源格式错误')
    return
  }
  const templateSource = datasourceSources[0]
  if (!templateSource) {
    ElMessage.error('请至少输入一个数据源，第一行作为模板数据源')
    return
  }
  if (selectedGroupTables.value.size === 0) {
    ElMessage.error('请至少选择一张表')
    return
  }
  if (hasUnsavedGroupConfig.value) {
    ElMessage.warning('配置已变更，请先保存配置后再同步元数据')
    return
  }
  if (hasGroupTemplateSourceChanged.value) {
    ElMessage.warning('模板数据源已变化，请先保存配置后重新拉取表信息')
    return
  }
  if (!canSyncGroupMetadata.value) {
    ElMessage.warning('当前表信息不是基于已保存模板源，请先保存配置后重新拉取表信息')
    return
  }
  syncingGroupMetadata.value = true
  try {
    const res = await syncAdminDatasourceMetadata(editingGroupId.value, {
      template_table_names: Array.from(selectedGroupTables.value),
    })
    if (res.status === 'success') {
      ElMessage.success(res.msg || '元数据同步成功')
      replaceDatasourceInList(res.data?.datasource)
    } else {
      ElMessage.error(res.msg || '元数据同步失败')
    }
  } catch {
    ElMessage.error('元数据同步失败')
  } finally {
    syncingGroupMetadata.value = false
  }
}

// 获取列图标
const getColumnIcon = (col: any) => {
  if (col.primary_key) return 'fa-key'
  if (col.unique_key) return 'fa-fingerprint'
  if (col.index_key) return 'fa-bolt'
  if (col.is_partition) return 'fa-layer-group'
  return 'fa-circle'
}

const getColumnIconColor = (col: any) => {
  if (col.primary_key) return 'text-yellow-600'
  if (col.unique_key) return 'text-blue-600'
  if (col.index_key) return 'text-green-600'
  if (col.is_partition) return 'text-blue-600'
  return 'text-slate-400'
}

const getColumnIconTitle = (col: any) => {
  if (col.primary_key) return '主键'
  if (col.unique_key) return '唯一键'
  if (col.index_key) return '索引'
  if (col.is_partition) return '分区字段'
  return ''
}

// 表级索引聚合
interface DisplayTableIndex {
  type: 'primary' | 'unique' | 'secondary'
  source: 'physical' | 'manual'
  name: string
  columns: string[]
}

const getTableDisplayIndexes = (table: TableInfo): DisplayTableIndex[] => {
  const result: DisplayTableIndex[] = []
  for (const columns of table.primary_keys || []) {
    result.push({ type: 'primary', source: 'physical', name: 'PRIMARY', columns })
  }
  for (const ui of table.unique_indexes || []) {
    result.push({ type: 'unique', source: 'physical', name: ui.name || 'UNIQUE', columns: ui.columns || [] })
  }
  for (const ii of table.indexes || []) {
    result.push({ type: 'secondary', source: 'physical', name: ii.name || 'INDEX', columns: ii.columns || [] })
  }
  for (const item of table.manual_indexes || []) {
    result.push({
      type: item.index_type,
      source: 'manual',
      name: item.index_name || (item.index_type === 'primary' ? 'PRIMARY' : 'INDEX'),
      columns: item.columns || [],
    })
  }
  return result
}

const hasTableIndexes = (table: TableInfo) => getTableDisplayIndexes(table).length > 0

const closeIndexModal = () => {
  showIndexModal.value = false
  indexContext.value = null
}

const openIndexModal = (datasource: DataSource, table: TableInfo) => {
  indexContext.value = { datasource, table }
  showIndexModal.value = true
}

const handleIndexesSaved = (indexes: NonNullable<TableInfo['manual_indexes']>) => {
  if (indexContext.value) {
    indexContext.value.table.manual_indexes = indexes.map(item => ({
      ...item,
      columns: [...item.columns],
      source: 'manual',
    }))
  }
  closeIndexModal()
}

const getDatasourceConnectionTitle = (ds: DataSource) => {
  const sourceType = (ds.source_type || '').toLowerCase()
  const config = (ds.source_config || {}) as Record<string, unknown>
  const host = String(config.host || '')
  const port = String(config.port || '')
  const database = String(config.database || '')

  if (sourceType === 'mysql' || sourceType === 'hive') {
    const protocol = sourceType.toUpperCase()
    const endpoint = [host, port].filter(Boolean).join(':')
    const databasePath = database ? `/${database}` : ''
    if (endpoint) return `${protocol} ${endpoint}${databasePath}`
    return `${protocol} 数据源`
  }

  return ds.ds_description || '数据源'
}

const getDatasourceEndpoint = (ds: DataSource) => {
  const sourceType = (ds.source_type || '').toLowerCase()
  if (sourceType !== 'mysql' && sourceType !== 'hive') return ''
  const config = (ds.source_config || {}) as Record<string, unknown>
  const host = String(config.host || '')
  const port = String(config.port || '')
  const database = String(config.database || '')
  const endpoint = [host, port].filter(Boolean).join(':')
  if (!endpoint && !database) return ''
  return `${endpoint}${database ? `/${database}` : ''}`
}

const formatDatasourceOperator = (value?: string) => {
  const text = String(value || '').trim()
  return text || '未知'
}

const getDatasourceCreatorName = (ds: DataSource) => {
  return formatDatasourceOperator(ds.create_by_name)
}

const getDatasourceUpdaterName = (ds: DataSource) => {
  return formatDatasourceOperator(ds.update_by_name)
}

const buildDatasourceMaintainerTitle = (ds: DataSource) => {
  const creator = getDatasourceCreatorName(ds)
  const updater = getDatasourceUpdaterName(ds)
  return `创建人：${creator}\n创建时间：${ds.create_time || '-'}\n修改人：${updater}\n修改时间：${ds.update_time || '-'}`
}

onMounted(() => {
  fetchDatasources()
})

// 暴露方法给父组件调用
defineExpose({
  openGroupModal,
  openEditGroupModal,
})
</script>

<template>
  <div class="chatdata-root flex h-full flex-col">
    <!-- 面板顶区：工具栏（新建动作属于本面板，不放弹窗头部） -->
    <AdminPanelToolbar>
      <template #actions>
        <el-button type="primary" size="small" round @click="openGroupModal">
          <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
          新建
        </el-button>
      </template>
    </AdminPanelToolbar>
    <!-- 数据源列表 -->
    <div class="flex-1 overflow-y-auto">
      <!-- 加载状态 -->
      <div v-if="datasourcesLoading" class="flex flex-col items-center justify-center py-12 text-center">
        <i class="fa-solid fa-spinner fa-spin mb-3 text-3xl text-slate-300"></i>
        <p class="text-sm text-slate-500">数据源加载中...</p>
      </div>

      <!-- 空状态 -->
      <div v-else-if="datasources.length === 0" class="flex flex-col items-center justify-center py-12 text-center">
        <i class="fa-solid fa-database mb-3 text-4xl text-slate-300"></i>
        <p class="text-sm text-slate-500">暂无数据源</p>
        <p class="mt-2 text-xs text-slate-400">点击上方"新建"按钮添加数据源</p>
      </div>

      <!-- 数据源树 -->
      <div v-else class="p-2">
        <div v-for="ds in datasources" :key="ds.id" class="mb-2">
          <!-- 数据源节点 -->
          <div class="group flex items-center gap-2 rounded px-2 py-1.5 transition-colors hover:bg-slate-100">
            <button class="flex flex-1 items-center gap-2 min-w-0" @click="toggleDatasource(ds.id)">
              <i :class="['fa-solid text-xs text-slate-500', expandedDatasources.has(ds.id) ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
              <i class="fa-solid fa-folder text-sm text-amber-600"></i>
              <span class="flex-1 text-left text-sm font-semibold text-slate-900 truncate" :title="getDatasourceConnectionTitle(ds)">{{ ds.group_name }}</span>
              <span
                class="flex max-w-[168px] shrink-0 items-center gap-1 truncate rounded border border-slate-200 bg-white px-2 py-0.5 text-xs text-slate-500"
                :title="buildDatasourceMaintainerTitle(ds)"
              >
                <i class="fa-regular fa-user text-[11px] text-slate-400"></i>
                <span class="shrink-0 text-slate-400">创建人</span>
                <span class="truncate">{{ getDatasourceCreatorName(ds) }}</span>
              </span>
              <span class="text-xs text-slate-500">{{ ds.ds_tables?.length || 0 }}</span>
            </button>

            <!-- 操作按钮 -->
            <div :class="['flex items-center gap-1 transition-opacity', ds.is_default ? 'opacity-100' : 'opacity-0 group-hover:opacity-100']">
              <button
                :disabled="settingDefaultId !== null || renamingDatasourceId !== null"
                :class="['flex h-7 min-w-[52px] items-center justify-center gap-1 rounded px-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                  ds.is_default ? 'text-yellow-500 hover:bg-yellow-50' : 'text-slate-400 hover:bg-slate-200 hover:text-yellow-500']"
                :title="ds.is_default ? '取消默认' : '设为默认'"
                @click.stop="handleToggleDefault(ds)"
              >
                <i :class="['text-xs', ds.is_default ? 'fa-solid fa-star' : 'fa-regular fa-star', settingDefaultId === ds.id ? 'fa-spin' : '']"></i>
                <span>{{ ds.is_default ? '默认' : '设默' }}</span>
              </button>
              <!-- 重命名 -->
              <button
                :disabled="renamingDatasourceId !== null"
                :class="['flex h-7 min-w-[52px] items-center justify-center gap-1 rounded px-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                  renamingDatasourceId === ds.id ? 'text-blue-600' : 'text-slate-600 hover:bg-slate-200']"
                title="修改数据源组"
                @click.stop="handleEditDatasource(ds)"
              >
                <i :class="['fa-solid fa-pen text-xs', renamingDatasourceId === ds.id ? 'fa-spin' : '']"></i>
                <span>编辑</span>
              </button>
              <button
                :disabled="loadingTableGroups || savingTableGroups"
                class="flex h-7 min-w-[52px] items-center justify-center gap-1 rounded px-2 text-xs text-slate-600 transition-colors hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
                title="表分组管理"
                @click.stop="openTableGroupModal(ds)"
              >
                <i class="fa-solid fa-layer-group text-xs"></i>
                <span>分组</span>
              </button>
              <!-- 模块映射 -->
              <el-popover
                :visible="mappingDsId === ds.id"
                placement="bottom"
                :width="260"
                trigger="manual"
                :popper-style="{ zIndex: 4001 }"
              >
                <template #reference>
                  <button
                    :style="{
                      display: 'flex',
                      minWidth: '52px',
                      height: '28px',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '4px',
                      borderRadius: '4px',
                      padding: '0 8px',
                      fontSize: '12px',
                      transition: 'all 0.2s',
                      color: (ds.mapped_modules && ds.mapped_modules.length > 0) ? '#2563eb' : '#94a3b8',
                    }"
                    title="模块映射"
                    @click.stop="mappingDsId === ds.id ? closeMappingPopover() : openMappingPopover(ds)"
                  >
                    <i class="fa-solid fa-tags" style="font-size: 12px;"></i>
                    <span>映射</span>
                  </button>
                </template>
                <div style="display: flex; flex-direction: column; gap: 10px;">
                  <div style="font-size: 12px; font-weight: 500; color: #475569;">模块映射</div>
                  <div style="display: flex; flex-wrap: wrap; gap: 6px; min-height: 28px;">
                    <span
                      v-for="(mod, idx) in mappingModules"
                      :key="idx"
                      style="display: inline-flex; align-items: center; gap: 4px; background: #eff6ff; color: #1d4ed8; font-size: 12px; padding: 2px 8px; border-radius: 10px;"
                    >
                      {{ mod }}
                      <i class="fa-solid fa-xmark" style="font-size: 10px; cursor: pointer; color: #93c5fd;" @click="removeMappingModule(idx)"></i>
                    </span>
                    <span v-if="mappingModules.length === 0" style="font-size: 12px; color: #94a3b8;">暂无映射</span>
                  </div>
                  <div style="display: flex; gap: 6px;">
                    <input
                      v-model="mappingInput"
                      type="text"
                      placeholder="输入模块名，回车添加"
                      style="flex: 1; border: 1px solid #e2e8f0; border-radius: 4px; padding: 4px 8px; font-size: 12px; outline: none;"
                      @keydown.enter.prevent="addMappingModule"
                      @focus="($event.target as HTMLInputElement).style.borderColor = '#3b82f6'"
                      @blur="($event.target as HTMLInputElement).style.borderColor = '#e2e8f0'"
                    />
                    <button
                      style="border-radius: 4px; background: #eff6ff; padding: 4px 10px; font-size: 12px; color: #2563eb; cursor: pointer; border: none;"
                      @click="addMappingModule"
                    >添加</button>
                  </div>
                  <div style="display: flex; justify-content: flex-end; gap: 6px; padding-top: 4px;">
                    <button
                      style="border-radius: 4px; padding: 4px 10px; font-size: 12px; color: #64748b; cursor: pointer; border: none; background: transparent;"
                      @click="closeMappingPopover"
                    >取消</button>
                    <button
                      :disabled="savingMapping"
                      :style="{
                        borderRadius: '4px', padding: '4px 10px', fontSize: '12px', color: '#fff', cursor: 'pointer',
                        border: 'none', background: savingMapping ? '#93c5fd' : '#3b82f6',
                      }"
                      @click="saveMappingModules"
                    >{{ savingMapping ? '保存中...' : '保存' }}</button>
                  </div>
                </div>
              </el-popover>
              <!-- 删除 -->
              <button
                v-if="ds.id !== 'default'"
                :disabled="!!confirmingDeleteDatasourceId || !!deletingDatasourceId"
                class="flex h-7 min-w-[52px] items-center justify-center gap-1 rounded px-2 text-xs text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                :title="deletingDatasourceId === ds.id ? '删除中...' : '删除数据源'"
                @click.stop="handleDelete(ds)"
              >
                <i :class="['fa-solid text-xs', deletingDatasourceId === ds.id ? 'fa-spinner fa-spin' : 'fa-trash']"></i>
                <span>{{ deletingDatasourceId === ds.id ? '删除中' : '删除' }}</span>
              </button>
            </div>
          </div>

          <!-- 表列表 -->
          <div v-if="expandedDatasources.has(ds.id)" class="ml-4 overflow-hidden">
            <div v-if="ds.ds_tables && ds.ds_tables.length > 0" class="border-l border-slate-200 py-1 overflow-x-auto">
              <div class="ml-2 mb-1 flex min-w-[360px] max-w-[520px] items-center gap-2 px-2 py-1">
                <div class="relative min-w-0 flex-1">
                  <i class="fa-solid fa-search absolute left-2.5 top-1/2 -translate-y-1/2 text-[11px] text-slate-400"></i>
                  <input
                    :value="getDatasourceTableSearchKeyword(ds.id)"
                    type="text"
                    placeholder="搜索表名、表备注或业务说明"
                    class="h-7 w-full rounded border border-slate-200 bg-white pl-7 pr-2 text-xs text-slate-600 outline-none focus:border-blue-500"
                    @input="setDatasourceTableSearchKeyword(ds.id, ($event.target as HTMLInputElement).value)"
                  />
                </div>
                <span class="shrink-0 text-[11px] text-slate-400">{{ getFilteredDatasourceTables(ds).length }}/{{ ds.ds_tables.length }}</span>
              </div>
              <div v-for="table in getFilteredDatasourceTables(ds)" :key="`${ds.id}-${table.table_name}`" class="ml-2">
                <!-- 表节点 -->
                <div class="flex min-w-max items-center gap-2 rounded px-2 py-1 text-left text-sm transition-colors hover:bg-slate-100 whitespace-nowrap">
                  <button
                    type="button"
                    class="flex min-w-0 items-center gap-2 text-left"
                    @click="toggleTable(ds, table)"
                  >
                    <i :class="['fa-solid text-xs text-slate-500', expandedTables.has(buildTableKey(ds.id, table.table_name)) ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
                    <i class="fa-solid fa-table text-xs text-blue-600"></i>
                    <span class="shrink-0 font-medium text-slate-900">{{ table.table_name }}</span>
                    <span v-if="table.description && table.description !== table.table_name" class="max-w-[240px] shrink-0 truncate text-xs text-slate-400">{{ table.description }}</span>
                    <span
                      v-if="table.business_desc"
                      class="max-w-[280px] shrink-0 truncate rounded border border-emerald-100 bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700"
                      :title="table.business_desc"
                    >{{ table.business_desc }}</span>
                  </button>
                  <button
                    type="button"
                    class="flex h-5 shrink-0 items-center gap-1 rounded px-1.5 text-xs text-blue-600 hover:bg-white hover:text-blue-700"
                    title="编辑表业务说明"
                    @click.stop="startEditTableBusinessDesc(ds, table)"
                  >
                    <i class="fa-solid fa-pen text-[10px]"></i>
                    <span>表描述</span>
                  </button>
                  <span class="text-xs text-slate-500">{{ table.columns?.length || 0 }}</span>
                </div>

                <div
                  v-if="editingTableBusinessDescKey === buildTableKey(ds.id, table.table_name)"
                  class="ml-8 mt-1 flex min-w-[520px] max-w-[760px] items-center gap-2 rounded border border-blue-100 bg-blue-50 px-2 py-1.5"
                >
                  <input
                    v-model="tableBusinessDescInput"
                    type="text"
                    maxlength="255"
                    placeholder="填写给业务人员看的表业务说明，不影响原表备注"
                    class="min-w-0 flex-1 rounded border border-blue-200 bg-white px-2 py-1 text-xs text-slate-700 outline-none focus:border-blue-500"
                    @keydown.enter.prevent="saveTableBusinessDesc(ds, table)"
                    @keydown.esc.prevent="cancelEditTableBusinessDesc"
                  />
                  <span class="shrink-0 text-[11px] text-slate-400">{{ tableBusinessDescInput.length }}/255</span>
                  <button
                    type="button"
                    :disabled="savingTableBusinessDescKey === buildTableKey(ds.id, table.table_name)"
                    class="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                    @click="saveTableBusinessDesc(ds, table)"
                  >
                    {{ savingTableBusinessDescKey === buildTableKey(ds.id, table.table_name) ? '保存中' : '保存' }}
                  </button>
                  <button
                    type="button"
                    :disabled="savingTableBusinessDescKey === buildTableKey(ds.id, table.table_name)"
                    class="rounded px-2.5 py-1 text-xs text-slate-500 hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
                    @click="cancelEditTableBusinessDesc"
                  >取消</button>
                </div>

                <!-- 列列表 -->
                <div v-if="expandedTables.has(buildTableKey(ds.id, table.table_name))" class="ml-6 overflow-hidden">
                  <div class="border-l border-slate-200 py-1">
                    <!-- 字段分组 -->
                    <div class="ml-2">
                      <div
                        class="flex cursor-pointer items-center gap-1.5 rounded px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
                        @click="toggleFieldGroup(ds.id, table.table_name)"
                      >
                        <i :class="['fa-solid text-[9px] text-slate-400', collapsedFieldGroups.has(buildTableKey(ds.id, table.table_name)) ? 'fa-chevron-right' : 'fa-chevron-down']"></i>
                        <i class="fa-solid fa-table-columns text-[10px] text-slate-400"></i>
                        <span>字段 ({{ table.columns.length }})</span>
                      </div>
                      <div v-show="!collapsedFieldGroups.has(buildTableKey(ds.id, table.table_name))">
                    <div
                      v-for="col in table.columns"
                      :key="col.name"
                      class="group ml-4 flex items-center gap-2 rounded px-2 py-1 text-xs transition-colors hover:bg-slate-50"
                    >
                      <i :class="['fa-solid text-[10px]', getColumnIcon(col), getColumnIconColor(col)]" :title="getColumnIconTitle(col)"></i>
                      <span class="font-mono font-medium text-slate-900">{{ col.name }}</span>
                      <span class="text-slate-500">{{ col.mysql_type || col.hive_type || col.type }}</span>
                      <span v-if="col.description" class="max-w-[360px] shrink-0 truncate text-slate-400">- {{ col.description }}</span>
                      <span
                        v-if="isColumnDictStatusLoading(ds.id, table.table_name)"
                        class="flex h-5 shrink-0 items-center gap-1 rounded px-1.5 text-[11px] text-slate-400"
                        title="正在检查字段字典状态"
                      >
                        <i class="fa-solid fa-spinner fa-spin text-[10px]"></i>
                      </span>
                      <button
                        type="button"
                        :class="[
                          'flex h-5 shrink-0 items-center gap-1 rounded px-1.5 text-[11px] transition-all',
                          isColumnDictMaintained(ds.id, table.table_name, col.name)
                            ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                            : 'text-blue-600 opacity-0 hover:bg-blue-50 hover:text-blue-700 group-hover:opacity-100'
                        ]"
                        :title="isColumnDictMaintained(ds.id, table.table_name, col.name) ? '已维护字段字典，点击编辑' : '维护字段字典'"
                        @click.stop="openColumnDictModal(ds, table, col)"
                      >
                        <i class="fa-solid fa-book text-[10px]"></i>
                        <span>{{ isColumnDictMaintained(ds.id, table.table_name, col.name) ? '已维护' : '字典' }}</span>
                      </button>
                      <span class="min-w-[160px] flex-1"></span>
                    </div>
                      </div>
                    </div>
                    <!-- 索引分组 -->
                    <div class="ml-2 mt-1">
                      <div
                        class="flex cursor-pointer items-center gap-1.5 rounded px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
                        @click="toggleIndexGroup(ds.id, table.table_name)"
                      >
                        <i :class="['fa-solid text-[9px] text-slate-400', collapsedIndexGroups.has(buildTableKey(ds.id, table.table_name)) ? 'fa-chevron-right' : 'fa-chevron-down']"></i>
                        <i class="fa-solid fa-list-ol text-[10px] text-slate-400"></i>
                        <span>索引 ({{ getTableDisplayIndexes(table).length }})</span>
                        <button type="button" class="ml-auto text-blue-600 hover:text-blue-700" @click.stop="openIndexModal(ds, table)">
                          <i class="fa-solid fa-pen text-[10px] mr-0.5"></i>配置
                        </button>
                      </div>
                      <div v-show="!collapsedIndexGroups.has(buildTableKey(ds.id, table.table_name))" class="py-1">
                        <div v-if="hasTableIndexes(table)" class="space-y-0.5">
                          <div
                            v-for="idx in getTableDisplayIndexes(table)"
                            :key="[idx.source, idx.type, idx.name, ...idx.columns].join(':')"
                            class="group ml-4 flex items-center gap-2 rounded px-2 py-1 text-xs transition-colors hover:bg-slate-50"
                          >
                            <i
                              :class="['fa-solid text-[10px]', idx.type === 'primary' ? 'fa-key text-amber-600' : idx.type === 'unique' ? 'fa-fingerprint text-blue-600' : 'fa-bolt text-slate-500']"
                              :title="idx.type === 'primary' ? '主键' : idx.type === 'unique' ? '唯一索引' : '二级索引'"
                            ></i>
                            <span :class="['shrink-0 rounded px-1 py-0.5 text-[10px]', idx.source === 'manual' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500']">
                              {{ idx.source === 'manual' ? '手工' : '物理' }}
                            </span>
                            <span class="font-mono text-slate-900">{{ idx.name }}</span>
                            <span class="font-mono text-slate-500">({{ idx.columns.join(', ') }})</span>
                          </div>
                        </div>
                        <div v-else class="ml-4 py-1 px-2 text-xs text-slate-400">暂无索引</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="getFilteredDatasourceTables(ds).length === 0" class="ml-4 py-2 text-xs text-slate-500">未找到匹配的数据表</div>
            </div>
            <div v-else class="ml-4 py-2 text-xs text-slate-500">该数据源暂无表</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 数据源组弹窗 -->
    <el-dialog v-model="showGroupModal" width="min(920px, 92vw)" :close-on-click-modal="!creatingGroup && !syncingGroupMetadata" :show-close="false">
      <template #header>
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <i class="fa-solid fa-layer-group text-blue-600"></i>
            <h3 class="text-lg font-semibold text-slate-900 m-0">{{ isEditingGroup ? '修改数据源组' : '新建数据源组' }}</h3>
          </div>
          <button
            type="button"
            :disabled="creatingGroup || syncingGroupMetadata"
            class="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
            @click="closeGroupModal"
          >
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
      </template>
      <div class="max-h-[74vh] space-y-4 overflow-y-auto pr-1">
        <section class="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div class="mb-3 flex items-center justify-between gap-3">
            <div class="flex items-center gap-2">
              <i class="fa-solid fa-gear text-sm text-blue-600"></i>
              <span class="text-sm font-semibold text-slate-800">基础配置</span>
              <span v-if="isEditingGroup && hasUnsavedGroupConfig" class="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">有未保存变更</span>
            </div>
            <button :disabled="creatingGroup || !canSaveGroupConfig" class="flex items-center gap-2 rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" @click="handleCreateDatasourceGroup">
              <i v-if="creatingGroup" class="fa-solid fa-spinner fa-spin"></i>
              <i v-else class="fa-solid fa-floppy-disk"></i>
              <span>{{ creatingGroup ? (isEditingGroup ? '保存中...' : '创建中...') : (isEditingGroup ? '保存配置' : '创建数据源组') }}</span>
            </button>
          </div>
          <div class="space-y-2.5">
            <div class="grid grid-cols-2 gap-3">
              <div class="flex min-w-0 items-center gap-2">
                <label class="w-24 shrink-0 text-sm text-slate-600">组名称<span class="text-red-500">*</span></label>
                <input v-model="groupForm.group_name" type="text" maxlength="128" placeholder="请输入数据源组名称" class="min-w-0 flex-1 rounded border border-slate-300 px-2.5 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
              </div>
              <div class="flex min-w-0 items-center gap-2">
                <label class="w-24 shrink-0 text-sm text-slate-600">描述</label>
                <input v-model="groupForm.group_desc" type="text" maxlength="128" placeholder="可选" class="min-w-0 flex-1 rounded border border-slate-300 px-2.5 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
              </div>
            </div>
            <div class="flex min-w-0 items-start gap-2">
              <label class="w-24 shrink-0 pt-2 text-sm text-slate-600">智能问数说明</label>
              <textarea
                v-model="groupForm.query_context"
                maxlength="20000"
                rows="4"
                placeholder="可维护当前数据源组的查询规则、口径说明、正反例等，会注入智能问数上下文"
                class="min-w-0 flex-1 resize-y rounded border border-slate-300 px-2.5 py-2 text-sm leading-5 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              ></textarea>
            </div>
            <div class="flex min-w-0 items-start gap-2">
              <label class="w-24 shrink-0 pt-2 text-sm text-slate-600">推荐问题</label>
              <textarea
                v-model="groupForm.recommended_questions_text"
                rows="4"
                placeholder="每行一个推荐问题，单条不超过 50 个字"
                class="min-w-0 flex-1 resize-y rounded border border-slate-300 px-2.5 py-2 text-sm leading-5 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              ></textarea>
            </div>
            <div class="flex items-center gap-2">
              <label class="w-24 shrink-0 text-sm text-slate-600">数据源类型<span class="text-red-500">*</span></label>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  :class="['rounded border px-3 py-1.5 text-sm transition-colors', groupForm.source_type === 'mysql' ? 'border-blue-500 bg-blue-50 text-blue-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50']"
                  @click="handleGroupSourceTypeChange('mysql')"
                >MySQL</button>
                <button
                  type="button"
                  :class="['rounded border px-3 py-1.5 text-sm transition-colors', groupForm.source_type === 'hive' ? 'border-amber-500 bg-amber-50 text-amber-600' : 'border-slate-300 text-slate-600 hover:bg-slate-50']"
                  @click="handleGroupSourceTypeChange('hive')"
                >Hive</button>
              </div>
            </div>
            <div>
              <div class="mb-2 flex items-center gap-2">
                <i class="fa-solid fa-link text-sm text-blue-600"></i>
                <span class="text-sm font-medium text-slate-700">组内数据源</span>
                <span class="rounded-full bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700">{{ memberSourceLineCount }}</span>
              </div>
              <div class="flex h-44 overflow-hidden rounded border border-slate-300 focus-within:border-blue-500 focus-within:ring-1 focus-within:ring-blue-500">
                <div class="overflow-hidden select-none border-r border-slate-200 bg-slate-50 px-2 py-2 text-right text-xs leading-6 text-slate-400">
                  <div :style="{ transform: `translateY(-${groupSourceScrollTop}px)` }">
                    <div v-for="lineNumber in groupSourceLineNumbers" :key="lineNumber" class="h-6 min-w-6">
                      {{ lineNumber }}
                    </div>
                  </div>
                </div>
                <textarea
                  ref="groupSourceTextareaRef"
                  v-model="groupForm.datasource_sources_text"
                  :placeholder="groupSourcePlaceholder"
                  wrap="off"
                  spellcheck="false"
                  class="h-full w-full overflow-x-auto overflow-y-auto whitespace-pre border-0 px-3 py-2 text-sm leading-6 font-mono resize-none focus:outline-none focus:ring-0"
                  @scroll="handleGroupSourceScroll"
                ></textarea>
              </div>
            </div>
          </div>
        </section>

        <section class="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div class="mb-3 flex items-center justify-between gap-3">
            <div class="flex min-w-0 items-center gap-2">
              <i class="fa-solid fa-table-list text-sm text-blue-600"></i>
              <span class="text-sm font-semibold text-slate-800">元数据同步</span>
            </div>
            <div class="flex items-center gap-2">
              <button :disabled="!isEditingGroup || hasUnsavedGroupConfig || loadingGroupTables || syncingGroupMetadata" class="flex items-center gap-2 rounded bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50" @click="fetchGroupDbTables">
                <i :class="['fa-solid', loadingGroupTables ? 'fa-spinner fa-spin' : 'fa-arrows-rotate']"></i>
                <span>{{ loadingGroupTables ? '拉取中...' : '拉取表信息' }}</span>
              </button>
              <button v-if="isEditingGroup" :disabled="loadingGroupTables || syncingGroupMetadata || !canSyncGroupMetadata" class="flex items-center gap-2 rounded bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50" @click="handleSyncGroupMetadata">
                <i :class="['fa-solid', syncingGroupMetadata ? 'fa-spinner fa-spin' : 'fa-cloud-arrow-down']"></i>
                <span>{{ syncingGroupMetadata ? '同步中...' : '同步元数据' }}</span>
              </button>
            </div>
          </div>

          <div v-if="!isEditingGroup" class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
            请先创建数据源组，创建成功后再拉取表信息并同步元数据。
          </div>
          <div v-else-if="hasUnsavedGroupConfig" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-700">
            配置已变更，请先在基础配置区域点击“保存配置”，再拉取表信息或同步元数据。
          </div>

          <div v-if="hasGroupTemplateSourceChanged && groupDbTables.length > 0" class="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-700">
            第一行模板数据源已变化，当前仍使用上次拉取的表信息；如需同步新模板源元数据，请先保存配置后重新拉取表信息。
          </div>

          <div v-if="groupDbTables.length > 0" class="mt-3 rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div class="mb-2 flex items-center gap-2">
              <span class="text-sm font-medium text-slate-700">选择表</span>
              <span class="rounded-full bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700">{{ groupSelectionSummaryText }}</span>
              <div class="relative flex-1">
                <i class="fa-solid fa-search absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-slate-400"></i>
                <input v-model="groupTableSearchKeyword" type="text" placeholder="搜索表名..." class="w-full rounded border border-slate-200 bg-white py-1 pl-6 pr-2 text-xs focus:border-blue-500 focus:outline-none" />
              </div>
              <button class="rounded px-1.5 py-0.5 text-xs text-slate-500 hover:bg-white hover:text-blue-600" @click="selectAllGroupTables">{{ groupSelectAllButtonText }}</button>
              <button class="rounded px-1.5 py-0.5 text-xs text-slate-500 hover:bg-white hover:text-blue-600" @click="deselectAllGroupTables">{{ groupDeselectAllButtonText }}</button>
            </div>
            <div class="max-h-44 overflow-y-auto rounded border border-slate-100 bg-white">
              <label v-for="table in filteredGroupTables" :key="table" class="flex cursor-pointer items-center gap-2 px-3 py-1 transition-colors hover:bg-slate-50">
                <input type="checkbox" :checked="selectedGroupTables.has(table)" class="h-3.5 w-3.5 shrink-0 rounded border-slate-300 text-blue-600 focus:ring-blue-500" @change="toggleGroupTableSelection(table)" />
                <span class="truncate text-xs text-slate-600">{{ table }}</span>
              </label>
            </div>
          </div>
        </section>
      </div>

    </el-dialog>

    <TableIndexDialog
      :visible="showIndexModal"
      :datasource="indexContext?.datasource"
      :table="indexContext?.table"
      @close="closeIndexModal"
      @saved="handleIndexesSaved"
    />

    <!-- 表分组管理弹窗 -->
    <el-dialog v-model="showTableGroupModal" width="760px" :close-on-click-modal="!savingTableGroups" :show-close="false">
      <template #header>
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-layer-group text-blue-600"></i>
          <h3 class="text-lg font-semibold text-slate-900 m-0">表分组管理<span v-if="tableGroupDs"> - {{ tableGroupDs.group_name }}</span></h3>
        </div>
      </template>

      <div class="flex max-h-[70vh] min-h-0 flex-col">
        <div v-if="loadingTableGroups" class="flex min-h-[320px] flex-col items-center justify-center text-slate-400">
          <i class="fa-solid fa-spinner fa-spin mb-2 text-2xl"></i>
          <span class="text-sm">正在加载表分组...</span>
        </div>
        <div v-else class="grid min-h-0 flex-1 grid-cols-[240px_1fr] gap-4">
          <div class="flex min-h-0 min-w-0 flex-col rounded-xl border border-slate-200 bg-slate-50">
          <div class="border-b border-slate-200 p-3">
            <div class="flex gap-2">
              <input
                v-model="tableGroupNameInput"
                type="text"
                maxlength="100"
                placeholder="新增分组名称"
                class="min-w-0 flex-1 rounded border border-slate-200 bg-white px-2 py-1.5 text-xs focus:border-blue-500 focus:outline-none"
                @keydown.enter.prevent="addTableGroup"
              />
              <button class="rounded bg-blue-600 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-blue-700" @click="addTableGroup">新增</button>
            </div>
          </div>
          <div class="flex-1 overflow-y-auto p-2">
            <div v-if="tableGroupRows.length === 0" class="p-6 text-center text-xs text-slate-400">暂无分组</div>
            <button
              v-for="group in tableGroupRows"
              :key="group.table_group_name"
              :class="['group flex w-full items-center gap-2 rounded px-2 py-2 text-left transition-colors',
                activeTableGroupName === group.table_group_name ? 'bg-white text-blue-700 shadow-sm' : 'text-slate-700 hover:bg-white']"
              @click="activeTableGroupName = group.table_group_name"
            >
              <template v-if="editingTableGroupName === group.table_group_name">
                <input
                  v-model="editingTableGroupInput"
                  type="text"
                  maxlength="100"
                  class="min-w-0 flex-1 rounded border border-blue-300 bg-white px-2 py-1 text-xs text-slate-800 outline-none"
                  @click.stop
                  @keydown.enter.prevent.stop="confirmRenameTableGroup(group.table_group_name)"
                  @keydown.esc.prevent.stop="cancelRenameTableGroup"
                />
                <i class="fa-solid fa-check text-xs text-blue-600 hover:text-blue-700" @click.stop="confirmRenameTableGroup(group.table_group_name)"></i>
                <i class="fa-solid fa-xmark text-xs text-slate-400 hover:text-slate-600" @click.stop="cancelRenameTableGroup"></i>
              </template>
              <template v-else>
                <i class="fa-solid fa-folder text-xs text-amber-600"></i>
                <span class="min-w-0 flex-1 truncate text-sm font-medium">{{ group.table_group_name }}</span>
                <span class="text-xs text-slate-400">{{ group.table_names.length }}</span>
                <i class="fa-solid fa-pen text-xs text-slate-300 opacity-0 hover:text-blue-600 group-hover:opacity-100" title="重命名" @click.stop="startRenameTableGroup(group.table_group_name)"></i>
                <i class="fa-solid fa-trash text-xs text-slate-300 opacity-0 hover:text-red-600 group-hover:opacity-100" title="删除" @click.stop="deleteTableGroup(group.table_group_name)"></i>
              </template>
            </button>
          </div>
        </div>

          <div class="flex min-h-0 min-w-0 flex-col rounded-xl border border-slate-200 bg-white">
          <div class="flex items-center gap-2 border-b border-slate-200 p-3">
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-semibold text-slate-800">{{ activeTableGroupName || '请选择分组' }}</div>
              <div class="mt-0.5 text-xs text-slate-400">同一张表可以加入多个分组</div>
            </div>
            <div class="relative w-56">
              <i class="fa-solid fa-search absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-slate-400"></i>
              <input v-model="tableGroupSearchKeyword" type="text" placeholder="搜索表名/描述" class="w-full rounded border border-slate-200 bg-slate-50 py-1.5 pl-6 pr-2 text-xs focus:border-blue-500 focus:bg-white focus:outline-none" />
            </div>
          </div>
          <div class="flex-1 overflow-y-auto p-2">
            <div v-if="!activeTableGroup" class="flex h-full items-center justify-center text-sm text-slate-400">先在左侧新增或选择一个分组</div>
            <div v-else-if="filteredTableGroupTables.length === 0" class="p-8 text-center text-sm text-slate-400">未找到表</div>
            <template v-else>
              <label
                v-for="table in filteredTableGroupTables"
                :key="table.table_name"
                class="flex cursor-pointer items-center gap-3 rounded px-3 py-2 transition-colors hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  :checked="activeTableGroupTableNames.has(table.table_name)"
                  class="h-4 w-4 shrink-0 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  @change="toggleActiveTableGroupTable(table.table_name)"
                />
                <i class="fa-solid fa-table text-xs text-blue-600"></i>
                <span class="min-w-0 flex-1 truncate text-sm font-medium text-slate-800">{{ table.table_name }}</span>
                <span v-if="table.description && table.description !== table.table_name" class="max-w-[180px] truncate text-xs text-slate-400">{{ table.description }}</span>
              </label>
            </template>
          </div>
        </div>
        </div>

        <div class="mt-4 flex shrink-0 justify-between">
          <button :disabled="savingTableGroups" class="rounded px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50" @click="closeTableGroupModal">取消</button>
          <button :disabled="savingTableGroups" class="flex items-center gap-2 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" @click="saveTableGroups">
            <i v-if="savingTableGroups" class="fa-solid fa-spinner fa-spin"></i>
            <i v-else class="fa-solid fa-floppy-disk"></i>
            <span>{{ savingTableGroups ? '保存中...' : '保存表分组' }}</span>
          </button>
        </div>
      </div>
    </el-dialog>

    <!-- 字段字典维护弹窗（统一由 ColumnDictEditDialog 承载） -->
    <ColumnDictEditDialog
      v-if="columnDictContext"
      v-model:visible="showColumnDictModal"
      :datasource-id="columnDictContext.datasource.id"
      :table-name="columnDictContext.table.table_name"
      :column-name="columnDictContext.column.name"
      admin-mode
      @saved="handleColumnDictSaved"
    />

  </div>
</template>
