<script setup lang="ts">
/**
 * 指标卡片组件
 * 用于展示单个或多个指标数据
 */
import { computed } from 'vue'
import { isNumericValue } from '../utils'

interface Props {
  data: Record<string, unknown>[]
  columns: string[]
  metrics?: string[]
  dimensions?: string[]
}

const props = defineProps<Props>()

// 检测数值列
const numericColumns = computed(() => {
  if (!props.data.length) return []
  return props.columns.filter((col) => {
    const value = props.data[0]?.[col]
    return isNumericValue(value)
  })
})

// 指标列（优先使用传入的 metrics，否则使用检测到的数值列）
const metricCols = computed(() => {
  if (props.metrics && props.metrics.length > 0) {
    return props.metrics.filter((m) => props.columns.includes(m))
  }
  return numericColumns.value
})

// 维度列（优先使用传入的 dimensions，否则使用非数值列）
const dimensionCols = computed(() => {
  if (props.dimensions && props.dimensions.length > 0) {
    return props.dimensions.filter((d) => props.columns.includes(d))
  }
  return props.columns.filter((col) => !numericColumns.value.includes(col))
})

// 布局模式
const layoutMode = computed<'single' | 'multi-metric' | 'multi-row' | 'grid'>(() => {
  const rowCount = props.data.length
  const metricCount = metricCols.value.length

  if (rowCount === 1 && metricCount === 1) return 'single'
  if (rowCount === 1 && metricCount > 1) return 'multi-metric'
  if (rowCount > 1 && metricCount === 1) return 'multi-row'
  return 'grid'
})

const formatValue = (value: unknown): string => {
  if (value === null || value === undefined) return '-'
  return String(value)
}

// 获取维度标签
const getDimensionLabel = (row: Record<string, unknown>): string => {
  return dimensionCols.value
    .map((dim) => String(row[dim] ?? ''))
    .filter(Boolean)
    .join(' / ')
}
</script>

<template>
  <div :class="[
    'flex flex-wrap gap-4 p-5',
    layoutMode === 'single' && 'justify-center',
    layoutMode === 'multi-metric' && 'justify-start',
    layoutMode === 'multi-row' && 'flex-row',
    layoutMode === 'grid' && 'flex-col max-h-[360px] overflow-y-auto'
  ]">
    <!-- 单指标单行：大卡片 -->
    <template v-if="layoutMode === 'single'">
      <div class="metric-card flex flex-col py-8 px-10 min-w-60 text-center bg-white border border-black/[0.04] rounded-2xl shadow-[0_2px_8px_rgba(0,0,0,0.02)] transition-all duration-300 relative overflow-hidden hover:-translate-y-1 hover:shadow-[0_12px_24px_rgba(31,130,217,0.12)] hover:border-[#1f82d9]/20">
        <div class="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">{{ metricCols[0] }}</div>
        <div class="text-[42px] font-bold bg-gradient-to-br from-[#1f82d9] to-[#135589] bg-clip-text text-transparent tabular-nums">
          {{ formatValue(data[0]?.[metricCols[0] ?? '']) }}
        </div>
        <div v-if="dimensionCols.length > 0" class="text-xs text-gray-400 mt-3 pt-3 border-t border-black/[0.04]">
          {{ getDimensionLabel(data[0] ?? {}) }}
        </div>
      </div>
    </template>

    <!-- 多指标单行：横向卡片组 -->
    <template v-else-if="layoutMode === 'multi-metric'">
      <div
        v-for="metric in metricCols"
        :key="metric"
        class="metric-card flex flex-col py-5 px-6 min-w-40 bg-white border border-black/[0.04] rounded-2xl shadow-[0_2px_8px_rgba(0,0,0,0.02)] transition-all duration-300 relative overflow-hidden hover:-translate-y-1 hover:shadow-[0_12px_24px_rgba(31,130,217,0.12)] hover:border-[#1f82d9]/20"
      >
        <div class="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">{{ metric }}</div>
        <div class="text-[28px] font-bold bg-gradient-to-br from-gray-800 to-gray-600 bg-clip-text text-transparent tabular-nums">
          {{ formatValue(data[0]?.[metric]) }}
        </div>
      </div>
    </template>

    <!-- 单指标多行：带维度标签的卡片 -->
    <template v-else-if="layoutMode === 'multi-row'">
      <div
        v-for="(row, index) in data"
        :key="index"
        class="metric-card flex flex-col py-5 px-6 min-w-40 bg-white border border-black/[0.04] rounded-2xl shadow-[0_2px_8px_rgba(0,0,0,0.02)] transition-all duration-300 relative overflow-hidden hover:-translate-y-1 hover:shadow-[0_12px_24px_rgba(31,130,217,0.12)] hover:border-[#1f82d9]/20"
      >
        <div class="text-sm font-semibold text-gray-700 mb-2.5">{{ getDimensionLabel(row) }}</div>
        <div class="text-[28px] font-bold bg-gradient-to-br from-gray-800 to-gray-600 bg-clip-text text-transparent tabular-nums">
          {{ formatValue(row[metricCols[0] ?? '']) }}
        </div>
        <div class="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">{{ metricCols[0] }}</div>
      </div>
    </template>

    <!-- 多指标多行：网格布局 -->
    <template v-else>
      <div
        v-for="(row, rowIndex) in data"
        :key="rowIndex"
        class="w-full p-4 rounded-xl bg-gradient-to-br from-slate-50 to-slate-100 mb-3 last:mb-0"
      >
        <div class="text-sm font-semibold text-gray-700 mb-3 pl-1">{{ getDimensionLabel(row) }}</div>
        <div class="flex flex-wrap gap-3">
          <div
            v-for="metric in metricCols"
            :key="metric"
            class="metric-card flex flex-col py-3.5 px-4.5 min-w-30 bg-white border border-black/[0.04] rounded-2xl shadow-[0_2px_8px_rgba(0,0,0,0.02)] transition-all duration-300 relative overflow-hidden hover:-translate-y-1 hover:shadow-[0_12px_24px_rgba(31,130,217,0.12)] hover:border-[#1f82d9]/20"
          >
            <div class="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">{{ metric }}</div>
            <div class="text-xl font-bold bg-gradient-to-br from-gray-800 to-gray-600 bg-clip-text text-transparent tabular-nums">
              {{ formatValue(row[metric]) }}
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 卡片悬浮时顶部装饰条 */
.metric-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 4px;
  background: linear-gradient(135deg, #1f82d9 0%, #135589 100%);
  opacity: 0;
  transition: opacity 0.3s;
}

.metric-card:hover::before {
  opacity: 1;
}
</style>
