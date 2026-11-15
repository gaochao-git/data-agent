import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SubagentTaskRow from '../conversation/SubagentTaskRow.vue'
import type { SubagentTaskRecord } from '../../types'

const task = (overrides: Partial<SubagentTaskRecord> = {}): SubagentTaskRecord => ({
  task_id: 'task-1',
  conversation_id: 'conv-1',
  parent_run_id: 'run-1',
  parent_tool_call_id: 'call-1',
  subagent_type: 'explore',
  label: 'Task',
  description: 'Task description',
  status: 'running',
  status_label: 'Running',
  execution_mode: 'background',
  progress: {},
  created_at: '2026-11-15T10:00:00',
  updated_at: '2026-11-15T10:01:00',
  started_at: '2026-11-15T10:00:01',
  finished_at: null,
  notified_at: null,
  timeout_seconds: 60,
  ...overrides,
})

describe('SubagentTaskRow', () => {
  it('shows cancel for an active sync task', () => {
    const wrapper = mount(SubagentTaskRow, {
      props: { task: task({ execution_mode: 'sync' }) },
    })

    expect(wrapper.find('[data-subagent-task-cancel]').exists()).toBe(true)
  })

  it('shows cancel for an active background task and emits cancel', async () => {
    const background = task()
    const wrapper = mount(SubagentTaskRow, {
      props: { task: background },
    })
    const button = wrapper.find('[data-subagent-task-cancel]')

    expect(button.exists()).toBe(true)
    await button.trigger('click')
    expect(wrapper.emitted('cancel')?.[0]?.[0]).toEqual(background)
  })

  it('shows pause for a running background task and emits pause', async () => {
    const background = task()
    const wrapper = mount(SubagentTaskRow, {
      props: { task: background },
    })
    const button = wrapper.find('[data-subagent-task-pause]')

    expect(button.exists()).toBe(true)
    await button.trigger('click')
    expect(wrapper.emitted('pause')?.[0]?.[0]).toEqual(background)
  })

  it('hides pause for sync, paused and cancelling tasks', () => {
    for (const overrides of [
      { execution_mode: 'sync' as const },
      { status: 'paused' as const },
      { status: 'cancelling' as const },
    ]) {
      const wrapper = mount(SubagentTaskRow, {
        props: { task: task(overrides) },
      })
      expect(wrapper.find('[data-subagent-task-pause]').exists()).toBe(false)
    }
  })

  it('shows session-level rounds, tool calls and first/last timestamps', () => {
    const first = task({
      task_id: 'task-1',
      status: 'completed',
      status_label: '已完成',
      progress: { tool_calls: 3 },
      created_at: '2026-11-15T10:00:00',
      finished_at: '2026-11-15T10:01:00',
    })
    const second = task({
      task_id: 'task-2',
      parent_task_id: first.task_id,
      turn_index: 2,
      status: 'cancelled',
      status_label: '已取消',
      progress: { tool_calls: 2 },
      created_at: '2026-11-15T10:02:00',
      finished_at: '2026-11-15T10:03:00',
      result_meta: {
        subagent_token_usage_detail: {
          used: 12_800,
          total: 128_000,
          current_input_tokens: 12_800,
          compression_trigger_tokens: 44_800,
          context_window: 128_000,
        },
      },
    })
    const wrapper = mount(SubagentTaskRow, {
      props: { task: second, sessionTasks: [first, second], sessionTurnCount: 2 },
    })

    expect(wrapper.text()).not.toContain('追问')
    expect(wrapper.text()).not.toContain('报告已生成')
    expect(wrapper.find('[data-subagent-task-rounds]').text()).toContain('2 轮问答')
    expect(wrapper.find('[data-subagent-task-tool-count]').text()).toContain('5 次工具调用')
    const context = wrapper.find('[data-subagent-task-context]')
    expect(context.text()).toContain('上下文 10%')
    expect(context.attributes('title')).toBe('当前输入 12.8k · 压缩阈值 44.8k · 上下文窗口 128k')
    const taskType = wrapper.find('[data-subagent-task-title] span')
    expect(taskType.text()).toBe('explore')
    expect(taskType.classes()).toContain('font-bold')
    expect(wrapper.find('[data-subagent-task-created]').text()).toContain('创建 2026-11-15 10:00')
    expect(wrapper.find('[data-subagent-task-finished]').text()).toContain('结束 2026-11-15 10:03')
    expect(wrapper.findAll('[data-subagent-task-meta-row]')).toHaveLength(2)
  })
})
