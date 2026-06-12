<script setup lang="ts">
/**
 * 结构化消息渲染组件
 * 按顺序渲染 segments
 */
import { ref, computed } from 'vue'
import MarkdownIt from 'markdown-it'
import { parseStructuredContent } from '../utils/parseContent'
import { hideSemanticConfigSelectionPayloads } from '../utils'
import type { ContentSegment } from '../utils/parseContent'

interface Props {
  content: string
  isStreaming?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  isStreaming: false
})

const emit = defineEmits<{
  copySql: [sql: string]
}>()
const md = new MarkdownIt({ breaks: true, html: false, linkify: true })

// 思考区域展开状态
const thinkingExpanded = ref<Record<number, boolean>>({})

// 解析内容
const parsed = computed(() => parseStructuredContent(props.content, props.isStreaming))

// 切换思考区域展开
const toggleThinking = (index: number) => {
  thinkingExpanded.value = {
    ...thinkingExpanded.value,
    [index]: !thinkingExpanded.value[index]
  }
}

// 判断思考区域是否展开
const isThinkingExpanded = (index: number, inProgress?: boolean): boolean => {
  const expanded = thinkingExpanded.value[index]
  if (expanded !== undefined) return expanded
  return true // 默认展开
}

// 解析语义参数
interface SemanticParam {
  label: string
  values: string[]
}

const parseSemanticParams = (content: string): SemanticParam[] => {
  const lines = content.split('\n').filter((line) => line.trim())
  return lines
    .map((line) => {
      const match = line.match(/\*\*([^*]+)\*\*:\s*(.+)/)
      if (match) {
        const label = match[1] ?? ''
        const valuesStr = match[2] ?? ''
        const values = valuesStr.match(/\*\*([^*]+)\*\*/g)?.map((v) => v.replace(/\*\*/g, '')) || []
        return { label, values: values.length > 0 ? values : [valuesStr] }
      }
      return { label: '', values: [line] }
    })
    .filter((item) => item.label || item.values.length > 0)
}

// 复制 SQL
const handleCopySql = (sql: string) => {
  emit('copySql', sql)
  navigator.clipboard.writeText(sql)
}

const renderMarkdown = (content: string) => {
  return md.render(hideSemanticConfigSelectionPayloads(content))
}
</script>

<template>
  <!-- 无 segments 时显示原始内容 -->
  <div
    v-if="parsed.segments.length === 0"
    class="text-sm text-gray-700 leading-relaxed markdown-body"
    v-html="renderMarkdown(content)"
  >
  </div>

  <!-- 按 segments 顺序渲染 -->
  <div v-else class="flex flex-col gap-3">
    <template v-for="(segment, index) in parsed.segments" :key="index">
      <!-- 思考区域 -->
      <div v-if="segment.type === 'thinking'" class="rounded-xl overflow-hidden bg-black/[0.02] border border-black/[0.06]">
        <button
          class="flex items-center gap-2.5 w-full px-4 py-3 border-none bg-transparent cursor-pointer text-left transition-colors duration-200 hover:bg-black/[0.02]"
          @click="toggleThinking(index)"
        >
          <i
            :class="[
              'fa-solid fa-chevron-right text-[10px] text-gray-400 transition-transform duration-200',
              isThinkingExpanded(index, segment.inProgress) && 'rotate-90'
            ]"
          ></i>
          <template v-if="segment.inProgress">
            <div class="w-5 h-5 flex items-center justify-center rounded-md bg-amber-500/10">
              <i class="fa-solid fa-spinner fa-spin text-[10px] text-amber-500"></i>
            </div>
            <span class="text-xs font-medium text-gray-500">正在思考...</span>
          </template>
          <template v-else>
            <div class="w-5 h-5 flex items-center justify-center rounded-md bg-gradient-to-br from-amber-500 to-amber-600">
              <i class="fa-solid fa-lightbulb text-[10px] text-white"></i>
            </div>
            <span class="text-xs font-medium text-gray-500">已思考</span>
          </template>
        </button>
        <div
          :class="[
            'overflow-hidden transition-all duration-200',
            isThinkingExpanded(index, segment.inProgress) ? 'max-h-[1000px] opacity-100' : 'max-h-0 opacity-0'
          ]"
        >
          <div
            class="px-4 pb-3 pt-3 text-sm text-gray-600 border-t border-black/[0.04] leading-relaxed markdown-body"
            v-html="renderMarkdown(segment.content)"
          ></div>
        </div>
      </div>

      <!-- 语义参数 -->
      <div v-else-if="segment.type === 'semanticParams'" class="rounded-xl overflow-hidden bg-emerald-500/[0.04] border border-emerald-500/20">
        <div class="flex items-center gap-2.5 px-4 py-3">
          <div class="w-5 h-5 flex items-center justify-center rounded-md bg-gradient-to-br from-emerald-500 to-emerald-600">
            <i class="fa-solid fa-layer-group text-[10px] text-white"></i>
          </div>
          <span class="text-xs font-medium text-emerald-600">语义参数</span>
          <i v-if="segment.inProgress" class="fa-solid fa-spinner fa-spin text-xs text-emerald-500"></i>
        </div>
        <div class="px-4 pb-3 pt-2 border-t border-emerald-500/10">
          <div
            v-for="(param, idx) in parseSemanticParams(segment.content)"
            :key="idx"
            class="flex items-baseline gap-2 py-0.5 text-sm"
          >
            <span class="font-medium text-gray-500">{{ param.label }}:</span>
            <span class="font-semibold text-gray-700">{{ param.values.join(', ') }}</span>
          </div>
        </div>
      </div>

      <!-- 普通文本 -->
      <div
        v-else-if="segment.type === 'text'"
        class="text-sm text-gray-700 leading-relaxed markdown-body"
        v-html="renderMarkdown(segment.content)"
      ></div>
    </template>
  </div>
</template>

<style scoped>
.markdown-body {
  max-width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;
}

.markdown-body :deep(p) {
  margin: 0 0 8px 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(strong) {
  font-weight: 700;
}

.markdown-body :deep(code) {
  padding: 1px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  background: rgba(15, 23, 42, 0.06);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-body :deep(pre) {
  max-width: 100%;
  margin: 8px 0;
  padding: 12px;
  overflow-x: auto;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  white-space: pre;
}

.markdown-body :deep(pre code) {
  display: block;
  min-width: max-content;
  padding: 0;
  background: transparent;
  white-space: pre;
  overflow-wrap: normal;
  word-break: normal;
  font-size: 12px;
  line-height: 1.6;
  color: #334155;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}

.markdown-body :deep(table) {
  width: 100%;
  max-width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  table-layout: fixed;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  border: 1px solid #d8dee8;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.markdown-body :deep(th) {
  background: #f8fafc;
  color: #334155;
  font-weight: 700;
}
</style>
