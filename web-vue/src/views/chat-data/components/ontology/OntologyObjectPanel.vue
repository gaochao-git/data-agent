<script setup lang="ts">
/**
 * 后台管理 · 本体对象列表面板（左树右详情）。
 * 左侧「根域 → 业务领域 → 对象/关系 → 属性/映射」完全树形导航（对象子节点懒加载），
 * 右侧按选中节点类型渲染只读详情卡；全部写操作（新建/编辑/状态/删除）收敛到右键菜单。
 * 归属口径：属性/映射是对象自有物，挂对象下；关系是对象之间的边（双规范对齐 Palantir link type / Ossie
 * top-level relationships），挂领域下（两端同域一条、跨域两端各一条带「跨域」标记），不放对象里。
 * 领域=业务域实体，一对一绑定数据源组（对象挂领域继承其组）；领域子节点为常驻「对象/关系」分组锚点。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { TreeInstance } from 'element-plus'
import AdminPanelToolbar from '../AdminPanelToolbar.vue'
import {
  changeAdminOntologyObjectDomain,
  deleteAdminOntologyDomain,
  deleteAdminOntologyObject,
  getAdminOntologyDomains,
  getAdminOntologyObject,
  getAdminOntologyObjects,
  getAdminOntologyRelations,
  getAdminDatasources
} from '../../api'
import type {
  OntologyDomainItem,
  OntologyMappingItem,
  OntologyObjectItem,
  OntologyObjectStatus,
  OntologyPropertyItem,
  OntologyRelationItem
} from '../../types'
import OntologyDomainForm from './OntologyDomainForm.vue'
import OntologyObjectForm from './OntologyObjectForm.vue'
import OntologyRelationForm from './OntologyRelationForm.vue'
import OntologyStatusDialog from './OntologyStatusDialog.vue'
import { mappingIdentityText, ONTOLOGY_MULTIPLICITY_META, ONTOLOGY_RENDER_HINTS, ONTOLOGY_STATUS_META } from './ontologyMeta'
import { useSemanticConfigOptions } from './useSemanticConfigOptions'

const emit = defineEmits<{ changed: [] }>()

// 语义模型选项（映射行回显「模型名（组名）」）
const { load: loadSemanticOptions, optionOf } = useSemanticConfigOptions()

// ---------- 领域（业务域实体）与数据源组名 ----------

const domains = ref<OntologyDomainItem[]>([])
const groupNames = ref<Record<string, string>>({})
const domainOf = (domainId: number | null): OntologyDomainItem | undefined =>
  domains.value.find(item => item.id === domainId)
const domainLabelOf = (domainId: number | null): string => {
  const domain = domainOf(domainId)
  if (!domain) return ''
  return domain.display_name || domain.domain_name
}
const groupNameOf = (groupId: string): string => groupNames.value[groupId] || groupId

const loadDomains = async () => {
  try {
    const res = await getAdminOntologyDomains()
    if (res.status === 'success') {
      domains.value = res.data?.domains || []
    } else {
      ElMessage.error(res.msg || '获取本体领域列表失败')
    }
  } catch (err) {
    console.error('获取本体领域列表失败:', err)
  }
}

const loadGroupNames = async () => {
  try {
    const res = await getAdminDatasources()
    if (res.status === 'success') {
      const map: Record<string, string> = {}
      for (const ds of res.data?.datasources || []) map[ds.id] = ds.group_name
      groupNames.value = map
    }
  } catch (err) {
    console.error('获取数据源组清单失败:', err)
  }
}

onMounted(() => {
  // 宿主用 v-show 承载本面板且切换页签时只调 AdminPanel.ensureLoaded；自加载保证任何进入路径都有数据
  void ensureLoaded()
  void loadSemanticOptions()
  void loadDomains()
  void loadGroupNames()
  void loadRelationSources()
  document.addEventListener('click', closeCtxMenu)
})

onUnmounted(() => {
  document.removeEventListener('click', closeCtxMenu)
})

const mappingLabel = (mapping: OntologyMappingItem): string =>
  optionOf(mapping.semantic_config_id)?.label || mappingIdentityText(mapping)

const loading = ref(false)
const items = ref<OntologyObjectItem[]>([])
const keyword = ref('')
const filterStatus = ref<OntologyObjectStatus | undefined>(undefined)
const filterDomainId = ref<number | undefined>(undefined)
const hasLoaded = ref(false)

// 关系分组数据源（不受工具栏筛选影响）：全量对象用于「对象→领域」映射，全量关系用于领域关系组
const allObjects = ref<OntologyObjectItem[]>([])
const relations = ref<OntologyRelationItem[]>([])

const loadRelationSources = async () => {
  try {
    const [objectRes, relationRes] = await Promise.all([
      getAdminOntologyObjects({}),
      getAdminOntologyRelations({})
    ])
    if (objectRes.status === 'success') allObjects.value = objectRes.data?.objects || []
    if (relationRes.status === 'success') relations.value = relationRes.data?.relations || []
  } catch (err) {
    console.error('获取本体关系分组数据失败:', err)
  }
}

// 对象 API 名 → 领域主键（null=未归领域）；关系按两端对象的领域归组
const objectDomainMap = computed(() => {
  const map = new Map<string, number | null>()
  for (const item of allObjects.value) map.set(item.object_name, item.domain_id)
  return map
})

// 对象 API 名 → 展示名（关系的树行/详情卡直接显示两端对象，避免角色短语造成歧义）
const objectDisplayMap = computed(() => {
  const map = new Map<string, string>()
  for (const item of allObjects.value) map.set(item.object_name, item.display_name || item.object_name)
  return map
})
const objectDisplayOf = (objectName: string | undefined) =>
  (objectName && objectDisplayMap.value.get(objectName)) || objectName || '-'

// 域筛选项已由领域清单接口提供（domains ref），不再从对象行提取

// ---------- 树节点类型：根域 → 领域 → 对象/关系 → 属性/映射 ----------

const UNGROUPED_DOMAIN = '未归领域'
// 常驻根域：展示层锚点（右键新增业务领域），非数据实体，DB 无层级字段
const ROOT_ROW_KEY = 'ontology-root'
const domainBucketKey = (domainId: number | null) => (domainId == null ? '__ungrouped__' : String(domainId))

interface RootNode {
  row_key: string
  kind: 'root'
  label: string
  children: DomainNode[]
}
interface DomainNode {
  row_key: string
  kind: 'domain'
  /** 领域主键；未归领域虚拟节点为 null */
  domain_id: number | null
  domain_name: string
  domain_label: string
  description: string
  group_id: string
  is_ungrouped: boolean
  /** 领域下对象数（领域行直接展示，不必展开「对象」分组） */
  object_count: number
  children: TreeRow[]
}
interface ObjectNodeData {
  row_key: string
  kind: 'object'
  object_name: string
  display_name: string
  status: OntologyObjectStatus
  domain_id: number | null
  domain_label: string
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
  relation: OntologyRelationItem
  /** 两端对象分属不同领域（跨域关系在两端领域各显示一次，带「跨域」标记） */
  is_cross_domain: boolean
  is_leaf: true
}
/** 对象子树分组节点（属性/映射）：UI 导航原语，非本体层级 */
interface GroupNode {
  row_key: string
  kind: 'group'
  group: 'property' | 'mapping'
  label: string
  object_name: string
  children: TreeRow[]
  is_leaf: false
}
/** 领域子节点分组（对象/关系）：常驻右键锚点——对象组管新建对象，关系组管新建关系 */
interface DomainGroupNode {
  row_key: string
  kind: 'domain-group'
  group: 'objects' | 'relations'
  label: string
  domain_id: number | null
  children: TreeRow[]
  is_leaf: false
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
type TreeRow = RootNode | DomainNode | DomainGroupNode | ObjectNodeData | GroupNode | PropertyNode | RelationNode | MappingNode | EmptyNode

// 数据版本号：编入对象节点 key，列表刷新/保存后懒加载缓存整体失效，防止展示脏数据
const dataVersion = ref(0)

const toObjectNode = (item: OntologyObjectItem): ObjectNodeData => ({
  row_key: `object:${item.object_name}:v${dataVersion.value}`,
  kind: 'object',
  object_name: item.object_name,
  display_name: item.display_name,
  status: item.status,
  domain_id: item.domain_id,
  domain_label: domainLabelOf(item.domain_id),
  description: item.description,
  mappings_count: item.mappings_count ?? (item.mappings || []).length,
  update_time: item.update_time || '',
  update_by_name: item.update_by_name || '',
  is_leaf: false
})

// 关系按两端对象的领域归组：同域一条；跨域在两端领域各显示一条（同一关系、不同出现位置）
const relationNodesByDomain = computed(() => {
  const map = new Map<number | null, RelationNode[]>()
  for (const rel of relations.value) {
    const fromDomain = objectDomainMap.value.get(rel.from_object_name || '')
    const toDomain = objectDomainMap.value.get(rel.to_object_name || '')
    if (fromDomain === undefined && toDomain === undefined) continue
    const isCrossDomain = fromDomain !== undefined && toDomain !== undefined && fromDomain !== toDomain
    const buckets = new Set<number | null>()
    if (fromDomain !== undefined) buckets.add(fromDomain)
    if (toDomain !== undefined) buckets.add(toDomain)
    for (const bucket of buckets) {
      if (!map.has(bucket)) map.set(bucket, [])
      map.get(bucket)!.push({
        row_key: `rel:${domainBucketKey(bucket)}:${rel.relation_name}`,
        kind: 'relation',
        relation: rel,
        is_cross_domain: isCrossDomain,
        is_leaf: true
      })
    }
  }
  return map
})

// 领域子节点=常驻「对象/关系」两个分组锚点（空组保留占位，右键入口始终可用）
const buildDomainChildren = (domainId: number | null, domainObjects: OntologyObjectItem[]): TreeRow[] => {
  const bucket = domainBucketKey(domainId)
  const objectNodes: TreeRow[] = domainObjects.map(toObjectNode)
  if (objectNodes.length === 0) {
    objectNodes.push({ row_key: `domain-group:${bucket}:objects:empty`, kind: 'empty', label: '暂无对象', is_leaf: true })
  }
  const relationNodes: TreeRow[] = relationNodesByDomain.value.get(domainId) || []
  if (relationNodes.length === 0) {
    relationNodes.push({ row_key: `domain-group:${bucket}:relations:empty`, kind: 'empty', label: '暂无关系', is_leaf: true })
  }
  return [
    {
      row_key: `domain-group:${bucket}:objects`,
      kind: 'domain-group',
      group: 'objects',
      label: '对象',
      domain_id: domainId,
      children: objectNodes,
      is_leaf: false
    },
    {
      row_key: `domain-group:${bucket}:relations`,
      kind: 'domain-group',
      group: 'relations',
      label: '关系',
      domain_id: domainId,
      children: relationNodes,
      is_leaf: false
    }
  ]
}

const treeData = computed<RootNode[]>(() => {
  // 对象按 domain_id 分组挂到领域；未挂领域的进「未归领域」常驻兜底节点
  const grouped = new Map<number, OntologyObjectItem[]>()
  const ungrouped: OntologyObjectItem[] = []
  for (const item of items.value) {
    if (item.domain_id == null) ungrouped.push(item)
    else {
      if (!grouped.has(item.domain_id)) grouped.set(item.domain_id, [])
      grouped.get(item.domain_id)!.push(item)
    }
  }
  const domainNodes: DomainNode[] = domains.value.map(domain => ({
    row_key: `domain:${domain.domain_name}`,
    kind: 'domain',
    domain_id: domain.id,
    domain_name: domain.domain_name,
    domain_label: domain.display_name || domain.domain_name,
    description: domain.description || '',
    group_id: domain.group_id,
    is_ungrouped: false,
    object_count: (grouped.get(domain.id) || []).length,
    children: buildDomainChildren(domain.id, grouped.get(domain.id) || [])
  }))
  domainNodes.push({
    row_key: 'domain:__ungrouped__',
    kind: 'domain',
    domain_id: null,
    domain_name: '',
    domain_label: UNGROUPED_DOMAIN,
    description: '未挂业务领域的对象：不继承任何数据源组，建议补齐领域归属。',
    group_id: '',
    is_ungrouped: true,
    object_count: ungrouped.length,
    children: buildDomainChildren(null, ungrouped)
  })
  // 常驻根域单顶层：业务领域全部挂根域下（展示层锚点，非数据实体）
  return [{ row_key: ROOT_ROW_KEY, kind: 'root', label: '根域', children: domainNodes }]
})

// 根域/领域/对象分组默认展开（key 稳定；对象节点 key 带版本号，保存/刷新后重新折叠）
// 默认只展开根域与领域层；对象/关系分组默认收起（保持树紧凑），展开与否随用户操作
const defaultExpandedKeys = computed(() => [
  ROOT_ROW_KEY,
  ...treeData.value[0]?.children.map(row => row.row_key) || []
])

// ---------- 对象子节点懒加载 + 详情缓存（树子行与右侧详情共用一次取数） ----------

interface ObjectDetailEntry {
  detail: OntologyObjectItem
  fromRelations: OntologyRelationItem[]
  toRelations: OntologyRelationItem[]
}

const treeRef = ref<TreeInstance>()
// key = 对象节点 row_key（含版本号）
const detailStore = ref<Record<string, ObjectDetailEntry>>({})
const objectLoadState = ref<Record<string, 'loading' | 'error' | 'ready'>>({})

const buildChildRows = (objectNode: ObjectNodeData, entry: ObjectDetailEntry): TreeRow[] => {
  // 分组树：属性/映射各成组（带计数），空组不展示；关系不在此处（关系是对象之间的，挂领域下）
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
      rows.push({ row_key: `${objectNode.row_key}:group:${group}`, kind: 'group', group, label, object_name: objectNode.object_name, children, is_leaf: false })
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
      getAdminOntologyObject(objectNode.object_name),
      getAdminOntologyRelations({ from_object: objectNode.object_name }),
      getAdminOntologyRelations({ to_object: objectNode.object_name })
    ])
    if (detailRes.status !== 'success' || !detailRes.data || fromRes.status !== 'success' || toRes.status !== 'success') {
      const msg = detailRes.msg || fromRes.msg || toRes.msg || '获取对象详情失败'
      ElMessage.error(msg)
      objectLoadState.value = { ...objectLoadState.value, [key]: 'error' }
      return
    }
    const entry: ObjectDetailEntry = {
      detail: detailRes.data,
      fromRelations: fromRes.data?.relations || [],
      toRelations: toRes.data?.relations || []
    }
    detailStore.value = { ...detailStore.value, [key]: entry }
    objectLoadState.value = { ...objectLoadState.value, [key]: 'ready' }
    treeRef.value?.updateKeyChildren?.(key, buildChildRows(objectNode, entry))
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('加载本体对象详情失败:', err)
    objectLoadState.value = { ...objectLoadState.value, [key]: 'error' }
  }
}

const ensureObjectData = (objectNode: ObjectNodeData) => {
  const state = objectLoadState.value[objectNode.row_key]
  if (state === 'ready' || state === 'loading') return
  void fetchObjectEntry(objectNode)
}

const retrySelectedObject = () => {
  if (selectedNode.value?.kind === 'object') void fetchObjectEntry(selectedNode.value)
}

// ---------- 节点选中与树事件 ----------

const treeProps = { label: 'row_key', isLeaf: 'is_leaf' }
const currentNodeKey = ref('')

const selectedNode = ref<TreeRow | null>(null)

const onNodeClick = (data: TreeRow) => {
  // 根域/对象子树分组/领域分组/空态占位是导航容器或提示节点，点击不改变右侧详情；
  // 领域节点有独立详情卡（否则点击后右侧空白）。
  if (data.kind === 'root' || data.kind === 'group' || data.kind === 'domain-group' || data.kind === 'empty') return
  selectedNode.value = data
  currentNodeKey.value = data.row_key
  if (data.kind === 'object') ensureObjectData(data)
}

const onNodeExpand = (data: TreeRow) => {
  if (data.kind === 'object') ensureObjectData(data)
}

// ---------- 右侧详情派生 ----------

const selectedObject = computed(() => {
  if (selectedNode.value?.kind !== 'object') return null
  const node = selectedNode.value
  return {
    node,
    entry: detailStore.value[node.row_key],
    state: objectLoadState.value[node.row_key]
  }
})

const selectedObjectStatusMeta = computed(() => {
  const node = selectedNode.value
  if (!node || node.kind !== 'object') return null
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
    const res = await getAdminOntologyObjects({
      keyword: keyword.value.trim() || undefined,
      status: filterStatus.value || undefined,
      domain_id: filterDomainId.value
    })
    if (res.status === 'success') {
      items.value = res.data?.objects || []
      hasLoaded.value = true
      dataVersion.value += 1
      detailStore.value = {}
      objectLoadState.value = {}
      // 节点 key 内含版本号，详情缓存已整体失效：按 object_name 找回选中节点并重拉详情
      restoreObjectSelection()
    } else {
      ElMessage.error(res.msg || '获取本体对象列表失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('获取本体对象列表失败:', err)
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
// 新建对象时的预选领域（树右键「新建对象」从领域节点发起）
const formDefaultDomainId = ref<number | null>(null)

const statusDialogVisible = ref(false)
const statusTarget = ref<OntologyObjectItem | null>(null)

const openCreate = (domainId: number | null = null) => {
  formObjectName.value = ''
  formDefaultDomainId.value = domainId
  formVisible.value = true
}

const openEdit = (objectName: string) => {
  // 右键菜单已置灰，这里兜底防止其他入口（键盘/程序调用）打开 deprecated 对象的编辑表单
  const item =
    items.value.find(candidate => candidate.object_name === objectName) ||
    allObjects.value.find(candidate => candidate.object_name === objectName)
  if (item?.status === 'deprecated') {
    ElMessage.warning('已废弃对象为终态不可编辑；如需继续演进，请基于替代对象重建')
    return
  }
  formObjectName.value = objectName
  formVisible.value = true
}

const openStatus = (objectName: string) => {
  const key = Object.keys(detailStore.value).find(candidate => candidate.startsWith(`object:${objectName}:`))
  statusTarget.value =
    (key && detailStore.value[key]?.detail) ||
    items.value.find(item => item.object_name === objectName) ||
    allObjects.value.find(item => item.object_name === objectName) ||
    null
  statusDialogVisible.value = true
}

const onSaved = async () => {
  await loadObjects()
  void loadRelationSources()
  emit('changed')
}

const removeObject = async (objectName: string, status: OntologyObjectStatus, displayName: string) => {
  // 后端禁止删除 certified 对象；前端先拦一道，给出明确处置路径而不是让请求失败
  if (status === 'certified') {
    ElMessage.warning('已认证（certified）对象禁止删除：请先降级为试验/草稿，或标记为废弃')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除本体对象「${displayName || objectName}」？删除后对象、其全部属性与参与的关系将一并移除（软删）。`,
      '删除本体对象',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntologyObject(objectName)
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
    console.error('删除本体对象失败:', err)
  }
}

defineExpose({ ensureLoaded, reload })

// ---------- 领域管理（弹窗 + 删除） ----------

const domainFormVisible = ref(false)
const domainFormName = ref('')

const openDomainCreate = () => {
  domainFormName.value = ''
  domainFormVisible.value = true
}

const openDomainEdit = (domainName: string) => {
  domainFormName.value = domainName
  domainFormVisible.value = true
}

const onDomainSaved = async () => {
  await loadDomains()
  emit('changed')
}

const removeDomain = async (domainName: string, displayName: string) => {
  try {
    await ElMessageBox.confirm(
      `确定删除业务领域「${displayName || domainName}」？领域下仍挂有对象时将被拒绝，需先移出对象。`,
      '删除业务领域',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    const res = await deleteAdminOntologyDomain(domainName)
    if (res.status === 'success') {
      ElMessage.success('删除成功')
      if (selectedNode.value?.kind === 'domain' && selectedNode.value.domain_name === domainName) {
        selectedNode.value = null
        currentNodeKey.value = ''
      }
      await loadDomains()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因（如领域下仍有对象），不再重复提示
    console.error('删除本体领域失败:', err)
  }
}

// ---------- 拖拽换域（仅对象节点拖入领域节点内部；API 名不可改，归属迁移靠拖拽） ----------

// 只允许对象节点发起拖拽（领域/根域/分组等锚点不可拖）
const allowDrag = (draggingNode: { data: TreeRow }) => draggingNode.data.kind === 'object'

// 只允许放到领域节点或其「对象」分组内部（inner）；禁止排序（prev/next）与拖到其他节点
const allowDrop = (draggingNode: { data: TreeRow }, dropNode: { data: TreeRow }, type: string) => {
  if (draggingNode.data.kind !== 'object' || type !== 'inner') return false
  const target = dropNode.data
  if (target.kind === 'domain') return true
  return target.kind === 'domain-group' && target.group === 'objects'
}

const onNodeDrop = async (draggingNode: { data: TreeRow }, dropNode: { data: TreeRow }, dropType: string) => {
  if (draggingNode.data.kind !== 'object' || dropType !== 'inner') return
  const target = dropNode.data
  let targetDomainId: number | null
  if (target.kind === 'domain') {
    targetDomainId = target.is_ungrouped ? null : target.domain_id
  } else if (target.kind === 'domain-group' && target.group === 'objects') {
    targetDomainId = target.domain_id
  } else {
    return
  }
  const objectNode = draggingNode.data
  if (objectNode.domain_id === targetDomainId) return
  const targetLabel = target.kind === 'domain' ? target.domain_label : domainLabelOf(targetDomainId)
  try {
    const res = await changeAdminOntologyObjectDomain(objectNode.object_name, targetDomainId)
    if (res.status === 'success') {
      ElMessage.success(
        targetDomainId == null
          ? `已移出领域：${objectNode.display_name || objectNode.object_name}`
          : `已移动到「${targetLabel}」`
      )
    } else {
      ElMessage.error(res.msg || '移动失败')
    }
  } catch (err) {
    // 拦截器已弹出具体错误原因，不再重复提示
    console.error('对象领域归属变更失败:', err)
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
    // 根域：只新增业务领域（对象必须挂在具体领域下，根域不直接建对象）
    menuItems.push({ key: 'create-domain', label: '新增业务领域', icon: 'fa-plus', handler: () => openDomainCreate() })
  } else if (node && node.kind === 'domain' && !node.is_ungrouped) {
    // 领域节点：治理动作（编辑/删除）；新建对象/关系由领域下的分组锚点承担（容器管新增，不重复挂）
    menuItems.push(
      { key: 'edit-domain', label: '编辑领域', icon: 'fa-pen', handler: () => openDomainEdit(node.domain_name) },
      { key: 'delete-domain', label: '删除领域', icon: 'fa-trash', danger: true, handler: () => removeDomain(node.domain_name, node.domain_label) }
    )
  } else if (node && node.kind === 'object') {
    // 对象节点：编辑 / 状态 / 删除。deprecated 为终态不可再编辑（后端口径），菜单置灰
    const deprecated = node.status === 'deprecated'
    menuItems.push(
      {
        key: 'edit-object',
        label: deprecated ? '编辑对象（已废弃不可编辑）' : '编辑对象',
        icon: 'fa-pen',
        disabled: deprecated,
        handler: () => openEdit(node.object_name)
      },
      { key: 'status-object', label: '状态迁移', icon: 'fa-arrow-right-arrow-left', handler: () => openStatus(node.object_name) },
      { key: 'delete-object', label: '删除对象', icon: 'fa-trash', danger: true, handler: () => removeObject(node.object_name, node.status, node.display_name) }
    )
  } else if (node && node.kind === 'domain-group' && node.group === 'objects') {
    // 领域的「对象」分组：新建对象（预选本领域；未归领域分组预选 null）
    menuItems.push({ key: 'create-object', label: '新建对象', icon: 'fa-cube', handler: () => openCreate(node.domain_id) })
  } else if (node && node.kind === 'domain-group' && node.group === 'relations') {
    // 领域的「关系」分组：新建关系（两端手选，关系是对象之间的，不再预选单一对象）
    menuItems.push({ key: 'create-relation', label: '新建关系', icon: 'fa-plus', handler: () => relationFormRef.value?.openCreate() })
  } else if (node && node.kind === 'relation') {
    // 关系条目节点：编辑 / 状态 / 删除
    menuItems.push(
      { key: 'edit-relation', label: '编辑关系', icon: 'fa-pen', handler: () => relationFormRef.value?.openEdit(node.relation) },
      { key: 'status-relation', label: '状态迁移', icon: 'fa-arrow-right-arrow-left', handler: () => relationFormRef.value?.openStatus(node.relation) },
      { key: 'delete-relation', label: '删除关系', icon: 'fa-trash', danger: true, handler: () => relationFormRef.value?.remove(node.relation) }
    )
  } else if (!node) {
    // 树空白处（容器级右键 node=null）：等同于根域层级。
    // 注意兜底只允许 null——未显式处理的节点类型（属性/映射条目、对象子树的属性/映射分组、
    // 空态占位）必须落空，否则「新增业务领域」等根域动作会泄漏到叶子节点上。
    menuItems.push({ key: 'create-domain', label: '新增业务领域', icon: 'fa-plus', handler: () => openDomainCreate() })
  }
  // 无菜单项的节点（未归领域虚拟节点、对象子树的属性/映射分组）不弹菜单
  if (menuItems.length === 0) return
  ctxMenu.value = { visible: true, x: event.clientX, y: event.clientY, items: menuItems }
}

// ---------- 关系写操作（右键 → OntologyRelationForm） ----------

const relationFormRef = ref<InstanceType<typeof OntologyRelationForm> | null>(null)

const findObjectNode = (objectName: string): ObjectNodeData | null => {
  for (const root of treeData.value) {
    for (const domain of root.children) {
      for (const group of domain.children) {
        if (group.kind !== 'domain-group' || group.group !== 'objects') continue
        const found = group.children.find(
          (item): item is ObjectNodeData => item.kind === 'object' && item.object_name === objectName
        )
        if (found) return found
      }
    }
  }
  return null
}

// 列表刷新会整体重建树节点与详情缓存（key 带版本号）：按 object_name 重新定位选中对象，
// 否则右侧详情卡查旧 key 拿不到缓存会整块空白；对象被筛选条件排除时清空选中。
const restoreObjectSelection = () => {
  const current = selectedNode.value
  if (!current || current.kind !== 'object') return
  const node = findObjectNode(current.object_name)
  if (!node) {
    selectedNode.value = null
    currentNodeKey.value = ''
    return
  }
  selectedNode.value = node
  currentNodeKey.value = node.row_key
  ensureObjectData(node)
}

// 关系保存/状态/删除后：重拉关系源（领域关系组就地更新，key 不变不塌缩展开状态）；
// 受影响对象的详情卡「参与关系」同步原地刷新（未展开的对象在展开时自然取新数据）
const onRelationSaved = (objectNames: string[]) => {
  void loadRelationSources()
  for (const name of objectNames) {
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
        v-model="filterDomainId"
        size="small"
        placeholder="全部业务领域"
        clearable
        filterable
        :empty-values="[null, undefined]"
        class="!w-44"
        @change="onFilterChange"
      >
        <el-option
          v-for="domain in domains"
          :key="domain.id"
          :label="domain.display_name || domain.domain_name"
          :value="domain.id"
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
            empty-text="暂无业务领域，右键根域可新增"
            draggable
            :allow-drag="allowDrag"
            :allow-drop="allowDrop"
            @node-click="onNodeClick"
            @node-expand="onNodeExpand"
            @node-drop="onNodeDrop"
            @node-contextmenu="(event: MouseEvent, data: TreeRow) => openCtxMenu(event, data)"
          >
            <template #default="{ data }">
              <!-- 根域节点（常驻，右键新增业务领域） -->
              <span v-if="data.kind === 'root'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-sitemap text-[11px] text-slate-500"></i>
                <span class="text-xs font-semibold text-slate-800">{{ data.label }}</span>
                <span class="text-[11px] text-slate-400">{{ data.children.length }} 个业务领域</span>
              </span>
              <!-- 领域节点 -->
              <span v-else-if="data.kind === 'domain'" class="flex items-center gap-1.5 py-0.5">
                <i :class="['fa-solid text-[11px]', data.is_ungrouped ? 'fa-box-open text-slate-300' : 'fa-layer-group text-amber-500']"></i>
                <span class="text-xs font-medium text-slate-700">{{ data.domain_label }}</span>
                <span v-if="!data.is_ungrouped && data.group_id" class="text-[11px] text-slate-400">
                  {{ groupNameOf(data.group_id) }}
                </span>
                <span class="text-[11px] text-slate-400">{{ data.object_count }} 个对象</span>
              </span>
              <!-- 领域分组节点（对象/关系，常驻锚点） -->
              <span v-else-if="data.kind === 'domain-group'" class="flex items-center gap-1.5 py-0.5">
                <i
                  :class="[
                    'fa-solid text-[10px]',
                    data.group === 'objects' ? 'fa-cube text-blue-400' : 'fa-circle-nodes text-violet-400'
                  ]"
                ></i>
                <span class="text-xs text-slate-600">{{ data.label }}</span>
                <span class="text-[11px] text-slate-400">{{ data.children[0]?.kind === 'empty' ? 0 : data.children.length }}</span>
              </span>
              <!-- 对象节点 -->
              <span v-else-if="data.kind === 'object'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-cube text-[11px] text-blue-500"></i>
                <span class="font-mono text-xs text-slate-800">{{ data.object_name }}</span>
                <span v-if="data.display_name" class="text-[11px] text-slate-500">{{ data.display_name }}</span>
                <el-tag
                  size="small"
                  :type="ONTOLOGY_STATUS_META[data.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'"
                  effect="plain"
                >
                  {{ ONTOLOGY_STATUS_META[data.status as keyof typeof ONTOLOGY_STATUS_META]?.label || data.status }}
                </el-tag>
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
              <!-- 关系节点（挂领域下，直接显示两端对象；角色短语/详情在右侧卡片） -->
              <span v-else-if="data.kind === 'relation'" class="flex items-center gap-1.5 py-0.5">
                <i class="fa-solid fa-circle-nodes text-[10px] text-violet-500"></i>
                <span class="truncate text-xs text-slate-700" :title="data.relation.relation_name">
                  {{ objectDisplayOf(data.relation.from_object_name) }}
                  <i class="fa-solid fa-arrow-right-long mx-0.5 text-[9px] text-slate-300"></i>
                  {{ objectDisplayOf(data.relation.to_object_name) }}
                </span>
                <el-tag v-if="data.is_cross_domain" size="small" type="warning" effect="plain">跨域</el-tag>
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
            在左侧选择对象、属性、关系或映射查看详情
            <div class="mt-1 text-[11px] text-slate-300">左侧树支持右键：根域新增业务领域，领域的对象/关系分组可新建</div>
          </div>
        </div>

        <!-- 对象详情卡 -->
        <template v-else-if="selectedNode.kind === 'object' && selectedObject">
          <div v-if="selectedObject.state === 'loading'" class="py-8 text-center text-xs text-slate-400">
            <i class="fa-solid fa-spinner fa-spin mr-1.5"></i>加载对象详情中…
          </div>
          <div v-else-if="selectedObject.state === 'error'" class="py-8 text-center">
            <div class="mb-2 text-xs text-slate-500">对象详情加载失败</div>
            <el-button size="small" type="primary" @click="retrySelectedObject">重试</el-button>
          </div>
          <template v-else-if="selectedObject.entry">
            <div class="mb-3 flex items-center gap-2">
              <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.object_name }}</span>
              <span v-if="selectedNode.display_name" class="text-xs text-slate-500">{{ selectedNode.display_name }}</span>
              <el-tooltip v-if="selectedObjectStatusMeta" :content="selectedObjectStatusMeta.hint" placement="top">
                <el-tag size="small" :type="selectedObjectStatusMeta.tagType" effect="plain">{{ selectedObjectStatusMeta.label }}</el-tag>
              </el-tooltip>
            </div>
            <div class="mb-3 grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
              <div>业务领域：<span class="text-slate-700">{{ selectedNode.domain_label || '-' }}</span></div>
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
                :key="`${rel.direction}:${rel.relation.relation_name}`"
                class="rounded border border-slate-100 bg-slate-50/60 px-2 py-1.5"
              >
                <div class="flex items-center gap-2">
                  <el-tag size="small" :type="rel.direction === 'from' ? 'primary' : 'info'" effect="plain">
                    {{ rel.direction === 'from' ? '发起' : '参与' }}
                  </el-tag>
                  <span class="font-mono text-xs text-slate-700">{{ rel.relation.relation_name }}</span>
                  <span class="text-xs text-slate-500">
                    {{ rel.direction === 'from'
                      ? `→ ${rel.relation.to_display_name || rel.relation.to_object_name || '-'}`
                      : `← ${rel.relation.from_display_name || rel.relation.from_object_name || '-'}` }}
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

        <!-- 领域详情卡（领域节点是业务域实体，点击后给出归属/绑定组信息，不再整块空白） -->
        <div v-else-if="selectedNode.kind === 'domain'" class="max-w-xl">
          <div class="mb-3 flex items-center gap-2">
            <i
              :class="[
                'fa-solid text-[11px]',
                selectedNode.is_ungrouped ? 'fa-box-open text-slate-400' : 'fa-layer-group text-amber-500'
              ]"
            ></i>
            <span class="text-sm font-medium text-slate-800">{{ selectedNode.domain_label }}</span>
            <el-tag v-if="selectedNode.is_ungrouped" size="small" type="info" effect="plain">虚拟分组</el-tag>
          </div>
          <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
            <div>
              领域 API 名：<span class="font-mono text-slate-700">{{ selectedNode.is_ungrouped ? '-' : selectedNode.domain_name }}</span>
            </div>
            <div>
              绑定数据源组：
              <span class="text-slate-700">
                {{ selectedNode.is_ungrouped ? '未归领域对象不继承任何组' : groupNameOf(selectedNode.group_id) }}
              </span>
            </div>
            <div class="col-span-2">对象数：<span class="text-slate-700">{{ selectedNode.object_count }} 个</span></div>
            <div class="col-span-2">
              描述：<span class="text-slate-700">{{ selectedNode.description || '-' }}</span>
            </div>
            <div class="col-span-2 text-[11px] text-slate-400">
              对象挂领域后继承其数据源组；右键领域节点可编辑/删除领域，新建对象与关系在领域下「对象」「关系」分组节点右键发起。
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
            <span class="font-mono text-sm font-medium text-slate-800">{{ selectedNode.relation.relation_name }}</span>
            <el-tag
              size="small"
              :type="ONTOLOGY_STATUS_META[selectedNode.relation.status as keyof typeof ONTOLOGY_STATUS_META]?.tagType || 'info'"
              effect="plain"
            >
              {{ ONTOLOGY_STATUS_META[selectedNode.relation.status as keyof typeof ONTOLOGY_STATUS_META]?.label || selectedNode.relation.status }}
            </el-tag>
          </div>
          <div class="grid grid-cols-2 gap-x-6 gap-y-1 text-xs text-slate-500">
            <div>发起方：<span class="text-slate-700">{{ objectDisplayOf(selectedNode.relation.from_object_name) }}</span></div>
            <div>目标方：<span class="text-slate-700">{{ objectDisplayOf(selectedNode.relation.to_object_name) }}</span></div>
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

    <!-- 领域新建/编辑弹窗 -->
    <OntologyDomainForm v-model:visible="domainFormVisible" :domain-name="domainFormName" @saved="onDomainSaved" />

    <!-- 新建/编辑弹窗 -->
    <OntologyObjectForm
      v-model:visible="formVisible"
      :object-name="formObjectName"
      :domains="domains"
      :default-domain-id="formDefaultDomainId"
      @saved="onSaved"
    />

    <!-- 状态迁移弹窗：替代对象候选与关系上限都应以全量对象为准，不能被工具栏筛选截断 -->
    <OntologyStatusDialog v-model:visible="statusDialogVisible" :object="statusTarget" :objects="allObjects" @saved="onSaved" />

    <!-- 关系新建/编辑/状态/删除（右键发起；端点候选/状态上限用全量对象） -->
    <OntologyRelationForm ref="relationFormRef" :objects="allObjects" @saved="onRelationSaved" />

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
