<script setup lang="ts">
/**
 * 本体对象图渲染：AntV G6 负责布局、缩放、平移、节点拖拽。
 * 选中节点只在图上高亮；查看详情/展开一跳由节点旁浮层按钮触发，父组件处理业务。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Graph } from '@antv/g6'

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
const graphRef = ref<any>(null)
const actionPosition = ref({ left: 0, top: 0 })
let resizeObserver: ResizeObserver | null = null
let resizeFrame: number | null = null
let lastContainerSize = { width: 0, height: 0 }

const selectedNode = computed(() => props.nodes.find(node => node.key === props.selectedKey) || null)

const toNodeData = (node: GraphCanvasNode) => ({
  id: node.key,
  data: {
    label: node.label,
    typeLabel: node.typeLabel || node.objectType
  }
})

const toEdgeData = (edge: GraphCanvasEdge) => ({
  id: edge.key,
  source: edge.fromKey,
  target: edge.toKey,
  data: { relationName: edge.relationName }
})

const graphData = () => ({
  nodes: props.nodes.map(toNodeData),
  edges: props.edges.map(toEdgeData)
})

const updateActionPosition = () => {
  const graph = graphRef.value
  const key = props.selectedKey
  if (!graph || !key) return
  try {
    const bounds = graph.getElementRenderBounds(key)
    if (!bounds) return
    const point = bounds.max || bounds.center
    const viewport = graph.getViewportByCanvas({ x: point[0], y: point[1] })
    actionPosition.value = { left: Math.max(8, viewport[0] - 74), top: viewport[1] + 8 }
  } catch (err) {
    console.warn('计算图节点操作浮层位置失败:', err)
  }
}

const fitToContainer = async () => {
  const graph = graphRef.value
  const container = containerRef.value
  if (!graph || !container) return
  const width = container.clientWidth
  const height = container.clientHeight
  if (width <= 0 || height <= 0) return
  // ResizeObserver 可能在 resize 过程中再次触发；尺寸未变化时不再 resize，避免循环。
  if (width === lastContainerSize.width && height === lastContainerSize.height) return
  lastContainerSize = { width, height }
  try {
    graph.resize(width, height)
    await graph.fitView()
    await nextTick()
    updateActionPosition()
  } catch (err) {
    console.warn('适配 G6 图容器失败:', err)
  }
}

const scheduleFitToContainer = () => {
  if (resizeFrame !== null) return
  resizeFrame = window.requestAnimationFrame(() => {
    resizeFrame = null
    void fitToContainer()
  })
}

const renderGraph = async () => {
  const graph = graphRef.value
  if (!graph) return
  try {
    await graph.setData(graphData())
    await graph.render()
    await graph.fitView()
    await nextTick()
    updateActionPosition()
  } catch (err) {
    console.error('刷新 G6 图失败:', err)
  }
}

const resetLayout = async () => {
  const graph = graphRef.value
  if (!graph) return
  try {
    await graph.layout()
    await graph.fitView()
    await nextTick()
    updateActionPosition()
  } catch (err) {
    console.warn('重置 G6 布局失败:', err)
  }
}

const syncSelectedState = () => {
  const graph = graphRef.value
  if (!graph) return
  try {
    for (const node of props.nodes) {
      if (node.key === props.selectedKey) graph.setElementState(node.key, ['selected'])
      else graph.setElementState(node.key, [])
    }
    void nextTick(updateActionPosition)
  } catch (err) {
    console.warn('同步图节点选中态失败:', err)
  }
}

onMounted(async () => {
  if (!containerRef.value) return
  const graph = new Graph({
    container: containerRef.value,
    autoFit: 'view',
    data: graphData(),
    node: {
      type: 'rect',
      style: {
        size: [176, 44],
        radius: 5,
        fill: '#ffffff',
        stroke: '#dbeafe',
        lineWidth: 1.2,
        labelText: (datum: any) => {
          const data = datum?.data || {}
          return `${data.typeLabel || ''} · ${data.label || ''}`
        },
        labelFill: '#475569',
        labelFontSize: 10,
        labelPlacement: 'center',
        cursor: 'pointer'
      },
      state: {
        selected: {
          fill: '#eff6ff',
          stroke: '#3b82f6',
          lineWidth: 2
        }
      }
    },
    edge: {
      type: 'line',
      style: {
        stroke: '#cbd5e1',
        lineWidth: 1.2,
        endArrow: false
      }
    },
    layout: {
      type: 'force',
      preventOverlap: true,
      nodeSize: [176, 44],
      linkDistance: 110,
      nodeStrength: -280,
      edgeStrength: 0.2,
      iterations: 300
    },
    behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element']
  })
  graph.on('node:click', (event: any) => {
    const key = String(event?.target?.id || '')
    if (key) emit('select', key)
  })
  graph.on('afterrender', updateActionPosition)
  graph.on('viewportchange', updateActionPosition)
  graphRef.value = graph
  resizeObserver = new ResizeObserver(() => {
    scheduleFitToContainer()
  })
  resizeObserver.observe(containerRef.value)
  try {
    await graph.render()
    await fitToContainer()
    syncSelectedState()
  } catch (err) {
    console.error('初始化 G6 图失败:', err)
  }
})

watch(
  () => [props.nodes, props.edges],
  () => {
    void renderGraph()
  },
  { deep: true }
)

watch(
  () => props.selectedKey,
  () => {
    syncSelectedState()
    void nextTick(updateActionPosition)
  }
)

onBeforeUnmount(() => {
  if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame)
  resizeObserver?.disconnect()
  resizeObserver = null
  graphRef.value?.destroy()
  graphRef.value = null
})

defineExpose({ resetLayout })
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
