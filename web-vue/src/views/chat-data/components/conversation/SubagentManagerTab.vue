<script setup lang="ts">
import { computed, watch } from 'vue'
import type { PropType } from 'vue'
import type { SubagentTaskRecord } from '../../types'
import { groupSubagentTaskSessions, type SubagentTaskSession } from '../../composables/useSubagentTasks'
import SubagentTaskRow from './SubagentTaskRow.vue'

const props = defineProps({
  tasks: { type: Array as PropType<SubagentTaskRecord[]>, default: () => [] },
  cancellingTaskIds: { type: Array as PropType<string[]>, default: () => [] },
  closingTaskIds: { type: Array as PropType<string[]>, default: () => [] },
  loadTaskDetail: { type: Function as PropType<(taskId: string, refresh?: boolean) => Promise<SubagentTaskRecord | null>>, default: null },
})

const emit = defineEmits<{
  open: [task: SubagentTaskRecord]
  cancel: [task: SubagentTaskRecord]
  closeSession: [task: SubagentTaskRecord]
}>()

const cancellingIds = computed(() => new Set(props.cancellingTaskIds))
const closingIds = computed(() => new Set(props.closingTaskIds))
const sessions = computed(() => groupSubagentTaskSessions(props.tasks))
const requestedDetailKeys = new Set<string>()
const taskStatusById = new Map<string, string>()
const sessionCardTask = (session: SubagentTaskSession): SubagentTaskRecord => ({
  ...session.latestTask,
  label: session.label,
})

// 管理卡要汇总上下文用量，而列表接口不带 result_meta；按会话只预取最新任务详情，
// 状态变化时再刷新一次，确保完成/取消后能拿到最终快照。会话上限为 5，请求量可控。
watch(
  () => sessions.value.map(session => `${session.latestTask.task_id}:${session.latestTask.status}`),
  () => {
    if (!props.loadTaskDetail) return
    for (const session of sessions.value) {
      const task = session.latestTask
      const key = `${task.task_id}:${task.status}`
      if (requestedDetailKeys.has(key)) continue
      requestedDetailKeys.add(key)
      const previousStatus = taskStatusById.get(task.task_id)
      taskStatusById.set(task.task_id, task.status)
      void props.loadTaskDetail(task.task_id, Boolean(previousStatus && previousStatus !== task.status))
        .catch(() => { requestedDetailKeys.delete(key) })
    }
  },
  { immediate: true },
)
</script>

<template>
  <section class="flex min-h-0 flex-col" data-subagent-manager>
    <div v-if="sessions.length" class="subagent-task-grid">
      <SubagentTaskRow
        v-for="session in sessions"
        :key="session.sessionId"
        :task="sessionCardTask(session)"
        :session-turn-count="session.turnCount"
        :session-tasks="session.tasks"
        :cancelling="cancellingIds.has(session.latestTask.task_id)"
        :closing="closingIds.has(session.latestTask.task_id)"
        @open="emit('open', $event)"
        @cancel="emit('cancel', $event)"
        @close-session="emit('closeSession', $event)"
      />
    </div>

    <div v-else class="rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 px-5 py-10 text-center" data-subagent-manager-empty>
      <span class="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-white text-slate-300 ring-1 ring-slate-200">
        <i class="fa-solid fa-wand-magic-sparkles text-sm"></i>
      </span>
      <p class="mt-3 text-[12px] font-medium text-slate-600">还没有子智能体任务</p>
      <p class="mt-1 text-[11px] text-slate-400">在对话中派发子智能体后，会在这里汇总进度与报告。</p>
    </div>
  </section>
</template>

<style scoped>
[data-subagent-manager] {
  container-type: inline-size;
}

.subagent-task-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}

@container (min-width: 620px) {
  .subagent-task-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
