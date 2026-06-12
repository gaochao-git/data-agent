<script setup lang="ts">
/**
 * 查询结果图表组件
 * 支持柱状图、折线图、面积图、饼图
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'

type ChartType = 'bar' | 'line' | 'area' | 'pie'

interface Props {
  type: ChartType
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
  if (props.yField && props.columns.includes(props.yField)) {
    return [props.yField]
  }
  // 选择数值列作为 Y 轴
  return props.numericColumns.length > 0 ? props.numericColumns.slice(0, 3) : []
})

// 颜色配置
const colors = ['#1677FF', '#52C41A', '#FAAD14', '#F5222D', '#722ED1', '#13C2C2']

// 生成图表配置
const generateOption = (): echarts.EChartsOption => {
  const xData = props.data.map((row) => String(row[autoXField.value] ?? ''))

  if (props.type === 'pie') {
    // 饼图配置
    const yField = autoYFields.value[0]
    if (!yField) return {}

    const pieData = props.data.map((row) => ({
      name: String(row[autoXField.value] ?? ''),
      value: Number(row[yField]) || 0
    }))

    return {
      tooltip: {
        trigger: 'item',
        formatter: '{a} <br/>{b}: {c} ({d}%)'
      },
      legend: {
        type: 'scroll',
        orient: 'vertical',
        right: 10,
        top: 20,
        bottom: 20
      },
      series: [
        {
          name: yField,
          type: 'pie',
          radius: ['40%', '70%'],
          center: ['40%', '50%'],
          avoidLabelOverlap: false,
          itemStyle: {
            borderRadius: 8,
            borderColor: '#fff',
            borderWidth: 2
          },
          label: {
            show: false,
            position: 'center'
          },
          emphasis: {
            label: {
              show: true,
              fontSize: 16,
              fontWeight: 'bold'
            }
          },
          labelLine: {
            show: false
          },
          data: pieData
        }
      ],
      color: colors
    }
  }

  // 柱状图/折线图/面积图配置
  const series: echarts.SeriesOption[] = autoYFields.value.map((yField, index) => {
    const baseConfig = {
      name: yField,
      data: props.data.map((row) => Number(row[yField]) || 0),
      itemStyle: {
        color: colors[index % colors.length]
      }
    }

    if (props.type === 'bar') {
      return {
        ...baseConfig,
        type: 'bar',
        barMaxWidth: 40,
        itemStyle: {
          ...baseConfig.itemStyle,
          borderRadius: [4, 4, 0, 0]
        }
      }
    }

    if (props.type === 'area') {
      return {
        ...baseConfig,
        type: 'line',
        smooth: true,
        areaStyle: {
          opacity: 0.3
        }
      }
    }

    // line
    return {
      ...baseConfig,
      type: 'line',
      smooth: true
    }
  })

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: props.type === 'bar' ? 'shadow' : 'line'
      }
    },
    legend: {
      show: autoYFields.value.length > 1,
      bottom: 0
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: autoYFields.value.length > 1 ? '15%' : '8%',
      top: '10%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: xData,
      axisLabel: {
        rotate: xData.length > 8 ? 45 : 0,
        interval: 0,
        overflow: 'truncate',
        width: 80,
        color: '#374151',
        fontSize: 12
      },
      axisLine: {
        lineStyle: {
          color: '#e5e7eb'
        }
      },
      axisTick: {
        show: false
      }
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: '#374151',
        fontSize: 12
      },
      splitLine: {
        lineStyle: {
          color: '#f3f4f6',
          type: 'dashed'
        }
      },
      axisLine: {
        show: false
      },
      axisTick: {
        show: false
      }
    },
    series,
    color: colors
  }
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
  () => [props.type, props.data, props.xField, props.yField],
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
