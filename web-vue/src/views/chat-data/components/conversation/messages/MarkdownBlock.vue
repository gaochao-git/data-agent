<script setup lang="ts">
/**
 * Markdown 正文块（主对话 / 子智能体过程共用）。
 *
 * 只负责：markdown → html、mermaid 渲染调度。内容不变时 computed 命中缓存，
 * 父级重渲染不会重复渲染 markdown（替代原来按条目手写的 markdownCache）。
 */
import { computed, ref, watch } from 'vue'
import { renderMarkdown } from '../../../utils'
import { useMermaidRenderer } from '../../../composables/useMermaidRenderer'

const props = defineProps<{
  content: string
}>()

const rootRef = ref<HTMLElement | null>(null)
const html = computed(() => renderMarkdown(props.content))

// 内容变化（含流式增量）后防抖一轮 mermaid 渲染；内容不变则不重排
const { schedule } = useMermaidRenderer(() => rootRef.value)
watch(() => props.content, () => schedule(), { immediate: true })
</script>

<template>
  <div
    ref="rootRef"
    class="conversation-markdown min-w-0 text-sm leading-relaxed text-slate-700"
    v-html="html"
  ></div>
</template>
