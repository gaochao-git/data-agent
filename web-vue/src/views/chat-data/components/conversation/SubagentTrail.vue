<script setup lang="ts">
/**
 * 子智能体完整过程（右侧工具详情面板），以「聊天记录」方式展示，并**复用主对话消息组件**：
 *
 * - 任务书 → user 消息 → `MessageItem/UserMessage`（主对话用户气泡同款）；
 * - 子智能体消息（思考 + 正文）→ assistant 消息 → `MessageItem/AssistantMessage`
 *   （工作过程折叠、思考块、markdown、内层工具卡、查询结果面板都是主对话那套）；
 * - 内层工具挂到所属助手消息的 segments 上，与主对话「助手消息 + 工具调用」结构一致。
 *
 * 行内不再展示过程区（只保留 subagent 工具卡），因此这里没有限流，进面板即全量。
 * 工作过程折叠跟随主对话口径：运行中默认展开（能看到正在做什么），完成后默认收起
 * 只留最终报告，点「工作过程」展开步骤——与主对话消息完全同构。
 */
import type { PropType } from 'vue'
import { computed } from 'vue'
import type { ExecutionTargetOption, SubagentTokenUsage, ToolCallInfo } from '../../types'
import { buildSubagentMessages, getAssistantCopyText, hasSubagentTrail } from '../../utils/messageView'
import { shouldInlineRenderQueryResult } from '../../utils/semanticTool'
import { formatDateTimeValue } from '../../utils'
import { buildSubagentTokenUsageRows, buildSubagentTokenUsageText } from '../../utils/subagentUsage'
import MessageItem from './messages/MessageItem.vue'
import MessageFooter from './messages/MessageFooter.vue'

const props = defineProps({
  toolCall: { type: Object as PropType<ToolCallInfo>, required: true },
  conversationId: { type: String as PropType<string | null>, default: null },
  datasourceId: { type: String, default: '' },
  executionTargets: { type: Array as PropType<ExecutionTargetOption[]>, default: () => [] },
  onSave: { type: Function as PropType<(sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => void>, default: undefined },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
  /** 子智能体已执行耗时（毫秒，后端 started_at 起算）；运行中进状态行，完成后落一行「用时」 */
  elapsedMs: { type: Number as PropType<number | null>, default: null },
  timingRunning: { type: Boolean, default: false },
  /** 子智能体 token 用量（billing 口径）：完成后与「用时」同一行展示 */
  tokenUsage: { type: Object as PropType<SubagentTokenUsage | null>, default: null },
  /** 运行中实时滑窗速率（与主对话同算法）；完成后改用 tokenUsage.tokens_per_second */
  liveSpeed: { type: Number as PropType<number | null>, default: null },
  /** 本次派发已推进的图步数（完成后随结果持久化；每次派发从 0 起） */
  supersteps: { type: Number, default: 0 },
  recursionLimit: { type: Number, default: 0 },
  /** 运行中实时步数（subagent_steps 事件） */
  liveSupersteps: { type: Number as PropType<number | null>, default: null },
  /** 运行中在消息流状态行展示取消入口；仅当前活跃任务开启 */
  cancellable: { type: Boolean, default: false },
  cancelling: { type: Boolean, default: false },
  /** 后台任务详情没有 trail 时也要展示任务书/报告；前台工具详情保持原口径 */
  showWithoutTrail: { type: Boolean, default: false },
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
  cancel: []
}>()

const messages = computed(() => buildSubagentMessages(props.toolCall))
const tokenUsageText = computed(() => buildSubagentTokenUsageText(props.tokenUsage))
const tokenUsageRows = computed(() => buildSubagentTokenUsageRows(props.tokenUsage))
/** 每轮只在最后一条助手消息上挂 footer，压缩切出的中间段不重复展示整轮统计。 */
const lastAssistantId = computed(() => (
  [...messages.value].reverse().find(message => message.role === 'assistant')?.id || ''
))
const showFooterFor = (message: { id: string; role: string; isStreaming?: boolean }): boolean => (
  message.role === 'assistant'
  && message.id === lastAssistantId.value
  && !message.isStreaming
)
</script>

<template>
  <div
    v-if="messages.length && (showWithoutTrail || hasSubagentTrail(toolCall))"
    data-subagent-process
    class="flex flex-col gap-3"
  >
    <!-- 过程记录直接复用主对话消息分派：user（任务书）+ assistant（思考/正文/工具） -->
    <div v-for="message in messages" :key="message.id" class="flex flex-col gap-1">
      <MessageItem
        :message="message"
        :show-footer="false"
        :conversation-id="conversationId"
        :datasource-id="datasourceId"
        :execution-targets="executionTargets"
        :on-save="onSave"
        :active-tool-call-id="activeToolCallId"
        :should-inline-render-result="shouldInlineRenderQueryResult"
        :live-generation-speed="timingRunning ? liveSpeed : null"
        :live-supersteps="timingRunning ? liveSupersteps : null"
        :live-superstep-limit="recursionLimit || null"
        :live-elapsed-ms="timingRunning ? elapsedMs : null"
        :streaming-cancellable="timingRunning && cancellable"
        :streaming-cancelling="cancelling"
        @select-tool="emit('selectTool', $event)"
        @cancel-streaming="emit('cancel')"
      />
      <MessageFooter
        v-if="showFooterFor(message)"
        :timestamp="formatDateTimeValue(message.timestamp)"
        :copy-text="getAssistantCopyText(message)"
        :duration-ms="timingRunning ? null : elapsedMs"
        :usage-text="tokenUsageText"
        :usage-model="tokenUsage?.model || ''"
        :usage-rows="tokenUsageRows"
        :usage-speed="tokenUsage?.tokens_per_second || 0"
        :supersteps="supersteps"
        :recursion-limit="recursionLimit"
        :show-ai-notice="false"
      />
    </div>
  </div>
</template>
