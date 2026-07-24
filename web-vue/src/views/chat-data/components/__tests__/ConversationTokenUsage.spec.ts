import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationTokenUsage from '../ConversationTokenUsage.vue'

const cacheUsage = {
  used: 12000,
  total: 89600,
  cumulative_cached_input_tokens: 36900,
  cumulative_cache_hit_rate: 64,
}

describe('ConversationTokenUsage', () => {
  it('keeps cache cards stable while a streaming request awaits the current call usage', async () => {
    const wrapper = mount(ConversationTokenUsage, {
      props: { usage: cacheUsage, isGenerating: true },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('当前命中率')
    expect(wrapper.text()).toContain('统计中')

    await wrapper.setProps({ isGenerating: false })
    expect(wrapper.text()).toContain('--')
    expect(wrapper.text()).toContain('64%')

    await wrapper.setProps({ usage: null, isGenerating: true })
    expect(wrapper.text()).not.toContain('模型缓存')
    wrapper.unmount()
  })

  it('clears prior cache data before the next request reports its first usage', async () => {
    const wrapper = mount(ConversationTokenUsage, {
      props: { usage: cacheUsage, isGenerating: false },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('模型缓存')

    await wrapper.setProps({ usage: null, isGenerating: true })
    expect(wrapper.text()).not.toContain('模型缓存')
    wrapper.unmount()
  })

  it('shows each input item in one compact row with content, ratio, round, type, and tokens', async () => {
    const wrapper = mount(ConversationTokenUsage, {
      props: {
        usage: {
          used: 100,
          total: 1000,
          breakdown: [{
            key: 'messages',
            label: '会话消息与工具上下文',
            tokens: 35,
            children: [{
              key: 'message_1',
              label: '第 1 轮 · 用户消息',
              tokens: 35,
              role: 'human',
              round: 1,
              description: '几点了',
              content: '几点了',
            }],
          }],
        },
      },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('内容')
    expect(wrapper.text()).toContain('占比')
    expect(wrapper.text()).toContain('轮次')
    expect(wrapper.text()).toContain('类型')
    expect(wrapper.text()).toContain('Token')
    expect(wrapper.text()).toContain('几点了')
    expect(wrapper.text()).toContain('35%')
    expect(wrapper.text()).toContain('用户')
    wrapper.unmount()
  })

  it('labels request protocol overhead explicitly instead of as conversation context', async () => {
    const wrapper = mount(ConversationTokenUsage, {
      props: {
        usage: {
          used: 100,
          total: 1000,
          breakdown: [{
            key: 'request_overhead',
            label: '请求协议开销',
            tokens: 19,
            description: '模型协议固定开销，已计入本次输入预算。',
          }],
        },
      },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('协议')
    expect(wrapper.text()).toContain('请求协议开销')
    expect(wrapper.get('[title="模型请求协议开销"]').text()).toBe('协议')
    wrapper.unmount()
  })

  it('keeps tool definitions as a distinct expandable tool row', async () => {
    const wrapper = mount(ConversationTokenUsage, {
      props: {
        usage: {
          used: 100,
          total: 1000,
          breakdown: [{
            key: 'tool_schema',
            label: '工具定义',
            tokens: 19,
            children: [{
              key: 'skill_load',
              label: '工具定义 · skill_load',
              tokens: 19,
              content: '{"name":"skill_load"}',
            }],
          }],
        },
      },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('工具定义（1 个）')
    expect(wrapper.get('[title="本次请求传入模型的工具定义"]').text()).toBe('工具')
    expect(wrapper.text()).not.toContain('skill_load')

    await wrapper.get('button[class*="grid"]').trigger('click')
    expect(wrapper.text()).toContain('skill_load')
    wrapper.unmount()
  })
})
