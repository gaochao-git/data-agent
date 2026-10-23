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
const nodeOffsets = ref<Record<string, { x: number; y: number }>>({})
let nodeStartX = 0
let nodeStartY = 0
let nodeStartOffsetX = 0
let nodeStartOffsetY = 0
let dragStartX = 0
let dragStartY = 0
let dragStartLeft = 0
let dragStartTop = 0

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
    const offsetX = nodeStartOffsetX + event.clientX - nodeStartX
    const offsetY = nodeStartOffsetY + event.clientY - nodeStartY
    nodeOffsets.value = {
      ...nodeOffsets.value,
      [draggingNodeKey.value]: { x: offsetX, y: offsetY }
    }
    return
  }
  if (!dragging.value || !graphScrollRef.value) return
  graphScrollRef.value.scrollLeft = dragStartLeft - (event.clientX - dragStartX)
  graphScrollRef.value.scrollTop = dragStartTop - (event.clientY - dragStartY)
}

const endDrag = () => {
  dragging.value = false
  draggingNodeKey.value = ''
}

const startNodeDrag = (event: MouseEvent, node: GraphNode) => {
  event.preventDefault()
  event.stopPropagation()
  const offset = nodeOffsets.value[node.key] || { x: 0, y: 0 }
  draggingNodeKey.value = node.key
  nodeStartX = event.clientX
  nodeStartY = event.clientY
  nodeStartOffsetX = offset.x
  nodeStartOffsetY = offset.y
}

const NODE_WIDTH = 170
const NODE_HEIGHT = 34
const COL_GAP = 320
const ROW_GAP = 150

const graph = computed(() => {
  const byOntology = new Map<string, OntologyObjectTypeItem[]>()
  for (const object of objects.value) {
    const key = String(object.ontology_id ?? 'ungrouped')
    if (!byOntology.has(key)) byOntology.set(key, [])
    byOntology.get(key)!.push(object)
  }
  const nodes: GraphNode[] = []
  const nodeByObject = new Map<string, GraphNode>()
  const columns = 4
  let rowIndex = 0
  for (const [, items] of byOntology) {
    items.forEach((object, index) => {
      const offset = nodeOffsets.value[object.object_type_name] || { x: 0, y: 0 }
      const node: GraphNode = {
        key: object.object_type_name,
        label: object.display_name || object.object_type_name,
        sublabel: object.object_type_name,
        x: 100 + (index % columns) * COL_GAP + offset.x,
        y: 90 + rowIndex * ROW_GAP + Math.floor(index / columns) * 80 + offset.y,
        ontologyLabel: ''
      }
      nodes.push(node)
      nodeByObject.set(object.object_type_name, node)
    })
    // 同一本体换行，避免所有对象类型排成一条长线。
    rowIndex += Math.max(1, Math.ceil(items.length / columns))
  }
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
    edges.push({
      key: relation.relation_type_name,
      x1: from.x + unitX * edgeDistance,
      y1: from.y + NODE_HEIGHT / 2 + unitY * edgeDistance,
      x2: to.x - unitX * edgeDistance,
      y2: to.y + NODE_HEIGHT / 2 - unitY * edgeDistance,
      // 两个方向标签分别放在线两侧；文字方向与连线平行（竖线竖排，横线横排）。
      fromLabelX: (from.x + to.x) / 2 - unitY * 18,
      fromLabelY: (from.y + to.y) / 2 + NODE_HEIGHT / 2 + unitX * 18,
      toLabelX: (from.x + to.x) / 2 + unitY * 18,
      toLabelY: (from.y + to.y) / 2 + NODE_HEIGHT / 2 - unitX * 18,
      fromLabel: relation.from_display_name || relation.from_verbalize || relation.relation_type_name,
      toLabel: relation.to_display_name || relation.to_verbalize || relation.relation_type_name,
      angle: (() => {
        let angle = (Math.atan2(dy, dx) * 180) / Math.PI
        if (angle > 90) angle -= 180
        if (angle < -90) angle += 180
        return angle
      })(),
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
      nodeOffsets.value = {}
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
        <div class="px-2 pt-1 text-[10px] text-slate-400">拖动画布查看 · 拖动节点调整位置</div>
        <svg
          :width="graph.width"
          :height="graph.height"
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
