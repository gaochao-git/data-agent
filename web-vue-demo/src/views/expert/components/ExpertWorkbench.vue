<!--
  工作台容器组件
  迁移自: web/src/pages/GeneralAssistant/components/unified/workbench/ExpertWorkbench.tsx

  功能：
  - 管理"法规依据"和"调试"两个面板
  - 支持面板切换和关闭
-->

<script setup lang="ts">
import { computed } from 'vue'
import PolicyPreviewPanel from './PolicyPreviewPanel.vue'
import TracePanel from './TracePanel.vue'
import type { ParsedCitation } from '../types'

// Props
interface Props {
  activePanelId: string | null
  citation: ParsedCitation | null
  conversationId?: string
  traceEnabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  activePanelId: null,
  citation: null,
  conversationId: '',
  traceEnabled: false,
})

// Emits
const emit = defineEmits<{
  (e: 'update:activePanelId', value: string | null): void
  (e: 'close'): void
}>()

// Computed
const panels = computed(() => {
  const basePanels = [{ id: 'reference', label: '法规依据' }]
  if (props.traceEnabled) {
    basePanels.push({ id: 'trace', label: '调试' })
  }
  return basePanels
})

const currentId = computed(() => {
  return props.activePanelId || panels.value[0].id
})

// Methods
const handlePanelChange = (panelId: string) => {
  emit('update:activePanelId', panelId)
}

const handleClose = () => {
  emit('close')
}
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 面板切换头部 -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2 text-[14px] text-[#8592A6]">
        <button
          v-for="panel in panels"
          :key="panel.id"
          type="button"
          @click="handlePanelChange(panel.id)"
          :class="panel.id === currentId
            ? 'rounded-full bg-[#EAF2F6] px-3 py-1 text-[20px] font-[500] leading-[28px] text-[#007EA7]'
            : 'px-2 text-[14px] text-[#8592A6]'
          "
        >
          {{ panel.label }}
        </button>
      </div>
      <button
        type="button"
        class="flex h-8 w-8 cursor-pointer items-center justify-center rounded-full bg-[#F3F5F7] text-[14px] font-semibold text-[#5B6B7C] transition hover:bg-[#E5EAF0] hover:text-[#1F3A56]"
        @click="handleClose"
      >
        ✕
      </button>
    </div>

    <!-- 面板内容 -->
    <div class="mt-4 flex-1 overflow-hidden rounded-[16px] bg-[#F7F9FA]">
      <TracePanel
        v-if="currentId === 'trace' && traceEnabled"
        :conversation-id="conversationId ?? null"
      />
      <PolicyPreviewPanel
        v-else
        :citation="citation"
      />
    </div>
  </div>
</template>
