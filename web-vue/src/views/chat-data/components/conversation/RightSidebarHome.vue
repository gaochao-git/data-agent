<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  fileCount: { type: Number, default: 0 },
  usedBytes: { type: Number, default: 0 },
  limitBytes: { type: Number, default: 0 },
  taskCount: { type: Number, default: 0 },
  activeTaskCount: { type: Number, default: 0 },
})

const emit = defineEmits<{
  openFiles: []
  openSubagents: []
}>()

const formatBytes = (value: number): string => {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value < 1024) return `${Math.round(value)} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

const workspaceUsage = computed(() => {
  if (!props.limitBytes) return props.fileCount ? `${props.fileCount} 个文件` : '等待文件统计'
  return `${formatBytes(props.usedBytes)} / ${formatBytes(props.limitBytes)}`
})
</script>

<template>
  <section class="flex h-full flex-col justify-center" data-right-sidebar-home>
    <div class="mx-auto w-full max-w-[520px]">
      <div class="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          class="group flex min-h-[168px] flex-col rounded-2xl border border-slate-200 bg-white p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-sky-300 hover:shadow-md"
          data-sidebar-home-files
          @click="emit('openFiles')"
        >
          <span class="flex items-center gap-3">
            <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-amber-400 to-orange-500 text-white shadow-sm">
              <i class="fa-solid fa-folder-open text-sm"></i>
            </span>
            <span class="text-[14px] font-semibold text-slate-800">文件管理</span>
          </span>
          <span class="mt-3 text-[11px] leading-5 text-slate-500">浏览上传文件、工作区中间文件与智能体产物。</span>
          <span class="mt-auto flex w-full items-center justify-between gap-2 pt-4 text-[11px] text-slate-400">
            <span class="truncate">{{ fileCount }} 个文件 · {{ workspaceUsage }}</span>
            <i class="fa-solid fa-arrow-right text-[10px] transition-transform group-hover:translate-x-0.5 group-hover:text-sky-500"></i>
          </span>
        </button>

        <button
          type="button"
          class="group flex min-h-[168px] flex-col rounded-2xl border border-slate-200 bg-white p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-sky-300 hover:shadow-md"
          data-sidebar-home-subagents
          @click="emit('openSubagents')"
        >
          <span class="flex items-center gap-3">
            <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-sky-500 to-indigo-500 text-white shadow-sm">
              <i class="fa-solid fa-robot text-sm" aria-hidden="true"></i>
            </span>
            <span class="flex min-w-0 flex-wrap items-center gap-2 text-[14px] font-semibold text-slate-800">
              子智能体管理
              <span v-if="activeTaskCount" class="rounded-full bg-sky-50 px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
                {{ activeTaskCount }} 执行中
              </span>
            </span>
          </span>
          <span class="mt-3 text-[11px] leading-5 text-slate-500">查看后台任务进度、过程、报告，或取消仍在执行的任务。</span>
          <span class="mt-auto flex w-full items-center justify-between gap-2 pt-4 text-[11px] text-slate-400">
            <span>{{ taskCount }} 个智能体</span>
            <i class="fa-solid fa-arrow-right text-[10px] transition-transform group-hover:translate-x-0.5 group-hover:text-sky-500"></i>
          </span>
        </button>
      </div>
    </div>
  </section>
</template>
