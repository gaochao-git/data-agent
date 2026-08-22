import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConversationInputBar from '../ConversationInputBar.vue'

const mountInput = (isGenerating: boolean) => mount(ConversationInputBar, {
  props: {
    input: '',
    placeholder: '输入问题',
    selectedModel: 'model-a',
    modelOptions: [{ id: 'model-a', label: '模型 A' }],
    enableDeepThinking: false,
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
})

describe('ConversationInputBar deep thinking switch', () => {
  const mountWithThinking = (
    enableDeepThinking: boolean | null,
    thinking?: { mode: 'none' | 'optional' | 'forced'; efforts: string[]; default_on: boolean; default_effort: string | null },
  ) => mount(ConversationInputBar, {
    props: {
      input: '',
      placeholder: '输入问题',
      selectedModel: 'model-a',
      modelOptions: [{ id: 'model-a', label: '模型 A', thinking }],
      enableDeepThinking,
      isGenerating: false,
      canSubmit: false,
    },
  })

  const findSwitch = async (wrapper: ReturnType<typeof mountWithThinking>) => {
    // 思考开关位于模型设置下拉内，需先展开
    await wrapper.find('[title="模型设置"]').trigger('click')
    return wrapper.find('button[role="switch"]')
  }

  it('falls back to model default_on for display when user has not touched the switch', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: true, default_effort: null })

    expect((await findSwitch(wrapper)).attributes('aria-checked')).toBe('true')
  })

  it('emits an explicit boolean on first click from untouched state', async () => {
    const wrapper = mountWithThinking(null, { mode: 'optional', efforts: [], default_on: false, default_effort: null })

    await (await findSwitch(wrapper)).trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')?.[0]).toEqual([true])
  })

  it('locks the switch for forced models and never emits', async () => {
    const wrapper = mountWithThinking(false, { mode: 'forced', efforts: ['high'], default_on: false, default_effort: 'high' })

    const sw = await findSwitch(wrapper)
    expect(sw.attributes('aria-checked')).toBe('true')
    expect(sw.attributes('aria-disabled')).toBe('true')

    await sw.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')).toBeUndefined()
  })

  it('locks the switch for none models and never emits', async () => {
    const wrapper = mountWithThinking(true, { mode: 'none', efforts: [], default_on: false, default_effort: null })

    const sw = await findSwitch(wrapper)
    expect(sw.attributes('aria-checked')).toBe('false')
    expect(sw.attributes('aria-disabled')).toBe('true')

    await sw.trigger('click')

    expect(wrapper.emitted('update:enableDeepThinking')).toBeUndefined()
  })
})
