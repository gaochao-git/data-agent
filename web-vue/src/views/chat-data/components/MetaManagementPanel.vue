<script setup lang="ts">
/**
 * 后台管理 · 元数据管理面板。
 * 顶层 tab 里的「元数据」容器：左侧为元数据类型导航（枚举值 / 数据血缘…），
 * 右侧承载对应类型的管理子面板。新增元数据类型时在 META_TYPES 登记并挂载子组件即可，
 * 不要在本容器里堆叠具体类型的管理逻辑。
 */
import { nextTick, ref } from 'vue'
import { ElMessage } from 'element-plus'
import GlobalEnumAdminPanel from './GlobalEnumAdminPanel.vue'

type MetaTypeKey = 'enum' | 'lineage'

const META_TYPES: Array<{ key: MetaTypeKey; label: string; icon: string; enabled: boolean; hint?: string }> = [
  { key: 'enum', label: '枚举值', icon: 'fa-list-check', enabled: true },
  { key: 'lineage', label: '数据血缘', icon: 'fa-diagram-project', enabled: false, hint: '数据血缘管理规划中' }
]

const activeType = ref<MetaTypeKey>('enum')
const enumPanelRef = ref<InstanceType<typeof GlobalEnumAdminPanel> | null>(null)
const hasLoaded = ref(false)

const switchType = async (meta: (typeof META_TYPES)[number]) => {
  if (!meta.enabled) {
    ElMessage.info(meta.hint || '该元数据类型暂未开放全局化管理')
    return
  }
  activeType.value = meta.key
  if (meta.key === 'enum') {
    await nextTick()
    enumPanelRef.value?.ensureLoaded()
  }
}

const ensureLoaded = async () => {
  if (hasLoaded.value) return
  hasLoaded.value = true
  if (activeType.value === 'enum') {
    await nextTick()
    enumPanelRef.value?.ensureLoaded()
  }
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 overflow-hidden">
    <!-- 左侧类型导航 -->
    <div class="flex w-40 shrink-0 flex-col gap-1 border-r border-slate-100 bg-slate-50/60 p-2">
      <button
        v-for="meta in META_TYPES"
        :key="meta.key"
        type="button"
        :class="[
          'flex h-8 items-center gap-2 rounded-md px-2.5 text-xs transition-colors',
          activeType === meta.key && meta.enabled ? 'bg-white text-blue-600 shadow-sm' : '',
          meta.enabled && activeType !== meta.key ? 'text-slate-600 hover:bg-white/70 hover:text-slate-800' : '',
          !meta.enabled ? 'cursor-not-allowed text-slate-300' : ''
        ]"
        @click="switchType(meta)"
      >
        <i :class="['fa-solid text-[10px]', meta.icon]"></i>
        <span>{{ meta.label }}</span>
        <i v-if="!meta.enabled" class="fa-solid fa-lock ml-auto text-[8px]"></i>
      </button>
    </div>

    <!-- 右侧类型内容区 -->
    <div class="min-w-0 flex-1 overflow-hidden">
      <GlobalEnumAdminPanel v-show="activeType === 'enum'" ref="enumPanelRef" class="h-full" />
    </div>
  </div>
</template>
