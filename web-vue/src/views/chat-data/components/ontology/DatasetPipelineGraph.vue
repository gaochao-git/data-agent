<script setup lang="ts">
/**
 * Dataset Pipeline DAG 画布（可选中编辑的只读拓扑）。
 * 节点按拓扑层级从左到右排布，边用 SVG 连线；点击节点回传 node_id 给右侧配置面板。
 * 写入操作（增删节点/改配置）由父组件负责，画布只做展示与选中，保证单一数据源。
 */
import { computed } from 'vue'
import type { PropType } from 'vue'
import type { DatasetPipelineNode, DatasetPipelineEdge } from '../../types'

const props = defineProps({
  nodes: { type: Array as PropType<DatasetPipelineNode[]>, default: () => [] },
  edges: { type: Array as PropType<DatasetPipelineEdge[]>, default: () => [] },
  selectedNodeId: { type: String, default: '' },
  dirtyNodeIds: { type: Array as PropType<string[]>, default: () => [] }
})

const emit = defineEmits<{ select: [nodeId: string] }>()

const NODE_WIDTH = 176
const NODE_HEIGHT = 68
const X_GAP = 72
const Y_GAP = 26
const PADDING = 24

interface LayoutNode {
  node: DatasetPipelineNode
  x: number
  y: number
}

interface LayoutEdge {
  key: string
  x1: number
  y1: number
  x2: number
  y2: number
}

interface LayoutResult {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  width: number
  height: number
}

const EMPTY_LAYOUT: LayoutResult = { nodes: [], edges: [], width: 0, height: 0 }

/** Kahn 拓扑分层：链式定义下等价于从左到右的链路顺序；有环/游离节点兜底放第 0 层。 */
const computeLevels = (nodes: DatasetPipelineNode[], edges: DatasetPipelineEdge[]): Map<string, number> => {
  const level = new Map<string, number>()
  const nodeIds = new Set(nodes.map((node) => node.node_id))
  const indegree = new Map<string, number>()
  const outgoing = new Map<string, string[]>()
  for (const node of nodes) indegree.set(node.node_id, 0)
  for (const edge of edges) {
    if (!nodeIds.has(edge.from_node_id) || !nodeIds.has(edge.to_node_id)) continue
    indegree.set(edge.to_node_id, (indegree.get(edge.to_node_id) || 0) + 1)
    const targets = outgoing.get(edge.from_node_id) || []
    targets.push(edge.to_node_id)
    outgoing.set(edge.from_node_id, targets)
  }
  const queue = nodes.filter((node) => (indegree.get(node.node_id) || 0) === 0).map((node) => node.node_id)
  for (const id of queue) level.set(id, 0)
  let cursor = 0
  while (cursor < queue.length) {
    const id = queue[cursor] as string
    cursor += 1
    for (const target of outgoing.get(id) || []) {
      level.set(target, Math.max(level.get(target) || 0, (level.get(id) || 0) + 1))
      indegree.set(target, (indegree.get(target) || 1) - 1)
      if ((indegree.get(target) || 0) === 0) queue.push(target)
    }
  }
  for (const node of nodes) if (!level.has(node.node_id)) level.set(node.node_id, 0)
  return level
}

const layout = computed<LayoutResult>(() => {
  const nodes = props.nodes || []
  const edges = props.edges || []
  if (nodes.length === 0) return EMPTY_LAYOUT
  const level = computeLevels(nodes, edges)
  const buckets = new Map<number, DatasetPipelineNode[]>()
  for (const node of nodes) {
    const column = level.get(node.node_id) || 0
    const bucket = buckets.get(column) || []
    bucket.push(node)
    buckets.set(column, bucket)
  }
  const bucketList = [...buckets.values()]
  const maxRows = Math.max(...bucketList.map((bucket) => bucket.length))
  const maxColumn = Math.max(...buckets.keys())
  const layoutNodes: LayoutNode[] = []
  const positions = new Map<string, { x: number; y: number }>()
  for (const [column, bucket] of buckets) {
    const offset = (maxRows - bucket.length) / 2
    bucket.forEach((node, index) => {
      const x = PADDING + column * (NODE_WIDTH + X_GAP)
      const y = PADDING + (offset + index) * (NODE_HEIGHT + Y_GAP)
      positions.set(node.node_id, { x, y })
      layoutNodes.push({ node, x, y })
    })
  }
  const layoutEdges: LayoutEdge[] = edges
    .filter((edge) => positions.has(edge.from_node_id) && positions.has(edge.to_node_id))
    .map((edge) => {
      const from = positions.get(edge.from_node_id) as { x: number; y: number }
      const to = positions.get(edge.to_node_id) as { x: number; y: number }
      return {
        key: edge.edge_id || `${edge.from_node_id}-${edge.to_node_id}`,
        x1: from.x + NODE_WIDTH,
        y1: from.y + NODE_HEIGHT / 2,
        x2: to.x,
        y2: to.y + NODE_HEIGHT / 2
      }
    })
  return {
    nodes: layoutNodes,
    edges: layoutEdges,
    width: PADDING * 2 + (maxColumn + 1) * NODE_WIDTH + maxColumn * X_GAP,
    height: PADDING * 2 + Math.max(1, maxRows) * NODE_HEIGHT + Math.max(0, maxRows - 1) * Y_GAP
  }
})

const nodeTypeLabel = (node: DatasetPipelineNode) => {
  if (node.node_type === 'source') return '输入 Dataset'
  if (node.node_type === 'output') return '输出 Dataset'
  return 'Transform'
}

const nodeIcon = (node: DatasetPipelineNode) => {
  if (node.node_type === 'source') return 'fa-database'
  if (node.node_type === 'output') return 'fa-table'
  return 'fa-wand-magic-sparkles'
}

const transformSummary = (config: Record<string, unknown>) => {
  if (String(config.transform_type || '') === 'aggregate') {
    const groupBy = Array.isArray(config.group_by) ? config.group_by.map(String).filter(Boolean) : []
    const metrics = Array.isArray(config.metrics) ? config.metrics : []
    const metric = metrics[0] && typeof metrics[0] === 'object' ? (metrics[0] as Record<string, unknown>) : {}
    const metricText = `${String(metric.function || 'count')}(${String(metric.column || '*')})`
    return `aggregate · ${groupBy.length ? groupBy.join(',') : '全部'} → ${metricText}`
  }
  const conditions = Array.isArray(config.conditions) ? config.conditions : []
  const condition = conditions[0] && typeof conditions[0] === 'object' ? (conditions[0] as Record<string, unknown>) : {}
  if (!condition.column) return 'filter · 待配置'
  return `filter · ${String(condition.column)} ${String(condition.operator || 'eq')} ${String(condition.value ?? '')}`
}

const nodeTitle = (node: DatasetPipelineNode) => node.node_name || node.node_id
const nodeSubtitle = (node: DatasetPipelineNode) => {
  const config = node.config || {}
  if (node.node_type === 'source') {
    const members = Array.isArray(config.member_datasource_ids) ? config.member_datasource_ids.length : 0
    const limit = config.limit ? `limit ${String(config.limit)}` : '默认 limit'
    return [String(config.dataset_name || '-'), members ? `${members} 个成员` : '全部成员', limit].join(' · ')
  }
  if (node.node_type === 'output') {
    return `${String(config.dataset_name || '-')} · ${String(config.layer || '-')}`
  }
  return transformSummary(config)
}

const isDirty = (node: DatasetPipelineNode) => (props.dirtyNodeIds || []).includes(node.node_id)
</script>

<template>
  <div v-if="layout.nodes.length > 0" class="overflow-auto rounded-lg border border-slate-200 bg-slate-50/70 p-3">
    <div class="relative" :style="{ width: `${layout.width}px`, height: `${layout.height}px` }">
      <svg class="pointer-events-none absolute inset-0" :width="layout.width" :height="layout.height" aria-hidden="true">
        <defs>
          <marker id="pipeline-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="#94a3b8" />
          </marker>
        </defs>
        <path
          v-for="edge in layout.edges"
          :key="edge.key"
          :d="`M ${edge.x1} ${edge.y1} C ${edge.x1 + 28} ${edge.y1}, ${edge.x2 - 28} ${edge.y2}, ${edge.x2} ${edge.y2}`"
          fill="none"
          stroke="#94a3b8"
          stroke-width="1.5"
          marker-end="url(#pipeline-arrow)"
        />
      </svg>
      <button
        v-for="item in layout.nodes"
        :key="item.node.node_id"
        type="button"
        :data-node-id="item.node.node_id"
        :data-node-type="item.node.node_type"
        :aria-pressed="selectedNodeId === item.node.node_id"
        class="absolute flex w-[176px] flex-col rounded-lg border bg-white px-3 py-2 text-left shadow-sm transition-all hover:shadow-md"
        :class="selectedNodeId === item.node.node_id ? 'border-blue-500 ring-2 ring-blue-100' : 'border-slate-200'"
        :style="{ left: `${item.x}px`, top: `${item.y}px`, height: '68px' }"
        @click="emit('select', item.node.node_id)"
      >
        <div class="flex items-center gap-1.5">
          <i :class="['fa-solid text-[10px]', item.node.node_type === 'output' ? 'text-emerald-500' : item.node.node_type === 'source' ? 'text-blue-500' : 'text-amber-500', nodeIcon(item.node)]"></i>
          <span class="truncate text-[11px] font-medium text-slate-700">{{ nodeTitle(item.node) }}</span>
          <span
            v-if="isDirty(item.node)"
            data-testid="pipeline-node-dirty"
            class="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500"
            title="有未保存修改"
          ></span>
          <span class="ml-auto shrink-0 rounded bg-slate-100 px-1 text-[9px] text-slate-500">{{ nodeTypeLabel(item.node) }}</span>
        </div>
        <div class="mt-1 truncate font-mono text-[10px] text-slate-500">{{ nodeSubtitle(item.node) }}</div>
      </button>
    </div>
  </div>
  <div v-else class="rounded-lg border border-dashed border-slate-200 px-4 py-8 text-center text-xs text-slate-400">
    暂无 Pipeline 节点
  </div>
</template>
