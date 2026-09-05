<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { ChatDataTokenUsage, ChatDataTokenUsageBreakdown, Message } from '../types'

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
  startedAtMs?: number | null
  endedAtMs?: number | null
  children?: TokenUsageRow[]
}

interface Props {
  usage: ChatDataTokenUsage | null
  /** 会话已加载消息：token_usage 明细行不再携带正文，展开时凭 message_id 在这里取 */
  messages?: Message[]
  compact?: boolean
  isGenerating?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  messages: () => [],
  compact: false,
  isGenerating: false,
})

const panelOpen = ref(false)
const sortMode = ref<TokenUsageSort>('round')
const expandedMessageKeys = ref<Set<string>>(new Set())
const buttonRef = ref<HTMLElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const panelPosition = ref({ left: 24, bottom: 112 })
const cacheSupportDetected = ref(false)

// 面板层压策略：高于普通页面内容，低于 Element Plus 弹窗/弹层（初始 z-index 2000 起），
// 避免后台管理、语义配置等业务弹窗打开后被本面板遮盖
const PANEL_Z_INDEX = 1900

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
  return `${Math.round(usagePercentage.value)}`
})
const compressionTriggerPercentage = computed(() => {
  const usage = props.usage
  const contextWindow = usage?.context_window
  const triggerTokens = usage?.compression_trigger_tokens
  if (
    typeof contextWindow !== 'number'
    || typeof triggerTokens !== 'number'
    || !Number.isFinite(contextWindow)
    || !Number.isFinite(triggerTokens)
    || contextWindow <= 0
  ) {
    return null
  }
  return Math.max(0, Math.min(100, (triggerTokens / contextWindow) * 100))
})
const ringStyle = computed(() => {
  const percentage = usageDisplay.value ? usagePercentage.value : 0
  const trigger = compressionTriggerPercentage.value
  const color = percentage >= 100 ? '#ef4444' : trigger !== null && percentage >= trigger ? '#f59e0b' : '#1677FF'
  return {
    background: `conic-gradient(${color} ${percentage}%, #d8dee8 0)`,
  }
})
const buttonTitle = computed(() => usageDisplay.value ? `上下文占用：${usageDisplay.value}` : '上下文计算中')
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
  expandedChildKeys.value = new Set()
}

// 点击面板和触发按钮之外的区域时收起面板：
// 打开后台管理、语义模型配置等其他弹窗前必然先点击页面，面板会随之自动收起
const handleDocumentPointerDown = (event: PointerEvent) => {
  if (!panelOpen.value) return
  const target = event.target as Node | null
  if (!target) return
  if (panelRef.value?.contains(target)) return
  if (buttonRef.value?.contains(target)) return
  closePanel()
}

const handleDocumentKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Escape' && panelOpen.value) {
    closePanel()
  }
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

const contextSummary = computed(() => {
  const usage = props.usage
  if (!usage) return ''
  const values = [
    ['当前输入', usage.current_input_tokens ?? usage.input_tokens ?? usage.used],
    ['压缩阈值', usage.compression_trigger_tokens],
    ['上下文窗口', usage.context_window],
  ]
  return values
    .filter(([, value]) => typeof value === 'number' && Number.isFinite(value))
    .map(([label, value]) => `${label} ${formatTokenCount(value as number)}`)
    .join(' · ')
})

const formatTokenCount = (value: number) => formatTokenUsageDisplay({ used: value, total: 1 }).split(' / ')[0] || ''
const formatPercentage = (value: number) => `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`
const cacheUsageRows = computed(() => {
  const usage = props.usage
  if (!cacheSupportDetected.value) return []
  const pendingValue = props.isGenerating ? '统计中' : '--'
  return [
    {
      label: '当前命中',
      value: typeof usage?.current_cached_input_tokens === 'number'
        ? formatTokenCount(usage.current_cached_input_tokens)
        : pendingValue,
    },
    {
      label: '累计命中',
      value: typeof usage?.cumulative_cached_input_tokens === 'number'
        ? formatTokenCount(usage.cumulative_cached_input_tokens)
        : pendingValue,
    },
    {
      label: '当前命中率',
      value: typeof usage?.current_cache_hit_rate === 'number'
        ? formatPercentage(usage.current_cache_hit_rate)
        : pendingValue,
    },
    {
      label: '累计命中率',
      value: typeof usage?.cumulative_cache_hit_rate === 'number'
        ? formatPercentage(usage.cumulative_cache_hit_rate)
        : pendingValue,
    },
  ]
})

watch(
  [() => props.usage, () => props.isGenerating],
  ([usage]) => {
    if (!usage) {
      cacheSupportDetected.value = false
      return
    }
    if (
      typeof usage?.current_cached_input_tokens === 'number'
      || typeof usage?.cumulative_cached_input_tokens === 'number'
      || typeof usage?.current_cache_hit_rate === 'number'
      || typeof usage?.cumulative_cache_hit_rate === 'number'
    ) {
      cacheSupportDetected.value = true
    }
  },
  { immediate: true },
)

const messageById = computed(() => {
  const map = new Map<string, Message>()
  for (const message of props.messages || []) {
    if (message.id) map.set(message.id, message)
  }
  return map
})

const toolResultByCallId = computed(() => {
  const map = new Map<string, string>()
  for (const message of props.messages || []) {
    if (message.role === 'tool' && message.toolCallId && message.content) {
      map.set(message.toolCallId, message.content)
    }
    for (const call of message.toolCalls || []) {
      if (call.tool_call_id && call.result != null && call.result !== '') {
        map.set(call.tool_call_id, String(call.result))
      }
    }
  }
  return map
})

// 明细展示用文本还原：正文是 content blocks 数组的序列化字符串，提取各对象的 text 字段并还原换行
const formatBreakdownText = (raw: string): string => {
  const text = (raw || '').trim()
  if (!text.startsWith('[') && !text.startsWith('{')) return text
  const parts: string[] = []
  const re = /['"]text['"]\s*:\s*(['"])((?:\\.|(?!\1).)*)\1/g
  let match: RegExpExecArray | null = re.exec(text)
  while (match !== null) {
    parts.push(match[2].replace(/\\n/g, '\n'))
    match = re.exec(text)
  }
  return parts.length ? parts.join('\n\n') : text
}

// token_usage payload 只带计数，正文从会话消息里按 message_id / tool_call_id 反查
const resolveBreakdownContent = (item: ChatDataTokenUsageBreakdown): string => {
  if (item.content) return item.content // 兼容仍携带正文的旧数据
  if (item.redacted) return item.description || ''
  if (item.tool_call_id) {
    const result = toolResultByCallId.value.get(item.tool_call_id)
    if (result) return result
  }
  if (item.message_id) {
    const message = messageById.value.get(item.message_id)
    if (message) {
      const parts: string[] = []
      if (message.content) parts.push(message.content)
      for (const call of message.toolCalls || []) {
        parts.push(`工具调用 ${call.tool_name} ${JSON.stringify(call.args ?? {})}`)
      }
      if (parts.length) return parts.join('\n')
    }
  }
  return item.description || ''
}

const toTokenUsageRow = (item: ChatDataTokenUsageBreakdown, labelPrefix = '', keyPrefix = ''): TokenUsageRow => {
  const content = formatBreakdownText(resolveBreakdownContent(item))
  // 子项 label 已含父项前缀时（如"工具定义 · file_read"）不再重复拼接
  const label = labelPrefix && !item.label.startsWith(labelPrefix) ? `${labelPrefix} · ${item.label}` : item.label
  return {
    key: keyPrefix ? `${keyPrefix}_${item.key}` : item.key,
    label,
    tokens: item.tokens,
    percentage: props.usage?.used ? Math.round((item.tokens / props.usage.used) * 1000) / 10 : 0,
    description: item.description || content.slice(0, 120),
    content,
    role: item.role,
    round: item.round,
    messageIndex: item.message_index,
    startedAtMs: item.started_at_ms,
    endedAtMs: item.ended_at_ms,
  }
}

// AI 行为模型调用耗时、工具行为工具执行耗时（起止时间相减），其余行无值显示 -
const formatDuration = (item: Pick<TokenUsageRow, 'startedAtMs' | 'endedAtMs'>): string => {
  const { startedAtMs, endedAtMs } = item
  if (
    typeof startedAtMs !== 'number' || !Number.isFinite(startedAtMs)
    || typeof endedAtMs !== 'number' || !Number.isFinite(endedAtMs)
    || endedAtMs < startedAtMs
  ) {
    return '-'
  }
  const durationMs = endedAtMs - startedAtMs
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`
  return `${(durationMs / 1000).toFixed(1)}s`
}

const formatTimingTitle = (item: TokenUsageRow): string => {
  if (typeof item.startedAtMs !== 'number' || typeof item.endedAtMs !== 'number') return ''
  const format = (ms: number) => new Date(ms).toLocaleTimeString('zh-CN', { hour12: false })
  const scope = item.role === 'tool' ? '工具执行' : '模型调用'
  return `${scope} ${format(item.startedAtMs)} ~ ${format(item.endedAtMs)}`
}

const messageRows = computed(() => {
  const rows: TokenUsageRow[] = []
  for (const item of breakdownRows.value) {
    if (!item.children?.length) continue
    if (item.key === 'tool_schema') {
      rows.push({
        ...toTokenUsageRow(item, '', item.key),
        messageIndex: rows.length,
        // 父行已标明「工具定义」，子行只留工具名
        children: item.children.map(child => ({
          ...toTokenUsageRow(child, '', item.key),
          label: child.label.replace(/^工具定义\s*·\s*/, ''),
        })),
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

const expandedChildKeys = ref<Set<string>>(new Set())

const toggleChild = (key: string) => {
  const next = new Set(expandedChildKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedChildKeys.value = next
}

const getTypeLabel = (item: TokenUsageRow): string => {
  if (item.key.includes('request_overhead')) return '协议'
  if (item.key.includes('tool_schema')) return '工具'
  const labels: Record<string, string> = {
    system: '系统',
    human: '用户',
    ai: 'AI',
    assistant: 'AI',
    tool: '工具',
    tool_schema: '工具定义',
  }
  return labels[item.role || ''] || '其他'
}

const getTypeClass = (item: TokenUsageRow): string => {
  if (item.key.includes('request_overhead')) return 'bg-slate-100 text-slate-600'
  if (item.key.includes('tool_schema')) return 'bg-amber-50 text-amber-700'
  const classes: Record<string, string> = {
    system: 'bg-violet-50 text-violet-600',
    human: 'bg-blue-50 text-blue-600',
    ai: 'bg-emerald-50 text-emerald-600',
    assistant: 'bg-emerald-50 text-emerald-600',
    tool: 'bg-amber-50 text-amber-700',
    tool_schema: 'bg-slate-100 text-slate-600',
  }
  return classes[item.role || ''] || 'bg-slate-100 text-slate-600'
}

const getTypeTitle = (item: TokenUsageRow): string => (
  item.key.includes('request_overhead')
    ? '模型请求协议开销'
    : item.key.includes('tool_schema')
      ? '本次请求传入模型的工具定义'
      : getTypeLabel(item)
)

const getRowContent = (item: TokenUsageRow): string => {
  if (item.key.includes('request_overhead')) return '请求协议开销'
  if (item.key.includes('tool_schema')) return `工具定义（${item.children?.length || 0} 个）`
  return item.description || item.label
}

onMounted(() => {
  window.addEventListener('resize', handleViewportChange)
  window.addEventListener('scroll', handleViewportChange, true)
  document.addEventListener('pointerdown', handleDocumentPointerDown, true)
  document.addEventListener('keydown', handleDocumentKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleViewportChange)
  window.removeEventListener('scroll', handleViewportChange, true)
  document.removeEventListener('pointerdown', handleDocumentPointerDown, true)
  document.removeEventListener('keydown', handleDocumentKeydown)
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
      ref="panelRef"
      class="fixed max-h-[calc(100vh-32px)] overflow-y-auto rounded-lg border border-gray-200 bg-white p-3 text-xs text-gray-600 shadow-lg"
      :style="{ ...panelStyle, zIndex: PANEL_Z_INDEX }"
    >
      <div class="flex items-center justify-between mb-2">
        <div class="font-semibold text-gray-800">上下文占用明细</div>
        <button type="button" class="border-none bg-transparent text-gray-400 cursor-pointer hover:text-gray-600" @click="closePanel">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="mb-2">
        <div class="mb-1 flex items-center justify-between gap-3 text-[11px] text-gray-400">
          <span class="min-w-0 truncate" :title="contextSummary">{{ contextSummary }}</span>
          <span class="shrink-0">{{ usagePercentageText }}%</span>
        </div>
        <div class="relative h-1.5 overflow-hidden rounded-full bg-gray-100">
          <div class="h-full rounded-full bg-blue-400" :style="{ width: `${usagePercentage}%` }"></div>
          <span
            v-if="compressionTriggerPercentage !== null"
            class="absolute top-0 h-full w-px bg-amber-500"
            :style="{ left: `${compressionTriggerPercentage}%` }"
            title="自动压缩阈值"
          ></span>
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
        <div class="max-h-[250px] overflow-auto rounded border border-gray-100 bg-white">
          <div class="sticky top-0 z-10 grid grid-cols-[minmax(0,1fr)_minmax(72px,0.5fr)_34px_42px_44px_48px] items-center gap-2 border-b border-gray-100 bg-slate-50 px-2 py-1 text-[10px] text-gray-400">
            <span>内容</span>
            <span>占比</span>
            <span class="text-center">轮次</span>
            <span class="text-center">类型</span>
            <span class="text-right">耗时</span>
            <span class="text-right">Token</span>
          </div>
          <div v-for="item in messageRows" :key="item.key" class="border-b border-gray-50 last:border-b-0">
            <button type="button" class="grid w-full grid-cols-[minmax(0,1fr)_minmax(72px,0.5fr)_34px_42px_44px_48px] items-center gap-2 border-none bg-transparent px-2 py-1 text-left text-[11px] cursor-pointer hover:bg-slate-50" @click="toggleMessage(item.key)">
              <span class="truncate text-gray-700" :title="item.content || getRowContent(item)">{{ getRowContent(item) }}</span>
              <span class="flex min-w-0 items-center gap-1.5">
                <span class="h-1 flex-1 overflow-hidden rounded-full bg-gray-100">
                  <span class="block h-full rounded-full bg-blue-400" :style="{ width: `${Math.min(Math.abs(item.percentage), 100)}%` }"></span>
                </span>
                <span class="w-8 text-right font-mono text-[10px] text-gray-400">{{ item.percentage.toFixed(item.percentage % 1 === 0 ? 0 : 1) }}%</span>
              </span>
              <span class="text-center text-gray-400">{{ item.round || '-' }}</span>
              <span :class="['justify-self-center whitespace-nowrap rounded px-1 py-0.5 text-[10px]', getTypeClass(item)]" :title="getTypeTitle(item)">{{ getTypeLabel(item) }}</span>
              <span class="text-right font-mono text-[10px] text-gray-400" :title="formatTimingTitle(item)">{{ formatDuration(item) }}</span>
              <span class="text-right font-mono text-gray-500">{{ formatTokenUsageDisplay({ used: item.tokens, total: 1 }).split(' / ')[0] }}</span>
            </button>
            <div v-if="expandedMessageKeys.has(item.key) && item.children?.length" class="space-y-1 border-t border-gray-100 bg-slate-50 px-2 py-1.5">
              <div v-for="child in item.children" :key="child.key" class="rounded bg-white">
                <button
                  type="button"
                  class="flex w-full items-center justify-between gap-2 border-none bg-transparent p-1.5 text-left text-[11px] text-gray-500 cursor-pointer hover:bg-slate-50"
                  @click="toggleChild(child.key)"
                >
                  <span class="flex min-w-0 items-center gap-1">
                    <i :class="['fa-solid text-[9px] text-gray-400', expandedChildKeys.has(child.key) ? 'fa-chevron-down' : 'fa-chevron-right']"></i>
                    <span class="truncate">{{ child.label }}</span>
                  </span>
                  <span class="shrink-0 font-mono">{{ formatTokenUsageDisplay({ used: child.tokens, total: 1 }).split(' / ')[0] }}</span>
                </button>
                <pre
                  v-if="expandedChildKeys.has(child.key)"
                  class="m-0 max-h-48 overflow-auto whitespace-pre-wrap border-t border-gray-100 px-1.5 py-1 text-[11px] leading-relaxed text-gray-600"
                >{{ child.content || child.description || '' }}</pre>
              </div>
            </div>
            <pre v-else-if="expandedMessageKeys.has(item.key)" class="max-h-40 overflow-auto whitespace-pre-wrap border-t border-gray-100 bg-slate-50 px-2 py-1.5 text-[11px] leading-relaxed text-gray-600">{{ item.content || item.description || '' }}</pre>
          </div>
        </div>
      </div>
      <div v-else class="text-gray-400">暂无可拆分明细。</div>
    </div>
  </div>
</template>
