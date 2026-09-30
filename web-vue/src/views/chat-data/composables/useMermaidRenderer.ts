import { onBeforeUnmount } from 'vue'
import { renderMermaidBlocks } from '../utils/mermaid'

/** 流式渲染静默窗口：增量期间不反复尝试，静默后才对容器跑一轮 */
const MERMAID_RENDER_DELAY_MS = 250

/**
 * 在 v-html markdown 容器上调度 mermaid 渲染。
 *
 * 调用方 watch 渲染内容变化后调 schedule()：流式增量期间防抖合并，
 * 静默 MERMAID_RENDER_DELAY_MS 后执行一轮替换渲染。首次触发用
 * immediate watch：延迟执行时组件必然已挂载，root 已就绪。
 */
export const useMermaidRenderer = (getRoot: () => HTMLElement | null | undefined) => {
  let timer: ReturnType<typeof setTimeout> | null = null
  const flush = () => {
    timer = null
    void renderMermaidBlocks(getRoot())
  }
  const schedule = () => {
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(flush, MERMAID_RENDER_DELAY_MS)
  }
  onBeforeUnmount(() => {
    if (timer !== null) clearTimeout(timer)
    timer = null
  })
  return { schedule }
}
