<script setup lang="ts">
/** 文件管理 tab 自己的工具条：根路径 / 一键折叠 / 文件数量（不属于 tab 条）。 */
import type { PropType } from 'vue'

const props = defineProps({
  root: { type: String, default: '' },
  fileCount: { type: Number, default: 0 },
  loading: { type: Boolean, default: false },
  collapsible: { type: Boolean, default: false },
  fullyCollapsed: { type: Boolean, default: false },
})

const emit = defineEmits<{
  toggleCollapsed: []
}>()
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
    <span class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">
      {{ props.loading ? '加载中' : `${props.fileCount} 个文件` }}
    </span>
  </div>
</template>
