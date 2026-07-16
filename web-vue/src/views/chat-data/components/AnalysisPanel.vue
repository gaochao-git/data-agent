<script setup lang="ts">
/**
 * 分析过程面板组件 - 右侧滑出面板
 * 展示思考过程和工具调用链路
 */
import { computed, ref, watch, nextTick } from 'vue'
import MarkdownIt from 'markdown-it'
import type { ExecutionTargetOption, SemanticConfigSelectionInfo, ThinkingSegment, ToolCallInfo } from '../types'
import ToolCallList from './ToolCallList.vue'

// 分析过程数据
export interface AnalysisData {
  roundId: string              // 轮次唯一标识
  thinkingSegments?: ThinkingSegment[] // 按时序分段的思考内容
  toolCalls: ToolCallInfo[]    // 工具调用列表
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
  textContent?: string         // 其他文字内容
}

interface Props {
  isOpen: boolean
  data: AnalysisData | null
  datasourceId?: string
  executionTargets?: ExecutionTargetOption[]
  isStreaming?: boolean
  showSwitchConversation?: boolean
}

const props = defineProps<Props>()

const emit = defineEmits<{
  close: []
  switchConversation: []
  copySql: [sql: string]
}>()

const HIDDEN_TOOLS = ['load_skill_tool']
const md = new MarkdownIt({ breaks: true, html: false, linkify: true })

// 可见的工具调用
const visibleToolCalls = computed(() => {
  if (!props.data?.toolCalls) return []
  return props.data.toolCalls.filter(tc => !HIDDEN_TOOLS.includes(tc.tool_name))
})

// 是否有工具调用
const hasToolCalls = computed(() => visibleToolCalls.value.length > 0)

const thinkingSegments = computed<ThinkingSegment[]>(() => {
  const maxToolCount = visibleToolCalls.value.length
  return props.data?.thinkingSegments
    ?.filter(item => item.content)
    .map(item => ({
      ...item,
      content: String(item.content || '').trim(),
      toolCallCount: Math.min(Math.max(0, item.toolCallCount), maxToolCount),
    }))
    .filter(item => item.content) || []
})
const hasUnderstandingStep = computed(() => thinkingSegments.value.length > 0)
const isEnabledFlag = (value: unknown) => value === true || value === 'true' || value === 1 || value === '1'
const understandingStepOrder = computed(() => 1)

type TimelineEntry =
  | { type: 'thinking'; key: string; segment: ThinkingSegment }
  | { type: 'tool'; key: string; tool: ToolCallInfo }

const timelineEntries = computed<TimelineEntry[]>(() => {
  const entries: TimelineEntry[] = []
  const maxToolCount = visibleToolCalls.value.length
  for (let toolCount = 0; toolCount <= maxToolCount; toolCount += 1) {
    thinkingSegments.value
      .filter(segment => segment.toolCallCount === toolCount)
      .forEach(segment => {
        entries.push({ type: 'thinking', key: `thinking-${toolCount}-${entries.length}-${segment.id}`, segment })
      })
    const tool = visibleToolCalls.value[toolCount]
    if (tool) {
      entries.push({ type: 'tool', key: tool.tool_call_id, tool })
    }
  }
  return entries
})

const hasTimeline = computed(() => timelineEntries.value.length > 0)
const hasCompletionStep = computed(() => !props.isStreaming && hasTimeline.value)

const isTimelineEntryInProgress = (entry: TimelineEntry, index: number): boolean => {
  if (!props.isStreaming) return false
  if (index !== timelineEntries.value.length - 1) return false
  if (entry.type === 'tool') return entry.tool.status === 'generating' || entry.tool.status === 'running'
  return true
}

// 自动滚动
const contentRef = ref<HTMLDivElement | null>(null)
const isNearBottom = ref(true)
const collapsedThinking = ref<Record<string, boolean>>({})

const isThinkingCollapsed = (key: string): boolean => collapsedThinking.value[key] === true

const toggleThinking = (key: string) => {
  collapsedThinking.value = {
    ...collapsedThinking.value,
    [key]: !isThinkingCollapsed(key),
  }
}

const handleContentScroll = () => {
  const container = contentRef.value
  if (!container) return
  const { scrollTop, scrollHeight, clientHeight } = container
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 80
}

watch(
  () => [props.data?.thinkingSegments, props.data?.toolCalls, props.data?.semanticConfigSelection],
  () => {
    if (props.isStreaming && contentRef.value && isNearBottom.value) {
      nextTick(() => {
        contentRef.value!.scrollTop = contentRef.value!.scrollHeight
      })
    }
  },
  { deep: true }
)

watch(
  () => props.data?.roundId,
  () => {
    isNearBottom.value = true
    nextTick(() => {
      if (contentRef.value) {
        contentRef.value.scrollTop = contentRef.value.scrollHeight
      }
    })
  }
)

const renderMarkdown = (content: string) => md.render(content)
</script>

<template>
  <div v-if="isOpen && data" class="flex h-full w-full min-w-0 flex-col bg-white">
    <!-- 面板头部 -->
    <div class="flex w-full min-w-0 items-center justify-between px-4 py-3 border-b border-gray-200">
      <h3 class="text-sm font-medium text-gray-900 m-0">分析过程</h3>
      <div class="flex items-center gap-1.5">
        <button
          v-if="showSwitchConversation"
          class="h-7 px-2.5 border border-gray-200 rounded-md bg-white text-xs text-gray-500 cursor-pointer transition-all hover:border-gray-300 hover:text-gray-700"
          title="切换到会话"
          @click="emit('switchConversation')"
        >
          会话
        </button>
        <button class="w-7 h-7 flex items-center justify-center border-none bg-transparent rounded-md text-gray-400 cursor-pointer transition-all hover:bg-gray-100 hover:text-gray-500" @click="emit('close')" title="关闭">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
    </div>

    <!-- 面板内容 - 链路布局 -->
    <div ref="contentRef" class="analysis-panel-scroll min-w-0 flex-1 overflow-y-scroll overflow-x-hidden pt-4 pb-10" @scroll="handleContentScroll">
      <div class="flex w-full min-w-0 flex-col px-4">
        <!-- 等待第一个数据时的占位（面板已打开但还没有内容） -->
        <div v-if="!hasTimeline" class="flex items-center gap-2 text-gray-500 text-sm py-2">
          <i class="fa-solid fa-spinner fa-spin text-blue-500"></i>
          <span>正在思考...</span>
        </div>

        <!-- 按时序展示思考与工具调用 -->
        <div
          v-for="(entry, index) in timelineEntries"
          :key="entry.key"
          class="flex min-w-0 relative"
          :style="{ order: understandingStepOrder }"
        >
          <div class="flex flex-col items-center mr-4">
            <div :class="[
              'w-6 h-6 flex items-center justify-center rounded-full shrink-0 text-[10px]',
              isTimelineEntryInProgress(entry, index)
                ? 'bg-gradient-to-br from-blue-500/15 to-blue-500/5 text-blue-500'
                : 'bg-gradient-to-br from-emerald-500/15 to-emerald-500/5 text-emerald-500'
            ]">
              <i v-if="isTimelineEntryInProgress(entry, index)" class="fa-solid fa-spinner fa-spin"></i>
              <i v-else class="fa-solid fa-check"></i>
            </div>
            <div
              v-if="index < timelineEntries.length - 1 || props.isStreaming || hasCompletionStep"
              class="w-px flex-1 min-h-5 mt-2"
              style="background-image: repeating-linear-gradient(to bottom, #D8DAE5 0 3px, transparent 3px 6px);"
            ></div>
          </div>
          <div class="min-w-0 flex-1 -mt-2.5 pb-6">
            <template v-if="entry.type === 'thinking'">
              <div class="min-w-0 max-w-full overflow-hidden rounded-xl border border-black/[0.04] bg-white/60">
                <button
                  type="button"
                  class="flex w-full min-w-0 items-center justify-between px-3.5 py-2.5 text-left text-sm font-medium text-gray-700 cursor-pointer transition-colors duration-200 hover:bg-black/[0.03]"
                  :class="{ 'bg-black/[0.02]': !isThinkingCollapsed(entry.key) }"
                  @click="toggleThinking(entry.key)"
                >
                  <span class="flex min-w-0 items-center gap-3">
                    <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-black/5 text-[11px] text-gray-500">
                      <i class="fa-solid fa-brain"></i>
                    </span>
                    <span class="truncate">思考</span>
                  </span>
                  <i
                    :class="[
                      'fa-solid fa-chevron-right shrink-0 text-xs text-gray-400 transition-transform duration-200',
                      !isThinkingCollapsed(entry.key) && 'rotate-90'
                    ]"
                  ></i>
                </button>
                <div
                  v-if="!isThinkingCollapsed(entry.key)"
                  class="min-w-0 border-t border-black/[0.04] bg-black/[0.02] px-3.5 py-2.5 text-sm leading-relaxed text-gray-400 markdown-body"
                  v-html="renderMarkdown(entry.segment.content)"
                ></div>
              </div>
            </template>
            <template v-else>
              <ToolCallList
                :tool-calls="[entry.tool]"
                :datasource-id="datasourceId"
                :execution-targets="executionTargets"
                :default-expanded="false"
                :show-running-indicator="false"
                :render-query-result-preview="false"
              />
            </template>
          </div>
        </div>

        <!-- 步骤 4：任务完成（流式结束且有实际内容后才显示） -->
        <div v-if="hasCompletionStep" class="flex min-w-0 relative" :style="{ order: 4 }">
          <div class="flex flex-col items-center mr-4">
            <div class="w-6 h-6 flex items-center justify-center rounded-full shrink-0 text-[10px] bg-gradient-to-br from-emerald-500 to-emerald-600 text-white">
              <i class="fa-solid fa-check"></i>
            </div>
          </div>
          <div class="min-w-0 flex-1 flex min-h-8 items-center pb-4">
            <span class="text-sm font-medium text-emerald-500">任务完成</span>
          </div>
        </div>

        <!-- 其他文字内容 -->
        <div v-if="data.textContent" class="mt-4 text-sm text-gray-700" :style="{ order: 5 }">
          {{ data.textContent }}
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.analysis-panel-scroll::-webkit-scrollbar {
  width: 10px;
}

.analysis-panel-scroll::-webkit-scrollbar-track {
  background: rgba(148, 163, 184, 0.12);
}

.analysis-panel-scroll::-webkit-scrollbar-thumb {
  background: rgba(100, 116, 139, 0.58);
  border: 2px solid rgba(248, 250, 252, 0.95);
  border-radius: 999px;
  background-clip: content-box;
}

.analysis-panel-scroll::-webkit-scrollbar-thumb:hover {
  background: rgba(71, 85, 105, 0.72);
  border: 2px solid rgba(248, 250, 252, 0.95);
  background-clip: content-box;
}
</style>
