<!--
  调试面板组件
  迁移自: web/src/pages/AISpecialistHome/RightPanels/TracePanel.tsx

  功能：
  - 显示 AI 思考过程和调试信息
  - 支持刷新 trace 记录
-->

<script setup lang="ts">
import { ref, computed, watch } from 'vue'

// API 基础路径
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5001'

// Trace 事件类型
interface TraceEvent {
  ts_ms?: number
  event?: string
  payload?: unknown
}

// Props
interface Props {
  conversationId: string | null
}

const props = withDefaults(defineProps<Props>(), {
  conversationId: null
})

// State
const events = ref<TraceEvent[]>([])
const isLoading = ref(false)
const error = ref<string | null>(null)

// Computed
const canFetch = computed(() => Boolean(props.conversationId))

const summaryText = computed(() => {
  if (!props.conversationId) {
    return '暂无会话可用。'
  }
  if (isLoading.value) {
    return '正在加载 trace...'
  }
  if (error.value) {
    return error.value
  }
  if (events.value.length === 0) {
    return '没有 trace 记录。'
  }
  return `共 ${events.value.length} 条记录。`
})

// 格式化时间戳
const formatTimestamp = (value?: number): string => {
  if (!value) {
    return ''
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return String(value)
  }
  return date.toLocaleString()
}

// 获取 trace 数据
const fetchTrace = async () => {
  if (!props.conversationId) {
    return
  }

  isLoading.value = true
  error.value = null

  try {
    const response = await fetch(
      `${API_BASE}/api/ai_chat/traces/${encodeURIComponent(props.conversationId)}`,
      {
        credentials: 'include',
      }
    )

    if (!response.ok) {
      const text = await response.text()
      throw new Error(text || `请求失败，状态码 ${response.status}`)
    }

    const data: { events?: TraceEvent[] } = await response.json()
    events.value = Array.isArray(data.events) ? data.events : []
  } catch (err) {
    const message = err instanceof Error ? err.message : '加载 trace 失败'
    error.value = message
    events.value = []
  } finally {
    isLoading.value = false
  }
}

// Watch conversationId 变化，自动加载 trace
watch(canFetch, (newCanFetch) => {
  if (!newCanFetch) {
    events.value = []
    error.value = null
  } else {
    fetchTrace()
  }
}, { immediate: true })
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 头部 -->
    <div class="flex items-center justify-between border-b border-gray-100 px-5 py-3">
      <div>
        <p class="text-sm font-semibold text-gray-900">调试 Trace</p>
        <p class="text-xs text-gray-400">{{ summaryText }}</p>
      </div>
      <button
        type="button"
        class="inline-flex items-center gap-2 rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-600 transition-colors hover:border-gray-300 hover:text-gray-800 disabled:cursor-not-allowed disabled:text-gray-400"
        @click="fetchTrace"
        :disabled="!canFetch || isLoading"
      >
        <i class="fa-solid fa-rotate-right text-[11px]"></i>
        刷新
      </button>
    </div>

    <!-- 内容区域 -->
    <div class="flex-1 overflow-auto px-5 py-4">
      <div v-if="events.length === 0" class="text-sm text-gray-500">
        {{ summaryText }}
      </div>
      <div v-else class="space-y-4">
        <div
          v-for="(item, index) in events"
          :key="`${item.event ?? 'event'}-${item.ts_ms ?? index}-${index}`"
          class="rounded-xl border border-gray-200 bg-white p-4 text-xs text-gray-600 shadow-sm"
        >
          <!-- 事件头部 -->
          <div class="flex flex-wrap items-center justify-between gap-2">
            <span class="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-700">
              {{ item.event || 'event' }}
            </span>
            <span class="text-[11px] text-gray-400">
              {{ formatTimestamp(item.ts_ms) }}
            </span>
          </div>

          <!-- 事件内容 -->
          <pre class="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-[11px] text-gray-700">
            {{ JSON.stringify(item.payload ?? {}, null, 2) }}
          </pre>
        </div>
      </div>
    </div>
  </div>
</template>
