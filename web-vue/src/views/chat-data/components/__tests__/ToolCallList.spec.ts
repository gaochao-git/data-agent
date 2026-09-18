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
            args: { description: '任务书', subagent_type: 'data-explore' },
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
                tool_name: 'data_analyzing_tool',
                args: { action: 'execute_query', action_payload: { semantic_config_id: 'sc_a' } },
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
    expect(chain.text()).toContain('子智能体执行过程')
    expect(chain.text()).toContain('精确计算')
    expect(chain.text()).toContain('数据分析')
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
            args: { description: '任务书', subagent_type: 'data-explore' },
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
    // 父 task 已结束 → 过程区自动收起，先手动展开再看思考按钮
    await wrapper.find('.subagent-chain-toggle').trigger('click')
    const settledToggle = wrapper.find('.sub-thinking-toggle')
    expect(settledToggle.text()).not.toContain('…')
  })

  it('collapses the subagent chain when the task settles and then obeys manual toggling', async () => {
    const task = (status: 'running' | 'completed') => ({
      tool_call_id: 'call-task',
      tool_name: 'task' as const,
      args: { description: '任务书' },
      status,
      subagentTrail: [
        { kind: 'tool' as const, id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 + 1' }, status: 'completed' as const, result: '2' },
      ],
    })
    const wrapper = mount(ToolCallList, { props: { toolCalls: [task('running')] } })

    // 运行中：过程可见
    expect(wrapper.find('.subagent-trail-scroll').exists()).toBe(true)

    // 跑完：自动收起（结论已落在主对话，过程不占屏）
    await wrapper.setProps({ toolCalls: [task('completed')] })
    expect(wrapper.find('.subagent-trail-scroll').exists()).toBe(false)

    // 手动展开后，自动策略不再覆盖用户意图
    await wrapper.find('.subagent-chain-toggle').trigger('click')
    expect(wrapper.find('.subagent-trail-scroll').exists()).toBe(true)
    await wrapper.setProps({ toolCalls: [task('running')] })
    expect(wrapper.find('.subagent-trail-scroll').exists()).toBe(true)

    // 再点一次收起
    await wrapper.find('.subagent-chain-toggle').trigger('click')
    expect(wrapper.find('.subagent-trail-scroll').exists()).toBe(false)
  })

  it('keeps the chain toggle away from the side panel: no selectTool, inner chips still select', async () => {
    const wrapper = mount(ToolCallList, {
      props: {
        toolCalls: [
          {
            tool_call_id: 'call-task',
            tool_name: 'task',
            args: { description: '任务书' },
            status: 'running',
            subagentTrail: [
              { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 + 1' }, status: 'completed', result: '2' },
            ],
          },
        ],
      },
    })

    // 折叠/展开标题行不触碰右侧面板
    await wrapper.find('.subagent-chain-toggle').trigger('click')
    await wrapper.find('.subagent-chain-toggle').trigger('click')
    expect(wrapper.emitted('selectTool')).toBeUndefined()

    // 内层 chip 与普通工具走同一条 selectTool 链路
    await wrapper.find('.sub-tool-chip').trigger('click')
    expect(wrapper.emitted('selectTool')?.[0]?.[0]).toMatchObject({ tool_call_id: 'inner-1' })
  })

  it('surfaces the in-flight inner tool in the header so fast tools do not look frozen', () => {
    const wrapper = mount(ToolCallList, {
      props: {
        toolCalls: [
          {
            tool_call_id: 'call-task',
            tool_name: 'task',
            args: { description: '任务书' },
            status: 'running',
            subagentTrail: [
              { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 + 1' }, status: 'completed', result: '2' },
              { kind: 'tool', id: 'inner-2', tool_name: 'data_analyzing_tool', args: { action: 'execute_query' }, status: 'running' },
            ],
          },
        ],
      },
    })

    const chain = wrapper.find('.subagent-chain')
    // 标题行给出「正在执行什么」：内层工具常在亚秒级完成，chip 上的小
    // spinner 会一闪而过，标题行才是用户能持续看到的位置
    expect(chain.text()).toContain('正在执行：数据分析')
    expect(chain.find('.subagent-chain-toggle .fa-spinner').exists()).toBe(true)

    // 执行中的 chip 与已完成的 chip 视觉可区分（对齐主图卡片的 running 底色）
    const chips = chain.findAll('.sub-tool-chip')
    expect(chips.length).toBe(2)
    const runningChip = chips[1]
    if (!runningChip) throw new Error('expected the running inner tool chip to exist')
    expect(runningChip.classes().join(' ')).toContain('bg-[rgba(31,130,217,0.06)]')
    expect(runningChip.classes().join(' ')).not.toContain('hover:bg-white')
    const settledChip = chips[0]
    if (!settledChip) throw new Error('expected the settled inner tool chip to exist')
    expect(settledChip.classes().join(' ')).not.toContain('bg-[rgba(31,130,217,0.06)]')
  })

  it('shows the in-flight hint only while running and drops it once settled', async () => {
    const toolCall = {
      tool_call_id: 'call-task',
      tool_name: 'task' as const,
      args: { description: '任务书' },
      status: 'running' as const,
      subagentTrail: [
        { kind: 'tool' as const, id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 / 0' }, status: 'error' as const, result: 'boom' },
        { kind: 'tool' as const, id: 'inner-2', tool_name: 'data_analyzing_tool', args: { action: 'execute_query' }, status: 'running' as const },
      ],
    }
    const wrapper = mount(ToolCallList, { props: { toolCalls: [toolCall] } })

    // 运行中：有工具正在跑，标题行优先说「正在执行什么」
    expect(wrapper.find('.subagent-chain').text()).toContain('正在执行：数据分析')

    await wrapper.setProps({ toolCalls: [{ ...toolCall, status: 'completed' as const }] })
    // 收敛后：不再有 in-flight 提示，标题行回归「子智能体执行过程」
    const settled = wrapper.find('.subagent-chain').text()
    expect(settled).not.toContain('正在执行')
    expect(settled).toContain('子智能体执行过程')
  })

  it('shows no in-flight hint when no inner tool is executing', () => {
    // 任务仍在跑、但模型正在思考下一步（没有工具在执行）：此时不谎报
    // 「正在执行」，反馈由中间文本的流式增量承担
    const wrapper = mount(ToolCallList, {
      props: {
        toolCalls: [
          {
            tool_call_id: 'call-task',
            tool_name: 'task',
            args: { description: '任务书' },
            status: 'running',
            subagentTrail: [
              { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 + 1' }, status: 'completed', result: '2' },
            ],
          },
        ],
      },
    })

    const header = wrapper.find('.subagent-chain-toggle').text()
    expect(header).not.toContain('正在执行')
    expect(header).not.toContain('部分工具失败')
  })
})
