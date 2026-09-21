<script setup lang="ts">
import { computed, ref, watch, type PropType } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getAdminDatasourceTableIndexes,
  updateAdminDatasourceTableIndexes,
} from '../api'
import type { DataSource, DatasourceTableIndexItem, TableInfo } from '../types'

const props = defineProps({
  visible: { type: Boolean, default: false },
  datasource: { type: Object as PropType<DataSource | null>, default: null },
  table: { type: Object as PropType<TableInfo | null>, default: null },
})

const emit = defineEmits(['close', 'saved'])
const rows = ref<DatasourceTableIndexItem[]>([])
const loading = ref(false)
const saving = ref(false)
const loadRequestId = ref(0)

const cloneIndexes = (indexes: DatasourceTableIndexItem[] = []) => indexes.map(item => ({
  index_type: item.index_type,
  index_name: item.index_type === 'primary' ? 'PRIMARY' : String(item.index_name || ''),
  columns: [...(item.columns || [])],
}))

const physicalIndexes = computed(() => {
  const table = props.table
  if (!table) return []
  return [
    ...(table.primary_keys || []).map((columns: string[]) => ({ type: 'primary', name: 'PRIMARY', columns })),
    ...(table.unique_indexes || []).map(item => ({ type: 'unique', name: item.name || 'UNIQUE', columns: item.columns || [] })),
    ...(table.indexes || []).map(item => ({ type: 'secondary', name: item.name || 'INDEX', columns: item.columns || [] })),
  ]
})

const loadIndexes = async () => {
  const requestId = ++loadRequestId.value
  const datasourceId = String(props.datasource?.id || '')
  const tableName = String(props.table?.table_name || '')
  if (!props.visible || !datasourceId || !tableName) {
    rows.value = []
    loading.value = false
    return
  }
  rows.value = cloneIndexes(props.table?.manual_indexes || [])
  loading.value = true
  try {
    const res = await getAdminDatasourceTableIndexes(datasourceId, tableName)
    if (requestId !== loadRequestId.value) return
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '获取索引配置失败')
      emit('close')
      return
    }
    rows.value = cloneIndexes(res.data?.indexes || [])
  } catch {
    if (requestId !== loadRequestId.value) return
    ElMessage.error('获取索引配置失败')
    emit('close')
  } finally {
    if (requestId === loadRequestId.value) loading.value = false
  }
}

watch(() => [props.visible, props.datasource?.id, props.table?.table_name], loadIndexes)

const addIndex = () => {
  if (rows.value.length >= 5) {
    ElMessage.warning('单表最多配置 5 个手工索引')
    return
  }
  rows.value = [...rows.value, { index_type: 'secondary', index_name: '', columns: [] }]
}

const removeIndex = (index: number) => {
  rows.value = rows.value.filter((_, rowIndex) => rowIndex !== index)
}

const requestClose = () => {
  if (!saving.value) emit('close')
}

const handleTypeChange = (row: DatasourceTableIndexItem) => {
  if (row.index_type === 'primary') row.index_name = 'PRIMARY'
  else if (row.index_name === 'PRIMARY') row.index_name = ''
}

const validateRows = () => {
  const seen = new Set<string>()
  let primaryCount = 0
  for (const row of rows.value) {
    const name = String(row.index_name || '').trim()
    if (row.index_type === 'primary') primaryCount += 1
    if (primaryCount > 1) return '同一张表只能配置一个主键索引'
    if (row.index_type !== 'primary' && !name) return '请填写索引名称'
    if (row.index_type === 'unique' && !name.startsWith('uniq_')) return '唯一索引名称必须以 uniq_ 开头'
    if (row.index_type === 'secondary' && !name.startsWith('idx_')) return '普通索引名称必须以 idx_ 开头'
    if (!row.columns.length) return '每个索引至少选择一个字段'
    if (row.columns.length > 5) return '单个组合索引最多包含 5 个字段'
    const key = `${row.index_type}:${row.index_type === 'primary' ? 'primary' : name.toLowerCase()}`
    if (seen.has(key)) return `索引名称重复: ${name}`
    seen.add(key)
  }
  return ''
}

const saveIndexes = async () => {
  const datasourceId = String(props.datasource?.id || '')
  const tableName = String(props.table?.table_name || '')
  if (!datasourceId || !tableName) return
  const validationError = validateRows()
  if (validationError) {
    ElMessage.warning(validationError)
    return
  }
  const payload = cloneIndexes(rows.value).map(item => ({
    ...item,
    index_name: item.index_type === 'primary' ? 'PRIMARY' : item.index_name.trim(),
  }))
  saving.value = true
  try {
    const res = await updateAdminDatasourceTableIndexes(datasourceId, tableName, payload)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '保存索引配置失败')
      return
    }
    const saved = cloneIndexes(res.data?.indexes || [])
    ElMessage.success('索引配置已保存')
    emit('saved', saved)
  } catch {
    ElMessage.error('保存索引配置失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog :model-value="visible" width="min(820px, 94vw)" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="false" @close="requestClose">
    <template #header>
      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0 flex items-center gap-2">
          <i class="fa-solid fa-list-ol text-blue-600"></i>
          <h3 class="m-0 truncate text-lg font-semibold text-slate-900">表索引配置<span v-if="table"> - {{ table.table_name }}</span></h3>
        </div>
        <button type="button" :disabled="saving" class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:cursor-not-allowed disabled:opacity-50" @click="requestClose"><i class="fa-solid fa-xmark"></i></button>
      </div>
    </template>

    <div class="max-h-[72vh] space-y-4 overflow-y-auto">
      <div class="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">物理索引由元数据同步自动维护，只读展示；手工索引保存后会在后续同步中保留。</div>
      <div v-if="loading" class="flex min-h-[220px] flex-col items-center justify-center text-slate-400"><i class="fa-solid fa-spinner fa-spin mb-2 text-xl"></i><span class="text-sm">正在加载索引配置...</span></div>
      <template v-else>
        <section class="border border-slate-200">
          <div class="border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-600">物理索引（只读）</div>
          <div v-if="physicalIndexes.length === 0" class="px-3 py-4 text-center text-xs text-slate-400">暂无物理索引</div>
          <div v-else class="divide-y divide-slate-100">
            <div v-for="(item, index) in physicalIndexes" :key="`${item.type}-${item.name}-${index}`" class="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
              <span class="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500">{{ item.type === 'primary' ? '主键' : item.type === 'unique' ? '唯一' : '普通' }}</span>
              <span class="font-mono text-slate-800">{{ item.name }}</span><span class="font-mono text-slate-500">({{ item.columns.join(', ') }})</span>
            </div>
          </div>
        </section>

        <section class="border border-blue-100">
          <div class="flex items-center justify-between gap-3 border-b border-blue-100 bg-blue-50 px-3 py-2">
            <span class="text-xs font-semibold text-blue-700">手工索引</span>
            <button type="button" :disabled="rows.length >= 5" class="flex items-center gap-1 rounded bg-white px-2 py-1 text-xs font-medium text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-50" @click="addIndex"><i class="fa-solid fa-plus text-[10px]"></i><span>新增索引</span></button>
          </div>
          <div v-if="rows.length === 0" class="px-3 py-5 text-center text-xs text-slate-400">暂无手工索引</div>
          <div v-else class="space-y-3 p-3">
            <div v-for="(row, index) in rows" :key="index" class="border border-slate-200 p-3">
              <div class="grid grid-cols-1 items-center gap-2 sm:grid-cols-[120px_minmax(0,1fr)_auto]">
                <select v-model="row.index_type" class="rounded border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700 outline-none focus:border-blue-500" @change="handleTypeChange(row)"><option value="primary">主键</option><option value="unique">唯一索引</option><option value="secondary">普通索引</option></select>
                <input v-model="row.index_name" :disabled="row.index_type === 'primary'" type="text" maxlength="32" :placeholder="row.index_type === 'unique' ? 'uniq_xxx' : 'idx_xxx'" class="min-w-0 rounded border border-slate-200 px-2 py-1.5 text-xs font-mono text-slate-700 outline-none focus:border-blue-500 disabled:bg-slate-100" />
                <button type="button" class="justify-self-end rounded px-2 py-1.5 text-xs text-red-500 hover:bg-red-50" title="删除索引" @click="removeIndex(index)"><i class="fa-solid fa-trash"></i></button>
              </div>
              <div class="mt-2 flex flex-wrap gap-2">
                <label v-for="column in (table?.columns || [])" :key="column.name" class="inline-flex cursor-pointer items-center gap-1.5 rounded border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"><input v-model="row.columns" type="checkbox" :value="column.name" class="h-3.5 w-3.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500" /><span class="font-mono">{{ column.name }}</span></label>
              </div>
              <div class="mt-1 text-[11px] text-slate-400">字段选择顺序即组合索引顺序，当前已选 {{ row.columns.length }} 个</div>
            </div>
          </div>
        </section>
      </template>
      <div class="flex items-center justify-end gap-2"><button type="button" :disabled="saving" class="rounded px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-50" @click="requestClose">取消</button><button type="button" :disabled="saving || loading" class="flex items-center gap-2 rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50" @click="saveIndexes"><i :class="['fa-solid', saving ? 'fa-spinner fa-spin' : 'fa-floppy-disk']"></i><span>{{ saving ? '保存中...' : '保存索引' }}</span></button></div>
    </div>
  </el-dialog>
</template>
