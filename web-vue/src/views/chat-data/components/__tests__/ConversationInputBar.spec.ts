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
