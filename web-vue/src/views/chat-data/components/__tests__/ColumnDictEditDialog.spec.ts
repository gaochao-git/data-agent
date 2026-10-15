import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getDatasourceColumnDict: vi.fn(),
  getAdminDatasourceColumnDict: vi.fn(),
  updateAdminDatasourceColumnDict: vi.fn(),
  generateAdminDatasourceColumnDict: vi.fn(),
  getAdminMetaEnums: vi.fn(),
  getAdminMetaEnum: vi.fn(),
  upsertAdminMetaEnum: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

import ColumnDictEditDialog from '../semantic/ColumnDictEditDialog.vue'

const columnDictResponse = {
  status: 'success',
  data: {
    dict: {
      enum_values: [{ value: '1', label: '有效' }],
      storage_format: 'tinyint',
      unit: '',
    },
  },
}

const mountDialog = (adminMode: boolean) => mount(ColumnDictEditDialog, {
  props: {
    visible: false,
    datasourceId: 'ds_a',
    tableName: 'orders',
    columnName: 'status',
    adminMode,
  },
  global: {
    stubs: {
      'el-dialog': {
        template: '<div><slot name="header" /><slot /></div>',
      },
    },
  },
})

describe('ColumnDictEditDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getDatasourceColumnDict.mockResolvedValue(columnDictResponse)
    apiMocks.getAdminDatasourceColumnDict.mockResolvedValue(columnDictResponse)
  })

  it('uses only the public GET endpoint and renders a read-only view for user models', async () => {
    const wrapper = mountDialog(false)

    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(apiMocks.getDatasourceColumnDict).toHaveBeenCalledWith('ds_a', 'orders', 'status')
    expect(apiMocks.getAdminDatasourceColumnDict).not.toHaveBeenCalled()
    expect(apiMocks.updateAdminDatasourceColumnDict).not.toHaveBeenCalled()
    expect(apiMocks.generateAdminDatasourceColumnDict).not.toHaveBeenCalled()
    expect(wrapper.findAll('input').map(input => input.element.value)).toContain('有效')
    expect(wrapper.text()).toContain('关闭')
    expect(wrapper.text()).not.toContain('AI 生成字段字典')
    expect(wrapper.text()).not.toContain('新增枚举')
    expect(wrapper.text()).not.toContain('保存字典')
    expect(wrapper.findAll('input').every(input => input.attributes('readonly') !== undefined)).toBe(true)
  })

  it('uses only the admin GET endpoint and keeps maintenance controls for system models', async () => {
    const wrapper = mountDialog(true)

    await wrapper.setProps({ visible: true })
    await flushPromises()

    expect(apiMocks.getAdminDatasourceColumnDict).toHaveBeenCalledWith('ds_a', 'orders', 'status')
    expect(apiMocks.getDatasourceColumnDict).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('AI 生成字段字典')
    expect(wrapper.text()).toContain('新增枚举')
    expect(wrapper.text()).toContain('保存字典')
    expect(wrapper.findAll('input').some(input => input.attributes('readonly') === undefined)).toBe(true)
  })
})
