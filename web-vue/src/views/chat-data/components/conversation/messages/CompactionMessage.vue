<script setup lang="ts">
/**
 * 上下文压缩时间线标记：无气泡边框，居中一行；tooltip 展示压缩前后 token。
 */
import type { PropType } from 'vue'
import { ElTooltip } from 'element-plus'
import type { Message } from '../../../types'
import { formatCompressionTokens } from '../../../utils/messageView'

defineProps({
  message: { type: Object as PropType<Message>, required: true },
})
</script>

<template>
  <ElTooltip v-if="message.contextCompression?.compressing" placement="top" effect="light">
    <template #content>
      <div class="text-xs leading-5">正在压缩历史上下文，稍候继续执行</div>
    </template>
    <div class="flex cursor-default items-center gap-3 py-1 text-xs text-slate-400">
      <span class="h-px flex-1 bg-slate-200"></span>
      <span class="flex items-center gap-1.5 whitespace-nowrap">
        <i class="fa-solid fa-circle-notch fa-spin"></i>
        上下文压缩中
      </span>
      <span class="h-px flex-1 bg-slate-200"></span>
    </div>
  </ElTooltip>
  <ElTooltip v-else placement="top" effect="light">
    <template #content>
      <div class="space-y-1 text-xs leading-5">
        <div>压缩前 {{ formatCompressionTokens(message.contextCompression?.estimated_tokens_before) }} tokens</div>
        <div>压缩后 {{ formatCompressionTokens(message.contextCompression?.estimated_tokens_after) }} tokens</div>
        <div>释放 {{ formatCompressionTokens(message.contextCompression?.estimated_cleared_tokens) }} tokens，压缩 {{ formatCompressionTokens(message.contextCompression?.compressed_messages) }} 条消息</div>
      </div>
    </template>
    <div class="flex cursor-default items-center gap-3 py-1 text-xs text-slate-400">
      <span class="h-px flex-1 bg-slate-200"></span>
      <span class="whitespace-nowrap">上下文已压缩</span>
      <span class="h-px flex-1 bg-slate-200"></span>
    </div>
  </ElTooltip>
</template>
