<script setup lang="ts">
/**
 * 后台管理 · 本体对象列表面板。
 * 对象/属性/关系为本体三等实体；本面板负责对象资产治理：
 * 列表过滤（关键字/状态/域）、新建与整体编辑、状态迁移、软删。
 */
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { deleteAdminOntologyObject, getAdminOntologyObjects } from '../../api'
import type { OntologyObjectItem, OntologyObjectStatus } from '../../types'
import OntologyObjectForm from './OntologyObjectForm.vue'
import OntologyStatusDialog from './OntologyStatusDialog.vue'
import { ONTOLOGY_STATUS_META } from './ontologyMeta'

const emit = defineEmits<{ changed: [] }>()

const loading = ref(false)
const items = ref<OntologyObjectItem[]>([])
const keyword = ref('')
const filterStatus = ref<OntologyObjectStatus | undefined>(undefined)
const filterDomain = ref<string | undefined>(undefined)
const hasLoaded = ref(false)

// 客户端分页：本体对象为公司级治理资产，量级有限
const page = ref(1)
const pageSize = ref(20)
const pagedItems = computed(() => {
  const start = (page.value - 1) * pageSize.value
  return items.value.slice(start, start + pageSize.value)
})

// 域筛选项从已加载对象中提取（后端无独立域清单接口）
const knownDomains = computed(() => Array.from(new Set(items.value.map(item => item.domain).filter(Boolean))).sort())

const loadObjects = async () => {
  loading.value = true
  try {
    const res = await getAdminOntologyObjects({
      keyword: keyword.value.trim() || undefined,
      status: filterStatus.value || undefined,
      domain: filterDomain.value || undefined
    })
    if (res.status === 'success') {
      items.value = res.data?.objects || []
      hasLoaded.value = true
      const maxPage = Math.max(1, Math.ceil(items.value.length / pageSize.value))
      if (page.value > maxPage) page.value = maxPage
    } else {
      ElMessage.error(res.msg || '获取本体对象列表失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取本体对象列表失败:', err)
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadObjects()
}

const reload = async () => {
  await loadObjects()
}

const searchObjects = () => {
  page.value = 1
  void loadObjects()
}

const onFilterChange = () => {
  page.value = 1
  void loadObjects()
}

// ---------- 编辑与状态 ----------

const formVisible = ref(false)
const formObjectName = ref('')

const statusDialogVisible = ref(false)
const statusTarget = ref<OntologyObjectItem | null>(null)

const openCreate = () => {
  formObjectName.value = ''
  formVisible.value = true
}

const openEdit = (row: OntologyObjectItem) => {
  formObjectName.value = row.object_name
  formVisible.value = true
}

const openStatus = (row: OntologyObjectItem) => {
  statusTarget.value = row
  statusDialogVisible.value = true
}

const onSaved = async () => {
  await loadObjects()
  emit('changed')
}

const removeObject = async (row: OntologyObjectItem) => {
  const statusHint =
    row.status === 'certified'
      ? 'certified 对象禁止删除，请先降级或标记废弃。'
      : '删除后对象、其全部属性与参与的关系将一并移除（软删）。'
  try {
    await ElMessageBox.confirm(
      `确定删除本体对象「${row.display_name || row.object_name}」？${statusHint}`,
      '删除本体对象',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntologyObject(row.object_name)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      await onSaved()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如 certified 禁删），不再重复提示
    console.error('删除本体对象失败:', err)
  }
}

defineExpose({ ensureLoaded, reload })
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 说明条 -->
    <div class="shrink-0 border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
      本体对象是语义层的外圈导航索引：认证态进入 agent 上下文；口径计算始终由语义层管理。
    </div>

    <!-- 工具栏 -->
    <div class="flex shrink-0 items-center gap-2 px-4 py-2">
      <el-select
        v-model="filterStatus"
        size="small"
        placeholder="全部状态"
        clearable
        :empty-values="[null, undefined]"
        class="!w-36"
        @change="onFilterChange"
      >
        <el-option v-for="(meta, key) in ONTOLOGY_STATUS_META" :key="key" :label="meta.label" :value="key" />
      </el-select>
      <el-select
        v-model="filterDomain"
        size="small"
        placeholder="全部业务域"
        clearable
        filterable
        :empty-values="[null, undefined]"
        class="!w-40"
        @change="onFilterChange"
      >
        <el-option v-for="domain in knownDomains" :key="domain" :label="domain" :value="domain" />
      </el-select>
      <el-input
        v-model="keyword"
        size="small"
        placeholder="搜索对象名/展示名/描述"
        clearable
        class="!w-56"
        @keyup.enter="searchObjects"
        @clear="searchObjects"
      />
      <el-button size="small" @click="searchObjects">搜索</el-button>
      <el-button size="small" :loading="loading" @click="loadObjects">
        <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
      </el-button>
      <div class="flex-1"></div>
      <el-button size="small" type="primary" round @click="openCreate">
        <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
        新建对象
      </el-button>
    </div>

    <!-- 对象列表 -->
    <div class="flex min-h-0 flex-1 flex-col px-4 pb-4">
      <el-table
        :data="pagedItems"
        v-loading="loading"
        size="small"
        height="100%"
        style="width: 100%"
        empty-text="暂无本体对象"
      >
        <el-table-column label="对象" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="font-mono text-xs text-slate-800">{{ row.object_name }}</span>
            <span v-if="row.display_name" class="ml-1.5 text-xs text-slate-500">{{ row.display_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tooltip :content="ONTOLOGY_STATUS_META[row.status as keyof typeof ONTOLOGY_STATUS_META]?.hint || row.status" placement="top">
              <el-tag size="small" :type="ONTOLOGY_STATUS_META[row.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'" effect="plain">
                {{ ONTOLOGY_STATUS_META[row.status as keyof typeof ONTOLOGY_STATUS_META]?.label || row.status }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="可见性" width="80" align="center">
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ ONTOLOGY_VISIBILITY_META[row.visibility as keyof typeof ONTOLOGY_VISIBILITY_META]?.label || row.visibility }}</span>
          </template>
        </el-table-column>
        <el-table-column label="业务域" width="110" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag v-if="row.domain" size="small" type="info" effect="plain">{{ row.domain }}</el-tag>
            <span v-else class="text-xs text-slate-300">-</span>
          </template>
        </el-table-column>
        <el-table-column label="绑定" width="70" align="center">
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.bindings_count ?? (row.bindings || []).length }}</span>
          </template>
        </el-table-column>
        <el-table-column label="描述" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.description || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="145" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.update_time || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="修改人员" width="95" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="text-xs text-slate-500">{{ row.update_by_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right" class-name="object-op-col">
          <template #default="{ row }">
            <el-button link size="small" @click="openEdit(row)">
              <i class="fa-solid fa-pen text-[11px]"></i> 编辑
            </el-button>
            <el-button link size="small" @click="openStatus(row)">
              <i class="fa-solid fa-arrow-right-arrow-left text-[11px]"></i> 状态
            </el-button>
            <el-button link size="small" type="danger" @click="removeObject(row)">
              <i class="fa-solid fa-trash text-[11px]"></i> 删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="items.length > 0" class="flex shrink-0 justify-end pt-2">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="items.length"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          size="small"
          background
        />
      </div>
    </div>

    <!-- 新建/编辑弹窗 -->
    <OntologyObjectForm v-model:visible="formVisible" :object-name="formObjectName" @saved="onSaved" />

    <!-- 状态迁移弹窗 -->
    <OntologyStatusDialog v-model:visible="statusDialogVisible" :object="statusTarget" :objects="items" @saved="onSaved" />
  </div>
</template>

<style scoped>
/* 操作列按钮必须同行，不许折行 */
:deep(.object-op-col .cell) {
  white-space: nowrap;
}
</style>
