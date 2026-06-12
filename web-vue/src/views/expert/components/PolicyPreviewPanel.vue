<!--
  法规预览面板组件
  迁移自: web/src/pages/AISpecialistHome/RightPanels/PolicyPreviewPanel.tsx

  功能：
  - 显示法规原文
  - 支持条款高亮
  - 自动滚动定位到高亮内容
-->

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { fetchPolicy } from '../api/expert'
import type { ParsedCitation } from '../types'

// Props
interface Props {
  citation: ParsedCitation | null
}

const props = withDefaults(defineProps<Props>(), {
  citation: null
})

// State
const content = ref('')
const loading = ref(false)
const error = ref<string | null>(null)
const highlightRef = ref<HTMLSpanElement | null>(null)
const hasRenderedHighlight = ref(false)

// Computed
const policyKey = computed(() => {
  return props.citation?.sourceId ?? props.citation?.anchorId ?? ''
})

const highlightTarget = computed(() => {
  const candidate =
    props.citation?.clause?.trim() ||
    props.citation?.description?.trim() ||
    ''
  if (!candidate) {
    return ''
  }
  if (!content.value.includes(candidate)) {
    return ''
  }
  return candidate
})

// 高亮内容 - 将目标文本用 mark 标签包裹
const highlightedContent = computed(() => {
  if (!highlightTarget.value) {
    return [{ type: 'text', content: content.value }] as Array<{
      type: 'text' | 'highlight'
      content: string
      isFirst?: boolean
    }>
  }

  const segments = content.value.split(highlightTarget.value)
  const nodes: Array<{
    type: 'text' | 'highlight'
    content: string
    isFirst?: boolean
  }> = []

  segments.forEach((segment, index) => {
    nodes.push({ type: 'text', content: segment })
    if (index < segments.length - 1) {
      nodes.push({
        type: 'highlight',
        content: highlightTarget.value,
        isFirst: index === 0,
      })
    }
  })

  return nodes
})

// Watch policyKey 变化，获取法规内容
let abortController: AbortController | null = null

watch(policyKey, async (newPolicyKey) => {
  // 取消之前的请求
  if (abortController) {
    abortController.abort()
    abortController = null
  }

  if (!newPolicyKey) {
    content.value = ''
    error.value = null
    loading.value = false
    hasRenderedHighlight.value = false
    return
  }

  loading.value = true
  error.value = null
  content.value = ''
  hasRenderedHighlight.value = false

  abortController = new AbortController()

  try {
    const data = await fetchPolicy(newPolicyKey)
    if (data) {
      content.value = typeof data.text === 'string' ? data.text : (typeof data.content === 'string' ? data.content : '')
    }
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      return
    }
    error.value = err instanceof Error ? err.message : '未知错误'
    content.value = ''
  } finally {
    if (abortController?.signal.aborted === false) {
      loading.value = false
    }
  }
})

// Watch highlightTarget 和 hasRenderedHighlight，滚动到高亮位置
watch([highlightTarget, hasRenderedHighlight], () => {
  if (!highlightTarget.value || !hasRenderedHighlight.value) {
    return
  }

  setTimeout(() => {
    highlightRef.value?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
    })
  }, 200)
})

// Watch highlightedContent，标记高亮是否已渲染
watch(highlightedContent, (newValue) => {
  if (highlightTarget.value && newValue.some(item => item.type === 'highlight')) {
    nextTick(() => {
      hasRenderedHighlight.value = true
    })
  } else {
    hasRenderedHighlight.value = false
  }
})
</script>

<template>
  <div v-if="!citation" class="px-6 py-10 text-center text-sm text-gray-400">
    选择引用后将在此处显示法规原文。
  </div>

  <div v-else class="flex h-full flex-col">
    <!-- 标题区域 -->
    <div class="space-y-1 px-6 py-4">
      <p class="text-sm font-semibold text-gray-900">{{ citation.title }}</p>
      <p
        v-if="citation.clause"
        class="text-xs text-blue-600 truncate"
        :title="citation.clause"
      >
        {{ citation.clause }}
      </p>
      <p
        v-if="citation.description"
        class="text-xs text-gray-500 truncate"
        :title="citation.description"
      >
        {{ citation.description }}
      </p>
    </div>

    <!-- 内容区域 -->
    <div class="flex-1 overflow-y-auto border-t border-gray-100">
      <!-- 加载中 -->
      <div v-if="loading" class="px-6 py-6 text-sm text-gray-400">
        <i class="fa-solid fa-spinner fa-spin mr-2"></i>
        正在加载法规内容...
      </div>

      <!-- 加载失败 -->
      <div v-else-if="error" class="px-6 py-6 text-sm text-red-500">
        加载失败：{{ error }}
      </div>

      <!-- 内容展示 -->
      <div
        v-else-if="content"
        class="whitespace-pre-wrap break-words px-6 py-6 text-sm leading-6 text-gray-800"
      >
        <template v-for="(item, index) in highlightedContent" :key="index">
          <span v-if="item.type === 'text'">{{ item.content }}</span>
          <mark
            v-else
            :ref="item.isFirst ? (el: any) => { if (el) highlightRef.value = el } : undefined"
            class="rounded bg-yellow-200/70 px-1 py-0.5 text-gray-900"
          >
            {{ item.content }}
          </mark>
        </template>
      </div>

      <!-- 无内容 -->
      <div v-else class="px-6 py-6 text-sm text-gray-400">
        暂无内容
      </div>
    </div>
  </div>
</template>
