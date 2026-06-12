<script setup lang="ts">
/**
 * 数据源管理组件
 * 支持数据源列表展示、新建数据源组、刷新、删除、设为默认
 */
import { ref, computed, onMounted, h } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getDatasources,
  deleteDatasource,
  getDbTables,
  setDefaultDatasource,
  unsetDefaultDatasource,
  updateDatasource,
  updateMappedModules,
  createDatasourceGroup,
  updateDatasourceGroup,
} from '../api'
import request from '@/utils/request'
import type { DataSource } from '../types'

const emit = defineEmits<{
  (e: 'datasources-change', datasources: DataSource[]): void
  (e: 'open-semantic-config', datasourceId: string): void
}>()

// 数据源列表
const datasources = ref<DataSource[]>([])
const datasourcesLoading = ref(false)
const expandedDatasources = ref<Set<string>>(new Set())
const expandedTables = ref<Set<string>>(new Set())

// 数据源组
const showGroupModal = ref(false)
const creatingGroup = ref(false)
const editingGroupId = ref<string | null>(null)
const isEditingGroup = computed(() => !!editingGroupId.value)
const loadingGroupTables = ref(false)
const groupDbTables = ref<string[]>([])
const selectedGroupTables = ref<Set<string>>(new Set())
const groupTableSearchKeyword = ref('')
const groupTableSnapshotKey = ref('')
const groupSourceTextareaRef = ref<HTMLTextAreaElement | null>(null)
const groupSourceScrollTop = ref(0)
const groupForm = ref({
  group_name: '',
  group_desc: '',
  source_type: 'mysql' as 'mysql' | 'hive',
  datasource_sources_text: '',
})

// 表选择
const showTableSelector = ref(false)
const dbTables = ref<string[]>([])
const selectedTables = ref<Set<string>>(new Set())
const loadingTables = ref(false)
const tableSearchKeyword = ref('')

// 刷新
const refreshingDatasourceId = ref<string | null>(null)
const showRefreshModal = ref(false)
const refreshingDs = ref<{ id: string; name: string; source_type: string; source_config: Record<string, unknown> } | null>(null)

// 设置默认
const settingDefaultId = ref<string | null>(null)
// 重命名
const renamingDatasourceId = ref<string | null>(null)

// 模块映射编辑
const mappingDsId = ref<string | null>(null)
const mappingModules = ref<string[]>([])
const mappingInput = ref('')
const savingMapping = ref(false)

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
    const res = await updateMappedModules(mappingDsId.value, mappingModules.value)
    if (res.status === 'success') {
      ElMessage.success('映射模块已保存')
      closeMappingPopover()
      fetchDatasources()
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch {
    ElMessage.error('保存失败')
  } finally {
    savingMapping.value = false
  }
}

// 过滤后的表
const filteredTables = computed(() =>
  dbTables.value.filter(t => t.toLowerCase().includes(tableSearchKeyword.value.toLowerCase()))
)
const filteredSelectedCount = computed(() =>
  filteredTables.value.filter(table => selectedTables.value.has(table)).length
)
const hasTableSearchKeyword = computed(() => tableSearchKeyword.value.trim().length > 0)
const selectionSummaryText = computed(() =>
  hasTableSearchKeyword.value
    ? `${filteredSelectedCount.value}/${filteredTables.value.length}（筛选结果）`
    : `${selectedTables.value.size}/${dbTables.value.length}`
)
const selectAllButtonText = computed(() => hasTableSearchKeyword.value ? '全选筛选结果' : '全选')
const deselectAllButtonText = computed(() => hasTableSearchKeyword.value ? '清空筛选结果' : '清空')
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
    return '每行一个数据源，格式：名称|direct/http|ip:port:db_name\n名称在 HTTP 模式下将作为 namespace\n第一行默认作为模板数据源，用于拉取表结构\n例如：\nhive-localhost|http|localhost:10000:default\nhive-direct|direct|10.0.0.10:10000:dw_prod'
  }
  return '每行一个数据源，格式：名称|direct/http|ip:port:db_name\n名称在 HTTP 模式下将作为执行标识\n第一行默认作为模板数据源，用于拉取表结构\n例如：\n杭州库|direct|db-host-1:3306:database_a\n宁波库|http|db-host-2:3306:database_b'
})

const parseTemplateSourceForSnapshot = () => {
  const firstLine = groupForm.value.datasource_sources_text
    .split('\n')
    .map(line => line.trim())
    .find(Boolean)
  if (!firstLine) return ''

  const segments = firstLine.split('|').map(item => item.trim())
  if (segments.length !== 3) {
    return ''
  }
  const sourceText = segments[2] || ''

  const parts = sourceText.split(':')
  if (parts.length !== 3) return ''
  const host = parts[0]?.trim() || ''
  const port = parts[1]?.trim() || ''
  const database = parts[2]?.trim() || ''
  if (!host || !port || !database) return ''
  return `${groupForm.value.source_type}|${host}:${port}:${database}`
}

const buildGroupSourceSnapshotKey = () => parseTemplateSourceForSnapshot()

const hasGroupTemplateSourceChanged = computed(() => {
  if (!showGroupModal.value || !groupTableSnapshotKey.value) return false
  const currentTemplateSnapshotKey = buildGroupSourceSnapshotKey()
  if (!currentTemplateSnapshotKey) return false
  return currentTemplateSnapshotKey !== groupTableSnapshotKey.value
})

const resetGroupTableSelector = () => {
  groupDbTables.value = []
  selectedGroupTables.value = new Set()
  groupTableSearchKeyword.value = ''
  groupTableSnapshotKey.value = ''
}

// 加载数据源
const fetchDatasources = async () => {
  datasourcesLoading.value = true
  try {
    const res = await getDatasources()
    if (res.status === 'success') {
      datasources.value = (res.data.datasources || []).filter(
        (ds: DataSource) => ds.ds_name && ds.ds_name.trim() !== ''
      )
      emit('datasources-change', datasources.value)
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

const toggleTable = (tableKey: string) => {
  const s = new Set(expandedTables.value)
  s.has(tableKey) ? s.delete(tableKey) : s.add(tableKey)
  expandedTables.value = s
}

const handleOpenSemanticConfig = (datasourceId: string) => {
  emit('open-semantic-config', datasourceId)
}

// 删除数据源
const handleDelete = async (ds: DataSource) => {
  try {
    await ElMessageBox.confirm(`确定要删除数据源"${ds.ds_name}"吗？`, '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    const res = await deleteDatasource(ds.id)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      fetchDatasources()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch {
    ElMessage.error('删除失败')
  }
}

// 设置/取消默认
const handleToggleDefault = async (ds: DataSource) => {
  settingDefaultId.value = ds.id
  try {
    const isDefault = ds.is_default
    const res = isDefault
      ? await unsetDefaultDatasource(ds.id)
      : await setDefaultDatasource(ds.id)
    if (res.status === 'success') {
      ElMessage.success(isDefault ? '已取消默认' : '已设为默认')
      fetchDatasources()
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

// 打开刷新弹窗
const openRefreshModal = async (ds: DataSource) => {
  if (refreshingDatasourceId.value) return

  if (!ds.source_type) {
    ElMessage.error('数据源类型缺失，无法刷新表结构')
    return
  }

  refreshingDs.value = {
    id: ds.id,
    name: ds.ds_name,
    source_type: ds.source_type,
    source_config: ds.source_config || {},
  }
  showRefreshModal.value = true
  loadingTables.value = true
  dbTables.value = []
  selectedTables.value = new Set()
  tableSearchKeyword.value = ''

  try {
    const config = ds.source_config || {}
    const res = await getDbTables({
      source_type: ds.source_type,
      host: config.host as string,
      port: config.port as number,
      database: config.database as string,
    })
    if (res.status === 'success') {
      const tables = res.data.tables || []
      dbTables.value = tables
      const existingTables = (config.table_names as string[]) || ds.ds_tables?.map(t => t.table_name) || []
      const initial = new Set(tables.filter((t: string) => existingTables.includes(t)))
      selectedTables.value = initial.size > 0 ? initial : new Set(tables)
    } else {
      ElMessage.error(res.msg || '获取表列表失败')
      showRefreshModal.value = false
    }
  } catch {
    ElMessage.error('连接失败')
    showRefreshModal.value = false
  } finally {
    loadingTables.value = false
  }
}

// 执行刷新
const executeRefresh = async (dsId: string, dsName: string, tableNames?: string[]) => {
  refreshingDatasourceId.value = dsId
  try {
    const res = await request({
      url: `/agents/api/chatdata/datasources/${dsId}/refresh`,
      method: 'post',
      data: { table_names: tableNames }
    }) as any
    if (res.status === 'success') {
      ElMessage.success(`刷新成功，共 ${res.data?.table_count || 0} 个表`)
      ElMessage.warning('数据源变更请同步检查并更新语义配置与收藏查询')
      showRefreshModal.value = false
      refreshingDs.value = null
      fetchDatasources()
    } else {
      ElMessage.error(res.msg || '刷新失败')
    }
  } catch {
    ElMessage.error('刷新失败')
  } finally {
    refreshingDatasourceId.value = null
  }
}

const handleConfirmRefresh = async () => {
  if (!refreshingDs.value) return
  const tableNames = selectedTables.value.size > 0 ? Array.from(selectedTables.value) : undefined
  await executeRefresh(refreshingDs.value.id, refreshingDs.value.name, tableNames)
}

const resetTableSelector = () => {
  showTableSelector.value = false
  dbTables.value = []
  selectedTables.value = new Set()
  tableSearchKeyword.value = ''
}

const toggleTableSelection = (tableName: string) => {
  const s = new Set(selectedTables.value)
  s.has(tableName) ? s.delete(tableName) : s.add(tableName)
  selectedTables.value = s
}

const selectAllTables = () => {
  selectedTables.value = new Set([...selectedTables.value, ...filteredTables.value])
}
const deselectAllTables = () => {
  const nextSelected = new Set(selectedTables.value)
  filteredTables.value.forEach(table => nextSelected.delete(table))
  selectedTables.value = nextSelected
}

const openGroupModal = () => {
  editingGroupId.value = null
  groupForm.value = {
    group_name: '',
    group_desc: '',
    source_type: 'mysql',
    datasource_sources_text: '',
  }
  resetGroupTableSelector()
  showGroupModal.value = true
}

const openEditGroupModal = (ds: DataSource) => {
  editingGroupId.value = ds.id
  const sourceType = ((ds.source_type || 'mysql') as 'mysql' | 'hive')
  groupForm.value = {
    group_name: ds.ds_name || '',
    group_desc: ds.ds_description || '',
    source_type: sourceType,
    datasource_sources_text: String((ds as DataSource & { member_sources_text?: string }).member_sources_text || '').trim() || (ds.member_sources || [])
      .map((item) => {
        const executorType = item.query_executor_type || 'direct'
        return `${item.datasource_name || ''}|${executorType}|${item.host}:${item.port}:${item.database}`
      })
      .join('\n'),
  }
  groupDbTables.value = (ds.ds_tables || []).map(table => table.table_name).filter(Boolean)
  selectedGroupTables.value = new Set(groupDbTables.value)
  groupTableSearchKeyword.value = ''
  groupTableSnapshotKey.value = buildGroupSourceSnapshotKey()
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

  const members: Array<{ datasource_name?: string; query_executor_type?: 'direct' | 'http'; host: string; port: number; database: string }> = []
  const nameSet = new Set<string>()
  const identitySet = new Set<string>()
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const segments = line.split('|').map(item => item.trim())
    let datasourceName = ''
    let queryExecutorType: 'direct' | 'http' = 'direct'
    let sourceText = ''

    if (groupForm.value.source_type === 'hive' || groupForm.value.source_type === 'mysql') {
      if (segments.length !== 3) {
        throw new Error(`第 ${index + 1} 行格式错误，请使用固定格式：名称|direct/http|ip:port:db_name`)
      }
      datasourceName = segments[0] || ''
      queryExecutorType = segments[1] === 'http' ? 'http' : 'direct'
      sourceText = segments[2] || ''
      if (!datasourceName) {
        throw new Error(`第 ${index + 1} 行缺少名称`)
      }
      if (!['direct', 'http'].includes(segments[1])) {
        throw new Error(`第 ${index + 1} 行查询方式错误，仅支持 direct 或 http`)
      }
    }

    const parts = sourceText.split(':')
    if (parts.length !== 3) {
      throw new Error(`第 ${index + 1} 行格式错误，请检查连接串`)
    }
    const host = parts[0]?.trim() || ''
    const port = Number(parts[1]?.trim() || '')
    const database = parts[2]?.trim() || ''
    if (!host || !port || !database) {
      throw new Error(`第 ${index + 1} 行格式错误，请检查连接串`)
    }
    const sourceKey = `${host}:${port}:${database}`
    if (datasourceName) {
      if (nameSet.has(datasourceName)) {
        throw new Error(`第 ${index + 1} 行名称重复：${datasourceName}`)
      }
      nameSet.add(datasourceName)
    }
    const identityKey = queryExecutorType === 'http'
      ? `http:${datasourceName}`
      : `${queryExecutorType}:${sourceKey}`
    if (identitySet.has(identityKey)) {
      if (queryExecutorType === 'http') {
        throw new Error(`第 ${index + 1} 行名称重复：${datasourceName}`)
      }
      throw new Error(`第 ${index + 1} 行连接重复：${sourceKey}`)
    }
    identitySet.add(identityKey)
    members.push({
      datasource_name: datasourceName || undefined,
      query_executor_type: queryExecutorType,
      host,
      port,
      database,
    })
  }
  return members
}

const fetchGroupDbTables = async () => {
  let datasourceSources: Array<{ datasource_name?: string; query_executor_type?: 'direct' | 'http'; host: string; port: number; database: string }> = []
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
  loadingGroupTables.value = true
  resetGroupTableSelector()
  try {
    const res = await getDbTables({
      source_type: groupForm.value.source_type,
      host: templateSource.host,
      port: templateSource.port,
      database: templateSource.database,
    })
    if (res.status === 'success') {
      const tables = res.data.tables || []
      groupDbTables.value = tables
      selectedGroupTables.value = new Set(tables)
      groupTableSnapshotKey.value = buildGroupSourceSnapshotKey()
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

const handleCreateDatasourceGroup = async () => {
  const groupName = groupForm.value.group_name.trim()
  if (!groupName) {
    ElMessage.error('请输入数据源组名称')
    return
  }

  let datasourceSources: Array<{ datasource_name?: string; query_executor_type?: 'direct' | 'http'; host: string; port: number; database: string }> = []
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

  const currentTemplateSnapshotKey = buildGroupSourceSnapshotKey()
  if (!currentTemplateSnapshotKey) {
    ElMessage.error('模板数据源格式错误，请先按固定格式修正第一行')
    return
  }
  if (groupDbTables.value.length === 0) {
    ElMessage.error('请先拉取模板表结构')
    return
  }
  if (groupDbTables.value.length > 0 && groupTableSnapshotKey.value && groupTableSnapshotKey.value !== currentTemplateSnapshotKey) {
    ElMessage.warning('模板数据源已变化，当前仍使用上次拉取的表结构；如需更新可手动拉取表信息')
  }

  const [templateSource, ...memberSources] = datasourceSources

  creatingGroup.value = true
  try {
    const payload = {
      group_name: groupName,
      group_desc: groupForm.value.group_desc.trim(),
      source_type: groupForm.value.source_type,
      template_source: templateSource,
      template_table_names: groupDbTables.value.length > 0 ? Array.from(selectedGroupTables.value) : undefined,
      member_sources: memberSources,
    }
    const res = editingGroupId.value
      ? await updateDatasourceGroup(editingGroupId.value, payload)
      : await createDatasourceGroup(payload)
    if (res.status === 'success') {
      ElMessage.success(editingGroupId.value ? '数据源组更新成功' : '数据源组创建成功')
      showGroupModal.value = false
      editingGroupId.value = null
      await fetchDatasources()
    } else {
      ElMessage.error(res.msg || (editingGroupId.value ? '数据源组更新失败' : '数据源组创建失败'))
    }
  } catch {
    ElMessage.error(editingGroupId.value ? '数据源组更新失败' : '数据源组创建失败')
  } finally {
    creatingGroup.value = false
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
              <span class="flex-1 text-left text-sm font-semibold text-slate-900 truncate" :title="getDatasourceConnectionTitle(ds)">{{ ds.ds_name }}</span>
                <span class="text-xs text-slate-500">{{ ds.ds_tables?.length || 0 }}</span>
            </button>

            <!-- 操作按钮 -->
            <div :class="['flex items-center gap-1 transition-opacity', ds.is_default ? 'opacity-100' : 'opacity-0 group-hover:opacity-100']">
              <button
                :disabled="refreshingDatasourceId !== null || settingDefaultId !== null || renamingDatasourceId !== null"
                :class="['flex h-6 w-6 items-center justify-center rounded transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                  ds.is_default ? 'text-yellow-500 hover:bg-yellow-50' : 'text-slate-400 hover:bg-slate-200 hover:text-yellow-500']"
                :title="ds.is_default ? '取消默认' : '设为默认'"
                @click.stop="handleToggleDefault(ds)"
              >
                <i :class="['text-xs', ds.is_default ? 'fa-solid fa-star' : 'fa-regular fa-star', settingDefaultId === ds.id ? 'fa-spin' : '']"></i>
              </button>
              <!-- 重命名 -->
              <button
                :disabled="refreshingDatasourceId !== null || renamingDatasourceId !== null"
                :class="['flex h-6 w-6 items-center justify-center rounded transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                  renamingDatasourceId === ds.id ? 'text-blue-600' : 'text-slate-600 hover:bg-slate-200']"
                title="修改数据源组"
                @click.stop="handleEditDatasource(ds)"
              >
                <i :class="['fa-solid fa-pen text-xs', renamingDatasourceId === ds.id ? 'fa-spin' : '']"></i>
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
                      display: 'flex', width: '24px', height: '24px', alignItems: 'center', justifyContent: 'center',
                      borderRadius: '4px', transition: 'all 0.2s',
                      color: (ds.mapped_modules && ds.mapped_modules.length > 0) ? '#2563eb' : '#94a3b8',
                    }"
                    title="模块映射"
                    @click.stop="mappingDsId === ds.id ? closeMappingPopover() : openMappingPopover(ds)"
                  >
                    <i class="fa-solid fa-tags" style="font-size: 12px;"></i>
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
              <!-- 刷新 -->
              <button
                :disabled="refreshingDatasourceId !== null"
                :class="['flex h-6 w-6 items-center justify-center rounded transition-colors',
                  refreshingDatasourceId === ds.id ? 'text-blue-600' : refreshingDatasourceId !== null ? 'cursor-not-allowed text-slate-300' : 'text-slate-600 hover:bg-slate-200']"
                title="刷新此数据源"
                @click.stop="openRefreshModal(ds)"
              >
                <i :class="['fa-solid fa-rotate text-xs', refreshingDatasourceId === ds.id ? 'fa-spin' : '']"></i>
              </button>
              <button
                class="flex h-6 w-6 items-center justify-center rounded text-slate-600 transition-colors hover:bg-slate-200"
                title="数据集管理"
                @click.stop="handleOpenSemanticConfig(ds.id)"
              >
                <i class="fa-solid fa-sitemap text-xs"></i>
              </button>
              <!-- 删除 -->
              <button
                v-if="ds.id !== 'default'"
                :disabled="refreshingDatasourceId !== null"
                class="flex h-6 w-6 items-center justify-center rounded text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                title="删除数据源"
                @click.stop="handleDelete(ds)"
              >
                <i class="fa-solid fa-trash text-xs"></i>
              </button>
            </div>
          </div>

          <!-- 表列表 -->
          <div v-if="expandedDatasources.has(ds.id)" class="ml-4 overflow-hidden">
            <div v-if="ds.ds_tables && ds.ds_tables.length > 0" class="border-l border-slate-200 py-1 overflow-x-auto">
              <div v-for="table in ds.ds_tables" :key="`${ds.id}-${table.table_name}`" class="ml-2">
                <!-- 表节点 -->
                <button
                  class="flex min-w-max items-center gap-2 rounded px-2 py-1 text-left text-sm transition-colors hover:bg-slate-100 whitespace-nowrap"
                  @click="toggleTable(`${ds.id}-${table.table_name}`)"
                >
                  <i :class="['fa-solid text-xs text-slate-500', expandedTables.has(`${ds.id}-${table.table_name}`) ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
                  <i class="fa-solid fa-table text-xs text-blue-600"></i>
                  <span class="shrink-0 font-medium text-slate-900">{{ table.table_name }}</span>
                  <span v-if="table.description && table.description !== table.table_name" class="shrink-0 text-xs text-slate-400">{{ table.description }}</span>
                  <span class="ml-auto text-xs text-slate-500">{{ table.columns?.length || 0 }}</span>
                </button>

                <!-- 列列表 -->
                <div v-if="expandedTables.has(`${ds.id}-${table.table_name}`)" class="ml-6 overflow-hidden">
                  <div class="border-l border-slate-200 py-1">
                    <div
                      v-for="col in table.columns"
                      :key="col.name"
                      class="ml-4 flex items-center gap-2 rounded px-2 py-1 text-xs transition-colors hover:bg-slate-50"
                    >
                      <i :class="['fa-solid text-[10px]', getColumnIcon(col), getColumnIconColor(col)]" :title="getColumnIconTitle(col)"></i>
                      <span class="font-mono font-medium text-slate-900">{{ col.name }}</span>
                      <span class="text-slate-500">{{ col.mysql_type || col.hive_type || col.type }}</span>
                      <span v-if="col.description" class="flex-1 truncate text-slate-400">- {{ col.description }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="ml-4 py-2 text-xs text-slate-500">该数据源暂无表</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 数据源组弹窗 -->
    <el-dialog v-model="showGroupModal" width="620px" :close-on-click-modal="!creatingGroup" :show-close="false">
      <template #header>
        <div class="flex items-center gap-2">
          <i class="fa-solid fa-layer-group text-blue-600"></i>
          <h3 class="text-lg font-semibold text-slate-900 m-0">{{ isEditingGroup ? '修改数据源组' : '新建数据源组' }}</h3>
        </div>
      </template>
      <div class="space-y-4">
        <div class="space-y-2.5">
          <div class="grid grid-cols-2 gap-3">
            <div class="flex min-w-0 items-center gap-2">
              <label class="w-16 shrink-0 text-sm text-slate-600">组名称<span class="text-red-500">*</span></label>
              <input v-model="groupForm.group_name" type="text" maxlength="128" placeholder="请输入数据源组名称" class="min-w-0 flex-1 rounded border border-slate-300 px-2.5 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
            </div>
            <div class="flex min-w-0 items-center gap-2">
              <label class="w-12 shrink-0 text-sm text-slate-600">描述</label>
              <input v-model="groupForm.group_desc" type="text" maxlength="128" placeholder="可选" class="min-w-0 flex-1 rounded border border-slate-300 px-2.5 py-1.5 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500" />
            </div>
          </div>
          <div class="flex items-center gap-2">
            <label class="w-20 shrink-0 text-sm text-slate-600">数据源类型<span class="text-red-500">*</span></label>
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
        </div>

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
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

        <div class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
          <div class="mb-2 flex items-center justify-between gap-2">
            <div class="flex items-center gap-2">
              <i class="fa-solid fa-table-list text-sm text-blue-600"></i>
              <span class="text-sm font-medium text-slate-700">模板表结构</span>
              <span v-if="groupDbTables.length > 0" class="rounded-full bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700">{{ groupSelectionSummaryText }}</span>
            </div>
            <button :disabled="loadingGroupTables" class="flex items-center gap-2 rounded bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50" @click="fetchGroupDbTables">
              <i :class="['fa-solid', loadingGroupTables ? 'fa-spinner fa-spin' : 'fa-arrows-rotate']"></i>
              <span>{{ loadingGroupTables ? '拉取中...' : '拉取表信息' }}</span>
            </button>
          </div>

          <div v-if="hasGroupTemplateSourceChanged && groupDbTables.length > 0" class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-700">
            第一行模板数据源已变化，当前仍使用上次拉取的表结构；如需更新，请手动点击“拉取表信息”。
          </div>

          <div v-if="groupDbTables.length > 0" class="mt-3 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
            <div class="mb-2 flex items-center gap-2">
              <span class="text-sm font-medium text-slate-700">选择表</span>
              <span class="rounded-full bg-blue-100 px-1.5 py-0.5 text-xs font-medium text-blue-700">{{ groupSelectionSummaryText }}</span>
              <div class="relative flex-1">
                <i class="fa-solid fa-search absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-slate-400"></i>
                <input v-model="groupTableSearchKeyword" type="text" placeholder="搜索表名..." class="w-full rounded border border-slate-200 bg-slate-50 py-1 pl-6 pr-2 text-xs focus:border-blue-500 focus:bg-white focus:outline-none" />
              </div>
              <button class="rounded px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100 hover:text-blue-600" @click="selectAllGroupTables">{{ groupSelectAllButtonText }}</button>
              <button class="rounded px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100 hover:text-blue-600" @click="deselectAllGroupTables">{{ groupDeselectAllButtonText }}</button>
            </div>
            <div class="max-h-44 overflow-y-auto rounded-lg border border-slate-100 bg-slate-50">
              <label v-for="table in filteredGroupTables" :key="table" class="flex cursor-pointer items-center gap-2 px-3 py-1 transition-colors hover:bg-white">
                <input type="checkbox" :checked="selectedGroupTables.has(table)" class="h-3.5 w-3.5 shrink-0 rounded border-slate-300 text-blue-600 focus:ring-blue-500" @change="toggleGroupTableSelection(table)" />
                <span class="truncate text-xs text-slate-600">{{ table }}</span>
              </label>
            </div>
          </div>
        </div>

        <div class="flex justify-between">
          <button :disabled="creatingGroup" class="rounded px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50" @click="showGroupModal = false">
            取消
          </button>
          <button :disabled="creatingGroup || (groupDbTables.length > 0 && selectedGroupTables.size === 0)" class="flex items-center gap-2 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" @click="handleCreateDatasourceGroup">
            <i v-if="creatingGroup" class="fa-solid fa-spinner fa-spin"></i>
            <i v-else class="fa-solid fa-plus"></i>
            <span>{{ creatingGroup ? (isEditingGroup ? '更新中...' : '创建中...') : (isEditingGroup ? '保存数据源组' : '创建数据源组') }}</span>
          </button>
        </div>
      </div>
    </el-dialog>

    <!-- 刷新弹窗 -->
    <el-dialog :model-value="showRefreshModal && !!refreshingDs" width="560px" :close-on-click-modal="!refreshingDatasourceId" :show-close="false" @close="!refreshingDatasourceId && (showRefreshModal = false, refreshingDs = null)">
      <template #header>
        <div v-if="refreshingDs" class="flex items-center gap-2">
          <i :class="['fa-solid fa-database', refreshingDs.source_type === 'hive' ? 'text-amber-600' : 'text-blue-600']"></i>
          <h3 class="text-lg font-semibold text-slate-900 m-0">刷新数据源: {{ refreshingDs.name }}</h3>
        </div>
      </template>
      <div v-if="refreshingDs">
          <!-- 加载中 -->
          <div v-if="loadingTables" class="flex flex-col items-center justify-center py-8">
            <i class="fa-solid fa-spinner fa-spin text-2xl text-slate-400 mb-2"></i>
            <span class="text-sm text-slate-500">正在获取表列表...</span>
          </div>

          <template v-else>
            <!-- 表选择器 -->
            <div v-if="dbTables.length > 0" class="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
              <div class="mb-2 flex items-center gap-2">
                <i :class="['fa-solid fa-table-list text-sm', refreshingDs.source_type === 'hive' ? 'text-amber-500' : 'text-blue-500']"></i>
                <span class="text-sm font-medium text-slate-700">选择表</span>
                <span :class="['rounded-full px-1.5 py-0.5 text-xs font-medium', refreshingDs.source_type === 'hive' ? 'bg-amber-100 text-amber-700' : 'bg-blue-100 text-blue-700']">
                  {{ selectionSummaryText }}
                </span>
                <div class="relative flex-1">
                  <i class="fa-solid fa-search absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-slate-400"></i>
                  <input v-model="tableSearchKeyword" type="text" placeholder="搜索..." :class="['w-full rounded border border-slate-200 bg-slate-50 py-1 pl-6 pr-2 text-xs focus:bg-white focus:outline-none', refreshingDs.source_type === 'hive' ? 'focus:border-amber-500' : 'focus:border-blue-500']" />
                </div>
                <button class="rounded px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100" @click="selectAllTables">{{ selectAllButtonText }}</button>
                <button class="rounded px-1.5 py-0.5 text-xs text-slate-500 hover:bg-slate-100" @click="deselectAllTables">{{ deselectAllButtonText }}</button>
              </div>
              <div class="max-h-44 overflow-y-auto rounded-lg border border-slate-100 bg-slate-50">
                <label v-for="table in filteredTables" :key="table" class="flex cursor-pointer items-center gap-2 px-3 py-1 transition-colors hover:bg-white">
                  <input type="checkbox" :checked="selectedTables.has(table)" :class="['h-3.5 w-3.5 shrink-0 rounded border-slate-300', refreshingDs.source_type === 'hive' ? 'text-amber-600 focus:ring-amber-500' : 'text-blue-600 focus:ring-blue-500']" @change="toggleTableSelection(table)" />
                  <span class="truncate text-xs text-slate-600">{{ table }}</span>
                </label>
              </div>
            </div>

          </template>

          <div class="mt-4 flex justify-between">
            <button :disabled="refreshingDatasourceId !== null" class="rounded px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50" @click="showRefreshModal = false; refreshingDs = null">取消</button>
            <button
              :disabled="refreshingDatasourceId !== null || loadingTables || selectedTables.size === 0"
              :class="['flex items-center gap-2 rounded px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50', refreshingDs.source_type === 'hive' ? 'bg-amber-600 hover:bg-amber-700' : 'bg-blue-600 hover:bg-blue-700']"
              @click="handleConfirmRefresh"
            >
              <i v-if="refreshingDatasourceId" class="fa-solid fa-spinner fa-spin"></i>
              <i v-else class="fa-solid fa-sync"></i>
              <span>{{ refreshingDatasourceId ? '刷新中...' : `刷新已选 ${selectedTables.size} 个表` }}</span>
            </button>
          </div>
        </div>
    </el-dialog>
  </div>
</template>
