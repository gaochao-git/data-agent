<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { SubagentTaskRecord } from '../../types'
import SubagentTaskRow from './SubagentTaskRow.vue'

const props = defineProps({
  tasks: { type: Array as PropType<SubagentTaskRecord[]>, default: () => [] },
  cancellingTaskIds: { type: Array as PropType<string[]>, default: () => [] },
})

const emit = defineEmits<{
  open: [task: SubagentTaskRecord]
  cancel: [task: SubagentTaskRecord]
}>()

const cancellingIds = computed(() => new Set(props.cancellingTaskIds))
</script>

<template>
  <section class="flex min-h-0 flex-col" data-subagent-manager>
    <div v-if="tasks.length" class="subagent-task-grid">
      <SubagentTaskRow
        v-for="task in tasks"
        :key="task.task_id"
        :task="task"
        :cancelling="cancellingIds.has(task.task_id)"
        @open="emit('open', $event)"
        @cancel="emit('cancel', $event)"
      />
    </div>

    <div v-else class="rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 px-5 py-10 text-center" data-subagent-manager-empty>
      <span class="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-white text-slate-300 ring-1 ring-slate-200">
        <i class="fa-solid fa-wand-magic-sparkles text-sm"></i>
      </span>
      <p class="mt-3 text-[12px] font-medium text-slate-600">还没有后台子智能体任务</p>
      <p class="mt-1 text-[11px] text-slate-400">在对话中派发后台任务后，会在这里汇总进度与报告。</p>
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
