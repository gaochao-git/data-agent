<script setup lang="ts">
/**
 * 数据集后台管理：Dataset 列表 + Pipeline Builder 工作台。
 * Pipeline 页签采用 master-detail：左侧列表/搜索/新建，右侧 DAG 画布 + 节点配置 + 运行记录。
 * 定义保存走 PUT（后端只追加版本），build 走 POST；节点编辑全部先落到本地 draft，保存后才提交。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  bootstrapAdminDatasets,
  buildAdminPipeline,
  deleteAdminDataset,
  deleteAdminPipeline,
  getAdminDataset,
  getAdminDatasets,
  getAdminDatasources,
  getAdminPipeline,
  getAdminPipelines,
  upsertAdminPipeline
} from '../api'
import DatasetPipelineGraph from './ontology/DatasetPipelineGraph.vue'
import type {
  DataSource,
  DatasetLayer,
  DatasetOrigin,
  DatasetPipelineEdge,
  DatasetPipelineItem,
  DatasetPipelineLayer,
  DatasetPipelineNode,
  DatasetPipelineNodePayload,
  DatasetPipelineUpsertPayload,
  LogicalDatasetItem
} from '../types'

const FILTER_OPERATORS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains']
const AGGREGATE_FUNCTIONS = ['sum', 'avg', 'min', 'max', 'count']
const DEFAULT_SOURCE_LIMIT = 5000
const OUTPUT_LAYERS: DatasetPipelineLayer[] = ['cleaned', 'enriched']

const activeView = ref<'datasets' | 'pipelines'>('datasets')
const loading = ref(false)
const bootstrapping = ref(false)
const pipelineLoading = ref(false)
const items = ref<LogicalDatasetItem[]>([])
const pipelines = ref<DatasetPipelineItem[]>([])
const datasources = ref<DataSource[]>([])
const groupId = ref('')
const layerFilter = ref<DatasetLayer | ''>('')
const originFilter = ref<DatasetOrigin | ''>('')
const detail = ref<LogicalDatasetItem | null>(null)
const detailVisible = ref(false)
const hasLoaded = ref(false)

// ---- Pipeline Builder 状态 ----
const pipelineSearch = ref('')
const activePipelineName = ref('')
const activePipeline = ref<DatasetPipelineItem | null>(null)
const activePipelineLoading = ref(false)
const pipelineDraftNodes = ref<DatasetPipelineNode[]>([])
const pipelineDraftEdges = ref<DatasetPipelineEdge[]>([])
const selectedPipelineNodeId = ref('')
const pipelineValidationError = ref('')
const pipelineDialogVisible = ref(false)
const savingPipeline = ref(false)
const buildingPipeline = ref('')
const deletingPipeline = ref('')
let pipelineLoadSeq = 0

interface PipelineFormState {
  pipeline_name: string
  display_name: string
  description: string
  target_layer: DatasetPipelineLayer
  source_dataset_name: string
  transform_type: 'filter' | 'aggregate'
  filter_column: string
  filter_operator: string
  filter_value: string
  group_by: string
  metric_column: string
  metric_function: string
  metric_alias: string
  output_dataset_name: string
  output_display_name: string
}

const createEmptyForm = (): PipelineFormState => ({
  pipeline_name: '',
  display_name: '',
  description: '',
  target_layer: 'cleaned',
  source_dataset_name: '',
  transform_type: 'filter',
  filter_column: '',
  filter_operator: 'eq',
  filter_value: '',
  group_by: '',
  metric_column: '',
  metric_function: 'sum',
  metric_alias: '',
  output_dataset_name: '',
  output_display_name: ''
})

const pipelineForm = reactive<PipelineFormState>(createEmptyForm())

// ---- 通用克隆 / 读取 ----
const cloneConfig = (config: Record<string, unknown> | undefined): Record<string, unknown> => {
  try {
    return JSON.parse(JSON.stringify(config || {})) as Record<string, unknown>
  } catch (err) {
    console.error('克隆 Pipeline 节点配置失败，回退浅拷贝:', err)
    return { ...(config || {}) }
  }
}

const clonePipelineNodes = (nodes?: DatasetPipelineNode[]): DatasetPipelineNode[] =>
  (nodes || []).map((node) => ({
    node_id: node.node_id,
    node_type: node.node_type,
    node_name: node.node_name || node.node_id,
    config: cloneConfig(node.config)
  }))

const clonePipelineEdges = (edges?: DatasetPipelineEdge[]): DatasetPipelineEdge[] =>
  (edges || []).map((edge) => ({
    edge_id: edge.edge_id,
    from_node_id: edge.from_node_id,
    to_node_id: edge.to_node_id
  }))

const readConfigText = (config: Record<string, unknown>, key: string): string => {
  const value = config[key]
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

const readConfigStringArray = (config: Record<string, unknown>, key: string): string[] => {
  const value = config[key]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

const readConfigObjectArray = (config: Record<string, unknown>, key: string): Record<string, unknown>[] => {
  const value = config[key]
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    : []
}

// ---- Dataset 页签 ----
const groups = computed(() => {
  const seen = new Set<string>()
  return datasources.value.filter((item) => {
    const id = item.id
    if (!id || seen.has(id)) return false
    seen.add(id)
    return true
  })
})

const sourceDatasetOptions = computed(() =>
  items.value.map((item) => ({
    value: item.dataset_name,
    label: `${item.dataset_name}（${item.dataset_layer || 'source'}）`
  }))
)

const memberDatasourceOptions = computed(() => {
  const seen = new Set<string>()
  const options: Array<{ value: string; label: string }> = []
  const push = (id: string | undefined, label: string) => {
    const value = String(id || '').trim()
    if (!value || seen.has(value)) return
    seen.add(value)
    options.push({ value, label: label || value })
  }
  for (const datasource of datasources.value) {
    const groupName = datasource.group_name || datasource.id
    const members = datasource.member_sources || []
    for (const member of members) {
      const memberName = member.org_name || member.org_code || member.member_datasource_id || ''
      push(member.member_datasource_id, memberName ? `${groupName} / ${memberName}` : groupName)
    }
    if (members.length === 0) {
      for (const memberId of datasource.member_datasource_ids || []) push(memberId, groupName)
    }
  }
  return options
})

const layerTagType = (layer?: string) => {
  if (layer === 'cleaned') return 'warning'
  if (layer === 'enriched') return 'success'
  return 'info'
}

const runTagType = (status?: string) => {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

const runStatusText = (status?: string) => {
  if (status === 'succeeded') return '成功'
  if (status === 'failed') return '失败'
  if (status === 'running') return '运行中'
  return status || '-'
}

// ---- Pipeline 列表 ----
const filteredPipelines = computed(() => {
  const keyword = pipelineSearch.value.trim().toLowerCase()
  if (!keyword) return pipelines.value
  return pipelines.value.filter((pipeline) =>
    [pipeline.pipeline_name, pipeline.display_name, pipeline.output_dataset_name]
      .some((value) => String(value || '').toLowerCase().includes(keyword))
  )
})

// ---- Pipeline draft / 选中节点 ----
const selectedPipelineNode = computed<DatasetPipelineNode | null>(
  () => pipelineDraftNodes.value.find((node) => node.node_id === selectedPipelineNodeId.value) || null
)
const selectedNodeConfig = computed<Record<string, unknown>>(() => selectedPipelineNode.value?.config || {})
const transformNodes = computed(() => pipelineDraftNodes.value.filter((node) => node.node_type === 'transform'))

const patchSelectedNodeConfig = (patch: Record<string, unknown>) => {
  const node = selectedPipelineNode.value
  if (!node) return
  node.config = { ...node.config, ...patch }
  pipelineValidationError.value = ''
}

const replaceSelectedNodeConfig = (config: Record<string, unknown>) => {
  const node = selectedPipelineNode.value
  if (!node) return
  node.config = config
  pipelineValidationError.value = ''
}

const nodeSnapshot = (node: DatasetPipelineNode) =>
  JSON.stringify([node.node_id, node.node_type, node.node_name || '', node.config || {}])

const edgeSnapshot = (edges: DatasetPipelineEdge[]) =>
  edges.map((edge) => `${edge.from_node_id}->${edge.to_node_id}`).sort().join('|')

const pipelineDirty = computed(() => {
  const pipeline = activePipeline.value
  if (!pipeline) return false
  const baseNodes = clonePipelineNodes(pipeline.nodes).map(nodeSnapshot).sort().join('|')
  const draftNodes = pipelineDraftNodes.value.map(nodeSnapshot).sort().join('|')
  return (
    baseNodes !== draftNodes ||
    edgeSnapshot(clonePipelineEdges(pipeline.edges)) !== edgeSnapshot(pipelineDraftEdges.value)
  )
})

const dirtyNodeIds = computed(() => {
  const pipeline = activePipeline.value
  if (!pipeline) return []
  const baseById = new Map(clonePipelineNodes(pipeline.nodes).map((node) => [node.node_id, nodeSnapshot(node)]))
  return pipelineDraftNodes.value
    .filter((node) => baseById.get(node.node_id) !== nodeSnapshot(node))
    .map((node) => node.node_id)
})

// ---- 节点配置：source ----
const sourceDatasetName = computed({
  get: () => readConfigText(selectedNodeConfig.value, 'dataset_name'),
  set: (value: string) => patchSelectedNodeConfig({ dataset_name: value })
})
const sourceMemberDatasourceIds = computed({
  get: () => readConfigStringArray(selectedNodeConfig.value, 'member_datasource_ids'),
  set: (value: string[]) => patchSelectedNodeConfig({ member_datasource_ids: value })
})
const sourceLimit = computed({
  get: () => readConfigText(selectedNodeConfig.value, 'limit'),
  set: (value: string) => {
    if (value === '') {
      patchSelectedNodeConfig({ limit: undefined })
      return
    }
    const parsed = Number(value)
    patchSelectedNodeConfig({ limit: Number.isFinite(parsed) && parsed > 0 ? parsed : value })
  }
})
const sourceMemberOptions = computed(() => {
  const options = [...memberDatasourceOptions.value]
  const known = new Set(options.map((option) => option.value))
  for (const id of sourceMemberDatasourceIds.value) {
    if (known.has(id)) continue
    known.add(id)
    options.push({ value: id, label: id })
  }
  return options
})

// ---- 节点配置：transform ----
const transformType = computed<'filter' | 'aggregate'>({
  get: () => (readConfigText(selectedNodeConfig.value, 'transform_type') === 'aggregate' ? 'aggregate' : 'filter'),
  set: (value) => {
    if (value === 'aggregate') {
      replaceSelectedNodeConfig({ transform_type: 'aggregate', group_by: [], metrics: [{ column: '', function: 'count', alias: '' }] })
      return
    }
    replaceSelectedNodeConfig({ transform_type: 'filter', conditions: [{ column: '', operator: 'eq', value: '' }] })
  }
})
const filterCondition = computed<Record<string, unknown>>(
  () => readConfigObjectArray(selectedNodeConfig.value, 'conditions')[0] || {}
)
const patchFilterCondition = (patch: Record<string, unknown>) => {
  patchSelectedNodeConfig({ conditions: [{ ...filterCondition.value, ...patch }] })
}
const filterColumn = computed({
  get: () => readConfigText(filterCondition.value, 'column'),
  set: (value: string) => patchFilterCondition({ column: value })
})
const filterOperator = computed({
  get: () => readConfigText(filterCondition.value, 'operator') || 'eq',
  set: (value: string) => patchFilterCondition({ operator: value })
})
const filterValue = computed({
  get: () => readConfigText(filterCondition.value, 'value'),
  set: (value: string) => patchFilterCondition({ value })
})
const aggregateGroupByText = computed({
  get: () => readConfigStringArray(selectedNodeConfig.value, 'group_by').join(','),
  set: (value: string) =>
    patchSelectedNodeConfig({ group_by: value.split(',').map((item) => item.trim()).filter(Boolean) })
})
const aggregateMetric = computed<Record<string, unknown>>(
  () => readConfigObjectArray(selectedNodeConfig.value, 'metrics')[0] || {}
)
const patchAggregateMetric = (patch: Record<string, unknown>) => {
  patchSelectedNodeConfig({ metrics: [{ ...aggregateMetric.value, ...patch }] })
}
const aggregateMetricColumn = computed({
  get: () => readConfigText(aggregateMetric.value, 'column'),
  set: (value: string) => patchAggregateMetric({ column: value })
})
const aggregateMetricFunction = computed({
  get: () => readConfigText(aggregateMetric.value, 'function') || 'count',
  set: (value: string) => patchAggregateMetric({ function: value })
})
const aggregateMetricAlias = computed({
  get: () => readConfigText(aggregateMetric.value, 'alias'),
  set: (value: string) => patchAggregateMetric({ alias: value })
})

// ---- 节点配置：output ----
const outputDatasetName = computed({
  get: () => readConfigText(selectedNodeConfig.value, 'dataset_name'),
  set: (value: string) => patchSelectedNodeConfig({ dataset_name: value })
})
const outputLayer = computed<DatasetPipelineLayer>({
  get: () => (readConfigText(selectedNodeConfig.value, 'layer') === 'enriched' ? 'enriched' : 'cleaned'),
  set: (value: DatasetPipelineLayer) => patchSelectedNodeConfig({ layer: value })
})
const outputDisplayName = computed({
  get: () => readConfigText(selectedNodeConfig.value, 'display_name'),
  set: (value: string) => patchSelectedNodeConfig({ display_name: value })
})

// ---- 加载 / 选中 ----
const clearActivePipeline = () => {
  activePipeline.value = null
  activePipelineName.value = ''
  pipelineDraftNodes.value = []
  pipelineDraftEdges.value = []
  selectedPipelineNodeId.value = ''
  pipelineValidationError.value = ''
}

const pickDefaultNodeId = (): string => {
  const nodes = pipelineDraftNodes.value
  const keepId = selectedPipelineNodeId.value
  if (keepId && nodes.some((node) => node.node_id === keepId)) return keepId
  return nodes.find((node) => node.node_type === 'source')?.node_id || nodes[0]?.node_id || ''
}

const resetPipelineDraft = () => {
  const pipeline = activePipeline.value
  if (!pipeline) {
    clearActivePipeline()
    return
  }
  pipelineDraftNodes.value = clonePipelineNodes(pipeline.nodes)
  pipelineDraftEdges.value = clonePipelineEdges(pipeline.edges)
  selectedPipelineNodeId.value = pickDefaultNodeId()
  pipelineValidationError.value = ''
}

const loadActivePipeline = async (pipelineName: string) => {
  const seq = ++pipelineLoadSeq
  activePipelineLoading.value = true
  try {
    const res = await getAdminPipeline(pipelineName)
    if (seq !== pipelineLoadSeq) return
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '加载 Pipeline 失败')
      return
    }
    activePipeline.value = res.data
    activePipelineName.value = res.data.pipeline_name || pipelineName
    selectedPipelineNodeId.value = ''
    resetPipelineDraft()
  } catch (err) {
    console.error('加载 Pipeline 详情失败:', err)
  } finally {
    if (seq === pipelineLoadSeq) activePipelineLoading.value = false
  }
}

const selectPipeline = async (pipelineName: string) => {
  if (pipelineName === activePipelineName.value && activePipeline.value) return
  if (pipelineDirty.value) {
    try {
      await ElMessageBox.confirm('当前 Pipeline 有未保存的修改，切换后修改将丢失。是否继续？', '切换 Pipeline', {
        type: 'warning'
      })
    } catch {
      return
    }
  }
  activePipelineName.value = pipelineName
  await loadActivePipeline(pipelineName)
}

const switchView = async (view: 'datasets' | 'pipelines') => {
  activeView.value = view
  if (view === 'pipelines') {
    await loadPipelines()
    const firstPipeline = pipelines.value[0]
    if (!activePipeline.value && firstPipeline) {
      await selectPipeline(firstPipeline.pipeline_name)
    }
    return
  }
  await load()
}

// ---- Dataset 加载 ----
const load = async () => {
  loading.value = true
  try {
    const res = await getAdminDatasets({
      dataset_layer: layerFilter.value || undefined,
      dataset_origin: originFilter.value || undefined
    })
    if (res.status === 'success') items.value = res.data.datasets || []
  } finally {
    loading.value = false
  }
}

const loadDatasources = async () => {
  try {
    const res = await getAdminDatasources({ include_tables: false })
    if (res.status === 'success') {
      datasources.value = res.data.datasources || []
      const firstGroup = groups.value[0]
      if (!groupId.value && firstGroup) groupId.value = firstGroup.id
    }
  } catch (err) {
    console.error('加载数据源组失败:', err)
  }
}

const loadPipelines = async () => {
  pipelineLoading.value = true
  try {
    const res = await getAdminPipelines()
    if (res.status === 'success') {
      pipelines.value = res.data.pipelines || []
      if (activePipelineName.value && !pipelines.value.some((item) => item.pipeline_name === activePipelineName.value)) {
        clearActivePipeline()
      }
    }
  } finally {
    pipelineLoading.value = false
  }
}

const ensureLoaded = async () => {
  if (hasLoaded.value) return
  hasLoaded.value = true
  await Promise.all([load(), loadDatasources(), loadPipelines()])
}

// ---- Dataset 操作 ----
const bootstrap = async () => {
  if (!groupId.value) {
    ElMessage.warning('请选择数据源组')
    return
  }
  bootstrapping.value = true
  try {
    const res = await bootstrapAdminDatasets(groupId.value)
    if (res.status === 'success') {
      ElMessage.success(`已注册 ${res.data.datasets?.length || 0} 个 Dataset`)
      await load()
    } else {
      ElMessage.error(res.msg || '注册失败')
    }
  } catch (err) {
    console.error('注册 Dataset 失败:', err)
  } finally {
    bootstrapping.value = false
  }
}

const openDetail = async (row: LogicalDatasetItem) => {
  const res = await getAdminDataset(row.dataset_name)
  if (res.status === 'success') {
    detail.value = res.data
    detailVisible.value = true
  }
}

const remove = async (row: LogicalDatasetItem) => {
  try {
    await ElMessageBox.confirm(`确定删除 Dataset「${row.dataset_name}」？`, '删除 Dataset', { type: 'warning' })
  } catch {
    return
  }
  const res = await deleteAdminDataset(row.dataset_name)
  if (res.status === 'success') {
    ElMessage.success('删除成功')
    await load()
  }
}

// ---- Pipeline draft 编辑 ----
const nextNodeId = (prefix: string) => {
  const used = new Set(pipelineDraftNodes.value.map((node) => node.node_id))
  let index = 1
  while (used.has(`${prefix}_${index}`)) index += 1
  return `${prefix}_${index}`
}

const nextEdgeId = (extra: string[] = []) => {
  const used = new Set([...pipelineDraftEdges.value.map((edge) => edge.edge_id), ...extra])
  let index = 1
  while (used.has(`edge_${index}`)) index += 1
  return `edge_${index}`
}

const buildDefaultTransform = (name: string): DatasetPipelineNode => ({
  node_id: nextNodeId('transform'),
  node_type: 'transform',
  node_name: name,
  config: { transform_type: 'filter', conditions: [{ column: '', operator: 'eq', value: '' }] }
})

const addTransformNode = () => {
  const source = pipelineDraftNodes.value.find((node) => node.node_type === 'source')
  const output = pipelineDraftNodes.value.find((node) => node.node_type === 'output')
  if (!source || !output) {
    ElMessage.warning('请先补全 source / output 节点')
    return
  }
  const node = buildDefaultTransform(`转换 ${transformNodes.value.length + 1}`)
  const incoming = pipelineDraftEdges.value.find((edge) => edge.to_node_id === output.node_id)
  const fromNodeId = incoming?.from_node_id || source.node_id
  const firstEdgeId = nextEdgeId()
  const secondEdgeId = nextEdgeId([firstEdgeId])
  pipelineDraftNodes.value = [...pipelineDraftNodes.value, node]
  pipelineDraftEdges.value = [
    ...pipelineDraftEdges.value.filter(
      (edge) => !(edge.from_node_id === fromNodeId && edge.to_node_id === output.node_id)
    ),
    { edge_id: firstEdgeId, from_node_id: fromNodeId, to_node_id: node.node_id },
    { edge_id: secondEdgeId, from_node_id: node.node_id, to_node_id: output.node_id }
  ]
  selectedPipelineNodeId.value = node.node_id
  pipelineValidationError.value = ''
}

const removeTransformNode = (nodeId: string) => {
  const node = pipelineDraftNodes.value.find((item) => item.node_id === nodeId)
  if (!node || node.node_type !== 'transform') return
  const incoming = pipelineDraftEdges.value.find((edge) => edge.to_node_id === nodeId)
  const outgoing = pipelineDraftEdges.value.find((edge) => edge.from_node_id === nodeId)
  if (!incoming || !outgoing) {
    ElMessage.warning('该 Transform 节点连线异常，无法删除')
    return
  }
  pipelineDraftNodes.value = pipelineDraftNodes.value.filter((item) => item.node_id !== nodeId)
  pipelineDraftEdges.value = [
    ...pipelineDraftEdges.value.filter(
      (edge) => edge.edge_id !== incoming.edge_id && edge.edge_id !== outgoing.edge_id
    ),
    {
      edge_id: nextEdgeId([incoming.edge_id, outgoing.edge_id]),
      from_node_id: incoming.from_node_id,
      to_node_id: outgoing.to_node_id
    }
  ]
  if (selectedPipelineNodeId.value === nodeId) selectedPipelineNodeId.value = incoming.from_node_id
  pipelineValidationError.value = ''
}

const initializePipelineDraft = () => {
  const pipeline = activePipeline.value
  if (!pipeline) return
  const outputLayer: DatasetPipelineLayer = pipeline.target_layer === 'enriched' ? 'enriched' : 'cleaned'
  const outputName = pipeline.output_dataset_name || `${pipeline.pipeline_name}_output`
  pipelineDraftNodes.value = [
    {
      node_id: 'src',
      node_type: 'source',
      node_name: '源数据',
      config: {
        dataset_name: sourceDatasetOptions.value[0]?.value || '',
        member_datasource_ids: [],
        limit: DEFAULT_SOURCE_LIMIT
      }
    },
    {
      node_id: 'transform',
      node_type: 'transform',
      node_name: '转换 1',
      config: { transform_type: 'filter', conditions: [{ column: '', operator: 'eq', value: '' }] }
    },
    {
      node_id: 'out',
      node_type: 'output',
      node_name: '输出',
      config: { dataset_name: outputName, layer: outputLayer, display_name: pipeline.display_name || outputName }
    }
  ]
  pipelineDraftEdges.value = [
    { edge_id: 'edge_1', from_node_id: 'src', to_node_id: 'transform' },
    { edge_id: 'edge_2', from_node_id: 'transform', to_node_id: 'out' }
  ]
  selectedPipelineNodeId.value = 'src'
  pipelineValidationError.value = ''
}

// ---- Pipeline 校验 ----
const validateDraftChain = (): string => {
  const nodes = pipelineDraftNodes.value
  const edges = pipelineDraftEdges.value
  const sourceNodes = nodes.filter((node) => node.node_type === 'source')
  const outputNodes = nodes.filter((node) => node.node_type === 'output')
  if (sourceNodes.length !== 1) return 'Pipeline 必须且只能有 1 个 source 节点'
  if (outputNodes.length !== 1) return 'Pipeline 必须且只能有 1 个 output 节点'
  if (transformNodes.value.length < 1) return 'Pipeline 至少需要 1 个 transform 节点'
  if (edges.length !== nodes.length - 1) return 'Pipeline 必须保持单链：连线数应等于节点数 - 1'
  const byId = new Map(nodes.map((node) => [node.node_id, node]))
  const outgoing = new Map<string, string>()
  const incoming = new Set<string>()
  for (const edge of edges) {
    const fromNode = byId.get(edge.from_node_id)
    const toNode = byId.get(edge.to_node_id)
    if (!fromNode || !toNode) return 'Pipeline 连线引用了不存在的节点'
    if (edge.from_node_id === edge.to_node_id) return 'Pipeline 不支持自环连线'
    if (outgoing.has(edge.from_node_id)) return `节点 ${edge.from_node_id} 存在多条出边，必须保持单链`
    if (incoming.has(edge.to_node_id)) return `节点 ${edge.to_node_id} 存在多条入边，必须保持单链`
    outgoing.set(edge.from_node_id, edge.to_node_id)
    incoming.add(edge.to_node_id)
  }
  const sourceId = sourceNodes[0]?.node_id
  if (!sourceId) return 'Pipeline 缺少 source 节点'
  const visited = new Set<string>([sourceId])
  let current = sourceId
  while (outgoing.has(current)) {
    const next = outgoing.get(current) as string
    if (visited.has(next)) return 'Pipeline 连线存在环，必须保持单链无环'
    visited.add(next)
    current = next
  }
  if (visited.size !== nodes.length) return 'Pipeline 存在未连通节点'
  if (byId.get(current)?.node_type !== 'output') return 'Pipeline 链路末端必须是 output 节点'
  return ''
}

const validateDraftNodeConfig = (): string => {
  for (const node of pipelineDraftNodes.value) {
    const config = node.config || {}
    if (node.node_type === 'source') {
      if (!readConfigText(config, 'dataset_name').trim()) return 'source 节点请选择输入 Dataset'
      const limitValue = config.limit
      if (limitValue !== undefined && limitValue !== null && limitValue !== '') {
        const limit = Number(limitValue)
        if (!Number.isFinite(limit) || limit < 1) return 'source 节点读取行数上限必须是正整数'
      }
      continue
    }
    if (node.node_type === 'transform') {
      const type = readConfigText(config, 'transform_type')
      if (type === 'aggregate') {
        const metric = readConfigObjectArray(config, 'metrics')[0] || {}
        if (!readConfigText(metric, 'column').trim()) return 'aggregate Transform 请填写聚合列'
        continue
      }
      if (type === 'filter') {
        const condition = readConfigObjectArray(config, 'conditions')[0] || {}
        if (!readConfigText(condition, 'column').trim()) return 'filter Transform 请填写过滤列'
        if (!readConfigText(condition, 'value')) return 'filter Transform 请填写过滤值'
        continue
      }
      return 'Transform 类型不合法，仅支持 filter / aggregate'
    }
    if (node.node_type === 'output') {
      if (!readConfigText(config, 'dataset_name').trim()) return 'output 节点请填写输出 Dataset 名称'
      if (!OUTPUT_LAYERS.includes(readConfigText(config, 'layer') as DatasetPipelineLayer)) {
        return 'output 节点层级必须是 cleaned 或 enriched'
      }
    }
  }
  return ''
}

const validatePipelineDraft = (): string => validateDraftChain() || validateDraftNodeConfig()

// ---- Pipeline 保存 / 构建 / 删除 ----
const normalizeNodeConfigForSave = (node: DatasetPipelineNode): Record<string, unknown> => {
  const config = cloneConfig(node.config)
  if (node.node_type === 'source') {
    const limit = Number(config.limit)
    const normalized: Record<string, unknown> = {
      dataset_name: readConfigText(config, 'dataset_name').trim(),
      member_datasource_ids: readConfigStringArray(config, 'member_datasource_ids')
    }
    if (Number.isFinite(limit) && limit > 0) normalized.limit = Math.floor(limit)
    return normalized
  }
  if (node.node_type === 'transform') {
    if (readConfigText(config, 'transform_type') === 'aggregate') {
      const metric = readConfigObjectArray(config, 'metrics')[0] || {}
      const column = readConfigText(metric, 'column').trim()
      const fn = readConfigText(metric, 'function') || 'count'
      return {
        transform_type: 'aggregate',
        group_by: readConfigStringArray(config, 'group_by'),
        metrics: [
          {
            column,
            function: fn,
            alias: readConfigText(metric, 'alias').trim() || `${fn}_${column}`
          }
        ]
      }
    }
    const condition = readConfigObjectArray(config, 'conditions')[0] || {}
    return {
      transform_type: 'filter',
      conditions: [
        {
          column: readConfigText(condition, 'column').trim(),
          operator: readConfigText(condition, 'operator') || 'eq',
          value: readConfigText(condition, 'value')
        }
      ]
    }
  }
  return {
    dataset_name: readConfigText(config, 'dataset_name').trim(),
    layer: readConfigText(config, 'layer') || 'cleaned',
    display_name: readConfigText(config, 'display_name').trim(),
    description: readConfigText(config, 'description').trim(),
    primary_key: readConfigStringArray(config, 'primary_key'),
    status: readConfigText(config, 'status') || 'experimental'
  }
}

const saveActivePipeline = async () => {
  const pipeline = activePipeline.value
  if (!pipeline) return
  const error = validatePipelineDraft()
  if (error) {
    pipelineValidationError.value = error
    ElMessage.warning(error)
    return
  }
  const outputNode = pipelineDraftNodes.value.find((node) => node.node_type === 'output')
  const outputConfigLayer = readConfigText(outputNode?.config || {}, 'layer')
  const targetLayer: DatasetPipelineLayer = outputConfigLayer === 'enriched' ? 'enriched' : 'cleaned'
  const nodesPayload: DatasetPipelineNodePayload[] = pipelineDraftNodes.value.map((node) => ({
    node_id: node.node_id,
    node_type: node.node_type,
    node_name: node.node_name || node.node_id,
    config: normalizeNodeConfigForSave(node)
  }))
  const payload: DatasetPipelineUpsertPayload = {
    display_name: pipeline.display_name || pipeline.pipeline_name,
    description: pipeline.description || '',
    target_layer: targetLayer,
    status: pipeline.status || 'experimental',
    nodes: nodesPayload,
    edges: pipelineDraftEdges.value.map((edge) => ({
      edge_id: edge.edge_id,
      from_node_id: edge.from_node_id,
      to_node_id: edge.to_node_id
    }))
  }
  savingPipeline.value = true
  try {
    const res = await upsertAdminPipeline(pipeline.pipeline_name, payload)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '保存失败')
      return
    }
    activePipeline.value = res.data
    resetPipelineDraft()
    ElMessage.success(`保存成功，当前定义版本 v${res.data.current_version_no}`)
    await Promise.all([loadPipelines(), load()])
  } catch (err) {
    console.error('保存 Pipeline 失败:', err)
  } finally {
    savingPipeline.value = false
  }
}

const buildActivePipeline = async () => {
  const pipeline = activePipeline.value
  if (!pipeline) return
  if (pipelineDirty.value) {
    ElMessage.warning('请先保存当前修改再构建')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定构建 Pipeline「${pipeline.pipeline_name}」？将生成新的输出 Dataset 版本。`,
      '构建 Pipeline',
      { type: 'warning' }
    )
  } catch {
    return
  }
  buildingPipeline.value = pipeline.pipeline_name
  try {
    const res = await buildAdminPipeline(pipeline.pipeline_name)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '构建失败')
      return
    }
    ElMessage.success(`构建完成：输出 ${res.data.output_summary?.row_count ?? 0} 行`)
    await Promise.all([loadPipelines(), load()])
    await loadActivePipeline(pipeline.pipeline_name)
  } catch (err) {
    console.error('构建 Pipeline 失败:', err)
  } finally {
    buildingPipeline.value = ''
  }
}

const refreshActiveRuns = async () => {
  const pipeline = activePipeline.value
  if (!pipeline) return
  if (pipelineDirty.value) {
    ElMessage.warning('请先保存或还原当前修改再刷新运行记录')
    return
  }
  const res = await getAdminPipeline(pipeline.pipeline_name)
  if (res.status === 'success' && activePipeline.value) {
    activePipeline.value = { ...activePipeline.value, runs: res.data.runs || [] }
  }
}

const removePipeline = async (pipeline: DatasetPipelineItem) => {
  try {
    await ElMessageBox.confirm(`确定删除 Pipeline「${pipeline.pipeline_name}」？`, '删除 Pipeline', { type: 'warning' })
  } catch {
    return
  }
  deletingPipeline.value = pipeline.pipeline_name
  try {
    const res = await deleteAdminPipeline(pipeline.pipeline_name)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '删除失败')
      return
    }
    ElMessage.success('删除成功')
    if (activePipelineName.value === pipeline.pipeline_name) clearActivePipeline()
    await Promise.all([loadPipelines(), load()])
  } catch (err) {
    console.error('删除 Pipeline 失败:', err)
  } finally {
    deletingPipeline.value = ''
  }
}

// ---- Pipeline 新建（保留原创建表单） ----
const resetPipelineForm = () => {
  Object.assign(pipelineForm, createEmptyForm())
}

const openCreatePipeline = () => {
  resetPipelineForm()
  pipelineDialogVisible.value = true
}

const buildTransformConfig = (): Record<string, unknown> => {
  if (pipelineForm.transform_type === 'filter') {
    return {
      transform_type: 'filter',
      conditions: [
        {
          column: pipelineForm.filter_column.trim(),
          operator: pipelineForm.filter_operator,
          value: pipelineForm.filter_value
        }
      ]
    }
  }
  const metricColumn = pipelineForm.metric_column.trim()
  const metricFunction = pipelineForm.metric_function
  return {
    transform_type: 'aggregate',
    group_by: pipelineForm.group_by
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    metrics: [
      {
        column: metricColumn,
        function: metricFunction,
        alias: pipelineForm.metric_alias.trim() || `${metricFunction}_${metricColumn}`
      }
    ]
  }
}

const validatePipelineForm = (): string => {
  if (!pipelineForm.pipeline_name.trim()) return '请填写 Pipeline 名称'
  if (!pipelineForm.source_dataset_name) return '请选择输入 Dataset'
  if (!pipelineForm.output_dataset_name.trim()) return '请填写输出 Dataset 名称'
  if (pipelineForm.transform_type === 'filter') {
    if (!pipelineForm.filter_column.trim()) return '请填写过滤列'
    if (!pipelineForm.filter_value) return '请填写过滤值'
  }
  if (pipelineForm.transform_type === 'aggregate' && !pipelineForm.metric_column.trim()) {
    return '请填写聚合列'
  }
  return ''
}

const saveCreatePipeline = async () => {
  const error = validatePipelineForm()
  if (error) {
    ElMessage.warning(error)
    return
  }
  const outputConfig: Record<string, unknown> = {
    dataset_name: pipelineForm.output_dataset_name.trim(),
    layer: pipelineForm.target_layer
  }
  if (pipelineForm.output_display_name.trim()) {
    outputConfig.display_name = pipelineForm.output_display_name.trim()
  }
  const nodes: DatasetPipelineNodePayload[] = [
    {
      node_id: 'src',
      node_type: 'source',
      node_name: '源数据',
      config: {
        dataset_name: pipelineForm.source_dataset_name,
        member_datasource_ids: [],
        limit: DEFAULT_SOURCE_LIMIT
      }
    },
    { node_id: 'transform', node_type: 'transform', node_name: '转换', config: buildTransformConfig() },
    { node_id: 'out', node_type: 'output', node_name: '输出', config: outputConfig }
  ]
  const payload: DatasetPipelineUpsertPayload = {
    display_name: pipelineForm.display_name.trim(),
    description: pipelineForm.description.trim(),
    target_layer: pipelineForm.target_layer,
    status: 'experimental',
    nodes,
    edges: [
      { edge_id: 'e1', from_node_id: 'src', to_node_id: 'transform' },
      { edge_id: 'e2', from_node_id: 'transform', to_node_id: 'out' }
    ]
  }
  savingPipeline.value = true
  try {
    const res = await upsertAdminPipeline(pipelineForm.pipeline_name.trim(), payload)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '创建失败')
      return
    }
    ElMessage.success('创建成功')
    pipelineDialogVisible.value = false
    resetPipelineForm()
    await Promise.all([loadPipelines(), load()])
    await selectPipeline(res.data.pipeline_name || pipelineForm.pipeline_name.trim())
  } catch (err) {
    console.error('创建 Pipeline 失败:', err)
  } finally {
    savingPipeline.value = false
  }
}

onMounted(() => {
  void ensureLoaded()
})
defineExpose({ ensureLoaded })
</script>

<template>
  <div class="flex h-full flex-col bg-slate-50">
    <div class="border-b border-slate-200 bg-white px-5 py-4">
      <div class="flex items-center justify-between gap-4">
        <div class="min-w-0">
          <div class="text-sm font-semibold text-slate-800">
            {{ activeView === 'datasets' ? '数据集' : 'Dataset Pipeline' }}
          </div>
          <div class="mt-0.5 truncate text-[11px] text-slate-400">
            {{
              activeView === 'datasets'
                ? '逻辑 Dataset：一套 schema 绑定多个机构数据源，按 source / cleaned / enriched 分层'
                : 'Pipeline Builder：左侧选择 Pipeline，右侧编辑 DAG 节点、保存定义（追加版本）并手动构建'
            }}
          </div>
        </div>
        <div class="flex shrink-0 items-center rounded-lg border border-slate-200 bg-slate-50 p-0.5">
          <button
            class="rounded-md px-3 py-1.5 text-xs transition-colors"
            :class="activeView === 'datasets' ? 'bg-slate-800 text-white shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="switchView('datasets')"
          >
            数据集
          </button>
          <button
            class="rounded-md px-3 py-1.5 text-xs transition-colors"
            :class="activeView === 'pipelines' ? 'bg-slate-800 text-white shadow-sm' : 'text-slate-500 hover:text-slate-700'"
            @click="switchView('pipelines')"
          >
            Pipeline
          </button>
        </div>
      </div>

      <div v-if="activeView === 'datasets'" class="mt-3 flex flex-wrap items-center gap-2">
        <el-select v-model="layerFilter" size="small" clearable placeholder="全部层级" class="!w-32" @change="load">
          <el-option label="source" value="source" />
          <el-option label="cleaned" value="cleaned" />
          <el-option label="enriched" value="enriched" />
        </el-select>
        <el-select v-model="originFilter" size="small" clearable placeholder="全部来源" class="!w-32" @change="load">
          <el-option label="datasource" value="datasource" />
          <el-option label="pipeline" value="pipeline" />
        </el-select>
        <el-select v-model="groupId" size="small" placeholder="选择数据源组" class="!w-48">
          <el-option
            v-for="group in groups"
            :key="group.id"
            :label="group.group_name || group.id"
            :value="group.id"
          />
        </el-select>
        <el-button size="small" type="primary" :loading="bootstrapping" @click="bootstrap">从数据源注册</el-button>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        <span class="ml-auto text-[11px] text-slate-400">{{ items.length }} 个 Dataset</span>
      </div>

      <div v-else class="mt-3 flex flex-wrap items-center gap-2">
        <el-button size="small" :loading="pipelineLoading" @click="loadPipelines">刷新</el-button>
        <span class="ml-auto text-[11px] text-slate-400">{{ filteredPipelines.length }} / {{ pipelines.length }} 个 Pipeline</span>
      </div>
    </div>

    <div v-if="activeView === 'datasets'" class="min-h-0 flex-1 overflow-auto p-5">
      <div class="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <el-table v-loading="loading" :data="items" size="small" border>
        <el-table-column prop="dataset_name" label="Dataset" min-width="150" />
        <el-table-column prop="display_name" label="展示名" min-width="140" />
        <el-table-column label="层级" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="layerTagType(row.dataset_layer)">{{ row.dataset_layer || 'source' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.dataset_origin === 'pipeline' ? 'warning' : 'info'">
              {{ row.dataset_origin || 'datasource' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="binding_count" label="数据源" width="80" />
        <el-table-column label="主键" min-width="160">
          <template #default="{ row }">{{ (row.primary_key || []).join(', ') || '-' }}</template>
        </el-table-column>
        <el-table-column prop="update_time" label="更新时间" width="170" />
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link size="small" type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- Pipeline Builder：master-detail -->
    <div v-else class="flex min-h-0 flex-1 flex-col lg:flex-row">
      <aside class="flex max-h-52 w-full shrink-0 flex-col border-b border-slate-200 bg-white lg:max-h-none lg:w-64 lg:border-b-0 lg:border-r">
        <div class="flex items-center gap-2 border-b border-slate-100 p-2.5">
          <el-input v-model="pipelineSearch" size="small" clearable placeholder="搜索 Pipeline" data-testid="pipeline-search" />
          <el-button size="small" :loading="pipelineLoading" @click="loadPipelines">刷新</el-button>
        </div>
        <div class="min-h-0 flex-1 overflow-auto p-2">
          <button
            v-for="item in filteredPipelines"
            :key="item.pipeline_name"
            type="button"
            :data-pipeline-name="item.pipeline_name"
            class="mb-1 w-full rounded-md border px-2.5 py-2 text-left transition-colors"
            :class="
              item.pipeline_name === activePipelineName
                ? 'border-blue-300 bg-blue-50'
                : 'border-transparent hover:border-slate-200 hover:bg-slate-50'
            "
            @click="selectPipeline(item.pipeline_name)"
          >
            <div class="flex items-center gap-1.5">
              <span class="truncate text-xs font-medium text-slate-700">{{ item.pipeline_name }}</span>
              <el-tag size="small" effect="plain" class="ml-auto shrink-0">v{{ item.current_version_no }}</el-tag>
            </div>
            <div class="mt-0.5 truncate text-[10px] text-slate-400">
              {{ item.display_name || item.output_dataset_name || '-' }}
            </div>
            <div class="mt-1 flex items-center gap-1.5 text-[10px] text-slate-400">
              <el-tag v-if="item.last_run" size="small" :type="runTagType(item.last_run.run_status)">
                {{ runStatusText(item.last_run.run_status) }}
              </el-tag>
              <span v-else>未运行</span>
              <span class="truncate">{{ item.output_dataset_name || '无输出' }}</span>
            </div>
          </button>
          <div v-if="filteredPipelines.length === 0" class="px-2 py-6 text-center text-[11px] text-slate-400">
            {{ pipelines.length === 0 ? '暂无 Pipeline，点击下方创建' : '没有匹配的 Pipeline' }}
          </div>
        </div>
        <div class="border-t border-slate-100 p-2">
          <el-button class="w-full" size="small" type="primary" @click="openCreatePipeline">创建 Pipeline</el-button>
        </div>
      </aside>

      <section class="min-h-0 min-w-0 flex-1 overflow-auto p-4">
        <template v-if="activePipeline">
          <div class="flex flex-wrap items-start gap-2">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-1.5">
                <span class="truncate text-sm font-semibold text-slate-800">{{ activePipeline.pipeline_name }}</span>
                <el-tag size="small" :type="layerTagType(activePipeline.target_layer)">{{ activePipeline.target_layer }}</el-tag>
                <el-tag size="small" effect="plain" data-testid="pipeline-version">v{{ activePipeline.current_version_no }}</el-tag>
                <el-tag v-if="pipelineDirty" size="small" type="warning" data-testid="pipeline-dirty">未保存修改</el-tag>
              </div>
              <div class="mt-0.5 text-[11px] text-slate-400">
                {{ activePipeline.display_name || '-' }} · 输出 {{ activePipeline.output_dataset_name || '未定义' }}
              </div>
            </div>
            <div class="ml-auto flex flex-wrap items-center gap-1.5">
              <el-button size="small" :disabled="!pipelineDirty" @click="resetPipelineDraft">还原</el-button>
              <el-button
                size="small"
                type="primary"
                data-testid="pipeline-save"
                :loading="savingPipeline"
                :disabled="!pipelineDirty"
                @click="saveActivePipeline"
              >
                保存定义
              </el-button>
              <el-button
                size="small"
                type="success"
                data-testid="pipeline-build"
                :loading="buildingPipeline === activePipeline.pipeline_name"
                :disabled="pipelineDirty"
                @click="buildActivePipeline"
              >
                构建
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                :loading="deletingPipeline === activePipeline.pipeline_name"
                @click="removePipeline(activePipeline)"
              >
                删除
              </el-button>
            </div>
          </div>

          <div
            v-if="pipelineValidationError"
            data-testid="pipeline-validation-error"
            class="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600"
          >
            {{ pipelineValidationError }}
          </div>

          <div class="mt-3 flex flex-col gap-3 xl:flex-row">
            <div class="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
              <div class="flex flex-wrap items-center gap-2">
                <span class="text-xs font-medium text-slate-600">Pipeline 定义 DAG</span>
                <span class="text-[10px] text-slate-400">单链 source → transforms → output；点击节点在右侧编辑配置</span>
                <el-button class="ml-auto" size="small" @click="addTransformNode">添加 Transform</el-button>
              </div>
              <div class="mt-2">
                <DatasetPipelineGraph
                  :nodes="pipelineDraftNodes"
                  :edges="pipelineDraftEdges"
                  :selected-node-id="selectedPipelineNodeId"
                  :dirty-node-ids="dirtyNodeIds"
                  @select="selectedPipelineNodeId = $event"
                />
                <div v-if="pipelineDraftNodes.length === 0" class="mt-2 text-center">
                  <el-button size="small" type="primary" plain @click="initializePipelineDraft">初始化定义节点</el-button>
                </div>
              </div>
            </div>

            <!-- 节点配置 -->
            <div class="w-full shrink-0 rounded-lg border border-slate-200 bg-white p-3 shadow-sm xl:w-80">
              <template v-if="selectedPipelineNode">
                <div class="flex items-center gap-2">
                  <span class="text-xs font-semibold text-slate-700">
                    {{ selectedPipelineNode.node_name || selectedPipelineNode.node_id }}
                  </span>
                  <el-tag size="small" effect="plain">{{ selectedPipelineNode.node_type }}</el-tag>
                  <el-button
                    v-if="selectedPipelineNode.node_type === 'transform'"
                    class="ml-auto"
                    link
                    size="small"
                    type="danger"
                    @click="removeTransformNode(selectedPipelineNode.node_id)"
                  >
                    删除节点
                  </el-button>
                </div>
                <div class="mt-1 font-mono text-[10px] text-slate-400">{{ selectedPipelineNode.node_id }}</div>

                <template v-if="selectedPipelineNode.node_type === 'source'">
                  <div class="mt-3">
                    <div class="mb-1 text-[11px] text-slate-500">输入 Dataset</div>
                    <el-select v-model="sourceDatasetName" size="small" class="w-full" filterable data-testid="source-dataset-name">
                      <el-option
                        v-for="option in sourceDatasetOptions"
                        :key="option.value"
                        :label="option.label"
                        :value="option.value"
                      />
                    </el-select>
                  </div>
                  <div class="mt-3">
                    <div class="mb-1 text-[11px] text-slate-500">成员数据源（可多选，留空表示全部）</div>
                    <el-select
                      v-model="sourceMemberDatasourceIds"
                      size="small"
                      class="w-full"
                      multiple
                      filterable
                      allow-create
                      default-first-option
                      placeholder="全部成员数据源"
                    >
                      <el-option
                        v-for="option in sourceMemberOptions"
                        :key="option.value"
                        :label="option.label"
                        :value="option.value"
                      />
                    </el-select>
                  </div>
                  <div class="mt-3">
                    <div class="mb-1 text-[11px] text-slate-500">读取行数上限</div>
                    <el-input
                      v-model="sourceLimit"
                      size="small"
                      type="number"
                      min="1"
                      :placeholder="String(DEFAULT_SOURCE_LIMIT)"
                      data-testid="source-limit"
                    />
                  </div>
                </template>

                <template v-else-if="selectedPipelineNode.node_type === 'transform'">
                  <div class="mt-3">
                    <div class="mb-1 text-[11px] text-slate-500">Transform 类型</div>
                    <el-select v-model="transformType" size="small" class="w-full" data-testid="transform-type">
                      <el-option label="filter（行过滤）" value="filter" />
                      <el-option label="aggregate（分组聚合）" value="aggregate" />
                    </el-select>
                  </div>
                  <template v-if="transformType === 'filter'">
                    <div class="mt-3">
                      <div class="mb-1 text-[11px] text-slate-500">过滤列</div>
                      <el-input v-model="filterColumn" size="small" placeholder="如 status" data-testid="filter-column" />
                    </div>
                    <div class="mt-3">
                      <div class="mb-1 text-[11px] text-slate-500">操作符</div>
                      <el-select v-model="filterOperator" size="small" class="w-full" data-testid="filter-operator">
                        <el-option v-for="op in FILTER_OPERATORS" :key="op" :label="op" :value="op" />
                      </el-select>
                    </div>
                    <div class="mt-3">
                      <div class="mb-1 text-[11px] text-slate-500">过滤值</div>
                      <el-input v-model="filterValue" size="small" placeholder="如 shipped / 100" data-testid="filter-value" />
                    </div>
                  </template>
                  <template v-else>
                    <div class="mt-3">
                      <div class="mb-1 text-[11px] text-slate-500">分组列（逗号分隔，可空）</div>
                      <el-input v-model="aggregateGroupByText" size="small" placeholder="如 carrier,status" data-testid="aggregate-group-by" />
                    </div>
                    <div class="mt-3">
                      <div class="mb-1 text-[11px] text-slate-500">聚合列</div>
                      <el-input v-model="aggregateMetricColumn" size="small" placeholder="如 amount" data-testid="aggregate-column" />
                    </div>
                    <div class="mt-3">
                      <div class="mb-1 text-[11px] text-slate-500">聚合函数</div>
                      <el-select v-model="aggregateMetricFunction" size="small" class="w-full" data-testid="aggregate-function">
                        <el-option v-for="fn in AGGREGATE_FUNCTIONS" :key="fn" :label="fn" :value="fn" />
                      </el-select>
                    </div>
                    <div class="mt-3">
                      <div class="mb-1 text-[11px] text-slate-500">输出列名（可空）</div>
                      <el-input v-model="aggregateMetricAlias" size="small" placeholder="如 total_amount" data-testid="aggregate-alias" />
                    </div>
                  </template>
                </template>

                <template v-else>
                  <div class="mt-3">
                    <div class="mb-1 text-[11px] text-slate-500">输出 Dataset 名称</div>
                    <el-input v-model="outputDatasetName" size="small" data-testid="output-dataset-name" />
                  </div>
                  <div class="mt-3">
                    <div class="mb-1 text-[11px] text-slate-500">输出层级</div>
                    <el-select v-model="outputLayer" size="small" class="w-full" data-testid="output-layer">
                      <el-option label="cleaned" value="cleaned" />
                      <el-option label="enriched" value="enriched" />
                    </el-select>
                  </div>
                  <div class="mt-3">
                    <div class="mb-1 text-[11px] text-slate-500">输出展示名</div>
                    <el-input v-model="outputDisplayName" size="small" data-testid="output-display-name" />
                  </div>
                </template>
              </template>
              <div v-else class="py-8 text-center text-xs text-slate-400">点击 DAG 节点查看并编辑配置</div>
            </div>
          </div>

          <div class="mt-3 rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-slate-600">运行记录</span>
              <el-button link size="small" @click="refreshActiveRuns">刷新</el-button>
            </div>
            <el-table :data="activePipeline.runs || []" size="small" border class="mt-2">
              <el-table-column prop="run_id" label="Run" min-width="200" />
              <el-table-column prop="pipeline_version" label="定义版本" width="90" />
              <el-table-column label="状态" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="runTagType(row.run_status)">{{ runStatusText(row.run_status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="输出 Dataset" min-width="150">
                <template #default="{ row }">{{ row.output_summary?.dataset_name || '-' }}</template>
              </el-table-column>
              <el-table-column label="输出行数" width="100">
                <template #default="{ row }">{{ row.output_summary?.row_count ?? '-' }}</template>
              </el-table-column>
              <el-table-column prop="error_message" label="错误" min-width="160" />
              <el-table-column prop="create_time" label="运行时间" width="170" />
            </el-table>
          </div>
        </template>

        <div v-else class="flex h-full min-h-64 flex-col items-center justify-center gap-2 text-center">
          <i class="fa-solid fa-diagram-project text-2xl text-slate-300"></i>
          <div class="text-xs text-slate-400">从左侧选择一个 Pipeline，或创建一个新的 Pipeline 开始编排</div>
          <el-button size="small" type="primary" plain @click="openCreatePipeline">创建 Pipeline</el-button>
        </div>
      </section>
    </div>

    <el-dialog v-model="detailVisible" width="min(900px, 92vw)" :title="detail?.dataset_name || 'Dataset 详情'">
      <template v-if="detail">
        <div class="grid grid-cols-2 gap-3 text-xs">
          <div><span class="text-slate-400">展示名：</span>{{ detail.display_name }}</div>
          <div><span class="text-slate-400">状态：</span>{{ detail.status }}</div>
          <div>
            <span class="text-slate-400">层级：</span>
            <el-tag size="small" :type="layerTagType(detail.dataset_layer)">
              {{ detail.dataset_layer || 'source' }}
            </el-tag>
          </div>
          <div>
            <span class="text-slate-400">来源：</span>
            <el-tag size="small" effect="plain">{{ detail.dataset_origin || 'datasource' }}</el-tag>
          </div>
          <div><span class="text-slate-400">主键：</span>{{ (detail.primary_key || []).join(', ') || '-' }}</div>
          <div><span class="text-slate-400">列数：</span>{{ (detail.columns || []).length }}</div>
        </div>
        <div class="mt-4 text-xs font-medium text-slate-600">成员绑定</div>
        <el-table :data="detail.bindings || []" size="small" border class="mt-1">
          <el-table-column prop="member_datasource_id" label="成员数据源" />
          <el-table-column prop="physical_table" label="物理表" />
          <el-table-column prop="status" label="状态" width="100" />
        </el-table>
        <div class="mt-4 text-xs font-medium text-slate-600">版本</div>
        <el-table :data="detail.versions || []" size="small" border class="mt-1">
          <el-table-column prop="version_no" label="版本" width="80" />
          <el-table-column prop="version_id" label="版本ID" />
          <el-table-column prop="row_count" label="行数" width="90" />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column prop="create_time" label="创建时间" width="170" />
        </el-table>
      </template>
    </el-dialog>

    <el-dialog
      v-model="pipelineDialogVisible"
      width="min(760px, 94vw)"
      title="创建 Pipeline"
    >
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">Pipeline 名称</div>
          <el-input v-model="pipelineForm.pipeline_name" size="small" data-testid="create-pipeline-name" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">展示名</div>
          <el-input v-model="pipelineForm.display_name" size="small" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">目标层</div>
          <el-select v-model="pipelineForm.target_layer" size="small" class="w-full">
            <el-option label="cleaned" value="cleaned" />
            <el-option label="enriched" value="enriched" />
          </el-select>
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">输入 Dataset</div>
          <el-select v-model="pipelineForm.source_dataset_name" size="small" class="w-full" filterable>
            <el-option v-for="item in sourceDatasetOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
        </div>
        <div class="col-span-2">
          <div class="mb-1 text-xs text-slate-500">描述</div>
          <el-input v-model="pipelineForm.description" size="small" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">Transform</div>
          <el-select v-model="pipelineForm.transform_type" size="small" class="w-full">
            <el-option label="filter（行过滤）" value="filter" />
            <el-option label="aggregate（分组聚合）" value="aggregate" />
          </el-select>
        </div>
        <template v-if="pipelineForm.transform_type === 'filter'">
          <div>
            <div class="mb-1 text-xs text-slate-500">过滤列</div>
            <el-input v-model="pipelineForm.filter_column" size="small" placeholder="逻辑列名，如 status" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">操作符</div>
            <el-select v-model="pipelineForm.filter_operator" size="small" class="w-full">
              <el-option v-for="op in FILTER_OPERATORS" :key="op" :label="op" :value="op" />
            </el-select>
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">过滤值</div>
            <el-input v-model="pipelineForm.filter_value" size="small" placeholder="如 shipped / 100" />
          </div>
        </template>
        <template v-else>
          <div>
            <div class="mb-1 text-xs text-slate-500">分组列（逗号分隔，可空）</div>
            <el-input v-model="pipelineForm.group_by" size="small" placeholder="如 carrier,status" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">聚合列</div>
            <el-input v-model="pipelineForm.metric_column" size="small" placeholder="如 amount" />
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">聚合函数</div>
            <el-select v-model="pipelineForm.metric_function" size="small" class="w-full">
              <el-option v-for="fn in AGGREGATE_FUNCTIONS" :key="fn" :label="fn" :value="fn" />
            </el-select>
          </div>
          <div>
            <div class="mb-1 text-xs text-slate-500">输出列名（可空）</div>
            <el-input v-model="pipelineForm.metric_alias" size="small" placeholder="如 total_amount" />
          </div>
        </template>
        <div>
          <div class="mb-1 text-xs text-slate-500">输出 Dataset 名称</div>
          <el-input v-model="pipelineForm.output_dataset_name" size="small" />
        </div>
        <div>
          <div class="mb-1 text-xs text-slate-500">输出展示名（可空）</div>
          <el-input v-model="pipelineForm.output_display_name" size="small" />
        </div>
      </div>
      <template #footer>
        <el-button size="small" @click="pipelineDialogVisible = false">取消</el-button>
        <el-button size="small" type="primary" :loading="savingPipeline" @click="saveCreatePipeline">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
