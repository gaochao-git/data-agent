import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getAdminSkills: vi.fn(),
  getAdminSkill: vi.fn(),
  getAdminSkillFile: vi.fn()
}))

const elMocks = vi.hoisted(() => ({ error: vi.fn() }))

vi.mock('../../api', () => apiMocks)
vi.mock('element-plus', () => ({ ElMessage: { error: elMocks.error } }))

import SkillAdminPanel from '../SkillAdminPanel.vue'
import type { SkillInfo } from '../../types'

const skills: SkillInfo[] = [
  {
    skill_id: 's1',
    name: 'weekly-report',
    title: '周报生成',
    description: '按周汇总经营数据',
    source: 'user',
    enabled: true,
    publish_status: 'published',
    visibility_type: 'private',
    current_version_no: 3,
    creator_name: '张三'
  },
  { skill_id: 'officecli', name: 'officecli', title: 'Office 技能', source: 'system' },
  {
    skill_id: 'plugin:fault-analysis',
    name: 'fault-analysis',
    title: '故障分析',
    source: 'plugin',
    plugin_name: 'ops-readonly'
  } as SkillInfo & { plugin_name: string }
]

const detail = {
  skill_id: 's1',
  name: 'weekly-report',
  title: '周报生成',
  source: 'user',
  publish_status: 'published',
  visibility_type: 'private',
  current_version_no: 3,
  creator_name: '张三',
  content: '# 周报生成\n\n按周汇总经营数据。',
  references: ['guide.md'],
  assets: []
}

const pluginDetail = {
  ...detail,
  skill_id: 'plugin:fault-analysis',
  name: 'fault-analysis',
  title: '故障分析',
  source: 'plugin',
  references: ['guide.md']
}

type PanelExpose = { ensureLoaded: () => void }
const mountPanel = () => mount(SkillAdminPanel)
const exposed = (wrapper: ReturnType<typeof mountPanel>): PanelExpose =>
  wrapper.vm as unknown as PanelExpose
const clickNode = async (wrapper: ReturnType<typeof mountPanel>, key: string) => {
  await wrapper.find(`[data-tree="${key}"]`).trigger('click')
  await flushPromises()
}

describe('SkillAdminPanel 左侧目录树 + 右侧只读内容', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    apiMocks.getAdminSkills.mockResolvedValue({ status: 'success', data: { skills, total: skills.length } })
    apiMocks.getAdminSkill.mockImplementation(async (key: string) => ({
      status: 'success',
      data: key.startsWith('plugin:') ? pluginDetail : detail
    }))
    apiMocks.getAdminSkillFile.mockResolvedValue({
      status: 'success',
      data: { dir_name: 'references', file_path: 'guide.md', content: '参考文档正文' }
    })
  })

  it('进入面板才拉取列表，默认展开来源分组并说明只读范围', async () => {
    const wrapper = mountPanel()
    expect(apiMocks.getAdminSkills).not.toHaveBeenCalled()

    exposed(wrapper).ensureLoaded()
    await flushPromises()

    expect(apiMocks.getAdminSkills).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-tree="group:system"]').exists()).toBe(true)
    expect(wrapper.find('[data-tree="group:user"]').exists()).toBe(true)
    expect(wrapper.find('[data-tree="group:plugin"]').exists()).toBe(true)
    expect(wrapper.find('[data-tree="skill:s1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('共 3 个技能')
    expect(wrapper.text()).toContain('只读总览')
    // 右侧不再铺开标题/标签/四列元信息，空间留给正文
    expect(wrapper.find('h3').exists()).toBe(false)
    expect(wrapper.find('dl').exists()).toBe(false)
    // 只读：不提供任何写操作入口
    for (const label of ['新建', '保存', '删除', '安装', '启停']) {
      expect(wrapper.text()).not.toContain(label)
    }
  })

  it('点击技能展开目录层级，右侧显示 SKILL.md 正文与元信息', async () => {
    const wrapper = mountPanel()
    exposed(wrapper).ensureLoaded()
    await flushPromises()

    await clickNode(wrapper, 'skill:s1')

    expect(apiMocks.getAdminSkill).toHaveBeenCalledWith('s1')
    expect(wrapper.find('[data-tree="skill:s1:SKILL.md"]').exists()).toBe(true)
    expect(wrapper.find('[data-tree="skill:s1:dir:references"]').exists()).toBe(true)
    expect(wrapper.find('[data-tree="skill:s1:dir:assets"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('# 周报生成')
    expect(wrapper.text()).toContain('v3')
    expect(wrapper.text()).toContain('张三')
  })

  it('展开 references 后点击文件按当前版本读取内容', async () => {
    const wrapper = mountPanel()
    exposed(wrapper).ensureLoaded()
    await flushPromises()
    await clickNode(wrapper, 'skill:s1')
    await clickNode(wrapper, 'skill:s1:dir:references')

    await clickNode(wrapper, 'skill:s1:dir:references:guide.md')

    expect(apiMocks.getAdminSkillFile).toHaveBeenCalledWith('s1', {
      dir_name: 'references',
      file_path: 'guide.md',
      version_no: 3
    })
    expect(wrapper.text()).toContain('参考文档正文')
    expect(wrapper.text()).toContain('guide.md')
    // 面包屑只显示路径，不带分组/目录的 (N) 计数
    const crumb = wrapper.find('[data-qa="admin-skill-content"] span[title]')
    expect(crumb.text()).toBe('用户技能 / 周报生成 / references / guide.md')
  })

  it('插件技能按插件分组挂树，附属文件不提供预览', async () => {
    const wrapper = mountPanel()
    exposed(wrapper).ensureLoaded()
    await flushPromises()

    expect(wrapper.find('[data-tree="plugin:group:ops-readonly"]').exists()).toBe(true)
    await clickNode(wrapper, 'plugin:group:ops-readonly')
    await clickNode(wrapper, 'skill:plugin:fault-analysis')
    await clickNode(wrapper, 'skill:plugin:fault-analysis:dir:references')
    await clickNode(wrapper, 'skill:plugin:fault-analysis:dir:references:guide.md')

    expect(apiMocks.getAdminSkillFile).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('插件技能的附属文件不支持在此预览')
  })

  it('关键词与来源筛选作用于目录树', async () => {
    const wrapper = mountPanel()
    exposed(wrapper).ensureLoaded()
    await flushPromises()

    await wrapper.find('input[aria-label="搜索技能"]').setValue('故障')
    expect(wrapper.findAll('[data-type="skill"]')).toHaveLength(1)
    expect(wrapper.find('[data-tree="skill:plugin:fault-analysis"]').exists()).toBe(true)

    await wrapper.find('input[aria-label="搜索技能"]').setValue('')
    await wrapper.find('select[aria-label="按来源筛选"]').setValue('user')
    expect(wrapper.find('[data-tree="group:system"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-type="skill"]')).toHaveLength(1)
    expect(wrapper.find('[data-tree="skill:s1"]').exists()).toBe(true)
  })

  it('列表接口失败时提示且不留半加载态', async () => {
    apiMocks.getAdminSkills.mockRejectedValue(new Error('network'))
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const wrapper = mountPanel()

    exposed(wrapper).ensureLoaded()
    await flushPromises()

    expect(elMocks.error).toHaveBeenCalledWith('获取技能总览失败，请稍后重试')
    expect(wrapper.text()).toContain('没有匹配的技能')
    consoleSpy.mockRestore()
  })
})

describe('技能管理布局:左栏控件 + 可拖拽分隔', () => {
  const dragEvent = (type: string, clientX: number) => {
    const event = new MouseEvent(type, { bubbles: true })
    Object.defineProperty(event, 'clientX', { value: clientX })
    return event
  }

  const mountWithMocks = () => mount(SkillAdminPanel)

  it('筛选控件位于左栏内部,右栏内容区不含控件', async () => {
    const wrapper = mountWithMocks()
    await flushPromises()
    const left = wrapper.find('[data-qa="admin-skill-tree"]')
    expect(left.exists()).toBe(true)
    expect(left.find('input[type="search"]').exists()).toBe(true)
    expect(left.find('select').exists()).toBe(true)
    const content = wrapper.find('[data-qa="admin-skill-content"]')
    expect(content.exists()).toBe(true)
    expect(content.find('input[type="search"]').exists()).toBe(false)
    expect(content.find('select').exists()).toBe(false)
    wrapper.unmount()
  })

  it('拖拽分隔条存在,pointer 拖动更新左栏宽度且受边界约束', async () => {
    const wrapper = mountWithMocks()
    await flushPromises()
    const splitter = wrapper.find('[data-qa="admin-skill-splitter"]')
    expect(splitter.exists()).toBe(true)
    const left = wrapper.find('[data-qa="admin-skill-tree"]')
    const before = (left.element as HTMLElement).style.width
    splitter.element.dispatchEvent(dragEvent('pointerdown', 400))
    window.dispatchEvent(dragEvent('pointermove', 520))
    window.dispatchEvent(dragEvent('pointerup', 520))
    await flushPromises()
    const after = (left.element as HTMLElement).style.width
    expect(after).not.toBe(before)
    expect(Number.parseInt(after, 10)).toBeGreaterThanOrEqual(280)
    expect(Number.parseInt(after, 10)).toBeLessThanOrEqual(640)
    wrapper.unmount()
  })

  it('向左拖动不低于 280 下限', async () => {
    const wrapper = mountWithMocks()
    await flushPromises()
    const splitter = wrapper.find('[data-qa="admin-skill-splitter"]')
    splitter.element.dispatchEvent(dragEvent('pointerdown', 400))
    window.dispatchEvent(dragEvent('pointermove', 40))
    window.dispatchEvent(dragEvent('pointerup', 40))
    await flushPromises()
    const width = Number.parseInt(
      (wrapper.find('[data-qa="admin-skill-tree"]').element as HTMLElement).style.width,
      10
    )
    expect(width).toBe(280)
    wrapper.unmount()
  })
})
