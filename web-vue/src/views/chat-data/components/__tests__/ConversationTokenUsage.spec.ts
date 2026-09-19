import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConversationTokenUsage from '../conversation/ConversationTokenUsage.vue'

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

  it('keeps the summary focused on current input, compression threshold, and context window', async () => {
    const wrapper = mount(ConversationTokenUsage, {
      props: {
        usage: {
          used: 13400,
          total: 48000,
          current_input_tokens: 13400,
          compression_trigger_tokens: 28000,
          hard_input_limit_tokens: 28000,
          max_output_tokens: 20000,
          context_window: 48000,
        },
      },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('当前输入')
    expect(wrapper.text()).toContain('压缩阈值')
    expect(wrapper.text()).toContain('上下文窗口')
    expect(wrapper.text()).not.toContain('最大输入容量')
    expect(wrapper.text()).not.toContain('输出预留')
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
              key: 'file_read',
              label: '工具定义 · file_read',
              tokens: 19,
              content: '{"name":"file_read"}',
            }],
          }],
        },
      },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('工具定义（1 个）')
    expect(wrapper.get('[title="本次请求传入模型的工具定义"]').text()).toBe('工具')
    expect(wrapper.text()).not.toContain('file_read')

    await wrapper.get('button[class*="grid"]').trigger('click')
    expect(wrapper.text()).toContain('file_read')
    wrapper.unmount()
  })

  it('resolves row content from loaded conversation messages by message_id when payload has no content', async () => {
    const wrapper = mount(ConversationTokenUsage, {
      props: {
        usage: {
          used: 100,
          total: 1000,
          breakdown: [{
            key: 'messages',
            label: '会话消息与工具上下文',
            tokens: 80,
            children: [
              {
                key: 'message_1',
                label: '第 1 轮 · 用户消息',
                tokens: 35,
                role: 'human',
                round: 1,
                message_id: 'msg-user-1',
              },
              {
                key: 'message_2',
                label: '第 1 轮 · 工具结果',
                tokens: 45,
                role: 'tool',
                round: 1,
                message_id: 'msg-tool-1',
                tool_call_id: 'call-1',
              },
            ],
          }],
        },
        messages: [
          { id: 'msg-user-1', role: 'user', content: '今天销量怎么样' },
          {
            id: 'msg-ai-1',
            role: 'assistant',
            content: '',
            toolCalls: [{
              tool_call_id: 'call-1',
              tool_name: 'run_sql',
              args: {},
              result: '销量共 1234 件',
              status: 'completed',
            }],
          },
        ],
      },
      attachTo: document.body,
    })

    await wrapper.get('button').trigger('click')
    expect(wrapper.text()).toContain('今天销量怎么样')

    const rows = wrapper.findAll('button[class*="grid"]')
    const detailRow = rows[1]
    if (!detailRow) {
      throw new Error(`未渲染第二行明细按钮，实际行数：${rows.length}`)
    }
    await detailRow.trigger('click')
    expect(wrapper.text()).toContain('销量共 1234 件')
    wrapper.unmount()
  })
})
