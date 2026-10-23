<script setup lang="ts">
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
import type {
  DataSource,
  DatasetLayer,
  DatasetOrigin,
  DatasetPipelineItem,
  DatasetPipelineLayer,
  DatasetPipelineNodePayload,
  DatasetPipelineRun,
  DatasetPipelineUpsertPayload,
  LogicalDatasetItem
} from '../types'

const FILTER_OPERATORS = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains']
const AGGREGATE_FUNCTIONS = ['sum', 'avg', 'min', 'max', 'count']

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
const pipelineDetail = ref<DatasetPipelineItem | null>(null)
const pipelineDetailVisible = ref(false)
const pipelineDialogVisible = ref(false)
const savingPipeline = ref(false)
const buildingPipeline = ref('')
const editingPipelineName = ref('')
const hasLoaded = ref(false)

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

const switchView = async (view: 'datasets' | 'pipelines') => {
  activeView.value = view
  if (view === 'pipelines') await loadPipelines()
  if (view === 'datasets') await load()
}

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
    if (res.status === 'success') pipelines.value = res.data.pipelines || []
  } finally {
    pipelineLoading.value = false
  }
}

const ensureLoaded = async () => {
  if (hasLoaded.value) return
  hasLoaded.value = true
  await Promise.all([load(), loadDatasources(), loadPipelines()])
}

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

const readString = (config: Record<string, unknown>, key: string): string => {
  const value = config[key]
  return typeof value === 'string' ? value : ''
}

const readStringArray = (config: Record<string, unknown>, key: string): string[] => {
  const value = config[key]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

const readRecordArray = (config: Record<string, unknown>, key: string): Record<string, unknown>[] => {
  const value = config[key]
  if (!Array.isArray(value)) return []
  return value.filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
}

const resetPipelineForm = () => {
  Object.assign(pipelineForm, createEmptyForm())
  editingPipelineName.value = ''
}

const openCreatePipeline = () => {
  resetPipelineForm()
  pipelineDialogVisible.value = true
}

const fillTransformForm = (config: Record<string, unknown>) => {
  const transformType = readString(config, 'transform_type')
  if (transformType === 'aggregate') {
    pipelineForm.transform_type = 'aggregate'
    pipelineForm.group_by = readStringArray(config, 'group_by').join(',')
    const metric = readRecordArray(config, 'metrics')[0] || {}
    pipelineForm.metric_column = readString(metric, 'column')
    pipelineForm.metric_function = readString(metric, 'function') || 'sum'
    pipelineForm.metric_alias = readString(metric, 'alias')
    return
  }
  pipelineForm.transform_type = 'filter'
  const condition = readRecordArray(config, 'conditions')[0] || {}
  pipelineForm.filter_column = readString(condition, 'column')
  pipelineForm.filter_operator = readString(condition, 'operator') || 'eq'
  const value = condition.value
  pipelineForm.filter_value = value === undefined || value === null ? '' : String(value)
}

const openEditPipeline = async (row: DatasetPipelineItem) => {
  const res = await getAdminPipeline(row.pipeline_name)
  if (res.status !== 'success') {
    ElMessage.error(res.msg || '加载 Pipeline 失败')
    return
  }
  const pipeline = res.data
  resetPipelineForm()
  editingPipelineName.value = pipeline.pipeline_name
  pipelineForm.pipeline_name = pipeline.pipeline_name
  pipelineForm.display_name = pipeline.display_name || ''
  pipelineForm.description = pipeline.description || ''
  pipelineForm.target_layer = pipeline.target_layer || 'cleaned'
  const sourceNode = (pipeline.nodes || []).find((node) => node.node_type === 'source')
  const transformNode = (pipeline.nodes || []).find((node) => node.node_type === 'transform')
  const outputNode = (pipeline.nodes || []).find((node) => node.node_type === 'output')
  pipelineForm.source_dataset_name = readString(sourceNode?.config || {}, 'dataset_name')
  pipelineForm.output_dataset_name = readString(outputNode?.config || {}, 'dataset_name')
  pipelineForm.output_display_name = readString(outputNode?.config || {}, 'display_name')
  fillTransformForm(transformNode?.config || {})
  pipelineDialogVisible.value = true
}

const openPipelineDetail = async (row: DatasetPipelineItem) => {
  const res = await getAdminPipeline(row.pipeline_name)
  if (res.status === 'success') {
    pipelineDetail.value = res.data
    pipelineDetailVisible.value = true
  }
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
  return {
    transform_type: 'aggregate',
    group_by: pipelineForm.group_by
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    metrics: [
      {
        column: pipelineForm.metric_column.trim(),
        function: pipelineForm.metric_function,
        alias:
          pipelineForm.metric_alias.trim() || `${pipelineForm.metric_function}_${pipelineForm.metric_column.trim()}`
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

const savePipeline = async () => {
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
      config: { dataset_name: pipelineForm.source_dataset_name, limit: 5000 }
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
    if (res.status === 'success') {
      ElMessage.success('保存成功')
      pipelineDialogVisible.value = false
      await Promise.all([loadPipelines(), load()])
    } else {
      ElMessage.error(res.msg || '保存失败')
    }
  } catch (err) {
    console.error('保存 Pipeline 失败:', err)
  } finally {
    savingPipeline.value = false
  }
}

const buildPipeline = async (row: DatasetPipelineItem) => {
  try {
    await ElMessageBox.confirm(
      `确定构建 Pipeline「${row.pipeline_name}」？将生成新的输出 Dataset 版本。`,
      '构建 Pipeline',
      { type: 'warning' }
    )
  } catch {
    return
  }
  buildingPipeline.value = row.pipeline_name
  try {
    const res = await buildAdminPipeline(row.pipeline_name)
    if (res.status === 'success') {
      const run: DatasetPipelineRun = res.data
      ElMessage.success(`构建完成：输出 ${run.output_summary?.row_count ?? 0} 行`)
      await Promise.all([loadPipelines(), load()])
      if (pipelineDetailVisible.value && pipelineDetail.value?.pipeline_name === row.pipeline_name) {
        await openPipelineDetail(row)
      }
    } else {
      ElMessage.error(res.msg || '构建失败')
    }
  } catch (err) {
    console.error('构建 Pipeline 失败:', err)
  } finally {
    buildingPipeline.value = ''
  }
}

const removePipeline = async (row: DatasetPipelineItem) => {
  try {
    await ElMessageBox.confirm(`确定删除 Pipeline「${row.pipeline_name}」？`, '删除 Pipeline', { type: 'warning' })
  } catch {
    return
  }
  const res = await deleteAdminPipeline(row.pipeline_name)
  if (res.status === 'success') {
    ElMessage.success('删除成功')
    await loadPipelines()
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
                : 'Pipeline：输入 Dataset → transform → 输出 cleaned / enriched Dataset，带版本、事务、血缘'
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
        <el-button size="small" type="primary" @click="openCreatePipeline">创建 Pipeline</el-button>
        <el-button size="small" :loading="pipelineLoading" @click="loadPipelines">刷新</el-button>
        <span class="ml-auto text-[11px] text-slate-400">{{ pipelines.length }} 个 Pipeline</span>
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

    <div v-else class="min-h-0 flex-1 overflow-auto p-5">
      <div class="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <el-table v-loading="pipelineLoading" :data="pipelines" size="small" border>
        <el-table-column prop="pipeline_name" label="Pipeline" min-width="160" />
        <el-table-column prop="display_name" label="展示名" min-width="140" />
        <el-table-column label="目标层" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="layerTagType(row.target_layer)">{{ row.target_layer }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="current_version_no" label="定义版本" width="90" />
        <el-table-column prop="output_dataset_name" label="输出 Dataset" min-width="160" />
        <el-table-column label="最近运行" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.last_run" size="small" :type="runTagType(row.last_run.run_status)">
              {{ runStatusText(row.last_run.run_status) }}
            </el-tag>
            <span v-else class="text-xs text-slate-400">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="update_time" label="更新时间" width="170" />
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" @click="openPipelineDetail(row)">详情</el-button>
            <el-button link size="small" type="primary" @click="openEditPipeline(row)">编辑</el-button>
            <el-button
              link
              size="small"
              type="success"
              :loading="buildingPipeline === row.pipeline_name"
              @click="buildPipeline(row)"
            >
              构建
            </el-button>
            <el-button link size="small" type="danger" @click="removePipeline(row)">删除</el-button>
          </template>
        </el-table-column>
        </el-table>
      </div>
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
      v-model="pipelineDetailVisible"
      width="min(980px, 94vw)"
      :title="pipelineDetail?.pipeline_name || 'Pipeline 详情'"
    >
      <template v-if="pipelineDetail">
        <div class="grid grid-cols-2 gap-3 text-xs">
          <div><span class="text-slate-400">展示名：</span>{{ pipelineDetail.display_name || '-' }}</div>
          <div><span class="text-slate-400">状态：</span>{{ pipelineDetail.status }}</div>
          <div><span class="text-slate-400">目标层：</span>{{ pipelineDetail.target_layer }}</div>
          <div><span class="text-slate-400">定义版本：</span>{{ pipelineDetail.current_version_no }}</div>
          <div><span class="text-slate-400">输出 Dataset：</span>{{ pipelineDetail.output_dataset_name || '-' }}</div>
          <div><span class="text-slate-400">描述：</span>{{ pipelineDetail.description || '-' }}</div>
        </div>
        <div class="mt-4 text-xs font-medium text-slate-600">节点</div>
        <el-table :data="pipelineDetail.nodes || []" size="small" border class="mt-1">
          <el-table-column prop="node_id" label="节点" width="120" />
          <el-table-column prop="node_type" label="类型" width="100" />
          <el-table-column label="配置">
            <template #default="{ row }">{{ JSON.stringify(row.config) }}</template>
          </el-table-column>
        </el-table>
        <div class="mt-4 text-xs font-medium text-slate-600">运行记录</div>
        <el-table :data="pipelineDetail.runs || []" size="small" border class="mt-1">
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
      </template>
    </el-dialog>

    <el-dialog
      v-model="pipelineDialogVisible"
      width="min(760px, 94vw)"
      :title="editingPipelineName ? `编辑 Pipeline：${editingPipelineName}` : '创建 Pipeline'"
    >
      <div class="grid grid-cols-2 gap-3">
        <div>
          <div class="mb-1 text-xs text-slate-500">Pipeline 名称</div>
          <el-input v-model="pipelineForm.pipeline_name" size="small" :disabled="!!editingPipelineName" />
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
        <el-button size="small" type="primary" :loading="savingPipeline" @click="savePipeline">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
