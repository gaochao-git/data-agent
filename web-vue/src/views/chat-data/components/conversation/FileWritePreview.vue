<script setup lang="ts">
/**
 * 写入类工具的内容预览（file_write / file_edit）。
 *
 * 只负责「按文件类型格式化展示内容」；参数视图由父组件保留原样渲染。
 * .md 渲染 Markdown，.json 美化缩进，.sql/.yaml 复用 CodeMirror 语法高亮，
 * 其余代码按只读行号展示，.txt/.log 等纯文本保持换行。
 * 生成中不挂 CodeMirror，避免半截 JSON 反复重建编辑器；先按纯文本逐段跟随，
 * 收口后再切换到最终格式化视图。
 */
import { computed, nextTick, ref, watch } from 'vue'
import { detectFilePreviewFormat, fileBaseName, formatJsonPreview } from '../../utils/fileWritePreview'
import { isScrolledToBottom } from '../../utils/scrollFollow'
import CodeFilePreview from './CodeFilePreview.vue'
import MarkdownFilePreview from './MarkdownFilePreview.vue'

const props = defineProps({
  path: { type: String, default: '' },
  content: { type: String, default: '' },
  action: { type: String, default: 'write' },
  height: { type: String, default: '16rem' },
  streaming: { type: Boolean, default: false },
  truncated: { type: Boolean, default: false },
})

const format = computed(() => detectFilePreviewFormat(props.path))
const hasPath = computed(() => Boolean(props.path.trim()))
const fileName = computed(() => (
  hasPath.value ? fileBaseName(props.path) || '未命名文件' : '文件路径生成中…'
))
const formatLabel = computed(() => (hasPath.value ? format.value.label : ''))
const lineCount = computed(() => (props.content ? props.content.split('\n').length : 0))
const jsonPreview = computed(() => (
  format.value.kind === 'json' && !props.streaming
    ? formatJsonPreview(props.content)
    : { text: props.content, error: '' }
))

// 生成中贴底跟随：用户主动上翻后暂停，回到底部附近恢复。
const scrollRef = ref<HTMLElement | null>(null)
let followBottom = true
const handleScroll = () => {
  const element = scrollRef.value
  if (element) followBottom = isScrolledToBottom(element, 24)
}
watch(
  () => props.content,
  async () => {
    if (!props.streaming) return
    await nextTick()
    const element = scrollRef.value
    if (element && followBottom) element.scrollTop = element.scrollHeight
  },
  { immediate: true },
)
</script>

<template>
  <div class="file-write-preview min-w-0 overflow-hidden rounded-lg border border-black/[0.06] bg-white" data-file-write-preview>
    <div class="flex min-w-0 flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-3 py-2 text-[11px] text-slate-500">
      <i class="fa-regular shrink-0" :class="format.icon"></i>
      <span class="min-w-0 max-w-[240px] truncate font-medium text-slate-600" :title="path">{{ fileName }}</span>
      <span v-if="formatLabel" class="shrink-0 rounded bg-slate-200/60 px-1.5 py-0.5 text-[10px] text-slate-600">{{ formatLabel }}</span>
      <span v-if="action === 'edit'" class="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-600">修改后内容</span>
      <span v-if="streaming" class="inline-flex shrink-0 items-center gap-1 rounded bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-600">
        <i class="fa-solid fa-circle-notch fa-spin text-[9px]"></i>
        <span>生成中</span>
      </span>
      <span class="ml-auto shrink-0 font-mono text-slate-400">{{ lineCount }} 行</span>
    </div>
    <div
      v-if="format.kind === 'json' && jsonPreview.error"
      class="border-b border-amber-100 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-600"
    >{{ jsonPreview.error }}</div>
    <div
      v-if="truncated"
      class="border-b border-amber-100 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-600"
    >预览已截断，完整内容以工具结果为准</div>
    <div class="min-w-0" :style="{ height }">
      <template v-if="streaming">
        <div v-if="!content" class="flex h-full items-center justify-center gap-2 text-xs text-slate-400">
          <i class="fa-solid fa-circle-notch fa-spin"></i>
          <span>正在生成内容...</span>
        </div>
        <div
          v-else-if="format.kind === 'markdown'"
          ref="scrollRef"
          class="h-full overflow-auto"
          @scroll.passive="handleScroll"
        >
          <MarkdownFilePreview :content="content" class="file-write-stream-md" />
        </div>
        <div
          v-else
          ref="scrollRef"
          class="h-full overflow-auto bg-slate-50/40 px-3.5 py-3"
          @scroll.passive="handleScroll"
        >
          <pre class="m-0 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-slate-700">{{ content }}</pre>
        </div>
      </template>
      <template v-else>
        <div v-if="format.kind === 'markdown'" class="h-full min-h-0">
          <MarkdownFilePreview :content="content" class="h-full" />
        </div>
        <div v-else-if="format.kind === 'text'" class="h-full overflow-auto bg-slate-50/40 px-3.5 py-3">
          <pre class="m-0 whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-slate-700">{{ content }}</pre>
        </div>
        <CodeFilePreview
          v-else
          :content="jsonPreview.text"
          :language="format.codeLanguage"
          class="file-write-code h-full"
        />
      </template>
    </div>
  </div>
</template>

<style scoped>
.file-write-preview :deep(.file-write-code.code-file-preview) {
  border: 0;
  border-radius: 0;
}

.file-write-preview :deep(.file-write-stream-md) {
  min-height: 100%;
  height: auto;
  overflow: visible;
}
</style>
