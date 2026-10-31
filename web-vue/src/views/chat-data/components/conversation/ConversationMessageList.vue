<script setup lang="ts">
/**
 * 会话消息列表容器。
 *
 * 职责收敛为三件事：列表级状态（当前 run 范围、footer 显隐）、消息分派、划选引用浮层。
 * 单条消息的渲染与消息级派生分别下沉到 messages/* 与 utils/messageView。
 */
import { computed } from 'vue'
import type { ExecutionTargetOption, Message, MessageAttachment, ToolCallInfo } from '../../types'
import { resolveActiveRunStartIndex, shouldShowAssistantFooter } from '../../utils/messageView'
import { useSelectionQuote } from '../../composables/useSelectionQuote'
import MessageItem from './messages/MessageItem.vue'
import SelectionQuoteToolbar from './messages/SelectionQuoteToolbar.vue'

interface Props {
  messages: Message[]
  datasourceId?: string
  executionTargets?: ExecutionTargetOption[]
  activeToolCallId?: string | null
  resolveToolCall?: (message: Message, toolCallId: string) => ToolCallInfo | null
  shouldInlineRenderResult?: (toolCall: ToolCallInfo) => boolean
  onSave?: (sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => void
  conversationId?: string | null
  /** 本轮 Run 仍活跃（流式进行中 / 链式接续 / 等待用户 / 恢复订阅）时抑制底部 footer */
  runActive?: boolean
  /** 生成中的实时速率（tokens/s，后端滑窗推送）；null 表示暂不展示 */
  liveGenerationSpeed?: number | null
  /** 本轮已执行耗时（毫秒，从本轮发起起算的墙钟，实时递增）；null 表示暂不展示 */
  liveElapsedMs?: number | null
}

const props = withDefaults(defineProps<Props>(), {
  datasourceId: '',
  executionTargets: () => [],
  activeToolCallId: null,
  runActive: false,
  liveGenerationSpeed: null,
  liveElapsedMs: null,
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
  openAttachment: [attachment: MessageAttachment]
  addToConversation: [text: string]
}>()

/**
 * run 活跃时，只有「当前 run 产生」的消息（最后一条 user 消息之后的）才抑制 footer、
 * 并保持工作过程展开；历史轮次保持稳定，不随新 run 启停闪烁。
 */
const activeRunStartIndex = computed(() => resolveActiveRunStartIndex(props.messages, props.runActive))

const isCurrentRun = (index: number): boolean => (
  activeRunStartIndex.value >= 0 && index > activeRunStartIndex.value
)

const shouldShowFooter = (message: Message, index: number): boolean => shouldShowAssistantFooter(message, index, {
  runActive: props.runActive,
  activeRunStartIndex: activeRunStartIndex.value,
})

const {
  quoteToolbar,
  toolbarStyle,
  handleMouseUp,
  handleCopyQuoteSelection,
  handleQuoteSelectionToConversation,
} = useSelectionQuote({
  onAddToConversation: text => emit('addToConversation', text),
})
</script>

<template>
  <div class="chat-message-list mx-auto flex w-[90%] max-w-[774px] flex-col gap-5" @mouseup="handleMouseUp">
    <MessageItem
      v-for="(message, messageIndex) in messages"
      :key="message.id"
      :message="message"
      :datasource-id="datasourceId"
      :execution-targets="executionTargets"
      :active-tool-call-id="activeToolCallId"
      :conversation-id="conversationId"
      :on-save="onSave"
      :is-current-run="isCurrentRun(messageIndex)"
      :show-footer="shouldShowFooter(message, messageIndex)"
      :live-generation-speed="liveGenerationSpeed"
      :live-elapsed-ms="liveElapsedMs"
      :resolve-tool-call="resolveToolCall"
      :should-inline-render-result="shouldInlineRenderResult"
      @select-tool="emit('selectTool', $event)"
      @open-attachment="emit('openAttachment', $event)"
    >
      <template #assistant-extra="slotProps">
        <slot name="assistant-extra" v-bind="slotProps" />
      </template>
    </MessageItem>

    <SelectionQuoteToolbar
      v-if="quoteToolbar"
      :state="quoteToolbar"
      :style="toolbarStyle"
      @copy="handleCopyQuoteSelection"
      @add="handleQuoteSelectionToConversation"
    />
  </div>
</template>
