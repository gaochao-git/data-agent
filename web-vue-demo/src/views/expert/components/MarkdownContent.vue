<!--
  Markdown 内容渲染组件
  支持引用高亮功能
-->

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

interface Props {
  content: string
  references?: Array<{
    id: string
    title: string
    summary: string
    displayLabel?: string
  }>
  onCitationClick?: (ref: any) => void
}

const props = withDefaults(defineProps<Props>(), {
  references: () => [],
})

const md = new MarkdownIt({
  html: false,
  xhtmlOut: false,
  breaks: true,
  linkify: true,
  typographer: true,
})

/**
 * 全角数字转换为半角数字
 */
const normalizeLabel = (value: string): string => {
  return value.replace(/[０-９]/g, (digit: string) => {
    return String.fromCharCode(digit.charCodeAt(0) - 0xfee0)
  })
}

/**
 * 替换引用标签 - 匹配【引用1】格式
 */
const replaceReferenceLabels = (content: string): string => {
  if (!content) return content
  return content.replace(/【引用([0-9]+)】/g, (match: string, num: string) => {
    // 保留【引用1】的显示文本，但包装成可点击的 span
    return `<span data-ref-label="引用${num}" class="ref-inline citation-highlight" role="button" tabindex="0">${match}</span>`
  })
}

/**
 * 解析引用ID - 匹配 [ref:xxx] 或 [法律条款:xxx] 格式
 */
const parseCitation = (text: string) => {
  const refMatch = text.match(/\[ref:(.+?)\]/i)
  if (refMatch) {
    return { type: 'ref', id: refMatch[1], text }
  }

  const lawMatch = text.match(/\[法律条款:(.+?)\]/i)
  if (lawMatch) {
    return { type: 'law', id: lawMatch[1], text }
  }

  return null
}

/**
 * 渲染内容，处理引用高亮
 */
const renderedContent = computed(() => {
  if (!props.content) return ''

  // 先替换【引用1】格式
  let html = replaceReferenceLabels(props.content)

  // 然后渲染 Markdown
  html = md.render(html)

  // 替换 [ref:xxx] 和 [法律条款:xxx] 为可点击的高亮元素
  html = html.replace(/\[ref:(.+?)\]/gi, (match: string, id: string) => {
    return `<span class="ref-inline citation-highlight" data-ref-id="${id}" role="button" tabindex="0">${match}</span>`
  })

  html = html.replace(/\[法律条款:(.+?)\]/gi, (match: string, id: string) => {
    return `<span class="ref-inline citation-highlight" data-ref-id="${id}" role="button" tabindex="0">${match}</span>`
  })

  return html
})

/**
 * 处理引用点击
 */
const handleCitationClick = (event: MouseEvent) => {
  const target = event.target as HTMLElement
  const refId = target?.getAttribute('data-ref-id')
  const refLabel = target?.getAttribute('data-ref-label')

  if (refId && props.references && props.onCitationClick) {
    // 处理 [ref:xxx] 和 [法律条款:xxx] 格式的点击
    const reference = props.references.find(ref => ref.id === refId)
    if (reference) {
      props.onCitationClick(reference)
    }
  } else if (refLabel && props.references && props.onCitationClick) {
    // 处理【引用1】格式的点击
    const normalizedLabel = normalizeLabel(refLabel)
    const reference = props.references.find(ref => {
      const displayLabel = ref.displayLabel || ''
      return normalizeLabel(displayLabel) === normalizedLabel
    })
    if (reference) {
      props.onCitationClick(reference)
    }
  }
}
</script>

<template>
  <div
    class="markdown-content prose prose-slate max-w-none text-[16px] font-[350] leading-[30px] text-[rgba(0,0,0,0.88)] prose-headings:mb-2 prose-headings:mt-4 prose-h2:text-[16px] prose-h2:font-[350] prose-p:my-2 prose-ul:my-2 prose-li:my-1 prose-li:text-[16px] prose-li:leading-[30px]"
    v-html="renderedContent"
    @click="handleCitationClick"
  />
</template>

<style scoped>
.markdown-content :deep(.citation-highlight) {
  background-color: rgba(123, 183, 212, 0.15);
  color: #007EA7;
  border-radius: 3px;
  padding: 1px 4px;
  cursor: pointer;
  transition: background-color 0.15s ease;
  font-weight: 500;
}

.markdown-content :deep(.citation-highlight:hover) {
  background-color: rgba(123, 183, 212, 0.25);
}

.markdown-content :deep(.ref-inline) {
  color: #007EA7;
}
</style>
