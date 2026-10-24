<script setup lang="ts">
/**
 * 本体对象图渲染：AntV G6 负责布局、缩放、平移、节点拖拽。
 * 选中节点只在图上高亮；查看详情/展开一跳由节点旁浮层按钮触发，父组件处理业务。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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

let renderInFlight = false
let renderQueued = false
let selectionQueued = false
let destroyed = false

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



const enqueueRender = () => {
  if (destroyed) return
  if (renderInFlight) {
    renderQueued = true
    return
  }
  renderInFlight = true
  void renderGraph().finally(() => {
    renderInFlight = false
    if (selectionQueued) {
      selectionQueued = false
      void syncSelectedState()
    }
    if (renderQueued) {
      renderQueued = false
      enqueueRender()
    }
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
  } catch (err) {
    console.error('刷新 G6 图失败:', err)
  }
}

const resizeGraph = async () => {
  const graph = graphRef.value
  const container = containerRef.value
  if (!graph || !container) return
  const width = container.clientWidth
  const height = container.clientHeight
  if (width <= 0 || height <= 0) return
  try {
    graph.resize(width, height)
    await graph.fitView()
  } catch (err) {
    console.warn('调整 G6 画布尺寸失败:', err)
  }
}

const resetLayout = async () => {
  const graph = graphRef.value
  if (!graph) return
  try {
    await graph.layout()
    await graph.fitView()
  } catch (err) {
    console.warn('重置 G6 布局失败:', err)
  }
}

const syncSelectedState = async () => {
  if (destroyed) return
  if (renderInFlight) {
    selectionQueued = true
    return
  }
  const graph = graphRef.value
  if (!graph) return
  try {
    const states: Record<string, string[]> = {}
    for (const node of props.nodes) {
      states[node.key] = node.key === props.selectedKey ? ['selected'] : []
    }
    // 官方批量接口：一次状态提交，避免逐节点 setElementState 触发渲染风暴。
    await graph.setElementState(states)
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
        radius: 0,
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
      // 对象图节点数不稳定；先用非迭代网格布局，彻底避开 force 主线程计算。
      type: 'grid',
      cols: 4,
      nodeSize: [230, 100],
      preventOverlap: true,
      condense: false
    },
    // 节点不单拖，避免拖拽触发重新布局；画布拖拽和缩放保留。
    behaviors: ['drag-canvas', 'zoom-canvas']
  })
  graph.on('node:click', (event: any) => {
    const key = String(event?.target?.id || '')
    if (key) emit('select', key)
  })
  graphRef.value = graph
  try {
    await graph.render()
    await graph.fitView()
    await syncSelectedState()
  } catch (err) {
    console.error('初始化 G6 图失败:', err)
  }
})

// 不要用 `() => [props.nodes, props.edges]`：getter 每次返回新数组，
// 子组件任何本地重渲染都会误触发一次全量 setData/render，形成渲染风暴。
watch(() => props.nodes, () => enqueueRender(), { deep: true })
watch(() => props.edges, () => enqueueRender(), { deep: true })

watch(
  () => props.selectedKey,
  () => {
    void syncSelectedState()
  }
)

onBeforeUnmount(() => {
  destroyed = true
  graphRef.value?.destroy()
  graphRef.value = null
})

defineExpose({ resetLayout, resizeGraph })
</script>

<template>
  <div ref="containerRef" class="relative h-full min-h-[420px] w-full overflow-hidden rounded bg-white" />
</template>
