<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Graph } from '@antv/g6'

const containerRef = ref<HTMLDivElement | null>(null)
const status = ref('初始化中…')
const selected = ref('')
const layoutType = ref<'grid' | 'force' | 'circular'>('grid')
let graph: any = null
let destroyed = false

const nodes = Array.from({ length: 30 }, (_, i) => ({
  id: `n${i}`,
  data: {
    label: `节点 ${i + 1}`,
    typeLabel: i < 3 ? '客户' : i < 13 ? '订单' : '订单明细'
  }
}))
const edges = Array.from({ length: 45 }, (_, i) => ({
  id: `e${i}`,
  source: `n${i % 3}`,
  target: `n${3 + (i % 12)}`
}))

const buildLayout = () => {
  if (layoutType.value === 'force') {
    return {
      type: 'force',
      preventOverlap: true,
      nodeSize: [200, 80],
      linkDistance: 140,
      iterations: 80,
      layoutAnimation: false
    }
  }
  if (layoutType.value === 'circular') {
    return { type: 'circular', radius: 360, ordering: 'degree' }
  }
  return { type: 'grid', cols: 5, nodeSize: [210, 90], preventOverlap: true, condense: false }
}

const createGraph = async () => {
  if (!containerRef.value || destroyed) return
  graph?.destroy()
  graph = new Graph({
    container: containerRef.value,
    autoFit: 'view',
    data: { nodes, edges },
    node: {
      type: 'rect',
      style: {
        size: [176, 44],
        radius: 0,
        fill: '#ffffff',
        stroke: '#dbeafe',
        lineWidth: 1.2,
        labelText: (d: any) => `${d.data.typeLabel} · ${d.data.label}`,
        labelFill: '#475569',
        labelFontSize: 10,
        labelPlacement: 'center',
        cursor: 'pointer'
      },
      state: { selected: { fill: '#eff6ff', stroke: '#3b82f6', lineWidth: 2 } }
    },
    edge: { type: 'line', style: { stroke: '#cbd5e1', lineWidth: 1.2 } },
    layout: buildLayout(),
    behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element']
  })
  graph.on('node:click', async (event: any) => {
    const key = String(event?.target?.id || '')
    if (!key) return
    selected.value = key
    status.value = `选中 ${key}`
    const states: Record<string, string[]> = {}
    for (const node of nodes) states[node.id] = node.id === key ? ['selected'] : []
    try {
      await graph.setElementState(states)
    } catch (err) {
      status.value = `选中态失败: ${String(err)}`
    }
  })
  await graph.render()
  await graph.fitView()
  status.value = `${layoutType.value} · ${nodes.length} 节点 · ${edges.length} 边`
}

const switchLayout = async (type: 'grid' | 'force' | 'circular') => {
  layoutType.value = type
  await nextTick()
  await createGraph()
}

const fit = () => void graph?.fitView()
const destroy = () => {
  destroyed = true
  graph?.destroy()
  graph = null
}

onMounted(() => void createGraph())
onBeforeUnmount(destroy)
</script>

<template>
  <div class="flex h-screen flex-col bg-slate-50">
    <div class="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-2">
      <span class="text-sm font-semibold text-slate-800">G6 独立验证</span>
      <el-button size="small" :type="layoutType === 'grid' ? 'primary' : 'default'" @click="switchLayout('grid')">Grid</el-button>
      <el-button size="small" :type="layoutType === 'force' ? 'primary' : 'default'" @click="switchLayout('force')">Force</el-button>
      <el-button size="small" :type="layoutType === 'circular' ? 'primary' : 'default'" @click="switchLayout('circular')">Circular</el-button>
      <el-button size="small" @click="fit">Fit</el-button>
      <span class="ml-auto text-xs text-slate-500">{{ status }}</span>
      <span v-if="selected" class="font-mono text-xs text-blue-600">{{ selected }}</span>
    </div>
    <div ref="containerRef" class="min-h-0 flex-1" />
  </div>
</template>
