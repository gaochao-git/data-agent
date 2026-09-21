<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import ResizableTable from './ResizableTable.vue'
import { normalizeChartType, type ChartType } from '../chartTypes'
import { registerChinaMap, isChinaMapRegistered } from '../utils/mapRegistry'
import { getSafeNumericColumns, toSafeNumber } from '../utils'
import { buildEChartsOption } from '../utils/chartOptionBuilder'

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
  return buildEChartsOption({
    type: chartType.value,
    rows,
    encoding: { xField: x, yFields: ys },
    labels: seriesLabels.value,
    graphData: hasGraphSpecData.value ? buildGraphDataFromSpec() : null,
    isMapReady: mapLoaded.value,
    toNumber: numberValue,
  })
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
  <div :class="isFullscreen ? 'fixed inset-0 z-[var(--z-fullscreen-layer)] bg-slate-950/55 p-4' : ''">
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
