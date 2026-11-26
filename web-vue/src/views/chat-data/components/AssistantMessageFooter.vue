<script setup lang="ts">
import AiGeneratedNotice from './AiGeneratedNotice.vue'

interface Props {
  timestamp?: string
  align?: 'between' | 'end'
  /** 子智能体过程不显示「本回答由 AI 生成」声明，其余底栏结构与主对话共用 */
  showAiNotice?: boolean
}

withDefaults(defineProps<Props>(), {
  timestamp: '',
  align: 'between',
  showAiNotice: true,
})
</script>

<template>
  <div
    :class="[
      'flex items-center gap-2 px-1',
      align === 'end' ? 'justify-end' : 'justify-between'
    ]"
    data-message-footer
  >
    <div class="flex items-center gap-2">
      <slot />
    </div>
    <div class="ml-auto flex items-center gap-3">
      <AiGeneratedNotice v-if="showAiNotice" />
      <span v-if="timestamp" class="whitespace-nowrap text-[11px] text-gray-400" data-message-timestamp>
        {{ timestamp }}
      </span>
    </div>
  </div>
</template>
