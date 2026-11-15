import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import ConversationWorkspacePanel from '../conversation/ConversationWorkspacePanel.vue'
import AgentProcessView from '../conversation/AgentProcessView.vue'
import ConversationSidePanel from '../conversation/ConversationSidePanel.vue'
import type { SubagentTaskRecord, ToolCallInfo } from '../../types'

const SubagentTrailStub = defineComponent({
  name: 'SubagentTrail',
  props: { toolCall: { type: Object, required: true } },
  template: '<div data-subagent-trail-stub :data-background="String(toolCall.args.background)" />',
})

const taskCall: ToolCallInfo = {
  tool_call_id: 'task-1',
  tool_name: 'subagent',
  status: 'completed',
  args: { subagent_type: 'explore' },
  result: '报告',
  subagentTrail: [{ kind: 'message', id: 'm1', content: '结论' }],
}
const taskCall2: ToolCallInfo = {
  tool_call_id: 'task-2',
  tool_name: 'subagent',
  status: 'completed',
  args: { subagent_type: 'general' },
  result: '第二份报告',
  subagentTrail: [{ kind: 'message', id: 'm2', content: '第二个结论' }],
}
const toolCall: ToolCallInfo = {
  tool_call_id: 'tool-1',
  tool_name: 'run_sql',
  status: 'completed',
  args: {},
  result: 'select 1',
}

const backgroundTask: SubagentTaskRecord = {
  task_id: 'bg-task-1',
  conversation_id: 'conv-1',
  parent_run_id: 'run-1',
  parent_tool_call_id: 'call-bg-1',
  subagent_type: 'explore',
  label: '入金趋势',
  description: '分析近 30 天入金趋势',
  status: 'completed',
  status_label: '已完成',
  progress: {},
  created_at: '2026-11-15T10:00:00',
  updated_at: '2026-11-15T10:01:00',
  started_at: '2026-11-15T10:00:01',
  finished_at: '2026-11-15T10:01:00',
  notified_at: null,
  timeout_seconds: 60,
}

const followUpBackgroundTask: SubagentTaskRecord = {
  ...backgroundTask,
  task_id: 'bg-task-2',
  parent_task_id: backgroundTask.task_id,
  root_task_id: backgroundTask.task_id,
  turn_index: 2,
  label: '先给自己起一个响亮的名字',
  description: '先给自己起一个响亮的名字，然后告诉我你叫什么。',
  updated_at: '2026-11-15T10:02:00',
  finished_at: '2026-11-15T10:02:00',
}

const syncTask: SubagentTaskRecord = {
  ...backgroundTask,
  task_id: 'sync-task-1',
  parent_tool_call_id: 'call-sync-1',
  label: '同步核对',
  description: '当前轮同步核对',
  execution_mode: 'sync',
}

const mountPanel = (
  overrides: Record<string, unknown> = {},
  options: { stubs?: Record<string, unknown> } = {},
) => mount(ConversationWorkspacePanel, {
  props: {
    open: true,
    conversationId: null,
    activeToolCall: null,
    resolveToolCall: (toolCallId: string) => {
      if (toolCallId === taskCall.tool_call_id) return taskCall
      if (toolCallId === taskCall2.tool_call_id) return taskCall2
      return toolCallId === toolCall.tool_call_id ? toolCall : null
    },
    ...overrides,
  },
  global: {
    stubs: {
      SubagentTrail: true,

      ToolCallDetailPanel: true,
      QueryResultPanel: true,
      ChartSpecRenderer: true,
      ...options.stubs,
    },
  },
})

const tabTitles = (wrapper: ReturnType<typeof mountPanel>) => (
  wrapper.findAll('[data-panel-tabs] > div').map(tab => tab.text())
)

const flush = () => new Promise(resolve => setTimeout(resolve, 40))

const scrollBody = (wrapper: ReturnType<typeof mountPanel>) => {
  const body = wrapper.find('[data-side-panel-body]').element as HTMLElement
  let scrollTop = 0
  let scrollHeight = 1_200
  Object.defineProperty(body, 'scrollHeight', { configurable: true, get: () => scrollHeight })
  Object.defineProperty(body, 'clientHeight', { configurable: true, value: 300 })
  Object.defineProperty(body, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value: number) => { scrollTop = value },
  })
  return {
    body,
    getScrollTop: () => scrollTop,
    setScrollTop: (value: number) => { scrollTop = value },
    setScrollHeight: (value: number) => { scrollHeight = value },
  }
}

describe('ConversationWorkspacePanel tab 容器', () => {
  it('打开工具/子智能体各占一个 tab，同对象再次打开只激活不重复', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }

    vm.openTool(taskCall)
    vm.openTool(toolCall)
    await wrapper.vm.$nextTick()

    expect(tabTitles(wrapper)).toEqual(['工作台', 'explore 子智能体', '工具详情'])

    vm.openTool(taskCall)
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toHaveLength(3)
  })

  it('多个子智能体 tab 各持有自己的过程视图（切回旧 tab 不会串内容）', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }
    vm.openTool(taskCall)
    vm.openTool(taskCall2)
    await wrapper.vm.$nextTick()

    const views = wrapper.findAllComponents(AgentProcessView)
    expect(views.map(view => view.props('toolCall').tool_call_id)).toEqual(['task-1', 'task-2'])

    // 切回第一个子智能体 tab：显示的是第一个 tab 的视图
    await wrapper.findAll('[data-panel-tabs] > div')[2]!.find('button').trigger('click')
    await wrapper.vm.$nextTick()
    const visible = views.filter(view => (view.element as HTMLElement).style.display !== 'none')
    expect(visible).toHaveLength(1)
  })

  it('主对话点击后台 task 工具卡直接进入任务详情页（与主对话同构的聊天消息）', async () => {
    const backgroundCall: ToolCallInfo = {
      tool_call_id: 'call-bg-1',
      tool_name: 'subagent',
      status: 'completed',
      args: { subagent_type: 'explore', background: true },
      result: '子智能体已在后台启动',
    }
    const wrapper = mountPanel({
      subagentTasks: [backgroundTask],
      subagentTaskDetails: { [backgroundTask.task_id]: backgroundTask },
      resolveToolCall: () => backgroundCall,
    })
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }

    vm.openTool(backgroundCall)
    await wrapper.vm.$nextTick()

    expect(tabTitles(wrapper)).toEqual(['工作台', '入金趋势'])
    expect(wrapper.find('[data-subagent-task-detail]').exists()).toBe(true)
  })

  it('同步 subagent 工具卡与后台任务打开同一套任务详情页', async () => {
    const syncCall: ToolCallInfo = {
      tool_call_id: 'call-sync-1',
      tool_name: 'subagent',
      status: 'completed',
      args: { subagent_type: 'explore', background: false },
      result: '同步核对结论',
    }
    const wrapper = mountPanel({
      subagentTasks: [syncTask],
      subagentTaskDetails: { [syncTask.task_id]: syncTask },
      resolveToolCall: () => syncCall,
    }, { stubs: { SubagentTrail: SubagentTrailStub } })
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }

    vm.openTool(syncCall)
    await wrapper.vm.$nextTick()

    expect(tabTitles(wrapper)).toEqual(['工作台', '同步核对'])
    expect(wrapper.find('[data-subagent-task-detail]').exists()).toBe(true)
    expect(wrapper.find('[data-subagent-trail-stub]').attributes('data-background')).toBe('false')
  })

  it('历史 task 工具卡仍打开子智能体过程页', async () => {
    const legacyCall: ToolCallInfo = {
      ...taskCall,
      tool_call_id: 'legacy-task-1',
      tool_name: 'task',
    }
    const wrapper = mountPanel({ resolveToolCall: () => legacyCall })
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }

    vm.openTool(legacyCall)
    await wrapper.vm.$nextTick()

    expect(tabTitles(wrapper)).toEqual(['工作台', 'explore 子智能体'])
    expect(wrapper.findComponent(AgentProcessView).exists()).toBe(true)
  })

  it('后台任务独立 tab：同一 task_id 只激活不重复，详情来自任务账本', async () => {
    const detailedTask = {
      ...backgroundTask,
      result_meta: {
        subagent_context_usage: { used_tokens: 1_000, context_window: 10_000, percent: 10 },
        subagent_compactions: [{ id: 'compact-1', compressed_messages: 3 }],
      },
    }
    const wrapper = mountPanel({
      subagentTasks: [detailedTask],
      subagentTaskDetails: { [backgroundTask.task_id]: detailedTask },
    })
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(backgroundTask)
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '入金趋势'])
    expect(wrapper.find('[data-subagent-task-detail]').exists()).toBe(true)
    const usage = wrapper.find('[data-subagent-task-usage]')
    expect(usage.exists()).toBe(true)
    expect(usage.find('button').attributes('title')).toContain('上下文占用')
    expect(wrapper.text()).toContain('已压缩 1 次')

    vm.openSubagentTask(backgroundTask)
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toHaveLength(2)
  })

  it('任务详情只展示过程，不提供用户直接追问的输入框', async () => {
    const wrapper = mountPanel({
      conversationId: 'conv-1',
      subagentTasks: [backgroundTask],
      subagentTaskDetails: { [backgroundTask.task_id]: backgroundTask },
    })
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(backgroundTask)
    await wrapper.vm.$nextTick()

    expect(wrapper.find('[data-subagent-follow-up]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('发送追问')
  })

  it('同一子会话的多轮追问在管理页只显示一张智能体卡片', async () => {
    const loadTaskDetail = vi.fn().mockResolvedValue(null)
    const wrapper = mountPanel({
      subagentTasks: [backgroundTask, followUpBackgroundTask],
      subagentTaskDetails: {
        [backgroundTask.task_id]: backgroundTask,
        [followUpBackgroundTask.task_id]: followUpBackgroundTask,
      },
      loadSubagentTaskDetail: loadTaskDetail,
    })
    const vm = wrapper.vm as unknown as { openSubagentManager: () => void }

    vm.openSubagentManager()
    await wrapper.vm.$nextTick()

    const cards = wrapper.findAll('[data-subagent-task-card]')
    expect(cards).toHaveLength(1)
    expect(cards[0]!.find('[data-subagent-task-title]').text()).toContain('入金趋势')
    expect(cards[0]!.find('[data-subagent-task-rounds]').text()).toContain('2 轮问答')
    expect(cards[0]!.text()).not.toContain('追问 1')
    expect(loadTaskDetail).toHaveBeenCalledWith(followUpBackgroundTask.task_id, false)

    await cards[0]!.trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '子智能体管理', '入金趋势'])
  })

  it('同一子会话的追问复用任务 tab，并切换到最新一轮', async () => {
    const wrapper = mountPanel({
      subagentTasks: [backgroundTask],
      subagentTaskDetails: { [backgroundTask.task_id]: backgroundTask },
    })
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(backgroundTask)
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '入金趋势'])

    await wrapper.setProps({
      subagentTasks: [backgroundTask, followUpBackgroundTask],
      subagentTaskDetails: {
        [backgroundTask.task_id]: backgroundTask,
        [followUpBackgroundTask.task_id]: followUpBackgroundTask,
      },
    })
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '入金趋势'])

    const detail = wrapper.findComponent({ name: 'SubagentTaskProcessView' })
    expect((detail.props('task') as SubagentTaskRecord).task_id).toBe(followUpBackgroundTask.task_id)
    expect((detail.props('sessionTasks') as SubagentTaskRecord[]).map(task => task.task_id)).toEqual([
      backgroundTask.task_id,
      followUpBackgroundTask.task_id,
    ])
  })

  it('任务详情副标题不重复展示最近工具调用', async () => {
    const runningTask: SubagentTaskRecord = {
      ...backgroundTask,
      status: 'running',
      status_label: '执行中',
      finished_at: null,
      progress: { phase: 'running', last_tool: 'file_read', tool_calls: 3 },
    }
    const wrapper = mountPanel({
      subagentTasks: [runningTask],
      subagentTaskDetails: { [runningTask.task_id]: runningTask },
    })
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(runningTask)
    await wrapper.vm.$nextTick()

    const subheader = wrapper.find('[data-subagent-task-subheader]')
    const status = subheader.find('[data-subagent-task-status]')
    expect(status.text()).toContain('explore')
    expect(status.attributes('title')).toBe('执行中')
    expect(subheader.text()).not.toContain('最近调用')
    expect(subheader.text()).not.toContain('已调用')
  })

  it('任务详情支持聊天/工具模式切换，工具模式停止自动贴底', async () => {
    const wrapper = mountPanel({
      subagentTasks: [backgroundTask],
      subagentTaskDetails: { [backgroundTask.task_id]: backgroundTask },
    })
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(backgroundTask)
    await wrapper.vm.$nextTick()

    const detail = wrapper.findComponent({ name: 'SubagentTaskProcessView' })
    expect(detail.props('viewMode')).toBe('agent')
    expect(wrapper.findComponent(ConversationSidePanel).props('autoFollow')).toBe(true)

    await wrapper.find('[data-subagent-view-generic]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(detail.props('viewMode')).toBe('generic')
    expect(wrapper.findComponent(ConversationSidePanel).props('autoFollow')).toBe(false)
  })

  it('后台任务运行中实时用量回传后，副标题展示上下文占用环', async () => {
    const runningTask: SubagentTaskRecord = {
      ...backgroundTask,
      status: 'running',
      status_label: '执行中',
      finished_at: null,
      progress: { phase: 'running' },
    }
    const ProcessViewStub = defineComponent({
      name: 'SubagentTaskProcessView',
      props: { task: { type: Object, required: true } },
      emits: ['selectTool', 'usageUpdated'],
      template: '<div data-subagent-task-detail />',
    })
    const wrapper = mountPanel({
      subagentTasks: [runningTask],
      subagentTaskDetails: { [runningTask.task_id]: runningTask },
    }, { stubs: { SubagentTaskProcessView: ProcessViewStub } })
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(runningTask)
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-subagent-task-usage]').exists()).toBe(false)

    wrapper.findComponent(ProcessViewStub).vm.$emit('usageUpdated', runningTask.task_id, {
      used: 3_200,
      total: 32_000,
      percentage: 10,
    })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-subagent-task-usage]').exists()).toBe(true)
  })

  it('右侧默认进入管理首页，点击卡片进入文件管理和子智能体管理', async () => {
    const wrapper = mountPanel()
    expect(wrapper.find('[data-right-sidebar-home]').exists()).toBe(true)
    expect(tabTitles(wrapper)).toEqual(['工作台'])

    await wrapper.find('[data-sidebar-home-files]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '文件管理'])

    await wrapper.findAll('[data-panel-tabs] > div')[0]!.find('button').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.find('[data-sidebar-home-subagents]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '文件管理', '子智能体管理'])
    expect(wrapper.find('[data-subagent-manager]').exists()).toBe(true)
    expect(wrapper.find('[data-subagent-manager-filters]').exists()).toBe(false)
    expect(wrapper.find('[data-subagent-manager]').text()).not.toContain('后台任务持续在服务端执行')
  })

  it('子智能体管理页可切到任务详情 tab，且只保留一份详情', async () => {
    const wrapper = mountPanel({ subagentTasks: [backgroundTask] })
    const vm = wrapper.vm as unknown as { openSubagentManager: () => void }
    vm.openSubagentManager()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-subagent-manager]').exists()).toBe(true)

    const card = wrapper.find('[data-subagent-task-card]')
    expect(card.exists()).toBe(true)
    expect(card.find('[data-subagent-task-title]').text()).toContain('explore-入金趋势')
    expect(card.find('[data-subagent-task-progress]').exists()).toBe(false)
    expect(card.find('[data-subagent-task-footer] [data-subagent-task-status]').text()).toContain('已完成')
    expect(card.text()).not.toContain('任务记录已保留')

    await wrapper.find('[data-subagent-manager] [role="button"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '子智能体管理', '入金趋势'])
    expect(wrapper.find('[data-subagent-task-detail]').exists()).toBe(true)
  })

  it('子智能体管理页可暂停运行中的后台任务', async () => {
    const running: SubagentTaskRecord = {
      ...backgroundTask,
      status: 'running',
      status_label: '执行中',
      finished_at: null,
    }
    const pauseSubagentTask = vi.fn().mockResolvedValue(undefined)
    const wrapper = mountPanel({ subagentTasks: [running], pauseSubagentTask })
    const vm = wrapper.vm as unknown as { openSubagentManager: () => void }
    vm.openSubagentManager()
    await wrapper.vm.$nextTick()

    const button = wrapper.find('[data-subagent-task-pause]')
    expect(button.exists()).toBe(true)
    await button.trigger('click')
    expect(pauseSubagentTask).toHaveBeenCalledWith(expect.objectContaining({ task_id: 'bg-task-1' }))
  })

  it('首次打开后台任务详情停在最新，并开启贴底跟随', async () => {
    const wrapper = mountPanel({
      subagentTasks: [backgroundTask],
      subagentTaskDetails: { [backgroundTask.task_id]: backgroundTask },
    })
    const scroll = scrollBody(wrapper)
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(backgroundTask)
    await flush()

    expect(wrapper.findComponent(ConversationSidePanel).props('autoFollow')).toBe(true)
    expect(scroll.getScrollTop()).toBe(1_200)
  })

  it('任务详情上翻阅读后暂停跟随，回到底部附近后恢复跟随', async () => {
    const wrapper = mountPanel({
      subagentTasks: [backgroundTask],
      subagentTaskDetails: { [backgroundTask.task_id]: backgroundTask },
    })
    const scroll = scrollBody(wrapper)
    const vm = wrapper.vm as unknown as { openSubagentTask: (task: SubagentTaskRecord) => void }

    vm.openSubagentTask(backgroundTask)
    await flush()

    // 用户上翻（距底超过 100px）：后续内容增长不得抢走阅读位置
    scroll.setScrollTop(200)
    scroll.body.dispatchEvent(new Event('scroll'))
    await flush()
    const growing = document.createElement('div')
    growing.textContent = '继续增长的过程内容'
    scroll.body.appendChild(growing)
    await flush()
    expect(scroll.getScrollTop()).toBe(200)

    // 用户滚回底部附近：恢复跟随，新内容可自动滚到最新
    scroll.setScrollTop(1_100)
    scroll.body.dispatchEvent(new Event('scroll'))
    await flush()
    scroll.setScrollHeight(2_000)
    growing.textContent = '恢复跟随后的新内容'
    await flush()
    expect(scroll.getScrollTop()).toBe(2_000)
  })

  it('从任务详情切到其他工具再切回，恢复原来的阅读位置', async () => {
    const wrapper = mountPanel({
      subagentTasks: [backgroundTask],
      subagentTaskDetails: { [backgroundTask.task_id]: backgroundTask },
    })
    const scroll = scrollBody(wrapper)
    const vm = wrapper.vm as unknown as {
      openSubagentTask: (task: SubagentTaskRecord) => void
      openTool: (call: ToolCallInfo) => void
    }

    vm.openSubagentTask(backgroundTask)
    await flush()
    scroll.setScrollTop(200)
    scroll.body.dispatchEvent(new Event('scroll'))

    vm.openTool(toolCall)
    await flush()
    await wrapper.findAll('[data-panel-tabs] > div')[1]!.find('button').trigger('click')
    await flush()

    expect(scroll.getScrollTop()).toBe(200)
  })

  it('关闭当前 tab 后回到相邻 tab，管理首页不可关闭', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as unknown as { openTool: (call: ToolCallInfo) => void }
    vm.openTool(taskCall)
    vm.openTool(toolCall)
    await wrapper.vm.$nextTick()

    const tabs = wrapper.findAll('[data-panel-tabs] > div')
    await tabs[1]!.find('button[title="关闭"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '工具详情'])

    // 管理首页是常驻 tab，不渲染关闭按钮
    expect(wrapper.findAll('[data-panel-tabs] > div')[0]!.find('button[title="关闭"]').exists()).toBe(false)
  })

  it('文件管理页可关闭并回到管理首页', async () => {
    const wrapper = mountPanel()
    await wrapper.find('[data-sidebar-home-files]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台', '文件管理'])

    await wrapper.findAll('[data-panel-tabs] > div')[1]!.find('button[title="关闭"]').trigger('click')
    await wrapper.vm.$nextTick()
    expect(tabTitles(wrapper)).toEqual(['工作台'])
    expect(wrapper.find('[data-right-sidebar-home]').exists()).toBe(true)
  })
})
