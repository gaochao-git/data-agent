<script setup lang="ts">
/**
 * 本体对象类型关系图：节点=对象类型，边=关系类型。
 * 只读探索；点击对象类型节点回到对象列表详情。
 */
import { computed, ref, watch } from 'vue'
import { getAdminOntologyObjectType, getAdminOntologyObjectTypes, getAdminOntologyRelationTypes } from '../../api'
import type { OntologyObjectTypeItem, OntologyRelationTypeItem } from '../../types'

const props = defineProps({
  visible: { type: Boolean, default: false },
  ontologyId: { type: Number, default: undefined },
  ontologyLabel: { type: String, default: '' }
})

const emit = defineEmits<{
  'update:visible': [value: boolean]
  select: [objectTypeName: string]
}>()

interface GraphNode {
  key: string
  label: string
  sublabel: string
  x: number
  y: number
  ontologyLabel: string
}

interface GraphEdge {
  key: string
  x1: number
  y1: number
  x2: number
  y2: number
  fromLabelX: number
  fromLabelY: number
  toLabelX: number
  toLabelY: number
  fromLabel: string
  toLabel: string
  angle: number
  relation: OntologyRelationTypeItem
}

const loading = ref(false)
const error = ref('')
const objects = ref<OntologyObjectTypeItem[]>([])
const relations = ref<OntologyRelationTypeItem[]>([])
const selectedName = ref('')
const selectedDetail = ref<OntologyObjectTypeItem | null>(null)
const selectedLoading = ref(false)
const selectedError = ref('')
const panelCollapsed = ref(false)
const graphScrollRef = ref<HTMLElement | null>(null)
const dragging = ref(false)
const draggingNodeKey = ref('')
const nodePositions = ref<Record<string, { x: number; y: number }>>({})
const zoom = ref(1)
const zoomPercent = computed(() => Math.round(zoom.value * 100))
let nodeStartX = 0
let nodeStartY = 0
let nodeStartPositionX = 0
let nodeStartPositionY = 0
let dragStartX = 0
let dragStartY = 0
let dragStartLeft = 0
let dragStartTop = 0

interface Point {
  x: number
  y: number
}

const positionStorageKey = () => `chatdata.ontology.graph.positions.${props.ontologyId ?? 'all'}`

const readSavedPositions = (): Record<string, Point> => {
  try {
    const raw = localStorage.getItem(positionStorageKey())
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, Point>
    const positions: Record<string, Point> = {}
    for (const [key, value] of Object.entries(parsed || {})) {
      if (typeof value?.x === 'number' && typeof value?.y === 'number') positions[key] = { x: value.x, y: value.y }
    }
    return positions
  } catch {
    return {}
  }
}

const savePositions = () => {
  try {
    localStorage.setItem(positionStorageKey(), JSON.stringify(nodePositions.value))
  } catch {
    // localStorage 不可用时只影响位置持久化，不影响图谱操作。
  }
}

const startDrag = (event: MouseEvent) => {
  if (event.button !== 0 || !graphScrollRef.value) return
  dragging.value = true
  dragStartX = event.clientX
  dragStartY = event.clientY
  dragStartLeft = graphScrollRef.value.scrollLeft
  dragStartTop = graphScrollRef.value.scrollTop
}

const onDrag = (event: MouseEvent) => {
  if (draggingNodeKey.value) {
    nodePositions.value = {
      ...nodePositions.value,
      [draggingNodeKey.value]: {
        x: Math.max(100, nodeStartPositionX + event.clientX - nodeStartX),
        y: Math.max(60, nodeStartPositionY + event.clientY - nodeStartY)
      }
    }
    return
  }
  if (!dragging.value || !graphScrollRef.value) return
  graphScrollRef.value.scrollLeft = dragStartLeft - (event.clientX - dragStartX)
  graphScrollRef.value.scrollTop = dragStartTop - (event.clientY - dragStartY)
}

const endDrag = () => {
  if (draggingNodeKey.value) savePositions()
  dragging.value = false
  draggingNodeKey.value = ''
}

const startNodeDrag = (event: MouseEvent, node: GraphNode) => {
  event.preventDefault()
  event.stopPropagation()
  draggingNodeKey.value = node.key
  nodeStartX = event.clientX
  nodeStartY = event.clientY
  nodeStartPositionX = node.x
  nodeStartPositionY = node.y
}

const zoomIn = () => {
  zoom.value = Math.min(2, Math.round((zoom.value + 0.15) * 100) / 100)
}

const zoomOut = () => {
  zoom.value = Math.max(0.5, Math.round((zoom.value - 0.15) * 100) / 100)
}

const resetZoom = () => {
  zoom.value = 1
}

const NODE_WIDTH = 170
const NODE_HEIGHT = 34
const COL_GAP = 320
const ROW_GAP = 150

const autoLayout = () => {
  const names = objects.value.map(item => item.object_type_name)
  if (names.length === 0) {
    nodePositions.value = {}
    return
  }
  const count = names.length
  const width = Math.max(1100, Math.ceil(Math.sqrt(count)) * 360)
  const height = Math.max(620, Math.ceil(count / 4) * 220)
  const positions: Record<string, Point> = {}
  names.forEach((name, index) => {
    const angle = (index / count) * Math.PI * 2
    positions[name] = {
      x: width / 2 + Math.cos(angle) * Math.min(width * 0.28, 360),
      y: height / 2 + Math.sin(angle) * Math.min(height * 0.28, 240)
    }
  })
  const pairs: Array<[string, string]> = relations.value
    .map(relation => [relation.from_object_type_name || '', relation.to_object_type_name || ''] as [string, string])
    .filter(([from, to]) => Boolean(from && to && positions[from] && positions[to]))
  const area = width * height
  const k = Math.sqrt(area / Math.max(count, 1)) * 0.72
  const iterations = 160
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const displacement: Record<string, Point> = {}
    names.forEach(name => {
      displacement[name] = { x: 0, y: 0 }
    })
    for (let i = 0; i < names.length; i += 1) {
      for (let j = i + 1; j < names.length; j += 1) {
        const first = positions[names[i]!]!
        const second = positions[names[j]!]!
        let dx = first.x - second.x
        let dy = first.y - second.y
        let distance = Math.hypot(dx, dy)
        if (distance < 1) {
          dx = (i - j) * 0.01 + 1
          dy = 1
          distance = Math.hypot(dx, dy)
        }
        const force = (k * k) / distance
        const unitX = dx / distance
        const unitY = dy / distance
        displacement[names[i]!]!.x += unitX * force
        displacement[names[i]!]!.y += unitY * force
        displacement[names[j]!]!.x -= unitX * force
        displacement[names[j]!]!.y -= unitY * force
      }
    }
    for (const [from, to] of pairs) {
      let dx = positions[from]!.x - positions[to]!.x
      let dy = positions[from]!.y - positions[to]!.y
      let distance = Math.hypot(dx, dy)
      if (distance < 1) {
        dx = 1
        dy = 1
        distance = Math.hypot(dx, dy)
      }
      const force = (distance * distance) / k
      const unitX = dx / distance
      const unitY = dy / distance
      displacement[from]!.x -= unitX * force
      displacement[from]!.y -= unitY * force
      displacement[to]!.x += unitX * force
      displacement[to]!.y += unitY * force
    }
    const temperature = Math.max(0.8, 24 * (1 - iteration / iterations))
    names.forEach(name => {
      const point = positions[name]!
      const move = displacement[name]!
      point.x += (move.x / Math.max(1, Math.hypot(move.x, move.y))) * temperature
      point.y += (move.y / Math.max(1, Math.hypot(move.x, move.y))) * temperature
      point.x += (width / 2 - point.x) * 0.008
      point.y += (height / 2 - point.y) * 0.008
      point.x = Math.max(100, Math.min(width - 100, point.x))
      point.y = Math.max(70, Math.min(height - 70, point.y))
    })
  }
  nodePositions.value = positions
  savePositions()
}

const applyNewLayout = () => {
  autoLayout()
}

const labelWidth = (text: string) => Math.max(40, Math.min(text.length, 12) * 11 + 10)

interface Box {
  x: number
  y: number
  width: number
  height: number
}

const overlaps = (box: Box, others: Box[]) =>
  others.some(other =>
    box.x < other.x + other.width &&
    box.x + box.width > other.x &&
    box.y < other.y + other.height &&
    box.y + box.height > other.y
  )

const pickLabelPosition = (
  text: string,
  from: GraphNode,
  dx: number,
  dy: number,
  unitX: number,
  unitY: number,
  side: 1 | -1,
  nodeBoxes: Box[],
  placed: Box[]
) => {
  const width = labelWidth(text)
  const height = 18
  const fractions = [0.5, 0.42, 0.58, 0.34, 0.66]
  const offsets = side > 0 ? [18, 34, 10, 50] : [-18, -34, -10, -50]
  for (const fraction of fractions) {
    for (const offset of offsets) {
      const x = from.x + dx * fraction - unitY * offset
      const y = from.y + NODE_HEIGHT / 2 + dy * fraction + unitX * offset
      const box: Box = { x: x - width / 2, y: y - height / 2, width, height }
      if (!overlaps(box, nodeBoxes) && !overlaps(box, placed)) {
        placed.push(box)
        return { x, y }
      }
    }
  }
  return {
    x: from.x + dx * 0.5 - unitY * side * 18,
    y: from.y + NODE_HEIGHT / 2 + dy * 0.5 + unitX * side * 18
  }
}

const graph = computed(() => {
  const columns = 4
  const nodes: GraphNode[] = objects.value.map((object, index) => {
    const point = nodePositions.value[object.object_type_name]
    return {
      key: object.object_type_name,
      label: object.display_name || object.object_type_name,
      sublabel: object.object_type_name,
      x: point?.x ?? 120 + (index % columns) * COL_GAP,
      y: point?.y ?? 90 + Math.floor(index / columns) * ROW_GAP,
      ontologyLabel: ''
    }
  })
  const nodeByObject = new Map<string, GraphNode>(nodes.map(node => [node.key, node]))
  const nodeBoxes: Box[] = nodes.map(node => ({
    x: node.x - NODE_WIDTH / 2,
    y: node.y,
    width: NODE_WIDTH,
    height: NODE_HEIGHT
  }))
  const labelBoxes: Box[] = []
  const edges: GraphEdge[] = []
  for (const relation of relations.value) {
    const from = nodeByObject.get(relation.from_object_type_name || '')
    const to = nodeByObject.get(relation.to_object_type_name || '')
    if (!from || !to) continue
    const dx = to.x - from.x
    const dy = to.y - from.y
    const distance = Math.hypot(dx, dy) || 1
    const unitX = dx / distance
    const unitY = dy / distance
    const edgeDistance = Math.min(
      NODE_WIDTH / 2 / Math.max(Math.abs(unitX), 1e-6),
      NODE_HEIGHT / 2 / Math.max(Math.abs(unitY), 1e-6)
    )
    const fromLabel = relation.from_display_name || relation.from_verbalize || relation.relation_type_name
    const toLabel = relation.to_display_name || relation.to_verbalize || relation.relation_type_name
    const fromPosition = pickLabelPosition(fromLabel, from, dx, dy, unitX, unitY, 1, nodeBoxes, labelBoxes)
    const toPosition = pickLabelPosition(toLabel, from, dx, dy, unitX, unitY, -1, nodeBoxes, labelBoxes)
    let angle = (Math.atan2(dy, dx) * 180) / Math.PI
    if (angle > 90) angle -= 180
    if (angle < -90) angle += 180
    edges.push({
      key: relation.relation_type_name,
      x1: from.x + unitX * edgeDistance,
      y1: from.y + NODE_HEIGHT / 2 + unitY * edgeDistance,
      x2: to.x - unitX * edgeDistance,
      y2: to.y + NODE_HEIGHT / 2 - unitY * edgeDistance,
      fromLabelX: fromPosition.x,
      fromLabelY: fromPosition.y,
      toLabelX: toPosition.x,
      toLabelY: toPosition.y,
      fromLabel,
      toLabel,
      angle,
      relation
    })
  }
  const width = Math.max(900, 220 + Math.max(0, ...nodes.map(node => node.x)))
  const height = Math.max(500, 160 + Math.max(0, ...nodes.map(node => node.y)))
  return { nodes, edges, width, height }
})

const selectedRelations = computed(() => {
  if (!selectedName.value) return []
  return relations.value
    .filter(relation => relation.from_object_type_name === selectedName.value || relation.to_object_type_name === selectedName.value)
    .map(relation => {
      const outgoing = relation.from_object_type_name === selectedName.value
      return {
        name: relation.relation_type_name,
        label: outgoing ? relation.from_display_name : relation.to_display_name,
        target: outgoing ? relation.to_object_type_name : relation.from_object_type_name,
        direction: outgoing ? '正向' : '反向'
      }
    })
})

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const [objectRes, relationRes] = await Promise.all([
      getAdminOntologyObjectTypes(props.ontologyId != null ? { ontology_id: props.ontologyId } : {}),
      getAdminOntologyRelationTypes({})
    ])
    if (objectRes.status === 'success' && relationRes.status === 'success') {
      objects.value = objectRes.data.object_types || []
      const names = new Set(objects.value.map(item => item.object_type_name))
      relations.value = (relationRes.data.relation_types || []).filter(
        relation => names.has(relation.from_object_type_name || '') && names.has(relation.to_object_type_name || '')
      )
      const objectNames = objects.value.map(item => item.object_type_name)
      const saved = readSavedPositions()
      if (objectNames.length > 0 && objectNames.every(name => saved[name])) {
        nodePositions.value = saved
      } else {
        autoLayout()
      }
      selectedName.value = ''
      selectedDetail.value = null
      panelCollapsed.value = true
    } else {
      error.value = objectRes.msg || relationRes.msg || '加载本体图谱失败'
    }
  } catch (err) {
    console.error('加载本体图谱失败:', err)
    error.value = '加载本体图谱失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.visible, props.ontologyId],
  ([visible]) => {
    if (visible) void load()
  }
)

const selectNode = async (node: GraphNode) => {
  panelCollapsed.value = false
  selectedName.value = node.key
  selectedDetail.value = null
  selectedError.value = ''
  selectedLoading.value = true
  try {
    const res = await getAdminOntologyObjectType(node.key)
    if (res.status === 'success') selectedDetail.value = res.data
    else selectedError.value = res.msg || '加载对象类型属性失败'
  } catch (err) {
    console.error('加载对象类型属性失败:', err)
    selectedError.value = '加载对象类型属性失败'
  } finally {
    selectedLoading.value = false
  }
}

const selectByKey = (name?: string) => {
  if (!name) return
  const node = graph.value.nodes.find(item => item.key === name)
  if (node) void selectNode(node)
}

const openSelected = () => {
  if (!selectedName.value) return
  emit('select', selectedName.value)
  emit('update:visible', false)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    width="min(1240px, 96vw)"
    destroy-on-close
    append-to-body
    @update:model-value="(value: boolean) => emit('update:visible', value)"
  >
    <template #header>
      <div class="flex items-center gap-2">
        <i class="fa-solid fa-diagram-project text-[11px] text-violet-500"></i>
        <span class="text-base font-medium text-slate-800">本体对象类型关系图</span>
        <span v-if="ontologyLabel" class="text-xs text-slate-400">{{ ontologyLabel }}</span>
        <span class="text-xs text-slate-400">节点：对象类型 · 边：关系类型</span>
      </div>
    </template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="flex min-h-[520px] gap-3">
      <div
        ref="graphScrollRef"
        v-loading="loading"
        class="min-w-0 flex-1 cursor-grab overflow-auto rounded border border-slate-100 bg-slate-50/60 active:cursor-grabbing"
        @mousedown="startDrag"
        @mousemove="onDrag"
        @mouseup="endDrag"
        @mouseleave="endDrag"
      >
        <div class="flex items-center justify-between px-2 pt-1" @mousedown.stop>
          <span class="text-[10px] text-slate-400">拖动画布查看 · 拖动节点调整位置</span>
          <div class="flex items-center gap-1">
            <el-button size="small" @click="applyNewLayout">
              <i class="fa-solid fa-wand-magic-sparkles mr-1 text-[10px]"></i>自动布局
            </el-button>
            <el-button size="small" :disabled="zoom <= 0.5" @click="zoomOut">−</el-button>
            <span class="w-10 text-center text-[10px] text-slate-500">{{ zoomPercent }}%</span>
            <el-button size="small" :disabled="zoom >= 2" @click="zoomIn">＋</el-button>
            <el-button size="small" @click="resetZoom">100%</el-button>
          </div>
        </div>
        <svg
          :width="graph.width * zoom"
          :height="graph.height * zoom"
          :viewBox="`0 0 ${graph.width} ${graph.height}`"
          class="block max-w-none"
          role="img"
          aria-label="本体对象类型关系图"
        >
          <g v-for="edge in graph.edges" :key="edge.key" class="cursor-help">
            <title>{{ edge.relation.description || edge.fromLabel }}（{{ edge.relation.relation_type_name }}） · 正向 {{ edge.fromLabel }} / 反向 {{ edge.toLabel }}</title>
            <line
              :x1="edge.x1"
              :y1="edge.y1"
              :x2="edge.x2"
              :y2="edge.y2"
              stroke="#94a3b8"
              stroke-width="1.4"
            />
            <g :transform="`rotate(${edge.angle} ${edge.fromLabelX} ${edge.fromLabelY})`">
              <rect
                :x="edge.fromLabelX - Math.max(40, Math.min(edge.fromLabel.length, 12) * 11 + 10) / 2"
                :y="edge.fromLabelY - 10"
                :width="Math.max(40, Math.min(edge.fromLabel.length, 12) * 11 + 10)"
                height="18"
                rx="3"
                fill="#f8fafc"
                fill-opacity="0.95"
              />
              <text
                :x="edge.fromLabelX"
                :y="edge.fromLabelY + 2"
                text-anchor="middle"
                class="fill-slate-500"
                style="font-size: 10px"
              >
                {{ edge.fromLabel.length > 12 ? `${edge.fromLabel.slice(0, 11)}…` : edge.fromLabel }}
              </text>
            </g>
            <g :transform="`rotate(${edge.angle} ${edge.toLabelX} ${edge.toLabelY})`">
              <rect
                :x="edge.toLabelX - Math.max(40, Math.min(edge.toLabel.length, 12) * 11 + 10) / 2"
                :y="edge.toLabelY - 10"
                :width="Math.max(40, Math.min(edge.toLabel.length, 12) * 11 + 10)"
                height="18"
                rx="3"
                fill="#f8fafc"
                fill-opacity="0.95"
              />
              <text
                :x="edge.toLabelX"
                :y="edge.toLabelY + 2"
                text-anchor="middle"
                class="fill-slate-500"
                style="font-size: 10px"
              >
                {{ edge.toLabel.length > 12 ? `${edge.toLabel.slice(0, 11)}…` : edge.toLabel }}
              </text>
            </g>
          </g>
          <g
            v-for="node in graph.nodes"
            :key="node.key"
            class="cursor-move"
            @mousedown="startNodeDrag($event, node)"
            @click="selectNode(node)"
          >
            <title>{{ node.sublabel }}</title>
            <rect
              :x="node.x - NODE_WIDTH / 2"
              :y="node.y"
              :width="NODE_WIDTH"
              :height="NODE_HEIGHT"
              rx="5"
              :fill="selectedName === node.key ? '#eff6ff' : '#ffffff'"
              :stroke="selectedName === node.key ? '#3b82f6' : '#dbeafe'"
              :stroke-width="selectedName === node.key ? 2 : 1.2"
            />
            <text
              :x="node.x"
              :y="node.y + 21"
              text-anchor="middle"
              :class="selectedName === node.key ? 'fill-blue-700' : 'fill-slate-700'"
              style="font-size: 11px; font-weight: 500"
            >
              {{ node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label }}
            </text>
          </g>
        </svg>
      </div>

      <aside v-if="selectedName && !panelCollapsed" class="flex w-[320px] shrink-0 flex-col rounded border border-slate-200 bg-white">
        <div class="border-b border-slate-100 px-3 py-2">
          <div class="flex items-center gap-2">
            <span class="font-mono text-xs font-medium text-slate-800">{{ selectedName }}</span>
            <el-button link size="small" class="ml-auto !p-0 text-slate-400" @click="panelCollapsed = true">
              <i class="fa-solid fa-angles-right mr-0.5 text-[9px]"></i>收起
            </el-button>
            <el-button link size="small" type="primary" class="!p-0" @click="openSelected">打开对象类型</el-button>
          </div>
          <div v-if="selectedDetail?.display_name" class="mt-0.5 text-xs text-slate-500">{{ selectedDetail.display_name }}</div>
        </div>
        <div v-if="selectedLoading" class="px-3 py-4 text-xs text-slate-400">加载属性中…</div>
        <el-alert v-else-if="selectedError" :title="selectedError" type="error" :closable="false" show-icon class="m-2" />
        <div v-else class="min-h-0 flex-1 overflow-y-auto px-3 py-2">
          <div class="mb-1 flex items-center gap-2">
            <span class="text-xs font-medium text-slate-600">属性</span>
            <span class="text-[11px] text-slate-400">{{ selectedDetail?.properties?.length || 0 }} 个</span>
          </div>
          <div v-if="(selectedDetail?.properties || []).length === 0" class="text-xs text-slate-400">暂无属性</div>
          <div v-for="prop in selectedDetail?.properties || []" :key="prop.property_name" class="border-b border-slate-50 py-1">
            <div class="flex items-center gap-1.5">
              <span class="font-mono text-[11px] text-slate-700">{{ prop.property_name }}</span>
              <span v-if="prop.display_name" class="text-[11px] text-slate-400">{{ prop.display_name }}</span>
              <el-tag v-if="prop.is_identifier" size="small" type="warning" effect="plain">主键</el-tag>
              <el-tag v-if="prop.is_title" size="small" type="success" effect="plain">标题</el-tag>
            </div>
            <div class="text-[10px] text-slate-400">{{ prop.value_type }}{{ prop.description ? ` · ${prop.description}` : '' }}</div>
          </div>

          <div class="mb-1 mt-3 flex items-center gap-2">
            <span class="text-xs font-medium text-slate-600">关系</span>
            <span class="text-[11px] text-slate-400">{{ selectedRelations.length }} 条</span>
          </div>
          <div v-if="selectedRelations.length === 0" class="text-xs text-slate-400">暂无关系</div>
          <button
            v-for="relation in selectedRelations"
            :key="relation.name"
            type="button"
            class="mb-1 block w-full border-0 bg-slate-50 px-2 py-1 text-left text-[11px] text-slate-600 hover:bg-blue-50 hover:text-blue-700"
            @click="selectByKey(relation.target)"
          >
            <span class="text-slate-400">{{ relation.direction }} · </span>{{ relation.label || relation.name }}
            <span class="text-slate-400"> → {{ relation.target }}</span>
          </button>
        </div>
      </aside>
    </div>
    <template #footer>
      <el-button @click="emit('update:visible', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>
