<script setup lang="ts">
/**
 * 本体对象图渲染：ECharts graph 负责布局、缩放、平移、节点拖拽。
 * ECharts 的 graph 节点是 canvas 图元，不承载 HTML 子元素；
 * 点击节点只选中，查看详情/展开一跳由父页面固定操作栏处理。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

interface GraphCanvasNode {
  key: string
  objectType: string
  label: string
  typeLabel: string
}

interface GraphCanvasEdge {
  key: string
  fromKey: string
  toKey: string
  relationName: string
}

const props = defineProps({
  nodes: { type: Array as () => GraphCanvasNode[], default: () => [] },
  edges: { type: Array as () => GraphCanvasEdge[], default: () => [] },
  selectedKey: { type: String, default: '' }
})

const emit = defineEmits<{ select: [key: string] }>()

const containerRef = ref<HTMLDivElement | null>(null)
const selectedNode = computed(() => props.nodes.find(node => node.key === props.selectedKey) || null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let resizeFrame: number | null = null
let lastSize = { width: 0, height: 0 }
let renderFrame: number | null = null

const buildOption = (): any => {
  const selectedIndex = props.nodes.findIndex(node => node.key === props.selectedKey)
  return {
    animation: true,
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        if (params.dataType === 'edge') return String(params.data?.relationName || params.name || '')
        return String(params.data?.fullLabel || params.name || '')
      }
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        selectedMode: 'single',
        symbol: 'rect',
        symbolSize: [176, 44],
        force: {
          repulsion: 1200,
          edgeLength: 120,
          gravity: 0.06,
          layoutAnimation: true,
          friction: 0.2
        },
        label: {
          show: true,
          color: '#475569',
          fontSize: 10,
          formatter: (params: any) => {
            const data = params.data || {}
            const type = String(data.nodeType || '')
            const label = String(data.nodeLabel || params.name || '')
            return type ? `${type} · ${label}` : label
          }
        },
        itemStyle: {
          color: '#ffffff',
          borderColor: '#dbeafe',
          borderWidth: 1.2
        },
        lineStyle: {
          color: '#cbd5e1',
          width: 1.2,
          curveness: 0.04
        },
        emphasis: {
          focus: 'adjacency',
          itemStyle: { borderColor: '#3b82f6', borderWidth: 2 },
          lineStyle: { width: 2, color: '#93c5fd' }
        },
        select: {
          itemStyle: { color: '#eff6ff', borderColor: '#3b82f6', borderWidth: 2 }
        },
        data: props.nodes.map((node, index) => ({
          id: node.key,
          name: node.label,
          value: node.label,
          nodeLabel: node.label,
          nodeType: node.typeLabel || node.objectType,
          fullLabel: `${node.typeLabel || node.objectType} · ${node.label}`,
          itemStyle:
            index === selectedIndex
              ? { color: '#eff6ff', borderColor: '#3b82f6', borderWidth: 2 }
              : undefined
        })),
        links: props.edges.map(edge => ({
          source: edge.fromKey,
          target: edge.toKey,
          value: edge.relationName,
          relationName: edge.relationName
        }))
      }
    ]
  }
}

const renderChart = (notMerge = false) => {
  if (!chart) return
  chart.setOption(buildOption(), notMerge)
}

const scheduleRender = () => {
  if (renderFrame !== null) return
  renderFrame = window.requestAnimationFrame(() => {
    renderFrame = null
    renderChart()
  })
}

const fitChart = () => {
  if (!chart || !containerRef.value) return
  const width = containerRef.value.clientWidth
  const height = containerRef.value.clientHeight
  if (width <= 0 || height <= 0) return
  if (width !== lastSize.width || height !== lastSize.height) {
    lastSize = { width, height }
    chart.resize({ width, height })
  }
}

const scheduleFitChart = () => {
  if (resizeFrame !== null) return
  resizeFrame = window.requestAnimationFrame(() => {
    resizeFrame = null
    fitChart()
  })
}

const resetLayout = () => {
  renderChart(true)
  fitChart()
}

defineExpose({ resetLayout })

onMounted(() => {
  if (!containerRef.value) return
  chart = echarts.init(containerRef.value, undefined, { renderer: 'canvas' })
  chart.on('click', (params: any) => {
    if (params.dataType !== 'node') return
    const key = String(params.data?.id || props.nodes[params.dataIndex]?.key || '')
    if (key) emit('select', key)
  })
  resizeObserver = new ResizeObserver(scheduleFitChart)
  resizeObserver.observe(containerRef.value)
  renderChart(true)
  fitChart()
})

watch(
  () => [props.nodes, props.edges],
  scheduleRender,
  { deep: true }
)

watch(
  () => props.selectedKey,
  scheduleRender
)

onBeforeUnmount(() => {
  if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame)
  if (renderFrame !== null) window.cancelAnimationFrame(renderFrame)
  resizeObserver?.disconnect()
  resizeObserver = null
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="containerRef" class="relative h-full min-h-[420px] w-full overflow-hidden rounded bg-white" />
</template>
