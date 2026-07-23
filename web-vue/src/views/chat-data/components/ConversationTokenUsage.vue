<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ChatDataTokenUsage, ChatDataTokenUsageBreakdown } from '../types'

type TokenUsageSort = 'round' | 'desc' | 'asc'

type TokenUsageRow = {
  key: string
  label: string
  tokens: number
  percentage: number
  description?: string
  content?: string
  role?: string
  round?: number
  messageIndex?: number
  children?: TokenUsageRow[]
}

interface Props {
  usage: ChatDataTokenUsage | null
  compact?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  compact: false,
})

const panelOpen = ref(false)
const sortMode = ref<TokenUsageSort>('round')
const expandedMessageKeys = ref<Set<string>>(new Set())
const buttonRef = ref<HTMLElement | null>(null)
const panelPosition = ref({ left: 24, bottom: 112 })

const PANEL_WIDTH = 460
const PANEL_MARGIN = 16

const formatTokenUsageDisplay = (usage: Pick<ChatDataTokenUsage, 'used' | 'total' | 'estimated'> | null) => {
  if (!usage || !Number.isFinite(usage.used) || !Number.isFinite(usage.total) || usage.total <= 0) {
    return ''
  }
  const formatValue = (value: number) => {
    if (value >= 1000000) {
      const scaled = value / 1000000
      return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}m`
    }
    if (value >= 1000) {
      const scaled = value / 1000
      return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}k`
    }
    return String(Math.round(value))
  }
  return `${usage.estimated ? '约 ' : ''}${formatValue(usage.used)} / ${formatValue(usage.total)}`
}

const usageDisplay = computed(() => formatTokenUsageDisplay(props.usage))
const usagePercentage = computed(() => {
  const usage = props.usage
  if (!usage || !Number.isFinite(usage.used) || !Number.isFinite(usage.total) || usage.total <= 0) return 0
  const percentage = Number.isFinite(usage.percentage)
    ? Number(usage.percentage)
    : Math.round((usage.used / usage.total) * 1000) / 10
  return Math.max(0, Math.min(100, percentage))
})
const usagePercentageText = computed(() => {
  if (!usageDisplay.value) return ''
  const rounded = Math.round(usagePercentage.value)
  return props.compact && rounded >= 100 ? '!' : `${rounded}`
})
const ringStyle = computed(() => {
  const percentage = usageDisplay.value ? usagePercentage.value : 0
  const color = percentage >= 90 ? '#ef4444' : percentage >= 70 ? '#f59e0b' : '#1677FF'
  return {
    background: `conic-gradient(${color} ${percentage}%, #d8dee8 0)`,
  }
})
const buttonTitle = computed(() => usageDisplay.value ? `输入预算占用：${usageDisplay.value}` : '上下文计算中')
const panelStyle = computed(() => ({
  left: `${panelPosition.value.left}px`,
  bottom: `${panelPosition.value.bottom}px`,
  width: `${PANEL_WIDTH}px`,
  maxWidth: `calc(100vw - ${PANEL_MARGIN * 2}px)`,
}))

const updatePanelPosition = () => {
  const rect = buttonRef.value?.getBoundingClientRect()
  if (!rect || typeof window === 'undefined') return
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || PANEL_WIDTH
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0
  const maxLeft = Math.max(PANEL_MARGIN, viewportWidth - PANEL_WIDTH - PANEL_MARGIN)
  panelPosition.value = {
    left: Math.min(Math.max(rect.right - PANEL_WIDTH, PANEL_MARGIN), maxLeft),
    bottom: Math.max(PANEL_MARGIN, viewportHeight - rect.top + 8),
  }
}

const togglePanel = () => {
  if (!usageDisplay.value) return
  if (!panelOpen.value) {
    updatePanelPosition()
  }
  panelOpen.value = !panelOpen.value
}

const closePanel = () => {
  panelOpen.value = false
  expandedMessageKeys.value = new Set()
}

const handleViewportChange = () => {
  if (panelOpen.value) {
    updatePanelPosition()
  }
}

const breakdownRows = computed(() => {
  const usage = props.usage
  if (!usage?.breakdown?.length) return []
  return usage.breakdown
    .filter(item => Number.isFinite(item.tokens) && item.tokens !== 0)
    .map(item => ({
      ...item,
      percentage: usage.used > 0 ? Math.round((item.tokens / usage.used) * 1000) / 10 : 0,
    }))
})

const detailRows = computed(() => {
  const usage = props.usage
  if (!usage) return []
  const rows = [
    { label: '当前输入', value: usage.current_input_tokens ?? usage.input_tokens ?? usage.used },
    { label: '可用输入预算', value: usage.total },
    { label: '模型上下文窗口', value: usage.context_window },
    { label: '输出预留', value: usage.max_output_tokens },
  ]
  return rows.filter((row): row is { label: string; value: number } => (
    typeof row.value === 'number' && Number.isFinite(row.value)
  ))
})

const formatTokenCount = (value: number) => formatTokenUsageDisplay({ used: value, total: 1 }).split(' / ')[0] || ''
const formatPercentage = (value: number) => `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`
const cacheUsageRows = computed(() => {
  const usage = props.usage
  if (!usage || typeof usage.cumulative_cached_input_tokens !== 'number') return []
  const rows = [] as Array<{ label: string; value: string }>
  if (typeof usage.current_cached_input_tokens === 'number') {
    rows.push({ label: '当前命中', value: formatTokenCount(usage.current_cached_input_tokens) })
  }
  rows.push({ label: '累计命中', value: formatTokenCount(usage.cumulative_cached_input_tokens) })
  if (typeof usage.current_cache_hit_rate === 'number') {
    rows.push({ label: '当前命中率', value: formatPercentage(usage.current_cache_hit_rate) })
  }
  if (typeof usage.cumulative_cache_hit_rate === 'number') {
    rows.push({ label: '累计命中率', value: formatPercentage(usage.cumulative_cache_hit_rate) })
  }
  return rows
})

const toTokenUsageRow = (item: ChatDataTokenUsageBreakdown, labelPrefix = '', keyPrefix = ''): TokenUsageRow => ({
  key: keyPrefix ? `${keyPrefix}_${item.key}` : item.key,
  label: labelPrefix ? `${labelPrefix} · ${item.label}` : item.label,
  tokens: item.tokens,
  percentage: props.usage?.used ? Math.round((item.tokens / props.usage.used) * 1000) / 10 : 0,
  description: item.description,
  content: item.content || item.description,
  role: item.role,
  round: item.round,
  messageIndex: item.message_index,
})

const messageRows = computed(() => {
  const rows: TokenUsageRow[] = []
  for (const item of breakdownRows.value) {
    if (!item.children?.length) continue
    if (item.key === 'tool_schema') {
      rows.push({
        ...toTokenUsageRow(item, '', item.key),
        messageIndex: rows.length,
        children: item.children.map(child => toTokenUsageRow(child, item.label, item.key)),
      })
      continue
    }
    for (const child of item.children || []) {
      if (!Number.isFinite(child.tokens) || child.tokens === 0) continue
      rows.push({ ...toTokenUsageRow(child, item.label, item.key), messageIndex: rows.length })
    }
  }
  if (!rows.length) {
    rows.push(...breakdownRows.value.map(item => toTokenUsageRow(item)))
  }
  if (sortMode.value === 'desc') {
    return [...rows].sort((a, b) => b.tokens - a.tokens)
  }
  if (sortMode.value === 'asc') {
    return [...rows].sort((a, b) => a.tokens - b.tokens)
  }
  return [...rows].sort((a, b) => {
    const roundDiff = (a.round ?? 0) - (b.round ?? 0)
    if (roundDiff !== 0) return roundDiff
    return (a.messageIndex ?? 0) - (b.messageIndex ?? 0)
  })
})

const toggleMessage = (key: string) => {
  const next = new Set(expandedMessageKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedMessageKeys.value = next
}

onMounted(() => {
  window.addEventListener('resize', handleViewportChange)
  window.addEventListener('scroll', handleViewportChange, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleViewportChange)
  window.removeEventListener('scroll', handleViewportChange, true)
})

defineExpose({
  closePanel,
})
</script>

<template>
  <div v-if="usageDisplay || compact" class="relative">
    <button
      ref="buttonRef"
      type="button"
      :class="[
        'flex shrink-0 items-center rounded-full border-none bg-transparent text-xs text-gray-500 cursor-pointer hover:bg-gray-100 disabled:cursor-default disabled:hover:bg-transparent',
        compact ? 'h-5 px-0' : 'h-7 gap-1.5 px-1.5'
      ]"
      :disabled="!usageDisplay"
      :title="buttonTitle"
      @click="togglePanel"
    >
      <span
        :class="[
          'relative flex shrink-0 items-center justify-center rounded-full',
          compact ? 'h-4 w-4' : 'h-5 w-5'
        ]"
        :style="ringStyle"
      >
        <span
          :class="[
            'flex items-center justify-center rounded-full bg-white font-semibold leading-none text-gray-500',
            compact ? 'h-3 w-3 text-[9px]' : 'h-4 w-4 text-[8px]'
          ]"
        >
          {{ usagePercentageText }}
        </span>
      </span>
      <span v-if="!compact" class="toolbar-label">上下文</span>
    </button>
    <div
      v-if="panelOpen"
      class="fixed z-[10000] max-h-[calc(100vh-32px)] overflow-y-auto rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-600 shadow-lg"
      :style="panelStyle"
    >
      <div class="flex items-center justify-between mb-2">
        <div class="font-semibold text-gray-800">输入预算明细</div>
        <button type="button" class="border-none bg-transparent text-gray-400 cursor-pointer hover:text-gray-600" @click="closePanel">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="grid grid-cols-2 gap-2 mb-2 sm:grid-cols-4">
        <div v-for="row in detailRows" :key="row.label" class="rounded bg-gray-50 px-2 py-1">
          <div class="text-[11px] text-gray-400">{{ row.label }}</div>
          <div class="font-medium text-gray-700">{{ formatTokenUsageDisplay({ used: row.value, total: 1 }).split(' / ')[0] }}</div>
        </div>
      </div>
      <div class="mb-2">
        <div class="mb-1 flex items-center justify-between text-[11px] text-gray-400">
          <span>输入预算占用</span>
          <span>{{ usageDisplay }} · {{ usagePercentageText }}%</span>
        </div>
        <div class="h-1.5 overflow-hidden rounded-full bg-gray-100">
          <div class="h-full rounded-full bg-blue-400" :style="{ width: `${usagePercentage}%` }"></div>
        </div>
      </div>
      <div v-if="cacheUsageRows.length" class="mb-2 border-t border-gray-100 pt-2">
        <div class="mb-1 text-[11px] text-gray-400">模型缓存</div>
        <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div v-for="row in cacheUsageRows" :key="row.label" class="rounded bg-emerald-50 px-2 py-1">
            <div class="text-[11px] text-emerald-600">{{ row.label }}</div>
            <div class="font-medium text-emerald-800">{{ row.value }}</div>
          </div>
        </div>
      </div>
      <div v-if="messageRows.length" class="space-y-1.5">
        <div class="flex items-center justify-between">
          <span class="text-[11px] text-gray-400">本次输入内容</span>
          <div class="inline-flex rounded bg-gray-100 p-0.5">
            <button
              type="button"
              class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
              :class="sortMode === 'round' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
              @click="sortMode = 'round'"
            >
              时序
            </button>
            <button
              type="button"
              class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
              :class="sortMode === 'desc' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
              @click="sortMode = 'desc'"
            >
              Token↓
            </button>
            <button
              type="button"
              class="border-none rounded px-2 py-0.5 text-[11px] cursor-pointer"
              :class="sortMode === 'asc' ? 'bg-white text-gray-700 shadow-sm' : 'bg-transparent text-gray-400'"
              @click="sortMode = 'asc'"
            >
              Token↑
            </button>
          </div>
        </div>
        <div class="max-h-[220px] overflow-auto pr-1 space-y-1.5">
          <div v-for="item in messageRows" :key="item.key" class="rounded border border-gray-100 bg-white px-2 py-1">
            <button type="button" class="w-full border-none bg-transparent p-0 text-left cursor-pointer" @click="toggleMessage(item.key)">
              <div class="flex items-center justify-between gap-2">
                <span class="text-gray-700">{{ item.label }}</span>
                <span class="font-mono text-gray-500">{{ formatTokenUsageDisplay({ used: item.tokens, total: 1 }).split(' / ')[0] }}</span>
              </div>
              <div class="mt-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                <div class="h-full rounded-full bg-blue-400" :style="{ width: `${Math.min(Math.abs(item.percentage), 100)}%` }"></div>
              </div>
              <div v-if="item.description && !expandedMessageKeys.has(item.key)" class="mt-1 truncate text-[11px] text-gray-400">{{ item.description }}</div>
            </button>
            <div v-if="expandedMessageKeys.has(item.key) && item.children?.length" class="mt-2 space-y-1.5">
              <div v-for="child in item.children" :key="child.key" class="rounded bg-gray-50 p-2">
                <div class="flex items-center justify-between gap-2 text-[11px] text-gray-500">
                  <span>{{ child.label }}</span>
                  <span class="font-mono">{{ formatTokenUsageDisplay({ used: child.tokens, total: 1 }).split(' / ')[0] }}</span>
                </div>
                <pre class="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] leading-relaxed text-gray-600">{{ child.content || child.description || '' }}</pre>
              </div>
            </div>
            <pre v-else-if="expandedMessageKeys.has(item.key)" class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-[11px] leading-relaxed text-gray-600">{{ item.content || item.description || '' }}</pre>
          </div>
        </div>
      </div>
      <div v-else class="text-gray-400">暂无可拆分明细。</div>
    </div>
  </div>
</template>
