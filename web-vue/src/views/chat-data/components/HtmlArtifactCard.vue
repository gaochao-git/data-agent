<script setup lang="ts">
import { ref, watch } from 'vue'
import { getConversationWorkspaceRawFile } from '../api'
import HtmlPagePreview from './HtmlPagePreview.vue'

interface HtmlArtifact {
  path: string
  name: string
  artifactType?: string
}

/**
 * 消息流内的 HTML artifact 内联卡片：默认折叠，展开时懒加载文件内容
 * 并以沙箱 iframe 渲染。预览区打开与下载动作交给父级统一处理。
 */
const props = defineProps({
  conversationId: {
    type: String,
    default: '',
  },
  artifact: {
    type: Object as () => HtmlArtifact,
    required: true,
  },
  defaultHeight: {
    type: String,
    default: 'h-[420px]',
  },
})

const emit = defineEmits<{
  preview: [artifact: HtmlArtifact]
  download: [artifact: HtmlArtifact]
}>()

const expanded = ref(false)
const loading = ref(false)
const html = ref('')
const errorText = ref('')

const resetState = () => {
  expanded.value = false
  loading.value = false
  html.value = ''
  errorText.value = ''
}

watch(() => [props.conversationId, props.artifact.path], resetState)

const loadHtml = async () => {
  if (!props.conversationId || loading.value) return
  loading.value = true
  errorText.value = ''
  try {
    const blob = await getConversationWorkspaceRawFile(props.conversationId, props.artifact.path)
    const text = await blob.text()
    // 仅接受自包含 HTML；为防误渲染，截断非 HTML 内容
    html.value = /<html[\s>]|<!doctype html/i.test(text) ? text : ''
    if (!html.value) errorText.value = '文件不是有效的 HTML 页面'
  } catch (err) {
    console.error('加载 HTML 页面失败:', err)
    errorText.value = '加载失败，请重试'
  } finally {
    loading.value = false
  }
}

const toggleExpanded = () => {
  expanded.value = !expanded.value
  if (expanded.value && !html.value && !errorText.value) void loadHtml()
}

const retry = () => {
  errorText.value = ''
  void loadHtml()
}
</script>

<template>
  <div class="mt-3 overflow-hidden rounded-lg border border-slate-200 bg-white">
    <button
      type="button"
      class="flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-slate-50"
      :aria-expanded="expanded"
      @click="toggleExpanded"
    >
      <span class="flex min-w-0 items-center gap-2.5">
        <span class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-600 text-white">
          <i class="fa-brands fa-html5 text-sm"></i>
        </span>
        <span class="min-w-0">
          <span class="block truncate text-sm font-semibold text-slate-800">{{ props.artifact.name }}</span>
          <span class="mt-0.5 block text-[11px] text-slate-500">交互 HTML 页面{{ expanded ? '' : '，点击展开预览' }}</span>
        </span>
      </span>
      <i class="fa-solid shrink-0 text-xs text-slate-400" :class="expanded ? 'fa-chevron-up' : 'fa-chevron-down'"></i>
    </button>
    <div v-if="expanded" class="border-t border-slate-100">
      <div class="flex items-center justify-end gap-1.5 px-3 py-1.5">
        <button
          type="button"
          class="flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-600 hover:bg-slate-100"
          title="在右侧预览区打开"
          @click="emit('preview', props.artifact)"
        >
          <i class="fa-regular fa-eye text-[10px]"></i>
          预览
        </button>
        <button
          type="button"
          class="flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-600 hover:bg-slate-100"
          title="下载 HTML 文件"
          @click="emit('download', props.artifact)"
        >
          <i class="fa-solid fa-download text-[10px]"></i>
          下载
        </button>
        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-[11px] text-slate-600 hover:bg-slate-100"
          title="重新加载"
          @click="retry"
        >
          <i class="fa-solid fa-rotate-right" :class="loading ? 'fa-spin' : ''"></i>
        </button>
      </div>
      <div v-if="loading && !html" class="flex h-48 items-center justify-center gap-2 text-xs text-slate-400">
        <i class="fa-solid fa-spinner fa-spin"></i>
        正在加载页面...
      </div>
      <div v-else-if="errorText" class="flex h-48 items-center justify-center text-xs text-slate-400">{{ errorText }}</div>
      <HtmlPagePreview v-else :html="html" :class="props.defaultHeight" />
    </div>
  </div>
</template>
