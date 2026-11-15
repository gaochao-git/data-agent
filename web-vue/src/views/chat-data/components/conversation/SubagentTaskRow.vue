<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { SubagentTaskRecord } from '../../types'
import { isSubagentTaskActive } from '../../composables/useSubagentTasks'
import { useToolTiming } from '../../composables/useToolTiming'
import { formatDateTimeValue } from '../../utils'
import { formatTokenCount } from '../../utils/subagentUsage'

const props = defineProps({
  task: { type: Object as PropType<SubagentTaskRecord>, required: true },
  sessionTurnCount: { type: Number, default: 1 },
  /** 同一子会话的全部轮次，用于汇总工具调用次数与首末时间。 */
  sessionTasks: { type: Array as PropType<SubagentTaskRecord[]>, default: () => [] },
  cancelling: { type: Boolean, default: false },
  pausing: { type: Boolean, default: false },
})

const emit = defineEmits<{
  open: [task: SubagentTaskRecord]
  cancel: [task: SubagentTaskRecord]
  pause: [task: SubagentTaskRecord]
}>()

const active = computed(() => isSubagentTaskActive(props.task))
// 暂停依赖子图控制中间件，仅后台任务可用；同步任务随主 Run 执行，不提供独立暂停。
const canPause = computed(() => (
  props.task.execution_mode !== 'sync'
  && (props.task.status === 'running' || props.task.status === 'pending')
))
const startedAt = computed(() => {
  const value = Date.parse(props.task.started_at || props.task.created_at || '')
  return Number.isFinite(value) ? value : null
})
const endedAt = computed(() => {
  if (active.value) return null
  const value = Date.parse(props.task.finished_at || props.task.updated_at || '')
  return Number.isFinite(value) ? value : null
})
const { durationText } = useToolTiming(() => startedAt.value, () => endedAt.value)

const taskTitle = computed(() => props.task.label?.trim() || props.task.description?.trim() || '子智能体任务')
const sessionTimeline = computed(() => (props.sessionTasks.length ? props.sessionTasks : [props.task]))
const sessionToolCallCount = computed(() => sessionTimeline.value.reduce(
  (total, item) => total + Math.max(Number(item.progress?.tool_calls) || 0, 0),
  0,
))
const sessionCreatedAt = computed(() => {
  const first = sessionTimeline.value[0]
  return formatDateTimeValue(first?.created_at || first?.started_at)
})
const sessionFinishedAt = computed(() => {
  const latest = sessionTimeline.value[sessionTimeline.value.length - 1]
  if (!latest || isSubagentTaskActive(latest)) return ''
  return formatDateTimeValue(latest.finished_at || latest.updated_at)
})
const metaTime = (value: string): string => value.slice(0, 16)
const formatContextTokens = (value: number): string => formatTokenCount(value).replace(/\.0([km])$/, '$1')
const contextUsage = computed(() => {
  const latest = sessionTimeline.value[sessionTimeline.value.length - 1]
  const detail = latest?.result_meta?.subagent_token_usage_detail
  if (detail && Number.isFinite(detail.used)) {
    const total = Number(detail.total) || 0
    return {
      used: Number(detail.current_input_tokens ?? detail.input_tokens ?? detail.used),
      total,
      percentage: Number.isFinite(detail.percentage)
        ? Number(detail.percentage)
        : total > 0 ? (detail.used / total) * 100 : null,
      compressionTriggerTokens: Number(detail.compression_trigger_tokens),
      contextWindow: Number(detail.context_window),
    }
  }
  const snapshot = latest?.result_meta?.subagent_context_usage
  if (!snapshot || !Number.isFinite(snapshot.used_tokens)) return null
  const total = Number(snapshot.context_window || snapshot.threshold_tokens) || 0
  return {
    used: snapshot.used_tokens,
    total,
    percentage: Number.isFinite(snapshot.percent)
      ? Number(snapshot.percent)
      : total > 0 ? (snapshot.used_tokens / total) * 100 : null,
    compressionTriggerTokens: Number(snapshot.threshold_tokens),
    contextWindow: Number(snapshot.context_window),
  }
})
const contextUsageText = computed(() => {
  const usage = contextUsage.value
  if (!usage || !Number.isFinite(usage.percentage)) return '—'
  return `${Math.round(Math.max(0, Math.min(100, Number(usage.percentage))))}%`
})
const contextUsageTitle = computed(() => {
  const usage = contextUsage.value
  if (!usage) return '上下文用量加载中'
  return [
    ['当前输入', usage.used],
    ['压缩阈值', usage.compressionTriggerTokens],
    ['上下文窗口', usage.contextWindow],
  ]
    .filter(([, value]) => Number.isFinite(value))
    .map(([label, value]) => `${label} ${formatContextTokens(value as number)}`)
    .join(' · ') || '上下文用量加载中'
})

const statusLabel = computed(() => {
  const label = props.task.status_label?.trim()
  if (label) return label
  switch (props.task.status) {
    case 'pending': return '等待中'
    case 'running': return '执行中'
    case 'cancelling': return '取消中'
    case 'paused': return '已暂停'
    case 'waiting_approval': return '等待审批'
    case 'completed': return '已完成'
    case 'cancelled': return '已取消'
    case 'timeout': return '已超时'
    case 'lost': return '已失联'
    case 'interrupted': return '已中断'
    default: return '执行失败'
  }
})

const statusTextTone = computed(() => {
  switch (props.task.status) {
    case 'completed': return 'text-emerald-600'
    case 'failed':
    case 'lost': return 'text-rose-600'
    case 'timeout':
    case 'cancelled':
    case 'interrupted': return 'text-amber-600'
    case 'waiting_approval': return 'text-amber-600'
    case 'paused': return 'text-slate-500'
    default: return 'text-sky-600'
  }
})

const accentLineTone = computed(() => {
  switch (props.task.status) {
    case 'completed': return 'from-emerald-400 via-emerald-300/70 to-transparent'
    case 'failed':
    case 'lost': return 'from-rose-400 via-rose-300/70 to-transparent'
    case 'timeout':
    case 'cancelled':
    case 'interrupted': return 'from-amber-400 via-amber-300/70 to-transparent'
    case 'waiting_approval': return 'from-amber-400 via-amber-300/70 to-transparent'
    case 'paused': return 'from-slate-400 via-slate-300/70 to-transparent'
    default: return 'from-sky-400 via-indigo-300/70 to-transparent'
  }
})

const progressText = computed(() => {
  const phase = String(props.task.progress?.phase || '').trim()
  const lastTool = String(props.task.progress?.last_tool || '').trim()
  if (lastTool) return `正在使用 ${lastTool}`
  if (phase === 'starting') return '正在初始化任务上下文'
  return '正在分析任务并规划执行步骤'
})
</script>

<template>
  <div
    class="group relative flex h-full cursor-pointer flex-col overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all duration-200 hover:-translate-y-0.5 hover:border-sky-200 hover:shadow-[0_12px_28px_rgba(15,23,42,0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-300/70 focus-visible:ring-offset-2"
    data-subagent-task-card
    role="button"
    tabindex="0"
    @click="emit('open', task)"
    @keydown.enter.prevent="emit('open', task)"
  >
    <span class="absolute inset-x-0 top-0 h-px bg-gradient-to-r" :class="accentLineTone"></span>

    <div class="flex flex-1 flex-col p-4">
      <div class="flex items-start justify-between gap-3">
        <h3
          class="m-0 line-clamp-2 min-w-0 flex-1 text-[14px] font-semibold leading-5 text-slate-800"
          data-subagent-task-title
        >
          <span class="text-[14px] font-bold text-sky-600">{{ task.subagent_type }}</span>
          <span class="mx-1 font-normal text-slate-300">-</span>
          <span>{{ taskTitle }}</span>
        </h3>

        <div class="flex shrink-0 items-center gap-0.5">
          <button
            v-if="canPause"
            type="button"
            class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-300 opacity-70 transition-all hover:bg-amber-50 hover:text-amber-500 group-hover:opacity-100"
            :disabled="pausing"
            data-subagent-task-pause
            title="暂停子智能体任务"
            @click.stop="emit('pause', task)"
          >
            <i :class="pausing ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-pause'" class="text-[11px]"></i>
          </button>
          <button
            v-if="active"
            type="button"
            class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-300 opacity-70 transition-all hover:bg-rose-50 hover:text-rose-500 group-hover:opacity-100"
            :disabled="cancelling"
            data-subagent-task-cancel
            title="取消子智能体任务"
            @click.stop="emit('cancel', task)"
          >
            <i :class="cancelling ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-xmark'" class="text-[11px]"></i>
          </button>
        </div>
      </div>

      <div class="mt-2.5 flex flex-col gap-1 text-[10.5px] text-slate-400" data-subagent-task-meta>
        <div class="flex min-w-0 items-center gap-x-3" data-subagent-task-meta-row>
          <span class="inline-flex shrink-0 items-center gap-1" data-subagent-task-rounds>
            <i class="fa-regular fa-comments text-[9px]"></i>
            {{ sessionTurnCount }} 轮问答
          </span>
          <span class="inline-flex shrink-0 items-center gap-1" data-subagent-task-tool-count>
            <i class="fa-solid fa-screwdriver-wrench text-[9px]"></i>
            {{ sessionToolCallCount }} 次工具调用
          </span>
          <span class="inline-flex min-w-0 items-center gap-1" :title="contextUsageTitle" data-subagent-task-context>
            <i class="fa-solid fa-chart-pie text-[9px]"></i>
            <span class="truncate">上下文 {{ contextUsageText }}</span>
          </span>
        </div>
        <div class="flex min-w-0 items-center gap-x-3" data-subagent-task-meta-row>
          <span v-if="sessionCreatedAt" class="inline-flex shrink-0 items-center gap-1" data-subagent-task-created>
            <i class="fa-regular fa-clock text-[9px]"></i>
            创建 {{ metaTime(sessionCreatedAt) }}
          </span>
          <span class="inline-flex shrink-0 items-center gap-1" data-subagent-task-finished>
            <i class="fa-solid fa-flag-checkered text-[9px]"></i>
            结束 {{ sessionFinishedAt ? metaTime(sessionFinishedAt) : (active ? '进行中' : '—') }}
          </span>
        </div>
      </div>

      <div
        v-if="active"
        class="mt-3.5 flex min-h-[40px] items-center gap-2 rounded-xl border border-sky-100/80 bg-sky-50/50 px-3 py-2 text-[11px] leading-4 text-slate-600"
        data-subagent-task-progress
      >
        <span class="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-sky-400"></span>
        <span class="line-clamp-2">{{ progressText }}</span>
      </div>

      <div
        class="mt-auto flex items-center justify-between gap-3 border-t border-slate-100 pt-3 text-[10.5px] text-slate-400"
        data-subagent-task-footer
      >
        <span class="inline-flex min-w-0 items-center gap-1.5">
          <span class="inline-flex shrink-0 items-center gap-1.5 font-medium" :class="statusTextTone" data-subagent-task-status>
            <span class="h-1.5 w-1.5 rounded-full bg-current"></span>
            {{ statusLabel }}
          </span>
          <template v-if="durationText">
            <span class="h-1 w-1 shrink-0 rounded-full bg-slate-300"></span>
            <span class="shrink-0 tabular-nums">{{ durationText }}</span>
          </template>
        </span>
        <span class="inline-flex shrink-0 items-center gap-1 font-medium text-slate-400 transition-colors group-hover:text-sky-600">
          查看过程
          <i class="fa-solid fa-chevron-right text-[8px] transition-transform group-hover:translate-x-0.5"></i>
        </span>
      </div>
    </div>
  </div>
</template>
