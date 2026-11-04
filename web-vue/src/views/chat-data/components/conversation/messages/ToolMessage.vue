<script setup lang="ts">
/**
 * 工具消息：工具调用列表 + 时间戳（主对话中的 tool role 消息）。
 */
import type { PropType } from 'vue'
import type { ExecutionTargetOption, Message, ToolCallInfo } from '../../../types'
import { formatDateTimeValue } from '../../../utils'
import ToolCallList from '../../ToolCallList.vue'

defineProps({
  message: { type: Object as PropType<Message>, required: true },
  datasourceId: { type: String, default: '' },
  executionTargets: { type: Array as PropType<ExecutionTargetOption[]>, default: () => [] },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
  conversationId: { type: String as PropType<string | null>, default: null },
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
}>()
</script>

<template>
  <div>
    <div class="rounded-xl border border-slate-100 bg-white px-4 py-3 shadow-sm">
      <ToolCallList
        v-if="message.toolCalls?.length"
        :tool-calls="message.toolCalls"
        :datasource-id="datasourceId"
        :execution-targets="executionTargets"
        compact
        :active-tool-call-id="activeToolCallId"
        :conversation-id="conversationId"
        @select-tool="emit('selectTool', $event)"
      />
    </div>
    <div v-if="formatDateTimeValue(message.timestamp)" class="px-1 text-right text-[10px] text-gray-400">
      {{ formatDateTimeValue(message.timestamp) }}
    </div>
  </div>
</template>
