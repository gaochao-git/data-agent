import { describe, expect, it } from 'vitest'
import type { SubagentTrailItem, ToolLifecycleEventData } from '../../types'
import { applySubagentTrailEvent } from '../subagentTrail'

const event = (value: Record<string, unknown>) => value as unknown as ToolLifecycleEventData

describe('applySubagentTrailEvent', () => {
  it('拼接正文增量并用完整消息只补截断标记', () => {
    let trail: SubagentTrailItem[] = []
    trail = applySubagentTrailEvent(trail, event({
      type: 'subagent_message_delta',
      message_id: 'm1',
      delta: '你好',
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'subagent_message_delta',
      message_id: 'm1',
      delta: '，世界',
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'subagent_message',
      message_id: 'm1',
      content: '不应覆盖增量全文',
      streamed: true,
      truncated: true,
    }))

    expect(trail).toEqual([
      { kind: 'message', id: 'm1', content: '你好，世界', truncated: true },
    ])
  })

  it('合并工具开始与完成事件并保留计时', () => {
    let trail: SubagentTrailItem[] = []
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_started',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      args: { file_path: '/a.md' },
      started_at_ms: 100,
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_completed',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      content: 'ok',
      ended_at_ms: 180,
    }))

    expect(trail).toHaveLength(1)
    expect(trail[0]).toMatchObject({
      kind: 'tool',
      id: 'tool1',
      status: 'completed',
      result: 'ok',
      started_at_ms: 100,
      ended_at_ms: 180,
    })
  })

  it('非 trail 事件保持原数组不变', () => {
    const trail: SubagentTrailItem[] = []
    const next = applySubagentTrailEvent(trail, event({
      type: 'subagent_context_usage',
      used_tokens: 10,
    }))
    expect(next).toBe(trail)
  })
})
