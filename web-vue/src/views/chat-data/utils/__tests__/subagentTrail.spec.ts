import { describe, expect, it } from 'vitest'
import type { SubagentTrailItem, ToolLifecycleEventData } from '../../types'
import { applySubagentTrailEvent } from '../subagentTrail'

const event = (value: Record<string, unknown>) => value as unknown as ToolLifecycleEventData

describe('applySubagentTrailEvent', () => {
  it('直播增量已拼出全文时，截断预览不覆盖更长正文', () => {
    let trail: SubagentTrailItem[] = []
    trail = applySubagentTrailEvent(trail, event({
      type: 'subagent_message_delta',
      message_id: 'm1',
      delta: '你好',
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'subagent_message_delta',
      message_id: 'm1',
      delta: '，世界，这是完整正文',
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'subagent_message',
      message_id: 'm1',
      content: '你好，世界…',
      streamed: true,
      truncated: true,
    }))

    expect(trail).toEqual([
      { kind: 'message', id: 'm1', content: '你好，世界，这是完整正文', truncated: true },
    ])
  })

  it('晚加入只收到尾部增量时，完整帧补齐正文与思考', () => {
    let trail = applySubagentTrailEvent([], event({
      type: 'subagent_message_delta',
      message_id: 'm1',
      delta: '尾部片段',
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'subagent_message',
      message_id: 'm1',
      content: '开头被游标跳过的完整正文，尾部片段',
      streamed: true,
      thinking: '完整思考过程',
      thinking_truncated: false,
    }))

    expect(trail).toEqual([
      {
        kind: 'message',
        id: 'm1',
        content: '开头被游标跳过的完整正文，尾部片段',
        truncated: false,
        thinking: '完整思考过程',
        thinkingTruncated: false,
      },
    ])
  })

  it('快照晚加入时用完整消息帧补齐正文与思考', () => {
    const trail = applySubagentTrailEvent([], event({
      type: 'subagent_message',
      message_id: 'm1',
      content: '最终正文',
      streamed: true,
      thinking: '思考全文',
      thinking_truncated: false,
    }))

    expect(trail).toEqual([
      { kind: 'message', id: 'm1', content: '最终正文', truncated: false, thinking: '思考全文', thinkingTruncated: false },
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
