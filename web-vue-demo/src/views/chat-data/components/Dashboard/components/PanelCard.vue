<script setup lang="ts">
/**
 * 面板卡片组件
 * 负责渲染图表内容
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { Codemirror } from 'vue-codemirror'
import { sql } from '@codemirror/lang-sql'
import { EditorView } from '@codemirror/view'
import type { Panel, PanelData } from '../../../api/dashboard'
import type { DataSource } from '../../../types'
import type { ThemeConfig } from '../constants'
import { CHART_ICONS, KPI_ICONS, KPI_COLORS, CHART_COLOR_OPTIONS, LINE_STYLES } from '../constants'
import { registerChinaMap, normalizeProvinceName, isChinaMapRegistered } from '../../../utils/mapRegistry'

// SQL 编辑器主题（浅色）
const sqlReadonlyTheme = EditorView.theme({
  '&': {
    backgroundColor: '#ffffff',
    color: '#1f2937',
    fontSize: '14px',
    height: '100%',
  },
  '&.cm-editor': {
    height: '100%',
    outline: 'none',
  },
  '&.cm-editor.cm-focused': {
    outline: 'none',
  },
  '.cm-scroller': {
    fontFamily: '"SF Mono", SFMono-Regular, ui-monospace, Menlo, Monaco, Consolas, monospace',
    lineHeight: '1.6',
    overflow: 'auto',
    height: '100%',
  },
  '.cm-content': {
    padding: '8px 0',
    caretColor: '#1677FF',
  },
  '.cm-gutters': {
    backgroundColor: '#f9fafb',
    color: '#9ca3af',
    border: 'none',
    borderRight: '1px solid #e5e7eb',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 8px 0 12px',
    minWidth: '32px',
    fontSize: '12px',
  },
  '.cm-activeLine': {
    backgroundColor: 'transparent',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'transparent',
  },
  '.cm-foldGutter': {
    display: 'none !important',
  },
  // SQL 语法高亮
  '.cm-keyword': {
    color: '#7c3aed',
    fontWeight: '600',
  },
  '.cm-string': {
    color: '#059669',
  },
  '.cm-number': {
    color: '#d97706',
  },
  '.cm-comment': {
    color: '#9ca3af',
    fontStyle: 'italic',
  },
  '.cm-operator': {
    color: '#0891b2',
  },
  '.cm-punctuation': {
    color: '#6b7280',
  },
  '.cm-variableName, .cm-propertyName': {
    color: '#dc2626',
  },
})

// CodeMirror 扩展
const sqlExtensions = [
  sql(),
  sqlReadonlyTheme,
]

interface Props {
  panel: Panel
  data: PanelData | null
  loading: boolean
  error: string | null
  theme: ThemeConfig
  isPreviewMode: boolean
  datasources?: DataSource[]
}

const props = defineProps<Props>()

// 数据源名称
const datasourceName = computed(() => {
  if (!props.panel.datasource_id || !props.datasources) return ''
  const ds = props.datasources.find(d => d.id === props.panel.datasource_id)
  return ds?.ds_name || props.panel.datasource_id
})

// 去除 SQL 公共缩进
const formattedSql = computed(() => {
  const sql = props.panel.sql || ''
  const lines = sql.split('\n')

  // 找出非空行的最小缩进
  const nonEmptyLines = lines.filter(line => line.trim().length > 0)
  if (nonEmptyLines.length === 0) return sql

  const minIndent = Math.min(
    ...nonEmptyLines.map(line => {
      const match = line.match(/^(\s*)/)
      return match ? match[1].length : 0
    })
  )

  // 去除公共缩进
  if (minIndent > 0) {
    return lines.map(line => line.slice(minIndent)).join('\n')
  }
  return sql
})

const emit = defineEmits<{
  refresh: []
  delete: []
  editName: [name: string]
  updateSql: [sql: string]
  changeChartType: [type: string]
  updateChartConfig: [config: Record<string, unknown>]
}>()

// 配置弹窗状态
const showKpiConfigModal = ref(false)
const showChartStyleModal = ref(false)
const showFieldConfigModal = ref(false)
const showSqlModal = ref(false)
const showRenameModal = ref(false)
const editingName = ref('')
const editingSql = ref('')
const savingSql = ref(false)

const openRenameModal = () => {
  editingName.value = props.panel.name || ''
  showRenameModal.value = true
}

const handleSaveName = () => {
  const trimmed = editingName.value.trim()
  if (!trimmed) return
  emit('editName', trimmed)
  showRenameModal.value = false
}

// 打开 SQL 编辑弹窗
const openSqlModal = () => {
  editingSql.value = formattedSql.value
  showSqlModal.value = true
}

// 保存 SQL
const handleSaveSql = () => {
  const trimmed = editingSql.value.trim()
  if (!trimmed) return
  savingSql.value = true
  emit('updateSql', trimmed)
  showSqlModal.value = false
  savingSql.value = false
}

// 地图加载状态
const mapLoaded = ref(isChinaMapRegistered())
const mapLoading = ref(false)

// 加载地图
const loadMap = async () => {
  if (mapLoaded.value || mapLoading.value) return
  mapLoading.value = true
  try {
    await registerChinaMap()
    mapLoaded.value = true
    // 地图加载完成后重新渲染
    await nextTick()
    renderChart()
  } catch (err) {
    console.error('地图加载失败:', err)
  } finally {
    mapLoading.value = false
  }
}

// 如果是地图类型，加载地图数据
watch(() => props.panel.chart_type, (type) => {
  if (type === 'map') {
    loadMap()
  }
}, { immediate: true })

// 全屏功能
const panelRef = ref<HTMLDivElement | null>(null)
const requestFullscreen = () => {
  if (panelRef.value) {
    panelRef.value.requestFullscreen()
  }
}

// KPI 配置
const currentKpiIcon = computed(() => (props.panel.chart_config?.kpiIcon as string) || 'fa-users')
const currentKpiColor = computed(() => (props.panel.chart_config?.kpiColor as string) || '#1677FF')
const currentKpiBgColor = computed(() => {
  const color = currentKpiColor.value
  // 将颜色转换为 rgba 格式，透明度 0.1
  if (color.startsWith('#')) {
    const r = parseInt(color.slice(1, 3), 16)
    const g = parseInt(color.slice(3, 5), 16)
    const b = parseInt(color.slice(5, 7), 16)
    return `rgba(${r}, ${g}, ${b}, 0.12)`
  }
  return color
})

const handleKpiIconSelect = (icon: string) => {
  emit('updateChartConfig', { ...props.panel.chart_config, kpiIcon: icon })
}

const handleKpiColorSelect = (color: string) => {
  emit('updateChartConfig', { ...props.panel.chart_config, kpiColor: color })
}

const handleKpiReset = () => {
  const newConfig = { ...props.panel.chart_config }
  delete newConfig.kpiIcon
  delete newConfig.kpiColor
  emit('updateChartConfig', newConfig)
}

// 图表样式配置
const currentChartColor = computed(() => (props.panel.chart_config?.chartColor as string) || '#1677FF')
const currentLineStyle = computed(() => (props.panel.chart_config?.lineStyle as string) || 'smooth')

const handleChartColorSelect = (color: string) => {
  emit('updateChartConfig', { ...props.panel.chart_config, chartColor: color })
}

const handleLineStyleSelect = (styleId: string) => {
  emit('updateChartConfig', { ...props.panel.chart_config, lineStyle: styleId })
}

const handleChartStyleReset = () => {
  const newConfig = { ...props.panel.chart_config }
  delete newConfig.chartColor
  delete newConfig.lineStyle
  emit('updateChartConfig', newConfig)
}

// 图表容器
const chartRef = ref<HTMLDivElement | null>(null)
let chartInstance: echarts.ECharts | null = null

// 图表类型菜单
const showChartMenu = ref(false)

const chartTypes = [
  { key: 'table', label: '表格', icon: 'fa-table' },
  { key: 'bar', label: '柱状图', icon: 'fa-chart-column' },
  { key: 'horizontal_bar', label: '条形图', icon: 'fa-chart-bar' },
  { key: 'line', label: '折线图', icon: 'fa-chart-line' },
  { key: 'pie', label: '饼图', icon: 'fa-chart-pie' },
  { key: 'kpi', label: 'KPI', icon: 'fa-hashtag' },
  { key: 'map', label: '地图', icon: 'fa-map' },
]

const parseNumericValue = (value: unknown): number | null => {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }
  if (typeof value !== 'string') {
    return null
  }
  const trimmed = value.trim()
  if (!trimmed) {
    return null
  }
  const normalized = trimmed.replace(/,/g, '')
  if (!/^-?\d+(\.\d+)?$/.test(normalized)) {
    return null
  }
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

// 可用列（分类列 & 数值列）
const availableColumns = computed(() => {
  const result = props.data?.result
  if (!result?.columns || !result?.data?.length) {
    return { all: [] as string[], categoryColumns: [] as string[], numericColumns: [] as string[] }
  }
  const columns = result.columns
  const sampleRows = result.data.slice(0, 5)
  const categoryColumns: string[] = []
  const numericColumns: string[] = []
  columns.forEach(col => {
    const nonEmptyValues = sampleRows
      .map(row => row[col])
      .filter(value => value !== null && value !== undefined && String(value).trim() !== '')
    const isNumericColumn = nonEmptyValues.length > 0 && nonEmptyValues.every(value => parseNumericValue(value) !== null)
    if (isNumericColumn) {
      numericColumns.push(col)
    } else {
      categoryColumns.push(col)
    }
  })
  return { all: columns, categoryColumns, numericColumns }
})

// 智能推断字段（支持手动覆盖）
const inferredFields = computed(() => {
  const { all, categoryColumns, numericColumns } = availableColumns.value
  if (all.length === 0) {
    return { xField: '', yFields: [] as string[], seriesField: '' }
  }

  const config = props.panel.chart_config || {}
  const manualX = config.xField as string | undefined
  const manualY = config.yField as string | undefined
  const manualSeries = config.seriesField as string | undefined

  // X 轴：手动设定 > 第一个分类字段 > 第一列
  const xField = (manualX && all.includes(manualX)) ? manualX : (categoryColumns[0] ?? all[0] ?? '')

  // Y 轴：手动设定 > 所有数值字段 > 第二列
  let yFields: string[]
  if (manualY && all.includes(manualY)) {
    yFields = [manualY]
  } else {
    yFields = numericColumns.length > 0 ? numericColumns : [all[1] ?? all[0] ?? ''].filter((field): field is string => !!field)
  }

  // 分组字段：手动设定 > 第二个分类字段
  const seriesField = (manualSeries && all.includes(manualSeries)) ? manualSeries : (categoryColumns[1] || '')

  return { xField, yFields, seriesField }
})

// 字段配置处理
const handleFieldConfigChange = (field: 'xField' | 'yField' | 'seriesField', value: string) => {
  emit('updateChartConfig', { ...props.panel.chart_config, [field]: value || undefined })
}

const handleFieldConfigReset = () => {
  const newConfig = { ...props.panel.chart_config }
  delete newConfig.xField
  delete newConfig.yField
  delete newConfig.seriesField
  emit('updateChartConfig', newConfig)
  showFieldConfigModal.value = false
}

// 生成图表配置
const chartOption = computed(() => {
  const result = props.data?.result
  if (!result?.data?.length) return null

  const { xField, yFields } = inferredFields.value
  if (!xField || yFields.length === 0) return null
  const chartType = props.panel.chart_type
  const data = result.data
  const theme = props.theme

  // 基础配置
  const baseOption: echarts.EChartsOption = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: chartType === 'pie' ? 'item' : 'axis',
      backgroundColor: theme.chartTooltipBg,
      borderColor: theme.chartTooltipBorder,
      textStyle: { color: theme.chartTooltipText },
    },
    grid: {
      left: 10,
      right: 20,
      top: 40,
      bottom: 10,
      containLabel: true,
    },
  }

  // 柱状图
  if (chartType === 'bar' || chartType === 'horizontal_bar') {
    const isHorizontal = chartType === 'horizontal_bar'
    const xData = data.map(row => String(row[xField] || ''))
    const configuredColor = props.panel.chart_config?.chartColor as string | undefined
    const defaultColors = ['#1677FF', '#36CFC9', '#52C41A', '#FAAD14']
    const series = yFields.map((field, idx) => ({
      name: field,
      type: 'bar' as const,
      data: data.map(row => parseNumericValue(row[field]) ?? 0),
      itemStyle: {
        color: configuredColor && idx === 0 ? configuredColor : defaultColors[idx % 4],
        borderRadius: isHorizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
      },
    }))

    const splitLineStyle = { lineStyle: { type: 'dashed' as const, color: theme.chartGridLine } }

    return {
      ...baseOption,
      xAxis: isHorizontal
        ? { type: 'value', axisLabel: { color: theme.chartAxisText }, splitLine: splitLineStyle }
        : { type: 'category', data: xData, axisLabel: { color: theme.chartAxisText }, splitLine: { show: false } },
      yAxis: isHorizontal
        ? { type: 'category', data: xData, axisLabel: { color: theme.chartAxisText }, splitLine: { show: false } }
        : { type: 'value', axisLabel: { color: theme.chartAxisText }, splitLine: splitLineStyle },
      series,
    }
  }

  // 折线图
  if (chartType === 'line') {
    const xData = data.map(row => String(row[xField] || ''))
    const configuredColor = props.panel.chart_config?.chartColor as string | undefined
    const lineStyleId = (props.panel.chart_config?.lineStyle as string) || 'smooth'
    const lineStyleConfig = LINE_STYLES.find(s => s.id === lineStyleId) ?? LINE_STYLES[0]!
    const defaultColors = ['#1677FF', '#36CFC9', '#52C41A']
    const series = yFields.map((field, idx) => ({
      name: field,
      type: 'line' as const,
      data: data.map(row => parseNumericValue(row[field]) ?? 0),
      smooth: lineStyleConfig.smooth,
      itemStyle: { color: configuredColor && idx === 0 ? configuredColor : defaultColors[idx % 3] },
      areaStyle: lineStyleConfig.areaFill ? { opacity: 0.1 } : undefined,
    }))

    const splitLineStyle = { lineStyle: { type: 'dashed' as const, color: theme.chartGridLine } }

    return {
      ...baseOption,
      xAxis: { type: 'category', data: xData, axisLabel: { color: theme.chartAxisText }, splitLine: { show: false } },
      yAxis: { type: 'value', axisLabel: { color: theme.chartAxisText }, splitLine: splitLineStyle },
      series,
    }
  }

  // 饼图
  if (chartType === 'pie') {
    const pieData = data.slice(0, 10).map(row => ({
      name: String(row[xField] || ''),
      value: parseNumericValue(row[yFields[0]]) ?? 0,
    }))

    return {
      ...baseOption,
      series: [{
        type: 'pie',
        radius: ['40%', '70%'],
        center: ['50%', '50%'],
        data: pieData,
        label: {
          color: theme.textColor,
          formatter: '{b}: {d}%',
        },
        itemStyle: {
          borderRadius: 4,
          borderColor: theme.panelBg,
          borderWidth: 2,
        },
      }],
    }
  }

  // 地图
  if (chartType === 'map' && mapLoaded.value) {
    const mapData = data.map(row => ({
      name: normalizeProvinceName(String(row[xField] || '')),
      value: parseNumericValue(row[yFields[0]]) ?? 0,
    }))

    // 计算数值范围
    const values = mapData.map(d => d.value).filter(v => v > 0)
    const minValue = values.length > 0 ? Math.min(...values) : 0
    const maxValue = values.length > 0 ? Math.max(...values) : 100

    const isDark = theme.pageBg === '#0f172a'

    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          if (params.value !== undefined && !isNaN(params.value)) {
            return `${params.name}: ${params.value.toLocaleString()}`
          }
          return `${params.name}: 暂无数据`
        },
        backgroundColor: theme.chartTooltipBg,
        borderColor: theme.chartTooltipBorder,
        textStyle: { color: theme.chartTooltipText },
      },
      visualMap: {
        min: minValue,
        max: maxValue,
        left: 'left',
        top: 'bottom',
        text: ['高', '低'],
        calculable: true,
        inRange: {
          color: isDark
            ? ['#7f1d1d', '#b91c1c', '#dc2626', '#ef4444', '#fca5a5']
            : ['#fee0d2', '#fcbba1', '#fc9272', '#fb6a4a', '#cb181d'],
        },
        textStyle: { color: theme.chartAxisText },
      },
      series: [{
        type: 'map',
        map: 'china',
        roam: true,
        zoom: 1.2,
        scaleLimit: { min: 0.8, max: 3 },
        label: {
          show: false,
          fontSize: 10,
          color: theme.textColor,
        },
        emphasis: {
          label: { show: true, fontWeight: 'bold' },
          itemStyle: {
            areaColor: '#ffd666',
            shadowBlur: 20,
            shadowColor: 'rgba(0, 0, 0, 0.3)',
          },
        },
        itemStyle: {
          areaColor: isDark ? '#374151' : '#f0f0f0',
          borderColor: isDark ? '#4b5563' : '#ffffff',
          borderWidth: 1,
        },
        data: mapData,
      }],
    }
  }

  return null
})

// 渲染图表
const renderChart = () => {
  if (!chartRef.value || props.panel.chart_type === 'table' || props.panel.chart_type === 'kpi') {
    return
  }

  // 地图需要等待加载完成
  if (props.panel.chart_type === 'map' && !mapLoaded.value) {
    return
  }

  if (chartInstance && chartInstance.getDom() !== chartRef.value) {
    chartInstance.dispose()
    chartInstance = null
  }

  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }

  if (chartOption.value) {
    chartInstance.resize()
    chartInstance.setOption(chartOption.value, true)
  } else {
    chartInstance.clear()
  }
}

// 监听数据变化
watch(() => [props.data, props.panel.chart_type, props.panel.chart_config, mapLoaded.value], async () => {
  await nextTick()
  renderChart()
}, { deep: true, flush: 'post' })

watch(chartRef, async (currentEl, previousEl) => {
  if (previousEl) {
    resizeObserver.unobserve(previousEl)
  }
  if (!currentEl) {
    chartInstance?.dispose()
    chartInstance = null
    return
  }
  resizeObserver.observe(currentEl)
  await nextTick()
  renderChart()
}, { flush: 'post' })

// 监听容器大小
const resizeObserver = new ResizeObserver(() => {
  chartInstance?.resize()
})

onMounted(() => {
  if (chartRef.value) {
    resizeObserver.observe(chartRef.value)
  }
  renderChart()
})

onUnmounted(() => {
  resizeObserver.disconnect()
  chartInstance?.dispose()
  chartInstance = null
})

// 数字人性化格式
const formatNumber = (num: number): { value: string; unit: string } => {
  const absNum = Math.abs(num)
  const sign = num < 0 ? '-' : ''

  if (absNum >= 100000000) {
    // 亿
    const val = absNum / 100000000
    return { value: sign + (val >= 10 ? val.toFixed(0) : val.toFixed(2)), unit: '亿' }
  } else if (absNum >= 10000) {
    // 万
    const val = absNum / 10000
    return { value: sign + (val >= 10 ? val.toFixed(0) : val.toFixed(2)), unit: '万' }
  } else if (absNum >= 1000) {
    return { value: sign + absNum.toLocaleString(), unit: '' }
  } else {
    return { value: sign + (Number.isInteger(absNum) ? absNum.toString() : absNum.toFixed(2)), unit: '' }
  }
}

// KPI 原始值
const kpiRawValue = computed(() => {
  const result = props.data?.result
  if (!result?.data?.length) return null
  const firstRow = result.data[0]
  const { yFields } = inferredFields.value
  if (!firstRow || yFields.length === 0) return null
  return firstRow[yFields[0]]
})

// KPI 值格式化
const kpiValue = computed(() => {
  const value = kpiRawValue.value
  if (value === null || value === undefined) return { value: '-', unit: '' }
  const numericValue = parseNumericValue(value)
  if (numericValue !== null) {
    return formatNumber(numericValue)
  }
  return { value: String(value || '-'), unit: '' }
})

// KPI 完整值（用于 tooltip）
const kpiFullValue = computed(() => {
  const value = kpiRawValue.value
  if (value === null || value === undefined) return ''
  const numericValue = parseNumericValue(value)
  if (numericValue !== null) {
    return numericValue.toLocaleString()
  }
  return String(value)
})
</script>

<template>
  <div ref="panelRef" class="panel-card flex flex-col h-full border border-solid rounded-xl overflow-hidden" :style="{ background: theme.panelBg, borderColor: theme.panelBorder }">
    <!-- 面板头部 -->
    <div :class="['flex items-center justify-between px-2.5 py-2 gap-2 min-h-10', !isPreviewMode && 'drag-handle cursor-move']" :style="{ background: theme.panelHeaderBg }">
      <div class="flex items-center gap-2 text-[13px] font-medium min-w-0 flex-1" :style="{ color: theme.textColor }">
        <span
          v-if="panel.chart_type !== 'kpi'"
          class="w-[3px] h-3.5 rounded-sm flex-shrink-0"
          :class="{
            'bg-indigo-500': panel.chart_type === 'table',
            'bg-blue-500': panel.chart_type === 'bar' || panel.chart_type === 'horizontal_bar',
            'bg-amber-500': panel.chart_type === 'line',
            'bg-emerald-500': panel.chart_type === 'pie',
            'bg-pink-500': panel.chart_type === 'kpi',
            'bg-teal-500': panel.chart_type === 'map'
          }"
        ></span>
        <span class="overflow-hidden text-ellipsis whitespace-nowrap">{{ panel.name }}</span>
      </div>
      <div v-if="!isPreviewMode" class="flex gap-0.5 flex-shrink-0">
        <!-- 图表类型切换 -->
        <button class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15" @click="showChartMenu = true" :style="{ color: theme.textMuted }">
          <i class="fa-solid fa-chart-bar"></i>
        </button>
        <!-- 图表样式配置（仅折线图/柱状图/条形图） -->
        <button
          v-if="['bar', 'horizontal_bar', 'line'].includes(panel.chart_type)"
          class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15"
          @click="showChartStyleModal = true"
          :style="{ color: panel.chart_config?.chartColor ? theme.accentColor : theme.textMuted }"
          title="配置图表颜色和样式"
        >
          <i class="fa-solid fa-palette"></i>
        </button>
        <!-- KPI图标配置（仅KPI类型） -->
        <button
          v-if="panel.chart_type === 'kpi'"
          class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15"
          @click="showKpiConfigModal = true"
          :style="{ color: panel.chart_config?.kpiIcon ? theme.accentColor : theme.textMuted }"
          title="配置图标和颜色"
        >
          <i class="fa-solid fa-palette"></i>
        </button>
        <!-- 字段配置（图表类型时显示） -->
        <button
          v-if="['bar', 'horizontal_bar', 'line', 'pie', 'map'].includes(panel.chart_type)"
          class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15"
          @click="showFieldConfigModal = true"
          :style="{ color: panel.chart_config?.xField || panel.chart_config?.yField ? theme.accentColor : theme.textMuted }"
          title="配置X轴/Y轴字段"
        >
          <i class="fa-solid fa-sliders"></i>
        </button>
        <!-- 编辑SQL -->
        <button class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15" @click="openSqlModal" :style="{ color: theme.textMuted }" title="编辑SQL">
          <i class="fa-solid fa-code"></i>
        </button>
        <!-- 刷新 -->
        <button class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15" @click="emit('refresh')" :style="{ color: theme.textMuted }">
          <i class="fa-solid fa-sync-alt"></i>
        </button>
        <!-- 重命名 -->
        <button class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-gray-500/15" @click="openRenameModal" :style="{ color: theme.textMuted }" title="重命名">
          <i class="fa-solid fa-pen"></i>
        </button>
        <!-- 删除 -->
        <button class="action-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs hover:bg-red-500/20 hover:!text-red-500" @click="emit('delete')" :style="{ color: theme.textMuted }">
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>
      <!-- 预览模式下的全屏按钮 -->
      <button
        v-if="isPreviewMode"
        class="fullscreen-btn w-6 h-6 flex items-center justify-center border-none bg-transparent rounded cursor-pointer transition-all duration-200 text-xs ml-auto opacity-0 hover:bg-gray-500/15"
        @click="requestFullscreen"
        :style="{ color: theme.textMuted }"
        title="全屏查看"
      >
        <i class="fa-solid fa-expand"></i>
      </button>
    </div>

    <!-- 面板内容 -->
    <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
      <!-- 加载状态 -->
      <div v-if="loading" class="flex-1 flex flex-col items-center justify-center gap-2 text-[13px] [&>i]:text-2xl [&>i]:opacity-50" :style="{ color: theme.textMuted }">
        <i class="fa-solid fa-spinner fa-spin"></i>
        <span>加载中...</span>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="error" class="flex-1 flex flex-col items-center justify-center gap-2 text-[13px] text-red-500 [&>i]:text-2xl">
        <i class="fa-solid fa-exclamation-triangle"></i>
        <span>{{ error }}</span>
      </div>

      <!-- 无数据 -->
      <div v-else-if="!data?.result?.data?.length" class="flex-1 flex flex-col items-center justify-center gap-2 text-[13px] [&>i]:text-2xl [&>i]:opacity-50" :style="{ color: theme.textMuted }">
        <i class="fa-solid fa-inbox"></i>
        <span>暂无数据</span>
      </div>

      <!-- 地图加载中 -->
      <div v-else-if="panel.chart_type === 'map' && !mapLoaded" class="flex-1 flex flex-col items-center justify-center gap-2 text-[13px] [&>i]:text-2xl [&>i]:opacity-50" :style="{ color: theme.textMuted }">
        <i class="fa-solid fa-spinner fa-spin"></i>
        <span>加载地图数据...</span>
      </div>

      <!-- KPI 卡片 -->
      <template v-else-if="panel.chart_type === 'kpi'">
        <div class="flex-1 flex flex-row items-center justify-between px-5 pb-4">
          <div class="flex items-baseline leading-none" :title="kpiFullValue">
            <span class="text-[32px] font-bold" :style="{ color: theme.textColor }">{{ kpiValue.value }}</span>
            <span v-if="kpiValue.unit" class="text-lg font-medium ml-0.5" :style="{ color: theme.textMuted }">{{ kpiValue.unit }}</span>
          </div>
          <div class="w-12 h-12 flex items-center justify-center text-[22px] rounded-xl" :style="{ color: currentKpiColor, background: currentKpiBgColor }">
            <i :class="['fa-solid', currentKpiIcon]"></i>
          </div>
        </div>
      </template>

      <!-- 表格 -->
      <template v-else-if="panel.chart_type === 'table'">
        <div class="flex-1 overflow-auto px-3 pb-3">
          <table class="w-full border-collapse border-spacing-0 text-[13px]" :style="{ color: theme.textColor }">
            <thead class="sticky top-0 z-[1]" :style="{ background: theme.tableHeaderBg, borderColor: theme.tableBorder }">
              <tr>
                <th v-for="col in data.result.columns" :key="col" class="px-4 py-3 text-left font-semibold whitespace-nowrap" :style="{ borderBottom: `1px solid ${theme.tableBorder}` }">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, idx) in data.result.data.slice(0, 10)"
                :key="idx"
                class="transition-colors duration-200 hover:bg-[var(--row-hover)]"
                :style="{ '--row-hover': theme.tableRowHover }"
              >
                <td
                  v-for="col in data.result.columns"
                  :key="col"
                  class="px-4 py-2.5 text-left"
                  :class="{ 'font-semibold tabular-nums': typeof row[col] === 'number' }"
                  :style="{ borderBottom: `1px solid ${theme.tableBorder}` }"
                >
                  {{ typeof row[col] === 'number' ? row[col].toLocaleString() : row[col] }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>

      <!-- 图表 -->
      <template v-else>
        <div ref="chartRef" class="flex-1 min-h-[200px]"></div>
      </template>
    </div>

    <!-- 图表类型切换弹窗 -->
    <el-dialog v-model="showChartMenu" title="切换图表类型" width="280px" append-to-body>
      <div class="grid grid-cols-2 gap-2">
        <button
          v-for="type in chartTypes"
          :key="type.key"
          class="flex items-center gap-2.5 px-3 py-2.5 rounded-lg border-none text-sm cursor-pointer transition-all duration-200"
          :style="{
            background: panel.chart_type === type.key ? 'rgba(22,119,255,0.1)' : 'transparent',
            color: panel.chart_type === type.key ? '#1677FF' : '#333',
            fontWeight: panel.chart_type === type.key ? '500' : '400'
          }"
          @click="emit('changeChartType', type.key); showChartMenu = false"
        >
          <i :class="['fa-solid', type.icon]"></i>
          <span>{{ type.label }}</span>
        </button>
      </div>
    </el-dialog>

    <!-- KPI 配置弹窗 -->
    <el-dialog v-model="showKpiConfigModal" title="KPI 图标配置" width="360px" append-to-body>
      <!-- 图标选择 -->
      <div class="mb-5 last:mb-0">
        <div class="text-[13px] mb-3 text-gray-500">选择图标</div>
        <div class="grid grid-cols-6 gap-2">
          <button
            v-for="item in KPI_ICONS"
            :key="item.icon"
            class="w-10 h-10 flex items-center justify-center border border-solid bg-transparent rounded-lg cursor-pointer text-base transition-all duration-200 hover:scale-110"
            :class="{ 'bg-[rgba(22,119,255,0.1)]': currentKpiIcon === item.icon }"
            :style="{
              color: currentKpiIcon === item.icon ? currentKpiColor : '#999',
              borderColor: currentKpiIcon === item.icon ? currentKpiColor : '#e5e7eb'
            }"
            @click="handleKpiIconSelect(item.icon)"
            :title="item.label"
          >
            <i :class="['fa-solid', item.icon]"></i>
          </button>
        </div>
      </div>
      <!-- 颜色选择 -->
      <div class="mb-5 last:mb-0">
        <div class="text-[13px] mb-3 text-gray-500">选择颜色</div>
        <div class="flex flex-wrap gap-2.5">
          <button
            v-for="item in KPI_COLORS"
            :key="item.color"
            class="w-9 h-9 flex items-center justify-center border-2 border-transparent rounded-full cursor-pointer transition-all duration-200 text-white text-xs hover:scale-110"
            :class="{ 'border-gray-300 shadow-[0_0_8px_currentColor]': currentKpiColor === item.color }"
            :style="{ background: item.color }"
            @click="handleKpiColorSelect(item.color)"
            :title="item.label"
          >
            <i v-if="currentKpiColor === item.color" class="fa-solid fa-check"></i>
          </button>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-between w-full">
          <el-button text @click="handleKpiReset">恢复默认</el-button>
          <el-button type="primary" @click="showKpiConfigModal = false">确定</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 图表样式配置弹窗 -->
    <el-dialog v-model="showChartStyleModal" title="图表样式配置" width="360px" append-to-body>
      <!-- 颜色选择 -->
      <div class="mb-5 last:mb-0">
        <div class="text-[13px] mb-3 text-gray-500">主题颜色</div>
        <div class="flex flex-wrap gap-2.5">
          <button
            v-for="item in CHART_COLOR_OPTIONS"
            :key="item.color"
            class="w-9 h-9 flex items-center justify-center border-2 border-transparent rounded-full cursor-pointer transition-all duration-200 text-white text-xs hover:scale-110"
            :class="{ 'border-gray-300 shadow-[0_0_8px_currentColor]': currentChartColor === item.color }"
            :style="{ background: item.color }"
            @click="handleChartColorSelect(item.color)"
            :title="item.label"
          >
            <i v-if="currentChartColor === item.color" class="fa-solid fa-check"></i>
          </button>
        </div>
      </div>
      <!-- 折线图样式选择（仅折线图显示） -->
      <div v-if="panel.chart_type === 'line'" class="mb-5 last:mb-0">
        <div class="text-[13px] mb-3 text-gray-500">线条样式</div>
        <div class="flex flex-col gap-2">
          <button
            v-for="item in LINE_STYLES"
            :key="item.id"
            class="px-4 py-2.5 border border-solid bg-transparent rounded-lg cursor-pointer text-left text-[13px] transition-all duration-200 hover:opacity-90"
            :class="currentLineStyle === item.id ? 'border-blue-500 text-blue-500 bg-blue-50' : 'border-gray-200 text-gray-700'"
            @click="handleLineStyleSelect(item.id)"
          >
            {{ item.label }}
          </button>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-between w-full">
          <el-button text @click="handleChartStyleReset">恢复默认</el-button>
          <el-button type="primary" @click="showChartStyleModal = false">确定</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- 字段配置弹窗 -->
    <el-dialog v-model="showFieldConfigModal" title="字段配置" width="380px" append-to-body>
      <div class="space-y-4">
        <!-- X 轴 -->
        <div>
          <label class="block text-[13px] mb-1.5 text-gray-500">
            X轴（分类维度）
            <span v-if="availableColumns.categoryColumns.length" class="ml-1 opacity-60">推荐: 分类列</span>
          </label>
          <el-select :model-value="inferredFields.xField" class="w-full" @change="(val: string) => handleFieldConfigChange('xField', val)">
            <el-option v-for="col in availableColumns.all" :key="col" :value="col" :label="`${col}${availableColumns.categoryColumns.includes(col) ? ' (分类)' : ' (数值)'}`" />
          </el-select>
        </div>
        <!-- Y 轴 -->
        <div>
          <label class="block text-[13px] mb-1.5 text-gray-500">
            Y轴（数值）
            <span v-if="availableColumns.numericColumns.length" class="ml-1 opacity-60">推荐: 数值列</span>
          </label>
          <el-select :model-value="inferredFields.yFields[0] || ''" class="w-full" @change="(val: string) => handleFieldConfigChange('yField', val)">
            <el-option v-for="col in availableColumns.all" :key="col" :value="col" :label="`${col}${availableColumns.numericColumns.includes(col) ? ' (数值)' : ' (分类)'}`" />
          </el-select>
        </div>
        <!-- 分组字段（可选） -->
        <div v-if="['bar', 'horizontal_bar', 'line'].includes(panel.chart_type)">
          <label class="block text-[13px] mb-1.5 text-gray-500">分组字段（可选）</label>
          <el-select :model-value="(panel.chart_config?.seriesField as string) || ''" class="w-full" @change="(val: string) => handleFieldConfigChange('seriesField', val)">
            <el-option value="" label="不分组" />
            <el-option v-for="col in availableColumns.categoryColumns" :key="col" :value="col" :label="col" />
          </el-select>
        </div>
      </div>
      <template #footer>
        <div class="flex justify-between w-full">
          <el-button text @click="handleFieldConfigReset">重置为智能推断</el-button>
          <el-button type="primary" @click="showFieldConfigModal = false">确定</el-button>
        </div>
      </template>
    </el-dialog>

    <!-- SQL 查看弹窗 -->
    <el-dialog v-model="showSqlModal" title="编辑 SQL" width="700px" append-to-body>
      <div v-if="panel.datasource_id" class="flex items-center gap-2 mb-3 text-xs text-gray-400">
        <i class="fa-solid fa-database"></i>
        <span>数据源: {{ datasourceName }}</span>
      </div>
      <div class="sql-editor-body !p-0">
        <Codemirror
          v-model="editingSql"
          :extensions="sqlExtensions"
          :style="{ maxHeight: '300px', overflow: 'auto' }"
        />
      </div>
      <template #footer>
        <el-button @click="showSqlModal = false">取消</el-button>
        <el-button type="primary" @click="handleSaveSql">
          <i class="fa-solid fa-play mr-1.5"></i>保存并执行
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showRenameModal" title="修改卡片名称" width="420px" append-to-body>
      <el-input
        v-model="editingName"
        maxlength="24"
        show-word-limit
        placeholder="请输入卡片名称"
        @keyup.enter="handleSaveName"
      />
      <template #footer>
        <el-button @click="showRenameModal = false">取消</el-button>
        <el-button type="primary" :disabled="!editingName.trim()" @click="handleSaveName">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* 全屏按钮悬停显示 */
.panel-card:hover .fullscreen-btn {
  opacity: 1;
}

/* 全屏状态样式 */
.panel-card:fullscreen {
  border-radius: 0;
}

.panel-card:fullscreen > div:last-of-type {
  padding: 20px;
}

.panel-card:fullscreen .flex-1.min-h-\[200px\] {
  min-height: 80vh;
}

/* SQL 弹窗 CodeMirror 深度样式 */
.sql-editor-body :deep(.cm-editor) {
  width: 100%;
}

.sql-editor-body :deep(.cm-scroller) {
  width: 100%;
}

.sql-editor-body :deep(.v-codemirror),
.sql-editor-body :deep(.cm-editor) {
  border: none !important;
  outline: none !important;
}

.sql-editor-body :deep(.cm-panels) {
  display: none !important;
}
</style>
