<script setup lang="ts">
/**
 * 查询结果图表组件
 * 支持柱状图、折线图、面积图、饼图、漏斗图、关系图
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import type { ChartType } from '../chartTypes'
import { toSafeNumber } from '../utils'
import { buildEChartsOption } from '../utils/chartOptionBuilder'

type ResultChartType = Extract<ChartType, 'bar' | 'horizontal_bar' | 'line' | 'area' | 'pie' | 'funnel' | 'graph'>

interface Props {
  type: ResultChartType
  data: Record<string, unknown>[]
  columns: string[]
  xField?: string
  yField?: string
  numericColumns: string[]
}

const props = defineProps<Props>()

const chartRef = ref<HTMLDivElement | null>(null)
let chartInstance: echarts.ECharts | null = null

// 自动选择 X 轴字段
const autoXField = computed(() => {
  if (props.xField && props.columns.includes(props.xField)) {
    return props.xField
  }
  // 优先选择非数值列作为 X 轴
  const nonNumeric = props.columns.filter((col) => !props.numericColumns.includes(col))
  return nonNumeric[0] || props.columns[0] || ''
})

// 自动选择 Y 轴字段
const autoYFields = computed(() => {
  if (props.type === 'graph') {
    if (props.yField && props.columns.includes(props.yField)) {
      return [props.yField]
    }
    const nonNumeric = props.columns.filter((col) => !props.numericColumns.includes(col) && col !== autoXField.value)
    return nonNumeric.length > 0 ? [nonNumeric[0]!] : []
  }
  if (props.yField && props.numericColumns.includes(props.yField)) {
    return [props.yField]
  }
  // 默认展示所有数值列，避免 SQL 返回的多指标结果被前端截断。
  return props.numericColumns.length > 0 ? props.numericColumns : []
})
// graph 类型：从行数据构建节点与链接（关系强度取第一个数值列）
const buildGraphData = () => {
  const sourceField = autoXField.value
  const targetField = autoYFields.value[0] || ''
  const valueField = props.numericColumns[0]
  const nodeMap = new Map<string, number>()
  const links: Array<{ source: string; target: string; value?: number }> = []

  props.data.forEach((row) => {
    const source = String(row.source ?? row.from ?? row.Source ?? row[sourceField] ?? '').trim()
    const target = String(row.target ?? row.to ?? row.Target ?? row[targetField] ?? '').trim()
    if (!source || !target) return
    const rawValue = valueField ? row[valueField] : row.value ?? row.weight ?? row.count
    const value = toSafeNumber(rawValue) ?? undefined
    nodeMap.set(source, (nodeMap.get(source) || 0) + (value ?? 1))
    nodeMap.set(target, nodeMap.get(target) || 0)
    links.push({ source, target, value })
  })

  const values = Array.from(nodeMap.values())
  const maxValue = values.length ? Math.max(...values) : 1
  const nodes = Array.from(nodeMap.entries()).map(([name, value]) => ({
    name,
    value,
    symbolSize: 18 + (maxValue > 0 ? (value / maxValue) * 22 : 0)
  }))
  return { nodes, links }
}

// 生成图表配置（option 构建统一走共享 builder）
const generateOption = (): echarts.EChartsOption => {
  return buildEChartsOption({
    type: props.type,
    rows: props.data,
    encoding: { xField: autoXField.value, yFields: autoYFields.value },
    graphData: props.type === 'graph' ? buildGraphData() : null,
    toNumber: toSafeNumber,
  })
}


// 渲染图表
const renderChart = () => {
  if (!chartRef.value) return

  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  const option = generateOption()
  chartInstance.setOption(option, true)
}

// 响应窗口大小变化
const handleResize = () => {
  chartInstance?.resize()
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  chartInstance?.dispose()
  chartInstance = null
})

// 监听数据和类型变化
watch(
  () => [props.type, props.data, props.columns, props.numericColumns, props.xField, props.yField],
  () => {
    renderChart()
  },
  { deep: true }
)
</script>

<template>
  <div class="relative w-full h-80 rounded-xl overflow-hidden bg-white">
    <div ref="chartRef" class="w-full h-full"></div>

    <!-- 无法生成图表时的提示 -->
    <div v-if="autoYFields.length === 0" class="absolute inset-0 flex flex-col items-center justify-center gap-3 text-gray-400 text-sm bg-gradient-to-br from-slate-50 to-slate-100">
      <i class="fa-solid fa-chart-simple text-[40px] bg-gradient-to-br from-[#1f82d9] to-[#135589] bg-clip-text text-transparent opacity-60"></i>
      <span class="font-medium">无法生成图表，需要至少一个数值列</span>
    </div>
  </div>
</template>
