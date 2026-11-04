<script setup lang="ts">
/** 文件管理 tab 自己的工具条：根路径 / 一键折叠 / 文件数量（不属于 tab 条）。 */
import { computed, type PropType } from 'vue'
import { formatFileSize } from '../../utils/consoleFileTree'

const props = defineProps({
  root: { type: String, default: '' },
  fileCount: { type: Number, default: 0 },
  usedBytes: { type: Number, default: 0 },
  limitBytes: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  collapsible: { type: Boolean, default: false },
  fullyCollapsed: { type: Boolean, default: false },
})

const emit = defineEmits<{
  toggleCollapsed: []
}>()

const usagePercent = computed(() => {
  if (props.limitBytes <= 0) return 0
  return Math.max(0, Math.min(100, (props.usedBytes / props.limitBytes) * 100))
})
const usageBarWidth = computed(() => Math.max(2, usagePercent.value))
const usageTone = computed(() => {
  if (usagePercent.value >= 100) return 'bg-red-500'
  if (usagePercent.value >= 90) return 'bg-amber-500'
  return 'bg-[#1677FF]'
})
</script>

<template>
  <div class="mb-2 flex shrink-0 items-center gap-2 px-1 text-xs" data-console-toolbar>
    <span class="min-w-0 flex-1 truncate font-mono text-xs text-slate-400">{{ props.root }}</span>
    <button
      v-if="props.collapsible"
      type="button"
      class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-slate-200 bg-white text-[11px] leading-none text-slate-500 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 active:bg-slate-100"
      :title="props.fullyCollapsed ? '展开全部目录' : '折叠全部目录'"
      :aria-label="props.fullyCollapsed ? '展开全部目录' : '折叠全部目录'"
      data-console-tree-toggle="true"
      @click="emit('toggleCollapsed')"
    >
      <i class="fa-solid" :class="props.fullyCollapsed ? 'fa-angles-down' : 'fa-angles-up'"></i>
    </button>
    <div
      v-if="props.limitBytes > 0"
      class="flex shrink-0 items-center gap-1.5"
      :title="`会话工作区占用：${formatFileSize(props.usedBytes)} / ${formatFileSize(props.limitBytes)}（${usagePercent.toFixed(1)}%）`"
    >
      <span
        class="text-[10px] tabular-nums"
        :class="props.usedBytes >= props.limitBytes ? 'text-red-500' : 'text-slate-400'"
      >{{ formatFileSize(props.usedBytes) }} / {{ formatFileSize(props.limitBytes) }}</span>
      <span class="relative h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
        <span
          class="block h-full rounded-full transition-all duration-300"
          :class="usageTone"
          :style="{ width: `${usageBarWidth}%` }"
        ></span>
      </span>
      <span class="text-[10px] tabular-nums text-slate-400">{{ usagePercent.toFixed(1) }}%</span>
    </div>
    <span class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">
      {{ props.loading ? '加载中' : `${props.fileCount} 个文件` }}
    </span>
  </div>
</template>
