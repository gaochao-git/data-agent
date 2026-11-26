<script setup lang="ts">
import type { PropType } from 'vue'

type ReconnectState = {
  phase: 'idle' | 'reconnecting' | 'unknown'
  attempt: number
  retryDelayMs: number
  reason: string | null
}

const props = defineProps({
  state: { type: Object as PropType<ReconnectState>, required: true },
})

const retryDelayLabel = () => `${Math.round(props.state.retryDelayMs / 1000)} 秒`
</script>

<template>
  <div
    v-if="props.state.phase !== 'idle'"
    class="rounded-lg border px-3 py-2 text-[13px]"
    :class="props.state.phase === 'unknown'
      ? 'border-amber-200 bg-amber-50 text-amber-800'
      : 'border-blue-200 bg-blue-50 text-blue-700'"
  >
    <div v-if="props.state.phase === 'reconnecting'" class="flex items-center gap-2">
      <i class="fa-solid fa-rotate fa-spin text-xs"></i>
      <span class="font-medium">连接中断，正在重连</span>
      <span v-if="props.state.attempt" class="text-xs">（{{ props.state.attempt }} 次）</span>
      <span class="ml-auto text-xs">重试延迟：{{ retryDelayLabel() }}</span>
    </div>
    <div v-else class="flex items-center gap-2">
      <i class="fa-solid fa-circle-exclamation text-xs"></i>
      <span class="font-medium">无法确认任务状态，任务可能仍在执行</span>
      <span class="ml-auto text-xs">TRANSPORT</span>
    </div>
  </div>
</template>
