<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PropType } from 'vue'
import type { ToolCallInfo } from '../../types'
import { getToolCompactDescription, getToolDisplayName, getDataAnalyzingActionName, formatToolDuration, formatToolTimingText, parseFileDiffResult, isFileDiffOffloaded } from '../../utils/toolDisplay'
import FileDiffViewer from '../FileDiffViewer.vue'

// 注意：Props 用运行时声明 + PropType（不用外部类型做 defineProps<T> 泛型），
// 见 AGENTS.md 4.3 —— chat-data 会被嵌入未安装 typescript 的宿主项目
const props = defineProps({
  toolCall: { type: Object as PropType<ToolCallInfo | null>, default: null },
  formatText: { type: Function as PropType<(value: string) => string>, default: (value: string) => value },
  conversationId: { type: String, default: '' },
})

const formatJson = (value: unknown) => props.formatText(JSON.stringify(value || {}, null, 2))

// 标题链：后端下发展示名（MCP tool_labels）优先，内置映射/限定名兜底，与 ToolCallCard 口径一致
const toolDisplayName = computed(() => props.toolCall?.display_name?.trim() || getToolDisplayName(props.toolCall?.tool_name))
// 数据分析合并工具在标题露出动作中文名（如「语义模型自检」），只看卡片名分不清具体动作
const toolActionName = computed(() => {
  const toolCall = props.toolCall
  if (!toolCall || toolCall.tool_name !== 'data_analyzing_tool') return ''
  const action = (toolCall.args as Record<string, unknown> | undefined)?.action
  return getDataAnalyzingActionName(typeof action === 'string' ? action : null)
})
const toolDescription = computed(() => {
  const toolCall = props.toolCall
  return toolCall ? getToolCompactDescription(toolCall.tool_name, toolCall.args || {}) : ''
})

const paramsExpanded = ref(true)
const resultExpanded = ref(true)

watch(
  () => props.toolCall?.tool_call_id,
  () => {
    paramsExpanded.value = true
    resultExpanded.value = true
  }
)

// file_diff：「查看 Diff」按钮入口；对比视图在 FileDiffViewer 弹层内，结果区保持通用展示
const fileDiffPayload = computed(() =>
  parseFileDiffResult(props.toolCall?.tool_name, props.toolCall?.status, props.toolCall?.result),
)
const fileDiffOffloaded = computed(() =>
  isFileDiffOffloaded(props.toolCall?.tool_name, props.toolCall?.status, props.toolCall?.result),
)
const diffViewerVisible = ref(false)

const toolResult = computed(() => {
  const toolCall = props.toolCall
  if (!toolCall) return ''
  const fallback = toolCall.status === 'completed' ? '已完成，工具未返回文本内容。' : '执行中...'
  return props.formatText(toolCall.result || fallback)
})

// 执行信息：状态 + 起止时间/耗时（数据来自 ToolTimingMiddleware 盖章，历史与直播同源）
const STATUS_META: Record<ToolCallInfo['status'], { label: string; className: string }> = {
  generating: { label: '执行中', className: 'text-[#1f82d9]' },
  running: { label: '执行中', className: 'text-[#1f82d9]' },
  completed: { label: '已完成', className: 'text-emerald-600' },
  error: { label: '执行失败', className: 'text-red-500' },
}
const statusMeta = computed(() => STATUS_META[props.toolCall?.status || 'running'])
const durationText = computed(() => formatToolDuration(props.toolCall?.started_at_ms, props.toolCall?.ended_at_ms))
const timingText = computed(() => formatToolTimingText(props.toolCall?.started_at_ms, props.toolCall?.ended_at_ms))
const hasTiming = computed(() => Boolean(props.toolCall?.started_at_ms || props.toolCall?.ended_at_ms))
</script>

<template>
  <div v-if="toolCall" class="rounded-xl border border-slate-100 bg-white shadow-sm">
    <div class="border-b border-slate-100 px-4 py-3">
      <div class="flex min-w-0 items-center gap-2 text-xs text-slate-500" :title="toolDescription || toolCall.tool_name">
        <span class="shrink-0 font-medium">{{ toolActionName ? `${toolDisplayName} · ${toolActionName}` : toolDisplayName }}</span>
        <span v-if="toolDescription" class="min-w-0 truncate font-mono text-slate-400">{{ toolDescription }}</span>
      </div>
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
          <span class="flex items-center gap-2">
            <span>结果</span>
            <!-- file_diff：独立按钮打开对比查看器，不改动结果的通用展示 -->
            <span
              v-if="fileDiffPayload"
              class="flex items-center gap-1 text-[#1f82d9] transition-colors hover:text-[#1668b3]"
              role="button"
              @click.stop="diffViewerVisible = true"
            >
              <i class="fa-solid fa-table-columns text-[10px]"></i>
              <span>查看 Diff</span>
            </span>
          </span>
          <i
            class="fa-solid fa-chevron-right text-[10px] text-slate-300 transition-transform"
            :class="{ 'rotate-90': resultExpanded }"
          ></i>
        </button>
        <div v-if="resultExpanded" class="px-4 pb-4">
          <!-- file_diff 结果过大被卸载时的友好提示（此时无法解析 diff） -->
          <div v-if="fileDiffOffloaded" class="mb-2 rounded bg-amber-50 px-2 py-1.5 text-xs text-amber-600">
            对比结果过大已保存到工作区；可让智能体用 file_read 分段查看 diff 明细。
          </div>
          <pre class="m-0 max-h-[56vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-700">{{ toolResult }}</pre>
        </div>
      </section>

      <section class="px-4 py-3">
        <div class="text-xs font-medium text-slate-500">执行信息</div>
        <div class="mt-2 flex flex-col gap-1.5">
          <div class="flex items-center gap-2 text-xs">
            <span class="w-14 shrink-0 text-slate-400">状态</span>
            <span :class="statusMeta.className">{{ statusMeta.label }}</span>
          </div>
          <template v-if="hasTiming">
            <div v-if="durationText" class="flex items-center gap-2 text-xs">
              <span class="w-14 shrink-0 text-slate-400">耗时</span>
              <span class="font-mono text-slate-700">{{ durationText }}</span>
            </div>
            <div class="flex items-start gap-2 text-xs">
              <span class="w-14 shrink-0 pt-0.5 text-slate-400">时间</span>
              <span class="min-w-0 break-all font-mono text-slate-700">{{ timingText }}</span>
            </div>
          </template>
        </div>
      </section>
    </div>
  </div>
  <div v-else class="rounded-lg border border-slate-100 bg-slate-50 p-4 text-sm text-slate-400">
    从消息中选择一个工具调用查看参数和结果。
  </div>

  <!-- file_diff 对比查看器弹层 -->
  <FileDiffViewer
    v-model:visible="diffViewerVisible"
    :payload="fileDiffPayload"
    :conversation-id="conversationId"
  />
</template>
