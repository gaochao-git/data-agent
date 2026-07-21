<script setup lang="ts">
import { computed, ref, watch } from 'vue'
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

const paramsExpanded = ref(true)
const resultExpanded = ref(true)

watch(
  () => props.toolCall?.tool_call_id,
  () => {
    paramsExpanded.value = true
    resultExpanded.value = true
  }
)

const toolResult = computed(() => {
  const toolCall = props.toolCall
  if (!toolCall) return ''
  const fallback = toolCall.status === 'completed' ? '已完成，工具未返回文本内容。' : '执行中...'
  return props.formatText(toolCall.result || fallback)
})
</script>

<template>
  <div v-if="toolCall" class="rounded-xl border border-slate-100 bg-white shadow-sm">
    <div class="border-b border-slate-100 px-4 py-3">
      <div class="min-w-0 truncate font-mono text-xs text-slate-500" :title="toolCall.tool_name">{{ toolCall.tool_name }}</div>
    </div>

    <div class="divide-y divide-slate-100">
      <section>
        <button
          type="button"
          class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-xs font-medium text-slate-500 transition-colors hover:bg-slate-50"
          @click="paramsExpanded = !paramsExpanded"
        >
          <span>参数</span>
          <i
            class="fa-solid fa-chevron-right text-[10px] text-slate-300 transition-transform"
            :class="{ 'rotate-90': paramsExpanded }"
          ></i>
        </button>
        <div v-if="paramsExpanded" class="px-4 pb-4">
          <pre class="m-0 max-h-[40vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">{{ formatJson(toolCall.args) }}</pre>
        </div>
      </section>

      <section>
        <button
          type="button"
          class="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-xs font-medium text-slate-500 transition-colors hover:bg-slate-50"
          @click="resultExpanded = !resultExpanded"
        >
          <span>结果</span>
          <i
            class="fa-solid fa-chevron-right text-[10px] text-slate-300 transition-transform"
            :class="{ 'rotate-90': resultExpanded }"
          ></i>
        </button>
        <div v-if="resultExpanded" class="px-4 pb-4">
          <pre class="m-0 max-h-[56vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">{{ toolResult }}</pre>
        </div>
      </section>
    </div>
  </div>
  <div v-else class="rounded-lg border border-slate-100 bg-slate-50 p-4 text-sm text-slate-400">
    从消息中选择一个工具调用查看参数和结果。
  </div>
</template>
