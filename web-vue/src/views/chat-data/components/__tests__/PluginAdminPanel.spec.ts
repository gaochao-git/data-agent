import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PluginAdminPanel from '../PluginAdminPanel.vue'

// ---- API mock：覆盖组件 import 的全部函数 ----
const apiMocks = vi.hoisted(() => ({
  getAdminPlugins: vi.fn(),
  installPlugin: vi.fn(),
  setAdminPluginEnabled: vi.fn(),
  uninstallPlugin: vi.fn()
}))

vi.mock('../../api', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() }
}))

const mountPanel = () =>
  mount(PluginAdminPanel, {
    global: {
      stubs: {
        'el-upload': { template: '<div class="upload-stub"><slot /></div>' },
        'el-button': {
          props: ['loading', 'type', 'size', 'round'],
          emits: ['click'],
          template: '<button type="button" @click="$emit(\'click\')"><slot /></button>'
        },
        // 不渲染 table 的默认插槽：空列表场景下列模板不需要真实行数据
        'el-table': { template: '<div class="table-stub" />' },
        'el-table-column': { template: '<div class="column-stub" />' },
        'el-switch': { template: '<div class="switch-stub" />' },
        'el-tag': { template: '<span class="tag-stub"><slot /></span>' },
        'el-pagination': { template: '<div class="pagination-stub" />' }
      }
    }
  })

const findButton = (wrapper: ReturnType<typeof mountPanel>, label: string) => {
  const button = wrapper.findAll('button').find(item => item.text().includes(label))
  if (!button) throw new Error(`工具栏按钮不存在: ${label}`)
  return button
}

describe('PluginAdminPanel 工具栏', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getAdminPlugins.mockResolvedValue({ status: 'success', data: { plugins: [], total: 0 } })
  })

  // 回归：组件模板使用了 <AdminPanelToolbar> 但曾漏 import，Vue 运行时解析不到组件，
  // #actions 插槽（安装插件/刷新）整体不渲染；vue-tsc 与构建都不会报错，只能靠挂载测试兜住。
  it('渲染统一工具栏的安装插件与刷新动作', () => {
    const wrapper = mountPanel()
    expect(findButton(wrapper, '安装插件').exists()).toBe(true)
    expect(findButton(wrapper, '刷新').exists()).toBe(true)
  })

  it('刷新按钮触发插件列表拉取（工具栏动作插槽已正确挂载）', async () => {
    const wrapper = mountPanel()
    await findButton(wrapper, '刷新').trigger('click')
    await flushPromises()

    expect(apiMocks.getAdminPlugins).toHaveBeenCalledWith({ limit: 20, offset: 0 })
  })
})
