<script setup lang="ts">
/**
 * 后台管理 · 本体对象类型面板（左树右详情）。
 * 左侧「本体（模块根）→ 本体 → 对象类型/关系类型 → 属性/映射」完全树形导航（对象类型子节点懒加载），
 * 右侧按选中节点类型渲染只读详情卡；全部写操作（新建/编辑/状态/删除）收敛到右键菜单。
 * 归属口径：属性/映射是对象自有物，挂对象下；关系是对象之间的边（双规范对齐 Palantir link type / Ossie
 * top-level relationships），挂本体下（两端同本体一条、跨本体两端各一条带「跨本体」标记），不放对象里。
 * 本体=纯语义容器，不绑数据源组（对象类型取数后端由各自映射决定，可跨组，对齐 Palantir「本体不绑存储」）；
 * 本体子节点为常驻「对象类型/关系类型」分组锚点。
 */
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { TreeInstance } from 'element-plus'
import AdminPanelToolbar from '../AdminPanelToolbar.vue'
import {
  changeAdminOntologyObjectTypeOntology,
  deleteAdminOntology,
  deleteAdminOntologyObjectType,
  getAdminOntologies,
  getAdminOntologyObjectType,
  getAdminOntologyActionTypes,
  getAdminOntologyObjectTypes,
  getAdminOntologyRelationTypes,
  readAdminOntologyObject
} from '../../api'
import type {
  OntologyItem,
  OntologyMappingItem,
  OntologyObjectInstance,
  OntologyObjectTypeItem,
  OntologyObjectTypeStatus,
  OntologyPropertyItem,
  OntologyRelationTypeItem,
  OntologyActionTypeItem
} from '../../types'
import ActionTypeAdminPanel from './ActionTypeAdminPanel.vue'
import OntologyConsistencyDialog from './OntologyConsistencyDialog.vue'
import OntologyForm from './OntologyForm.vue'
import OntologyObjectForm from './OntologyObjectForm.vue'
import OntologyRelationForm from './OntologyRelationForm.vue'
import OntologyStatusDialog from './OntologyStatusDialog.vue'
import { mappingIdentityText, ONTOLOGY_MULTIPLICITY_META, ONTOLOGY_RENDER_HINTS, ONTOLOGY_STATUS_META } from './ontologyMeta'
import { useSemanticConfigOptions } from './useSemanticConfigOptions'

const emit = defineEmits<{ changed: [] }>()

// 语义模型选项（映射行回显「模型名（组名）」）
const { load: loadSemanticOptions, optionOf } = useSemanticConfigOptions()

// ---------- 本体（对象类型容器，纯语义容器不绑数据源组） ----------

const ontologies = ref<OntologyItem[]>([])
const ontologyOf = (ontologyId: number | null): OntologyItem | undefined =>
  ontologies.value.find(item => item.id === ontologyId)
const ontologyLabelOf = (ontologyId: number | null): string => {
  const ontology = ontologyOf(ontologyId)
  if (!ontology) return ''
  return ontology.display_name || ontology.ontology_name
}

const loadOntologies = async () => {
  try {
    const res = await getAdminOntologies()
    if (res.status === 'success') {
      ontologies.value = res.data?.ontologies || []
    } else {
      ElMessage.error(res.msg || '获取本体列表失败')
    }
  } catch (err) {
    console.error('获取本体列表失败:', err)
  }
}

onMounted(() => {
  // 宿主用 v-show 承载本面板且切换页签时只调 AdminPanel.ensureLoaded；自加载保证任何进入路径都有数据
  void ensureLoaded()
  void loadSemanticOptions()
  void loadOntologies()
  void loadRelationSources()
  document.addEventListener('click', closeCtxMenu)
})

onUnmounted(() => {
  document.removeEventListener('click', closeCtxMenu)
})

const mappingLabel = (mapping: OntologyMappingItem): string =>
  optionOf(mapping.semantic_config_id)?.label || mappingIdentityText(mapping)

const loading = ref(false)
const items = ref<OntologyObjectTypeItem[]>([])
const keyword = ref('')
const filterStatus = ref<OntologyObjectTypeStatus | undefined>(undefined)
const filterOntologyId = ref<number | undefined>(undefined)
const hasLoaded = ref(false)

// 关系分组数据源（不受工具栏筛选影响）：全量对象用于「对象→本体」映射，全量关系用于本体关系组
const allObjects = ref<OntologyObjectTypeItem[]>([])
const relation_types = ref<OntologyRelationTypeItem[]>([])
const action_types = ref<OntologyActionTypeItem[]>([])

const loadRelationSources = async () => {
  try {
    const [objectRes, relationRes, actionRes] = await Promise.all([
      getAdminOntologyObjectTypes({}),
      getAdminOntologyRelationTypes({}),
      getAdminOntologyActionTypes()
    ])
    if (objectRes.status === 'success') allObjects.value = objectRes.data?.object_types || []
    if (relationRes.status === 'success') relation_types.value = relationRes.data?.relation_types || []
    if (actionRes.status === 'success') action_types.value = actionRes.data?.action_types || []
  } catch (err) {
    console.error('获取本体关系分组数据失败:', err)
  }
}

// 对象 API 名 → 本体主键（null=未归本体）；关系按两端对象的本体归组
const objectTypeOntologyMap = computed(() => {
  const map = new Map<string, number | null>()
  for (const item of allObjects.value) map.set(item.object_type_name, item.ontology_id)
  return map
})

// 对象 API 名 → 展示名（关系的树行/详情卡直接显示两端对象，避免角色短语造成歧义）
const objectDisplayMap = computed(() => {
  const map = new Map<string, string>()
  for (const item of allObjects.value) map.set(item.object_type_name, item.display_name || item.object_type_name)
  return map
})
const objectTypeDisplayOf = (objectTypeName: string | undefined) =>
  (objectTypeName && objectDisplayMap.value.get(objectTypeName)) || objectTypeName || '-'

// 域筛选项已由本体清单接口提供（ontologies ref），不再从对象行提取

// ---------- 树节点类型：本体（模块根）→ 本体 → 对象类型/关系类型 → 属性/映射 ----------

const UNGROUPED_ONTOLOGY = '未归本体'
// 常驻根节点：展示层锚点（右键新增本体），非数据实体，DB 无层级字段
const ROOT_ROW_KEY = 'ontology-root'
const PLACEHOLDER_GROUP_META = [
  { key: 'shared_properties', label: '共享属性', icon: 'fa-share-nodes' },
  { key: 'interfaces', label: '接口', icon: 'fa-code-branch' },
  { key: 'functions', label: '函数', icon: 'fa-calculator' }
] as const
const ontologyBucketKey = (ontologyId: number | null) => (ontologyId == null ? '__ungrouped__' : String(ontologyId))

interface RootNode {
  row_key: string
  kind: 'root'
  label: string
  children: TreeRow[]
}
interface OntologyNode {
  row_key: string
  kind: 'ontology'
  /** 本体主键；未归本体虚拟节点为 null */
  ontology_id: number | null
  ontology_name: string
  ontology_label: string
  description: string
  is_ungrouped: boolean
  /** 本体下对象数（本体行直接展示，不必展开「对象」分组） */
  object_type_count: number
  children: TreeRow[]
}
interface ObjectNodeData {
  row_key: string
  kind: 'object_type'
  object_type_name: string
  display_name: string
  status: OntologyObjectTypeStatus
  ontology_id: number | null
  ontology_label: string
  description: string
  mappings_count: number
  update_time: string
  update_by_name: string
  is_leaf: false
}
interface PropertyNode {
  row_key: string
  kind: 'property'
  property: OntologyPropertyItem
  is_leaf: true
}
interface RelationNode {
  row_key: string
  kind: 'relation'
  relation: OntologyRelationTypeItem
  /** 两端对象分属不同本体（跨域关系在两端本体各显示一次，带「跨域」标记） */
  is_cross_ontology: boolean
  is_leaf: true
}
interface ActionTypeNode {
  row_key: string
  kind: 'action_type'
  action_type: OntologyActionTypeItem
  is_leaf: true
}
/** 对象子树分组节点（属性/映射）：UI 导航原语，非本体层级 */
interface GroupNode {
  row_key: string
  kind: 'group'
  group: 'property' | 'mapping'
  label: string
  object_type_name: string
  children: TreeRow[]
  is_leaf: false
}
/** 本体子节点分组（对象类型/关系类型）：常驻右键锚点——对象类型组管新建对象类型，关系类型组管新建关系类型 */
interface OntologyGroupNode {
  row_key: string
  kind: 'ontology-group'
  group: 'object_types' | 'relation_types' | 'action_types' | 'shared_properties' | 'interfaces' | 'functions'
  label: string
  ontology_id: number | null
  children: TreeRow[]
  is_leaf: false
  /** 对齐 Palantir 的预留资源位：规划中，不伪造数据入口 */
  placeholder?: boolean
}
interface MappingNode {
  row_key: string
  kind: 'mapping'
  mapping: OntologyMappingItem
  is_leaf: true
}
interface EmptyNode {
  row_key: string
  kind: 'empty'
  label: string
  is_leaf: true
}
type TreeRow = RootNode | OntologyNode | OntologyGroupNode | ObjectNodeData | GroupNode | PropertyNode | RelationNode | ActionTypeNode | MappingNode | EmptyNode

// 数据版本号：编入对象节点 key，列表刷新/保存后懒加载缓存整体失效，防止展示脏数据
const dataVersion = ref(0)

const toObjectNode = (item: OntologyObjectTypeItem): ObjectNodeData => ({
  row_key: `object:${item.object_type_name}:v${dataVersion.value}`,
  kind: 'object_type',
  object_type_name: item.object_type_name,
  display_name: item.display_name,
  status: item.status,
  ontology_id: item.ontology_id,
  ontology_label: ontologyLabelOf(item.ontology_id),
  description: item.description,
  mappings_count: item.mappings_count ?? (item.mappings || []).length,
  update_time: item.update_time || '',
  update_by_name: item.update_by_name || '',
  is_leaf: false
})

// 关系按两端对象的本体归组：同域一条；跨域在两端本体各显示一条（同一关系、不同出现位置）
const relationTypeNodesByOntology = computed(() => {
  const map = new Map<number | null, RelationNode[]>()
  for (const rel of relation_types.value) {
    const fromOntology = objectTypeOntologyMap.value.get(rel.from_object_type_name || '')
    const toOntology = objectTypeOntologyMap.value.get(rel.to_object_type_name || '')
    if (fromOntology === undefined && toOntology === undefined) continue
    const isCrossOntology = fromOntology !== undefined && toOntology !== undefined && fromOntology !== toOntology
    const buckets = new Set<number | null>()
    if (fromOntology !== undefined) buckets.add(fromOntology)
    if (toOntology !== undefined) buckets.add(toOntology)
    for (const bucket of buckets) {
      if (!map.has(bucket)) map.set(bucket, [])
      map.get(bucket)!.push({
        row_key: `rel:${ontologyBucketKey(bucket)}:${rel.relation_type_name}`,
        kind: 'relation',
        relation: rel,
        is_cross_ontology: isCrossOntology,
        is_leaf: true
      })
    }
  }
  return map
})

// 动作类型按目标对象类型所属本体归组（一个动作只改一个对象类型，不跨本体重复）
const actionTypeNodesByOntology = computed(() => {
  const map = new Map<number | null, ActionTypeNode[]>()
  for (const action of action_types.value) {
    const targetOntology = objectTypeOntologyMap.value.get(action.object_type_name || '')
    if (targetOntology === undefined) continue
    if (!map.has(targetOntology)) map.set(targetOntology, [])
    map.get(targetOntology)!.push({
      row_key: `action:${ontologyBucketKey(targetOntology)}:${action.action_type_name}`,
      kind: 'action_type',
      action_type: action,
      is_leaf: true
    })
  }
  return map
})

// 本体子节点=常驻「对象类型/关系类型/动作类型」三个分组锚点（空组保留占位，右键入口始终可用）
const buildOntologyChildren = (ontologyId: number | null, ontologyObjects: OntologyObjectTypeItem[]): TreeRow[] => {
  const bucket = ontologyBucketKey(ontologyId)
  const objectTypeNodes: TreeRow[] = ontologyObjects.map(toObjectNode)
  if (objectTypeNodes.length === 0) {
    objectTypeNodes.push({ row_key: `ontology-group:${bucket}:object_types:empty`, kind: 'empty', label: '暂无对象类型', is_leaf: true })
  }
  const relationTypeNodes: TreeRow[] = relationTypeNodesByOntology.value.get(ontologyId) || []
  if (relationTypeNodes.length === 0) {
    relationTypeNodes.push({ row_key: `ontology-group:${bucket}:relation_types:empty`, kind: 'empty', label: '暂无关系类型', is_leaf: true })
  }
  const actionTypeNodes: TreeRow[] = actionTypeNodesByOntology.value.get(ontologyId) || []
  if (actionTypeNodes.length === 0) {
    actionTypeNodes.push({ row_key: `ontology-group:${bucket}:action_types:empty`, kind: 'empty', label: '暂无动作类型', is_leaf: true })
  }
  return [
    {
      row_key: `ontology-group:${bucket}:object_types`,
      kind: 'ontology-group',
      group: 'object_types',
      label: '对象类型',
      ontology_id: ontologyId,
      children: objectTypeNodes,
      is_leaf: false
    },
    {
      row_key: `ontology-group:${bucket}:relation_types`,
      kind: 'ontology-group',
      group: 'relation_types',
      label: '关系类型',
      ontology_id: ontologyId,
      children: relationTypeNodes,
      is_leaf: false
    },
    {
      row_key: `ontology-group:${bucket}:action_types`,
      kind: 'ontology-group',
      group: 'action_types',
      label: '动作类型',
      ontology_id: ontologyId,
      children: actionTypeNodes,
      is_leaf: false
    },
    ...PLACEHOLDER_GROUP_META.map(meta => ({
      row_key: `ontology-group:${bucket}:${meta.key}`,
      kind: 'ontology-group' as const,
      group: meta.key,
      label: meta.label,
      ontology_id: ontologyId,
      children: [],
      is_leaf: false as const,
      placeholder: true
    }))
  ]
}

const treeData = computed<RootNode[]>(() => {
  // 对象按 ontology_id 分组挂到本体；未挂本体的进「未归本体」常驻兜底节点
  const grouped = new Map<number, OntologyObjectTypeItem[]>()
  const ungrouped: OntologyObjectTypeItem[] = []
  for (const item of items.value) {
    if (item.ontology_id == null) ungrouped.push(item)
    else {
      if (!grouped.has(item.ontology_id)) grouped.set(item.ontology_id, [])
      grouped.get(item.ontology_id)!.push(item)
    }
  }
  const ontologyNodes: OntologyNode[] = ontologies.value.map(ontology => ({
    row_key: `ontology:${ontology.ontology_name}`,
    kind: 'ontology',
    ontology_id: ontology.id,
    ontology_name: ontology.ontology_name,
    ontology_label: ontology.display_name || ontology.ontology_name,
    description: ontology.description || '',
    is_ungrouped: false,
    object_type_count: (grouped.get(ontology.id) || []).length,
    children: buildOntologyChildren(ontology.id, grouped.get(ontology.id) || [])
  }))
  ontologyNodes.push({
    row_key: 'ontology:__ungrouped__',
    kind: 'ontology',
    ontology_id: null,
    ontology_name: '',
    ontology_label: UNGROUPED_ONTOLOGY,
    description: '未挂本体的对象：建议补齐本体归属（纯导航归属，不影响取数）。',
    is_ungrouped: true,
    object_type_count: ungrouped.length,
    children: buildOntologyChildren(null, ungrouped)
  })
  // 常驻根节点单顶层：本体全部挂根节点下（展示层锚点，非数据实体）
  return [{ row_key: ROOT_ROW_KEY, kind: 'root', label: '本体', children: ontologyNodes }]
})

// 根/本体/对象分组默认展开（key 稳定；对象节点 key 带版本号，保存/刷新后重新折叠）
// 默认只展开根节点与本体层；对象类型/关系类型分组默认收起（保持树紧凑），展开与否随用户操作
const defaultExpandedKeys = computed(() => [
  ROOT_ROW_KEY,
  ...treeData.value[0]?.children.map(row => row.row_key) || []
])

// ---------- 对象子节点懒加载 + 详情缓存（树子行与右侧详情共用一次取数） ----------

interface ObjectDetailEntry {
  detail: OntologyObjectTypeItem
  fromRelations: OntologyRelationTypeItem[]
  toRelations: OntologyRelationTypeItem[]
}

const treeRef = ref<TreeInstance>()
// key = 对象节点 row_key（含版本号）
const detailStore = ref<Record<string, ObjectDetailEntry>>({})
const objectLoadState = ref<Record<string, 'loading' | 'error' | 'ready'>>({})

const buildChildRows = (objectNode: ObjectNodeData, entry: ObjectDetailEntry): TreeRow[] => {
  // 分组树：属性/映射各成组（带计数），空组不展示；关系不在此处（关系是对象之间的，挂本体下）
  const propertyChildren: TreeRow[] = (entry.detail.properties || []).map(prop => ({
    row_key: `${objectNode.row_key}:prop:${prop.property_name}`,
    kind: 'property',
    property: prop,
    is_leaf: true
  }))
  const mappingChildren: TreeRow[] = (entry.detail.mappings || []).map(mapping => ({
    row_key: `${objectNode.row_key}:mapping:${mappingIdentityText(mapping)}`,
    kind: 'mapping',
    mapping,
    is_leaf: true
  }))
  const rows: TreeRow[] = []
  const pushGroup = (group: GroupNode['group'], label: string, children: TreeRow[]) => {
    if (children.length > 0) {
      rows.push({ row_key: `${objectNode.row_key}:group:${group}`, kind: 'group', group, label, object_type_name: objectNode.object_type_name, children, is_leaf: false })
    }
  }
  pushGroup('property', '属性', propertyChildren)
  pushGroup('mapping', '映射', mappingChildren)
  if (rows.length === 0) {
    rows.push({ row_key: `${objectNode.row_key}:empty`, kind: 'empty', label: '暂无属性与映射', is_leaf: true })
  }
  return rows
}

const fetchObjectEntry = async (objectNode: ObjectNodeData) => {
  const key = objectNode.row_key
  objectLoadState.value = { ...objectLoadState.value, [key]: 'loading' }
  try {
    const [detailRes, fromRes, toRes] = await Promise.all([
      getAdminOntologyObjectType(objectNode.object_type_name),
      getAdminOntologyRelationTypes({ from_object_type: objectNode.object_type_name }),
      getAdminOntologyRelationTypes({ to_object_type: objectNode.object_type_name })
    ])
    if (detailRes.status !== 'success' || !detailRes.data || fromRes.status !== 'success' || toRes.status !== 'success') {
      const msg = detailRes.msg || fromRes.msg || toRes.msg || '获取对象详情失败'
      ElMessage.error(msg)
      objectLoadState.value = { ...objectLoadState.value, [key]: 'error' }
      return
    }
    const entry: ObjectDetailEntry = {
      detail: detailRes.data,
      fromRelations: fromRes.data?.relation_types || [],
      toRelations: toRes.data?.relation_types || []
    }
    detailStore.value = { ...detailStore.value, [key]: entry }
    objectLoadState.value = { ...objectLoadState.value, [key]: 'ready' }
    treeRef.value?.updateKeyChildren?.(key, buildChildRows(objectNode, entry))
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('加载本体对象类型详情失败:', err)
    objectLoadState.value = { ...objectLoadState.value, [key]: 'error' }
  }
}

const ensureObjectData = (objectNode: ObjectNodeData) => {
  const state = objectLoadState.value[objectNode.row_key]
  if (state === 'ready' || state === 'loading') return
  void fetchObjectEntry(objectNode)
}

const retrySelectedObject = () => {
  if (selectedNode.value?.kind === 'object_type') void fetchObjectEntry(selectedNode.value)
}

const openObjectPreview = () => {
  if (selectedNode.value?.kind !== 'object_type') return
  objectPreviewType.value = selectedNode.value.object_type_name
  objectPreviewKey.value = ''
  objectPreviewData.value = null
  objectPreviewError.value = ''
  objectPreviewVisible.value = true
}

const loadObjectPreview = async () => {
  if (!objectPreviewType.value || !objectPreviewKey.value.trim()) {
    objectPreviewError.value = '请输入对象主键值'
    return
  }
  objectPreviewLoading.value = true
  objectPreviewError.value = ''
  try {
    const res = await readAdminOntologyObject(objectPreviewType.value, objectPreviewKey.value.trim())
    if (res.status === 'success') {
      objectPreviewData.value = res.data
    } else {
      objectPreviewError.value = res.msg || '读取对象失败'
    }
  } catch (err) {
    console.error('读取本体对象失败:', err)
    objectPreviewError.value = '读取对象失败，请检查映射、数据源权限或稍后重试'
  } finally {
    objectPreviewLoading.value = false
  }
}

// ---------- 节点选中与树事件 ----------

const treeProps = { label: 'row_key', isLeaf: 'is_leaf' }
const currentNodeKey = ref('')

const selectedNode = ref<TreeRow | null>(null)

const onNodeClick = (data: TreeRow) => {
  // 动作类型是写操作资源：点击直接打开管理弹窗，不占右侧对象详情位
  if (data.kind === 'action_type') {
    void openActionTypeManager('edit', data.action_type)
    return
  }
  // 根/对象子树分组/本体分组/空态占位是导航容器或提示节点，点击不改变右侧详情；
  // 本体节点有独立详情卡（否则点击后右侧空白）。
  if (data.kind === 'root' || data.kind === 'group' || data.kind === 'ontology' || data.kind === 'ontology-group' || data.kind === 'empty') return
  selectedNode.value = data
  currentNodeKey.value = data.row_key
  if (data.kind === 'object_type') ensureObjectData(data)
}

const onNodeExpand = (data: TreeRow) => {
  if (data.kind === 'object_type') ensureObjectData(data)
}

// ---------- 右侧详情派生 ----------

const selectedObject = computed(() => {
  if (selectedNode.value?.kind !== 'object_type') return null
  const node = selectedNode.value
  return {
    node,
    entry: detailStore.value[node.row_key],
    state: objectLoadState.value[node.row_key]
  }
})

const selectedObjectStatusMeta = computed(() => {
  const node = selectedNode.value
  if (!node || node.kind !== 'object_type') return null
  return ONTOLOGY_STATUS_META[node.status as keyof typeof ONTOLOGY_STATUS_META] || null
})

const renderHintLabels = (hints: Record<string, boolean>) =>
  ONTOLOGY_RENDER_HINTS.filter(hint => hints[hint.key]).map(hint => hint.label)

const mappingMappingText = (mapping: OntologyMappingItem) => {
  const entries = Object.entries(mapping.column_mappings || {})
  if (entries.length === 0) return '暂无字段映射'
  return entries.map(([prop, dim]) => `${prop} → ${dim}`).join('，')
}

// ---------- 列表加载与过滤 ----------

const loadObjects = async () => {
  loading.value = true
  try {
    const res = await getAdminOntologyObjectTypes({
      keyword: keyword.value.trim() || undefined,
      status: filterStatus.value || undefined,
      ontology_id: filterOntologyId.value
    })
    if (res.status === 'success') {
      items.value = res.data?.object_types || []
      hasLoaded.value = true
      dataVersion.value += 1
      detailStore.value = {}
      objectLoadState.value = {}
      // 节点 key 内含版本号，详情缓存已整体失效：按 object_type_name 找回选中节点并重拉详情
      restoreObjectSelection()
    } else {
      ElMessage.error(res.msg || '获取本体对象类型列表失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取本体对象类型列表失败:', err)
  } finally {
    loading.value = false
  }
}

const ensureLoaded = async () => {
  if (!hasLoaded.value) await loadObjects()
}

const reload = async () => {
  await loadObjects()
}

const searchObjects = () => {
  void loadObjects()
}

const onFilterChange = () => {
  void loadObjects()
}

// ---------- 编辑与状态（对象详情卡操作） ----------

const formVisible = ref(false)
const formObjectName = ref('')
// 新建对象类型时的预选本体（树右键「新建对象类型」从本体节点发起）
const formDefaultOntologyId = ref<number | null>(null)

const statusDialogVisible = ref(false)
// 一致性健康巡检弹窗开关（工具栏按钮触发）
const consistencyDialogVisible = ref(false)
const objectPreviewVisible = ref(false)
const objectPreviewKey = ref('')
const objectPreviewLoading = ref(false)
const objectPreviewError = ref('')
const objectPreviewData = ref<OntologyObjectInstance | null>(null)
const objectPreviewType = ref('')
const statusTarget = ref<OntologyObjectTypeItem | null>(null)

const openCreate = (ontologyId: number | null = null) => {
  formObjectName.value = ''
  formDefaultOntologyId.value = ontologyId
  formVisible.value = true
}

const openEdit = (objectTypeName: string) => {
  // 右键菜单已置灰，这里兜底防止其他入口（键盘/程序调用）打开 deprecated 对象的编辑表单
  const item =
    items.value.find(candidate => candidate.object_type_name === objectTypeName) ||
    allObjects.value.find(candidate => candidate.object_type_name === objectTypeName)
  if (item?.status === 'deprecated') {
    ElMessage.warning('已废弃对象类型为终态不可编辑；如需继续演进，请基于替代对象类型重建')
    return
  }
  formObjectName.value = objectTypeName
  formVisible.value = true
}

const openStatus = (objectTypeName: string) => {
  const key = Object.keys(detailStore.value).find(candidate => candidate.startsWith(`object:${objectTypeName}:`))
  statusTarget.value =
    (key && detailStore.value[key]?.detail) ||
    items.value.find(item => item.object_type_name === objectTypeName) ||
    allObjects.value.find(item => item.object_type_name === objectTypeName) ||
    null
  statusDialogVisible.value = true
}

const onSaved = async () => {
  await loadObjects()
  void loadRelationSources()
  emit('changed')
}

const removeObject = async (objectTypeName: string, status: OntologyObjectTypeStatus, displayName: string) => {
  // 后端禁止删除 certified 对象；前端先拦一道，给出明确处置路径而不是让请求失败
  if (status === 'certified') {
    ElMessage.warning('已认证（certified）对象禁止删除：请先降级为试验/草稿，或标记为废弃')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除对象类型「${displayName || objectTypeName}」？删除后该类型、其全部属性与参与的关系类型将一并移除（软删）。`,
      '删除对象类型',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntologyObjectType(objectTypeName)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      selectedNode.value = null
      currentNodeKey.value = ''
      await onSaved()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如 certified 禁删），不再重复提示
    console.error('删除本体对象类型失败:', err)
  }
}

defineExpose({ ensureLoaded, reload })

// ---------- 本体管理（弹窗 + 删除） ----------

const ontologyFormVisible = ref(false)
const ontologyFormName = ref('')

const openOntologyCreate = () => {
  ontologyFormName.value = ''
  ontologyFormVisible.value = true
}

const openOntologyEdit = (ontologyName: string) => {
  ontologyFormName.value = ontologyName
  ontologyFormVisible.value = true
}

const onOntologySaved = async () => {
  await loadOntologies()
  emit('changed')
}

const removeOntology = async (ontologyName: string, displayName: string) => {
  try {
    await ElMessageBox.confirm(
      `确定删除本体「${displayName || ontologyName}」？本体下仍挂有对象时将被拒绝，需先移出对象。`,
      '删除本体',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntology(ontologyName)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      if (selectedNode.value?.kind === 'ontology' && selectedNode.value.ontology_name === ontologyName) {
        selectedNode.value = null
        currentNodeKey.value = ''
      }
      await loadOntologies()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如本体下仍有对象），不再重复提示
    console.error('删除本体失败:', err)
  }
}

// ---------- 拖拽换本体（仅对象节点拖入本体节点内部；API 名不可改，归属迁移靠拖拽） ----------

// 只允许对象节点发起拖拽（根/本体/分组等锚点不可拖）
const allowDrag = (draggingNode: { data: TreeRow }) => draggingNode.data.kind === 'object_type'

// 只允许放到本体节点或其「对象」分组内部（inner）；禁止排序（prev/next）与拖到其他节点
const allowDrop = (draggingNode: { data: TreeRow }, dropNode: { data: TreeRow }, type: string) => {
  if (draggingNode.data.kind !== 'object_type' || type !== 'inner') return false
  const target = dropNode.data
  if (target.kind === 'ontology') return true
  return target.kind === 'ontology-group' && target.group === 'object_types'
}

const onNodeDrop = async (draggingNode: { data: TreeRow }, dropNode: { data: TreeRow }, dropType: string) => {
  if (draggingNode.data.kind !== 'object_type' || dropType !== 'inner') return
  const target = dropNode.data
  let targetOntologyId: number | null
  if (target.kind === 'ontology') {
    targetOntologyId = target.is_ungrouped ? null : target.ontology_id
  } else if (target.kind === 'ontology-group' && target.group === 'object_types') {
    targetOntologyId = target.ontology_id
  } else {
    return
  }
  const objectNode = draggingNode.data
  if (objectNode.ontology_id === targetOntologyId) return
  const targetLabel = target.kind === 'ontology' ? target.ontology_label : ontologyLabelOf(targetOntologyId)
  try {
    const res = await changeAdminOntologyObjectTypeOntology(objectNode.object_type_name, targetOntologyId)
    if (res.status === 'success') {
      ElMessage.success(
        targetOntologyId == null
          ? `已移出本体：${objectNode.display_name || objectNode.object_type_name}`
          : `已移动到「${targetLabel}」`
      )
    } else {
      ElMessage.error(res.msg || '移动失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('对象本体归属变更失败:', err)
  } finally {
    // el-tree 已就地移动节点；无论成败都重拉列表纠正视图（对象 key 带版本号，懒加载缓存一并失效）
    await loadObjects()
    void loadRelationSources()
  }
}

// ---------- 右键菜单（手写 fixed 浮层，Teleport to body，层级 --z-floating-tip） ----------

interface ContextMenuItem {
  key: string
  label: string
  icon: string
  danger?: boolean
  // 门禁动作置灰（如 deprecated 对象禁止编辑），仍展示原因避免用户误以为菜单丢了
  disabled?: boolean
  handler: () => void
}

const ctxMenu = ref<{ visible: boolean; x: number; y: number; items: ContextMenuItem[] }>({
  visible: false,
  x: 0,
  y: 0,
  items: []
})

const closeCtxMenu = () => {
  ctxMenu.value.visible = false
}

const openCtxMenu = (event: MouseEvent, node: TreeRow | null) => {
  event.preventDefault()
  const menuItems: ContextMenuItem[] = []
  if (node && node.kind === 'root') {
    // 根节点：只新增本体（对象必须挂在具体本体下，根不直接建对象）
    menuItems.push({ key: 'create-ontology', label: '新增本体', icon: 'fa-plus', handler: () => openOntologyCreate() })
  } else if (node && node.kind === 'ontology' && !node.is_ungrouped) {
    // 本体节点：治理动作（编辑/删除）；新建对象类型/关系类型由本体下的分组锚点承担（容器管新增，不重复挂）
    menuItems.push(
      { key: 'edit-ontology', label: '编辑本体', icon: 'fa-pen', handler: () => openOntologyEdit(node.ontology_name) },
      { key: 'delete-ontology', label: '删除本体', icon: 'fa-trash', danger: true, handler: () => removeOntology(node.ontology_name, node.ontology_label) }
    )
  } else if (node && node.kind === 'object_type') {
    // 对象节点：编辑 / 状态 / 删除。deprecated 为终态不可再编辑（后端口径），菜单置灰
    const deprecated = node.status === 'deprecated'
    menuItems.push(
      {
        key: 'edit-object',
        label: deprecated ? '编辑对象类型（已废弃不可编辑）' : '编辑对象类型',
        icon: 'fa-pen',
        disabled: deprecated,
        handler: () => openEdit(node.object_type_name)
      },
      { key: 'status-object', label: '状态迁移', icon: 'fa-arrow-right-arrow-left', handler: () => openStatus(node.object_type_name) },
      { key: 'delete-object', label: '删除对象类型', icon: 'fa-trash', danger: true, handler: () => removeObject(node.object_type_name, node.status, node.display_name) }
    )
  } else if (node && node.kind === 'ontology-group' && node.group === 'object_types') {
    // 本体的「对象类型」分组：新建对象类型（预选本本体；未归本体分组预选 null）
    menuItems.push({ key: 'create-object', label: '新建对象类型', icon: 'fa-cube', handler: () => openCreate(node.ontology_id) })
  } else if (node && node.kind === 'ontology-group' && node.group === 'relation_types') {
    // 本体的「关系类型」分组：新建关系类型（两端手选，关系类型是对象类型之间的，不再预选单一对象类型）
    menuItems.push({ key: 'create-relation', label: '新建关系类型', icon: 'fa-plus', handler: () => relationFormRef.value?.openCreate() })
  } else if (node && node.kind === 'ontology-group' && node.group === 'action_types') {
    // 本体的「动作类型」分组：新建动作类型（目标对象在弹窗内选择）
    menuItems.push({ key: 'create-action-type', label: '新建动作类型', icon: 'fa-bolt', handler: () => void openActionTypeManager('create') })
  } else if (node && node.kind === 'action_type') {
    // 动作类型节点：编辑 / 试运行 / 执行 / 删除（复用 ActionTypeAdminPanel 单入口）
    menuItems.push(
      { key: 'edit-action-type', label: '编辑动作类型', icon: 'fa-pen', handler: () => void openActionTypeManager('edit', node.action_type) },
      { key: 'test-action-type', label: '试运行', icon: 'fa-vial', handler: () => void openActionTypeManager('test', node.action_type) },
      { key: 'execute-action-type', label: '执行', icon: 'fa-play', handler: () => void openActionTypeManager('execute', node.action_type) },
      { key: 'delete-action-type', label: '删除动作类型', icon: 'fa-trash', danger: true, handler: () => void openActionTypeManager('remove', node.action_type) }
    )
  } else if (node && node.kind === 'relation') {
    // 关系条目节点：编辑 / 状态 / 删除
    menuItems.push(
      { key: 'edit-relation', label: '编辑关系类型', icon: 'fa-pen', handler: () => relationFormRef.value?.openEdit(node.relation) },
      { key: 'status-relation', label: '状态迁移', icon: 'fa-arrow-right-arrow-left', handler: () => relationFormRef.value?.openStatus(node.relation) },
      { key: 'delete-relation', label: '删除关系类型', icon: 'fa-trash', danger: true, handler: () => relationFormRef.value?.remove(node.relation) }
    )
  } else if (!node) {
    // 树空白处（容器级右键 node=null）：等同于根节点层级。
    // 注意兜底只允许 null——未显式处理的节点类型（属性/映射条目、对象子树的属性/映射分组、
    // 空态占位）必须落空，否则「新增本体」等本体动作会泄漏到叶子节点上。
    menuItems.push({ key: 'create-ontology', label: '新增本体', icon: 'fa-plus', handler: () => openOntologyCreate() })
  }
  // 无菜单项的节点（未归本体虚拟节点、对象子树的属性/映射分组）不弹菜单
  if (menuItems.length === 0) return
  ctxMenu.value = { visible: true, x: event.clientX, y: event.clientY, items: menuItems }
}

// ---------- 关系写操作（右键 → OntologyRelationForm） ----------

const relationFormRef = ref<InstanceType<typeof OntologyRelationForm> | null>(null)

// ---------- 动作类型管理（树右键/点击 → 复用 ActionTypeAdminPanel 弹窗） ----------

const actionTypeManagerVisible = ref(false)
const actionTypePanelRef = ref<InstanceType<typeof ActionTypeAdminPanel> | null>(null)

type ActionTypeManagerMode = 'create' | 'edit' | 'test' | 'execute' | 'remove'

const openActionTypeManager = async (mode: ActionTypeManagerMode, item: OntologyActionTypeItem | null = null) => {
  actionTypeManagerVisible.value = true
  await nextTick()
  await actionTypePanelRef.value?.ensureLoaded()
  if (mode === 'create') actionTypePanelRef.value?.openCreate()
  else if (!item) return
  else if (mode === 'edit') void actionTypePanelRef.value?.openEdit(item)
  else if (mode === 'test') actionTypePanelRef.value?.openTest(item)
  else if (mode === 'execute') actionTypePanelRef.value?.openExecute(item)
  else actionTypePanelRef.value?.remove(item)
}

const findObjectNode = (objectTypeName: string): ObjectNodeData | null => {
  for (const root of treeData.value) {
    for (const ontology of root.children) {
      if (ontology.kind !== 'ontology') continue
      for (const group of ontology.children) {
        if (group.kind !== 'ontology-group' || group.group !== 'object_types') continue
        const found = group.children.find(
          (item): item is ObjectNodeData => item.kind === 'object_type' && item.object_type_name === objectTypeName
        )
        if (found) return found
      }
    }
  }
  return null
}

// 列表刷新会整体重建树节点与详情缓存（key 带版本号）：按 object_type_name 重新定位选中对象，
// 否则右侧详情卡查旧 key 拿不到缓存会整块空白；对象被筛选条件排除时清空选中。
const restoreObjectSelection = () => {
  const current = selectedNode.value
  if (!current || current.kind !== 'object_type') return
  const node = findObjectNode(current.object_type_name)
  if (!node) {
    selectedNode.value = null
    currentNodeKey.value = ''
    return
  }
  selectedNode.value = node
  currentNodeKey.value = node.row_key
  ensureObjectData(node)
}

// 关系保存/状态/删除后：重拉关系源（本体关系组就地更新，key 不变不塌缩展开状态）；
// 受影响对象的详情卡「参与关系」同步原地刷新（未展开的对象在展开时自然取新数据）
const onRelationSaved = (objectTypeNames: string[]) => {
  void loadRelationSources()
  for (const name of objectTypeNames) {
    if (!name) continue
    const objectNode = findObjectNode(name)
    if (objectNode && objectLoadState.value[objectNode.row_key] === 'ready') void fetchObjectEntry(objectNode)
  }
  emit('changed')
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col overflow-hidden">
    <!-- 工具栏 -->
    <AdminPanelToolbar>
      <el-select
        v-model="filterStatus"
        size="small"
        placeholder="全部状态"
        clearable
        :empty-values="[null, undefined]"
        class="!w-36"
        @change="onFilterChange"
      >
        <el-option v-for="(meta, key) in ONTOLOGY_STATUS_META" :key="key" :label="meta.label" :value="key" />
      </el-select>
      <el-select
        v-model="filterOntologyId"
        size="small"
        placeholder="全部本体"
        clearable
        filterable
        :empty-values="[null, undefined]"
        class="!w-44"
        @change="onFilterChange"
      >
        <el-option
          v-for="ontology in ontologies"
          :key="ontology.id"
          :label="ontology.display_name || ontology.ontology_name"
          :value="ontology.id"
        />
      </el-select>
      <el-input
        v-model="keyword"
        size="small"
        placeholder="搜索对象名/展示名/描述"
        clearable
        class="!w-56"
        @keyup.enter="searchObjects"
        @clear="searchObjects"
      />
      <el-button size="small" @click="searchObjects">搜索</el-button>
      <el-button size="small" :loading="loading" @click="loadObjects">
        <i class="fa-solid fa-rotate-right" style="margin-right: 4px; font-size: 10px;"></i>刷新
      </el-button>
      <el-button size="small" @click="consistencyDialogVisible = true">
        <i class="fa-solid fa-stethoscope" style="margin-right: 4px; font-size: 10px;"></i>一致性检查
      </el-button>
      <el-button size="small" :disabled="selectedNode?.kind !== 'object_type'" @click="openObjectPreview">
        <i class="fa-solid fa-eye" style="margin-right: 4px; font-size: 10px;"></i>读取对象
      </el-button>
    </AdminPanelToolbar>

    <!-- 左树右详情 -->
    <div class="flex min-h-0 flex-1 overflow-hidden">
      <!-- 左：完全树形导航（空白/节点右键菜单） -->
      <div class="flex min-h-0 w-[340px] shrink-0 flex-col border-r border-slate-100 px-2 pb-2" @contextmenu="openCtxMenu($event, null)">
        <div class="min-h-0 flex-1 overflow-y-auto pt-1">
          <el-tree
            ref="treeRef"
            :data="treeData"
            node-key="row_key"
            :props="treeProps"
            :expand-on-click-node="false"
            :default-expanded-keys="defaultExpandedKeys"
            :current-node-key="currentNodeKey"
            highlight-current
            empty-text="暂无本体，右键本体可新增"
            draggable
            :allow-drag="allowDrag"
            :allow-drop="allowDrop"
            @node-click="onNodeClick"
            @node-expand="onNodeExpand"
            @node-drop="onNodeDrop"
            @node-contextmenu="(event: MouseEvent, data: TreeRow) => openCtxMenu(event, data)"
          >
            <template #default="{ data }">
              <!-- 本体节点（常驻，右键新增本体） -->
              <span v-if="data.kind === 'root'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-sitemap text-[11px] text-slate-500"></i>
                <span class="text-xs font-semibold text-slate-800">{{ data.label }}</span>
                <span class="text-[11px] text-slate-400">{{ data.children.filter((item: TreeRow) => item.kind === 'ontology').length }} 个本体</span>
              </span>
              <!-- 本体节点 -->
              <span v-else-if="data.kind === 'ontology'" class="flex items-center gap-1.5 py-0.5">
                <i :class="['fa-solid text-[11px]', data.is_ungrouped ? 'fa-box-open text-slate-300' : 'fa-layer-group text-amber-500']"></i>
                <span class="text-xs font-medium text-slate-700">{{ data.ontology_label }}</span>
                <span class="text-[11px] text-slate-400">{{ data.object_type_count }} 个对象类型</span>
              </span>
              <!-- 本体分组节点（对象类型/关系类型，常驻锚点） -->
              <span
                v-else-if="data.kind === 'ontology-group'"
                class="flex items-center gap-1.5 py-0.5"
                :class="data.placeholder ? 'cursor-not-allowed opacity-50' : ''"
              >
                <i
                  :class="[
                    'fa-solid text-[10px]',
                    data.group === 'object_types'
                      ? 'fa-cube text-blue-400'
                      : data.group === 'relation_types'
                        ? 'fa-circle-nodes text-violet-400'
                        : data.group === 'action_types'
                          ? 'fa-bolt text-amber-500'
                          : data.group === 'shared_properties'
                            ? 'fa-share-nodes text-slate-400'
                            : data.group === 'interfaces'
                              ? 'fa-code-branch text-slate-400'
                              : 'fa-calculator text-slate-400'
                  ]"
                ></i>
                <span class="text-xs" :class="data.placeholder ? 'text-slate-400' : 'text-slate-600'">{{ data.label }}</span>
                <span v-if="!data.placeholder" class="text-[11px] text-slate-400">{{ data.children[0]?.kind === 'empty' ? 0 : data.children.length }}</span>
              </span>
              <!-- 对象节点 -->
              <span v-else-if="data.kind === 'object_type'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-cube text-[11px] text-blue-500"></i>
                <span class="font-mono text-xs text-slate-800">{{ data.object_type_name }}</span>
                <span v-if="data.display_name" class="text-[11px] text-slate-500">{{ data.display_name }}</span>
                <el-tag
                  size="small"
                  :type="ONTOLOGY_STATUS_META[data.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'"
                  effect="plain"
                >
                  {{ ONTOLOGY_STATUS_META[data.status as keyof typeof ONTOLOGY_STATUS_META]?.label || data.status }}
                </el-tag>
              </span>
              <!-- 动作类型节点 -->
              <span v-else-if="data.kind === 'action_type'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-bolt text-[10px] text-amber-500"></i>
                <span class="truncate font-mono text-xs text-slate-800" :title="data.action_type.action_type_name">{{ data.action_type.action_type_name }}</span>
                <el-tag size="small" type="warning" effect="plain">{{ data.action_type.status }}</el-tag>
              </span>
              <!-- 分组节点（属性/映射，带计数） -->
              <span v-else-if="data.kind === 'group'" class="flex items-center gap-1.5 py-0.5">
                <i
                  :class="[
                    'fa-solid text-[10px]',
                    data.group === 'property' ? 'fa-list text-sky-400' : 'fa-link text-teal-400'
                  ]"
                ></i>
                <span class="text-xs text-slate-600">{{ data.label }}</span>
                <span class="text-[11px] text-slate-400">{{ data.children.length }}</span>
              </span>
              <!-- 属性节点 -->
              <span v-else-if="data.kind === 'property'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-tag text-[10px] text-sky-500"></i>
                <span class="font-mono text-xs text-slate-700">{{ data.property.property_name }}</span>
                <span class="text-[11px] text-slate-400">{{ data.property.value_type }}</span>
                <el-tag v-if="data.property.is_identifier" size="small" type="warning" effect="plain">标识</el-tag>
                <el-tag v-if="data.property.is_title" size="small" type="success" effect="plain">标题</el-tag>
              </span>
              <!-- 关系节点（挂本体下，直接显示两端对象；角色短语/详情在右侧卡片） -->
              <span v-else-if="data.kind === 'relation'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-circle-nodes text-[10px] text-violet-500"></i>
                <span class="truncate text-xs text-slate-700" :title="data.relation.relation_type_name">
                  {{ objectTypeDisplayOf(data.relation.from_object_type_name) }}
                  <i class="fa-solid fa-arrow-right-long mx-0.5 text-[9px] text-slate-300"></i>
                  {{ objectTypeDisplayOf(data.relation.to_object_type_name) }}
                </span>
                <el-tag v-if="data.is_cross_ontology" size="small" type="warning" effect="plain">跨域</el-tag>
              </span>
              <!-- 映射节点 -->
              <span v-else-if="data.kind === 'mapping'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-link text-[10px] text-teal-500"></i>
                <span class="text-xs text-slate-700">{{ mappingLabel(data.mapping) }}</span>
              </span>
              <!-- 占位节点 -->
              <span v-else class="text-xs text-slate-400">{{ data.label }}</span>
            </template>
          </el-tree>
        </div>
      </div>

      <!-- 右：选中节点详情 -->
      <div class="min-h-0 min-w-0 flex-1 overflow-y-auto px-5 py-3">
        <!-- 空态 -->
        <div v-if="!selectedNode" class="flex h-full items-center justify-center">
          <div class="text-center text-xs text-slate-400">
            <i class="fa-solid fa-sitemap mb-2 block text-2xl text-slate-300"></i>
            在左侧选择对象类型、属性、关系类型、动作类型或映射查看详情
            <div class="mt-1 text-[11px] text-slate-300">左侧树支持右键：本体新增本体，本体的对象类型/关系类型/动作类型分组可新建</div>
          </div>
        </div>

        <!-- 对象详情卡 -->
        <template v-else-if="selectedNode.kind === 'object_type' && selectedObject">
          <div v-if="selectedObject.state === 'loading'" class="py-8 text-center text-xs text-slate-400">
            <i class="fa-solid fa-spinner fa-spin mr-1.5"></i>加载对象类型详情中…
          </div>
          <div v-else-if="selectedObject.state === 'error'" class="py-8 text-center">
            <div class="mb-2 text-xs text-slate-500">对象类型详情加载失败</div>
            <el-button size="small" type="primary" @click="retrySelectedObject">重试</el-button>
          </div>
          <template v-else-if="selectedObject.entry">
            <div class="mb-3 flex items-center gap-2">
              <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.object_type_name }}</span>
              <span v-if="selectedNode.display_name" class="text-xs text-slate-500">{{ selectedNode.display_name }}</span>
              <el-tooltip v-if="selectedObjectStatusMeta" :content="selectedObjectStatusMeta.hint" placement="top">
                <el-tag size="small" :type="selectedObjectStatusMeta.tagType" effect="plain">{{ selectedObjectStatusMeta.label }}</el-tag>
              </el-tooltip>
            </div>
            <div class="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
              <div>本体：<span class="text-slate-700">{{ selectedNode.ontology_label || '-' }}</span></div>
              <div>更新时间：<span class="text-slate-700">{{ selectedNode.update_time || '-' }}</span></div>
              <div>修改人员：<span class="text-slate-700">{{ selectedNode.update_by_name || '-' }}</span></div>
              <div>映射数：<span class="text-slate-700">{{ selectedNode.mappings_count }}</span></div>
              <div class="col-span-2">描述：<span class="text-slate-700">{{ selectedNode.description || '-' }}</span></div>
            </div>

            <!-- 属性清单 -->
            <div class="mb-1 mt-4 flex items-center gap-2">
              <i class="fa-solid fa-tag text-[10px] text-sky-500"></i>
              <span class="text-xs font-medium text-slate-600">属性清单</span>
              <span class="text-xs text-slate-400">{{ (selectedObject.entry.detail.properties || []).length }} 个</span>
            </div>
            <el-table
              v-if="(selectedObject.entry.detail.properties || []).length > 0"
              :data="selectedObject.entry.detail.properties"
              size="small"
              max-height="240"
              border
            >
              <el-table-column label="属性" min-width="170" show-overflow-tooltip>
                <template #default="{ row: prop }">
                  <span class="font-mono text-xs text-slate-800">{{ prop.property_name }}</span>
                  <span v-if="prop.display_name" class="ml-1.5 text-xs text-slate-500">{{ prop.display_name }}</span>
                  <el-tag v-if="prop.is_identifier" size="small" type="warning" effect="plain" class="ml-1">标识</el-tag>
                  <el-tag v-if="prop.is_title" size="small" type="success" effect="plain" class="ml-1">标题</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="类型" width="90" align="center">
                <template #default="{ row: prop }">
                  <span class="text-xs text-slate-500">{{ prop.value_type }}</span>
                </template>
              </el-table-column>
              <el-table-column label="描述" min-width="200" show-overflow-tooltip>
                <template #default="{ row: prop }">
                  <span class="text-xs text-slate-500">{{ prop.description || '-' }}</span>
                </template>
              </el-table-column>
            </el-table>
            <div v-else class="py-1 text-xs text-slate-400">暂无属性</div>

            <!-- 参与关系 -->
            <div class="mb-1 mt-4 flex items-center gap-2">
              <i class="fa-solid fa-circle-nodes text-[10px] text-violet-500"></i>
              <span class="text-xs font-medium text-slate-600">参与关系</span>
              <span class="text-xs text-slate-400">
                {{ selectedObject.entry.fromRelations.length + selectedObject.entry.toRelations.length }} 条
              </span>
            </div>
            <div
              v-if="selectedObject.entry.fromRelations.length + selectedObject.entry.toRelations.length > 0"
              class="flex flex-col gap-1.5"
            >
              <div
                v-for="rel in [...selectedObject.entry.fromRelations.map(relation => ({ relation, direction: 'from' })), ...selectedObject.entry.toRelations.map(relation => ({ relation, direction: 'to' }))]"
                :key="`${rel.direction}:${rel.relation.relation_type_name}`"
                class="rounded border border-slate-100 bg-slate-50/60 px-2 py-1.5"
              >
                <div class="flex items-center gap-2">
                  <el-tag size="small" :type="rel.direction === 'from' ? 'primary' : 'info'" effect="plain">
                    {{ rel.direction === 'from' ? '发起' : '参与' }}
                  </el-tag>
                  <span class="font-mono text-xs text-slate-700">{{ rel.relation.relation_type_name }}</span>
                  <span class="text-xs text-slate-500">
                    {{ rel.direction === 'from'
                      ? `→ ${rel.relation.to_display_name || rel.relation.to_object_type_name || '-'}`
                      : `← ${rel.relation.from_display_name || rel.relation.from_object_type_name || '-'}` }}
                  </span>
                  <span class="text-[11px] text-slate-400">{{ ONTOLOGY_MULTIPLICITY_META[rel.relation.multiplicity] || rel.relation.multiplicity }}</span>
                </div>
                <div v-if="rel.relation.description" class="mt-0.5 text-[11px] text-slate-500">{{ rel.relation.description }}</div>
              </div>
            </div>
            <div v-else class="py-1 text-xs text-slate-400">未参与任何关系</div>

            <!-- 映射摘要 -->
            <div class="mb-1 mt-4 flex items-center gap-2">
              <i class="fa-solid fa-link text-[10px] text-teal-500"></i>
              <span class="text-xs font-medium text-slate-600">映射摘要</span>
              <span class="text-xs text-slate-400">{{ (selectedObject.entry.detail.mappings || []).length }} 条</span>
            </div>
            <div v-if="(selectedObject.entry.detail.mappings || []).length > 0" class="flex flex-col gap-1.5 pb-2">
              <div
                v-for="mapping in selectedObject.entry.detail.mappings"
                :key="mappingIdentityText(mapping)"
                class="rounded border border-slate-100 bg-slate-50/60 px-2 py-1.5"
              >
                <div class="flex items-center gap-2">
                  <span class="text-xs text-slate-700">{{ mappingLabel(mapping) }}</span>
                  <span class="text-[11px] text-slate-400">绑定语义模型</span>
                </div>
                <div class="mt-0.5 font-mono text-[11px] text-slate-500">{{ mappingMappingText(mapping) }}</div>
              </div>
            </div>
            <div v-else class="py-1 text-xs text-slate-400">未绑定语义模型</div>
          </template>
        </template>

        <!-- 本体详情卡（本体节点是对象类型容器，点击后给出归属/绑定组信息，不再整块空白） -->
        <div v-else-if="selectedNode.kind === 'ontology'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i
              :class="[
                'fa-solid text-[11px]',
                selectedNode.is_ungrouped ? 'fa-box-open text-slate-400' : 'fa-layer-group text-amber-500'
              ]"
            ></i>
            <span class="text-sm font-medium text-slate-800">{{ selectedNode.ontology_label }}</span>
            <el-tag v-if="selectedNode.is_ungrouped" size="small" type="info" effect="plain">虚拟分组</el-tag>
          </div>
          <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
            <div>
              本体 API 名：<span class="font-mono text-slate-700">{{ selectedNode.is_ungrouped ? '-' : selectedNode.ontology_name }}</span>
            </div>
            <div>对象类型数：<span class="text-slate-700">{{ selectedNode.object_type_count }} 个</span></div>
            <div class="col-span-2">
              描述：<span class="text-slate-700">{{ selectedNode.description || '-' }}</span>
            </div>
            <div class="col-span-2 text-[11px] text-slate-400">
              本体不绑定数据源组，对象类型的取数后端由各自映射决定（可跨组）；右键本体节点可编辑/删除本体，新建对象类型与关系类型在本体下「对象类型」「关系类型」分组节点右键发起。
            </div>
          </div>
        </div>

        <!-- 属性详情卡 -->
        <div v-else-if="selectedNode.kind === 'property'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i class="fa-solid fa-tag text-[11px] text-sky-500"></i>
            <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.property.property_name }}</span>
            <span v-if="selectedNode.property.display_name" class="text-xs text-slate-500">{{ selectedNode.property.display_name }}</span>
            <el-tag v-if="selectedNode.property.is_identifier" size="small" type="warning" effect="plain">标识属性</el-tag>
            <el-tag v-if="selectedNode.property.is_title" size="small" type="success" effect="plain">标题属性</el-tag>
          </div>
          <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
            <div>值类型：<span class="text-slate-700">{{ selectedNode.property.value_type }}</span></div>
            <div v-if="selectedNode.property.enum_ref">枚举引用：<span class="font-mono text-slate-700">{{ selectedNode.property.enum_ref }}</span></div>
            <div v-if="renderHintLabels(selectedNode.property.render_hints).length > 0" class="col-span-2">
              渲染提示：<span class="text-slate-700">{{ renderHintLabels(selectedNode.property.render_hints).join('、') }}</span>
            </div>
            <div class="col-span-2">描述：<span class="text-slate-700">{{ selectedNode.property.description || '-' }}</span></div>
          </div>
        </div>

        <!-- 关系详情卡 -->
        <div v-else-if="selectedNode.kind === 'relation'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i class="fa-solid fa-circle-nodes text-[11px] text-violet-500"></i>
            <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.relation.relation_type_name }}</span>
            <el-tag
              size="small"
              :type="ONTOLOGY_STATUS_META[selectedNode.relation.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'"
              effect="plain"
            >
              {{ ONTOLOGY_STATUS_META[selectedNode.relation.status as keyof typeof ONTOLOGY_STATUS_META]?.label || selectedNode.relation.status }}
            </el-tag>
          </div>
          <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
            <div>发起方：<span class="text-slate-700">{{ objectTypeDisplayOf(selectedNode.relation.from_object_type_name) }}</span></div>
            <div>目标方：<span class="text-slate-700">{{ objectTypeDisplayOf(selectedNode.relation.to_object_type_name) }}</span></div>
            <div>基数：<span class="text-slate-700">{{ ONTOLOGY_MULTIPLICITY_META[selectedNode.relation.multiplicity] || selectedNode.relation.multiplicity }}</span></div>
            <div>状态：<span class="text-slate-700">{{ selectedNode.relation.status }}</span></div>
            <div class="col-span-2">发起列：<span class="font-mono text-slate-700">{{ selectedNode.relation.from_columns.join('、') || '-' }}</span></div>
            <div class="col-span-2">目标列：<span class="font-mono text-slate-700">{{ selectedNode.relation.to_columns.join('、') || '-' }}</span></div>
            <div class="col-span-2">
              导航名：<span class="text-slate-700">正向「{{ selectedNode.relation.to_display_name || '-' }}」；反向「{{ selectedNode.relation.from_display_name || '-' }}」</span>
              <span class="text-[11px] text-slate-300">（图谱边 label 取正向）</span>
            </div>
            <div class="col-span-2">描述：<span class="text-slate-700">{{ selectedNode.relation.description || '-' }}</span></div>
          </div>
        </div>

        <!-- 映射详情卡 -->
        <div v-else-if="selectedNode.kind === 'mapping'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i class="fa-solid fa-link text-[11px] text-teal-500"></i>
            <span class="text-sm font-medium text-slate-800">{{ mappingLabel(selectedNode.mapping) }}</span>
            <span class="text-xs text-slate-400">绑定语义模型</span>
          </div>
          <div class="mb-1 text-xs font-medium text-slate-600">字段映射（属性 API 名 → 模型维度名）</div>
          <div v-if="Object.keys(selectedNode.mapping.column_mappings || {}).length > 0" class="flex flex-col gap-1">
            <div
              v-for="(dim, propName) in selectedNode.mapping.column_mappings"
              :key="propName"
              class="flex items-center gap-2 font-mono text-xs text-slate-600"
            >
              <span>{{ propName }}</span>
              <i class="fa-solid fa-arrow-right text-[10px] text-slate-300"></i>
              <span>{{ dim }}</span>
            </div>
          </div>
          <div v-else class="text-xs text-slate-400">暂无字段映射</div>
        </div>
      </div>
    </div>

    <!-- 本体新建/编辑弹窗 -->
    <OntologyForm v-model:visible="ontologyFormVisible" :ontology-name="ontologyFormName" @saved="onOntologySaved" />

    <!-- 新建/编辑弹窗 -->
    <OntologyObjectForm
      v-model:visible="formVisible"
      :object-name="formObjectName"
      :ontologies="ontologies"
      :default-ontology-id="formDefaultOntologyId"
      @saved="onSaved"
    />

    <!-- 状态迁移弹窗：替代对象类型候选与关系类型上限都应以全量对象类型为准，不能被工具栏筛选截断 -->
    <OntologyStatusDialog v-model:visible="statusDialogVisible" :object="statusTarget" :object_types="allObjects" @saved="onSaved" />

    <!-- 对象实例只读预览：数据库投影，不落对象存储 -->
    <el-dialog v-model="objectPreviewVisible" title="对象实例预览" width="720px" destroy-on-close>
      <div class="mb-2 text-xs text-slate-500">对象类型：{{ objectPreviewType }}</div>
      <div class="mb-3 flex items-center gap-2">
        <el-input
          v-model="objectPreviewKey"
          size="small"
          placeholder="请输入对象主键值，如 ORD2024000001"
          @keyup.enter="loadObjectPreview"
        />
        <el-button size="small" type="primary" :loading="objectPreviewLoading" @click="loadObjectPreview">读取</el-button>
      </div>
      <el-alert v-if="objectPreviewError" :title="objectPreviewError" type="error" :closable="false" show-icon />
      <div v-else-if="objectPreviewData" class="rounded border border-slate-200 bg-slate-50 p-3">
        <pre class="max-h-[420px] overflow-auto text-xs leading-5 text-slate-700">{{ JSON.stringify(objectPreviewData, null, 2) }}</pre>
      </div>
      <el-empty v-else description="输入主键后读取对象" :image-size="60" />
      <template #footer>
        <el-button @click="objectPreviewVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 动作类型管理：从树分组/节点打开，复用 ActionTypeAdminPanel 定义/试运行/执行 -->
    <el-dialog v-model="actionTypeManagerVisible" title="动作类型" width="min(1100px, 94vw)" destroy-on-close>
      <ActionTypeAdminPanel ref="actionTypePanelRef" class="h-[68vh]" @changed="loadRelationSources" />
    </el-dialog>

    <!-- 一致性健康巡检弹窗：本体↔语义模型失修清单（手动触发，只读不阻断） -->
    <OntologyConsistencyDialog v-model:visible="consistencyDialogVisible" />

    <!-- 关系新建/编辑/状态/删除（右键发起；端点候选/状态上限用全量对象） -->
    <OntologyRelationForm ref="relationFormRef" :object_types="allObjects" @saved="onRelationSaved" />

    <!-- 树右键菜单（fixed 浮层，宿主页嵌入时必须 Teleport 防 transform 包含块错位） -->
    <Teleport to="body">
      <div
        v-if="ctxMenu.visible"
        class="fixed z-[var(--z-floating-tip)] w-40 rounded-md border border-slate-200 bg-white py-1 shadow-[0_6px_16px_rgba(0,0,0,0.12)]"
        :style="{ left: `${ctxMenu.x}px`, top: `${ctxMenu.y}px` }"
        @contextmenu.prevent
      >
        <button
          v-for="item in ctxMenu.items"
          :key="item.key"
          type="button"
          :disabled="item.disabled"
          :class="[
            // chat-data 不引 preflight：裸 button 保留浏览器默认样式，须显式重置底色/边框/光标
            'flex h-8 w-full items-center gap-2 border-0 px-3 text-xs transition-colors',
            item.disabled
              ? 'cursor-not-allowed bg-transparent text-slate-300'
              : item.danger
                ? 'cursor-pointer bg-transparent text-red-600 hover:bg-red-50'
                : 'cursor-pointer bg-transparent text-slate-700 hover:bg-slate-100'
          ]"
          @click="item.handler(); closeCtxMenu()"
        >
          <i :class="['fa-solid text-[10px]', item.icon]"></i>
          <span>{{ item.label }}</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
/* 树节点内容横向排布，超出省略 */
:deep(.el-tree-node__content) {
  height: 28px;
}
</style>
