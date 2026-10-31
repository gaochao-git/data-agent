<script setup lang="ts">
/**
 * 划选引用浮层：复制选中片段 / 把片段作为引用加入输入框。
 * 位置与显隐由 useSelectionQuote 计算，这里只负责展示与上抛。
 */
import type { CSSProperties, PropType } from 'vue'
import type { QuoteToolbarState } from '../../../composables/useSelectionQuote'

defineProps({
  state: { type: Object as PropType<QuoteToolbarState>, required: true },
  style: { type: Object as PropType<CSSProperties>, required: true },
})

const emit = defineEmits<{
  copy: []
  add: []
}>()
</script>

<template>
  <div
    data-quote-toolbar
    class="fixed z-[var(--z-floating-tip)] flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-1 shadow-lg"
    :style="style"
    @mousedown.prevent
  >
    <button
      type="button"
      class="flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md border-none bg-transparent px-2.5 text-xs text-gray-600 transition-colors duration-150 hover:bg-gray-100 hover:text-gray-800"
      @click="emit('copy')"
    >
      <i class="fa-regular fa-copy text-[11px]"></i>
      <span>复制</span>
    </button>
    <div class="h-4 w-px bg-slate-200"></div>
    <button
      type="button"
      class="flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md border-none bg-transparent px-2.5 text-xs text-gray-600 transition-colors duration-150 hover:bg-blue-50 hover:text-blue-600"
      title="把选中内容作为引用加入输入框"
      @click="emit('add')"
    >
      <i class="fa-solid fa-quote-left text-[11px]"></i>
      <span>添加到对话</span>
    </button>
  </div>
</template>
