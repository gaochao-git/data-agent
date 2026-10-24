<script setup lang="ts">
/**
 * 智能问数后台管理弹窗。
 * 只负责管理入口、左侧分组导航和通用弹窗框架，具体能力由子面板承载。
 * 导航按性质分组（数据资产/语义层/本体/智能体/系统）：新功能先归组再挂载面板，
 * 不要回退成平铺 tab 条；本体是业务对象操作层，不归入数据资产。
 */
import { computed, nextTick, ref } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import DataSourceManager from './DataSourceManager.vue'
import DatasetAdminPanel from './DatasetAdminPanel.vue'
import GlobalEnumAdminPanel from './GlobalEnumAdminPanel.vue'
import OntologyAdminPanel from './ontology/OntologyAdminPanel.vue'
import SemanticModelAdminPanel from './SemanticModelAdminPanel.vue'
import GlobalSqlHistory from './GlobalSqlHistory.vue'
import AgentScheduleAdminPanel from './AgentScheduleAdminPanel.vue'
import ModelAdminPanel from './ModelAdminPanel.vue'
import PluginAdminPanel from './PluginAdminPanel.vue'
import SettingAdminPanel from './SettingAdminPanel.vue'
import { getAdminDatasources } from '../api'
import type { DataSource } from '../types'

type AdminNavKey =
  | 'datasource'
  | 'dataset'
  | 'semantic'
  | 'enum'
  | 'ontology'
  | 'lineage'
  | 'schedule'
  | 'plugins'
  | 'setting'
  | 'models'
  | 'sqlHistory'

interface AdminNavItem {
  key: AdminNavKey
  label: string
  icon: string
  disabled?: boolean
  hint?: string
}

// 左侧分组导航：分组即信息架构（资产/自动化/系统），新增条目先归组
const NAV_GROUPS: Array<{ label: string; items: AdminNavItem[] }> = [
  {
    label: '数据资产',
    items: [
      { key: 'datasource', label: '数据源管理', icon: 'fa-database' },
      { key: 'dataset', label: '数据集', icon: 'fa-layer-group' },
      { key: 'lineage', label: '数据血缘', icon: 'fa-diagram-project', disabled: true, hint: '数据血缘管理规划中' }
    ]
  },
  {
    label: '语义层',
    items: [
      { key: 'semantic', label: '语义模型管理', icon: 'fa-sitemap' },
      { key: 'enum', label: '枚举值', icon: 'fa-list-check' }
    ]
  },
  {
    label: '业务本体',
    items: [{ key: 'ontology', label: '本体', icon: 'fa-cubes' }]
  },
  {
    label: '智能体',
    items: [
      { key: 'models', label: '大模型管理', icon: 'fa-microchip' },
      { key: 'schedule', label: '定时任务', icon: 'fa-stopwatch' },
      { key: 'plugins', label: '插件管理', icon: 'fa-plug' }
    ]
  },
  {
    label: '系统',
    items: [
      { key: 'setting', label: '运行配置', icon: 'fa-sliders' },
      { key: 'sqlHistory', label: '历史 SQL', icon: 'fa-clock-rotate-left' }
    ]
  }
]

const props = withDefaults(defineProps<{
  modelValue: boolean
}>(), {})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'datasources-updated': []
}>()

const activeNav = ref<AdminNavKey>('datasource')
const enumPanelRef = ref<InstanceType<typeof GlobalEnumAdminPanel> | null>(null)
const ontologyPanelRef = ref<InstanceType<typeof OntologyAdminPanel> | null>(null)
const sqlHistoryRef = ref<InstanceType<typeof GlobalSqlHistory> | null>(null)
const schedulePanelRef = ref<InstanceType<typeof AgentScheduleAdminPanel> | null>(null)
const modelPanelRef = ref<InstanceType<typeof ModelAdminPanel> | null>(null)
const pluginPanelRef = ref<InstanceType<typeof PluginAdminPanel> | null>(null)
const settingPanelRef = ref<InstanceType<typeof SettingAdminPanel> | null>(null)
const adminDatasourceOptions = ref<DataSource[]>([])
const adminDatasourceOptionsLoaded = ref(false)
const loadingAdminDatasourceOptions = ref(false)

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value)
})

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

const switchNav = async (item: AdminNavItem) => {
  if (item.disabled) {
    ElMessage.info(item.hint || '该功能暂未开放')
    return
  }
  activeNav.value = item.key
  await nextTick()
  if (item.key === 'enum') {
    enumPanelRef.value?.ensureLoaded()
  } else if (item.key === 'ontology') {
    ontologyPanelRef.value?.ensureLoaded()
  } else if (item.key === 'schedule') {
    schedulePanelRef.value?.ensureLoaded()
  } else if (item.key === 'plugins') {
    pluginPanelRef.value?.ensureLoaded()
  } else if (item.key === 'models') {
    modelPanelRef.value?.ensureLoaded()
  } else if (item.key === 'setting') {
    settingPanelRef.value?.ensureLoaded()
  } else if (item.key === 'sqlHistory') {
    await loadAdminDatasourceOptions()
    sqlHistoryRef.value?.ensureLoaded()
  } else if (item.key === 'semantic') {
    await loadAdminDatasourceOptions()
  }
}
</script>

<template>
  <el-dialog v-model="visible" width="min(1440px, 96vw)" :show-close="false">
    <template #header>
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2 text-sm font-medium text-slate-700">
          <i class="fa-solid fa-gear text-xs text-slate-400"></i>
          <span>后台管理</span>
        </div>
        <div class="flex items-center gap-2">
          <el-button :icon="Close" circle size="small" @click="visible = false" />
        </div>
      </div>
    </template>

    <div class="flex h-[min(700px,72vh)] overflow-hidden">
      <!-- 左侧分组导航 -->
      <div class="flex w-44 shrink-0 flex-col overflow-y-auto border-r border-slate-100 bg-slate-50/60 p-2">
        <div v-for="(group, groupIndex) in NAV_GROUPS" :key="group.label">
          <div
            :class="[
              'px-2.5 text-[10px] font-medium tracking-wide text-slate-400',
              groupIndex === 0 ? 'pb-1 pt-1' : 'mt-2 border-t border-slate-200/60 pb-1 pt-2.5'
            ]"
          >
            {{ group.label }}
          </div>
          <button
            v-for="item in group.items"
            :key="item.key"
            type="button"
            :class="[
              'flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-xs transition-colors',
              activeNav === item.key && !item.disabled ? 'bg-white text-blue-600 shadow-sm' : '',
              item.disabled ? 'cursor-not-allowed text-slate-300' : '',
              !item.disabled && activeNav !== item.key ? 'text-slate-600 hover:bg-white/70 hover:text-slate-800' : ''
            ]"
            @click="switchNav(item)"
          >
            <i :class="['fa-solid text-[10px]', item.icon]"></i>
            <span>{{ item.label }}</span>
            <i v-if="item.disabled" class="fa-solid fa-lock ml-auto text-[8px]"></i>
          </button>
        </div>
      </div>

      <!-- 右侧内容区：面板只按导航键显隐，加载时机由 switchNav 触发 -->
      <div class="min-w-0 flex-1 overflow-hidden">
        <DataSourceManager
          v-show="activeNav === 'datasource'"
          class="h-full"
          @datasources-updated="handleDatasourcesUpdated"
        />
        <DatasetAdminPanel v-show="activeNav === 'dataset'" class="h-full" />
        <SemanticModelAdminPanel
          v-show="activeNav === 'semantic'"
          class="h-full"
          :datasources="adminDatasourceOptions"
        />
        <GlobalEnumAdminPanel v-show="activeNav === 'enum'" ref="enumPanelRef" class="h-full" />
        <OntologyAdminPanel v-show="activeNav === 'ontology'" ref="ontologyPanelRef" class="h-full" />
        <GlobalSqlHistory
          v-show="activeNav === 'sqlHistory'"
          ref="sqlHistoryRef"
          class="h-full"
          :datasources="adminDatasourceOptions"
        />
        <AgentScheduleAdminPanel v-show="activeNav === 'schedule'" ref="schedulePanelRef" class="h-full" />
        <PluginAdminPanel v-show="activeNav === 'plugins'" ref="pluginPanelRef" class="h-full" />
        <ModelAdminPanel v-show="activeNav === 'models'" ref="modelPanelRef" class="h-full" />
        <SettingAdminPanel v-show="activeNav === 'setting'" ref="settingPanelRef" class="h-full" />
      </div>
    </div>
  </el-dialog>
</template>
