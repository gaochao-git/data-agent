<script setup lang="ts">
/**
 * 后台管理 · 本体管理面板。
 * 元数据管理下的「本体」类型容器：左侧小节导航（对象与属性 / 关系），
 * 右侧承载对应子面板。对象状态变化会影响关系状态，切回关系页签时刷新。
 */
import { nextTick, ref } from 'vue'
import OntologyObjectPanel from './OntologyObjectPanel.vue'
import OntologyRelationPanel from './OntologyRelationPanel.vue'

type SectionKey = 'objects' | 'relations'

const SECTIONS: Array<{ key: SectionKey; label: string; icon: string }> = [
  { key: 'objects', label: '对象与属性', icon: 'fa-cubes' },
  { key: 'relations', label: '关系', icon: 'fa-arrows-left-right' }
]

const activeSection = ref<SectionKey>('objects')
const objectPanelRef = ref<InstanceType<typeof OntologyObjectPanel> | null>(null)
const relationPanelRef = ref<InstanceType<typeof OntologyRelationPanel> | null>(null)
const hasLoaded = ref(false)

const switchSection = async (key: SectionKey) => {
  activeSection.value = key
  await nextTick()
  if (key === 'objects') objectPanelRef.value?.ensureLoaded()
  else relationPanelRef.value?.ensureLoaded()
}

const ensureLoaded = async () => {
  if (hasLoaded.value) return
  hasLoaded.value = true
  await nextTick()
  objectPanelRef.value?.ensureLoaded()
}

// 对象变更（含状态迁移）后刷新关系列表（关系状态随对象级联）
const onObjectsChanged = async () => {
  if (hasLoaded.value) await relationPanelRef.value?.reload()
}

defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full min-h-0 overflow-hidden">
    <!-- 左侧小节导航 -->
    <div class="flex w-36 shrink-0 flex-col gap-1 border-r border-slate-100 bg-slate-50/60 p-2">
      <button
        v-for="section in SECTIONS"
        :key="section.key"
        type="button"
        :class="[
          'flex h-8 items-center gap-2 rounded-md px-2.5 text-xs transition-colors',
          activeSection === section.key ? 'bg-white text-blue-600 shadow-sm' : 'text-slate-600 hover:bg-white/70 hover:text-slate-800'
        ]"
        @click="switchSection(section.key)"
      >
        <i :class="['fa-solid text-[10px]', section.icon]"></i>
        <span>{{ section.label }}</span>
      </button>
    </div>

    <!-- 右侧小节内容区 -->
    <div class="min-w-0 flex-1 overflow-hidden">
      <OntologyObjectPanel v-show="activeSection === 'objects'" ref="objectPanelRef" class="h-full" @changed="onObjectsChanged" />
      <OntologyRelationPanel v-show="activeSection === 'relations'" ref="relationPanelRef" class="h-full" />
    </div>
  </div>
</template>
