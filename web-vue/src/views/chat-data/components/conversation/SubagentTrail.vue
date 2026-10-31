<script setup lang="ts">
/**
 * 子智能体完整过程（右侧工具详情面板），以「聊天记录」方式展示，并**复用主对话消息组件**：
 *
 * - 任务书 → user 消息 → `MessageItem/UserMessage`（主对话用户气泡同款）；
 * - 子智能体消息（思考 + 正文）→ assistant 消息 → `MessageItem/AssistantMessage`
 *   （工作过程折叠、思考块、markdown、内层工具卡、查询结果面板都是主对话那套）；
 * - 内层工具挂到所属助手消息的 segments 上，与主对话「助手消息 + 工具调用」结构一致。
 *
 * 行内不再展示过程区（只保留 task 工具卡），因此这里没有限流，进面板即全量。
 * 工作过程折叠跟随主对话口径：运行中默认展开（能看到正在做什么），完成后默认收起
 * 只留最终报告，点「工作过程」展开步骤——与主对话消息完全同构。
 */
import type { PropType } from 'vue'
import { computed } from 'vue'
import type { SubagentTokenUsage, ToolCallInfo } from '../../types'
import { buildSubagentMessages, hasSubagentTrail } from '../../utils/messageView'
import { shouldInlineRenderQueryResult } from '../../utils/semanticTool'
import { formatTotalDurationLabel } from '../../utils'
import { buildSubagentTokenUsageText } from '../../utils/subagentUsage'
import MessageItem from './messages/MessageItem.vue'

const props = defineProps({
  toolCall: { type: Object as PropType<ToolCallInfo>, required: true },
  conversationId: { type: String as PropType<string | null>, default: null },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
  /** 子智能体已执行耗时（毫秒，后端 started_at 起算）；运行中进状态行，完成后落一行「用时」 */
  elapsedMs: { type: Number as PropType<number | null>, default: null },
  timingRunning: { type: Boolean, default: false },
  /** 子智能体 token 用量（billing 口径）：完成后与「用时」同一行展示 */
  tokenUsage: { type: Object as PropType<SubagentTokenUsage | null>, default: null },
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
}>()

const messages = computed(() => buildSubagentMessages(props.toolCall))
const tokenUsageText = computed(() => buildSubagentTokenUsageText(props.tokenUsage))
</script>

<template>
  <div v-if="hasSubagentTrail(toolCall)" data-subagent-process class="flex flex-col gap-3">
    <!-- 过程记录直接复用主对话消息分派：user（任务书）+ assistant（思考/正文/工具） -->
    <MessageItem
      v-for="message in messages"
      :key="message.id"
      :message="message"
      :show-footer="false"
      :conversation-id="conversationId"
      :active-tool-call-id="activeToolCallId"
      :should-inline-render-result="shouldInlineRenderQueryResult"
      :live-elapsed-ms="timingRunning ? elapsedMs : null"
      @select-tool="emit('selectTool', $event)"
    />

    <!-- 完成后与主对话一致：气泡下方一行「用时 X」+ 子智能体 token 用量 -->
    <div
      v-if="!timingRunning && (elapsedMs || tokenUsageText)"
      class="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11px] text-gray-400"
    >
      <span v-if="elapsedMs" data-subagent-duration>用时 {{ formatTotalDurationLabel(elapsedMs) }}</span>
      <span v-if="tokenUsageText" data-subagent-tokens>{{ tokenUsageText }}</span>
    </div>
  </div>
</template>
