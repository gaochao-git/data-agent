<script setup lang="ts">
/**
 * 子智能体会话详情：与主对话消息列表同构，并按轮次连续展示多轮问答。
 *
 * 每一轮任务书是 user 气泡，子智能体过程 + 最终报告是 assistant 气泡；工作过程折叠、
 * 思考块、内层工具卡、查询结果全部复用 MessageItem。状态/类型/上下文用量放在
 * 右侧面板 subheader，详情区不再单独铺一张状态卡，也不再重复渲染最终报告。
 */
import { computed, onMounted, watch } from 'vue'
import type { PropType } from 'vue'
import type { ChatDataTokenUsage, SubagentTaskRecord, SubagentTokenUsage, ToolCallInfo } from '../../types'
import { isSubagentTaskActive } from '../../composables/useSubagentTasks'
import { useSubagentTaskStream } from '../../composables/useSubagentTaskStream'
import { useToolTiming } from '../../composables/useToolTiming'
import SubagentTrail from './SubagentTrail.vue'
import ToolCallDetailPanel from './ToolCallDetailPanel.vue'

const props = defineProps({
  task: { type: Object as PropType<SubagentTaskRecord>, required: true },
  /** 同一子会话的全部轮次，按轮次升序；缺省时只展示当前任务。 */
  sessionTasks: { type: Array as PropType<SubagentTaskRecord[]>, default: () => [] },
  conversationId: { type: String as PropType<string | null>, default: null },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
  /** agent=按多轮聊天过程展示；generic=按工具调用展示每轮请求参数与结果。 */
  viewMode: { type: String as PropType<'agent' | 'generic'>, default: 'agent' },
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
  usageUpdated: [taskId: string, usage: ChatDataTokenUsage | null]
}>()

const active = computed(() => isSubagentTaskActive(props.task))
const sessionTasks = computed(() => (props.sessionTasks.length ? props.sessionTasks : [props.task]))
const startedAtMs = (task: SubagentTaskRecord): number | null => {
  const value = Date.parse(task.started_at || task.created_at || '')
  return Number.isFinite(value) ? value : null
}
const endedAtMs = (task: SubagentTaskRecord): number | null => {
  if (isSubagentTaskActive(task)) return null
  const value = Date.parse(task.finished_at || task.updated_at || '')
  return Number.isFinite(value) ? value : null
}
const startedAt = computed(() => startedAtMs(props.task))
const endedAt = computed(() => endedAtMs(props.task))
const { elapsedMs, isRunning } = useToolTiming(() => startedAt.value, () => endedAt.value)

const stream = useSubagentTaskStream(
  () => props.task.task_id,
  () => props.conversationId,
  () => active.value,
)

const isCurrentTask = (task: SubagentTaskRecord): boolean => task.task_id === props.task.task_id
const trailForTask = (task: SubagentTaskRecord) => {
  const persisted = task.result_meta?.subagent_trail || []
  if (isCurrentTask(task) && active.value && stream.trail.value.length) return stream.trail.value
  return persisted.length ? persisted : (isCurrentTask(task) ? stream.trail.value : [])
}
const compactionsForTask = (task: SubagentTaskRecord) => {
  const persisted = task.result_meta?.subagent_compactions || []
  if (isCurrentTask(task) && active.value && stream.compactions.value.length) return stream.compactions.value
  return persisted.length ? persisted : (isCurrentTask(task) ? stream.compactions.value : [])
}
const contextUsageForTask = (task: SubagentTaskRecord) => {
  const persisted = task.result_meta?.subagent_context_usage || task.progress?.context_usage
  if (isCurrentTask(task) && active.value && stream.contextUsage.value) return stream.contextUsage.value
  return persisted || (isCurrentTask(task) ? stream.contextUsage.value : null)
}
const tokenUsageDetailForTask = (task: SubagentTaskRecord) => (
  isCurrentTask(task)
    ? (stream.tokenUsageDetail.value || task.result_meta?.subagent_token_usage_detail)
    : task.result_meta?.subagent_token_usage_detail
)
const tokenUsageForTask = (task: SubagentTaskRecord): SubagentTokenUsage | null => (
  task.result_meta?.subagent_token_usage || null
)
const elapsedForTask = (task: SubagentTaskRecord): number | null => {
  if (isCurrentTask(task)) return elapsedMs.value
  const started = startedAtMs(task)
  const ended = endedAtMs(task)
  return started !== null && ended !== null ? Math.max(ended - started, 0) : null
}
const hasConversationForTask = (task: SubagentTaskRecord): boolean => Boolean(
  task.description
  || task.report
  || task.error
  || trailForTask(task).length
  || isSubagentTaskActive(task),
)

const usageForPanel = computed<ChatDataTokenUsage | null>(() => {
  const detail = tokenUsageDetailForTask(props.task)
  if (detail) return detail
  const snapshot = contextUsageForTask(props.task)
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
const taskResultText = (task: SubagentTaskRecord): string => {
  const report = String(task.report || '').trim()
  const error = String(task.error || '').trim()
  if (report && error && task.status !== 'completed') return `${report}\n\n错误：${error}`
  if (report) return report
  if (error) return `错误：${error}`
  if (isSubagentTaskActive(task)) return ''
  return `任务${task.status_label?.trim() || task.status}，无结果内容。`
}
const toolCallForTask = (task: SubagentTaskRecord): ToolCallInfo => ({
  tool_call_id: task.parent_tool_call_id || task.task_id,
  tool_name: 'subagent',
  status: isSubagentTaskActive(task) ? 'running' : task.status === 'completed' ? 'completed' : 'error',
  args: {
    subagent_type: task.subagent_type,
    description: task.description,
    background: task.execution_mode !== 'sync',
    label: task.label,
  },
  result: taskResultText(task),
  started_at_ms: startedAtMs(task),
  ended_at_ms: endedAtMs(task),
  subagentTrail: trailForTask(task),
  subagentError: task.error || '',
  subagentContextUsage: contextUsageForTask(task) || undefined,
  subagentTokenUsage: tokenUsageForTask(task) || undefined,
  subagentCompactions: compactionsForTask(task),
  subagentTokenUsageDetail: tokenUsageDetailForTask(task),
})
const hasConversation = computed(() => sessionTasks.value.some(hasConversationForTask))

// 顶部副标题只拿得到任务账本快照，运行中的实时占用要从过程流回传；
// 否则会出现「执行中时看不到上下文占用，任务结束才出现」。
watch(usageForPanel, usage => emit('usageUpdated', props.task.task_id, usage))
onMounted(() => emit('usageUpdated', props.task.task_id, usageForPanel.value))
</script>

<template>
  <div class="flex flex-col gap-3" data-subagent-task-detail>
    <template v-if="hasConversation">
      <div
        v-for="(turn, index) in sessionTasks"
        :key="turn.task_id"
        class="flex flex-col gap-3"
        data-subagent-turn
      >
        <SubagentTrail
          v-if="viewMode === 'agent'"
          :tool-call="toolCallForTask(turn)"
          :conversation-id="conversationId"
          :active-tool-call-id="activeToolCallId"
          :elapsed-ms="elapsedForTask(turn)"
          :timing-running="isRunning && turn.task_id === task.task_id"
          :token-usage="tokenUsageForTask(turn)"
          show-without-trail
          @select-tool="emit('selectTool', $event)"
        />
        <div v-else class="flex flex-col gap-2" data-subagent-task-tool-detail>
          <div v-if="sessionTasks.length > 1" class="px-1 text-[11px] font-medium text-slate-400">
            第 {{ turn.turn_index || index + 1 }} 轮
          </div>
          <ToolCallDetailPanel
            :tool-call="toolCallForTask(turn)"
            :conversation-id="conversationId || ''"
          />
        </div>
      </div>
    </template>
    <div v-else class="flex items-center gap-2 rounded-xl border border-dashed border-slate-200 px-3.5 py-4 text-[12px] text-slate-500">
      <i class="fa-regular fa-message text-slate-400"></i>
      <span>暂无过程记录</span>
    </div>
  </div>
</template>
