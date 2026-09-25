import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ModelPicker from '../ModelPicker.vue'

type ModelPickerProps = InstanceType<typeof ModelPicker>['$props']

const mountPicker = (props: ModelPickerProps) => mount(ModelPicker, { props })

const openMenu = async (wrapper: ReturnType<typeof mountPicker>) => {
  await wrapper.find('[title="模型设置"]').trigger('click')
  return wrapper
}

const findLevelButtons = async (wrapper: ReturnType<typeof mountPicker>) => {
  await wrapper.find('[title="模型设置"]').trigger('click')
  return wrapper.findAll('button').filter(b => (b.attributes('title') ?? '').startsWith('思考档位'))
}

describe('ModelPicker 模型选择', () => {
  it('选中模型并回显名称，未选时显示系统默认', async () => {
    const wrapper = await openMenu(mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A' }, { id: 'model-b', label: '模型 B' }],
    }))

    expect(wrapper.find('.model-trigger-label').text()).toBe('模型 A')

    const optionB = wrapper.findAll('button').find(b => b.text() === '模型 B')!
    await optionB.trigger('click')

    expect(wrapper.emitted('update:selectedModel')?.[0]).toEqual(['model-b'])
  })

  it('allowEmpty 时提供「系统默认」选项，选择后回退空值', async () => {
    const wrapper = await openMenu(mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A' }],
      allowEmpty: true,
    }))

    const systemDefault = wrapper.findAll('button').find(b => b.text() === '系统默认')!
    await systemDefault.trigger('click')

    expect(wrapper.emitted('update:selectedModel')?.[0]).toEqual([''])
  })

  it('未选模型时触发器展示「系统默认」', () => {
    const wrapper = mountPicker({ selectedModel: '', modelOptions: [], allowEmpty: true })

    expect(wrapper.find('.model-trigger-label').text()).toBe('系统默认')
  })
})

describe('ModelPicker 思考档位（单选形态）', () => {
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

    const buttons = await findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['关闭', '开启'])
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('开启')
  })

  it('点击「开启」从未触碰态发出显式 true', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: false, default_effort: null })

    const buttons = await findLevelButtons(wrapper)
    await buttons.find(b => b.text() === '开启')!.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
  })

  it('点击「关闭」发出显式 false', async () => {
    const wrapper = mountWithThinking(true, { mode: 'optional', efforts: ['low'], default_on: true, default_effort: 'low' })

    const buttons = await findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['关闭', '低'])
    await buttons.find(b => b.text() === '关闭')!.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([false])
    expect(wrapper.emitted('update:thinkingEffort')).toBeUndefined()
  })

  it('effort 档点击同时发出开启与档位，default_effort 高亮', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: ['low', 'medium', 'high'], default_on: true, default_effort: 'high' })

    const buttons = await findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['关闭', '低', '中', '高'])
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('高')

    await buttons.find(b => b.text() === '低')!.trigger('click')
    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
    expect(wrapper.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })

  it('forced 模型恒开：只列 efforts 档（无「关闭」），default_effort 高亮', async () => {
    const wrapper = mountWithThinking(false, { mode: 'forced', efforts: ['low', 'high'], default_on: false, default_effort: 'high' })

    const buttons = await findLevelButtons(wrapper)
    expect(buttons.map(b => b.text())).toEqual(['低', '高'])
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('高')

    await buttons.find(b => b.text() === '低')!.trigger('click')
    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
    expect(wrapper.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })

  it('none 模型不支持思考：无档位按钮且不发出事件', async () => {
    const wrapper = mountWithThinking(true, { mode: 'none', efforts: [], default_on: false, default_effort: null })

    await wrapper.find('[title="模型设置"]').trigger('click')

    expect(wrapper.findAll('button').filter(b => (b.attributes('title') ?? '').startsWith('思考档位')).length).toBe(0)
    expect(wrapper.text()).toContain('当前模型不支持深度思考')
    expect(wrapper.emitted('update:enableDeepThinking')).toBeUndefined()
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

describe('ModelPicker 定时任务组合（allowEmpty + 档位形态）', () => {
  const mountSchedule = (enableDeepThinking: boolean | null = null) =>
    mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A', thinking: { mode: 'optional', efforts: ['low', 'high'], default_on: false, default_effort: 'high' } }],
      enableDeepThinking,
      thinkingEffort: null,
      allowEmpty: true,
    })

  it('allowEmpty 与思考档位共存，交互与主对话一致', async () => {
    // 未触碰：当前档回退 default_on（false → 「关闭」高亮），点「低」发出显式 true + low
    const untouched = mountSchedule()
    const buttons = await findLevelButtons(untouched)
    expect(buttons.find(b => b.classes().includes('text-blue-600'))?.text()).toBe('关闭')

    await buttons.find(b => b.text() === '低')!.trigger('click')
    expect(untouched.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
    expect(untouched.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })

  it('选「系统默认」模型后思考档位不可知，展示不支持文案', async () => {
    const wrapper = mountSchedule(true)
    await wrapper.find('[title="模型设置"]').trigger('click')

    const systemDefault = wrapper.findAll('button').find(b => b.text() === '系统默认')!
    await systemDefault.trigger('click')

    expect(wrapper.emitted('update:selectedModel')?.[0]).toEqual([''])
    // 无模型选中时档位不可知，不展示档位按钮
    expect(wrapper.findAll('button').filter(b => (b.attributes('title') ?? '').startsWith('思考档位')).length).toBe(0)
  })
})
