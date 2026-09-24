<script setup lang="ts">
import { useSlots } from 'vue'
import {
  workspaceFileDirPath,
  type WorkspaceFilePick,
} from '../../composables/useWorkspaceFileMention'

interface Props {
  open: boolean
  files: WorkspaceFilePick[]
  highlightedIndex?: number
  /** 无会话/工作区为空时的空态说明（@ 引用遵循「工作区有什么就能引用什么」）。 */
  emptyHint?: string
}

const props = withDefaults(defineProps<Props>(), {
  highlightedIndex: 0,
  emptyHint: '没有匹配的文件',
})

const emit = defineEmits<{
  select: [file: WorkspaceFilePick]
}>()

const slots = useSlots()
const hasFooter = () => Boolean(slots.footer)
</script>

<template>
  <div
    v-if="props.open"
    class="absolute left-0 right-0 bottom-full z-20 mb-2 max-h-[320px] overflow-y-auto rounded-xl border border-slate-200 bg-white text-left shadow-[0_12px_32px_rgba(15,23,42,0.16)]"
  >
    <template v-if="props.files.length === 0">
      <div class="px-3 py-3 text-xs text-slate-400">{{ props.emptyHint }}</div>
      <slot name="footer" />
    </template>
    <template v-else>
      <button
        v-for="(file, index) in props.files"
        :key="file.relativePath"
        type="button"
        :class="[
          'flex h-10 w-full items-center gap-2 border-none px-3 py-1.5 text-left transition-colors',
          index === props.highlightedIndex ? 'bg-blue-50' : 'bg-white hover:bg-slate-50'
        ]"
        :title="file.relativePath"
        @mousedown.prevent="emit('select', file)"
      >
        <i class="fa-regular fa-file-lines shrink-0 text-xs text-slate-400"></i>
        <span class="max-w-48 shrink-0 truncate text-sm text-slate-800">{{ file.name }}</span>
        <span class="min-w-0 flex-1 truncate text-[11px] text-slate-400">{{ workspaceFileDirPath(file) }}</span>
      </button>
      <slot v-if="hasFooter()" name="footer" />
    </template>
  </div>
</template>
