import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ModelPicker from '../ModelPicker.vue'

type ModelPickerProps = InstanceType<typeof ModelPicker>['$props']

const mountPicker = (props: ModelPickerProps) => mount(ModelPicker, { props })

const openMenu = async (wrapper: ReturnType<typeof mountPicker>) => {
  await wrapper.find('[title="模型设置"]').trigger('click')
  return wrapper
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

describe('ModelPicker 深度思考开关（toggle 形态）', () => {
  const mountWithThinking = (
    enableDeepThinking: boolean | null,
    thinking?: { mode: 'none' | 'optional' | 'forced'; efforts: string[]; default_on: boolean; default_effort: string | null },
  ) => mountPicker({
    selectedModel: 'model-a',
    modelOptions: [{ id: 'model-a', label: '模型 A', thinking }],
    enableDeepThinking,
    thinkingEffort: null,
  })
  const findSwitch = async (wrapper: ReturnType<typeof mountWithThinking>) => {
    await wrapper.find('[title="模型设置"]').trigger('click')
    return wrapper.find('button[role="switch"]')
  }

  it('用户未触碰时展示态回退模型 default_on', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: true, default_effort: null })

    expect((await findSwitch(wrapper)).attributes('aria-checked')).toBe('true')
  })

  it('首次点击从未触碰态发出显式布尔值', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: false, default_effort: null })

    await (await findSwitch(wrapper)).trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
  })

  it('forced 模型锁定开关且不发出事件', async () => {
    const wrapper = mountWithThinking(false, { mode: 'forced', efforts: ['high'], default_on: false, default_effort: 'high' })

    const sw = await findSwitch(wrapper)
    expect(sw.attributes('aria-checked')).toBe('true')
    expect(sw.attributes('aria-disabled')).toBe('true')

    await sw.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')).toBeUndefined()
  })

  it('none 模型锁定开关且不发出事件', async () => {
    const wrapper = mountWithThinking(true, { mode: 'none', efforts: [], default_on: false, default_effort: null })

    const sw = await findSwitch(wrapper)
    expect(sw.attributes('aria-checked')).toBe('false')
    expect(sw.attributes('aria-disabled')).toBe('true')

    await sw.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')).toBeUndefined()
  })

  it('档位选择器仅在思考开启且模型声明 efforts 时出现，点击发出所选档位', async () => {
    const wrapper = mountWithThinking(null, { mode: 'forced', efforts: ['low', 'medium', 'high'], default_on: true, default_effort: 'high' })

    await wrapper.find('[title="模型设置"]').trigger('click')

    const buttons = wrapper.findAll('button').filter(b => (b.attributes('title') ?? '').startsWith('思考等级'))
    expect(buttons.length).toBe(3)

    const highlighted = buttons.find(b => b.classes().includes('text-blue-600'))
    expect(highlighted?.text()).toBe('高')

    await buttons.find(b => b.text() === '低')!.trigger('click')
    expect(wrapper.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })
})

describe('ModelPicker 定时任务组合（allowEmpty + 开关形态）', () => {
  const mountSchedule = (enableDeepThinking: boolean | null = null) =>
    mountPicker({
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A', thinking: { mode: 'optional', efforts: ['low', 'high'], default_on: false, default_effort: 'high' } }],
      enableDeepThinking,
      thinkingEffort: null,
      allowEmpty: true,
    })

  it('allowEmpty 与深度思考开关/档位共存，交互与主对话一致', async () => {
    // 未触碰：开关展示回退 default_on（false → 关），点击发出显式 true
    const untouched = mountSchedule()
    await untouched.find('[title="模型设置"]').trigger('click')
    const toggle = untouched.find('button[role="switch"]')
    expect(toggle.attributes('aria-checked')).toBe('false')
    await toggle.trigger('click')
    expect(untouched.emitted('update:enableDeepThinking')?.[0]).toEqual([true])

    // 开关开启后展示档位（含模型 default_effort 高亮）
    const enabled = mountSchedule(true)
    await enabled.find('[title="模型设置"]').trigger('click')

    const effortButtons = enabled.findAll('button').filter(b => (b.attributes('title') ?? '').startsWith('思考等级'))
    expect(effortButtons.map(b => b.text())).toEqual(['低', '高'])
    const highlighted = effortButtons.find(b => b.classes().includes('text-blue-600'))
    expect(highlighted?.text()).toBe('高')

    await effortButtons.find(b => b.text() === '低')!.trigger('click')
    expect(enabled.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
  })

  it('选「系统默认」模型后仍可控制思考开关（按默认模型运行时生效）', async () => {
    const wrapper = mountSchedule(true)
    await wrapper.find('[title="模型设置"]').trigger('click')

    const systemDefault = wrapper.findAll('button').find(b => b.text() === '系统默认')!
    await systemDefault.trigger('click')

    expect(wrapper.emitted('update:selectedModel')?.[0]).toEqual([''])
    // 无模型选中时档位不可知，不展示档位选择器
    expect(wrapper.findAll('button').filter(b => (b.attributes('title') ?? '').startsWith('思考等级')).length).toBe(0)
  })
})