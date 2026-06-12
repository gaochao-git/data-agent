<template>
  <div class="space-y-3">
    <!-- 操作栏 -->
    <div v-if="analysisResult" class="flex items-center justify-between">
      <p class="text-sm text-gray-600 dark:text-gray-400">
        共识别出 <span class="font-semibold text-gray-900 dark:text-white">{{ analysisResult.违法行为数量 || 0 }}</span> 项违法事实
      </p>
      <button
        @click="onToggleAll"
        class="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
      >
        <i :class="`fa-solid ${isAllExpanded ? 'fa-compress-alt' : 'fa-expand-alt'}`"></i>
        {{ isAllExpanded ? '全部折叠' : '全部展开' }}
      </button>
    </div>

    <!-- 事实认定列表 -->
    <div
      v-if="analysisResult?.违法行为分析"
      v-for="violation in analysisResult.违法行为分析"
      :key="violation.违法行为编号"
    >
      <ViolationCard
        :violation="violation"
        :is-expanded="expandedItems.has(violation.违法行为编号)"
        :is-completed="isCompleted"
        :on-toggle="() => onToggle(violation.违法行为编号)"
        :on-law-click="onLawClick"
        :on-show-tooltip="onShowTooltip"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AnalysisResult } from '../types'
import ViolationCard from './ViolationCard.vue'

interface Props {
  analysisResult: AnalysisResult
  isCompleted: boolean
  expandedItems: Set<number>
  onToggle: (id: number) => void
  onToggleAll: () => void
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void
  onShowTooltip?: (title: string, content: string) => void
}

const props = defineProps<Props>()

// 计算是否全部展开
const isAllExpanded = computed(() => {
  return props.analysisResult?.违法行为分析
    ? props.expandedItems.size === props.analysisResult.违法行为分析.length
    : false
})
</script>
