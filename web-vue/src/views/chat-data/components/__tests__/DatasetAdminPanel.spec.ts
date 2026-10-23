import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  DatasetPipelineItem,
  DatasetPipelineNodePayload,
  DatasetPipelineUpsertPayload,
  LogicalDatasetItem
} from '../../types'

// ---- API mock：覆盖组件 import 的全部函数 ----
const apiMocks = vi.hoisted(() => ({
  bootstrapAdminDatasets: vi.fn(),
  buildAdminPipeline: vi.fn(),
  deleteAdminDataset: vi.fn(),
  deleteAdminPipeline: vi.fn(),
  getAdminDataset: vi.fn(),
  getAdminDatasets: vi.fn(),
  getAdminDatasources: vi.fn(),
  getAdminPipeline: vi.fn(),
  getAdminPipelines: vi.fn(),
  upsertAdminPipeline: vi.fn()
}))

const elMocks = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  confirm: vi.fn()
}))

vi.mock('../../api', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: { success: elMocks.success, warning: elMocks.warning, error: elMocks.error, info: vi.fn() },
  ElMessageBox: { confirm: elMocks.confirm }
}))

import DatasetAdminPanel from '../DatasetAdminPanel.vue'

const PIPELINE_NAME = 'orders_pipeline'

const buildPipelineDetail = (version = 1): DatasetPipelineItem => ({
  id: 1,
  pipeline_id: 'pipeline_1',
  pipeline_name: PIPELINE_NAME,
  display_name: '订单清洗',
  description: '',
  target_layer: 'cleaned',
  status: 'experimental',
  current_version_no: version,
  output_dataset_name: 'orders_cleaned',
  nodes: [
    {
      node_id: 'src',
      node_type: 'source',
      node_name: '源数据',
      config: { dataset_name: 'orders', member_datasource_ids: ['member_1'], limit: 5000 }
    },
    {
      node_id: 'transform_1',
      node_type: 'transform',
      node_name: '过滤',
      config: { transform_type: 'filter', conditions: [{ column: 'status', operator: 'eq', value: 'shipped' }] }
    },
    {
      node_id: 'out',
      node_type: 'output',
      node_name: '输出',
      config: { dataset_name: 'orders_cleaned', layer: 'cleaned', display_name: '清洗订单' }
    }
  ],
  edges: [
    { edge_id: 'edge_1', from_node_id: 'src', to_node_id: 'transform_1' },
    { edge_id: 'edge_2', from_node_id: 'transform_1', to_node_id: 'out' }
  ],
  runs: []
})

const buildPipelineListItem = (): DatasetPipelineItem => ({
  id: 1,
  pipeline_id: 'pipeline_1',
  pipeline_name: PIPELINE_NAME,
  display_name: '订单清洗',
  description: '',
  target_layer: 'cleaned',
  status: 'experimental',
  current_version_no: 1,
  output_dataset_name: 'orders_cleaned',
  last_run: null
})

const buildDatasetItem = (): LogicalDatasetItem => ({
  id: 1,
  dataset_name: 'orders',
  display_name: '订单',
  description: '',
  primary_key: [],
  columns: [],
  status: 'experimental',
  dataset_layer: 'source',
  dataset_origin: 'datasource'
})

const toSavedPipeline = (payload: DatasetPipelineUpsertPayload): DatasetPipelineItem => {
  const nodes = payload.nodes.map((node) => ({
    node_id: node.node_id,
    node_type: node.node_type,
    node_name: node.node_name || node.node_id,
    config: node.config
  }))
  return {
    ...buildPipelineDetail(2),
    target_layer: payload.target_layer,
    nodes,
    edges: payload.edges.map((edge, index) => ({
      edge_id: edge.edge_id || `edge_${index + 1}`,
      from_node_id: edge.from_node_id,
      to_node_id: edge.to_node_id
    }))
  }
}

const mountPanel = () =>
  mount(DatasetAdminPanel, {
    global: {
      directives: { loading: {} },
      stubs: {
        'el-button': {
          props: ['loading', 'type', 'size', 'disabled', 'link', 'plain'],
          emits: ['click'],
          template: '<button type="button" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>'
        },
        'el-input': {
          props: ['modelValue', 'type', 'placeholder', 'clearable', 'size'],
          emits: ['update:modelValue'],
          template: '<input :value="modelValue ?? \'\'" @input="$emit(\'update:modelValue\', $event.target.value)" />'
        },
        'el-select': {
          props: ['modelValue', 'multiple', 'clearable', 'filterable', 'placeholder'],
          emits: ['update:modelValue'],
          template: '<select><slot /></select>'
        },
        'el-option': { template: '<option />' },
        'el-tag': { template: '<span class="tag-stub"><slot /></span>' },
        'el-table': { template: '<div class="table-stub" />' },
        'el-table-column': { template: '<div class="column-stub" />' },
        'el-dialog': {
          props: ['modelValue', 'title', 'width'],
          emits: ['update:modelValue'],
          template: '<div v-if="modelValue" class="dialog-stub"><slot /></div>'
        }
      }
    }
  })

type PanelWrapper = ReturnType<typeof mountPanel>

const findButton = (wrapper: PanelWrapper, label: string) => {
  const button = wrapper.findAll('button').find((item) => item.text().trim() === label)
  if (!button) throw new Error(`按钮不存在: ${label}`)
  return button
}

const openPipelineWorkspace = async (wrapper: PanelWrapper) => {
  await flushPromises()
  await findButton(wrapper, 'Pipeline').trigger('click')
  await flushPromises()
}

describe('DatasetAdminPanel Pipeline Builder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    elMocks.confirm.mockResolvedValue('confirm')
    apiMocks.getAdminDatasets.mockResolvedValue({ status: 'success', data: { datasets: [buildDatasetItem()] } })
    apiMocks.getAdminDatasources.mockResolvedValue({
      status: 'success',
      data: {
        datasources: [
          {
            id: 'group_1',
            group_name: '机构组',
            ds_description: '',
            ds_tables: [],
            member_sources: [{ member_datasource_id: 'member_1', org_name: '机构A' }]
          }
        ]
      }
    })
    apiMocks.getAdminPipelines.mockResolvedValue({ status: 'success', data: { pipelines: [buildPipelineListItem()] } })
    apiMocks.getAdminPipeline.mockResolvedValue({ status: 'success', data: buildPipelineDetail(1) })
    apiMocks.upsertAdminPipeline.mockImplementation((_name: string, payload: DatasetPipelineUpsertPayload) =>
      Promise.resolve({ status: 'success', data: toSavedPipeline(payload) })
    )
    apiMocks.buildAdminPipeline.mockResolvedValue({
      status: 'success',
      data: {
        run_id: 'run_1',
        pipeline_name: PIPELINE_NAME,
        pipeline_version: 1,
        run_status: 'succeeded',
        trigger_type: 'manual',
        output_summary: { dataset_name: 'orders_cleaned', row_count: 42 }
      }
    })
  })

  it('Pipeline 页签为 master-detail：左侧列表自动选中首个 Pipeline，右侧渲染可编辑 DAG', async () => {
    const wrapper = mountPanel()
    await openPipelineWorkspace(wrapper)

    expect(wrapper.find(`[data-pipeline-name="${PIPELINE_NAME}"]`).exists()).toBe(true)
    expect(wrapper.find('[data-node-id="src"]').exists()).toBe(true)
    expect(wrapper.find('[data-node-id="transform_1"]').exists()).toBe(true)
    expect(wrapper.find('[data-node-id="out"]').exists()).toBe(true)
    expect(apiMocks.getAdminPipeline).toHaveBeenCalledWith(PIPELINE_NAME)
    expect(wrapper.find('[data-testid="pipeline-version"]').text()).toContain('v1')
  })

  it('点击 source 节点可编辑配置，保存时 PUT 追加版本并回填新版本号', async () => {
    const wrapper = mountPanel()
    await openPipelineWorkspace(wrapper)

    await wrapper.find('[data-node-id="src"]').trigger('click')
    const limitInput = wrapper.find('[data-testid="source-limit"]')
    expect(limitInput.exists()).toBe(true)
    await limitInput.setValue('1000')
    expect(wrapper.find('[data-testid="pipeline-dirty"]').exists()).toBe(true)

    await wrapper.find('[data-testid="pipeline-save"]').trigger('click')
    await flushPromises()

    expect(apiMocks.upsertAdminPipeline).toHaveBeenCalledTimes(1)
    const [pipelineName, payload] = apiMocks.upsertAdminPipeline.mock.calls[0] as [string, DatasetPipelineUpsertPayload]
    expect(pipelineName).toBe(PIPELINE_NAME)
    const sourceNode = payload.nodes.find((node) => node.node_type === 'source') as DatasetPipelineNodePayload
    expect(sourceNode.config).toMatchObject({
      dataset_name: 'orders',
      member_datasource_ids: ['member_1'],
      limit: 1000
    })
    expect(payload.edges).toHaveLength(2)
    expect(elMocks.success).toHaveBeenCalled()
    expect(wrapper.find('[data-testid="pipeline-version"]').text()).toContain('v2')
  })

  it('保存前校验单链与节点配置：output 缺 dataset_name 时在 UI 提示且不调用 PUT', async () => {
    const wrapper = mountPanel()
    await openPipelineWorkspace(wrapper)

    await wrapper.find('[data-node-id="out"]').trigger('click')
    await wrapper.find('[data-testid="output-dataset-name"]').setValue('')
    await wrapper.find('[data-testid="pipeline-save"]').trigger('click')
    await flushPromises()

    expect(apiMocks.upsertAdminPipeline).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="pipeline-validation-error"]').text()).toContain(
      'output 节点请填写输出 Dataset 名称'
    )
    expect(elMocks.warning).toHaveBeenCalled()
  })

  it('构建选中 Pipeline：调用 build 接口并刷新运行记录', async () => {
    const wrapper = mountPanel()
    await openPipelineWorkspace(wrapper)

    await wrapper.find('[data-testid="pipeline-build"]').trigger('click')
    await flushPromises()

    expect(elMocks.confirm).toHaveBeenCalled()
    expect(apiMocks.buildAdminPipeline).toHaveBeenCalledWith(PIPELINE_NAME)
    expect(elMocks.success).toHaveBeenCalledWith(expect.stringContaining('构建完成'))
    // 初始选中 + 构建后重新加载详情
    expect(apiMocks.getAdminPipeline).toHaveBeenCalledTimes(2)
  })

  it('保留原创建表单作为新建入口', async () => {
    const wrapper = mountPanel()
    await openPipelineWorkspace(wrapper)

    await findButton(wrapper, '创建 Pipeline').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="create-pipeline-name"]').exists()).toBe(true)
  })
})
