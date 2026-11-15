import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  listSubagentTasks: vi.fn(),
  getSubagentTask: vi.fn(),
  cancelSubagentTask: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

import { groupSubagentTaskSessions, mergeSubagentTasks, useSubagentTasks } from '../useSubagentTasks'
import type { SubagentTaskRecord } from '../../types'

const task = (overrides: Partial<SubagentTaskRecord> = {}): SubagentTaskRecord => ({
  task_id: 'task-1',
  conversation_id: 'conv-1',
  parent_run_id: 'run-1',
  parent_tool_call_id: 'call-1',
  subagent_type: 'explore',
  label: '入金趋势',
  description: '分析近 30 天入金趋势',
  status: 'running',
  status_label: '执行中',
  progress: { phase: 'running', last_tool: 'file_read' },
  created_at: '2026-11-15T10:00:00',
  updated_at: '2026-11-15T10:00:00',
  started_at: '2026-11-15T10:00:01',
  finished_at: null,
  notified_at: null,
  timeout_seconds: 60,
  ...overrides,
})

describe('mergeSubagentTasks', () => {
  it('按 task_id 去重，列表帧不覆盖已加载的详情字段', () => {
    const detailed = task({ report: '完整报告', result_meta: { subagent_trail: [] } })
    const listFrame = task({ status: 'completed', status_label: '已完成', updated_at: '2026-11-15T10:01:00' })

    const merged = mergeSubagentTasks([detailed], [listFrame])

    expect(merged).toHaveLength(1)
    expect(merged[0]!.status).toBe('completed')
    expect(merged[0]!.report).toBe('完整报告')
    expect(merged[0]!.result_meta).toEqual({ subagent_trail: [] })
  })

  it('活跃任务排在终态任务前，其余按更新时间倒序', () => {
    const merged = mergeSubagentTasks([], [
      task({ task_id: 'done-old', status: 'completed', updated_at: '2026-11-15T09:00:00' }),
      task({ task_id: 'run-new', status: 'running', updated_at: '2026-11-15T10:00:00' }),
      task({ task_id: 'done-new', status: 'completed', updated_at: '2026-11-15T11:00:00' }),
    ])

    expect(merged.map(item => item.task_id)).toEqual(['run-new', 'done-new', 'done-old'])
  })
})

describe('groupSubagentTaskSessions', () => {
  it('同一子会话的追问归并为一个智能体，保留首轮名称并统计轮次', () => {
    const sessions = groupSubagentTaskSessions([
      task({ task_id: 'follow-1', parent_task_id: 'root-1', root_task_id: 'root-1', turn_index: 2, label: '补充分析', status: 'completed' }),
      task({ task_id: 'root-1', root_task_id: 'root-1', turn_index: 1, label: '语义模型目录盘点', status: 'completed' }),
    ])

    expect(sessions).toHaveLength(1)
    expect(sessions[0]!.sessionId).toBe('root-1')
    expect(sessions[0]!.label).toBe('语义模型目录盘点')
    expect(sessions[0]!.turnCount).toBe(2)
    expect(sessions[0]!.latestTask.task_id).toBe('follow-1')
  })

  it('新数据优先按 agent_session_id 聚合，root_task_id 只做兼容兜底', () => {
    const sessions = groupSubagentTaskSessions([
      task({
        task_id: 'root-1',
        agent_id: 'agent-1',
        agent_session_id: 'agent-1',
        root_task_id: 'legacy-root',
        turn_index: 1,
        status: 'completed',
      }),
      task({
        task_id: 'follow-1',
        agent_id: 'agent-1',
        agent_session_id: 'agent-1',
        root_task_id: 'legacy-root',
        parent_task_id: 'root-1',
        turn_index: 2,
        status: 'completed',
      }),
    ])

    expect(sessions).toHaveLength(1)
    expect(sessions[0]!.sessionId).toBe('agent-1')
    expect(sessions[0]!.turnCount).toBe(2)
  })
})

const mountComposable = () => {
  let state!: ReturnType<typeof useSubagentTasks>
  const wrapper = mount(defineComponent({
    setup() {
      state = useSubagentTasks()
      return () => h('div')
    },
  }))
  return { state, wrapper }
}

describe('useSubagentTasks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('取消任务调用接口并把 cancelling 快照合并回列表', async () => {
    apiMocks.listSubagentTasks.mockResolvedValue([task()])
    apiMocks.cancelSubagentTask.mockResolvedValue(task({ status: 'cancelling', status_label: '正在取消' }))

    const { state, wrapper } = mountComposable()
    await state.setConversation('conv-1')
    const updated = await state.cancelTask('task-1')

    expect(apiMocks.cancelSubagentTask).toHaveBeenCalledWith('conv-1', 'task-1')
    expect(updated?.status).toBe('cancelling')
    expect(state.tasks.value[0]!.status).toBe('cancelling')
    wrapper.unmount()
  })

  it('打开中的详情跟随列表刷新，任务收口后补拉完整报告与过程', async () => {
    apiMocks.listSubagentTasks
      .mockResolvedValueOnce([task()])
      .mockResolvedValueOnce([task({ status: 'completed', status_label: '已完成' })])
    apiMocks.getSubagentTask
      .mockResolvedValueOnce(task())
      .mockResolvedValueOnce(task({ status: 'completed', report: '最终报告', result_meta: { subagent_trail: [{ kind: 'message', id: 'm1', content: '过程' }] } }))

    const { state, wrapper } = mountComposable()
    await state.setConversation('conv-1')
    await state.loadTaskDetail('task-1')
    await state.loadTasks(false)
    await new Promise(resolve => setTimeout(resolve, 0))

    expect(state.details.value['task-1']!.status).toBe('completed')
    expect(state.details.value['task-1']!.report).toBe('最终报告')
    expect(state.details.value['task-1']!.result_meta?.subagent_trail).toHaveLength(1)
    wrapper.unmount()
  })

  it('SSE 快照只合并当前会话，并保留已加载详情', async () => {
    apiMocks.listSubagentTasks.mockResolvedValue([task()])
    apiMocks.getSubagentTask.mockResolvedValue(task({ report: '详细报告' }))

    const { state, wrapper } = mountComposable()
    await state.setConversation('conv-1')
    await state.loadTaskDetail('task-1')

    state.applyTaskUpdate(task({ conversation_id: 'conv-other', status: 'completed' }))
    expect(state.tasks.value[0]!.status).toBe('running')

    state.applyTaskUpdate(task({ status: 'completed', status_label: '已完成' }))
    expect(state.tasks.value[0]!.status).toBe('completed')
    expect(state.details.value['task-1']!.report).toBe('详细报告')
    wrapper.unmount()
  })
})
