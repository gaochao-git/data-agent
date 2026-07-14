<script setup lang="ts">
import { computed } from 'vue'
import type { ToolCallInfo } from '../types'

interface Props {
  toolCall?: ToolCallInfo | null
  formatText?: (value: string) => string
}

const props = withDefaults(defineProps<Props>(), {
  toolCall: null,
  formatText: (value: string) => value,
})

const formatJson = (value: unknown) => props.formatText(JSON.stringify(value || {}, null, 2))

const toolResult = computed(() => {
  const toolCall = props.toolCall
  if (!toolCall) return ''
  const fallback = toolCall.status === 'completed' ? '已完成，工具未返回文本内容。' : '执行中...'
  return props.formatText(toolCall.result || fallback)
})
</script>

<template>
  <div v-if="toolCall" class="space-y-3">
    <div class="rounded-lg border border-slate-100 bg-slate-50 p-3">
      <div class="mb-2 flex items-center justify-between gap-2">
        <div class="text-xs font-medium text-slate-500">参数</div>
        <div class="truncate font-mono text-[10px] text-slate-400">{{ toolCall.tool_name }}</div>
      </div>
      <pre class="m-0 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">{{ formatJson(toolCall.args) }}</pre>
    </div>
    <div class="rounded-lg border border-slate-100 bg-slate-50 p-3">
      <div class="mb-2 text-xs font-medium text-slate-500">结果</div>
      <pre class="m-0 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">{{ toolResult }}</pre>
    </div>
  </div>
  <div v-else class="rounded-lg border border-slate-100 bg-slate-50 p-4 text-sm text-slate-400">
    从消息中选择一个工具调用查看参数和结果。
  </div>
</template>
