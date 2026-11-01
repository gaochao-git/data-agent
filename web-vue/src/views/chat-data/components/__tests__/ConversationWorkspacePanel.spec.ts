import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ConversationWorkspacePanel from '../conversation/ConversationWorkspacePanel.vue'
import type { ToolCallInfo } from '../../types'

const taskCall: ToolCallInfo = {
  tool_call_id: 'task-1',
  tool_name: 'task',
  status: 'completed',
  args: { subagent_type: 'explore' },
  result: '报告',
  subagentTrail: [{ kind: 'message', id: 'm1', content: '结论' }],
}
const toolCall: ToolCallInfo = {
  tool_call_id: 'tool-1',
  tool_name: 'run_sql',
  status: 'completed',
  args: {},
  result: 'select 1',
}

const mountPanel = () => mount(ConversationWorkspacePanel, {
  props: {
    open: true,
    conversationId: null,
    activeToolCall: null,
    resolveToolCall: (toolCallId: string) => (
      toolCallId === taskCall.tool_call_id ? taskCall : (toolCallId === toolCall.tool_call_id ? toolCall : null)
    ),
  },
  global: {
    stubs: {
      SubagentTrail: true,
      ToolCallDetailPanel: true,
      QueryResultPanel: true,
      ChartSpecRenderer: true,
    },
  },
})

const tabTitles = (wrapper: ReturnType<typeof mountPanel>) => (
  wrapper.findAll('[data-panel-tabs] > div').map(tab => tab.text())
)

describe('ConversationWorkspacePanel tab 容器', () => {
  it('打开工具/子智能体各占一个 tab，同对象再次打开只激活不重复', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }

    vm.openTool(taskCall)
    vm.openTool(toolCall)
    await wrapper.vm.$nextTick()

    expect(tabTitles(wrapper)).toEqual(['文件管理', 'explore 子智能体', '工具详情'])

    vm.openTool(taskCall)
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toHaveLength(3)
  })

  it('关闭当前 tab 后回到相邻 tab，工作区不可关闭', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }
    vm.openTool(taskCall)
    vm.openTool(toolCall)
    await wrapper.vm.$nextTick()

    const tabs = wrapper.findAll('[data-panel-tabs] > div')
    await tabs[1]!.find('button[title="关闭"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['文件管理', '工具详情'])

    // 工作区是常驻 tab，不渲染关闭按钮
    expect(wrapper.findAll('[data-panel-tabs] > div')[0]!.find('button[title="关闭"]').exists()).toBe(false)
  })
})
