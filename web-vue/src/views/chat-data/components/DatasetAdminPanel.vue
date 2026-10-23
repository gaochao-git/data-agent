<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  bootstrapAdminDatasets,
  deleteAdminDataset,
  getAdminDataset,
  getAdminDatasets,
  getAdminDatasources
} from '../api'
import type { DataSource, LogicalDatasetItem } from '../types'

const loading = ref(false)
const bootstrapping = ref(false)
const items = ref<LogicalDatasetItem[]>([])
const datasources = ref<DataSource[]>([])
const groupId = ref('')
const detail = ref<LogicalDatasetItem | null>(null)
const detailVisible = ref(false)
const hasLoaded = ref(false)

const groups = computed(() => {
  const seen = new Set<string>()
  return datasources.value.filter((item) => {
    const id = item.id
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
})

const load = async () => {
  loading.value = true
  try {
    const res = await getAdminDatasets()
    if (res.status === 'success') items.value = res.data.datasets || []
  } finally {
    loading.value = false
  }
}

const loadDatasources = async () => {
  try {
    const res = await getAdminDatasources({ include_tables: false })
    if (res.status === 'success') {
      datasources.value = res.data.datasources || []
      const firstGroup = groups.value[0]
      if (!groupId.value && firstGroup) groupId.value = firstGroup.id
    }
  } catch (err) {
    console.error('加载数据源组失败:', err)
  }
}

const ensureLoaded = async () => {
  if (hasLoaded.value) return
  hasLoaded.value = true
  await Promise.all([load(), loadDatasources()])
}

const bootstrap = async () => {
  if (!groupId.value) {
    ElMessage.warning('请选择数据源组')
    return
  }
  bootstrapping.value = true
  try {
    const res = await bootstrapAdminDatasets(groupId.value)
    if (res.status === 'success') {
      ElMessage.success(`已注册 ${res.data.datasets?.length || 0} 个 Dataset`)
      await load()
    } else {
      ElMessage.error(res.msg || '注册失败')
    }
  } catch (err) {
    console.error('注册 Dataset 失败:', err)
  } finally {
    bootstrapping.value = false
  }
}

const openDetail = async (row: LogicalDatasetItem) => {
  const res = await getAdminDataset(row.dataset_name)
  if (res.status === 'success') {
    detail.value = res.data
    detailVisible.value = true
  }
}

const remove = async (row: LogicalDatasetItem) => {
  try {
    await ElMessageBox.confirm(`确定删除 Dataset「${row.dataset_name}」？`, '删除 Dataset', { type: 'warning' })
  } catch {
    return
  }
  const res = await deleteAdminDataset(row.dataset_name)
  if (res.status === 'success') {
    ElMessage.success('删除成功')
    await load()
  }
}

onMounted(() => { void ensureLoaded() })
defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full flex-col bg-white">
    <div class="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
      <div>
        <div class="text-sm font-medium text-slate-800">数据集</div>
        <div class="text-[11px] text-slate-400">逻辑 Dataset：一套 schema 绑定多个机构数据源，供指标层和本体层独立引用</div>
      </div>
      <div class="ml-auto flex items-center gap-2">
        <el-select v-model="groupId" size="small" placeholder="选择数据源组" class="w-52">
          <el-option
            v-for="group in groups"
            :key="group.id"
            :label="group.group_name || group.id"
            :value="group.id"
          />
        </el-select>
        <el-button size="small" type="primary" :loading="bootstrapping" @click="bootstrap">从数据源注册</el-button>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <div class="min-h-0 flex-1 overflow-auto p-4">
      <el-table v-loading="loading" :data="items" size="small" border>
        <el-table-column prop="dataset_name" label="Dataset" min-width="150" />
        <el-table-column prop="display_name" label="展示名" min-width="140" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="binding_count" label="数据源" width="80" />
        <el-table-column label="主键" min-width="160">
          <template #default="{ row }">{{ (row.primary_key || []).join(', ') || '-' }}</template>
        </el-table-column>
        <el-table-column prop="update_time" label="更新时间" width="170" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="detailVisible" width="min(900px, 92vw)" :title="detail?.dataset_name || 'Dataset 详情'">
      <template v-if="detail">
        <div class="grid grid-cols-2 gap-3 text-xs">
          <div><span class="text-slate-400">展示名：</span>{{ detail.display_name }}</div>
          <div><span class="text-slate-400">状态：</span>{{ detail.status }}</div>
          <div><span class="text-slate-400">主键：</span>{{ (detail.primary_key || []).join(', ') || '-' }}</div>
          <div><span class="text-slate-400">列数：</span>{{ (detail.columns || []).length }}</div>
        </div>
        <div class="mt-4 text-xs font-medium text-slate-600">成员绑定</div>
        <el-table :data="detail.bindings || []" size="small" border class="mt-1">
          <el-table-column prop="member_datasource_id" label="成员数据源" />
          <el-table-column prop="physical_table" label="物理表" />
          <el-table-column prop="status" label="状态" width="100" />
        </el-table>
        <div class="mt-4 text-xs font-medium text-slate-600">版本</div>
        <el-table :data="detail.versions || []" size="small" border class="mt-1">
          <el-table-column prop="version_no" label="版本" width="80" />
          <el-table-column prop="version_id" label="版本ID" />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column prop="create_time" label="创建时间" width="170" />
        </el-table>
      </template>
    </el-dialog>
  </div>
</template>
