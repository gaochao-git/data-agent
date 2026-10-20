import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConversationInputBar from '../conversation/ConversationInputBar.vue'

const mountInput = (isGenerating: boolean) => mount(ConversationInputBar, {
  props: {
    input: '',
    placeholder: '输入问题',
    selectedModel: 'model-a',
    modelOptions: [{ id: 'model-a', label: '模型 A' }],
    enableDeepThinking: false,
    thinkingEffort: null,
    isGenerating,
    canSubmit: false,
  },
})

describe('ConversationInputBar actions', () => {
  it('shows only stop while generating and only submit when idle', async () => {
    const wrapper = mountInput(true)

    expect(wrapper.find('[title="停止生成"]').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(false)

    await wrapper.setProps({ isGenerating: false })

    expect(wrapper.find('[title="停止生成"]').exists()).toBe(false)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })

  it('keeps the "+" toolbar entry visible while generating', () => {
    const wrapper = mountInput(true)

    expect(wrapper.find('[title="更多操作"]').exists()).toBe(true)
  })

  it('forwards paste-files from the inner textarea', async () => {
    const wrapper = mountInput(false)
    const file = new File(['img'], 'image.png', { type: 'image/png' })

    await wrapper.find('textarea').trigger('paste', { clipboardData: { files: [file] } })

    expect(wrapper.emitted('pasteFiles')?.[0]).toEqual([[file]])
  })
})

describe('ConversationInputBar thinking level menu', () => {
  // 模型设置已重构为两级菜单（ModelPicker）：主面板「推理等级」行 → 二级档位列表，
  // 档位按钮 title 前缀为「思考档位」。旧版开关（role="switch"）已被取代。
  const mountWithThinking = (
    enableDeepThinking: boolean | null,
    thinking: { mode: 'none' | 'optional' | 'forced'; efforts: string[]; default_on: boolean; default_effort: string | null },
    thinkingEffort: string | null = null,
  ) => mount(ConversationInputBar, {
    props: {
      input: '',
      placeholder: '输入问题',
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A', thinking }],
      enableDeepThinking,
      thinkingEffort,
      isGenerating: false,
      canSubmit: false,
    },
  })

  const openLevelList = async (wrapper: ReturnType<typeof mountWithThinking>) => {
    await wrapper.find('[title="模型设置"]').trigger('click')
    const entry = wrapper.find('[data-model-picker-menu]').findAll('button')
      .find(button => button.text().startsWith('推理等级'))
    await entry!.trigger('click')
    return wrapper.find('[data-model-picker-menu]').findAll('button')
      .filter(button => (button.attributes('title') ?? '').startsWith('思考档位'))
  }

  it('falls back to model default_on for display when user has not touched the switch', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: true, default_effort: null })

    // 未触碰时展示态回退 default_on：触发器内联档位文案为「开启」，菜单高亮「开启」
    expect(wrapper.find('[title="模型设置"]').text()).toContain('开启')

    const levels = await openLevelList(wrapper)
    const highlighted = levels.find(button => button.classes().includes('text-blue-600'))
    expect(highlighted?.text()).toBe('开启')
  })

  it('emits an explicit boolean on first click from untouched state', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: false, default_effort: null })

    const levels = await openLevelList(wrapper)
    await levels.find(button => button.text() === '开启')!.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
  })

  it('emits false when the user picks 关闭', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: ['low'], default_on: true, default_effort: 'low' })

    const levels = await openLevelList(wrapper)
    await levels.find(button => button.text() === '关闭')!.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([false])
    expect(wrapper.emitted('update:thinkingEffort')).toBeUndefined()
  })

  it('offers no 关闭 level for forced models', async () => {
    const wrapper = mountWithThinking(false, { mode: 'forced', efforts: ['high'], default_on: false, default_effort: 'high' })

    const levels = await openLevelList(wrapper)

    // forced 恒开：档位列表只有模型声明的 efforts，没有「关闭」可点
    expect(levels.map(button => button.text())).toEqual(['高'])
  })

  it('disables the level entry for none models', async () => {
    const wrapper = mountWithThinking(true, { mode: 'none', efforts: [], default_on: false, default_effort: null })

    await wrapper.find('[title="模型设置"]').trigger('click')

    // none 模型不支持思考：推理等级行禁用且显示「不支持」，二级列表不可进
    const entry = wrapper.find('[data-model-picker-menu]').findAll('button')
      .find(button => button.text().startsWith('推理等级'))
    expect(entry!.attributes('disabled')).toBeDefined()
    expect(entry!.text()).toContain('不支持')
  })
})

describe('ConversationInputBar thinking effort selector', () => {
  const mountWithEfforts = (
    thinkingEffort: string | null,
    enableDeepThinking: boolean | null = null,
  ) => mount(ConversationInputBar, {
    props: {
      input: '',
      placeholder: '输入问题',
      selectedModel: 'model-a',
      modelOptions: [{
        id: 'model-a',
        label: '模型 A',
        thinking: { mode: 'forced', efforts: ['low', 'medium', 'high'], default_on: true, default_effort: 'high' },
      }],
      enableDeepThinking,
      thinkingEffort,
      isGenerating: false,
      canSubmit: false,
    },
  })

  const openMenuAndFindSelector = async (wrapper: ReturnType<typeof mountWithEfforts>) => {
    // 两级菜单：模型设置 → 推理等级 → 档位按钮 title 前缀「思考档位」
    await wrapper.find('[title="模型设置"]').trigger('click')
    const entry = wrapper.find('[data-model-picker-menu]').findAll('button')
      .find(button => button.text().startsWith('推理等级'))
    await entry!.trigger('click')
    return wrapper.find('[data-model-picker-menu]').findAll('button')
      .filter(button => (button.attributes('title') ?? '').startsWith('思考档位'))
  }

  it('falls back to default_effort for highlight when user has not chosen', async () => {
    const wrapper = mountWithEfforts(null)

    const buttons = await openMenuAndFindSelector(wrapper)

    const highlighted = buttons.find(b => b.classes().includes('text-blue-600'))
    expect(highlighted?.text()).toBe('高')
  })

  it('emits the picked effort on click', async () => {
    const wrapper = mountWithEfforts(null)

    const buttons = await openMenuAndFindSelector(wrapper)
    const low = buttons.find(b => b.text() === '低')!

    await low.trigger('click')

    expect(wrapper.emitted('update:thinkingEffort')?.[0]).toEqual(['low'])
    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
  })
})
