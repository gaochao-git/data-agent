<script setup lang="ts">
/**
 * 后台任务详情：与主对话消息列表同构。
 *
 * 任务书是 user 气泡，子智能体过程 + 最终报告是 assistant 气泡；工作过程折叠、
 * 思考块、内层工具卡、查询结果全部复用 MessageItem。状态/类型/上下文用量放在
 * 右侧面板 subheader，详情区不再单独铺一张状态卡，也不再重复渲染最终报告。
 */
import { computed, onMounted, watch } from 'vue'
import type { PropType } from 'vue'
import type { ChatDataTokenUsage, SubagentTaskRecord, ToolCallInfo } from '../../types'
import { isSubagentTaskActive } from '../../composables/useSubagentTasks'
import { useSubagentTaskStream } from '../../composables/useSubagentTaskStream'
import { useToolTiming } from '../../composables/useToolTiming'
import SubagentTrail from './SubagentTrail.vue'

const props = defineProps({
  task: { type: Object as PropType<SubagentTaskRecord>, required: true },
  conversationId: { type: String as PropType<string | null>, default: null },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
  usageUpdated: [taskId: string, usage: ChatDataTokenUsage | null]
}>()

const active = computed(() => isSubagentTaskActive(props.task))
const startedAt = computed(() => {
  const value = Date.parse(props.task.started_at || props.task.created_at || '')
  return Number.isFinite(value) ? value : null
})
const endedAt = computed(() => {
  if (active.value) return null
  const value = Date.parse(props.task.finished_at || props.task.updated_at || '')
  return Number.isFinite(value) ? value : null
})
const { elapsedMs, isRunning } = useToolTiming(() => startedAt.value, () => endedAt.value)

const resultMeta = computed(() => props.task.result_meta || {})
const persistedTrail = computed(() => resultMeta.value.subagent_trail || [])
const stream = useSubagentTaskStream(
  () => props.task.task_id,
  () => props.conversationId,
  () => active.value,
)
const trail = computed(() => {
  if (active.value && stream.trail.value.length) return stream.trail.value
  return persistedTrail.value.length ? persistedTrail.value : stream.trail.value
})
const compactions = computed(() => (
  active.value && stream.compactions.value.length
    ? stream.compactions.value
    : (resultMeta.value.subagent_compactions || stream.compactions.value)
))
const contextUsage = computed(() => (
  active.value && stream.contextUsage.value
    ? stream.contextUsage.value
    : (resultMeta.value.subagent_context_usage || stream.contextUsage.value)
))
const tokenUsageDetail = computed(() => (
  stream.tokenUsageDetail.value || resultMeta.value.subagent_token_usage_detail
))
const tokenUsage = computed(() => resultMeta.value.subagent_token_usage || null)
const usageForPanel = computed<ChatDataTokenUsage | null>(() => {
  if (tokenUsageDetail.value) return tokenUsageDetail.value
  const snapshot = contextUsage.value
  if (!snapshot) return null
  return {
    used: snapshot.used_tokens,
    total: snapshot.context_window ?? snapshot.threshold_tokens,
    percentage: snapshot.percent,
    current_input_tokens: snapshot.used_tokens,
    compression_trigger_tokens: snapshot.threshold_tokens,
    context_window: snapshot.context_window,
    estimated: true,
  }
})
const hasConversation = computed(() => Boolean(
  props.task.description
  || props.task.report
  || props.task.error
  || trail.value.length
  || active.value,
))

const toolCall = computed<ToolCallInfo>(() => ({
  tool_call_id: props.task.parent_tool_call_id || props.task.task_id,
  tool_name: 'task',
  status: active.value ? 'running' : props.task.status === 'completed' ? 'completed' : 'error',
  args: {
    subagent_type: props.task.subagent_type,
    description: props.task.description,
    background: true,
    label: props.task.label,
  },
  result: props.task.report || '',
  started_at_ms: startedAt.value,
  ended_at_ms: endedAt.value,
  subagentTrail: trail.value,
  subagentError: props.task.error || '',
  subagentContextUsage: contextUsage.value || undefined,
  subagentTokenUsage: tokenUsage.value || undefined,
  subagentCompactions: compactions.value.length ? compactions.value : undefined,
  subagentTokenUsageDetail: tokenUsageDetail.value,
}))

// 顶部副标题只拿得到任务账本快照，运行中的实时占用要从过程流回传；
// 否则会出现「执行中时看不到上下文占用，任务结束才出现」。
watch(usageForPanel, usage => emit('usageUpdated', props.task.task_id, usage))
onMounted(() => emit('usageUpdated', props.task.task_id, usageForPanel.value))
</script>

<template>
  <div class="flex flex-col gap-3" data-subagent-task-detail>
    <SubagentTrail
      v-if="hasConversation"
      :tool-call="toolCall"
      :conversation-id="conversationId"
      :active-tool-call-id="activeToolCallId"
      :elapsed-ms="elapsedMs"
      :timing-running="isRunning"
      :token-usage="tokenUsage"
      show-without-trail
      @select-tool="emit('selectTool', $event)"
    />
    <div v-else class="flex items-center gap-2 rounded-xl border border-dashed border-slate-200 px-3.5 py-4 text-[12px] text-slate-500">
      <i class="fa-regular fa-message text-slate-400"></i>
      <span>暂无过程记录</span>
    </div>
  </div>
</template>
