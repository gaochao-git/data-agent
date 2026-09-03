<script setup lang="ts">
/**
 * 工具调用列表组件
 * 用于在消息中展示多个工具调用
 */
import { computed, nextTick, ref, watch } from 'vue'
import ToolCallCard from './ToolCallCard.vue'
import { getToolCompactDescription, getToolDisplayName } from '../utils/toolDisplay'
import { renderMarkdown } from '../utils'
import type { ExecutionTargetOption, SubagentTrailItem, ToolCallInfo } from '../types'

interface Props {
  toolCalls: ToolCallInfo[]
  datasourceId?: string
  executionTargets?: ExecutionTargetOption[]
  defaultExpanded?: boolean
  compact?: boolean
  showRunningIndicator?: boolean
  renderQueryResultPreview?: boolean
  activeToolCallId?: string | null
  onViewAnalysis?: () => void
  isAnalysisActive?: boolean
  conversationId?: string | null
}

const props = withDefaults(defineProps<Props>(), {
  defaultExpanded: false,
  compact: false,
  showRunningIndicator: true,
  renderQueryResultPreview: true,
  activeToolCallId: null,
  isAnalysisActive: false
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
}>()

// 展开状态
const expandedIds = ref<Set<string>>(new Set())

// 初始化展开状态
watch(
  () => props.toolCalls,
  (newToolCalls) => {
    if (props.defaultExpanded) {
      expandedIds.value = new Set(newToolCalls.map((tc) => tc.tool_call_id))
    } else {
      expandedIds.value = new Set([...expandedIds.value].filter((id) => newToolCalls.some((tc) => tc.tool_call_id === id)))
    }
  },
  { immediate: true }
)

// 切换展开状态
const toggleExpand = (id: string) => {
  if (props.compact) return
  const newSet = new Set(expandedIds.value)
  if (newSet.has(id)) {
    newSet.delete(id)
  } else {
    newSet.add(id)
  }
  expandedIds.value = newSet
}

// 子智能体内部过程：task 卡片下方独立区域，工具与中间文本按发生顺序混排，
// 整块可折叠（运行中展开、跑完自动收起，手动点过则听用户的）。
// 内层工具的参数与结果不在此区展示；点击 chip 在右侧详情面板查看——与普通
// 工具走同一条 selectTool 链路（同一面板、同样的再点一次收起行为），区别只在
// 内容来自 SSE 转发的截断预览（后端限长），并非完整原文。

interface TrailView {
  kind: 'tool' | 'message'
  key: string
  // 工具条目
  toolCall?: ToolCallInfo
  displayName?: string
  compactDescription?: string
  isRunning?: boolean
  isError?: boolean
  // 文本条目
  content?: string
  // 正文的 markdown 渲染结果（与主智能体共用 utils/markdown 的渲染器）
  contentHtml?: string
  truncated?: boolean
  // 思考过程（默认收起；父 task 仍在运行时按钮行露尾部预览）
  thinking?: string
  thinkingTruncated?: boolean
  thinkingRunning?: boolean
}

// 流式期间每个 token 增量都会换一整份 props.toolCalls，若逐帧对全部条目重跑
// markdown 渲染，成本是「条目数 × 累计字数」/token。按条目缓存渲染结果：只有
// 内容真正变化的条目才重算。非响应式 Map，增删不触发渲染。
const markdownCache = new Map<string, { source: string; html: string }>()

const renderTrailMarkdown = (key: string, content: string): string => {
  const cached = markdownCache.get(key)
  if (cached && cached.source === content) return cached.html
  const html = renderMarkdown(content)
  markdownCache.set(key, { source: content, html })
  return html
}

const buildTrailView = (item: SubagentTrailItem, isParentRunning: boolean): TrailView => {
  if (item.kind === 'message') {
    const content = item.content || ''
    return {
      kind: 'message',
      key: item.id,
      content,
      contentHtml: renderTrailMarkdown(item.id, content),
      truncated: Boolean(item.truncated),
      thinking: item.thinking || '',
      thinkingTruncated: Boolean(item.thinkingTruncated),
      thinkingRunning: isParentRunning,
    }
  }
  return {
    kind: 'tool',
    key: item.id,
    toolCall: {
      tool_call_id: item.id,
      tool_name: item.tool_name || 'tool_call',
      args: item.args || {},
      result: item.result,
      status: item.status || 'running',
    },
    displayName: getToolDisplayName(item.tool_name || ''),
    compactDescription: getToolCompactDescription(item.tool_name || '', item.args),
    isRunning: item.status === 'running',
    isError: item.status === 'error',
  }
}

const trailViewsByToolCallId = computed(() => {
  const views = new Map<string, TrailView[]>()
  const aliveKeys = new Set<string>()
  for (const toolCall of props.toolCalls) {
    const trail = toolCall.tool_name === 'task' ? toolCall.subagentTrail : undefined
    if (!trail?.length) continue
    const isParentRunning = toolCall.status === 'running' || toolCall.status === 'generating'
    for (const item of trail) {
      if (item.kind === 'message') aliveKeys.add(item.id)
    }
    views.set(toolCall.tool_call_id, trail.map((item) => buildTrailView(item, isParentRunning)))
  }
  // 条目消失（如轨迹重建）后回收缓存，避免长会话累积
  for (const key of markdownCache.keys()) {
    if (!aliveKeys.has(key)) markdownCache.delete(key)
  }
  return views
})

const trailViewsOf = (toolCallId: string): TrailView[] => trailViewsByToolCallId.value.get(toolCallId) || []

const onSubToolClick = (toolCall: ToolCallInfo) => {
  emit('selectTool', toolCall)
}

// ---- 子智能体过程区折叠 ----
// 默认策略：运行中展开（过程可见是这个功能本身的价值），跑完自动收起
// （结论已作为 task 的 ToolMessage 落在主对话里，过程不占屏）。用户一旦
// 手动点过，就完全听用户的，不再被自动策略覆盖。
// 折叠只隐藏过程区，不触碰任何选中状态——右侧面板的归属与内容不受影响，
// 内层 chip 点击仍与普通工具一致地走 selectTool。
const collapsedChainIds = ref<Set<string>>(new Set())
const userTouchedChains = ref<Set<string>>(new Set())

const isChainRunning = (tc: ToolCallInfo) => tc.status === 'running' || tc.status === 'generating'

const isChainCollapsed = (tc: ToolCallInfo): boolean => {
  if (userTouchedChains.value.has(tc.tool_call_id)) {
    return collapsedChainIds.value.has(tc.tool_call_id)
  }
  return !isChainRunning(tc)
}

const toggleChain = (toolCallId: string) => {
  const target = props.toolCalls.find((tc) => tc.tool_call_id === toolCallId)
  if (!target) return
  const shouldCollapse = !isChainCollapsed(target)
  userTouchedChains.value = new Set(userTouchedChains.value).add(toolCallId)
  const collapsed = new Set(collapsedChainIds.value)
  if (shouldCollapse) {
    collapsed.add(toolCallId)
  } else {
    collapsed.delete(toolCallId)
  }
  collapsedChainIds.value = collapsed
}

// 折叠后过程不可见，仅在内层工具失败时补一个标记，避免异常被完全藏起来
const chainHasError = (tc: ToolCallInfo) =>
  (tc.subagentTrail || []).some((item) => item.kind === 'tool' && item.status === 'error')

// 当前正在执行的内层工具名。内层工具可能执行得很快（读文件/走缓存的检索
// 常在亚秒级完成），chip 上的小 spinner 会一闪而过；标题行始终可见，把
// 「正在执行什么」落在这里，用户才感知得到子智能体没有卡住。
const runningToolName = (tc: ToolCallInfo): string => {
  if (!isChainRunning(tc)) return ''
  const trail = tc.subagentTrail || []
  for (let index = trail.length - 1; index >= 0; index -= 1) {
    const item = trail[index]
    if (item?.kind === 'tool' && item.status === 'running') {
      return getToolDisplayName(item.tool_name || '')
    }
  }
  return ''
}

// ---- 子智能体思考过程：与主对话口径一致，默认收起；流式进行中按钮行露尾部预览 ----
const THINKING_TAIL_PREVIEW_LENGTH = 80

const expandedThinkingIds = ref<Set<string>>(new Set())

const toggleThinking = (key: string) => {
  const newSet = new Set(expandedThinkingIds.value)
  if (newSet.has(key)) {
    newSet.delete(key)
  } else {
    newSet.add(key)
  }
  expandedThinkingIds.value = newSet
}

const thinkingTailPreview = (thinking: string) => {
  const flattened = thinking.replace(/\s+/g, ' ').trimEnd()
  if (flattened.length <= THINKING_TAIL_PREVIEW_LENGTH) return flattened
  return `…${flattened.slice(-THINKING_TAIL_PREVIEW_LENGTH)}`
}

// ---- 子智能体链路滚动容器：粘底跟随，与主对话 isNearBottom 同款机制 ----
// 参照主对话实现（AnalysisConversation 的 handleScroll + isNearBottom）：
// 跟随状态只在 @scroll 事件里重算（用户滚动或程序化滚动），内容增长不翻转
// 状态——流式段落一次长高超过阈值不会误判为"用户在上翻浏览历史"；用户主动
// 上滚离开底部 → 停止跟随；滚回底部附近 → 自动恢复跟随。
const CHAIN_NEAR_BOTTOM_PX = 100

const chainScrollEls = new Map<string, HTMLElement>()
// 非响应式粘底标志：仅驱动命令式滚动。Vue 内联函数 ref 每次重渲染都会以
// null → el 重新调用，粘底状态必须与 ref 回调解耦，否则会被反复重置。
const chainSticky = new Map<string, boolean>()
const chainScrollHandlers = new Map<string, (event: Event) => void>()

const setChainScrollEl = (id: string) => (el: unknown) => {
  if (el instanceof HTMLElement) {
    if (!chainScrollHandlers.has(id)) {
      chainScrollHandlers.set(id, (event: Event) => {
        const target = event.target as HTMLElement
        chainSticky.set(id, target.scrollHeight - target.scrollTop - target.clientHeight < CHAIN_NEAR_BOTTOM_PX)
      })
    }
    // addEventListener 对同一 (el, handler) 幂等；重渲染以 null → el 重复调用无害
    el.addEventListener("scroll", chainScrollHandlers.get(id)!)
    // 首次挂载默认跟随（等价于主对话 isNearBottom 初始为 true）
    if (!chainSticky.has(id)) {
      chainSticky.set(id, true)
    }
    chainScrollEls.set(id, el)
  } else {
    const attached = chainScrollEls.get(id)
    const handler = chainScrollHandlers.get(id)
    if (attached && handler) {
      attached.removeEventListener("scroll", handler)
    }
    chainScrollEls.delete(id)
  }
}

const scrollChainsToLatest = async () => {
  await nextTick()
  for (const [id, el] of chainScrollEls.entries()) {
    if (!chainSticky.get(id)) continue
    el.scrollTop = el.scrollHeight
  }
}

// 轨迹签名：任何条目新增、工具状态变化或流式文本增长都触发一次跟随滚动
watch(
  () => props.toolCalls
    .map((tc) => (tc.subagentTrail || []).map((item) => {
      if (item.kind !== 'tool') {
        return `m:${item.id}:${(item.content || '').length}:${(item.thinking || '').length}`
      }
      return `${item.id}:${item.status ?? 'running'}`
    }).join('|'))
    .join(';'),
  () => { void scrollChainsToLatest() },
  { immediate: true }
)
</script>

<template>
  <div v-if="toolCalls.length > 0" class="flex min-w-0 max-w-full flex-col gap-2">
    <template v-for="tc in toolCalls" :key="tc.tool_call_id">
      <ToolCallCard
        :tool-call="tc"
        :is-expanded="expandedIds.has(tc.tool_call_id)"
        :compact="compact"
        :show-running-indicator="showRunningIndicator"
        :render-query-result-preview="renderQueryResultPreview"
        :active="activeToolCallId === tc.tool_call_id"
        :datasource-id="datasourceId"
        :execution-targets="executionTargets"
        :on-view-analysis="tc.tool_name === 'semantic_query_tool' && tc.renderId && onViewAnalysis ? onViewAnalysis : undefined"
        :is-analysis-active="Boolean(tc.tool_name === 'semantic_query_tool' && tc.renderId && isAnalysisActive)"
        :conversation-id="conversationId"
        @toggle-expand="toggleExpand(tc.tool_call_id)"
        @select="emit('selectTool', tc)"
      />

      <!-- 子智能体内部过程：作为 task 卡片下方的独立视觉区域 -->
      <div
        v-if="tc.tool_name === 'task' && (tc.subagentTrail?.length ?? 0) > 0"
        class="subagent-chain ml-3 mt-1 flex flex-col gap-1.5 rounded-lg border-l-2 border-[#1f82d9]/40 bg-[#1f82d9]/[0.03] px-3 py-2.5"
        :data-parent-tool-call-id="tc.tool_call_id"
      >
        <button
          type="button"
          class="subagent-chain-toggle flex w-full items-center gap-1.5 text-left text-xs font-medium text-[#1f82d9] transition-colors hover:text-[#1668b0]"
          :aria-expanded="!isChainCollapsed(tc)"
          @click.stop="toggleChain(tc.tool_call_id)"
        >
          <i class="fa-solid fa-diagram-project shrink-0 text-[10px]"></i>
          <span class="shrink-0">子智能体执行过程</span>
          <template v-if="runningToolName(tc)">
            <i class="fa-solid fa-spinner fa-spin shrink-0 text-[10px]"></i>
            <span class="min-w-0 truncate text-[11px] font-normal text-[#1f82d9]/80">正在执行：{{ runningToolName(tc) }}</span>
          </template>
          <span v-else-if="chainHasError(tc)" class="shrink-0 text-[11px] font-normal text-red-400">部分工具失败</span>
          <i
            class="fa-solid fa-chevron-right ml-auto shrink-0 text-[9px] text-[#1f82d9]/60 transition-transform duration-200"
            :class="{ 'rotate-90': !isChainCollapsed(tc) }"
          ></i>
        </button>
        <div
          v-if="!isChainCollapsed(tc)"
          class="subagent-trail-scroll flex max-h-[280px] flex-col gap-1 overflow-y-auto pr-1"
          :ref="setChainScrollEl(tc.tool_call_id)"
        >
          <template v-for="entry in trailViewsOf(tc.tool_call_id)" :key="entry.key">
            <!-- 子智能体中间文本输出：与主智能体回复同一渲染方式（markdown）；
                 思考过程默认收起，父 task 运行中在按钮行露尾部预览 -->
            <div v-if="entry.kind === 'message' && (entry.content || entry.thinking)" class="min-w-0 py-0.5">
              <div v-if="entry.thinking" class="mb-1">
                <button
                  type="button"
                  class="sub-thinking-toggle flex max-w-full items-center gap-1.5 rounded px-1 py-0.5 text-[11px] text-slate-400 transition-colors hover:text-slate-600"
                  @click.stop="toggleThinking(entry.key)"
                >
                  <i class="fa-solid fa-brain text-[10px] shrink-0"></i>
                  <span class="shrink-0">{{ expandedThinkingIds.has(entry.key) ? '收起思考' : '思考中' }}</span>
                  <span
                    v-if="!expandedThinkingIds.has(entry.key) && entry.thinkingRunning && entry.thinking"
                    class="min-w-0 flex-1 truncate text-left text-slate-300"
                  >{{ thinkingTailPreview(entry.thinking) }}</span>
                  <i v-if="entry.thinkingRunning" class="fa-solid fa-spinner fa-spin shrink-0 text-[9px]"></i>
                </button>
                <div
                  v-if="expandedThinkingIds.has(entry.key)"
                  class="mt-1 whitespace-pre-wrap break-words rounded bg-slate-50 px-2 py-1.5 text-[11px] leading-relaxed text-slate-500"
                >{{ entry.thinking }}</div>
                <div v-if="entry.thinkingTruncated" class="mt-0.5 text-[11px] text-slate-400">…（思考已截断）</div>
              </div>
              <div
                v-if="entry.content"
                class="conversation-markdown text-sm leading-relaxed text-slate-700"
                v-html="entry.contentHtml"
              ></div>
              <div v-if="entry.truncated" class="mt-0.5 text-[11px] text-slate-400">…（已截断）</div>
            </div>
            <!-- 内层工具调用：参数与结果走右侧详情面板，点击 chip 查看 -->
            <div v-else-if="entry.kind === 'tool'" class="flex flex-col">
              <div
                class="sub-tool-chip group flex max-w-full cursor-pointer items-center gap-2 rounded-md border px-2 py-1 text-xs transition-all duration-200"
                :class="{
                  'border-transparent text-slate-500 hover:bg-white hover:text-slate-700': !entry.isRunning && !entry.isError,
                  'border-[rgba(31,130,217,0.15)] bg-[rgba(31,130,217,0.06)] text-[#1f82d9] hover:bg-[rgba(31,130,217,0.1)]': entry.isRunning,
                  'border-red-200 bg-red-50 text-red-600 hover:bg-red-100': entry.isError,
                }"
                :title="entry.compactDescription || entry.displayName"
                @click.stop="entry.toolCall && onSubToolClick(entry.toolCall)"
              >
                <i class="fa-solid fa-wrench text-[10px] shrink-0"></i>
                <span class="shrink-0 font-semibold">{{ entry.displayName }}</span>
                <span v-if="entry.compactDescription" class="min-w-0 truncate font-mono text-[11px] text-slate-400">{{ entry.compactDescription }}</span>
                <i v-if="entry.isRunning" class="fa-solid fa-spinner fa-spin ml-auto shrink-0 text-[10px] text-[#1f82d9]"></i>
                <i v-else-if="entry.isError" class="fa-solid fa-circle-exclamation ml-auto shrink-0 text-[10px] text-red-400"></i>
                <i v-else class="fa-solid fa-circle-check ml-auto shrink-0 text-[10px] text-emerald-400"></i>
              </div>
            </div>
          </template>
        </div>
      </div>
    </template>
  </div>
</template>
