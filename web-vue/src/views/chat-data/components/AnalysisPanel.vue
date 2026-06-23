<script setup lang="ts">
/**
 * 分析过程面板组件 - 右侧滑出面板
 * 展示思考过程和工具调用链路
 */
import { computed, ref, watch, nextTick } from 'vue'
import type { SemanticConfigSelectionInfo, SemanticConfigSelectRequiredInfo, ThinkingSegment, ToolCallInfo } from '../types'
import StructuredMessage from './StructuredMessage.vue'
import ToolCallList from './ToolCallList.vue'

// 分析过程数据
export interface AnalysisData {
  roundId: string              // 轮次唯一标识
  thinkingContent: string      // 思考内容
  thinkingSegments?: ThinkingSegment[] // 按时序分段的思考内容
  toolCalls: ToolCallInfo[]    // 工具调用列表
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
  semanticConfigSelectRequired?: SemanticConfigSelectRequiredInfo | null
  textContent?: string         // 其他文字内容
}

interface Props {
  isOpen: boolean
  data: AnalysisData | null
  datasourceId?: string
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

// 可见的工具调用
const visibleToolCalls = computed(() => {
  if (!props.data?.toolCalls) return []
  return props.data.toolCalls.filter(tc => !HIDDEN_TOOLS.includes(tc.tool_name))
})

// 是否有工具调用
const hasToolCalls = computed(() => visibleToolCalls.value.length > 0)

// 是否有需要用户处理的指标集选择过程；自动选择结果在工具调用结果中展示。
const hasSemanticConfigStep = computed(() => Boolean(props.data?.semanticConfigSelectRequired))

const stripThinkingWrapper = (content: string): string => {
  const match = content.match(/^<thinking>([\s\S]*?)<\/thinking>$/)
  return stripModelThinkTags(match?.[1] ?? content)
}

const stripModelThinkTags = (content: string): string => {
  return String(content || '')
    .replace(/<think\b[^>]*>[\s\S]*?<\/think>/gi, '')
    .replace(/<think\b[^>]*>[\s\S]*$/gi, '')
    .replace(/<\/think>/gi, '')
    .trim()
}

const thinkingSegments = computed<ThinkingSegment[]>(() => {
  const maxToolCount = visibleToolCalls.value.length
  const segments = props.data?.thinkingSegments
    ?.filter(item => item.content)
    .map(item => ({
      ...item,
      content: stripThinkingWrapper(item.content),
      toolCallCount: Math.min(Math.max(0, item.toolCallCount), maxToolCount),
    })) || []
  if (segments.length) return segments
  return props.data?.thinkingContent
    ? [{ id: 'thinking-legacy', content: stripThinkingWrapper(props.data.thinkingContent), toolCallCount: 0 }]
    : []
})
const hasUnderstandingStep = computed(() => thinkingSegments.value.length > 0)
const isEnabledFlag = (value: unknown) => value === true || value === 'true' || value === 1 || value === '1'
const semanticConfigBeforeUnderstanding = computed(() => Boolean(
  isEnabledFlag(props.data?.semanticConfigSelection?.user_selected)
  && !isEnabledFlag(props.data?.semanticConfigSelection?.auto_selected)
))
const understandingStepOrder = computed(() => semanticConfigBeforeUnderstanding.value ? 2 : 1)
const semanticConfigStepOrder = computed(() => semanticConfigBeforeUnderstanding.value ? 1 : 2)
const semanticConfigHasNext = computed(() => Boolean(
  semanticConfigBeforeUnderstanding.value
    ? (hasUnderstandingStep.value || hasToolCalls.value || props.isStreaming)
    : (hasToolCalls.value || props.isStreaming)
))

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
const hasCompletionStep = computed(() => !props.isStreaming && (hasTimeline.value || hasSemanticConfigStep.value))

const isTimelineEntryInProgress = (entry: TimelineEntry, index: number): boolean => {
  if (!props.isStreaming) return false
  if (index !== timelineEntries.value.length - 1) return false
  if (entry.type === 'tool') return entry.tool.status === 'generating' || entry.tool.status === 'running'
  return true
}

// 自动滚动
const contentRef = ref<HTMLDivElement | null>(null)
const isNearBottom = ref(true)

const handleContentScroll = () => {
  const container = contentRef.value
  if (!container) return
  const { scrollTop, scrollHeight, clientHeight } = container
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 80
}

watch(
  () => [props.data?.thinkingContent, props.data?.thinkingSegments, props.data?.toolCalls, props.data?.semanticConfigSelection, props.data?.semanticConfigSelectRequired],
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
</script>

<template>
  <div v-if="isOpen && data" class="flex flex-col h-full bg-white">
    <!-- 面板头部 -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200">
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
    <div ref="contentRef" class="flex flex-1 flex-col overflow-y-auto px-4 pt-4 pb-10" @scroll="handleContentScroll">
      <!-- 等待第一个数据时的占位（面板已打开但还没有内容） -->
      <div v-if="!hasTimeline && !hasSemanticConfigStep" class="flex items-center gap-2 text-gray-500 text-sm py-2">
        <i class="fa-solid fa-spinner fa-spin text-blue-500"></i>
        <span>正在思考...</span>
      </div>

      <!-- 需要用户选择指标集的阻塞场景 -->
      <div v-if="hasSemanticConfigStep" class="flex relative" :style="{ order: semanticConfigStepOrder }">
        <div class="flex flex-col items-center mr-4">
          <div :class="[
            'w-6 h-6 flex items-center justify-center rounded-full shrink-0 text-[10px]',
            'bg-gradient-to-br from-blue-500/15 to-blue-500/5 text-blue-500'
          ]">
            <i class="fa-solid fa-list-check"></i>
          </div>
          <div
            v-if="semanticConfigHasNext"
            class="w-px flex-1 min-h-5 mt-2"
            style="background-image: repeating-linear-gradient(to bottom, #D8DAE5 0 3px, transparent 3px 6px);"
          ></div>
        </div>
        <div class="flex-1 pb-6">
          <h4 class="text-sm font-medium text-gray-900 m-0 mb-2">选择指标集</h4>
          <div v-if="data.semanticConfigSelectRequired" class="rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2 text-sm text-slate-700">
            <div class="font-medium text-slate-800">{{ data.semanticConfigSelectRequired.message || '需要选择指标集' }}</div>
            <div v-if="data.semanticConfigSelectRequired.reason" class="mt-1.5 text-xs leading-relaxed text-slate-600">
              {{ data.semanticConfigSelectRequired.reason }}
            </div>
            <div v-if="data.semanticConfigSelectRequired.candidates.length && !data.semanticConfigSelectRequired.cross_config_required" class="mt-2 flex flex-wrap gap-1.5">
              <span
                v-for="candidate in data.semanticConfigSelectRequired.candidates"
                :key="candidate.id"
                class="rounded border border-blue-100 bg-white px-2 py-0.5 text-xs text-blue-600"
                :title="candidate.reason || candidate.coverage || ''"
              >
                {{ candidate.name || candidate.id }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 按时序展示思考与工具调用 -->
      <div
        v-for="(entry, index) in timelineEntries"
        :key="entry.key"
        class="flex relative"
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
        <div class="flex-1 pb-6">
          <template v-if="entry.type === 'thinking'">
            <h4 class="text-sm font-medium text-gray-900 m-0 mb-2">思考</h4>
            <div class="text-sm text-gray-600">
              <StructuredMessage
                :content="`<thinking>${entry.segment.content}</thinking>`"
                :is-streaming="isTimelineEntryInProgress(entry, index)"
                @copy-sql="emit('copySql', $event)"
              />
            </div>
          </template>
          <template v-else>
            <h4 class="text-sm font-medium text-gray-900 m-0 mb-2">工具调用</h4>
            <ToolCallList
              :tool-calls="[entry.tool]"
              :datasource-id="datasourceId"
              :default-expanded="false"
            />
          </template>
        </div>
      </div>

      <!-- 步骤 4：任务完成（流式结束且有实际内容后才显示） -->
      <div v-if="hasCompletionStep" class="flex relative" :style="{ order: 4 }">
        <div class="flex flex-col items-center mr-4">
          <div class="w-6 h-6 flex items-center justify-center rounded-full shrink-0 text-[10px] bg-gradient-to-br from-emerald-500 to-emerald-600 text-white">
            <i class="fa-solid fa-check"></i>
          </div>
        </div>
        <div class="flex-1 flex min-h-8 items-center pb-4">
          <span class="text-sm font-medium text-emerald-500">任务完成</span>
        </div>
      </div>

      <!-- 其他文字内容 -->
      <div v-if="data.textContent" class="mt-4 text-sm text-gray-700" :style="{ order: 5 }">
        {{ data.textContent }}
      </div>
    </div>
  </div>
</template>
