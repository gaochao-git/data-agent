<script setup lang="ts">
/**
 * 后台管理 · 模型管理面板（语义模型）。
 * 顶栏一行承载数据源组选择、语义模型选择与批量管理动作（批量初始化 / 批量启停 / 新建），
 * 动作经 ref 转发给内嵌 SemanticConfigPanel（expose 契约），就地编辑不跳出后台弹窗。
 * 数据源列表由容器（ChatDataAdminManager）传入并保证已刷新。
 */
import { ref, watch } from 'vue'
import AdminPanelToolbar from './AdminPanelToolbar.vue'
import SemanticConfigPanel from './SemanticConfigPanel.vue'
import { getDatasourceSchema } from '../api'
import type { DataSource, SemanticConfigItem, TableInfo } from '../types'
import { normalizeTableInfoList, resolveDatasourceType } from '../utils/datasourceSchema'

const props = defineProps<{
  datasources: DataSource[]
}>()

const selectedDatasourceId = ref('')
const selectedConfigId = ref('')
const configOptions = ref<SemanticConfigItem[]>([])
const tablesCache = ref<Record<string, TableInfo[]>>({})
const loadingSchema = ref(false)

const selectedDatasource = () => props.datasources.find(ds => ds.id === selectedDatasourceId.value) || null

const loadSchema = async (datasourceId: string, force = false) => {
  if (!datasourceId) return
  if (!force && tablesCache.value[datasourceId]) return
  loadingSchema.value = true
  try {
    const res = await getDatasourceSchema(datasourceId)
    if (res.status === 'success') {
      tablesCache.value = {
        ...tablesCache.value,
        [datasourceId]: normalizeTableInfoList(res.data?.tables),
      }
    }
  } catch (err) {
    console.error('加载数据源表结构失败:', err)
  } finally {
    loadingSchema.value = false
  }
}

// 列表到达后默认选中首个数据源；选中变化即拉表结构（面板编辑依赖）
watch(() => props.datasources, (list) => {
  if (!selectedDatasourceId.value && list.length > 0 && list[0]) {
    selectedDatasourceId.value = list[0].id
  }
}, { immediate: true })

watch(selectedDatasourceId, (datasourceId) => {
  // 切换数据源后模型选择交还给面板（回落到首个模型并由 update 事件回填）
  selectedConfigId.value = ''
  void loadSchema(datasourceId)
}, { immediate: true })

// 顶栏动作 → 内嵌面板（expose 契约）
const configPanelRef = ref<InstanceType<typeof SemanticConfigPanel> | null>(null)
const currentTables = () => tablesCache.value[selectedDatasourceId.value] || []

const handleGenerateBasicConfigs = () => {
  void configPanelRef.value?.generateBasicConfigs()
}
const handleBatchToggle = (enabled: boolean) => {
  configPanelRef.value?.openBatchToggle(enabled)
}
const handleCreateConfig = () => {
  void configPanelRef.value?.createConfig()
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <AdminPanelToolbar>
      <span class="shrink-0 whitespace-nowrap text-[13px] font-medium text-slate-700">数据源组</span>
      <select
        v-model="selectedDatasourceId"
        class="h-8 w-[260px] max-w-[22vw] rounded-lg border border-slate-200 bg-white px-3 text-[13px] text-slate-800 outline-none focus:border-blue-500"
      >
        <option value="">请选择数据源组</option>
        <option v-for="ds in datasources" :key="ds.id" :value="ds.id">
          {{ ds.group_name }}
        </option>
      </select>
      <span class="shrink-0 whitespace-nowrap text-[13px] font-medium text-slate-700">语义模型</span>
      <el-select
        v-model="selectedConfigId"
        class="!w-[320px] shrink-0"
        filterable
        :disabled="!selectedDatasourceId || configOptions.length === 0"
        placeholder="请选择语义模型"
      >
        <el-option
          v-for="config in configOptions"
          :key="config.id"
          :value="config.id"
          :label="config.name"
        >
          <div class="flex items-center justify-between gap-3">
            <span class="min-w-0 truncate">{{ config.name }}</span>
            <span
              class="shrink-0 rounded border px-1.5 py-px text-[11px] leading-4"
              :class="config.enabled
                ? 'border-emerald-200 bg-emerald-50 text-emerald-600'
                : 'border-slate-200 bg-slate-100 text-slate-500'"
            >{{ config.enabled ? '已启用' : '已禁用' }}</span>
          </div>
        </el-option>
      </el-select>
      <button
        class="h-8 shrink-0 flex items-center gap-1.5 whitespace-nowrap border border-blue-200 rounded-lg bg-blue-50 px-3 text-[12px] text-blue-600 transition-colors hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60"
        title="为尚未建模的物理表初始化基础模型（默认启用）"
        :disabled="!selectedDatasourceId || currentTables().length === 0"
        @click="handleGenerateBasicConfigs"
      >
        <i class="fa-solid fa-layer-group"></i>
        <span>批量初始化</span>
      </button>
      <button
        class="h-8 shrink-0 flex items-center gap-1.5 whitespace-nowrap border border-slate-200 rounded-lg bg-white px-3 text-[12px] text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        title="批量启动选中的语义模型"
        :disabled="!selectedDatasourceId || configOptions.length === 0"
        @click="handleBatchToggle(true)"
      >
        <i class="fa-solid fa-play"></i>
        <span>批量启动</span>
      </button>
      <button
        class="h-8 shrink-0 flex items-center gap-1.5 whitespace-nowrap border border-slate-200 rounded-lg bg-white px-3 text-[12px] text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        title="批量暂停选中的语义模型"
        :disabled="!selectedDatasourceId || configOptions.length === 0"
        @click="handleBatchToggle(false)"
      >
        <i class="fa-solid fa-pause"></i>
        <span>批量暂停</span>
      </button>
      <button
        class="h-8 shrink-0 flex items-center gap-1.5 whitespace-nowrap border border-slate-200 rounded-lg bg-white px-3 text-[12px] text-slate-600 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        title="新建语义模型"
        :disabled="!selectedDatasourceId"
        @click="handleCreateConfig"
      >
        <i class="fa-solid fa-plus"></i>
        <span>新建</span>
      </button>
      <span v-if="loadingSchema" class="text-xs text-slate-400"><i class="fa-solid fa-spinner fa-spin"></i> 表结构加载中</span>
    </AdminPanelToolbar>
    <div class="min-h-0 flex-1 overflow-hidden">
      <SemanticConfigPanel
        v-if="selectedDatasourceId"
        ref="configPanelRef"
        :key="selectedDatasourceId"
        :datasource-id="selectedDatasourceId"
        :datasource-name="selectedDatasource()?.group_name || ''"
        :datasource-type="resolveDatasourceType(selectedDatasource())"
        :tables="tablesCache[selectedDatasourceId] || []"
        :selected-config-id="selectedConfigId || null"
        admin-mode
        embedded
        @config-list-change="configOptions = $event"
        @update:selected-config-id="selectedConfigId = $event || ''"
        @close="selectedDatasourceId = ''"
      />
      <div v-else class="flex h-full items-center justify-center text-sm text-slate-400">
        请选择数据源组
      </div>
    </div>
  </div>
</template>
