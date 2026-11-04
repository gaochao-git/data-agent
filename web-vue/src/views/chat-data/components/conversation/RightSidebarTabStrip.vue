<script setup lang="ts">
/** 右侧栏 tab 条：只负责展示与事件，不持有状态（状态在 useRightSidebarTabs）。 */
import type { PropType } from 'vue'
import type { RightSidebarTab } from '../../composables/useRightSidebarTabs'

const props = defineProps({
  tabs: { type: Array as PropType<RightSidebarTab[]>, required: true },
  activeTabId: { type: String, required: true },
})

const emit = defineEmits<{
  select: [tabId: string]
  close: [tabId: string]
}>()

const tabIcon = (tab: RightSidebarTab): string => {
  if (tab.kind === 'workspace') return 'fa-folder'
  if (tab.kind === 'file') return 'fa-file-lines'
  if (tab.kind === 'agentProcess') return 'fa-robot'
  return 'fa-wrench'
}
</script>

<template>
  <div class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto" data-panel-tabs>
    <div
      v-for="tab in props.tabs"
      :key="tab.id"
      class="group flex h-7 min-w-0 max-w-[190px] shrink-0 items-center gap-1 rounded-md border pl-2 pr-1 text-xs transition-colors"
      :class="tab.id === props.activeTabId
        ? 'border-transparent bg-slate-100 font-medium text-slate-700'
        : 'border-transparent text-slate-500 hover:bg-slate-50'"
    >
      <button
        type="button"
        class="flex min-w-0 items-center gap-1.5"
        :title="tab.title"
        @click="emit('select', tab.id)"
      >
        <i class="fa-solid shrink-0 text-[10px]" :class="tabIcon(tab)"></i>
        <span class="truncate">{{ tab.title }}</span>
      </button>
      <button
        v-if="tab.kind !== 'workspace'"
        type="button"
        class="flex size-4 shrink-0 items-center justify-center rounded text-slate-400 opacity-60 transition-opacity hover:bg-slate-200 hover:text-slate-700 group-hover:opacity-100"
        title="关闭"
        @click.stop="emit('close', tab.id)"
      >
        <i class="fa-solid fa-xmark text-[9px]"></i>
      </button>
    </div>
  </div>
</template>
