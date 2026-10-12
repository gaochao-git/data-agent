<script setup lang="ts">
/**
 * Mermaid 图表文件预览（.mmd）：整份文件内容即 mermaid 源码。
 * 展示走原生布局（svg 宽度自适应容器、纵向滚动），全屏查看器提供缩放/拖拽；
 * 渲染失败（语法不完整或不支持）回退为源码展示，不放大错误。
 * 异步渲染用过期 token 丢弃：内容快速切换（点选其他文件）时不回写旧图。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { renderMermaidSourceBlock } from '../../utils/mermaid'

const props = defineProps({
  content: {
    type: String,
    default: '',
  },
})

const host = ref<HTMLElement | null>(null)
const renderFailed = ref(false)
let renderToken = 0

const render = async () => {
  const el = host.value
  if (!el) return
  const token = ++renderToken
  const ok = await renderMermaidSourceBlock(el, props.content)
  if (token !== renderToken) return // 内容已变化，丢弃过期渲染结果
  renderFailed.value = !ok
  if (!ok) el.replaceChildren()
}

// 监听 [host, content]：host 是模板 ref，setup 期间为 null、挂载后才绑定，
// 单独 immediate watch content 会在 ref 绑定前跑一次而白渲染（el 为 null 早退）。
watch([host, () => props.content], render)

onBeforeUnmount(() => {
  renderToken += 1
})
</script>

<template>
  <div class="mermaid-file-preview h-full min-w-0 overflow-auto rounded-md border border-slate-100 bg-white">
    <div v-show="!renderFailed" ref="host" class="mermaid-file-preview-host h-full"></div>
    <pre
      v-if="renderFailed"
      class="m-0 overflow-auto whitespace-pre-wrap break-words p-3 text-xs leading-relaxed text-slate-700"
    >{{ content }}</pre>
  </div>
</template>
