<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import ResizableTable from './ResizableTable.vue'
import { normalizeChartType, type ChartType } from '../chartTypes'
import { registerChinaMap, normalizeProvinceName, isChinaMapRegistered } from '../utils/mapRegistry'
import { getSafeNumericColumns, isSafeNumericValue, toSafeNumber } from '../utils'

interface ChartSpecSeries {
  field?: string
  label?: string
  format?: string
}

interface ChartSpec {
  version?: string
  title?: string
  chart_type?: ChartType | string
  data?: Record<string, unknown>[] | {
    nodes?: Array<Record<string, unknown> | string>
    links?: Array<Record<string, unknown>>
    edges?: Array<Record<string, unknown>>
  }
  nodes?: Array<Record<string, unknown> | string>
  links?: Array<Record<string, unknown>>
  edges?: Array<Record<string, unknown>>
  encoding?: {
    x?: string
    y?: string[]
    series?: string
  }
  series?: ChartSpecSeries[]
  meta?: Record<string, unknown>
}

const props = defineProps<{
  spec: ChartSpec
}>()

const chartRef = ref<HTMLDivElement | null>(null)
const isFullscreen = ref(false)
const mapLoaded = ref(isChinaMapRegistered())
const mapLoading = ref(false)
let chartInstance: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const dataRows = computed(() => Array.isArray(props.spec?.data) ? props.spec.data : [])
const graphPayload = computed(() => {
  const data = props.spec?.data
  return data && typeof data === 'object' && !Array.isArray(data) ? data : props.spec
})
const chartType = computed<ChartType>(() => {
  return normalizeChartType(props.spec?.chart_type)
})
const numericChartTypes = new Set<ChartType>(['bar', 'horizontal_bar', 'line', 'area', 'pie', 'map', 'combo', 'funnel'])
const graphNodeCount = computed(() => Array.isArray(graphPayload.value?.nodes) ? graphPayload.value.nodes.length : 0)
const graphLinkCount = computed(() => {
  if (Array.isArray(graphPayload.value?.links)) return graphPayload.value.links.length
  if (Array.isArray(graphPayload.value?.edges)) return graphPayload.value.edges.length
  return 0
})
const hasGraphSpecData = computed(() => chartType.value === 'graph' && graphNodeCount.value > 0 && graphLinkCount.value > 0)
const chartBodyHeightClass = computed(() => {
  if (isFullscreen.value) return 'h-[calc(100vh-132px)]'
  return chartType.value === 'funnel' ? 'h-[360px]' : 'h-72'
})
const xField = computed(() => String(props.spec?.encoding?.x || inferXField()))
const yFields = computed(() => {
  const fields = props.spec?.encoding?.y
  if (Array.isArray(fields) && fields.length) {
    const requestedFields = fields.map(item => String(item)).filter(Boolean)
    return numericChartTypes.has(chartType.value)
      ? requestedFields.filter(field => safeNumericColumns.value.includes(field))
      : requestedFields
  }
  return inferYFields()
})
const comboYFields = computed(() => {
  const fields = yFields.value
  return chartType.value === 'combo' ? fields.slice(0, 2) : fields
})
const tableColumns = computed(() => {
  const seen = new Set<string>()
  dataRows.value.forEach((row) => {
    Object.keys(row || {}).forEach(key => seen.add(key))
  })
  return Array.from(seen).map(key => ({ key, title: key, width: 150 }))
})
const safeNumericColumns = computed(() => getSafeNumericColumns(dataRows.value, tableColumns.value.map(column => column.key)))
const chartUnavailable = computed(() => {
  return numericChartTypes.has(chartType.value) && dataRows.value.length > 0 && yFields.value.length === 0
})
const seriesLabels = computed(() => {
  const labels: Record<string, string> = {}
  for (const item of props.spec?.series || []) {
    if (item?.field) labels[String(item.field)] = String(item.label || item.field)
  }
  return labels
})

const colors = ['#1677FF', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#06B6D4', '#EC4899', '#64748B']

async function ensureMapLoaded() {
  if (mapLoaded.value || mapLoading.value) return
  mapLoading.value = true
  try {
    await registerChinaMap()
    mapLoaded.value = true
  } catch (error) {
    console.error('[ChartSpecRenderer] 地图数据加载失败:', error)
  } finally {
    mapLoading.value = false
  }
}

function inferXField() {
  const first = dataRows.value[0]
  if (!first) return ''
  const keys = Object.keys(first)
  const ySet = new Set(inferYFields())
  return keys.find(key => !ySet.has(key)) || keys[0] || ''
}

function inferYFields() {
  return safeNumericColumns.value.slice(0, 4)
}

function numberValue(value: unknown) {
  return toSafeNumber(value)
}

function getLabel(field: string) {
  return seriesLabels.value[field] || field
}

function buildGraphData(rows: Record<string, unknown>[], sourceField: string, targetField: string, valueField?: string) {
  const nodeMap = new Map<string, number>()
  const links: Array<{ source: string; target: string; value?: number }> = []

  rows.forEach((row) => {
    const source = String(row.source ?? row.from ?? row.Source ?? row[sourceField] ?? '').trim()
    const target = String(row.target ?? row.to ?? row.Target ?? row[targetField] ?? '').trim()
    if (!source || !target) return
    const value = numberValue(valueField ? row[valueField] : row.value ?? row.weight ?? row.count) ?? undefined
    nodeMap.set(source, (nodeMap.get(source) || 0) + (value ?? 1))
    nodeMap.set(target, nodeMap.get(target) || 0)
    links.push({ source, target, value })
  })

  const values = Array.from(nodeMap.values())
  const maxValue = values.length ? Math.max(...values) : 1
  const nodes = Array.from(nodeMap.entries()).map(([name, value]) => ({
    name,
    value,
    symbolSize: 18 + (maxValue > 0 ? (value / maxValue) * 22 : 0),
  }))
  return { nodes, links }
}

function buildGraphDataFromSpec() {
  const specNodes = Array.isArray(graphPayload.value?.nodes) ? graphPayload.value.nodes : []
  const specLinks = Array.isArray(graphPayload.value?.links)
    ? graphPayload.value.links
    : Array.isArray(graphPayload.value?.edges) ? graphPayload.value.edges : []
  if (!specNodes.length || !specLinks.length) return null
  const nodes = specNodes.map((item) => {
    if (typeof item === 'string') return { name: item }
    const name = String(item.name ?? item.id ?? '').trim()
    return { ...item, name: name || String(item.label ?? '') }
  }).filter(item => String(item.name || '').trim())
  const links = specLinks.map((item) => ({
    ...item,
    source: String(item.source ?? item.from ?? '').trim(),
    target: String(item.target ?? item.to ?? '').trim(),
  })).filter(item => item.source && item.target)
  return nodes.length && links.length ? { nodes, links } : null
}

function buildOption(): echarts.EChartsOption {
  const rows = dataRows.value
  const x = xField.value
  const ys = yFields.value
  if (chartType.value === 'graph') {
    const graphData = buildGraphDataFromSpec() || (rows.length && ys.length ? buildGraphData(rows, x, ys[0] || '', ys[1]) : null)
    if (!graphData || !graphData.nodes.length || !graphData.links.length) return {}
    return {
      tooltip: { trigger: 'item' },
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        label: { show: true, color: '#334155' },
        force: { repulsion: 180, edgeLength: [60, 160] },
        lineStyle: { color: '#94A3B8', width: 1.4, curveness: 0.18 },
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 8,
        emphasis: { focus: 'adjacency' },
        data: graphData.nodes,
        links: graphData.links,
      }],
      color: colors,
    }
  }
  if (!rows.length || !ys.length) return {}

  if (chartType.value === 'map') {
    const y = ys[0] || ''
    if (!y || !mapLoaded.value) return {}
    const mapData = rows.map(row => ({
      name: normalizeProvinceName(String(row[x] ?? '')),
      value: numberValue(row[y]) ?? 0,
    }))
    const values = mapData.map(item => item.value).filter(value => value > 0)
    const minValue = values.length ? Math.min(...values) : 0
    const maxValue = values.length ? Math.max(...values) : 100
    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => isSafeNumericValue(params.value)
          ? `${params.name}: ${String(params.value)}`
          : `${params.name}: 暂无数据`,
      },
      visualMap: {
        min: minValue,
        max: maxValue,
        left: 'left',
        bottom: 8,
        text: ['高', '低'],
        calculable: true,
        inRange: { color: ['#E0F2FE', '#7DD3FC', '#0EA5E9', '#0369A1'] },
        textStyle: { color: '#64748B', fontSize: 11 },
      },
      series: [{
        type: 'map',
        map: 'china',
        roam: true,
        zoom: 1.18,
        itemStyle: { areaColor: '#F8FAFC', borderColor: '#FFFFFF', borderWidth: 1 },
        emphasis: { label: { show: true }, itemStyle: { areaColor: '#FBBF24' } },
        data: mapData,
      }],
    }
  }

  if (chartType.value === 'pie') {
    const y = ys[0] || ''
    if (!y) return {}
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { type: 'scroll', bottom: 0, textStyle: { color: '#475569', fontSize: 11 } },
      series: [{
        name: getLabel(y),
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '45%'],
        itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
        data: rows.map(row => ({ name: String(row[x] ?? ''), value: numberValue(row[y]) ?? 0 })),
      }],
      color: colors,
    }
  }

  if (chartType.value === 'funnel') {
    const y = ys[0] || ''
    if (!y) return {}
    const funnelData = rows.map(row => ({ name: String(row[x] ?? ''), value: numberValue(row[y]) ?? 0 }))
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c}' },
      legend: { show: false },
      series: [{
        name: getLabel(y),
        type: 'funnel',
        left: '5%',
        top: 12,
        bottom: 12,
        width: '90%',
        minSize: '18%',
        maxSize: '100%',
        sort: 'descending',
        gap: 4,
        label: {
          show: true,
          position: 'inside',
          formatter: '{b}\n{c}',
          color: '#FFFFFF',
          fontSize: 12,
          fontWeight: 600,
          lineHeight: 18,
        },
        labelLine: { show: false },
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        emphasis: { label: { fontSize: 13, fontWeight: 700 } },
        data: funnelData,
      }],
      color: colors,
    }
  }

  const xData = rows.map(row => String(row[x] ?? ''))
  const isHorizontalBar = chartType.value === 'horizontal_bar'
  const isBar = chartType.value === 'bar' || isHorizontalBar
  const isCombo = chartType.value === 'combo'
  const axisYFields = comboYFields.value
  const useDualAxisCombo = isCombo && axisYFields.length > 1
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: isBar ? 'shadow' : 'line' } },
    legend: { show: axisYFields.length > 1, top: 0, textStyle: { color: '#475569', fontSize: 11 } },
    grid: { left: 24, right: useDualAxisCombo ? 42 : 16, top: axisYFields.length > 1 ? 34 : 18, bottom: 28, containLabel: true },
    xAxis: isHorizontalBar
      ? {
          type: 'value',
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: '#64748B', fontSize: 11 },
          splitLine: { lineStyle: { color: '#E2E8F0', type: 'dashed' } },
        }
      : {
          type: 'category',
          data: xData,
          axisTick: { show: false },
          axisLine: { lineStyle: { color: '#CBD5E1' } },
          axisLabel: { color: '#64748B', fontSize: 11, rotate: xData.length > 8 ? 35 : 0 },
        },
    yAxis: isHorizontalBar
      ? {
          type: 'category',
          data: xData,
          axisTick: { show: false },
          axisLine: { lineStyle: { color: '#CBD5E1' } },
          axisLabel: { color: '#64748B', fontSize: 11 },
        }
      : useDualAxisCombo
        ? [
            {
              type: 'value',
              axisLine: { show: false },
              axisTick: { show: false },
              axisLabel: { color: '#64748B', fontSize: 11 },
              splitLine: { lineStyle: { color: '#E2E8F0', type: 'dashed' } },
            },
            {
              type: 'value',
              position: 'right',
              axisLine: { show: true, lineStyle: { color: '#CBD5E1' } },
              axisTick: { show: false },
              axisLabel: { color: '#64748B', fontSize: 11, margin: 10 },
              splitLine: { show: false },
            },
          ]
        : {
            type: 'value',
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: '#64748B', fontSize: 11 },
            splitLine: { lineStyle: { color: '#E2E8F0', type: 'dashed' } },
          },
    series: axisYFields.map((field, index) => ({
      name: getLabel(field),
      type: (isBar || (isCombo && index === 0)) ? 'bar' : 'line',
      yAxisIndex: useDualAxisCombo ? (index === 0 ? 0 : 1) : 0,
      smooth: !(isBar || (isCombo && index === 0)),
      barMaxWidth: 36,
      areaStyle: chartType.value === 'area' ? { opacity: 0.18 } : undefined,
      itemStyle: {
        color: colors[index % colors.length],
        borderRadius: isHorizontalBar ? [0, 4, 4, 0] : isBar || (isCombo && index === 0) ? [4, 4, 0, 0] : undefined,
      },
      data: rows.map(row => numberValue(row[field])),
      connectNulls: false,
    })) as echarts.SeriesOption[],
    color: colors,
  }
}

function renderChart() {
  if (!chartRef.value || ['table', 'kpi'].includes(chartType.value)) return
  if (chartType.value === 'map' && !mapLoaded.value) {
    void ensureMapLoaded()
    return
  }
  if (chartInstance && chartInstance.getDom() !== chartRef.value) {
    chartInstance.dispose()
    chartInstance = null
  }
  if (!chartInstance) chartInstance = echarts.init(chartRef.value)
  chartInstance.setOption(buildOption(), true)
}

function resizeChart() {
  chartInstance?.resize()
}

function scheduleResizeChart() {
  void nextTick(() => {
    window.requestAnimationFrame(() => {
      resizeChart()
      window.setTimeout(resizeChart, 80)
    })
  })
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
  scheduleResizeChart()
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !isFullscreen.value) return
  isFullscreen.value = false
  scheduleResizeChart()
}

function observeChartElement() {
  resizeObserver?.disconnect()
  if (chartRef.value) resizeObserver?.observe(chartRef.value)
}

onMounted(() => {
  if (chartType.value === 'map') void ensureMapLoaded()
  resizeObserver = new ResizeObserver(() => resizeChart())
  observeChartElement()
  renderChart()
  window.addEventListener('resize', resizeChart)
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeChart)
  window.removeEventListener('keydown', handleKeydown)
  resizeObserver?.disconnect()
  resizeObserver = null
  chartInstance?.dispose()
  chartInstance = null
})

watch(
  () => props.spec,
  () => {
    if (['table', 'kpi'].includes(chartType.value)) {
      chartInstance?.dispose()
      chartInstance = null
      return
    }
    if (chartType.value === 'map' && !mapLoaded.value) {
      void ensureMapLoaded()
      return
    }
    renderChart()
  },
  { deep: true }
)

watch(mapLoaded, () => {
  if (chartType.value === 'map') renderChart()
})

watch(chartRef, () => {
  if (!chartRef.value) {
    chartInstance?.dispose()
    chartInstance = null
    return
  }
  observeChartElement()
  renderChart()
})
</script>

<template>
  <div :class="isFullscreen ? 'fixed inset-0 z-[9999] bg-slate-950/55 p-4' : ''">
    <div
      class="overflow-hidden rounded-lg border border-slate-200 bg-white"
      :class="isFullscreen ? 'mx-auto flex h-full w-full max-w-[1440px] flex-col rounded-xl shadow-2xl' : ''"
    >
    <div class="flex items-center justify-between gap-3 border-b border-slate-100 px-3 py-2">
      <div class="min-w-0">
        <div class="truncate text-sm font-semibold text-slate-800">{{ spec.title || '图表预览' }}</div>
        <div class="mt-0.5 font-mono text-[10px] text-slate-400">
          {{ chartType }} · {{ hasGraphSpecData ? `${graphNodeCount} nodes / ${graphLinkCount} links` : `${dataRows.length} rows` }}
        </div>
      </div>
      <div class="flex shrink-0 items-center gap-1">
        <button
          type="button"
          class="flex h-8 w-8 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-blue-600"
          :title="isFullscreen ? '退出全屏' : '全屏查看'"
          @click="toggleFullscreen"
        >
          <i :class="isFullscreen ? 'fa-solid fa-compress' : 'fa-solid fa-expand'"></i>
        </button>
        <i class="fa-solid fa-chart-line text-sm text-blue-500"></i>
      </div>
    </div>

    <div v-if="!dataRows.length && !hasGraphSpecData" class="flex h-52 items-center justify-center text-xs text-slate-400">
      暂无可渲染数据
    </div>
    <div v-else-if="chartUnavailable" class="flex flex-col items-center justify-center gap-2 text-xs text-slate-400" :class="chartBodyHeightClass">
      <i class="fa-solid fa-chart-column text-2xl opacity-50"></i>
      <span>当前结果没有可安全计算的数值字段</span>
    </div>
    <div v-else-if="chartType === 'map' && !mapLoaded" class="flex items-center justify-center gap-2 text-xs text-slate-400" :class="chartBodyHeightClass">
      <i class="fa-solid fa-spinner fa-spin"></i>
      <span>加载地图数据...</span>
    </div>
    <div v-else-if="chartType === 'table'" :class="chartBodyHeightClass">
      <ResizableTable :columns="tableColumns" :data="dataRows" max-height="100%" />
    </div>
    <div v-else-if="chartType === 'kpi'" class="grid grid-cols-2 gap-2 p-3" :class="isFullscreen ? 'min-h-[calc(100vh-132px)] content-start' : ''">
      <div v-for="field in yFields" :key="field" class="rounded-md border border-slate-100 bg-slate-50 p-3">
        <div class="text-xs text-slate-400">{{ getLabel(field) }}</div>
        <div class="mt-1 truncate text-xl font-semibold text-slate-800">{{ dataRows[0]?.[field] ?? '-' }}</div>
      </div>
    </div>
    <div v-else ref="chartRef" class="w-full" :class="chartBodyHeightClass"></div>
    </div>
  </div>
</template>
