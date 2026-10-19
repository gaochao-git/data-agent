import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ModelPicker from '../ModelPicker.vue'

type ModelPickerProps = InstanceType<typeof ModelPicker>['$props']

const mountPicker = (props: ModelPickerProps) => mount(ModelPicker, { props })

const isPanelOpen = (wrapper: ReturnType<typeof mountPicker>) =>
  wrapper.find('[data-model-picker-menu]').exists()

const openPanel = async (wrapper: ReturnType<typeof mountPicker>) => {
  if (!isPanelOpen(wrapper)) await wrapper.find('[title="模型设置"]').trigger('click')
  return wrapper
}

const findEntry = async (wrapper: ReturnType<typeof mountPicker>, label: string) => {
  await openPanel(wrapper)
  // 排除触发器按钮（title="模型设置"，其文本同样可能以「模型」开头）
  return wrapper.findAll('button').find(b => b.attributes('title') !== '模型设置' && b.text().startsWith(label))!
}

const openModelsView = async (wrapper: ReturnType<typeof mountPicker>) => {
  await (await findEntry(wrapper, '模型')).trigger('click')
  return wrapper
}

const openLevelsView = async (wrapper: ReturnType<typeof mountPicker>) => {
  await (await findEntry(wrapper, '推理等级')).trigger('click')
  return wrapper
}

const findLevelButtons = (wrapper: ReturnType<typeof mountPicker>) =>
  wrapper.findAll('button').filter(b => (b.attributes('title') ?? '').startsWith('思考档位'))

describe('ModelPicker 视觉能力标注', () => {
  it('支持视觉的模型在模型列表标注「视觉」，不支持的不标注', async () => {
    const wrapper = mountPicker({
      selectedModel: 'model-a',
      modelOptions: [
        { id: 'model-a', label: '模型 A', vlm: true },
        { id: 'model-b', label: '模型 B', vlm: false },
        { id: 'model-c', label: '模型 C' },
      ],
    })
    await openModelsView(wrapper)
    const badges = wrapper.findAll('span[title="支持图片视觉输入"]')
    expect(badges).toHaveLength(1)
    expect(badges[0]?.text()).toBe('视觉')
  })
})

describe('ModelPicker 模型选择', () => {
  it('主面板两行入口展示当前模型与等级，二级选中并回显', async () => {
    const wrapper = mountPicker({
      selectedModel: 'model-a',
      modelOptions: [
        { id: 'model-a', label: '模型 A', thinking: { mode: 'optional', efforts: ['low'], default_on: false, default_effort: null } },
        { id: 'model-b', label: '模型 B', thinking: { mode: 'optional', efforts: ['low'], default_on: false, default_effort: null } },
      ],
    })

    const panel = await openPanel(wrapper)
    expect(panel.text()).toContain('模型 A')

    await openModelsView(wrapper)
    const optionB = wrapper.findAll('button').find(b => b.text() === '模型 B')!
    await optionB.trigger('click')

    expect(wrapper.emitted('update:selectedModel')?.[0]).toEqual(['model-b'])
    // 模拟父组件回写后：回到主面板并回显新模型
    await wrapper.setProps({ selectedModel: 'model-b' })
    expect(wrapper.text()).toContain('模型 B')
    expect(wrapper.text()).toContain('推理等级')
  })

  it('allowEmpty 时二级提供「系统默认」选项，选择后回退空值', async () => {
    const wrapper = mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A' }],
      allowEmpty: true,
    })

    await openModelsView(wrapper)
    const systemDefault = wrapper.findAll('button').find(b => b.text() === '系统默认')!
    await systemDefault.trigger('click')

    expect(wrapper.emitted('update:selectedModel')?.[0]).toEqual([''])
  })

  it('未选模型时入口展示「系统默认」', async () => {
    const wrapper = mountPicker({ selectedModel: '', modelOptions: [], allowEmpty: true })

    const entry = await findEntry(wrapper, '模型')
    expect(entry.text()).toContain('系统默认')
  })
})

describe('ModelPicker 推理等级（两级筛选）', () => {
  const mountWithThinking = (
    enableDeepThinking: boolean | null,
    thinking?: { mode: 'none' | 'optional' | 'forced'; efforts: string[]; default_on: boolean; default_effort: string | null },
  ) => mountPicker({
    selectedModel: 'model-a',
    modelOptions: [{ id: 'model-a', label: '模型 A', thinking }],
    enableDeepThinking,
    thinkingEffort: null,
  })

  it('未触碰时当前档回退模型 default_on（开且无 efforts → 「开启」高亮）', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: true, default_effort: null })

    await openLevelsView(wrapper)
    const buttons = findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['关闭', '开启'])
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('开启')
  })

  it('点击「开启」从未触碰态发出显式 true', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: false, default_effort: null })

    await openLevelsView(wrapper)
    const buttons = findLevelButtons(wrapper)
    await buttons.find(b => b.text() === '开启')!.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
  })

  it('点击「关闭」发出显式 false', async () => {
    const wrapper = mountWithThinking(true, { mode: 'optional', efforts: ['low'], default_on: true, default_effort: 'low' })

    await openLevelsView(wrapper)
    const buttons = findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['关闭', '低'])
    await buttons.find(b => b.text() === '关闭')!.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([false])
    expect(wrapper.emitted('update:thinkingEffort')).toBeUndefined()
  })

  it('effort 档点击同时发出开启与档位，default_effort 高亮', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: ['low', 'medium', 'high'], default_on: true, default_effort: 'high' })

    await openLevelsView(wrapper)
    const buttons = findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['关闭', '低', '中', '高'])
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('高')

    await buttons.find(b => b.text() === '低')!.trigger('click')
    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
    expect(wrapper.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })

  it('forced 模型恒开：只列 efforts 档（无「关闭」），default_effort 高亮', async () => {
    const wrapper = mountWithThinking(false, { mode: 'forced', efforts: ['low', 'high'], default_on: false, default_effort: 'high' })

    await openLevelsView(wrapper)
    const buttons = findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['低', '高'])
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('高')

    await buttons.find(b => b.text() === '低')!.trigger('click')
    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
    expect(wrapper.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })

  it('none 模型：推理等级行禁用并显示「不支持」', async () => {
    const wrapper = mountWithThinking(true, { mode: 'none', efforts: [], default_on: false, default_effort: null })

    const panel = await openPanel(wrapper)
    const entry = wrapper.findAll('button').find(b => b.text().startsWith('推理等级'))!
    expect(entry.text()).toContain('不支持')
    expect(entry.attributes('disabled')).toBeDefined()
    expect(panel.emitted('update:enableDeepThinking')).toBeUndefined()
  })

  it('触发器内联展示当前档位文案', () => {
    const off = mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A', thinking: { mode: 'optional', efforts: ['low'], default_on: true, default_effort: null } }],
      enableDeepThinking: false,
    })
    expect(off.find('[title="模型设置"]').text()).toContain('关闭')

    const high = mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A', thinking: { mode: 'optional', efforts: ['low', 'high'], default_on: true, default_effort: 'high' } }],
      enableDeepThinking: null,
    })
    expect(high.find('[title="模型设置"]').text()).toContain('高')
  })
})

describe('ModelPicker 定时任务组合（allowEmpty + 两级筛选）', () => {
  const mountSchedule = (enableDeepThinking: boolean | null = null) =>
    mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A', thinking: { mode: 'optional', efforts: ['low', 'high'], default_on: false, default_effort: 'high' } }],
      enableDeepThinking,
      thinkingEffort: null,
      allowEmpty: true,
    })

  it('allowEmpty 与推理等级共存，交互与主对话一致', async () => {
    // 未触碰：当前档回退 default_on（false → 「关闭」高亮），点「低」发出显式 true + low
    const untouched = mountSchedule()
    await openLevelsView(untouched)
    const buttons = findLevelButtons(untouched)
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('关闭')

    await buttons.find(b => b.text() === '低')!.trigger('click')
    expect(untouched.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
    expect(untouched.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })

  it('选「系统默认」模型后推理等级不可知，入口禁用', async () => {
    const wrapper = mountSchedule(true)
    await openModelsView(wrapper)

    const systemDefault = wrapper.findAll('button').find(b => b.text() === '系统默认')!
    await systemDefault.trigger('click')

    expect(wrapper.emitted('update:selectedModel')?.[0]).toEqual([''])
    // 模拟父组件回写后：无模型选中时等级不可知，入口禁用
    await wrapper.setProps({ selectedModel: '' })
    const levelEntry = wrapper.findAll('button').find(b => b.attributes('title') !== '模型设置' && b.text().startsWith('推理等级'))!
    expect(levelEntry.attributes('disabled')).toBeDefined()
  })
})
