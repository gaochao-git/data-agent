import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SubagentTrail from '../conversation/SubagentTrail.vue'
import type { SubagentTrailItem, ToolCallInfo } from '../../types'

const MessageItemStub = {
  name: 'MessageItem',
  props: [
    'message',
    'isCurrentRun',
    'showFooter',
    'conversationId',
    'activeToolCallId',
    'shouldInlineRenderResult',
    'datasourceId',
    'executionTargets',
    'onSave',
    'liveGenerationSpeed',
    'liveElapsedMs',
    'resolveToolCall',
  ],
  emits: ['selectTool', 'openAttachment'],
  template: '<div class="message-item-stub" :data-role="message.role" :data-id="message.id"></div>',
}

const stubs = { MessageItem: MessageItemStub }

const taskCall = (trail: SubagentTrailItem[], status: ToolCallInfo['status'] = 'running'): ToolCallInfo => ({
  tool_call_id: 'call-task',
  tool_name: 'task',
  args: { description: '盘点语义模型中的指标与维度', subagent_type: 'explore' },
  status,
  subagentTrail: trail,
})

const mountTrail = (toolCall: ToolCallInfo, extraProps: Record<string, unknown> = {}) => mount(SubagentTrail, {
  props: { toolCall, conversationId: 'conv-1', ...extraProps },
  global: { stubs },
})

describe('SubagentTrail（右侧聊天式完整过程）', () => {
  it('过程投影成主对话消息，直接复用 MessageItem：任务书 user + 子智能体 assistant', () => {
    const wrapper = mountTrail(taskCall([
      { kind: 'message', id: 'msg-1', content: '先看目录', thinking: '思考一' },
      { kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: { path: '/mnt/a.sql' }, status: 'completed', result: 'select 1' },
      { kind: 'message', id: 'msg-2', content: '结论：已盘点' },
    ]))

    const items = wrapper.findAllComponents(MessageItemStub)
    expect(items.map(item => item.props('message').role)).toEqual(['user', 'assistant'])
    expect(items.map(item => item.props('message').id)).toEqual(['call-task-task', 'call-task-process'])

    // 任务书 → user 消息；整段过程合并成一条 assistant 消息（思考/正文/工具按发生顺序）
    expect(items[0]!.props('message').content).toBe('盘点语义模型中的指标与维度')
    expect(items[1]!.props('message').segments.map((segment: { type: string }) => segment.type))
      .toEqual(['thinkingSegment', 'text', 'toolCall', 'text'])
    expect(items[1]!.props('message').toolCalls).toHaveLength(1)
    expect(items[1]!.props('message').isStreaming).toBe(true)

    // 复用主对话消息组件的同一套 props 口径
    for (const item of items) {
      expect(item.props('showFooter')).toBe(false)
      expect(typeof item.props('shouldInlineRenderResult')).toBe('function')
    }
  })

  it('内层工具再次选中：由 MessageItem 上抛 selectTool', () => {
    const wrapper = mountTrail(taskCall([
      { kind: 'message', id: 'msg-1', content: '先看目录' },
      { kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: { path: '/mnt/a.sql' }, status: 'completed', result: 'select 1' },
    ]))

    const assistant = wrapper.findAllComponents(MessageItemStub)[1]!
    assistant.vm.$emit('selectTool', assistant.props('message').toolCalls[0])
    expect(wrapper.emitted('selectTool')?.[0]?.[0]).toMatchObject({ tool_call_id: 'inner-1' })
  })

  it('真实消息组件渲染冒烟：任务书 user 气泡 + 一条助手气泡（工作过程）', () => {
    const wrapper = mount(SubagentTrail, {
      props: {
        toolCall: taskCall([
          { kind: 'message', id: 'msg-1', content: '先看目录', thinking: '思考一' },
          { kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: {}, status: 'completed', result: 'select 1' },
          { kind: 'message', id: 'msg-2', content: '结论：已盘点' },
        ]),
        conversationId: 'conv-1',
      },
      global: {
        stubs: {
          MarkdownBlock: { props: ['content'], template: '<div class="conversation-markdown">{{ content }}</div>' },
          ThinkingBlock: { props: ['content'], template: '<div class="thinking-block">{{ content }}</div>' },
          ToolCallList: true,
          QueryResultPanel: true,
          WaitingTip: true,
        },
      },
    })

    // 任务书走主对话用户气泡（蓝底）
    expect(wrapper.text()).toContain('盘点语义模型中的指标与维度')
    // 整段过程只渲染一条助手气泡，工作过程标题只出现一次
    expect(wrapper.findAll('[data-assistant-bubble]')).toHaveLength(1)
    expect(wrapper.text().match(/工作过程/g)).toHaveLength(1)
    expect(wrapper.text()).toContain('结论：已盘点')
  })

  it('已用时与主对话一致：运行中进助手状态行，完成后在气泡下方显示用时', () => {
    const running = mountTrail(taskCall([
      { kind: 'message', id: 'msg-1', content: '过程', thinking: '思考' },
    ], 'running'), { elapsedMs: 5_000, timingRunning: true })
    expect(running.findAllComponents(MessageItemStub)[1]!.props('liveElapsedMs')).toBe(5_000)
    expect(running.find('[data-subagent-duration]').exists()).toBe(false)

    const finished = mountTrail(taskCall([
      { kind: 'message', id: 'msg-1', content: '结论：已完成' },
    ], 'completed'), { elapsedMs: 90_000, timingRunning: false })
    expect(finished.findAllComponents(MessageItemStub)[1]!.props('liveElapsedMs')).toBeNull()
    expect(finished.find('[data-subagent-duration]').text()).toContain('用时 1 分 30 秒')
  })

  it('完成态在用量行展示子智能体 token 用量', () => {
    const wrapper = mountTrail(taskCall([
      { kind: 'message', id: 'msg-1', content: '结论' },
    ], 'completed'), {
      elapsedMs: 90_000,
      timingRunning: false,
      tokenUsage: { input_tokens: 12_345, output_tokens: 678, cache_read_input_tokens: 4_000 },
    })
    const tokens = wrapper.find('[data-subagent-tokens]')
    expect(tokens.text()).toBe('tokens 输入 12.3k · 输出 678 · 缓存命中 32%')
  })

  it('完成态默认收起工作过程，只留最终报告（与主对话口径一致）', () => {
    const wrapper = mount(SubagentTrail, {
      props: {
        toolCall: taskCall([
          { kind: 'message', id: 'msg-1', content: '先看目录', thinking: '思考一' },
          { kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: {}, status: 'completed', result: 'ok' },
          { kind: 'message', id: 'msg-2', content: '结论：已盘点' },
        ], 'completed'),
        conversationId: 'conv-1',
      },
      global: {
        stubs: {
          MarkdownBlock: { props: ['content'], template: '<div class="conversation-markdown">{{ content }}</div>' },
          ThinkingBlock: { props: ['content'], template: '<div class="thinking-block">{{ content }}</div>' },
          ToolCallList: true,
          QueryResultPanel: true,
          WaitingTip: true,
        },
      },
    })

    const processButton = wrapper.findAll('button').find(button => button.text().includes('工作过程'))!
    expect(processButton.attributes('aria-expanded')).toBe('false')
    expect(wrapper.text()).toContain('结论：已盘点')
    expect(wrapper.find('.thinking-block').exists()).toBe(false)
  })

  it('非 task 调用 / 无 trail 时不渲染过程区', () => {
    expect(mountTrail(taskCall([])).find('[data-subagent-process]').exists()).toBe(false)
    expect(mountTrail({
      tool_call_id: 'call-other',
      tool_name: 'file_read',
      args: {},
      status: 'completed',
      result: '',
      subagentTrail: [{ kind: 'message', id: 'msg-1', content: '过程' }],
    }).find('[data-subagent-process]').exists()).toBe(false)
  })
})
