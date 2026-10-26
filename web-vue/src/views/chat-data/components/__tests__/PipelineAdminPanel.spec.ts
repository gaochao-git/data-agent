import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

vi.mock('../../api', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() }
}))

import PipelineAdminPanel from '../PipelineAdminPanel.vue'

describe('PipelineAdminPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getAdminPipelines.mockResolvedValue({ data: [] })
    apiMocks.getAdminDatasets.mockResolvedValue({ data: [] })
    apiMocks.getAdminDatasources.mockResolvedValue({ data: [] })
  })

  it('renders only the pipeline workbench without the dataset/pipeline tabs', async () => {
    const wrapper = mount(PipelineAdminPanel)
    await flushPromises()

    // 固定 Pipeline 视图：不出现数据集切换条与数据集筛选项
    expect(wrapper.text()).toContain('Dataset Pipeline')
    expect(wrapper.text()).toContain('Pipeline Builder')
    expect(wrapper.text()).not.toContain('从数据源注册')
    // 需要加载 Pipeline 列表；数据集列表仍会加载（source 节点选 Dataset 需要）
    expect(apiMocks.getAdminPipelines).toHaveBeenCalled()
    expect(apiMocks.getAdminDatasets).toHaveBeenCalled()
  })
})
