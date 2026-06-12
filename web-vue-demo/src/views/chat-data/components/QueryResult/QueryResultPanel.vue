<script setup lang="ts">
/**
 * 查询结果面板组件
 * 支持表格、文本、图表三种视图模式
 * 辅助查询和 SQL 查询共用
 */
import { ref, computed, watch } from 'vue'
import * as echarts from 'echarts'
import type { QueryResultItem } from '../../types'

interface Props {
  /** 查询结果列表（支持多个结果 tab） */
  results: QueryResultItem[]
  /** 是否加载中 */
  loading?: boolean
  /** 当前激活的结果索引 */
  activeIndex?: number
  /** 是否隐藏 tabs（单个结果时） */
  hideTabs?: boolean
  /** 是否隐藏工具栏 */
  hideToolbar?: boolean
  /** 空状态内容 */
  emptyText?: string
}

const props = withDefaults(defineProps<Props>(), {
  loading: false,
  activeIndex: 0,
  hideTabs: false,
  hideToolbar: false,
  emptyText: '暂无数据',
})

const emit = defineEmits<{
  'update:activeIndex': [index: number]
  'save': [result: QueryResultItem]
}>()

// 视图模式
type ViewMode = 'table' | 'text' | 'chart'
const viewMode = ref<ViewMode>('table')

// 当前选中的结果
const currentIndex = ref(props.activeIndex)
watch(() => props.activeIndex, (val) => {
  currentIndex.value = val
})

const currentResult = computed(() => props.results[currentIndex.value] || null)

// 切换 tab
const handleTabChange = (index: number) => {
  currentIndex.value = index
  emit('update:activeIndex', index)
}

// 切换视图模式
const handleViewModeChange = (mode: ViewMode) => {
  viewMode.value = mode
}

// 格式化执行时间
const formatExecutionTime = (ms?: number) => {
  if (!ms) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

// 判断单元格值是否为数字
const isNumber = (value: unknown): boolean => {
  return typeof value === 'number'
}

const isRatioColumn = (columnName: string): boolean => {
  const name = (columnName || '').toLowerCase()
  return name === 'ratio_value' || name === '比率'
}

const formatNumberValue = (value: number): string => {
  if (Number.isInteger(value)) return value.toLocaleString('zh-CN')
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  })
}

// 格式化单元格值
const formatCellValue = (value: unknown, columnName = ''): string => {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number') {
    if (isRatioColumn(columnName)) {
      return `${(value * 100).toFixed(2)}%`
    }
    return formatNumberValue(value)
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

// 图表相关
const chartRef = ref<HTMLDivElement | null>(null)
let chartInstance: echarts.ECharts | null = null

// 图表类型
type ChartType = 'bar' | 'line' | 'pie'
const chartType = ref<ChartType>('bar')

// 图表字段配置
const xField = ref('')
const yField = ref('')

// 初始化图表字段
watch(currentResult, (result) => {
  if (result && result.columns.length > 0) {
    // 自动选择字段
    const cols = result.columns
    const firstRow = result.data[0]

    // X 轴选择第一个非数字字段
    const stringCol = cols.find(col => firstRow && typeof firstRow[col] !== 'number')
    xField.value = stringCol || cols[0]

    // Y 轴选择第一个数字字段
    const numberCol = cols.find(col => firstRow && typeof firstRow[col] === 'number')
    yField.value = numberCol || cols[1] || cols[0]
  }
}, { immediate: true })

// 渲染图表
const renderChart = () => {
  if (!chartRef.value || !currentResult.value) return

  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  const result = currentResult.value
  const data = result.data.slice(0, 20) // 限制显示条数

  let option: echarts.EChartsOption = {}

  if (chartType.value === 'pie') {
    option = {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        data: data.map(row => ({
          name: String(row[xField.value] || ''),
          value: Number(row[yField.value]) || 0,
        })),
        label: { formatter: '{b}: {d}%' },
      }],
    }
  } else {
    const xData = data.map(row => String(row[xField.value] || ''))
    const yData = data.map(row => Number(row[yField.value]) || 0)

    option = {
      tooltip: { trigger: 'axis' },
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
      xAxis: {
        type: 'category',
        data: xData,
        axisLabel: { rotate: xData.length > 10 ? 45 : 0 },
      },
      yAxis: { type: 'value' },
      series: [{
        type: chartType.value,
        data: yData,
        smooth: chartType.value === 'line',
        itemStyle: { color: '#1677FF' },
        areaStyle: chartType.value === 'line' ? { opacity: 0.1 } : undefined,
      }],
    }
  }

  chartInstance.setOption(option, true)
}

// 监听图表相关变化
watch([viewMode, chartType, xField, yField, currentResult], () => {
  if (viewMode.value === 'chart') {
    setTimeout(renderChart, 100)
  }
}, { deep: true })

// 复制结果
const copyResult = () => {
  if (!currentResult.value) return

  const result = currentResult.value
  const header = result.columns.join('\t')
  const rows = result.data.map(row =>
    result.columns.map(col => formatCellValue(row[col], col)).join('\t')
  ).join('\n')

  navigator.clipboard.writeText(`${header}\n${rows}`)
}
</script>

<template>
  <div class="flex flex-col h-full bg-white rounded-lg overflow-hidden">
    <!-- 加载状态 -->
    <div v-if="loading" class="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500 text-sm">
      <i class="fa-solid fa-spinner fa-spin text-[32px] opacity-50"></i>
      <span>查询中...</span>
    </div>

    <!-- 空状态 -->
    <div v-else-if="results.length === 0" class="flex-1 flex flex-col items-center justify-center gap-3 text-gray-500 text-sm">
      <i class="fa-solid fa-inbox text-[32px] opacity-50"></i>
      <span>{{ emptyText }}</span>
    </div>

    <!-- 结果内容 -->
    <template v-else>
      <!-- Tabs（多个结果时显示） -->
      <div v-if="!hideTabs && results.length > 1" class="flex gap-1 px-3 py-2 border-b border-gray-100 bg-gray-50">
        <button
          v-for="(result, idx) in results"
          :key="idx"
          :class="[
            'flex items-center gap-1 px-3 py-1.5 border-none bg-transparent rounded text-[13px] cursor-pointer transition-all duration-200',
            currentIndex === idx ? 'bg-blue-500 text-white' : 'text-gray-600 hover:bg-gray-100',
            result.error && currentIndex !== idx && 'text-red-500',
            result.error && currentIndex === idx && 'bg-red-500 text-white'
          ]"
          @click="handleTabChange(idx)"
        >
          <span>查询 {{ idx + 1 }}</span>
          <span v-if="result.error" class="text-xs">
            <i class="fa-solid fa-exclamation-circle"></i>
          </span>
        </button>
      </div>

      <!-- 工具栏 -->
      <div v-if="!hideToolbar && currentResult" class="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50">
        <div class="flex items-center gap-3 text-[13px]">
          <!-- 状态信息 -->
          <span v-if="currentResult.error" class="flex items-center gap-1 text-red-500">
            <i class="fa-solid fa-times-circle"></i>
            执行失败
          </span>
          <span v-else class="flex items-center gap-1 text-green-500">
            <i class="fa-solid fa-check-circle"></i>
            执行成功
          </span>
          <span class="text-gray-500">{{ formatExecutionTime(currentResult.executionTime) }}</span>
          <span v-if="!currentResult.error" class="text-gray-500">
            {{ currentResult.total_count }} 行
          </span>
        </div>

        <div class="flex items-center gap-2">
          <!-- 视图切换 -->
          <div class="flex border border-gray-300 rounded overflow-hidden">
            <button
              :class="[
                'w-8 h-7 flex items-center justify-center border-none bg-white text-gray-600 cursor-pointer transition-all duration-200',
                viewMode === 'table' ? 'bg-blue-500 text-white' : 'hover:bg-gray-100',
                'border-r border-gray-300 last:border-r-0'
              ]"
              @click="handleViewModeChange('table')"
              title="表格视图"
            >
              <i class="fa-solid fa-table"></i>
            </button>
            <button
              :class="[
                'w-8 h-7 flex items-center justify-center border-none bg-white text-gray-600 cursor-pointer transition-all duration-200',
                viewMode === 'text' ? 'bg-blue-500 text-white' : 'hover:bg-gray-100',
                'border-r border-gray-300 last:border-r-0'
              ]"
              @click="handleViewModeChange('text')"
              title="文本视图"
            >
              <i class="fa-solid fa-list"></i>
            </button>
            <button
              :class="[
                'w-8 h-7 flex items-center justify-center border-none bg-white text-gray-600 cursor-pointer transition-all duration-200',
                viewMode === 'chart' ? 'bg-blue-500 text-white' : 'hover:bg-gray-100'
              ]"
              @click="handleViewModeChange('chart')"
              title="图表视图"
            >
              <i class="fa-solid fa-chart-bar"></i>
            </button>
          </div>

          <!-- 复制 -->
          <button
            class="w-8 h-7 flex items-center justify-center border border-gray-300 bg-white rounded text-gray-600 cursor-pointer transition-all duration-200 hover:border-blue-500 hover:text-blue-500"
            @click="copyResult"
            title="复制结果"
          >
            <i class="fa-solid fa-copy"></i>
          </button>

          <!-- 保存 -->
          <button
            v-if="!currentResult.error"
            class="w-8 h-7 flex items-center justify-center border border-gray-300 bg-white rounded text-gray-600 cursor-pointer transition-all duration-200 hover:border-blue-500 hover:text-blue-500"
            @click="emit('save', currentResult)"
            title="保存到看板"
          >
            <i class="fa-solid fa-bookmark"></i>
          </button>
        </div>
      </div>

      <!-- 错误信息 -->
      <div v-if="currentResult?.error" class="flex items-center gap-2 p-4 m-3 bg-red-50 border border-red-200 rounded-md text-red-500 text-sm">
        <i class="fa-solid fa-exclamation-triangle"></i>
        <span>{{ currentResult.error }}</span>
      </div>

      <!-- 结果视图 -->
      <div v-else-if="currentResult" class="flex-1 overflow-hidden flex flex-col">
        <!-- 表格视图 -->
        <div v-if="viewMode === 'table'" class="flex-1 overflow-auto">
          <table class="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                <th
                  v-for="col in currentResult.columns"
                  :key="col"
                  class="sticky top-0 px-3 py-2.5 bg-gray-50 border-b border-gray-100 text-left font-semibold whitespace-nowrap"
                >
                  {{ col }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, idx) in currentResult.data"
                :key="idx"
                class="hover:bg-gray-50"
              >
                <td
                  v-for="col in currentResult.columns"
                  :key="col"
                  :class="[
                    'px-3 py-2 border-b border-gray-100 max-w-[300px] overflow-hidden text-ellipsis whitespace-nowrap',
                    isNumber(row[col]) && 'font-semibold tabular-nums text-right'
                  ]"
                  :title="formatCellValue(row[col], col)"
                >
                  {{ formatCellValue(row[col], col) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 文本视图 -->
        <div v-else-if="viewMode === 'text'" class="flex-1 overflow-auto p-3">
          <div
            v-for="(row, idx) in currentResult.data"
            :key="idx"
            class="mb-4 border border-gray-100 rounded-md overflow-hidden"
          >
            <div class="px-3 py-2 bg-gray-50 text-xs font-semibold text-gray-500 border-b border-gray-100">
              行 {{ idx + 1 }}
            </div>
            <div class="p-3">
              <div
                v-for="col in currentResult.columns"
                :key="col"
                class="flex gap-2 py-1 text-[13px]"
              >
                <span class="text-gray-500 min-w-30">{{ col }}:</span>
                <span :class="['text-gray-900 break-all', isNumber(row[col]) && 'font-semibold tabular-nums']">
                  {{ formatCellValue(row[col], col) }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- 图表视图 -->
        <div v-else-if="viewMode === 'chart'" class="flex-1 flex flex-col overflow-hidden">
          <!-- 图表配置 -->
          <div class="flex gap-4 p-3 border-b border-gray-100 bg-gray-50">
            <div class="flex items-center gap-2 text-[13px]">
              <label class="text-gray-500">图表类型</label>
              <select v-model="chartType" class="px-2 py-1 border border-gray-300 rounded text-[13px] bg-white">
                <option value="bar">柱状图</option>
                <option value="line">折线图</option>
                <option value="pie">饼图</option>
              </select>
            </div>
            <div class="flex items-center gap-2 text-[13px]">
              <label class="text-gray-500">{{ chartType === 'pie' ? '名称字段' : 'X 轴' }}</label>
              <select v-model="xField" class="px-2 py-1 border border-gray-300 rounded text-[13px] bg-white">
                <option v-for="col in currentResult.columns" :key="col" :value="col">
                  {{ col }}
                </option>
              </select>
            </div>
            <div class="flex items-center gap-2 text-[13px]">
              <label class="text-gray-500">{{ chartType === 'pie' ? '数值字段' : 'Y 轴' }}</label>
              <select v-model="yField" class="px-2 py-1 border border-gray-300 rounded text-[13px] bg-white">
                <option v-for="col in currentResult.columns" :key="col" :value="col">
                  {{ col }}
                </option>
              </select>
            </div>
          </div>
          <div ref="chartRef" class="flex-1 min-h-[300px]"></div>
        </div>
      </div>
    </template>
  </div>
</template>
