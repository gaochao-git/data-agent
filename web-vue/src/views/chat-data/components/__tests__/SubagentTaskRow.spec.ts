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
  it('hides cancel for an active sync task', () => {
    const wrapper = mount(SubagentTaskRow, {
      props: { task: task({ execution_mode: 'sync' }) },
    })

    expect(wrapper.find('[data-subagent-task-cancel]').exists()).toBe(false)
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
})
