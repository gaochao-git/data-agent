import { describe, expect, it } from 'vitest'
import type { SubagentTrailItem, ToolLifecycleEventData } from '../../types'
import { applySubagentTrailEvent, finalizeSubagentTrailOnStop } from '../subagentTrail'

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
      args: { file_path: '/a.md' },
      result: 'ok',
      started_at_ms: 100,
      ended_at_ms: 180,
    })
  })

  it('重连重放 tool_started 不会把已完成工具打回运行中', () => {
    let trail: SubagentTrailItem[] = []
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_started',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      args: { file_path: '/a.md' },
      replace_args: true,
      started_at_ms: 100,
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_completed',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      content: 'ok',
      ended_at_ms: 180,
    }))

    const replayed = applySubagentTrailEvent(trail, event({
      type: 'tool_started',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      args: { file_path: '/a.md' },
      replace_args: true,
      started_at_ms: 100,
    }))

    expect(replayed).toBe(trail)
    expect(replayed[0]).toMatchObject({
      status: 'completed',
      result: 'ok',
      args: { file_path: '/a.md' },
      ended_at_ms: 180,
    })
  })

  it('tool_started 声明 replace_args 时按权威快照整份替换参数', () => {
    let trail = applySubagentTrailEvent([], event({
      type: 'tool_started',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      args: { file_path: '/a.md', stale: true },
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_started',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      args: { file_path: '/b.md' },
      replace_args: true,
    }))

    expect(trail[0]?.args).toEqual({ file_path: '/b.md' })
  })

  it('参数生成增量先建 trail，tool_started 收口后停止预览', () => {
    let trail: SubagentTrailItem[] = []
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_args_delta',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      status: 'generating',
      delta: '{"file_path": "/a.md"',
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_args_delta',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      status: 'generating',
      delta: '}',
    }))

    expect(trail[0]).toMatchObject({
      kind: 'tool',
      id: 'tool1',
      status: 'generating',
      argsStreaming: true,
      argsText: '{"file_path": "/a.md"}',
    })

    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_started',
      tool_call_id: 'tool1',
      tool_name: 'file_read',
      args: { file_path: '/a.md' },
    }))

    expect(trail[0]).toMatchObject({
      status: 'running',
      args: { file_path: '/a.md' },
      argsStreaming: false,
      argsText: '{"file_path": "/a.md"}',
    })
  })

  it('文件工具的 +N / -M 与 intent 从参数增量保留到执行完成', () => {
    let trail: SubagentTrailItem[] = []
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_args_delta',
      tool_call_id: 'edit-1',
      tool_name: 'file_edit',
      status: 'generating',
      delta: '{"file_path":"/a.html","new_content":"a\\nb\\nc"',
      content_lines: 3,
      removed_lines: 1,
      intent: '补全页面结构',
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_started',
      tool_call_id: 'edit-1',
      tool_name: 'file_edit',
      args: { file_path: '/a.html' },
    }))
    trail = applySubagentTrailEvent(trail, event({
      type: 'tool_completed',
      tool_call_id: 'edit-1',
      tool_name: 'file_edit',
      content: '已修改',
    }))

    expect(trail[0]).toMatchObject({
      kind: 'tool',
      id: 'edit-1',
      status: 'completed',
      contentLines: 3,
      removedLines: 1,
      streamingIntent: '补全页面结构',
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

describe('finalizeSubagentTrailOnStop', () => {
  it('把仍在运行/生成参数的内层工具收口为已停止，已完成条目保持原样', () => {
    const completed: SubagentTrailItem = {
      kind: 'tool',
      id: 'tool-done',
      tool_name: 'file_read',
      status: 'completed',
      result: 'ok',
      ended_at_ms: 180,
    }
    const running: SubagentTrailItem = {
      kind: 'tool',
      id: 'tool-running',
      tool_name: 'sql_query',
      status: 'running',
      args: { sql: 'select 1' },
      started_at_ms: 200,
    }
    const generating: SubagentTrailItem = {
      kind: 'tool',
      id: 'tool-generating',
      tool_name: 'file_write',
      status: 'generating',
      argsText: '{"path":',
      argsStreaming: true,
    }

    const result = finalizeSubagentTrailOnStop([completed, running, generating])

    expect(result[0]).toBe(completed)
    expect(result[1]).toMatchObject({
      id: 'tool-running',
      status: 'cancelled',
      result: '任务已停止，工具未执行完成。',
      started_at_ms: 200,
      ended_at_ms: expect.any(Number),
    })
    expect(result[2]).toMatchObject({
      id: 'tool-generating',
      status: 'cancelled',
      argsStreaming: false,
      argsText: '{"path":',
    })
  })

  it('没有未完成工具时返回原数组，避免无意义的重渲染', () => {
    const trail: SubagentTrailItem[] = [
      { kind: 'message', id: 'm1', content: '正文' },
      { kind: 'tool', id: 'tool1', tool_name: 'file_read', status: 'completed', result: 'ok' },
    ]
    expect(finalizeSubagentTrailOnStop(trail)).toBe(trail)
  })
})
