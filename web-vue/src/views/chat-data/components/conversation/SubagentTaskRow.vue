<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { SubagentTaskRecord } from '../../types'
import { isSubagentTaskActive } from '../../composables/useSubagentTasks'
import { useToolTiming } from '../../composables/useToolTiming'

const props = defineProps({
  task: { type: Object as PropType<SubagentTaskRecord>, required: true },
  sessionTurnCount: { type: Number, default: 1 },
  cancelling: { type: Boolean, default: false },
})

const emit = defineEmits<{
  open: [task: SubagentTaskRecord]
  cancel: [task: SubagentTaskRecord]
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
const { durationText } = useToolTiming(() => startedAt.value, () => endedAt.value)

const taskTitle = computed(() => props.task.label?.trim() || props.task.description?.trim() || '后台任务')

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
    :title="task.description"
    @click="emit('open', task)"
    @keydown.enter.prevent="emit('open', task)"
  >
    <span class="absolute inset-x-0 top-0 h-px bg-gradient-to-r" :class="accentLineTone"></span>

    <div class="flex flex-1 flex-col p-4">
      <div class="flex items-start justify-between gap-3">
        <h3
          class="m-0 line-clamp-2 min-w-0 flex-1 text-[14px] font-semibold leading-5 text-slate-800"
          :title="`${task.subagent_type} - ${taskTitle}`"
          data-subagent-task-title
        >
          <span class="text-[12px] font-semibold text-sky-600">{{ task.subagent_type }}</span>
          <span
            v-if="sessionTurnCount > 1"
            class="ml-1 rounded bg-sky-50 px-1 py-0.5 align-middle text-[9px] font-medium text-sky-600"
            data-subagent-task-turn
          >追问 {{ sessionTurnCount - 1 }}</span>
          <span class="mx-1 font-normal text-slate-300">-</span>
          <span>{{ taskTitle }}</span>
        </h3>

        <button
          v-if="active"
          type="button"
          class="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-slate-300 opacity-70 transition-all hover:bg-rose-50 hover:text-rose-500 group-hover:opacity-100"
          :disabled="cancelling"
          title="取消后台任务"
          @click.stop="emit('cancel', task)"
        >
          <i :class="cancelling ? 'fa-solid fa-spinner fa-spin' : 'fa-solid fa-xmark'" class="text-[11px]"></i>
        </button>
      </div>

      <div
        v-if="task.status === 'completed' && task.report"
        class="mt-2.5 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-[10.5px] text-slate-400"
      >
        <span class="inline-flex shrink-0 items-center gap-1 text-emerald-600">
          <i class="fa-regular fa-file-lines text-[9px]"></i>
          报告已生成
        </span>
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
