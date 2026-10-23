<script setup lang="ts">
/**
 * 对象类型 · 对象实例工作台。
 * 主从布局：左侧分页列表/搜索，右侧对象详情；关联对象在当前工作台下钻，不再弹窗跳转。
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  getAdminOntologyObjectType,
  getAdminOntologyRelationTypes,
  readAdminOntologyObject,
  searchAdminOntologyObjects
} from '../../api'
import type {
  OntologyObjectFilter,
  OntologyObjectInstance,
  OntologyObjectListItem,
  OntologyObjectSort,
  OntologyObjectTypeItem
} from '../../types'

const props = defineProps({
  objectType: { type: String, default: '' },
  objectLabel: { type: String, default: '' }
})

const loading = ref(false)
const error = ref('')
const keyword = ref('')
const page = ref(1)
const pageSize = 20
const objects = ref<OntologyObjectListItem[]>([])
const hasMore = ref(false)
const titleProperty = ref('')
const totalCount = ref(0)
const filters = ref<OntologyObjectFilter[]>([])
const sortProperty = ref('')
const sortDirection = ref<'asc' | 'desc'>('asc')

const meta = ref<OntologyObjectTypeItem | null>(null)
const relationLabels = ref<Record<string, string>>({})
const selectedKey = ref('')
const listSelectedKey = ref('')
const detail = ref<OntologyObjectInstance | null>(null)
const detailLoading = ref(false)
const linksLoading = ref(false)
const detailError = ref('')
const detailMode = ref<'table' | 'search' | 'graph'>('table')
const searchKeyword = ref('')
const detailCache = new Map<string, OntologyObjectInstance>()
const history = ref<Array<{ objectType: string; objectKey: string; label: string }>>([])

const filterProperties = computed(() =>
  (meta.value?.properties || []).map(prop => ({
    name: prop.property_name,
    label: prop.display_name || prop.property_name
  }))
)

const OPERATOR_OPTIONS = [
  { value: 'eq', label: '等于' },
  { value: 'ne', label: '不等于' },
  { value: 'contains', label: '包含' },
  { value: 'gt', label: '大于' },
  { value: 'gte', label: '大于等于' },
  { value: 'lt', label: '小于' },
  { value: 'lte', label: '小于等于' },
  { value: 'in', label: '属于' }
] as const

const buildSearchPayload = () => ({
  keyword: keyword.value.trim() || undefined,
  filters: filters.value
    .filter(item => item.property)
    .map(item => {
      if (item.operator === 'in') {
        return {
          property: item.property,
          operator: item.operator,
          values: String(item.value || '')
            .split(',')
            .map(value => value.trim())
            .filter(Boolean)
        }
      }
      return { property: item.property, operator: item.operator, value: item.value || '' }
    }),
  sort: sortProperty.value
    ? ([{ property: sortProperty.value, direction: sortDirection.value }] as OntologyObjectSort[])
    : [],
  limit: pageSize,
  offset: (page.value - 1) * pageSize
})

const addFilter = () => {
  filters.value.push({
    property: filterProperties.value[0]?.name || '',
    operator: 'eq',
    value: ''
  })
}

const removeFilter = (index: number) => {
  filters.value.splice(index, 1)
}

const applyFilters = () => {
  void loadList(true)
}

const clearFilters = () => {
  filters.value = []
  sortProperty.value = ''
  void loadList(true)
}

const propertyLabels = computed<Record<string, string>>(() => {
  const labels: Record<string, string> = {}
  for (const prop of meta.value?.properties || []) labels[prop.property_name] = prop.display_name || prop.property_name
  return labels
})

const detailPropertyRows = computed(() => {
  const data = detail.value
  if (!data) return []
  return Object.entries(data.properties || {}).map(([name, value]) => ({
    name,
    label: propertyLabels.value[name] || '',
    value
  }))
})

const detailTitle = computed(() => {
  const data = detail.value
  if (!data) return ''
  const titleKey = meta.value?.properties?.find(item => item.is_title)?.property_name
  if (!titleKey) return ''
  const value = data.properties?.[titleKey]
  return value == null ? '' : String(value)
})

const linkedGroups = computed(() => {
  const data = detail.value
  if (!data) return []
  return Object.entries(data.links || {}).map(([relationName, rawEntries]) => {
    const entries = Array.isArray(rawEntries)
      ? rawEntries.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      : []
    return {
      relationName,
      label: relationLabels.value[relationName] || relationName,
      entries
    }
  })
})

const searchAroundGroups = computed(() => {
  const needle = searchKeyword.value.trim().toLowerCase()
  return linkedGroups.value
    .map(group => ({
      ...group,
      entries: group.entries.filter(entry => {
        if (!needle) return true
        return String(entry.related_object_key || '').toLowerCase().includes(needle)
      })
    }))
    .filter(group => group.entries.length > 0)
})

const searchAroundCount = computed(() => searchAroundGroups.value.reduce((total, group) => total + group.entries.length, 0))

interface ExploreNode {
  key: string
  objectType: string
  label: string
  properties: Record<string, unknown>
}

interface ExploreEdge {
  key: string
  relationName: string
  fromKey: string
  toKey: string
}

const exploreNodes = ref<Record<string, ExploreNode>>({})
const exploreEdges = ref<Record<string, ExploreEdge>>({})
const exploreSelectedKey = ref('')
const seedAfterLoad = ref(false)
const graphFullscreen = ref(false)
const showGraphProperties = ref(false)
const exploreNodeOffsets = ref<Record<string, { x: number; y: number }>>({})
const draggingExploreKey = ref('')
const exploreLoadingKey = ref('')
let exploreRequestId = 0
let exploreStartX = 0
let exploreStartY = 0
let exploreStartOffsetX = 0
let exploreStartOffsetY = 0

const startExploreNodeDrag = (event: MouseEvent, node: { key: string; x: number; y: number }) => {
  event.preventDefault()
  event.stopPropagation()
  const offset = exploreNodeOffsets.value[node.key] || { x: 0, y: 0 }
  draggingExploreKey.value = node.key
  exploreStartX = event.clientX
  exploreStartY = event.clientY
  exploreStartOffsetX = offset.x
  exploreStartOffsetY = offset.y
}

const onExploreNodeDrag = (event: MouseEvent) => {
  if (!draggingExploreKey.value) return
  exploreNodeOffsets.value = {
    ...exploreNodeOffsets.value,
    [draggingExploreKey.value]: {
      x: exploreStartOffsetX + event.clientX - exploreStartX,
      y: exploreStartOffsetY + event.clientY - exploreStartY
    }
  }
}

const endExploreNodeDrag = () => {
  draggingExploreKey.value = ''
}

const addExploreNode = (rawKey: string, objectType: string, label: string, properties: Record<string, unknown>) => {
  if (!rawKey) return
  exploreNodes.value = {
    ...exploreNodes.value,
    [rawKey]: { key: rawKey, objectType, label: label || rawKey, properties }
  }
}

const addExploreEdge = (relationName: string, fromKey: string, toKey: string) => {
  if (!fromKey || !toKey) return
  const key = `${relationName}:${fromKey}:${toKey}`
  exploreEdges.value = {
    ...exploreEdges.value,
    [key]: { key, relationName, fromKey, toKey }
  }
}

const mergeCurrentLinksIntoExplore = () => {
  const data = detail.value
  if (!data) return
  addExploreNode(data.object_key, data.object_type || props.objectType, detailTitle.value || data.object_key, data.properties || {})
  for (const group of linkedGroups.value) {
    for (const entry of group.entries) {
      const relatedKey = String(entry.related_object_key || '')
      if (!relatedKey) continue
      const separator = relatedKey.indexOf(':')
      const relatedType = separator > 0 ? relatedKey.slice(0, separator) : props.objectType
      const relatedTitle = String(entry.related_title || '').trim()
      addExploreNode(relatedKey, relatedType, relatedTitle || relatedKey.split(':').slice(1).join(':') || relatedKey, {})
      addExploreEdge(group.relationName, data.object_key, relatedKey)
    }
  }
}

const seedExploreGraph = () => {
  exploreNodes.value = {}
  exploreEdges.value = {}
  exploreNodeOffsets.value = {}
  exploreSelectedKey.value = detail.value?.object_key || ''
  mergeCurrentLinksIntoExplore()
}

const clearExploreGraph = () => {
  exploreNodes.value = {}
  exploreEdges.value = {}
  exploreNodeOffsets.value = {}
  exploreSelectedKey.value = ''
}

const afterDetailLoaded = () => {
  if (detailMode.value !== 'graph' || !detail.value) return
  addExploreNode(
    detail.value.object_key,
    detail.value.object_type || props.objectType,
    detailTitle.value || detail.value.object_key,
    detail.value.properties || {}
  )
  exploreSelectedKey.value = detail.value.object_key
  if (seedAfterLoad.value) {
    seedExploreGraph()
    seedAfterLoad.value = false
  }
}

const exploreGraph = computed(() => {
  const nodes = Object.values(exploreNodes.value)
  if (nodes.length === 0) return { nodes: [], edges: [], width: 760, height: 460 }
  const width = Math.max(760, Math.min(1300, 520 + nodes.length * 58))
  const height = Math.max(460, Math.min(820, 300 + nodes.length * 34))
  const centerX = width / 2
  const centerY = height / 2
  const positions = new Map<string, { x: number; y: number }>()
  nodes.forEach((node, index) => {
    const angle = (index / Math.max(nodes.length, 1)) * Math.PI * 2
    positions.set(node.key, {
      x: centerX + Math.cos(angle) * Math.min(230, nodes.length * 22),
      y: centerY + Math.sin(angle) * Math.min(150, nodes.length * 16)
    })
  })
  const keys = nodes.map(node => node.key)
  const pairs = Object.values(exploreEdges.value)
    .map(edge => [edge.fromKey, edge.toKey] as [string, string])
    .filter(([from, to]) => positions.has(from) && positions.has(to))
  const k = Math.sqrt((width * height) / Math.max(nodes.length, 1)) * 0.72
  const iterations = 120
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const displacement: Record<string, { x: number; y: number }> = {}
    keys.forEach(key => {
      displacement[key] = { x: 0, y: 0 }
    })
    for (let i = 0; i < keys.length; i += 1) {
      for (let j = i + 1; j < keys.length; j += 1) {
        const first = positions.get(keys[i]!)!
        const second = positions.get(keys[j]!)!
        let dx = first.x - second.x
        let dy = first.y - second.y
        let distance = Math.hypot(dx, dy)
        if (distance < 1) {
          dx = 1
          dy = (i - j) * 0.1
          distance = Math.hypot(dx, dy)
        }
        const force = (k * k) / distance
        const unitX = dx / distance
        const unitY = dy / distance
        displacement[keys[i]!]!.x += unitX * force
        displacement[keys[i]!]!.y += unitY * force
        displacement[keys[j]!]!.x -= unitX * force
        displacement[keys[j]!]!.y -= unitY * force
      }
    }
    for (const [from, to] of pairs) {
      const first = positions.get(from)!
      const second = positions.get(to)!
      let dx = first.x - second.x
      let dy = first.y - second.y
      let distance = Math.hypot(dx, dy)
      if (distance < 1) {
        dx = 1
        distance = 1
      }
      const force = (distance * distance) / k
      const unitX = dx / distance
      const unitY = dy / distance
      displacement[from]!.x -= unitX * force
      displacement[from]!.y -= unitY * force
      displacement[to]!.x += unitX * force
      displacement[to]!.y += unitY * force
    }
    const temperature = Math.max(0.8, 18 * (1 - iteration / iterations))
    keys.forEach(key => {
      const point = positions.get(key)!
      const move = displacement[key]!
      const magnitude = Math.max(1, Math.hypot(move.x, move.y))
      point.x += (move.x / magnitude) * temperature
      point.y += (move.y / magnitude) * temperature
      point.x += (centerX - point.x) * 0.01
      point.y += (centerY - point.y) * 0.01
      point.x = Math.max(100, Math.min(width - 100, point.x))
      point.y = Math.max(45, Math.min(height - 45, point.y))
    })
    // 节点框碰撞消解：避免节点和标题互相压住。
    for (let pass = 0; pass < 2; pass += 1) {
      for (let i = 0; i < keys.length; i += 1) {
        for (let j = i + 1; j < keys.length; j += 1) {
          const first = positions.get(keys[i]!)!
          const second = positions.get(keys[j]!)!
          let dx = second.x - first.x
          let dy = second.y - first.y
          const overlapX = 196 - Math.abs(dx)
          const overlapY = 52 - Math.abs(dy)
          if (overlapX <= 0 || overlapY <= 0) continue
          if (dx === 0) dx = i % 2 === 0 ? 1 : -1
          if (dy === 0) dy = j % 2 === 0 ? 1 : -1
          if (overlapX < overlapY) {
            const shift = (overlapX / 2 + 1) * (dx > 0 ? 1 : -1)
            first.x -= shift
            second.x += shift
          } else {
            const shift = (overlapY / 2 + 1) * (dy > 0 ? 1 : -1)
            first.y -= shift
            second.y += shift
          }
          first.x = Math.max(100, Math.min(width - 100, first.x))
          second.x = Math.max(100, Math.min(width - 100, second.x))
          first.y = Math.max(45, Math.min(height - 45, first.y))
          second.y = Math.max(45, Math.min(height - 45, second.y))
        }
      }
    }
  }
  const graphNodes = nodes.map(node => {
    const point = positions.get(node.key)!
    const offset = exploreNodeOffsets.value[node.key] || { x: 0, y: 0 }
    return { ...node, x: point.x + offset.x, y: point.y + offset.y }
  })
  const byKey = new Map(graphNodes.map(node => [node.key, node]))
  const edges = Object.values(exploreEdges.value)
    .map(edge => {
      const from = byKey.get(edge.fromKey)
      const to = byKey.get(edge.toKey)
      if (!from || !to) return null
      const dx = to.x - from.x
      const dy = to.y - from.y
      const distance = Math.hypot(dx, dy) || 1
      const unitX = dx / distance
      const unitY = dy / distance
      const edgeDistance = Math.min(84 / Math.max(Math.abs(unitX), 1e-6), 16 / Math.max(Math.abs(unitY), 1e-6))
      return {
        ...edge,
        x1: from.x + unitX * edgeDistance,
        y1: from.y + unitY * edgeDistance,
        x2: to.x - unitX * edgeDistance,
        y2: to.y - unitY * edgeDistance
      }
    })
    .filter((edge): edge is NonNullable<typeof edge> => edge !== null)
  return { nodes: graphNodes, edges, width, height }
})

const openExploreNode = async (rawKey: string) => {
  const requestId = ++exploreRequestId
  exploreSelectedKey.value = rawKey
  exploreLoadingKey.value = rawKey
  try {
    await loadDetail(rawKey, exploreNodes.value[rawKey]?.objectType || props.objectType)
    // 快速连点多个节点时，只允许最后一次请求补图。
    if (requestId !== exploreRequestId || detailMode.value !== 'graph') return
    mergeCurrentLinksIntoExplore()
  } finally {
    if (requestId === exploreRequestId) exploreLoadingKey.value = ''
  }
}

const onDetailModeChange = (mode: string | number | boolean | undefined) => {
  detailMode.value = mode as 'table' | 'search' | 'graph'
  if (detailMode.value === 'graph' && detail.value) seedExploreGraph()
}

interface InstanceGraphNode {
  key: string
  label: string
  x: number
  y: number
  isCenter: boolean
  rawKey: string
}

interface InstanceGraphEdge {
  key: string
  x1: number
  y1: number
  x2: number
  y2: number
}

const instanceGraph = computed<{ nodes: InstanceGraphNode[]; edges: InstanceGraphEdge[] }>(() => {
  const data = detail.value
  if (!data) return { nodes: [], edges: [] }
  const centerX = 300
  const centerY = 190
  const radiusX = 210
  const radiusY = 95
  const related = new Map<string, { rawKey: string; label: string }>()
  for (const group of linkedGroups.value) {
    for (const entry of group.entries) {
      const rawKey = String(entry.related_object_key || '')
      if (rawKey && !related.has(rawKey)) {
        related.set(rawKey, { rawKey, label: rawKey.split(':').slice(1).join(':') || rawKey })
      }
    }
  }
  const relatedNodes: InstanceGraphNode[] = [...related.values()].slice(0, 12).map((item, index, list) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / Math.max(list.length, 1)
    return {
      key: `related:${item.rawKey}`,
      label: item.label.length > 16 ? `${item.label.slice(0, 15)}…` : item.label,
      x: centerX + Math.cos(angle) * radiusX,
      y: centerY + Math.sin(angle) * radiusY,
      isCenter: false,
      rawKey: item.rawKey
    }
  })
  const center: InstanceGraphNode = {
    key: 'center',
    label: (detailTitle.value || data.object_key).slice(0, 18),
    x: centerX,
    y: centerY,
    isCenter: true,
    rawKey: data.object_key
  }
  const edges: InstanceGraphEdge[] = relatedNodes.map(node => {
    const dx = node.x - centerX
    const dy = node.y - centerY
    const distance = Math.hypot(dx, dy) || 1
    const unitX = dx / distance
    const unitY = dy / distance
    const halfWidth = 84
    const halfHeight = 16
    const edgeDistance = Math.min(halfWidth / Math.max(Math.abs(unitX), 1e-6), halfHeight / Math.max(Math.abs(unitY), 1e-6))
    return {
      key: `${center.key}:${node.key}`,
      x1: centerX + unitX * edgeDistance,
      y1: centerY + unitY * edgeDistance,
      x2: node.x - unitX * edgeDistance,
      y2: node.y - unitY * edgeDistance
    }
  })
  return { nodes: [center, ...relatedNodes], edges }
})

const loadMeta = async (objectType: string) => {
  if (!objectType) return
  const [metaRes, fromRes, toRes] = await Promise.all([
    getAdminOntologyObjectType(objectType),
    getAdminOntologyRelationTypes({ from_object_type: objectType }),
    getAdminOntologyRelationTypes({ to_object_type: objectType })
  ])
  if (metaRes.status === 'success') meta.value = metaRes.data
  const labels: Record<string, string> = {}
  if (fromRes.status === 'success') {
    for (const relation of fromRes.data.relation_types || []) {
      labels[relation.relation_type_name] = relation.from_display_name || relation.relation_type_name
    }
  }
  if (toRes.status === 'success') {
    for (const relation of toRes.data.relation_types || []) {
      labels[relation.relation_type_name] = relation.to_display_name || labels[relation.relation_type_name] || relation.relation_type_name
    }
  }
  relationLabels.value = labels
}

const loadList = async (resetPage = false) => {
  if (!props.objectType) return
  if (resetPage) page.value = 1
  loading.value = true
  error.value = ''
  try {
    const res = await searchAdminOntologyObjects(props.objectType, buildSearchPayload())
    if (res.status === 'success') {
      objects.value = res.data.objects || []
      hasMore.value = Boolean(res.data.has_more)
      totalCount.value = Number(res.data.total || 0)
      titleProperty.value = res.data.title_property || ''
    } else {
      error.value = res.msg || '加载对象列表失败'
    }
  } catch (err) {
    console.error('加载对象列表失败:', err)
    error.value = '加载对象列表失败，请检查映射和数据源权限'
  } finally {
    loading.value = false
  }
}

const loadDetail = async (rawKey: string, objectType = props.objectType, seed?: OntologyObjectListItem) => {
  const separator = rawKey.indexOf(':')
  const type = separator > 0 ? rawKey.slice(0, separator) : objectType
  const key = separator > 0 ? rawKey.slice(separator + 1) : rawKey
  selectedKey.value = rawKey
  detailError.value = ''
  const cached = detailCache.get(rawKey)
  if (cached) {
    detail.value = cached
    detailLoading.value = false
    linksLoading.value = false
    afterDetailLoaded()
    return
  }
  if (seed) {
    // 列表行已经带回属性：先渲染属性，再在后台补 links，避免点击后整块等待。
    detail.value = {
      object_type: type,
      object_key: rawKey,
      properties: seed.properties || {},
      links: {}
    }
    detailLoading.value = false
    linksLoading.value = true
  } else {
    // 切换图节点时保留上一个对象，避免整块变成白屏；加载中给轻量提示。
    detailLoading.value = true
    linksLoading.value = false
  }
  try {
    const res = await readAdminOntologyObject(type, key)
    if (res.status === 'success') {
      detail.value = res.data
      detailCache.set(rawKey, res.data)
      afterDetailLoaded()
      if (type !== props.objectType) await loadMeta(type)
    } else {
      detailError.value = res.msg || '读取对象失败'
    }
  } catch (err) {
    console.error('读取对象失败:', err)
    detailError.value = '读取对象失败，请检查映射和数据源权限'
  } finally {
    detailLoading.value = false
    linksLoading.value = false
  }
}

const selectRow = (row: OntologyObjectListItem) => {
  history.value = []
  listSelectedKey.value = row.object_key
  seedAfterLoad.value = detailMode.value === 'graph'
  void loadDetail(row.object_key, props.objectType, row)
}

const openLinked = (rawKey: unknown) => {
  const key = String(rawKey || '')
  if (!key || !detail.value) return
  if (detailMode.value === 'graph') {
    openExploreNode(key)
    return
  }
  history.value.push({ objectType: props.objectType, objectKey: detail.value.object_key, label: detailTitle.value || detail.value.object_key })
  void loadDetail(key)
}

const backToObject = async () => {
  const previous = history.value.pop()
  if (!previous) return
  await loadDetail(previous.objectKey, previous.objectType)
  if (previous.objectType !== props.objectType) await loadMeta(previous.objectType)
}

const resetToList = () => {
  selectedKey.value = ''
  listSelectedKey.value = ''
  detail.value = null
  detailError.value = ''
  linksLoading.value = false
  history.value = []
}

const rowClassName = ({ row }: { row: OntologyObjectListItem }) =>
  row.object_key === listSelectedKey.value ? 'object-instance-row--active' : ''

const titleValue = (row: OntologyObjectListItem) => {
  if (!titleProperty.value) return ''
  const value = row.properties?.[titleProperty.value]
  return value == null ? '' : String(value)
}

const previewValueText = (value: unknown) => {
  if (value == null) return '-'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

const initialize = () => {
  if (!props.objectType) return
  keyword.value = ''
  page.value = 1
  filters.value = []
  sortProperty.value = ''
  totalCount.value = 0
  exploreNodes.value = {}
  exploreEdges.value = {}
  exploreSelectedKey.value = ''
  resetToList()
  void loadMeta(props.objectType)
  void loadList()
}

watch(() => props.objectType, initialize)
onMounted(initialize)
</script>

<template>
  <div class="flex h-[640px] min-h-0 gap-4">
    <section class="flex w-[360px] shrink-0 flex-col border-r border-slate-100 pr-3">
      <div class="mb-2 flex items-center gap-2">
        <el-input
          v-model="keyword"
          size="small"
          clearable
          placeholder="搜索标题键或主键"
          @keyup.enter="loadList(true)"
          @clear="loadList(true)"
        />
        <el-button size="small" type="primary" @click="loadList(true)">搜索</el-button>
      </div>
      <div class="mb-2 flex items-center gap-1">
        <el-popover placement="bottom-start" :width="440" trigger="click">
          <template #reference>
            <el-button size="small">
              <i class="fa-solid fa-filter mr-1 text-[10px]"></i>筛选
              <span v-if="filters.length > 0" class="ml-1 text-[10px] text-blue-600">{{ filters.length }}</span>
            </el-button>
          </template>
          <div class="flex flex-col gap-1.5">
            <div v-for="(item, index) in filters" :key="index" class="flex items-center gap-1">
              <el-select v-model="item.property" size="small" class="!w-28">
                <el-option
                  v-for="prop in filterProperties"
                  :key="prop.name"
                  :label="prop.label"
                  :value="prop.name"
                />
              </el-select>
              <el-select v-model="item.operator" size="small" class="!w-24">
                <el-option
                  v-for="operator in OPERATOR_OPTIONS"
                  :key="operator.value"
                  :label="operator.label"
                  :value="operator.value"
                />
              </el-select>
              <el-input
                v-model="item.value"
                size="small"
                class="min-w-0 flex-1"
                :placeholder="item.operator === 'in' ? '多个值用逗号分隔' : '值'"
              />
              <el-button link size="small" class="!p-0 text-slate-400" @click="removeFilter(index)">
                <i class="fa-solid fa-trash-can text-[10px]"></i>
              </el-button>
            </div>
            <div v-if="filters.length === 0" class="text-xs text-slate-400">还没有筛选条件</div>
            <div class="mt-1 flex items-center gap-1 border-t border-slate-100 pt-2">
              <el-button link size="small" @click="addFilter">
                <i class="fa-solid fa-plus mr-1 text-[10px]"></i>添加条件
              </el-button>
              <el-button link size="small" class="ml-auto" @click="clearFilters">清空</el-button>
              <el-button size="small" type="primary" @click="applyFilters">应用</el-button>
            </div>
          </div>
        </el-popover>
        <el-select
          v-model="sortProperty"
          size="small"
          clearable
          placeholder="排序"
          class="min-w-0 flex-1"
        >
          <el-option
            v-for="prop in filterProperties"
            :key="prop.name"
            :label="prop.label"
            :value="prop.name"
          />
        </el-select>
        <el-select v-if="sortProperty" v-model="sortDirection" size="small" class="!w-20">
          <el-option label="升序" value="asc" />
          <el-option label="降序" value="desc" />
        </el-select>
        <el-button size="small" @click="applyFilters">应用</el-button>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="mb-2" />
      <el-table
        v-loading="loading"
        :data="objects"
        size="small"
        border
        height="540"
        empty-text="暂无对象数据"
        :row-class-name="rowClassName"
        @row-click="selectRow"
      >
        <el-table-column v-if="titleProperty" label="标题" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ titleValue(row) || '-' }}</template>
        </el-table-column>
        <el-table-column prop="object_key" label="对象主键" min-width="170" show-overflow-tooltip />
      </el-table>
      <div class="mt-2 flex items-center justify-end gap-2">
        <span class="text-[11px] text-slate-400">共 {{ totalCount }} 个 · 第 {{ page }} 页</span>
        <el-button size="small" :disabled="page <= 1 || loading" @click="page -= 1; loadList()">上一页</el-button>
        <el-button size="small" :disabled="!hasMore || loading" @click="page += 1; loadList()">下一页</el-button>
      </div>
    </section>

    <section class="min-w-0 flex-1 overflow-y-auto pl-1">
      <div v-if="!detail && !detailLoading" class="flex h-full items-center justify-center text-center text-xs text-slate-400">
        <div>
          <i class="fa-solid fa-table-list mb-2 block text-2xl text-slate-200"></i>
          从左侧列表选择一个对象
        </div>
      </div>
      <div v-else-if="!detail && detailLoading" class="py-2">
        <el-skeleton :rows="4" animated />
      </div>
      <template v-else-if="detail">
        <div v-if="detailLoading" class="mb-2 rounded bg-blue-50 px-2 py-1 text-[11px] text-blue-600">
          <i class="fa-solid fa-spinner fa-spin mr-1"></i>加载所选对象详情中…
        </div>
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" show-icon class="mb-2" />
        <div class="mb-3 flex items-center gap-2">
          <el-button v-if="history.length > 0" link size="small" @click="backToObject">返回</el-button>
          <el-button link size="small" @click="resetToList">返回列表</el-button>
          <span class="font-mono text-xs text-slate-700">{{ detail.object_key }}</span>
          <span v-if="detailTitle" class="text-xs text-slate-500">{{ detailTitle }}</span>
          <el-radio-group v-model="detailMode" size="small" class="ml-auto" @change="onDetailModeChange">
            <el-radio-button value="table">属性</el-radio-button>
            <el-radio-button value="search">关系探索</el-radio-button>
            <el-radio-button value="graph">图探索</el-radio-button>
          </el-radio-group>
        </div>

        <template v-if="detailMode === 'table'">
        <div class="mb-1 text-xs font-medium text-slate-600">属性</div>
        <el-table :data="detailPropertyRows" size="small" border empty-text="暂无属性">
          <el-table-column label="属性" min-width="150">
            <template #default="{ row }">
              <span class="font-mono text-xs text-slate-700">{{ row.name }}</span>
              <span v-if="row.label" class="ml-1.5 text-xs text-slate-400">{{ row.label }}</span>
            </template>
          </el-table-column>
          <el-table-column label="值" min-width="220">
            <template #default="{ row }">{{ previewValueText(row.value) }}</template>
          </el-table-column>
        </el-table>

        <div v-if="linksLoading" class="mt-3 text-xs text-slate-400">加载关联对象中…</div>
        <template v-else-if="linkedGroups.length > 0">
          <div class="mb-1 mt-3 text-xs font-medium text-slate-600">关联对象</div>
          <div v-for="group in linkedGroups" :key="group.relationName" class="mb-2 rounded border border-slate-200 bg-white px-3 py-2">
            <div class="flex items-center gap-2">
              <span class="text-xs text-slate-700">{{ group.label }}</span>
              <el-tag size="small" type="info" effect="plain">{{ group.entries.length }}</el-tag>
            </div>
            <div class="mt-1 flex flex-col gap-0.5">
              <button
                v-for="(item, index) in group.entries"
                :key="index"
                type="button"
                class="w-full border-0 bg-transparent p-0 text-left font-mono text-[11px] text-slate-500 hover:text-blue-600"
                @click="openLinked(item.related_object_key)"
              >
                {{ item.related_object_key || '-' }}
              </button>
            </div>
          </div>
        </template>
        </template>
        <div v-else-if="detailMode === 'search'" class="rounded border border-slate-200 bg-white p-3">
          <div class="mb-2 flex items-center gap-2">
            <span class="text-xs font-medium text-slate-700">关系探索</span>
            <span class="font-mono text-[11px] text-slate-500">{{ detail.object_key }}</span>
            <span v-if="history.length > 0" class="text-[11px] text-slate-400">已下钻 {{ history.length }} 层</span>
            <span v-if="linksLoading" class="ml-auto text-[11px] text-slate-400">加载关系…</span>
            <span v-else class="ml-auto text-[11px] text-slate-400">{{ searchAroundCount }} 个关联对象</span>
          </div>
          <el-input
            v-model="searchKeyword"
            size="small"
            clearable
            placeholder="按关联对象主键过滤"
            class="mb-2 !w-64"
          />
          <div v-if="linksLoading" class="py-3 text-xs text-slate-400">加载关系数据中…</div>
          <div v-else-if="searchAroundGroups.length === 0" class="py-3 text-xs text-slate-400">
            {{ searchKeyword ? '没有匹配的关联对象' : '当前对象没有关联对象' }}
          </div>
          <div v-else class="flex flex-col gap-2">
            <div v-for="group in searchAroundGroups" :key="group.relationName" class="rounded border border-slate-100 bg-slate-50/60 px-2 py-1.5">
              <div class="mb-1 flex items-center gap-2">
                <span class="text-xs text-slate-700">{{ group.label }}</span>
                <el-tag size="small" type="info" effect="plain">{{ group.entries.length }}</el-tag>
                <span class="text-[10px] text-slate-400">{{ group.relationName }}</span>
              </div>
              <div class="flex flex-col gap-0.5">
                <button
                  v-for="(item, index) in group.entries"
                  :key="index"
                  type="button"
                  class="flex w-full items-center border-0 bg-transparent p-0 text-left font-mono text-[11px] text-slate-500 hover:text-blue-600"
                  @click="openLinked(item.related_object_key)"
                >
                  <i class="fa-solid fa-arrow-right mr-1 text-[9px] text-slate-300"></i>
                  {{ item.related_object_key || '-' }}
                </button>
              </div>
            </div>
          </div>
        </div>
        <div
          v-else
          class="rounded border border-slate-200 bg-white p-2"
          :class="graphFullscreen ? 'fixed inset-0 z-[var(--z-fullscreen-layer)] flex flex-col bg-white p-4' : ''"
          @mousemove="onExploreNodeDrag"
          @mouseup="endExploreNodeDrag"
          @mouseleave="endExploreNodeDrag"
        >
          <div class="mb-1 flex flex-wrap items-center gap-2">
            <span class="text-xs font-medium text-slate-700">图探索</span>
            <span class="text-[10px] text-slate-400">力导向布局</span>
            <span class="text-[11px] text-slate-400">
              已加入 {{ Object.keys(exploreNodes).length }} 个对象 · {{ Object.keys(exploreEdges).length }} 条关系
            </span>
            <span v-if="exploreSelectedKey" class="font-mono text-[11px] text-slate-500">{{ exploreSelectedKey }}</span>
            <div class="ml-auto flex items-center gap-1">
              <el-button size="small" @click="showGraphProperties = !showGraphProperties">
                <i class="fa-solid fa-table-columns mr-1 text-[10px]"></i>
                {{ showGraphProperties ? '隐藏属性' : '显示属性' }}
              </el-button>
              <el-button size="small" @click="graphFullscreen = !graphFullscreen">
                <i :class="['fa-solid mr-1 text-[10px]', graphFullscreen ? 'fa-compress' : 'fa-expand']"></i>
                {{ graphFullscreen ? '退出全屏' : '全屏' }}
              </el-button>
              <el-button size="small" :disabled="!detail" @click="mergeCurrentLinksIntoExplore">展开关联</el-button>
              <el-button size="small" :disabled="!detail" @click="seedExploreGraph">重置为当前对象</el-button>
              <el-button size="small" :disabled="Object.keys(exploreNodes).length === 0" @click="clearExploreGraph">清空</el-button>
            </div>
          </div>
          <div class="flex min-h-0 gap-2" :class="graphFullscreen ? 'flex-1' : ''">
            <div class="min-w-0 flex-1">
              <div
                v-if="Object.keys(exploreNodes).length === 0"
                :class="graphFullscreen ? 'flex h-full min-h-[340px] items-center justify-center text-xs text-slate-400' : 'flex h-[420px] items-center justify-center text-xs text-slate-400'"
              >
                从左侧选择一个对象，或点击「重置为当前对象」开始
              </div>
              <svg
                v-else
                :viewBox="`0 0 ${exploreGraph.width} ${exploreGraph.height}`"
                :class="graphFullscreen ? 'h-full w-full' : 'h-[420px] w-full'"
                role="img"
                aria-label="对象实例探索图"
              >
                <g v-for="edge in exploreGraph.edges" :key="edge.key">
                  <title>{{ edge.relationName }}</title>
                  <line :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2" stroke="#cbd5e1" stroke-width="1.2" />
                </g>
                <g
                  v-for="node in exploreGraph.nodes"
                  :key="node.key"
                  class="cursor-move"
                  @mousedown="startExploreNodeDrag($event, node)"
                  @click="openExploreNode(node.key)"
                >
                  <title>{{ node.key }}</title>
                  <rect
                    :x="node.x - 84"
                    :y="node.y - 16"
                    width="168"
                    height="32"
                    rx="5"
                    :fill="node.key === exploreSelectedKey ? '#eff6ff' : '#ffffff'"
                    :stroke="node.key === exploreSelectedKey ? '#3b82f6' : '#dbeafe'"
                    :stroke-width="node.key === exploreSelectedKey ? 2 : 1.2"
                  />
                  <text
                    :x="node.x"
                    :y="node.y + 4"
                    text-anchor="middle"
                    :class="node.key === exploreSelectedKey ? 'fill-blue-700' : 'fill-slate-700'"
                    style="font-size: 10px; font-weight: 500"
                  >
                    {{ node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label }}
                  </text>
                  <g v-if="exploreLoadingKey === node.key">
                    <circle
                      :cx="node.x - 64"
                      :cy="node.y"
                      r="6"
                      fill="none"
                      stroke="#3b82f6"
                      stroke-width="2"
                      stroke-dasharray="24 12"
                    >
                      <animateTransform
                        attributeName="transform"
                        type="rotate"
                        :from="`0 ${node.x - 64} ${node.y}`"
                        :to="`360 ${node.x - 64} ${node.y}`"
                        dur="0.8s"
                        repeatCount="indefinite"
                      />
                    </circle>
                  </g>
                </g>
              </svg>
            </div>
            <aside
              v-if="detail && showGraphProperties"
              class="w-[250px] shrink-0 overflow-y-auto rounded border border-slate-100 bg-slate-50/50 p-2"
              :class="graphFullscreen ? 'h-full' : 'max-h-[420px]'"
            >
              <div class="mb-1 flex items-center gap-1">
                <span class="text-[11px] font-medium text-slate-700">选中对象</span>
                <span class="ml-auto font-mono text-[10px] text-slate-400">{{ detail.object_key }}</span>
              </div>
              <div v-if="detailTitle" class="mb-2 text-xs font-medium text-slate-700">{{ detailTitle }}</div>
              <div class="mb-1 text-[10px] text-slate-400">属性</div>
              <div v-for="row in detailPropertyRows" :key="row.name" class="border-b border-slate-100 py-1">
                <div class="flex items-center gap-1">
                  <span class="font-mono text-[10px] text-slate-600">{{ row.name }}</span>
                  <span v-if="row.label" class="text-[10px] text-slate-400">{{ row.label }}</span>
                </div>
                <div class="break-all text-[11px] text-slate-700">{{ previewValueText(row.value) }}</div>
              </div>
              <div v-if="detailPropertyRows.length === 0" class="text-[11px] text-slate-400">暂无属性</div>
            </aside>
          </div>
          <div class="mt-1 text-[10px] text-slate-400">
            点击节点切换选中；点击「展开关联」把当前节点的直接关联加入图，继续沿图探索。
          </div>
        </div>
      </template>
    </section>
  </div>
</template>


<style scoped>
:deep(.object-instance-row--active > td.el-table__cell) {
  background-color: #eff6ff;
}
:deep(.object-instance-row--active:hover > td.el-table__cell) {
  background-color: #dbeafe;
}
</style>
