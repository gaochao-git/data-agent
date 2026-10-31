import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolCallList from '../ToolCallList.vue'
import type { ToolCallInfo } from '../../types'

const ToolCallCardStub = {
  name: 'ToolCallCard',
  props: ['toolCall', 'isExpanded', 'compact', 'active', 'onViewAnalysis'],
  emits: ['select', 'toggle-expand'],
  template: '<div class="tool-card-stub" :data-tool="toolCall.tool_call_id" :data-expanded="String(isExpanded)" @click="$emit(\'select\')"></div>',
}

const mountList = (toolCalls: ToolCallInfo[], extraProps: Record<string, unknown> = {}) => mount(ToolCallList, {
  props: { toolCalls, ...extraProps },
  global: { stubs: { ToolCallCard: ToolCallCardStub } },
})

const toolCall = (patch: Partial<ToolCallInfo> = {}): ToolCallInfo => ({
  tool_call_id: 'call-1',
  tool_name: 'file_read',
  args: { path: '/mnt/a.sql' },
  status: 'completed',
  result: 'select 1',
  ...patch,
})

describe('ToolCallList', () => {
  it('为每个工具调用渲染一张工具卡，并透传 selectTool', async () => {
    const wrapper = mountList([
      toolCall({ tool_call_id: 'call-1' }),
      toolCall({ tool_call_id: 'call-2', tool_name: 'calculator_tool' }),
    ])

    const cards = wrapper.findAll('.tool-card-stub')
    expect(cards.map(card => card.attributes('data-tool'))).toEqual(['call-1', 'call-2'])

    await cards[0]!.trigger('click')
    expect(wrapper.emitted('selectTool')?.[0]?.[0]).toMatchObject({ tool_call_id: 'call-1' })
  })

  it('defaultExpanded 决定工具卡初始展开态', () => {
    const collapsed = mountList([toolCall()])
    expect(collapsed.find('.tool-card-stub').attributes('data-expanded')).toBe('false')

    const expanded = mountList([toolCall()], { defaultExpanded: true })
    expect(expanded.find('.tool-card-stub').attributes('data-expanded')).toBe('true')
  })

  it('行内不再渲染子智能体过程区（完整过程在右侧工具详情面板）', () => {
    const wrapper = mountList([
      toolCall({
        tool_call_id: 'call-task',
        tool_name: 'task',
        args: { description: '任务书' },
        status: 'running',
        subagentTrail: [
          { kind: 'message', id: 'msg-1', content: '子智能体中间输出' },
          { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: {}, status: 'running' },
        ],
      }),
    ])

    expect(wrapper.findAll('.tool-card-stub')).toHaveLength(1)
    expect(wrapper.find('[data-subagent-process]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('子智能体执行过程')
  })

  it('查询分析入口只在 execute_query 工具时透传 onViewAnalysis', () => {
    const wrapper = mountList([
      toolCall({ tool_call_id: 'q-1', tool_name: 'data_analyzing_tool', args: { action: 'execute_query' }, renderId: 'r1' }),
      toolCall({ tool_call_id: 'q-2', tool_name: 'data_analyzing_tool', args: { action: 'check' } }),
    ], { onViewAnalysis: () => {}, isAnalysisActive: true })

    const cards = wrapper.findAllComponents(ToolCallCardStub)
    expect(cards[0]!.props('onViewAnalysis')).toBeTypeOf('function')
    expect(cards[1]!.props('onViewAnalysis')).toBeUndefined()
  })
})
