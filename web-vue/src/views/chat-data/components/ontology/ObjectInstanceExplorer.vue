<script setup lang="ts">
/**
 * 对象类型 · 对象实例工作台。
 * 主从布局：左侧分页列表/搜索，右侧对象详情；关联对象在当前工作台下钻，不再弹窗跳转。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getAdminOntologyObjectType,
  getAdminOntologyRelationTypes,
  readAdminOntologyObject,
  searchAdminOntologyObjects,
  traverseAdminOntologyGraph
} from '../../api'
import type {
  OntologyRelationTypeItem,
  OntologyObjectFilter,
  OntologyObjectInstance,
  OntologyPropertyItem,
  OntologyObjectListItem,
  OntologyObjectSort,
  OntologyObjectTypeItem
} from '../../types'

const props = defineProps({
  objectType: { type: String, default: '' },
  objectLabel: { type: String, default: '' },
  // 父级（对象类型详情卡）已取过同一对象类型的元数据/关系类型，优先复用，避免重复请求。
  initialMeta: { type: Object, default: null },
  initialFromRelations: { type: Array, default: () => [] },
  initialToRelations: { type: Array, default: () => [] }
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

/** 左侧列表所属对象类型的元数据（筛选/排序/机构下拉只认它）。 */
const meta = ref<OntologyObjectTypeItem | null>(null)
/** 右侧详情当前对象类型的元数据：下钻到关联对象时必须用对端类型，不能覆盖 meta。 */
const detailMeta = ref<OntologyObjectTypeItem | null>(null)
const metaCache = new Map<string, OntologyObjectTypeItem>()
const relationLabelsLoaded = new Set<string>()
const selectedMember = ref('')
const relationLabels = ref<Record<string, string>>({})
const selectedKey = ref('')
const listSelectedKey = ref('')
const detail = ref<OntologyObjectInstance | null>(null)
const detailLoading = ref(false)
const linksLoading = ref(false)
const detailError = ref('')
const detailMode = ref<'table' | 'graph'>('table')
const detailCache = new Map<string, OntologyObjectInstance>()
const history = ref<Array<{ objectType: string; objectKey: string; label: string }>>([])
// 列表/详情的请求序号：慢查询乱序返回时只允许最后一次请求落盘，避免旧机构/旧对象数据残留。
let listRequestId = 0
let detailRequestId = 0

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

const memberOptions = computed(() => meta.value?.source_binding?.member_datasource_ids || [])

const parseObjectKey = (rawKey: string, fallbackType: string) => {
  const parts = rawKey.split(':')
  const first = parts[0] ?? ''
  const second = parts[1] ?? ''
  if (parts.length >= 3) {
    return { member: first, type: second, key: parts.slice(2).join(':') }
  }
  if (parts.length >= 2 && first === fallbackType) {
    return { member: '', type: fallbackType, key: parts.slice(1).join(':') }
  }
  return { member: '', type: fallbackType, key: rawKey }
}

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
  offset: (page.value - 1) * pageSize,
  member_datasource_ids: selectedMember.value ? [selectedMember.value] : []
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
  for (const prop of detailMeta.value?.properties || meta.value?.properties || []) {
    labels[prop.property_name] = prop.display_name || prop.property_name
  }
  return labels
})

const derivationSummary = (property: OntologyPropertyItem | undefined) => {
  const derivation = property?.derivation
  if (!derivation) return ''
  const target = derivation.target_property ? `(${derivation.target_property})` : ''
  const operator = derivation.operator || derivation.kind
  const period = derivation.period
    ? ` · 近${derivation.period.days}天(${derivation.period.time_property})`
    : ''
  return `${derivation.relation_type_name} · ${operator}${target}${period}`
}

const propertyMetaByName = computed<Record<string, OntologyPropertyItem>>(() => {
  const source = detailMeta.value?.properties || meta.value?.properties || []
  return Object.fromEntries(source.map(property => [property.property_name, property]))
})

const detailPropertyRows = computed(() => {
  const data = detail.value
  if (!data) return []
  return Object.entries(data.properties || {}).map(([name, value]) => {
    const property = propertyMetaByName.value[name]
    return {
      name,
      label: propertyLabels.value[name] || '',
      value,
      valueType: property?.value_type || '',
      derivation: property?.derivation || null,
      derivationText: derivationSummary(property)
    }
  })
})

const detailTitle = computed(() => {
  const data = detail.value
  if (!data) return ''
  const titleKey = detailMeta.value?.properties?.find(item => item.is_title)?.property_name ||
    meta.value?.properties?.find(item => item.is_title)?.property_name
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
const graphDetailWidth = ref(320)
let graphDetailStartX = 0
let graphDetailStartWidth = 0

const onGraphDetailResize = (event: MouseEvent) => {
  const delta = graphDetailStartX - event.clientX
  graphDetailWidth.value = Math.min(760, Math.max(260, graphDetailStartWidth + delta))
}

const stopGraphDetailResize = () => {
  window.removeEventListener('mousemove', onGraphDetailResize)
  window.removeEventListener('mouseup', stopGraphDetailResize)
}

const startGraphDetailResize = (event: MouseEvent) => {
  graphDetailStartX = event.clientX
  graphDetailStartWidth = graphDetailWidth.value
  window.addEventListener('mousemove', onGraphDetailResize)
  window.addEventListener('mouseup', stopGraphDetailResize)
  event.preventDefault()
}

const resetGraphDetailWidth = () => {
  graphDetailWidth.value = 320
}
const exploreNodeOffsets = ref<Record<string, { x: number; y: number }>>({})
const draggingExploreKey = ref('')
const exploreLoadingKey = ref('')
/** 图查询失败时的可见降级提示；不要再只写 console 让用户以为图是完整的。 */
const graphFallbackNotice = ref('')
const graphAsOf = ref('')
const graphDegradedReason = ref('')
const graphLoading = ref(false)
let graphLoadingCount = 0
const GRAPH_NODE_LIMIT = 80
const graphExpansionNotice = ref('')
const graphNewKeys = ref<string[]>([])
let graphNewKeysTimer: number | null = null

const markGraphNewKeys = (keys: string[]) => {
  graphNewKeys.value = keys
  if (graphNewKeysTimer !== null) window.clearTimeout(graphNewKeysTimer)
  graphNewKeysTimer = window.setTimeout(() => {
    graphNewKeys.value = []
    graphNewKeysTimer = null
  }, 5000)
}
/** 图节点类型显示名（客户/订单/发货单...），避免图上只看到英文对象名。 */
const objectTypeDisplayNames = ref<Record<string, string>>({})
const objectTypeLabelPending = new Set<string>()
let exploreRequestId = 0
let exploreStartX = 0
let exploreStartY = 0
let exploreStartOffsetX = 0
let exploreStartOffsetY = 0
let exploreDragMoved = false

const startExploreNodeDrag = (event: MouseEvent, node: { key: string; x: number; y: number }) => {
  event.preventDefault()
  event.stopPropagation()
  const offset = exploreNodeOffsets.value[node.key] || { x: 0, y: 0 }
  exploreDragMoved = false
  draggingExploreKey.value = node.key
  exploreStartX = event.clientX
  exploreStartY = event.clientY
  exploreStartOffsetX = offset.x
  exploreStartOffsetY = offset.y
}

const onExploreNodeDrag = (event: MouseEvent) => {
  if (!draggingExploreKey.value) return
  if (Math.abs(event.clientX - exploreStartX) > 3 || Math.abs(event.clientY - exploreStartY) > 3) {
    exploreDragMoved = true
  }
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

const onExploreNodeClick = (rawKey: string) => {
  // 拖动结束后浏览器仍可能补发 click；拖动过的节点只移动，不切换选中/加载。
  if (exploreDragMoved) {
    exploreDragMoved = false
    return
  }
  // 点击只选中节点，不发起详情/关系加载；必须点节点下方按钮才会加载。
  seedAfterLoad.value = false
  exploreSelectedKey.value = rawKey
}

const orderedGraphNodes = computed(() => {
  const nodes = exploreGraph.value.nodes
  const selected = exploreSelectedKey.value
  if (!selected) return nodes
  return [...nodes.filter(node => node.key !== selected), ...nodes.filter(node => node.key === selected)]
})

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
  const linkedTypes: string[] = []
  addExploreNode(data.object_key, data.object_type || props.objectType, detailTitle.value || data.object_key, data.properties || {})
  for (const group of linkedGroups.value) {
    for (const entry of group.entries) {
      const relatedKey = String(entry.related_object_key || '')
      if (!relatedKey) continue
      const keyParts = relatedKey.split(':')
      const relatedType = String(entry.related_object_type || '').trim() ||
        (keyParts.length >= 3 && keyParts[1] ? keyParts[1] : props.objectType)
      const relatedTitle = String(entry.related_title || '').trim()
      linkedTypes.push(relatedType)
      addExploreNode(relatedKey, relatedType, relatedTitle || relatedKey.split(':').slice(1).join(':') || relatedKey, {})
      addExploreEdge(group.relationName, data.object_key, relatedKey)
    }
  }
  void ensureObjectTypeDisplayNames([data.object_type || props.objectType, ...linkedTypes])
}

/** Nebula 图结果合并到本地图探索；图不可用时保留一跳 links 兜底。 */
const objectTypeDisplayName = (objectType: string) => {
  const key = String(objectType || '')
  return objectTypeDisplayNames.value[key] || key
}

const ensureObjectTypeDisplayNames = async (types: string[]) => {
  const pending = [...new Set(types.map(type => String(type || '')).filter(Boolean))]
    .filter(type => !objectTypeDisplayNames.value[type] && !objectTypeLabelPending.has(type))
  if (!pending.length) return
  pending.forEach(type => objectTypeLabelPending.add(type))
  await Promise.all(
    pending.map(async type => {
      try {
        const cached = metaCache.get(type)
        const detail = cached || (await getAdminOntologyObjectType(type)).data
        if (detail) {
          metaCache.set(type, detail)
          objectTypeDisplayNames.value = {
            ...objectTypeDisplayNames.value,
            [type]: detail.display_name || type
          }
        }
      } catch (err) {
        console.warn('加载图节点类型显示名失败:', type, err)
      } finally {
        objectTypeLabelPending.delete(type)
      }
    })
  )
}

const mergeGraphResult = (data: {
  nodes?: Array<{ object_key: string; object_type: string; title: string }>
  edges?: Array<{ from_object_key: string; to_object_key: string; relation_type: string }>
  graph_as_of?: string
  degraded_reason?: string
}) => {
  graphAsOf.value = data.graph_as_of || ''
  graphDegradedReason.value = data.degraded_reason || ''
  const addedKeys: string[] = []
  let truncated = false
  for (const node of data.nodes || []) {
    if (!node.object_key) continue
    if (!exploreNodes.value[node.object_key] && Object.keys(exploreNodes.value).length >= GRAPH_NODE_LIMIT) {
      truncated = true
      continue
    }
    if (!exploreNodes.value[node.object_key]) addedKeys.push(node.object_key)
    addExploreNode(node.object_key, node.object_type || '', node.title || node.object_key, {})
  }
  for (const edge of data.edges || []) {
    if (!exploreNodes.value[edge.from_object_key] || !exploreNodes.value[edge.to_object_key]) continue
    addExploreEdge(edge.relation_type || 'related', edge.from_object_key, edge.to_object_key)
  }
  graphExpansionNotice.value = truncated
    ? `本次展开达到 ${GRAPH_NODE_LIMIT} 个对象上限，已截断；请减少展开深度或缩小范围。`
    : ''
  markGraphNewKeys(addedKeys)
  void ensureObjectTypeDisplayNames((data.nodes || []).map(node => node.object_type))
}

/** 调 Nebula 多跳；返回 false 表示图不可用/无数据，由调用方回退一跳。 */
const loadTraverseIntoExplore = async (objectKey: string, depth = 2): Promise<boolean> => {
  if (!objectKey) return false
  const member = selectedMember.value || parseObjectKey(objectKey, props.objectType).member
  if (!member) return false
  graphLoadingCount += 1
  graphLoading.value = true
  try {
    const res = await traverseAdminOntologyGraph({
      object_key: objectKey,
      depth,
      direction: 'both',
      limit: 30,
      member_datasource_ids: [member]
    })
    if (res.status !== 'success' || !res.data) {
      graphFallbackNotice.value = '图查询暂不可用，已回退为当前对象的一跳关系；结果可能不完整。'
      graphAsOf.value = ''
      graphDegradedReason.value = ''
      return false
    }
    graphFallbackNotice.value = ''
    mergeGraphResult(res.data)
    return true
  } catch (err) {
    console.warn('Nebula 图查询不可用，回退一跳 links:', err)
    graphFallbackNotice.value = '图查询暂不可用，已回退为当前对象的一跳关系；结果可能不完整。'
    graphAsOf.value = ''
    graphDegradedReason.value = ''
    return false
  } finally {
    graphLoadingCount = Math.max(0, graphLoadingCount - 1)
    graphLoading.value = graphLoadingCount > 0
  }
}

const expandCurrentLinks = async () => {
  const currentKey = exploreSelectedKey.value || detail.value?.object_key || ''
  const nodeCount = Object.keys(exploreNodes.value).length
  const edgeCount = Object.keys(exploreEdges.value).length
  const graphLoaded = await loadTraverseIntoExplore(currentKey, 1)
  if (!graphLoaded) mergeCurrentLinksIntoExplore()
  const added = Object.keys(exploreNodes.value).length - nodeCount + Object.keys(exploreEdges.value).length - edgeCount
  if (added <= 0) {
    ElMessage.info('当前节点已无更多可展开的关联')
  }
}

const seedExploreGraph = () => {
  graphFallbackNotice.value = ''
  graphAsOf.value = ''
  graphDegradedReason.value = ''
  graphExpansionNotice.value = ''
  graphNewKeys.value = []
  graphLoadingCount = 0
  graphLoading.value = false
  exploreNodes.value = {}
  exploreEdges.value = {}
  exploreNodeOffsets.value = {}
  exploreSelectedKey.value = detail.value?.object_key || ''
  mergeCurrentLinksIntoExplore()
  // 默认只展开 1 跳，避免一进图就铺满。
  void loadTraverseIntoExplore(exploreSelectedKey.value, 1)
}

const clearExploreGraph = () => {
  graphFallbackNotice.value = ''
  graphAsOf.value = ''
  graphDegradedReason.value = ''
  graphExpansionNotice.value = ''
  graphNewKeys.value = []
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
  const height = Math.max(500, Math.min(860, 340 + nodes.length * 34))
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
          first.y = Math.max(45, Math.min(height - 85, first.y))
          second.y = Math.max(45, Math.min(height - 85, second.y))
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
      const edgeDistance = Math.min(84 / Math.max(Math.abs(unitX), 1e-6), 20 / Math.max(Math.abs(unitY), 1e-6))
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

const viewNodeDetail = async (rawKey: string) => {
  if (!detail.value || detail.value.object_key !== rawKey) {
    await openExploreNode(rawKey)
  }
  showGraphProperties.value = true
}

const expandNodeOneHop = async (rawKey: string) => {
  if (!detail.value || detail.value.object_key !== rawKey) {
    await openExploreNode(rawKey)
  }
  await expandCurrentLinks()
}

const openExploreNode = async (rawKey: string) => {
  const requestId = ++exploreRequestId
  seedAfterLoad.value = false
  exploreSelectedKey.value = rawKey
  exploreLoadingKey.value = rawKey
  try {
    // 点击节点只负责“选中 + 加载详情”，不自动扩图；扩图必须点「展开一跳」。
    await loadDetail(rawKey, exploreNodes.value[rawKey]?.objectType || props.objectType)
    if (requestId !== exploreRequestId || detailMode.value !== 'graph') return
  } finally {
    if (requestId === exploreRequestId) exploreLoadingKey.value = ''
  }
}

const onDetailModeChange = (mode: string | number | boolean | undefined) => {
  detailMode.value = mode as 'table' | 'graph'
  if (detailMode.value === 'graph' && detail.value) seedExploreGraph()
}

/** 某个对象类型的关系显示名（关联下钻和返回都要用；按类型只补一次）。 */
const seedInitialMetadata = () => {
  const initialMeta = props.initialMeta as OntologyObjectTypeItem | null
  if (!initialMeta || (initialMeta.object_type_name || '') !== props.objectType) return
  meta.value = initialMeta
  metaCache.set(props.objectType, initialMeta)
}

const seedInitialRelationLabels = () => {
  if (!props.objectType) return
  const fromRelations = (props.initialFromRelations || []) as OntologyRelationTypeItem[]
  const toRelations = (props.initialToRelations || []) as OntologyRelationTypeItem[]
  const labels: Record<string, string> = { ...relationLabels.value }
  for (const relation of fromRelations) {
    labels[relation.relation_type_name] = relation.from_display_name || relation.relation_type_name
  }
  for (const relation of toRelations) {
    labels[relation.relation_type_name] =
      relation.to_display_name || labels[relation.relation_type_name] || relation.relation_type_name
  }
  relationLabels.value = labels
  // 父级已按 from/to 两个方向取全，初始对象类型不再重复请求；下钻类型仍按需拉取。
  relationLabelsLoaded.add(props.objectType)
}

const loadRelationLabels = async (objectType: string) => {
  if (!objectType || relationLabelsLoaded.has(objectType)) return
  relationLabelsLoaded.add(objectType)
  try {
    const [fromRes, toRes] = await Promise.all([
      getAdminOntologyRelationTypes({ from_object_type: objectType }),
      getAdminOntologyRelationTypes({ to_object_type: objectType })
    ])
    const labels: Record<string, string> = { ...relationLabels.value }
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
  } catch (err) {
    relationLabelsLoaded.delete(objectType)
    console.error('加载关系类型显示名失败:', err)
  }
}

/** 左侧列表所属对象类型的元数据（机构绑定只认列表类型；父级已取过则直接复用）。 */
const loadMeta = async (objectType: string) => {
  if (!objectType) return
  const cachedMeta = metaCache.get(objectType)
  const loaded = cachedMeta || (await getAdminOntologyObjectType(objectType)).data
  if (loaded) {
    meta.value = loaded
    metaCache.set(objectType, loaded)
    objectTypeDisplayNames.value = { ...objectTypeDisplayNames.value, [objectType]: loaded.display_name || objectType }
    if (!detail.value || (detail.value.object_type || objectType) === objectType) detailMeta.value = loaded
    const members = loaded.source_binding?.member_datasource_ids || []
    if (!selectedMember.value || !members.includes(selectedMember.value)) {
      selectedMember.value = members[0] || ''
    }
  }
  await loadRelationLabels(objectType)
}

/** 详情对象类型元数据：关联下钻取对端类型，返回时用缓存恢复，避免污染左侧列表的筛选/排序口径。 */
const ensureDetailMeta = async (objectType: string) => {
  if (!objectType) return
  if (objectType === props.objectType && meta.value?.object_type_name === objectType) {
    detailMeta.value = meta.value
    return
  }
  const cached = metaCache.get(objectType)
  if (cached) {
    detailMeta.value = cached
    return
  }
  try {
    const res = await getAdminOntologyObjectType(objectType)
    if (res.status === 'success') {
      metaCache.set(objectType, res.data)
      objectTypeDisplayNames.value = { ...objectTypeDisplayNames.value, [objectType]: res.data.display_name || objectType }
      if (detail.value?.object_type === objectType) detailMeta.value = res.data
    }
  } catch (err) {
    console.error('加载对象类型元数据失败:', err)
  }
}

const loadList = async (resetPage = false) => {
  if (!props.objectType) return
  if (resetPage) page.value = 1
  const requestId = ++listRequestId
  loading.value = true
  error.value = ''
  try {
    const res = await searchAdminOntologyObjects(props.objectType, buildSearchPayload())
    // 切机构/翻页/搜索连续操作时，慢请求可能后到；只认最后一次请求的结果。
    if (requestId !== listRequestId) return
    if (res.status === 'success') {
      objects.value = res.data.objects || []
      hasMore.value = Boolean(res.data.has_more)
      totalCount.value = Number(res.data.total || 0)
      titleProperty.value = res.data.title_property || ''
    } else {
      error.value = res.msg || '加载对象列表失败'
    }
  } catch (err) {
    if (requestId !== listRequestId) return
    console.error('加载对象列表失败:', err)
    error.value = '加载对象列表失败，请检查映射和数据源权限'
  } finally {
    if (requestId === listRequestId) loading.value = false
  }
}

/** 切换机构：列表按新机构重查，详情/历史/图探索必须清空，避免展示旧机构数据。 */
const onMemberChange = () => {
  detailRequestId += 1
  resetToList()
  detailMeta.value = meta.value
  exploreNodes.value = {}
  exploreEdges.value = {}
  exploreSelectedKey.value = ''
  exploreLoadingKey.value = ''
  void loadList(true)
}

const loadDetail = async (rawKey: string, objectType = props.objectType, seed?: OntologyObjectListItem) => {
  const parsed = parseObjectKey(rawKey, objectType)
  const type = parsed.type
  const key = parsed.key
  const member = parsed.member || selectedMember.value
  const requestId = ++detailRequestId
  selectedKey.value = rawKey
  detailError.value = ''
  const cached = detailCache.get(rawKey)
  if (cached) {
    detail.value = cached
    detailLoading.value = false
    linksLoading.value = false
    await Promise.all([ensureDetailMeta(type), loadRelationLabels(type)])
    if (requestId !== detailRequestId) return
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
    const res = await readAdminOntologyObject(type, key, member ? [member] : [])
    // 慢查询下用户可能已返回/切到别的对象：迟到的响应不能覆盖当前详情。
    if (requestId !== detailRequestId) return
    if (res.status === 'success') {
      detail.value = res.data
      detailCache.set(rawKey, res.data)
      await Promise.all([ensureDetailMeta(type), loadRelationLabels(type)])
      if (requestId !== detailRequestId) return
      afterDetailLoaded()
    } else {
      detailError.value = res.msg || '读取对象失败'
    }
  } catch (err) {
    if (requestId !== detailRequestId) return
    console.error('读取对象失败:', err)
    detailError.value = '读取对象失败，请检查映射和数据源权限'
  } finally {
    if (requestId === detailRequestId) {
      detailLoading.value = false
      linksLoading.value = false
    }
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
  // 历史按“当前详情所属类型”记录，多级下钻后返回才能回到真实上一跳。
  history.value.push({
    objectType: detail.value.object_type || props.objectType,
    objectKey: detail.value.object_key,
    label: detailTitle.value || detail.value.object_key
  })
  void loadDetail(key)
}

const backToObject = async () => {
  const previous = history.value.pop()
  if (!previous) return
  // loadDetail 内部会自增请求序号：返回后迟到的关联对象响应不会再覆盖详情。
  await loadDetail(previous.objectKey, previous.objectType)
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

const initialize = async () => {
  if (!props.objectType) return
  // 切换对象类型时作废上一类型仍在途的列表/详情请求。
  listRequestId += 1
  detailRequestId += 1
  keyword.value = ''
  page.value = 1
  filters.value = []
  sortProperty.value = ''
  totalCount.value = 0
  detailMeta.value = null
  exploreNodes.value = {}
  exploreEdges.value = {}
  exploreNodeOffsets.value = {}
  exploreSelectedKey.value = ''
  exploreLoadingKey.value = ''
  resetToList()
  seedInitialMetadata()
  seedInitialRelationLabels()
  await loadMeta(props.objectType)
  await loadList()
}

watch(() => props.objectType, () => void initialize())
onMounted(() => void initialize())
onBeforeUnmount(() => stopGraphDetailResize())
</script>

<template>
  <div class="flex h-[640px] min-h-0 gap-4">
    <section class="flex w-[360px] shrink-0 flex-col border-r border-slate-100 pr-3">
      <div class="mb-2 flex items-center gap-2">
        <div v-if="memberOptions.length > 1" class="w-28 shrink-0">
          <el-select
            v-model="selectedMember"
            size="small"
            class="w-full"
            @change="onMemberChange"
          >
            <el-option v-for="member in memberOptions" :key="member" :label="member" :value="member" />
          </el-select>
        </div>
        <div class="min-w-0 flex-1">
          <el-input
            v-model="keyword"
            size="small"
            clearable
            placeholder="搜索标题键或主键"
            class="w-full"
            @keyup.enter="loadList(true)"
            @clear="loadList(true)"
          />
        </div>
        <el-button size="small" type="primary" class="shrink-0" @click="loadList(true)">搜索</el-button>
      </div>
      <div class="mb-2 flex items-center gap-1">
        <el-popover placement="bottom-start" :width="440" trigger="click" popper-class="object-instance-filter-popper">
          <template #reference>
            <el-button size="small">
              <i class="fa-solid fa-filter mr-1 text-[10px]"></i>筛选
              <span v-if="filters.length > 0" class="ml-1 text-[10px] text-blue-600">{{ filters.length }}</span>
            </el-button>
          </template>
          <div class="flex flex-col gap-1.5">
            <div v-for="(item, index) in filters" :key="index" class="flex items-center gap-1">
              <el-select v-model="item.property" size="small" class="!w-28" :teleported="false">
                <el-option
                  v-for="prop in filterProperties"
                  :key="prop.name"
                  :label="prop.label"
                  :value="prop.name"
                />
              </el-select>
              <el-select v-model="item.operator" size="small" class="!w-24" :teleported="false">
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
        <el-alert
          v-if="detail.warnings?.length"
          :title="detail.warnings.join('；')"
          type="warning"
          :closable="false"
          show-icon
          class="mb-2"
        />
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <el-button v-if="history.length > 0" link size="small" class="shrink-0" @click="backToObject">返回</el-button>
          <span class="min-w-0 truncate font-mono text-xs text-slate-700">{{ detail.object_key }}</span>
          <span v-if="detailTitle" class="truncate text-xs text-slate-500">{{ detailTitle }}</span>
          <el-radio-group v-model="detailMode" size="small" class="ml-auto shrink-0" @change="onDetailModeChange">
            <el-radio-button value="table">属性</el-radio-button>
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
          <el-table-column label="类型 / 口径" min-width="190">
            <template #default="{ row }">
              <el-tooltip :content="row.derivationText || row.valueType || '未标注'" placement="top">
                <span class="inline-flex max-w-full items-center gap-1 overflow-hidden">
                  <el-tag v-if="row.derivation" size="small" type="warning" effect="plain">派生</el-tag>
                  <span class="truncate text-[11px] text-slate-500">{{ row.derivationText || row.valueType || '-' }}</span>
                </span>
              </el-tooltip>
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
        <div
          v-else
          class="rounded border border-slate-200 bg-white p-2"
          :class="graphFullscreen ? 'fixed inset-0 z-[var(--z-fullscreen-layer)] flex flex-col bg-white p-4' : ''"
          @mousemove="onExploreNodeDrag"
          @mouseup="endExploreNodeDrag"
          @mouseleave="endExploreNodeDrag"
        >
          <el-alert
            v-if="graphFallbackNotice"
            :title="graphFallbackNotice"
            type="warning"
            :closable="false"
            show-icon
            class="mb-2"
          />
          <el-alert
            v-if="graphDegradedReason"
            :title="graphDegradedReason"
            type="warning"
            :closable="false"
            show-icon
            class="mb-2"
          />
          <el-alert
            v-if="graphExpansionNotice"
            :title="graphExpansionNotice"
            type="warning"
            :closable="false"
            show-icon
            class="mb-2"
          />
          <div class="mb-1 flex flex-wrap items-center gap-2">
            <span class="text-xs font-medium text-slate-700">图探索</span>
            <span class="text-[10px] text-slate-400">力导向布局</span>
            <span v-if="graphLoading" class="flex items-center gap-1 text-[10px] text-blue-500">
              <i class="fa-solid fa-spinner fa-spin"></i>
              正在加载关系图…
            </span>
            <span class="text-[11px] text-slate-400">
              已加入 {{ Object.keys(exploreNodes).length }} 个对象 · {{ Object.keys(exploreEdges).length }} 条关系
            </span>
            <span
              v-if="graphAsOf"
              class="text-[10px] text-slate-400"
              :title="`图数据截至 ${graphAsOf}`"
            >
              图数据截至 {{ graphAsOf.replace('T', ' ').replace('Z', ' UTC') }}
            </span>
            <span v-if="exploreSelectedKey" class="font-mono text-[11px] text-slate-500">{{ exploreSelectedKey }}</span>
            <div class="ml-auto flex items-center gap-1">
              <el-button size="small" @click="graphFullscreen = !graphFullscreen">
                <i :class="['fa-solid mr-1 text-[10px]', graphFullscreen ? 'fa-compress' : 'fa-expand']"></i>
                {{ graphFullscreen ? '退出全屏' : '全屏' }}
              </el-button>
              <el-button v-if="exploreSelectedKey" size="small" @click="viewNodeDetail(exploreSelectedKey)">查看详情</el-button>
              <el-button v-if="exploreSelectedKey" size="small" @click="expandNodeOneHop(exploreSelectedKey)">展开一跳</el-button>
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
                {{ detail ? '当前对象暂无关联关系，或图同步尚未覆盖' : '从左侧选择一个对象，或点击「重置为当前对象」开始' }}
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
                  v-for="node in orderedGraphNodes"
                  :key="node.key"
                  class="cursor-move"
                  @mousedown="startExploreNodeDrag($event, node)"
                  @click="onExploreNodeClick(node.key)"
                >
                  <title>{{ node.key }}</title>
                  <rect
                    :x="node.x - 84"
                    :y="node.y - 20"
                    width="168"
                    height="40"
                    rx="5"
                    :fill="node.key === exploreSelectedKey ? '#eff6ff' : '#ffffff'"
                    :stroke="node.key === exploreSelectedKey ? '#3b82f6' : graphNewKeys.includes(node.key) ? '#f59e0b' : '#dbeafe'"
                    :stroke-width="node.key === exploreSelectedKey ? 2 : graphNewKeys.includes(node.key) ? 1.8 : 1.2"
                  />
                  <text
                    :x="node.key === exploreSelectedKey ? node.x - 16 : node.x"
                    :y="node.y - 5"
                    text-anchor="middle"
                    class="fill-slate-400"
                    style="font-size: 8px; font-weight: 500"
                  >
                    {{ objectTypeDisplayName(node.objectType) }}
                  </text>
                  <text
                    :x="node.key === exploreSelectedKey ? node.x - 16 : node.x"
                    :y="node.y + 9"
                    text-anchor="middle"
                    :class="node.key === exploreSelectedKey ? 'fill-blue-700' : 'fill-slate-700'"
                    style="font-size: 10px; font-weight: 500"
                  >
                    {{ node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label }}
                  </text>
                  <foreignObject
                    v-if="node.key === exploreSelectedKey"
                    :x="node.x + 38"
                    :y="node.y - 18"
                    width="44"
                    height="20"
                  >
                    <div xmlns="http://www.w3.org/1999/xhtml" class="flex h-full items-center justify-center gap-0.5">
                      <button
                        type="button"
                        title="查看详情"
                        class="flex h-4 w-4 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 hover:border-blue-300 hover:text-blue-600"
                        @mousedown.stop.prevent
                        @click.stop="viewNodeDetail(node.key)"
                      >
                        <i class="fa-solid fa-eye text-[8px]"></i>
                      </button>
                      <button
                        type="button"
                        title="展开一跳"
                        class="flex h-4 w-4 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 hover:border-blue-300 hover:text-blue-600"
                        @mousedown.stop.prevent
                        @click.stop="expandNodeOneHop(node.key)"
                      >
                        <i class="fa-solid fa-arrow-up-right-from-square text-[8px]"></i>
                      </button>
                    </div>
                  </foreignObject>
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
            <div
              v-if="detail && showGraphProperties"
              class="group relative w-2 shrink-0 cursor-col-resize self-stretch select-none"
              title="左右拖动调整图与详情区域宽度；双击恢复默认"
              @mousedown="startGraphDetailResize"
              @dblclick="resetGraphDetailWidth"
            >
              <div class="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-slate-200 transition-colors group-hover:bg-blue-400" />
              <div class="absolute left-1/2 top-1/2 flex h-10 w-1.5 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center gap-0.5 rounded-full bg-slate-300 shadow-sm transition-colors group-hover:bg-blue-500">
                <span class="h-0.5 w-0.5 rounded-full bg-white" />
                <span class="h-0.5 w-0.5 rounded-full bg-white" />
                <span class="h-0.5 w-0.5 rounded-full bg-white" />
              </div>
            </div>
            <aside
              v-if="detail && showGraphProperties"
              class="flex shrink-0 flex-col overflow-hidden rounded border border-slate-100 bg-slate-50/50 p-2"
              :class="graphFullscreen ? 'h-full' : 'max-h-[420px]'"
              :style="{ width: `${graphDetailWidth}px`, maxWidth: '66%' }"
            >
              <div class="mb-1 flex items-center gap-1">
                <span class="text-[11px] font-medium text-slate-700">选中对象</span>
                <span class="ml-auto max-w-[60%] truncate font-mono text-[10px] text-slate-400" :title="detail.object_key">{{ detail.object_key }}</span>
              </div>
              <div v-if="detailTitle" class="mb-2 truncate text-xs font-medium text-slate-700" :title="detailTitle">{{ detailTitle }}</div>
              <el-table
                :data="detailPropertyRows"
                size="small"
                border
                height="100%"
                empty-text="暂无属性"
                class="min-h-0 flex-1"
              >
                <el-table-column label="属性" min-width="120" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span class="font-mono text-[10px] text-slate-600">{{ row.name }}</span>
                    <span v-if="row.label" class="ml-1 text-[10px] text-slate-400">{{ row.label }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="值" min-width="140" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span class="break-all text-[11px] text-slate-700">{{ previewValueText(row.value) }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </aside>
          </div>
          <div class="mt-1 text-[10px] text-slate-400">
            先点击节点选中；选中后节点下方出现「查看详情」和「展开一跳」，不会自动加载或扩图。
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
