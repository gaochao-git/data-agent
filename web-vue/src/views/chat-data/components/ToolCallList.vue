<script setup lang="ts">
/**
 * 工具调用列表组件
 * 用于在消息中展示多个工具调用；子智能体完整过程在右侧工具详情面板（SubagentTrail）。
 */
import { ref, watch } from 'vue'
import ToolCallCard from './ToolCallCard.vue'
import type { ExecutionTargetOption, ToolCallInfo } from '../types'

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

// 查询分析入口：data_analyzing_tool 的 execute_query
const isQueryAnalysisTool = (tc: ToolCallInfo): boolean => {
  return tc.tool_name === 'data_analyzing_tool' && (tc.args || {})?.action === 'execute_query'
}
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
        :on-view-analysis="isQueryAnalysisTool(tc) && tc.renderId && onViewAnalysis ? onViewAnalysis : undefined"
        :is-analysis-active="Boolean(isQueryAnalysisTool(tc) && tc.renderId && isAnalysisActive)"
        :conversation-id="conversationId"
        @toggle-expand="toggleExpand(tc.tool_call_id)"
        @select="emit('selectTool', tc)"
      />
    </template>
  </div>
</template>
