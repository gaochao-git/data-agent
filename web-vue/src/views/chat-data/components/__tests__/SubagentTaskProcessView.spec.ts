import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it } from 'vitest'
import SubagentTaskProcessView from '../conversation/SubagentTaskProcessView.vue'
import type { SubagentTaskRecord } from '../../types'

const TrailStub = defineComponent({
  name: 'SubagentTrail',
  props: {
    toolCall: { type: Object, required: true },
    datasourceId: { type: String, default: '' },
    executionTargets: { type: Array, default: () => [] },
    onSave: { type: Function, default: undefined },
  },
  emits: ['selectTool'],
  template: '<div data-subagent-trail-stub :data-result="toolCall.result" :data-description="toolCall.args.dispatch.description" :data-background="String(toolCall.args.dispatch.background)" />',
})

const ToolDetailStub = defineComponent({
  name: 'ToolCallDetailPanel',
  props: { toolCall: { type: Object, required: true } },
  template: '<div data-tool-detail-stub :data-result="toolCall.result" :data-description="toolCall.args.dispatch.description" />',
})

const turn = (overrides: Partial<SubagentTaskRecord> = {}): SubagentTaskRecord => ({
  task_id: 'task-1',
  conversation_id: 'conv-1',
  parent_run_id: 'run-1',
  parent_tool_call_id: 'call-1',
  root_task_id: 'task-1',
  turn_index: 1,
  subagent_type: 'explore',
  label: '语义模型目录盘点',
  description: '盘点语义模型目录',
  status: 'completed',
  status_label: '已完成',
  progress: {},
  created_at: '2026-11-15T10:00:00',
  updated_at: '2026-11-15T10:01:00',
  started_at: '2026-11-15T10:00:00',
  finished_at: '2026-11-15T10:01:00',
  notified_at: null,
  timeout_seconds: 60,
  report: '第一轮报告',
  ...overrides,
})

describe('SubagentTaskProcessView', () => {
  it('同一子会话按轮次连续渲染多轮问答', () => {
    const onSave = () => {}
    const executionTargets = [{ member_datasource_id: 'target-1', label: '默认执行机构' }]
    const first = turn()
    const second = turn({
      task_id: 'task-2',
      parent_task_id: first.task_id,
      turn_index: 2,
      label: '先给自己起一个响亮的名字',
      description: '先给自己起一个响亮的名字',
      report: '第二轮报告',
      created_at: '2026-11-15T10:02:00',
      updated_at: '2026-11-15T10:03:00',
      started_at: '2026-11-15T10:02:00',
      finished_at: '2026-11-15T10:03:00',
    })

    const wrapper = mount(SubagentTaskProcessView, {
      props: {
        task: second,
        sessionTasks: [first, second],
        conversationId: 'conv-1',
        datasourceId: 'ds-1',
        executionTargets,
        onSave,
      },
      global: { stubs: { SubagentTrail: TrailStub } },
    })

    const turns = wrapper.findAll('[data-subagent-turn]')
    expect(turns).toHaveLength(2)
    expect(turns.map(item => item.find('[data-subagent-trail-stub]').attributes('data-result'))).toEqual([
      '第一轮报告',
      '第二轮报告',
    ])
    expect(turns[0]!.find('[data-subagent-trail-stub]').attributes('data-description')).toBe('盘点语义模型目录')
    expect(turns[1]!.find('[data-subagent-trail-stub]').attributes('data-description')).toBe('先给自己起一个响亮的名字')
    const trail = wrapper.findComponent(TrailStub)
    expect(trail.props('datasourceId')).toBe('ds-1')
    expect(trail.props('executionTargets')).toEqual(executionTargets)
    expect(trail.props('onSave')).toBe(onSave)
  })

  it('同步任务详情保留同步执行语义，不伪装成后台任务', () => {
    const synchronous = turn({ execution_mode: 'sync' })
    const wrapper = mount(SubagentTaskProcessView, {
      props: { task: synchronous, sessionTasks: [synchronous], conversationId: 'conv-1' },
      global: { stubs: { SubagentTrail: TrailStub } },
    })

    expect(wrapper.find('[data-subagent-trail-stub]').attributes('data-background')).toBe('false')
  })

  it('工具模式按轮展示派发参数与结果', () => {
    const first = turn()
    const second = turn({
      task_id: 'task-2',
      parent_task_id: first.task_id,
      turn_index: 2,
      description: '第二轮任务书',
      report: '第二轮报告',
    })
    const wrapper = mount(SubagentTaskProcessView, {
      props: {
        task: second,
        sessionTasks: [first, second],
        conversationId: 'conv-1',
        viewMode: 'generic',
      },
      global: { stubs: { SubagentTrail: TrailStub, ToolCallDetailPanel: ToolDetailStub } },
    })

    const details = wrapper.findAll('[data-tool-detail-stub]')
    expect(details).toHaveLength(2)
    expect(details.map(item => item.attributes('data-description'))).toEqual(['盘点语义模型目录', '第二轮任务书'])
    expect(details.map(item => item.attributes('data-result'))).toEqual(['第一轮报告', '第二轮报告'])
    expect(wrapper.text()).toContain('第 1 轮')
    expect(wrapper.text()).toContain('第 2 轮')
  })

  it('工具模式在失败任务中展示错误信息而不是执行中占位', () => {
    const failed = turn({
      status: 'failed',
      status_label: '执行失败',
      report: '',
      error: '模型调用超时',
    })
    const wrapper = mount(SubagentTaskProcessView, {
      props: {
        task: failed,
        sessionTasks: [failed],
        conversationId: 'conv-1',
        viewMode: 'generic',
      },
      global: { stubs: { SubagentTrail: TrailStub, ToolCallDetailPanel: ToolDetailStub } },
    })

    expect(wrapper.find('[data-tool-detail-stub]').attributes('data-result')).toBe('错误：模型调用超时')
  })

  it('详情页上下文用量以快照为准，token 明细只补充明细', () => {
    const taskWithUsage = turn({
      result_meta: {
        subagent_context_usage: {
          used_tokens: 20_000,
          threshold_tokens: 64_000,
          context_window: 128_000,
          percent: 16,
        },
        subagent_token_usage_detail: {
          used: 50_000,
          total: 128_000,
          percentage: 39,
          context_window: 128_000,
          compression_trigger_tokens: 64_000,
        },
      },
    })
    const wrapper = mount(SubagentTaskProcessView, {
      props: { task: taskWithUsage, sessionTasks: [taskWithUsage], conversationId: 'conv-1' },
      global: { stubs: { SubagentTrail: TrailStub } },
    })

    const emitted = wrapper.emitted('usageUpdated') || []
    const usage = emitted[emitted.length - 1]?.[1] as { used: number; percentage?: number } | undefined
    expect(usage?.used).toBe(20_000)
    expect(usage?.percentage).toBe(16)
  })
})
