<script setup lang="ts">
/**
 * 智能问数后台管理弹窗。
 * 只负责管理入口、页签和通用弹窗框架，具体能力由子组件承载。
 */
import { computed, nextTick, ref } from 'vue'
import { Close } from '@element-plus/icons-vue'
import DataSourceManager from './DataSourceManager.vue'
import GlobalSqlHistory from './GlobalSqlHistory.vue'
import type { DataSource } from '../types'

const props = withDefaults(defineProps<{
  modelValue: boolean
  datasources?: DataSource[]
}>(), {
  datasources: () => [],
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  datasourcesChange: [datasources: DataSource[]]
  openSemanticConfig: [datasourceId: string]
}>()

const activeTab = ref<'datasource' | 'sqlHistory'>('datasource')
const datasourceManagerRef = ref<InstanceType<typeof DataSourceManager> | null>(null)
const sqlHistoryRef = ref<InstanceType<typeof GlobalSqlHistory> | null>(null)

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const tabs = [
  { key: 'datasource', label: '数据源管理', icon: 'fa-database' },
  { key: 'sqlHistory', label: '全局历史 SQL', icon: 'fa-clock-rotate-left' },
] as const

const openDatasourceGroupModal = () => {
  activeTab.value = 'datasource'
  datasourceManagerRef.value?.openGroupModal()
}

const switchTab = async (tab: 'datasource' | 'sqlHistory') => {
  activeTab.value = tab
  if (tab !== 'sqlHistory') return
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
        @datasources-change="emit('datasourcesChange', $event)"
        @open-semantic-config="emit('openSemanticConfig', $event)"
      />
      <GlobalSqlHistory
        v-show="activeTab === 'sqlHistory'"
        ref="sqlHistoryRef"
        class="h-full"
        :datasources="datasources"
      />
    </div>
  </el-dialog>
</template>
