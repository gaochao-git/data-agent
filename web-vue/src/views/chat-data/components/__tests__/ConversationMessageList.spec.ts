import { describe, expect, it } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import ConversationMessageList from '../conversation/ConversationMessageList.vue'
import type { Message } from '../../types'

const longThinkingContent = `${'用户询问数据情况，需要先定位语义模型，再决定查询口径。'.repeat(3)}最后核对时间范围。`

const streamingAssistantMessage: Message = {
  id: 'assistant-1',
  role: 'assistant',
  content: '',
  isStreaming: true,
  thinkingSegments: [{
    id: 'thinking-1',
    content: longThinkingContent,
    toolCallCount: 0,
  }],
  segments: [{ type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' }],
}

const mountList = (messages: Message[], extraProps: Record<string, unknown> = {}) => shallowMount(ConversationMessageList, {
  props: { messages, ...extraProps },
  global: {
    stubs: {
      ElTooltip: { template: '<div><slot /><slot name="content" /></div>' },
      // shallowMount 的自动 stub 不渲染默认 slot，footer 内的复制按钮与 AI 声明需显式透出
      AssistantMessageFooter: { template: '<div><slot /><span>本回答由 AI 生成</span></div>' },
      // 思考正文抽为 ThinkingSegmentBody（流式粘底跟随），stub 需保留容器类与内容供断言
      ThinkingSegmentBody: {
        props: ['content', 'active'],
        template: '<div class="bg-slate-50">{{ content }}</div>',
      },
    },
  },
})

const getThinkingButton = (wrapper: ReturnType<typeof mountList>) =>
  wrapper.findAll('button').find(button => button.text().includes('思考'))!

describe('ConversationMessageList', () => {
  it('keeps streaming thinking segments collapsed by default with a single-line tail preview', () => {
    const wrapper = mountList([streamingAssistantMessage])

    // 默认收起：完整内容不落盘，只有单行尾部预览
    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    expect(wrapper.text()).toContain('思考中')
    expect(wrapper.text()).toContain(`…${longThinkingContent.slice(-80)}`)
    expect(wrapper.find('.fa-chevron-right').classes()).not.toContain('rotate-90')
  })

  it('expands a collapsed thinking segment on click and collapses it again', async () => {
    const wrapper = mountList([streamingAssistantMessage])
    const thinkingButton = getThinkingButton(wrapper)

    await thinkingButton.trigger('click')

    expect(wrapper.find('.bg-slate-50').exists()).toBe(true)
    expect(wrapper.find('.bg-slate-50').text()).toContain(longThinkingContent)
    expect(wrapper.find('.fa-chevron-right').classes()).toContain('rotate-90')

    await thinkingButton.trigger('click')

    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    expect(wrapper.find('.fa-chevron-right').classes()).not.toContain('rotate-90')
  })

  it('keeps thinking segments collapsed and hides the preview when streaming finishes', async () => {
    const wrapper = mountList([streamingAssistantMessage])

    await wrapper.setProps({
      messages: [{ ...streamingAssistantMessage, isStreaming: false }],
    })

    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    expect(wrapper.text()).toContain('思考')
    expect(wrapper.text()).not.toContain('思考中')
    // 流式结束后不再显示尾部预览文字
    expect(wrapper.text()).not.toContain(longThinkingContent.slice(-80))
  })

  it('hands the single-line preview over to the newly started thinking segment', async () => {
    const secondThinkingContent = `${'正在整理查询结果并核对字段口径。'.repeat(6)}准备输出结论。`
    const wrapper = mountList([streamingAssistantMessage])

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

    // 两个分段都保持收起，仅当前流式分段显示预览
    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(longThinkingContent.slice(-80))
    expect(wrapper.text()).toContain(`…${secondThinkingContent.slice(-80)}`)
  })

  it('renders context compression as an unframed timeline marker', () => {
    const wrapper = mountList([{
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
    }])

    expect(wrapper.text()).toContain('上下文已压缩')
    expect(wrapper.text()).toContain('释放 49,000 tokens')
    expect(wrapper.find('.rounded-2xl').exists()).toBe(false)
  })

  it('shows the assistant footer after streaming finishes', () => {
    const wrapper = mountList([{
      id: 'assistant-done',
      role: 'assistant',
      content: '已完成回答',
      isStreaming: false,
    }])

    expect(wrapper.text()).toContain('复制')
    expect(wrapper.text()).toContain('本回答由 AI 生成')
  })

  it('hides the assistant footer while the run is still active even if the message stopped streaming', () => {
    // 中间事件（等待用户 / 队列准入 / 上下文压缩 / 断流恢复轮询）会把单条消息
    // 的 isStreaming 提前置 false，但 Run 仍活跃；footer 属于 Run 级语义，必须抑制。
    const wrapper = mountList([{
      id: 'assistant-mid-run',
      role: 'assistant',
      content: '部分回答',
      isStreaming: false,
    }], { runActive: true })

    expect(wrapper.text()).not.toContain('复制')
    expect(wrapper.text()).not.toContain('本回答由 AI 生成')
  })

  it('keeps historical assistant footers visible while a new run is active', () => {
    // 抑制范围只覆盖当前 run（最后一条 user 消息之后），历史轮次 footer 不随新 run 启停闪烁。
    const wrapper = mountList([
      { id: 'user-1', role: 'user', content: '第一轮提问', isStreaming: false },
      { id: 'assistant-old', role: 'assistant', content: '第一轮回答', isStreaming: false },
      { id: 'user-2', role: 'user', content: '第二轮提问', isStreaming: false },
      { id: 'assistant-new', role: 'assistant', content: '第二轮部分回答', isStreaming: false },
    ], { runActive: true })

    // 只有历史消息（assistant-old）显示 footer；当前 run 的消息（assistant-new）被抑制
    expect(wrapper.text().match(/复制/g)).toHaveLength(1)
    expect(wrapper.text().match(/本回答由 AI 生成/g)).toHaveLength(1)
  })
})
