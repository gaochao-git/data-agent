<script setup lang="ts">
/**
 * 本体对象图渲染：ECharts graph 负责布局、缩放、平移、节点拖拽。
 * 点击节点只选中；查看详情/展开一跳由节点旁浮层按钮触发。
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

const emit = defineEmits<{
  select: [key: string]
  'view-detail': [key: string]
  expand: [key: string]
}>()

const containerRef = ref<HTMLDivElement | null>(null)
const actionPosition = ref({ left: 0, top: 0 })
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null
let resizeFrame: number | null = null
let lastSize = { width: 0, height: 0 }
let renderFrame: number | null = null

const selectedNode = computed(() => props.nodes.find(node => node.key === props.selectedKey) || null)

const buildOption = (): any => {
  const selectedIndex = props.nodes.findIndex(node => node.key === props.selectedKey)
  return {
    animation: true,
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        if (params.dataType === 'edge') {
          return String(params.data?.relationName || params.name || '')
        }
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
        symbol: 'roundRect',
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
            const type = String(data.typeLabel || '')
            const label = String(data.nodeLabel || params.name || '')
            return type ? `${type} · ${label}` : label
          }
        },
        itemStyle: {
          color: '#ffffff',
          borderColor: '#dbeafe',
          borderWidth: 1.2,
          borderRadius: 5
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
          typeLabel: node.typeLabel || node.objectType,
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

const clampActionPosition = (left: number, top: number) => {
  const width = containerRef.value?.clientWidth || 800
  const height = containerRef.value?.clientHeight || 420
  return {
    left: Math.max(8, Math.min(width - 200, left)),
    top: Math.max(8, Math.min(height - 48, top))
  }
}

const updateActionPosition = () => {
  if (!chart || !props.selectedKey) return
  try {
    const series = (chart as any).getModel()?.getSeriesByIndex(0)
    const data = series?.getData?.()
    if (!data) return
    const index = props.nodes.findIndex(node => node.key === props.selectedKey)
    if (index < 0) return
    const layout = data.getItemLayout(index)
    if (!layout) return
    const pixel = chart.convertToPixel({ seriesIndex: 0 }, layout) as unknown as number[]
    if (!Array.isArray(pixel) || pixel.length < 2 || !Number.isFinite(Number(pixel[0]))) return
    actionPosition.value = clampActionPosition(Number(pixel[0]) - 74, Number(pixel[1]) + 25)
  } catch (err) {
    console.warn('计算图节点操作浮层位置失败:', err)
  }
}

const renderChart = (notMerge = false) => {
  if (!chart) return
  chart.setOption(buildOption(), notMerge)
  void nextTick(updateActionPosition)
}

const scheduleRender = () => {
  if (renderFrame !== null) return
  renderFrame = window.requestAnimationFrame(() => {
    renderFrame = null
    renderChart()
    void nextTick(updateActionPosition)
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
  void nextTick(updateActionPosition)
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
    if (!key) return
    const event = params.event
    if (event && Number.isFinite(Number(event.offsetX)) && Number.isFinite(Number(event.offsetY))) {
      actionPosition.value = clampActionPosition(Number(event.offsetX) - 74, Number(event.offsetY) + 25)
    }
    emit('select', key)
  })
  chart.on('graphroam', () => {
    void nextTick(updateActionPosition)
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
  () => {
    scheduleRender()
    void nextTick(updateActionPosition)
  }
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
  <div ref="containerRef" class="relative h-full min-h-[420px] w-full overflow-hidden rounded bg-white">
    <div
      v-if="selectedNode"
      class="absolute z-10 flex -translate-y-1/2 items-center gap-1 rounded bg-white/95 p-1 shadow-sm ring-1 ring-slate-200"
      :style="{ left: `${actionPosition.left}px`, top: `${actionPosition.top}px` }"
    >
      <button
        type="button"
        class="rounded border border-slate-200 bg-white px-2 py-0.5 text-[10px] text-slate-600 hover:border-blue-300 hover:text-blue-600"
        @click.stop="emit('view-detail', selectedKey)"
      >
        查看详情
      </button>
      <button
        type="button"
        class="rounded border border-slate-200 bg-white px-2 py-0.5 text-[10px] text-slate-600 hover:border-blue-300 hover:text-blue-600"
        @click.stop="emit('expand', selectedKey)"
      >
        展开一跳
      </button>
    </div>
  </div>
</template>
