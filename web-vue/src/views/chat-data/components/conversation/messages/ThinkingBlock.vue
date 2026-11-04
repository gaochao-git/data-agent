<script setup lang="ts">
/**
 * 思考块（主对话 / 子智能体过程共用）。
 *
 * 折叠状态就近放在块内部（组件按分段 id 复用，流式期间不会丢）；
 * 流式中且收起时，按钮行露尾部预览；展开后正文由 ThinkingSegmentBody 粘底跟随。
 */
import { computed, ref } from 'vue'
import ThinkingSegmentBody from '../ThinkingSegmentBody.vue'

const props = withDefaults(defineProps<{
  content: string
  /** 该分段是否正在流式追加 */
  active?: boolean
  /** 收起态尾部预览（由投影层算好，仅 active 时非空） */
  preview?: string
  /** 内容被截断（子智能体过程预览） */
  truncated?: boolean
  /** assistant：主对话样式；trail：子智能体过程样式 */
  variant?: 'assistant' | 'trail'
}>(), {
  active: false,
  preview: '',
  truncated: false,
  variant: 'assistant',
})

const expanded = ref(false)
const isTrail = computed(() => props.variant === 'trail')

const label = computed(() => {
  if (isTrail.value) {
    if (expanded.value) return '收起思考'
    return props.active ? '思考中' : '已思考'
  }
  return props.active ? '思考中' : '思考'
})

const buttonClass = computed(() => (isTrail.value
  ? 'sub-thinking-toggle flex w-full max-w-full items-center gap-1.5 rounded px-1 py-0.5 text-[11px] text-slate-400 transition-colors hover:text-slate-600'
  : 'group inline-flex max-w-full items-center gap-2 rounded-md border-none bg-transparent px-2.5 py-1.5 text-left text-xs font-medium text-slate-400 transition-colors duration-200 hover:text-slate-600'))

const showPreview = computed(() => props.active && !expanded.value && Boolean(props.preview))
</script>

<template>
  <div class="min-w-0">
    <button
      type="button"
      :class="buttonClass"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <i
        :class="[
          'fa-solid fa-brain text-xs text-slate-400 transition-colors duration-200 group-hover:text-slate-600',
          active ? 'fa-fade' : '',
        ]"
      ></i>
      <span>{{ label }}</span>
      <i
        :class="[
          'fa-solid fa-chevron-right text-[10px] text-slate-400 transition-all duration-200 group-hover:text-slate-600',
          expanded ? 'rotate-90 opacity-100' : 'opacity-0 group-hover:opacity-100',
        ]"
      ></i>
      <span
        v-if="showPreview"
        class="min-w-0 flex-1 truncate text-xs font-normal text-slate-400/80"
      >{{ preview }}</span>
      <i v-if="isTrail && active" class="fa-solid fa-spinner fa-spin shrink-0 text-[9px]"></i>
    </button>
    <ThinkingSegmentBody v-if="expanded" :content="content" :active="active" />
    <div v-if="isTrail && expanded && truncated" class="mt-0.5 text-[11px] text-slate-400">…（思考已截断）</div>
  </div>
</template>
