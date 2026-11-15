import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import SubagentTaskRow from '../conversation/SubagentTaskRow.vue'
import type { SubagentTaskRecord } from '../../types'

const { copyTextToClipboard } = vi.hoisted(() => ({ copyTextToClipboard: vi.fn().mockResolvedValue(true) }))
vi.mock('../../utils/clipboard', () => ({ copyTextToClipboard }))

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
  it('renders agent type as a tag and copies the task id', async () => {
    const background = task({ subagent_type: 'general' })
    const wrapper = mount(SubagentTaskRow, {
      props: { task: background },
    })

    expect(wrapper.find('[data-subagent-task-agent-tag]').text()).toBe('general')
    const copyButton = wrapper.find('[data-subagent-task-copy-id]')
    expect(copyButton.attributes('title')).toContain('task-1')
    await copyButton.trigger('click')
    expect(copyTextToClipboard).toHaveBeenCalledWith('task-1', '已复制任务 ID')
    expect(wrapper.emitted('open')).toBeUndefined()
  })

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
    expect(button.text()).toContain('取消')
    await button.trigger('click')
    expect(wrapper.emitted('cancel')?.[0]?.[0]).toEqual(background)
  })

  it('shows cancelling state on the cancel button', () => {
    const wrapper = mount(SubagentTaskRow, {
      props: { task: task(), cancelling: true },
    })

    const button = wrapper.find('[data-subagent-task-cancel]')
    expect(button.text()).toContain('取消中')
    expect(button.attributes('disabled')).toBeDefined()
  })

  it('shows session-level rounds, tool calls and first/last timestamps', () => {
    const first = task({
      task_id: 'task-1',
      status: 'completed',
      status_label: '已完成',
      progress: { tool_calls: 3 },
      created_at: '2026-11-15T10:00:00',
      finished_at: '2026-11-15T10:01:00',
      compaction_count: 2,
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
      compaction_count: 1,
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
    expect(wrapper.find('[data-subagent-task-compactions]').text()).toContain('压缩 3 次')
    const context = wrapper.find('[data-subagent-task-context]')
    expect(context.text()).toContain('上下文 10%')
    expect(context.attributes('title')).toBe('当前输入 12.8k · 压缩阈值 44.8k · 上下文窗口 128k')
    const taskType = wrapper.find('[data-subagent-task-agent-tag]')
    expect(taskType.text()).toBe('explore')
    expect(taskType.classes()).toContain('rounded-sm')
    expect(taskType.classes()).toContain('font-semibold')
    expect(wrapper.find('[data-subagent-task-created]').text()).toContain('创建 2026-11-15 10:00')
    expect(wrapper.find('[data-subagent-task-finished]').text()).toContain('结束 2026-11-15 10:03')
    expect(wrapper.findAll('[data-subagent-task-meta-row]')).toHaveLength(2)
  })

  it('shows context usage from progress while the task is running or cancelled', () => {
    const running = task({
      status: 'running',
      progress: {
        tool_calls: 1,
        context_usage: {
          used_tokens: 25_600,
          threshold_tokens: 64_000,
          context_window: 128_000,
          percent: 20,
        },
      },
    })
    const runningWrapper = mount(SubagentTaskRow, { props: { task: running } })
    expect(runningWrapper.find('[data-subagent-task-context]').text()).toContain('上下文 20%')

    const cancelled = task({
      status: 'cancelled',
      status_label: '已取消',
      progress: {
        context_usage: {
          used_tokens: 38_400,
          threshold_tokens: 64_000,
          context_window: 128_000,
          percent: 30,
        },
      },
    })
    const cancelledWrapper = mount(SubagentTaskRow, { props: { task: cancelled } })
    expect(cancelledWrapper.find('[data-subagent-task-context]').text()).toContain('上下文 30%')
  })
})
