<script setup lang="ts">
/**
 * 单条消息分派：按 role 选择消息组件，并负责对齐/宽度等行级布局。
 * 新增消息类型只改这里 + 对应消息组件，列表容器不用动。
 */
import type { PropType } from 'vue'
import type { ExecutionTargetOption, Message, MessageAttachment, ToolCallInfo } from '../../../types'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import ToolMessage from './ToolMessage.vue'
import CompactionMessage from './CompactionMessage.vue'

defineProps({
  message: { type: Object as PropType<Message>, required: true },
  datasourceId: { type: String, default: '' },
  executionTargets: { type: Array as PropType<ExecutionTargetOption[]>, default: () => [] },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
  conversationId: { type: String as PropType<string | null>, default: null },
  onSave: {
    type: Function as PropType<(sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => void>,
    default: undefined,
  },
  isCurrentRun: { type: Boolean, default: false },
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
  openAttachment: [attachment: MessageAttachment]
}>()
</script>

<template>
  <div
    class="flex"
    :class="message.role === 'user' ? 'justify-end' : message.role === 'context_compression' ? 'justify-center' : 'justify-start'"
    :data-conversation-turn-id="message.role === 'user' ? message.id : undefined"
  >
    <div :class="message.role === 'user' ? 'flex max-w-[80%] flex-col items-end gap-1.5' : 'w-full min-w-0'">
      <UserMessage
        v-if="message.role === 'user'"
        :message="message"
        @open-attachment="emit('openAttachment', $event)"
      />
      <ToolMessage
        v-else-if="message.role === 'tool'"
        :message="message"
        :datasource-id="datasourceId"
        :execution-targets="executionTargets"
        :active-tool-call-id="activeToolCallId"
        :conversation-id="conversationId"
        @select-tool="emit('selectTool', $event)"
      />
      <CompactionMessage v-else-if="message.role === 'context_compression'" :message="message" />
      <AssistantMessage
        v-else
        :message="message"
        :datasource-id="datasourceId"
        :execution-targets="executionTargets"
        :active-tool-call-id="activeToolCallId"
        :conversation-id="conversationId"
        :on-save="onSave"
        :is-current-run="isCurrentRun"
        :show-footer="showFooter"
        :live-generation-speed="liveGenerationSpeed"
        :live-elapsed-ms="liveElapsedMs"
        :resolve-tool-call="resolveToolCall"
        :should-inline-render-result="shouldInlineRenderResult"
        @select-tool="emit('selectTool', $event)"
      >
        <template #assistant-extra="slotProps">
          <slot name="assistant-extra" v-bind="slotProps" />
        </template>
      </AssistantMessage>
    </div>
  </div>
</template>
