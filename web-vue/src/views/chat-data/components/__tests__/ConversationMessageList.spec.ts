import { afterEach, describe, expect, it, vi } from 'vitest'
import { shallowMount } from '@vue/test-utils'
import ConversationMessageList from '../conversation/ConversationMessageList.vue'
import type { Message } from '../../types'

const copyTextToClipboardMock = vi.hoisted(() => vi.fn().mockResolvedValue(true))
vi.mock('../../utils/clipboard', () => ({ copyTextToClipboard: copyTextToClipboardMock }))

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
  wrapper.findAll('button').find(button => button.text().includes('思考') && !button.text().includes('工作过程'))!

const getWorkProcessButton = (wrapper: ReturnType<typeof mountList>) =>
  wrapper.findAll('button').find(button => button.text().includes('工作过程'))!

describe('ConversationMessageList', () => {
  it('keeps streaming thinking segments collapsed by default with a single-line tail preview', () => {
    const wrapper = mountList([streamingAssistantMessage])

    // 默认收起：完整内容不落盘，只有单行尾部预览
    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    expect(wrapper.text()).toContain('思考中')
    expect(wrapper.text()).toContain(`…${longThinkingContent.slice(-80)}`)
    expect(getThinkingButton(wrapper).find('.fa-chevron-right').classes()).not.toContain('rotate-90')
  })

  it('expands a collapsed thinking segment on click and collapses it again', async () => {
    const wrapper = mountList([streamingAssistantMessage])
    const thinkingButton = getThinkingButton(wrapper)

    await thinkingButton.trigger('click')

    expect(wrapper.find('.bg-slate-50').exists()).toBe(true)
    expect(wrapper.find('.bg-slate-50').text()).toContain(longThinkingContent)
    expect(thinkingButton.find('.fa-chevron-right').classes()).toContain('rotate-90')

    await thinkingButton.trigger('click')

    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    expect(thinkingButton.find('.fa-chevron-right').classes()).not.toContain('rotate-90')
  })

  it('keeps thinking segments collapsed and hides the preview when streaming finishes', async () => {
    const wrapper = mountList([streamingAssistantMessage])

    await wrapper.setProps({
      messages: [{ ...streamingAssistantMessage, isStreaming: false }],
    })

    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    // 流式结束后整个工作过程折叠为一行（摘要里提及思考分段数）
    expect(wrapper.text()).toContain('工作过程')
    expect(wrapper.text()).toContain('思考')
    expect(wrapper.text()).not.toContain('思考中')
    // 流式结束后不再显示尾部预览文字
    expect(wrapper.text()).not.toContain(longThinkingContent.slice(-80))
  })

  it('folds the finished work process behind a toggle row and expands on click', async () => {
    const wrapper = mountList([{
      ...streamingAssistantMessage,
      isStreaming: false,
      segments: [
        { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
        { type: 'text', content: '最终回答' },
      ],
    }])

    // 完成后默认折叠：思考正文隐藏，只剩「工作过程」一行 + 摘要
    expect(wrapper.text()).toContain('工作过程')
    expect(wrapper.text()).toContain('思考 1')
    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
    expect(getWorkProcessButton(wrapper).attributes('aria-expanded')).toBe('false')

    await getWorkProcessButton(wrapper).trigger('click')

    // 手动展开后工作过程可见：思考分段行恢复（分段级仍默认收起，需再点一次展开正文）
    const thinkingButton = getThinkingButton(wrapper)
    expect(thinkingButton).toBeTruthy()
    expect(wrapper.text()).not.toContain('思考 1')

    await thinkingButton.trigger('click')
    expect(wrapper.find('.bg-slate-50').exists()).toBe(true)
    expect(wrapper.find('.bg-slate-50').text()).toContain(longThinkingContent)
  })

  it('auto-expands a finished work process when there is no final answer', () => {
    const wrapper = mountList([{ ...streamingAssistantMessage, isStreaming: false }])

    // 没有最终回答时不能把全部过程折叠掉，否则气泡只剩一个空按钮。
    expect(wrapper.text()).toContain('工作过程')
    expect(getWorkProcessButton(wrapper).find('.fa-list-check').exists()).toBe(false)
    expect(getThinkingButton(wrapper)).toBeTruthy()
    expect(getWorkProcessButton(wrapper).attributes('aria-expanded')).toBe('true')
  })

  it('keeps the work process expanded while streaming and after manual toggle', async () => {
    const wrapper = mountList([streamingAssistantMessage])

    // 流式中默认展开：思考分段行直接可见
    expect(wrapper.text()).toContain('工作过程')
    expect(wrapper.text()).toContain('思考中')

    // 手动收起工作过程后，思考分段整体隐藏
    await getWorkProcessButton(wrapper).trigger('click')
    expect(wrapper.text()).not.toContain('思考中')
    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)

    // 再展开恢复
    await getWorkProcessButton(wrapper).trigger('click')
    expect(wrapper.text()).toContain('思考中')
  })

  it('folds intermediate narration text and keeps only the final answer when collapsed', () => {
    const wrapper = mountList([{
      ...streamingAssistantMessage,
      isStreaming: false,
      segments: [
        { type: 'text', content: '我先看一下数据分布。' },
        { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
        { type: 'text', content: '结论：数据已就绪。' },
      ],
    }])

    // 折叠后只留最后一段文本（最终回答），中间叙述与思考分段一起进折叠区
    expect(wrapper.text()).toContain('结论：数据已就绪。')
    expect(wrapper.text()).not.toContain('我先看一下数据分布。')
    expect(wrapper.find('.bg-slate-50').exists()).toBe(false)
  })

  it('does not show a work process button when only legacy arrays have content', () => {
    const wrapper = mountList([{
      id: 'assistant-legacy',
      role: 'assistant',
      content: '最终回答',
      isStreaming: false,
      thinkingSegments: [{ id: 'legacy-thinking', content: '旧思考', toolCallCount: 0 }],
      segments: [],
    }])

    // 渲染源是 segments；没有可渲染过程行时不能出现空壳按钮。
    expect(wrapper.text()).not.toContain('工作过程')
    expect(wrapper.text()).toContain('最终回答')
  })

  it('keeps a finished work process open while its run is still active', () => {
    const wrapper = mountList([
      { id: 'user-run', role: 'user', content: '查一下', isStreaming: false },
      {
        ...streamingAssistantMessage,
        id: 'assistant-run',
        isStreaming: false,
        segments: [
          { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
          { type: 'text', content: '阶段回答' },
        ],
      },
    ], { runActive: true })

    // 单条消息 isStreaming 提前结束，但当前 run 仍活跃，过程不应提前收起。
    expect(getWorkProcessButton(wrapper).attributes('aria-expanded')).toBe('true')
  })

  it('copies only the final answer, not hidden intermediate narration', async () => {
    copyTextToClipboardMock.mockClear()
    const wrapper = mountList([{
      id: 'assistant-copy',
      role: 'assistant',
      content: '中间叙述\n\n结论：数据已就绪。',
      isStreaming: false,
      segments: [
        { type: 'text', content: '我先看一下数据分布。' },
        { type: 'text', content: '结论：数据已就绪。' },
      ],
    }])

    const copyButton = wrapper.findAll('button').find(button => button.text().includes('复制'))!
    await copyButton.trigger('click')

    expect(copyTextToClipboardMock).toHaveBeenCalledWith('结论：数据已就绪。')
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

  describe('划选引用浮层', () => {
    const fakeSelectionRect = { left: 40, top: 120, width: 60, height: 18 }

    const stubSelection = (overrides: Record<string, unknown> = {}) => {
      const anchorNode = document.createTextNode('选中')
      const selection = {
        isCollapsed: false,
        rangeCount: 1,
        anchorNode,
        focusNode: anchorNode,
        toString: () => '选中的片段',
        getRangeAt: () => ({ getBoundingClientRect: () => ({ ...fakeSelectionRect }) }),
        removeAllRanges: vi.fn(),
        ...overrides,
      }
      vi.stubGlobal('getSelection', () => selection)
      return { selection, anchorNode }
    }

    afterEach(() => {
      vi.unstubAllGlobals()
    })

    const mountWithAssistant = () => mountList([{
      id: 'assistant-quote',
      role: 'assistant',
      content: '一段可以划选的回答内容',
      isStreaming: false,
    }])

    const mouseUpOnBubble = async (wrapper: ReturnType<typeof mountList>, anchorNode: Node) => {
      const bubble = wrapper.find('.rounded-2xl.rounded-bl-md')
      // 选区两端必须真实落在气泡 DOM 内：把桩节点挂进气泡再触发 mouseup
      bubble.element.appendChild(anchorNode)
      await bubble.trigger('mouseup', { button: 0 })
    }

    it('划选后浮出操作条，「添加到对话」上抛片段文本并清空选区', async () => {
      const wrapper = mountWithAssistant()
      expect(wrapper.text()).not.toContain('添加到对话')

      const { anchorNode, selection } = stubSelection()
      await mouseUpOnBubble(wrapper, anchorNode)

      expect(wrapper.text()).toContain('复制')
      expect(wrapper.text()).toContain('添加到对话')
      const quoteButton = wrapper.findAll('button').find(button => button.text().includes('添加到对话'))!
      await quoteButton.trigger('click')

      expect(wrapper.emitted('addToConversation')).toEqual([['选中的片段']])
      expect(selection.removeAllRanges).toHaveBeenCalledTimes(1)
      // 上抛后浮层收起
      expect(wrapper.text()).not.toContain('添加到对话')
    })

    it('流式中的助手气泡不触发划选浮层', async () => {
      const wrapper = mountList([{
        id: 'assistant-streaming',
        role: 'assistant',
        content: '生成中的回答',
        isStreaming: true,
      }])

      const { anchorNode } = stubSelection()
      const bubble = wrapper.find('.rounded-2xl.rounded-bl-md')
      bubble.element.appendChild(anchorNode)
      await bubble.trigger('mouseup', { button: 0 })

      expect(wrapper.text()).not.toContain('添加到对话')
    })
  })
})
