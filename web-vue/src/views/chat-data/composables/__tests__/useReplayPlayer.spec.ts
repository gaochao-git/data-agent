import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { collectFullReplay, useReplayPlayer } from '../useReplayPlayer'
import type { Message } from '../../types'

const userMessage = (): Message => ({
  id: 'u1',
  role: 'user',
  content: '查一下最近的订单',
})

const assistantMessage = (): Message => ({
  id: 'a1',
  role: 'assistant',
  content: '订单查询完成，共 12 条。',
  isStreaming: false,
  thinkingSegments: [
    { id: 't1', content: '需要调用订单查询工具', toolCallCount: 1 },
  ],
  toolCalls: [
    {
      tool_call_id: 'tc1',
      tool_name: 'query_data',
      args: { sql: 'SELECT * FROM orders' },
      result: '{"rows": 12}',
      status: 'completed',
      started_at_ms: 1000,
      ended_at_ms: 2400,
    },
  ],
  segments: [
    { type: 'thinkingSegment', thinkingSegmentId: 't1' },
    { type: 'toolCall', toolCallId: 'tc1' },
    { type: 'text', content: '订单查询完成，共 12 条。' },
  ],
})

/** 反复推进假时钟直到播放结束（每个 timer 恰好消费一个动作） */
const runToFinish = (player: ReturnType<typeof useReplayPlayer>, maxTicks = 5000) => {
  for (let i = 0; i < maxTicks && player.status.value !== 'finished'; i += 1) {
    vi.advanceTimersByTime(2000)
  }
}

describe('useReplayPlayer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('播放完成后克隆消息与源终态一致', () => {
    const player = useReplayPlayer()
    const group = [userMessage(), assistantMessage()]
    player.start(group)
    expect(player.status.value).toBe('playing')
    expect(player.totalSteps.value).toBeGreaterThan(0)
    runToFinish(player)
    expect(player.status.value).toBe('finished')
    const final = player.messages.value[1]
    expect(final?.content).toBe(group[1]?.content)
    expect(final?.segments).toEqual(group[1]?.segments)
    expect(final?.thinkingSegments).toEqual(group[1]?.thinkingSegments)
    expect(final?.toolCalls?.[0]?.status).toBe('completed')
    expect(final?.toolCalls?.[0]?.result).toBe('{"rows": 12}')
    expect(final?.isStreaming).toBe(false)
  })

  it('播放过程模拟流式：工具先 running 无结果，后 completed 带结果', () => {
    const player = useReplayPlayer()
    player.start([userMessage(), assistantMessage()])
    let sawRunning = false
    let sawCompleted = false
    for (let i = 0; i < 5000 && player.status.value !== 'finished'; i += 1) {
      vi.advanceTimersByTime(50)
      const toolCall = player.messages.value[1]?.toolCalls?.[0]
      if (toolCall?.status === 'running' && toolCall.result === undefined) sawRunning = true
      if (toolCall?.status === 'completed') sawCompleted = true
    }
    expect(sawRunning).toBe(true)
    expect(sawCompleted).toBe(true)
  })

  it('正文分块打字：content 从空渐进到完整', () => {
    const longText = '订单查询完成。本月共产生 128 笔订单，总金额 45,200 元，环比增长 12%。'.repeat(3)
    const longMessage: Message = { ...assistantMessage(), content: longText, segments: [{ type: 'text', content: longText }] }
    const player = useReplayPlayer()
    player.start([userMessage(), longMessage])
    const contents: string[] = []
    for (let i = 0; i < 8000 && player.status.value !== 'finished'; i += 1) {
      vi.advanceTimersByTime(20)
      const content = player.messages.value[1]?.content || ''
      if (contents[contents.length - 1] !== content) contents.push(content)
    }
    expect(contents[0]).toBe('')
    expect(contents[contents.length - 1]).toBe(longText)
    expect(contents.length).toBeGreaterThan(2)
    // 渐进性：每个快照都是最终文本的前缀
    contents.forEach((snapshot) => {
      expect(longText.startsWith(snapshot)).toBe(true)
    })
  })

  it('skipToEnd 立即到达终态', () => {
    const player = useReplayPlayer()
    player.start([userMessage(), assistantMessage()])
    player.skipToEnd()
    expect(player.status.value).toBe('finished')
    const final = player.messages.value[1]
    expect(final?.segments).toEqual(assistantMessage().segments)
    expect(final?.toolCalls?.[0]?.result).toBe('{"rows": 12}')
    expect(final?.isStreaming).toBe(false)
  })

  it('暂停后不再推进，继续后播完', () => {
    const player = useReplayPlayer()
    player.start([userMessage(), assistantMessage()])
    vi.advanceTimersByTime(1000)
    player.pause()
    const frozen = player.stepIndex.value
    vi.advanceTimersByTime(5000)
    expect(player.stepIndex.value).toBe(frozen)
    player.resume()
    runToFinish(player)
    expect(player.status.value).toBe('finished')
  })

  it('close 清空状态', () => {
    const player = useReplayPlayer()
    player.start([userMessage(), assistantMessage()])
    player.close()
    expect(player.status.value).toBe('idle')
    expect(player.messages.value).toHaveLength(0)
    expect(player.totalSteps.value).toBe(0)
  })
})

describe('collectFullReplay', () => {
  it('返回从第一条用户提问到最后一条回复的整个会话（过滤非 user/assistant 角色）', () => {
    const group = [
      userMessage(),
      { ...assistantMessage(), id: 'a0' },
      { id: 'u2', role: 'user' as const, content: '第二条' },
      { id: 'ctx', role: 'context_compression' as const, content: '压缩记录' },
      { ...assistantMessage(), id: 'a2' },
    ]
    const result = collectFullReplay(group)
    // 压缩分隔卡保留（整卡出现）；tool 不参与（信息已并入助手 toolCalls）
    expect(result.map(item => item.id)).toEqual(['u1', 'a0', 'u2', 'ctx', 'a2'])
  })

  it('空列表返回空数组', () => {
    expect(collectFullReplay([])).toEqual([])
  })
})

describe('整个会话回放行为', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('开场不预揭示任何消息，用户提问随节奏逐条出现', () => {
    const player = useReplayPlayer()
    const group = [userMessage(), assistantMessage(), { id: 'u2', role: 'user' as const, content: '第二条' }, { ...assistantMessage(), id: 'a2' }]
    player.start(group)
    // 开场全部未揭示
    expect(player.revealed.value.every(item => item === false)).toBe(true)
    // 推进一小段时间后第一条提问已出现（首动作 delay=500ms@1x），后续提问尚未出现（尚未推进到该轮）
    vi.advanceTimersByTime(600)
    expect(player.revealed.value[0]).toBe(true)
    expect(player.revealed.value[2]).toBe(false)
    player.close()
  })

  it('压缩分隔卡整卡出现且终态与源一致', () => {
    const player = useReplayPlayer()
    const compression = { id: 'ctx', role: 'context_compression' as const, content: '压缩记录' } as Message
    player.start([userMessage(), compression, assistantMessage()])
    // 两个 reveal 动作各 500ms（1x）：用户提问 + 压缩卡
    vi.advanceTimersByTime(1100)
    const revealed = player.messages.value.filter((_, index) => player.revealed.value[index])
    const ctxCard = revealed.find(item => item.id === 'ctx')
    expect(ctxCard?.role).toBe('context_compression')
    expect(ctxCard?.content).toBe('压缩记录')
    player.close()
  })

  it('播放完成后消息对象与源终态完全一致（保证渲染与一次性历史渲染相同）', () => {
    const player = useReplayPlayer()
    const group = [userMessage(), assistantMessage()]
    player.start(group)
    runToFinish(player)
    expect(player.status.value).toBe('finished')
    const final = player.messages.value[1]
    expect(final).toEqual(group[1])
    // 深相等覆盖 segments/thinkingSegments/toolCalls/dashboardSpec/error 等全部字段
    expect(JSON.stringify(final)).toBe(JSON.stringify(group[1]))
  })
})
