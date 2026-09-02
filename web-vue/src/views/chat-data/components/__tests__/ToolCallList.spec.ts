import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import ToolCallList from '../ToolCallList.vue'

describe('ToolCallList', () => {
  it('renders the subagent chain as an independent region below the task card and emits selectTool for inner tools', async () => {
    const wrapper = mount(ToolCallList, {
      props: {
        defaultExpanded: true,
        toolCalls: [
          {
            tool_call_id: 'call-task',
            tool_name: 'task',
            args: { description: '任务书', subagent_type: 'deep-analysis' },
            status: 'running',
            subagentTrail: [
              {
                kind: 'tool',
                id: 'inner-1',
                tool_name: 'calculator_tool',
                args: { expression: '1 + 1' },
                status: 'completed',
                result: '2',
              },
              {
                kind: 'tool',
                id: 'inner-2',
                tool_name: 'semantic_query_tool',
                args: { query: '订单量' },
                status: 'running',
              },
            ],
          },
          {
            tool_call_id: 'call-other',
            tool_name: 'calculator_tool',
            args: { expression: '2 + 2' },
            status: 'completed',
          },
        ],
      },
    })

    const chain = wrapper.find('.subagent-chain')
    expect(chain.exists()).toBe(true)
    expect(chain.attributes('data-parent-tool-call-id')).toBe('call-task')
    expect(chain.text()).toContain('子智能体内部逻辑')
    expect(chain.text()).toContain('精确计算')
    expect(chain.text()).toContain('查询数据')
    // 内层工具结果不在消息流内展示（完整参数/结果点击 chip 走右侧详情面板）
    expect(chain.text()).not.toContain('↳')

    // 子智能体链路在 task 卡片之后、且不污染其它工具卡片
    const allChains = wrapper.findAll('.subagent-chain')
    expect(allChains.length).toBe(1)

    const chips = chain.findAll('.sub-tool-chip')
    expect(chips.length).toBe(2)

    const firstChip = chips[0]
    if (!firstChip) throw new Error('expected the first inner tool chip to exist')
    await firstChip.trigger('click')
    const emitted = wrapper.emitted('selectTool')
    expect(emitted?.[0]?.[0]).toMatchObject({ tool_call_id: 'inner-1' })
  })

  it('interleaves subagent text output with tool calls in occurrence order', () => {
    const wrapper = mount(ToolCallList, {
      props: {
        defaultExpanded: true,
        toolCalls: [
          {
            tool_call_id: 'call-task',
            tool_name: 'task',
            args: { description: '任务书', subagent_type: 'deep-analysis' },
            status: 'running',
            subagentTrail: [
              { kind: 'message', id: 'msg-1', content: '先算一下 1+1' },
              { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 + 1' }, status: 'completed', result: '2' },
              { kind: 'message', id: 'msg-2', content: '结果是 2，任务完成。', truncated: true },
            ],
          },
        ],
      },
    })

    const chain = wrapper.find('.subagent-chain')
    expect(chain.text()).toContain('先算一下 1+1')
    expect(chain.text()).toContain('结果是 2，任务完成。')
    expect(chain.text()).toContain('已截断')

    const messages = chain.findAll('.conversation-markdown')
    expect(messages.length).toBe(2)
    // 计数只统计工具条目
    expect(chain.text()).toContain('（1）')

    // 顺序：文本 → 工具 → 文本
    const order = Array.from((chain.element as HTMLElement).querySelectorAll('.conversation-markdown, .sub-tool-chip'))
      .map(node => (node.classList.contains('conversation-markdown') ? 'message' : 'tool'))
    expect(order).toEqual(['message', 'tool', 'message'])
  })

  it('renders subagent messages through the same markdown pipeline as the main agent', () => {
    const wrapper = mount(ToolCallList, {
      props: {
        defaultExpanded: true,
        toolCalls: [
          {
            tool_call_id: 'call-task',
            tool_name: 'task',
            args: { description: '任务书' },
            status: 'running',
            subagentTrail: [
              { kind: 'message', id: 'msg-1', content: '结果是 **2**' },
            ],
          },
        ],
      },
    })

    const message = wrapper.find('.subagent-chain .conversation-markdown')
    expect(message.exists()).toBe(true)
    // 走 markdown-it 渲染，而非原样输出文本
    expect(message.html()).toContain('<strong>2</strong>')
    // 与主智能体文本段同一套展示 class（样式由全局 markdown.css 提供）
    expect(message.classes()).toEqual(expect.arrayContaining(['text-sm', 'leading-relaxed', 'text-slate-700']))
  })

  it('does not render a subagent chain for non-task tool calls', () => {
    const wrapper = mount(ToolCallList, {
      props: {
        toolCalls: [
          {
            tool_call_id: 'call-1',
            tool_name: 'calculator_tool',
            args: { expression: '1 + 1' },
            status: 'completed',
          },
        ],
      },
    })
    expect(wrapper.find('.subagent-chain').exists()).toBe(false)
  })

  it('caps the trail height and auto-scrolls to the latest entry when overflowing', async () => {
    const base = {
      tool_call_id: 'call-task',
      tool_name: 'task',
      args: { description: '任务书' },
      status: 'running' as const,
    }
    const wrapper = mount(ToolCallList, {
      props: {
        defaultExpanded: true,
        toolCalls: [{ ...base, subagentTrail: [{ kind: 'message', id: 'msg-1', content: '开头' }] }],
      },
    })

    const container = wrapper.find('.subagent-trail-scroll')
    expect(container.exists()).toBe(true)
    expect(container.classes()).toContain('max-h-[280px]')
    expect(container.classes()).toContain('overflow-y-auto')

    // 模拟溢出：scrollHeight 600 / clientHeight 280，跟随状态默认粘底
    const el = container.element as HTMLElement
    let scrollTop = 320
    Object.defineProperty(el, 'scrollHeight', { value: 600, configurable: true })
    Object.defineProperty(el, 'clientHeight', { value: 280, configurable: true })
    Object.defineProperty(el, 'scrollTop', {
      get: () => scrollTop,
      set: (v: number) => { scrollTop = v },
      configurable: true,
    })

    // 新增条目后应自动滚到最新（初始默认粘底）
    await wrapper.setProps({
      toolCalls: [{
        ...base,
        subagentTrail: [
          { kind: 'message', id: 'msg-1', content: '开头' },
          { kind: 'message', id: 'msg-2', content: '结尾' },
        ],
      }],
    })
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(scrollTop).toBe(600)

    // 用户手动上翻查看历史（真实触发 scroll 事件）：停止跟随
    scrollTop = 0
    await container.trigger('scroll')
    await wrapper.setProps({
      toolCalls: [{
        ...base,
        subagentTrail: [
          { kind: 'message', id: 'msg-1', content: '开头' },
          { kind: 'message', id: 'msg-2', content: '结尾' },
          { kind: 'message', id: 'msg-3', content: '更多' },
        ],
      }],
    })
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(scrollTop).toBe(0)

    // 用户滚回底部附近（scroll 事件里距离 < 100）：恢复跟随
    scrollTop = 250
    await container.trigger('scroll')
    await wrapper.setProps({
      toolCalls: [{
        ...base,
        subagentTrail: [
          { kind: 'message', id: 'msg-1', content: '开头' },
          { kind: 'message', id: 'msg-2', content: '结尾' },
          { kind: 'message', id: 'msg-3', content: '更多' },
          { kind: 'message', id: 'msg-4', content: '最新' },
        ],
      }],
    })
    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(scrollTop).toBe(600)
  })

  it('renders subagent thinking collapsed by default and expands on toggle', async () => {
    const wrapper = mount(ToolCallList, {
      props: {
        defaultExpanded: true,
        toolCalls: [
          {
            tool_call_id: 'call-task',
            tool_name: 'task',
            args: { description: '任务书' },
            status: 'running',
            subagentTrail: [
              {
                kind: 'message',
                id: 'msg-1',
                content: '结论是 2。',
                thinking: '先看语义模型，再查订单表，最后核对数值。这一段思考需要超过八十个字符的长度，才能验证尾部预览只会保留最后八十个字符并在开头补省略号，所以这里继续补充一些无关紧要的内容把总长度撑过去，确保触发截断逻辑。',
              },
            ],
          },
        ],
      },
    })

    const chain = wrapper.find('.subagent-chain')
    // 默认收起：按钮存在、思考全文不可见、运行中露尾部预览
    const toggle = chain.find('.sub-thinking-toggle')
    expect(toggle.exists()).toBe(true)
    expect(chain.text()).toContain('思考中')
    expect(chain.text()).not.toContain('先看语义模型，再查订单表')
    expect(toggle.text()).toContain('…')
    expect(toggle.text()).not.toContain('先看语义模型')

    // 点击展开后可见全文
    await toggle.trigger('click')
    expect(chain.text()).toContain('先看语义模型，再查订单表')

    // 父 task 已结束：不再展示尾部预览与运行图标
    await wrapper.setProps({
      toolCalls: [
        {
          tool_call_id: 'call-task',
          tool_name: 'task',
          args: { description: '任务书' },
          status: 'completed',
          subagentTrail: [
            {
              kind: 'message',
              id: 'msg-1',
              content: '结论是 2。',
              thinking: '思考过程',
            },
          ],
        },
      ],
    })
    const settledToggle = wrapper.find('.sub-thinking-toggle')
    expect(settledToggle.text()).not.toContain('…')
  })
})
