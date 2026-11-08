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
  datasource: {},
  semantic: {},
  enum: { ensureLoaded: vi.fn() },
  sqlHistory: { ensureLoaded: vi.fn() },
  schedule: { ensureLoaded: vi.fn() },
  plugins: { ensureLoaded: vi.fn() },
  skills: { ensureLoaded: vi.fn() },
  models: { ensureLoaded: vi.fn() },
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
        SemanticModelAdminPanel: panelStub('SemanticModelAdminPanel', 'semantic'),
        GlobalEnumAdminPanel: panelStub('GlobalEnumAdminPanel', 'enum'),
        GlobalSqlHistory: panelStub('GlobalSqlHistory', 'sqlHistory'),
        AgentScheduleAdminPanel: panelStub('AgentScheduleAdminPanel', 'schedule'),
        PluginAdminPanel: panelStub('PluginAdminPanel', 'plugins'),
        SkillAdminPanel: panelStub('SkillAdminPanel', 'skills'),
        ModelAdminPanel: panelStub('ModelAdminPanel', 'models'),
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

  it('左侧渲染三组导航，枚举/血缘与数据源同组，大模型管理归智能体组', () => {
    const wrapper = mountManager()
    const text = wrapper.text()
    for (const label of ['数据资产', '数据源管理', '模型管理', '枚举值', '数据血缘', '智能体', '定时任务', '插件管理', '技能管理', '系统', '大模型管理', '运行配置', '历史 SQL']) {
      expect(text).toContain(label)
    }
    expect(text).not.toContain('元数据')
    // 数据血缘为规划中条目：带锁标记
    expect(navButton(wrapper, '数据血缘').find('i.fa-lock').exists()).toBe(true)
  })

  it('默认激活数据源管理，其余面板隐藏；新建按钮属于数据源面板而非弹窗头部', () => {
    const wrapper = mountManager()
    expect(isPanelVisible(wrapper, 'datasource')).toBe(true)
    for (const panel of ['semantic', 'enum', 'sqlHistory', 'schedule', 'plugins', 'skills', 'models', 'setting']) {
      expect(isPanelVisible(wrapper, panel)).toBe(false)
    }
    expect(wrapper.findAll('button').some(b => b.text().includes('新建'))).toBe(false)
  })

  it('点击大模型管理切换面板并触发懒加载', async () => {
    const wrapper = mountManager()
    await navButton(wrapper, '大模型管理').trigger('click')
    await flushPromises()

    expect(isPanelVisible(wrapper, 'models')).toBe(true)
    expect(panelMocks.models.ensureLoaded).toHaveBeenCalledTimes(1)
    expect(panelMocks.enum.ensureLoaded).not.toHaveBeenCalled()
  })

  it('点击技能管理切换面板并触发懒加载', async () => {
    const wrapper = mountManager()
    await navButton(wrapper, '技能管理').trigger('click')
    await flushPromises()

    expect(isPanelVisible(wrapper, 'skills')).toBe(true)
    expect(panelMocks.skills.ensureLoaded).toHaveBeenCalledTimes(1)
    expect(panelMocks.plugins.ensureLoaded).not.toHaveBeenCalled()
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
    await navButton(wrapper, '历史 SQL').trigger('click')
    await flushPromises()

    expect(apiMocks.getAdminDatasources).toHaveBeenCalledWith({ include_tables: false })
    expect(panelMocks.sqlHistory.ensureLoaded).toHaveBeenCalledTimes(1)
    expect(isPanelVisible(wrapper, 'sqlHistory')).toBe(true)
  })

  it('点击语义模型管理切换面板并先拉数据源选项', async () => {
    const wrapper = mountManager()
    await navButton(wrapper, '语义模型管理').trigger('click')
    await flushPromises()

    expect(apiMocks.getAdminDatasources).toHaveBeenCalledWith({ include_tables: false })
    expect(isPanelVisible(wrapper, 'semantic')).toBe(true)
    expect(isPanelVisible(wrapper, 'datasource')).toBe(false)
  })
})
