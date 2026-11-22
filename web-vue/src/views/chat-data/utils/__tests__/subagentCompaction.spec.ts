import { describe, expect, it } from 'vitest'
import type { SubagentTrailItem, ToolCallInfo } from '../../types'
import {
  applySubagentCompactionStartedToTool,
  applySubagentCompactionToTool,
  buildSubagentCompactionStartedTrail,
  clearSubagentCompactionPlaceholders,
  replaceSubagentCompactionPlaceholder,
} from '../subagentCompaction'

const tool = (trail: SubagentTrailItem[] = []): ToolCallInfo => ({
  tool_call_id: 'call_1',
  tool_name: 'subagent',
  args: {},
  status: 'running',
  subagentTrail: trail,
})

const placeholder: SubagentTrailItem = {
  kind: 'compaction',
  id: 'started-1',
  compressing: true,
  timestamp: '2026-11-15 20:30:00',
}

const completed: SubagentTrailItem = {
  kind: 'compaction',
  id: 'done-1',
  estimated_tokens_before: 900,
  estimated_tokens_after: 300,
  estimated_cleared_tokens: 600,
  compressed_messages: 4,
}

describe('buildSubagentCompactionStartedTrail', () => {
  it('构造只属于实时流的压缩中占位', () => {
    expect(buildSubagentCompactionStartedTrail({ id: ' started-1 ', timestamp: '2026-11-15 20:30:00' }))
      .toEqual(placeholder)
  })

  it('缺 id 时丢弃', () => {
    expect(buildSubagentCompactionStartedTrail({ timestamp: '2026-11-15 20:30:00' })).toBeNull()
    expect(buildSubagentCompactionStartedTrail({ id: '   ' })).toBeNull()
  })
})

describe('replaceSubagentCompactionPlaceholder', () => {
  it('只移除压缩中占位，保留消息/工具与已完成的压缩分隔卡', () => {
    const trail: SubagentTrailItem[] = [
      { kind: 'message', id: 'm1', content: '先查数据' },
      placeholder,
      completed,
      { kind: 'tool', id: 't1', tool_name: 'sql_query', status: 'completed' },
    ]
    expect(replaceSubagentCompactionPlaceholder(trail, { ...placeholder, id: 'started-2' })).toEqual([
      { kind: 'message', id: 'm1', content: '先查数据' },
      completed,
      { kind: 'tool', id: 't1', tool_name: 'sql_query', status: 'completed' },
      { ...placeholder, id: 'started-2' },
    ])
    // 不修改入参数组
    expect(trail).toHaveLength(4)
  })
})

describe('clearSubagentCompactionPlaceholders', () => {
  it('任务收口时清掉残留占位，保留正文与已完成的分隔卡', () => {
    const trail: SubagentTrailItem[] = [
      { kind: 'message', id: 'm1', content: '分析中' },
      completed,
      placeholder,
    ]
    expect(clearSubagentCompactionPlaceholders(trail)).toEqual([
      { kind: 'message', id: 'm1', content: '分析中' },
      completed,
    ])
    expect(trail).toHaveLength(3)
  })
})

describe('压缩事件写入 subagent 调用', () => {
  it('压缩中：替换旧占位，且不写 subagentCompactions', () => {
    const current = tool([{ kind: 'message', id: 'm1', content: '分析中' }, placeholder])
    const next = applySubagentCompactionStartedToTool(current, { id: 'started-2' })
    expect(next?.subagentTrail).toEqual([
      { kind: 'message', id: 'm1', content: '分析中' },
      { kind: 'compaction', id: 'started-2', compressing: true, timestamp: undefined },
    ])
    expect(next?.subagentCompactions).toBeUndefined()
  })

  it('压缩中：缺 id 不改状态', () => {
    expect(applySubagentCompactionStartedToTool(tool(), {})).toBeNull()
  })

  it('压缩完成：顶掉占位并按 id 去重计数', () => {
    const record = {
      id: 'done-1',
      estimated_tokens_before: 900,
      estimated_tokens_after: 300,
      estimated_cleared_tokens: 600,
      compressed_messages: 4,
    }
    const next = applySubagentCompactionToTool(
      tool([{ kind: 'message', id: 'm1', content: '分析中' }, placeholder]),
      record,
    )
    expect(next?.subagentTrail).toEqual([
      { kind: 'message', id: 'm1', content: '分析中' },
      completed,
    ])
    expect(next?.subagentCompactions).toEqual([record])
    expect(applySubagentCompactionToTool(next!, record)).toBeNull()
  })

  it('重复完成帧（断线重连回放）：不重复加卡，但清掉残留的压缩中占位', () => {
    const record = { id: 'done-1' }
    const current: ToolCallInfo = {
      ...tool([{ kind: 'message', id: 'm1', content: '分析中' }, placeholder]),
      subagentCompactions: [record],
    }
    const next = applySubagentCompactionToTool(current, record)
    expect(next?.subagentCompactions).toEqual([record])
    expect(next?.subagentTrail).toEqual([{ kind: 'message', id: 'm1', content: '分析中' }])
    // 无占位且 id 已存在时保持原样，避免无谓写回
    expect(applySubagentCompactionToTool(next!, record)).toBeNull()
  })
})
