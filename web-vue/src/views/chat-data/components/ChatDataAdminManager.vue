<script setup lang="ts">
/**
 * 智能问数后台管理弹窗。
 * 只负责管理入口、页签和通用弹窗框架，具体能力由子组件承载。
 */
import { computed, nextTick, ref } from 'vue'
import { Close } from '@element-plus/icons-vue'
import DataSourceManager from './DataSourceManager.vue'
import GlobalSqlHistory from './GlobalSqlHistory.vue'
import AgentScheduleAdminPanel from './AgentScheduleAdminPanel.vue'
import SettingAdminPanel from './SettingAdminPanel.vue'
import { getAdminDatasources } from '../api'
import type { DataSource } from '../types'

const props = withDefaults(defineProps<{
  modelValue: boolean
}>(), {})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'datasources-updated': []
  'open-semantic-config': [datasourceId: string, datasource?: DataSource]
}>()

const activeTab = ref<'datasource' | 'sqlHistory' | 'schedule' | 'setting'>('datasource')
const datasourceManagerRef = ref<InstanceType<typeof DataSourceManager> | null>(null)
const sqlHistoryRef = ref<InstanceType<typeof GlobalSqlHistory> | null>(null)
const schedulePanelRef = ref<InstanceType<typeof AgentScheduleAdminPanel> | null>(null)
const settingPanelRef = ref<InstanceType<typeof SettingAdminPanel> | null>(null)
const adminDatasourceOptions = ref<DataSource[]>([])
const adminDatasourceOptionsLoaded = ref(false)
const loadingAdminDatasourceOptions = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const tabs = [
  { key: 'datasource', label: '数据源管理', icon: 'fa-database' },
  { key: 'sqlHistory', label: '全局历史 SQL', icon: 'fa-clock-rotate-left' },
  { key: 'schedule', label: '定时任务', icon: 'fa-stopwatch' },
  { key: 'setting', label: '运行配置', icon: 'fa-sliders' },
] as const

const openDatasourceGroupModal = () => {
  activeTab.value = 'datasource'
  datasourceManagerRef.value?.openGroupModal()
}

const loadAdminDatasourceOptions = async (force = false) => {
  if (loadingAdminDatasourceOptions.value) return
  if (!force && adminDatasourceOptionsLoaded.value) return
  loadingAdminDatasourceOptions.value = true
  try {
    const res = await getAdminDatasources({ include_tables: false })
    if (res.status === 'success') {
      adminDatasourceOptions.value = res.data.datasources || []
      adminDatasourceOptionsLoaded.value = true
    }
  } catch (err) {
    console.error('加载后台数据源选项失败:', err)
  } finally {
    loadingAdminDatasourceOptions.value = false
  }
}

const handleDatasourcesUpdated = () => {
  adminDatasourceOptionsLoaded.value = false
  void loadAdminDatasourceOptions(true)
  emit('datasources-updated')
}

const switchTab = async (tab: 'datasource' | 'sqlHistory' | 'schedule' | 'setting') => {
  activeTab.value = tab
  if (tab === 'schedule') {
    await nextTick()
    schedulePanelRef.value?.ensureLoaded()
    return
  }
  if (tab === 'setting') {
    await nextTick()
    settingPanelRef.value?.ensureLoaded()
    return
  }
  if (tab !== 'sqlHistory') return
  await loadAdminDatasourceOptions()
  await nextTick()
  sqlHistoryRef.value?.ensureLoaded()
}
</script>

<template>
  <el-dialog v-model="visible" width="min(1320px, 94vw)" :show-close="false">
    <template #header>
      <div class="flex items-center justify-between">
        <div class="flex min-w-0 items-center gap-3">
          <h3 class="m-0 flex shrink-0 items-center text-sm font-medium text-slate-700">
            <i class="fa-solid fa-sliders mr-2 text-blue-500"></i>
            智能问数后台管理
          </h3>
          <div class="flex items-center gap-1 rounded-lg bg-slate-100 p-1">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              :class="[
                'flex h-7 items-center gap-1.5 rounded-md px-3 text-xs transition-colors',
                activeTab === tab.key ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              ]"
              @click="switchTab(tab.key)"
            >
              <i :class="['fa-solid text-[10px]', tab.icon]"></i>
              <span>{{ tab.label }}</span>
            </button>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <el-button v-if="activeTab === 'datasource'" type="primary" size="small" round @click="openDatasourceGroupModal">
            <i class="fa-solid fa-plus" style="margin-right: 4px; font-size: 10px;"></i>
            新建
          </el-button>
          <el-button :icon="Close" circle size="small" @click="visible = false" />
        </div>
      </div>
    </template>

    <div class="h-[min(700px,72vh)] overflow-hidden">
      <DataSourceManager
        v-show="activeTab === 'datasource'"
        ref="datasourceManagerRef"
        class="h-full"
        @datasources-updated="handleDatasourcesUpdated"
        @open-semantic-config="(datasourceId, datasource) => emit('open-semantic-config', datasourceId, datasource)"
      />
      <GlobalSqlHistory
        v-show="activeTab === 'sqlHistory'"
        ref="sqlHistoryRef"
        class="h-full"
        :datasources="adminDatasourceOptions"
      />
      <AgentScheduleAdminPanel
        v-show="activeTab === 'schedule'"
        ref="schedulePanelRef"
        class="h-full"
      />
      <SettingAdminPanel
        v-show="activeTab === 'setting'"
        ref="settingPanelRef"
        class="h-full"
      />
    </div>
  </el-dialog>
</template>
