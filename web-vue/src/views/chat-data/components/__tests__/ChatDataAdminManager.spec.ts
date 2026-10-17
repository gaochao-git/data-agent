import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'

const apiMocks = vi.hoisted(() => ({
  getAdminDatasources: vi.fn(),
}))

const elMocks = vi.hoisted(() => ({
  info: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: { info: elMocks.info },
}))

import ChatDataAdminManager from '../ChatDataAdminManager.vue'

// 子面板桩：暴露 ensureLoaded/openGroupModal 供容器懒加载调用，根节点带 data-panel 便于断言
const panelMocks = vi.hoisted(() => ({
  datasource: { openGroupModal: vi.fn() },
  enum: { ensureLoaded: vi.fn() },
  ontology: { ensureLoaded: vi.fn() },
  sqlHistory: { ensureLoaded: vi.fn() },
  schedule: { ensureLoaded: vi.fn() },
  plugins: { ensureLoaded: vi.fn() },
  setting: { ensureLoaded: vi.fn() },
}))

const panelStub = (name: string, key: keyof typeof panelMocks) =>
  defineComponent({
    name,
    setup(_, { expose }) {
      expose(panelMocks[key])
    },
    template: `<div :data-panel="'${key}'" />`,
  })

const mountManager = () =>
  mount(ChatDataAdminManager, {
    props: { modelValue: true },
    global: {
      stubs: {
        'el-dialog': { template: '<div><slot name="header" /><slot /></div>' },
        'el-button': { template: '<button type="button" @click="$emit(\'click\')"><slot /></button>' },
        DataSourceManager: panelStub('DataSourceManager', 'datasource'),
        GlobalEnumAdminPanel: panelStub('GlobalEnumAdminPanel', 'enum'),
        OntologyAdminPanel: panelStub('OntologyAdminPanel', 'ontology'),
        GlobalSqlHistory: panelStub('GlobalSqlHistory', 'sqlHistory'),
        AgentScheduleAdminPanel: panelStub('AgentScheduleAdminPanel', 'schedule'),
        PluginAdminPanel: panelStub('PluginAdminPanel', 'plugins'),
        SettingAdminPanel: panelStub('SettingAdminPanel', 'setting'),
      },
    },
  })

const navButton = (wrapper: ReturnType<typeof mountManager>, label: string) => {
  const button = wrapper.findAll('button').find(b => b.text().trim() === label)
  if (!button) throw new Error(`导航项不存在: ${label}`)
  return button
}

const isPanelVisible = (wrapper: ReturnType<typeof mountManager>, panel: string) =>
  (wrapper.find(`[data-panel="${panel}"]`).attributes('style') ?? '').includes('display: none') === false

describe('ChatDataAdminManager 分组导航', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getAdminDatasources.mockResolvedValue({ status: 'success', data: { datasources: [] } })
  })

  it('左侧渲染三组导航，枚举/本体/血缘与数据源同组', () => {
    const wrapper = mountManager()
    const text = wrapper.text()
    for (const label of ['数据资产', '数据源管理', '枚举值', '本体', '数据血缘', '自动化', '定时任务', '插件管理', '系统', '运行配置', '全局历史 SQL']) {
      expect(text).toContain(label)
    }
    expect(text).not.toContain('元数据')
    // 数据血缘为规划中条目：带锁标记
    expect(navButton(wrapper, '数据血缘').find('i.fa-lock').exists()).toBe(true)
  })

  it('默认激活数据源管理，其余面板隐藏', () => {
    const wrapper = mountManager()
    expect(isPanelVisible(wrapper, 'datasource')).toBe(true)
    for (const panel of ['enum', 'ontology', 'sqlHistory', 'schedule', 'plugins', 'setting']) {
      expect(isPanelVisible(wrapper, panel)).toBe(false)
    }
  })

  it('点击本体切换面板并触发懒加载，重复进入不重复拉取由面板自管', async () => {
    const wrapper = mountManager()
    await navButton(wrapper, '本体').trigger('click')
    await flushPromises()

    expect(isPanelVisible(wrapper, 'ontology')).toBe(true)
    expect(panelMocks.ontology.ensureLoaded).toHaveBeenCalledTimes(1)
    expect(panelMocks.enum.ensureLoaded).not.toHaveBeenCalled()
  })

  it('禁用项（数据血缘）只提示不切换', async () => {
    const wrapper = mountManager()
    await navButton(wrapper, '数据血缘').trigger('click')
    await flushPromises()

    expect(elMocks.info).toHaveBeenCalledWith('数据血缘管理规划中')
    expect(isPanelVisible(wrapper, 'datasource')).toBe(true)
  })

  it('进入全局 SQL 历史先拉数据源选项再懒加载', async () => {
    const wrapper = mountManager()
    await navButton(wrapper, '全局历史 SQL').trigger('click')
    await flushPromises()

    expect(apiMocks.getAdminDatasources).toHaveBeenCalledWith({ include_tables: false })
    expect(panelMocks.sqlHistory.ensureLoaded).toHaveBeenCalledTimes(1)
    expect(isPanelVisible(wrapper, 'sqlHistory')).toBe(true)
  })
})
