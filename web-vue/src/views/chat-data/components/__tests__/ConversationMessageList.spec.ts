import { describe, expect, it } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import ConversationMessageList from '../ConversationMessageList.vue'
import type { Message } from '../../types'

const streamingAssistantMessage: Message = {
  id: 'assistant-1',
  role: 'assistant',
  content: '',
  isStreaming: true,
  thinkingSegments: [{
    id: 'thinking-1',
    content: '正在分析数据源和查询条件。',
    toolCallCount: 0,
  }],
  segments: [{ type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' }],
}

describe('ConversationMessageList', () => {
  it('allows a streaming thinking segment to be collapsed', async () => {
    const wrapper = shallowMount(ConversationMessageList, {
      props: {
        messages: [streamingAssistantMessage],
      },
    })

    expect(wrapper.text()).toContain(streamingAssistantMessage.thinkingSegments![0]!.content)

    await wrapper.get('button').trigger('click')

    expect(wrapper.text()).not.toContain(streamingAssistantMessage.thinkingSegments![0]!.content)
    expect(wrapper.find('.fa-chevron-right').classes()).not.toContain('rotate-90')
  })

  it('automatically collapses thinking segments when streaming finishes', async () => {
    const wrapper = shallowMount(ConversationMessageList, {
      props: {
        messages: [streamingAssistantMessage],
      },
    })

    await wrapper.setProps({
      messages: [{ ...streamingAssistantMessage, isStreaming: false }],
    })

    expect(wrapper.text()).not.toContain(streamingAssistantMessage.thinkingSegments![0]!.content)
    expect(wrapper.find('.fa-chevron-right').classes()).not.toContain('rotate-90')
  })

  it('collapses the completed thinking segment before the next one starts', async () => {
    const secondThinkingContent = '正在整理查询结果。'
    const wrapper = shallowMount(ConversationMessageList, {
      props: {
        messages: [streamingAssistantMessage],
      },
    })

    await wrapper.setProps({
      messages: [{
        ...streamingAssistantMessage,
        thinkingSegments: [
          ...streamingAssistantMessage.thinkingSegments!,
          { id: 'thinking-2', content: secondThinkingContent, toolCallCount: 1 },
        ],
        segments: [
          { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
          { type: 'toolCall', toolCallId: 'tool-1' },
          { type: 'thinkingSegment', thinkingSegmentId: 'thinking-2' },
        ],
      }],
    })

    expect(wrapper.text()).not.toContain(streamingAssistantMessage.thinkingSegments![0]!.content)
    expect(wrapper.text()).toContain(secondThinkingContent)
  })

  it('renders context compression as an unframed timeline marker', () => {
    const wrapper = shallowMount(ConversationMessageList, {
      props: {
        messages: [{
          id: 'context-compression-1',
          role: 'context_compression',
          content: '',
          contextCompression: {
            id: 'context-compression-1',
            estimated_tokens_before: 70000,
            estimated_tokens_after: 21000,
            estimated_cleared_tokens: 49000,
            compressed_messages: 12,
          },
        }],
      },
      global: {
        stubs: { ElTooltip: { template: '<div><slot /><slot name="content" /></div>' } },
      },
    })

    expect(wrapper.text()).toContain('上下文已压缩')
    expect(wrapper.text()).toContain('释放 49,000 tokens')
    expect(wrapper.find('.rounded-2xl').exists()).toBe(false)
  })
})
