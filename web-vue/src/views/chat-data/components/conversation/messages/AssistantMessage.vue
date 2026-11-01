<script setup lang="ts">
/**
 * 助手消息：工作过程折叠 + 块渲染（文本/思考/工具）+ 查询结果内联 + 产物插槽 + 错误
 * + footer + 流式状态。
 *
 * 块由 utils/messageView 投影得到；「最终回答」由投影层标记，折叠策略在组件内：
 * 默认流式/当前 run 展开、结束后收起，用户手动切换后以手动态为准。
 */
import type { PropType } from 'vue'
import { computed, ref } from 'vue'
import type { ExecutionTargetOption, Message, ToolCallInfo } from '../../../types'
import {
  buildMessageBlocks,
  getAssistantCopyText,
  hasWorkProcess,
  shouldShowStreamingStatus,
  summarizeWorkProcess,
} from '../../../utils/messageView'
import { extractGeneratedSqlFromToolCall } from '../../../utils/semanticTool'
import { formatDateTimeValue } from '../../../utils'
import MarkdownBlock from './MarkdownBlock.vue'
import ThinkingBlock from './ThinkingBlock.vue'
import MessageFooter from './MessageFooter.vue'
import StreamingStatus from './StreamingStatus.vue'
import ToolCallList from '../../ToolCallList.vue'
import QueryResultPanel from '../../QueryResultPanel.vue'

const props = defineProps({
  message: { type: Object as PropType<Message>, required: true },
  isCurrentRun: { type: Boolean, default: false },
  datasourceId: { type: String, default: '' },
  executionTargets: { type: Array as PropType<ExecutionTargetOption[]>, default: () => [] },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
  conversationId: { type: String as PropType<string | null>, default: null },
  onSave: {
    type: Function as PropType<(sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => void>,
    default: undefined,
  },
  showFooter: { type: Boolean, default: false },
  liveGenerationSpeed: { type: Number as PropType<number | null>, default: null },
  liveElapsedMs: { type: Number as PropType<number | null>, default: null },
  resolveToolCall: {
    type: Function as PropType<(message: Message, toolCallId: string) => ToolCallInfo | null>,
    default: undefined,
  },
  shouldInlineRenderResult: {
    type: Function as PropType<(toolCall: ToolCallInfo) => boolean>,
    default: undefined,
  },
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
}>()

const blocks = computed(() => buildMessageBlocks(props.message, {
  resolveToolCall: props.resolveToolCall,
  shouldInlineRenderResult: props.shouldInlineRenderResult,
  forceWorkProcess: props.message.forceWorkProcess,
}))

const copyText = computed(() => getAssistantCopyText(props.message))
const showStreamingStatus = computed(() => shouldShowStreamingStatus(props.message))
const timestamp = computed(() => formatDateTimeValue(props.message.timestamp))
const fallbackText = computed(() => {
  const first = blocks.value[0]
  return blocks.value.length === 1 && first?.type === 'text' ? first.content : ''
})

// ---- 工作过程折路口径：过程行整体收起，只留最终回答在外 ----
const workProcessAvailable = computed(() => hasWorkProcess(blocks.value))
const workProcessSummary = computed(() => summarizeWorkProcess(blocks.value))

// null = 跟随默认策略；用户点过之后以手动态为准
const expandedOverride = ref<boolean | null>(null)

const defaultExpanded = computed(() => {
  // 没有最终回答时必须展开，否则折叠后气泡只剩一个空按钮
  if (!blocks.value.some(block => block.type === 'text' && block.finalAnswer)) return true
  return props.isCurrentRun || Boolean(props.message.isStreaming)
})

const workProcessExpanded = computed(() => (
  expandedOverride.value === null ? defaultExpanded.value : expandedOverride.value
))

const toggleWorkProcess = () => {
  expandedOverride.value = !workProcessExpanded.value
}
</script>

<template>
  <div
    class="min-w-0 rounded-2xl rounded-bl-md border border-black/[0.06] bg-white p-4 shadow-[0_2px_8px_rgba(0,0,0,0.04)]"
    data-assistant-bubble
    :data-message-id="message.id"
    :data-streaming="message.isStreaming ? 'true' : 'false'"
  >
    <button
      v-if="workProcessAvailable"
      type="button"
      class="group inline-flex max-w-full items-center gap-2 self-start rounded-md border-none bg-transparent py-1.5 pl-0 pr-2.5 text-left text-xs font-medium text-slate-400 transition-colors duration-200 hover:text-slate-600"
      :aria-expanded="workProcessExpanded"
      :aria-controls="`work-process-${message.id}`"
      @click="toggleWorkProcess"
    >
      <span>工作过程</span>
      <span
        v-if="!workProcessExpanded && workProcessSummary"
        class="text-xs font-normal text-slate-400/80"
      >{{ workProcessSummary }}</span>
      <i
        :class="[
          'fa-solid fa-chevron-right text-[10px] text-slate-400 transition-all duration-200 group-hover:text-slate-600',
          workProcessExpanded ? 'rotate-90 opacity-100' : 'opacity-60 group-hover:opacity-100',
        ]"
      ></i>
    </button>

    <div
      v-if="message.segments?.length"
      :id="`work-process-${message.id}`"
      class="flex min-w-0 flex-col gap-3"
    >
      <template v-for="block in blocks" :key="block.key">
        <MarkdownBlock
          v-if="block.type === 'text' && (workProcessExpanded || block.finalAnswer)"
          :content="block.content"
        />
        <ThinkingBlock
          v-else-if="block.type === 'thinking' && workProcessExpanded"
          :content="block.content"
          :active="block.active"
          :preview="block.preview"
        />
        <div v-else-if="block.type === 'tool' && workProcessExpanded" class="min-w-0">
          <ToolCallList
            :tool-calls="[block.toolCall]"
            :datasource-id="datasourceId"
            :execution-targets="executionTargets"
            compact
            :active-tool-call-id="activeToolCallId"
            :conversation-id="conversationId"
            @select-tool="emit('selectTool', $event)"
          />
          <QueryResultPanel
            v-if="block.queryResultToolCall"
            class="mt-2 max-h-[46vh]"
            :result-data="block.queryResultToolCall.renderId!"
            :conversation-id="conversationId"
            :sql="extractGeneratedSqlFromToolCall(block.queryResultToolCall)"
            :semantic-params="block.queryResultToolCall.semanticParams"
            :on-save="onSave"
          />
        </div>
      </template>
    </div>

    <MarkdownBlock
      v-else-if="fallbackText"
      :content="fallbackText"
    />
    <div v-else-if="message.isStreaming" class="flex items-center gap-2 text-sm text-slate-500">
      <i class="fa-solid fa-spinner fa-spin text-xs"></i>
      <span>正在处理...</span>
    </div>

    <slot name="assistant-extra" :message="message" />

    <div v-if="message.error" class="mt-3 flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-600">
      <i class="fa-solid fa-circle-exclamation"></i>
      <span>{{ message.error }}</span>
    </div>
  </div>

  <div v-if="showFooter" class="mt-1 px-0">
    <MessageFooter :timestamp="timestamp" :stats="message.generationStats" :copy-text="copyText" />
  </div>

  <StreamingStatus
    v-if="message.isStreaming && showStreamingStatus"
    :live-generation-speed="liveGenerationSpeed"
    :live-elapsed-ms="liveElapsedMs"
  />
</template>
