<script setup lang="ts">
/**
 * 单个子智能体 tab 的过程视图：包一层 SubagentTrail + 该 task 的计时。
 *
 * 为什么单独成组件：右侧面板允许多个子智能体 tab 同时打开，每个 tab 的过程状态
 * （工作过程折叠、滚动高度）必须各自保留，所以按 tab 渲染一个实例，而不是共用
 * 一个绑定「最近 tab」的 SubagentTrail。
 */
import type { PropType } from 'vue'
import type { ToolCallInfo } from '../../types'
import { useToolTiming } from '../../composables/useToolTiming'
import SubagentTrail from './SubagentTrail.vue'

const props = defineProps({
  toolCall: { type: Object as PropType<ToolCallInfo>, required: true },
  conversationId: { type: String as PropType<string | null>, default: null },
  activeToolCallId: { type: String as PropType<string | null>, default: null },
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
}>()

const { elapsedMs, isRunning } = useToolTiming(
  () => props.toolCall.started_at_ms,
  () => props.toolCall.ended_at_ms,
)
</script>

<template>
  <SubagentTrail
    :tool-call="toolCall"
    :conversation-id="conversationId"
    :active-tool-call-id="activeToolCallId"
    :elapsed-ms="elapsedMs"
    :timing-running="isRunning"
    :token-usage="toolCall.subagentTokenUsage || null"
    @select-tool="emit('selectTool', $event)"
  />
</template>
