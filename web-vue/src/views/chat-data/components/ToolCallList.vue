<script setup lang="ts">
/**
 * 工具调用列表组件
 * 用于在消息中展示多个工具调用
 */
import { ref, watch } from 'vue'
import ToolCallCard from './ToolCallCard.vue'
import type { ToolCallInfo } from '../types'

interface Props {
  toolCalls: ToolCallInfo[]
  datasourceId?: string
  defaultExpanded?: boolean
  compact?: boolean
  activeToolCallId?: string | null
  onViewAnalysis?: () => void
  isAnalysisActive?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  defaultExpanded: false,
  compact: false,
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
</script>

<template>
  <div v-if="toolCalls.length > 0" class="flex flex-col gap-2">
    <ToolCallCard
      v-for="tc in toolCalls"
      :key="tc.tool_call_id"
      :tool-call="tc"
      :is-expanded="expandedIds.has(tc.tool_call_id)"
      :compact="compact"
      :active="activeToolCallId === tc.tool_call_id"
      :datasource-id="datasourceId"
      :on-view-analysis="tc.tool_name === 'semantic_query_tool' && tc.renderId && onViewAnalysis ? onViewAnalysis : undefined"
      :is-analysis-active="Boolean(tc.tool_name === 'semantic_query_tool' && tc.renderId && isAnalysisActive)"
      @toggle-expand="toggleExpand(tc.tool_call_id)"
      @select="emit('selectTool', tc)"
    />
  </div>
</template>
