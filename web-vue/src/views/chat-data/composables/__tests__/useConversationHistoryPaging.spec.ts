import { ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  getConversationHistory: vi.fn(),
}))

vi.mock('../../api', () => apiMocks)

import { HISTORY_TURN_PAGE_SIZE, useConversationHistory } from '../useConversationHistory'

const event = (sequenceNo: number, content: string) => ({
  sequence_no: sequenceNo,
  turn_id: `turn-${sequenceNo}`,
  event_type: 'user_message',
  payload: {
    id: `user-${sequenceNo}`,
    role: 'user',
    content,
    timestamp: '2026-09-11 10:00:00',
  },
})

const assistantEvent = (sequenceNo: number, turnId: string, id: string, content: string) => ({
  sequence_no: sequenceNo,
  turn_id: turnId,
  event_type: 'assistant_message',
  payload: {
    id,
    role: 'assistant',
    content,
    timestamp: '2026-09-11 10:00:00',
  },
})

const historyResponse = (events: unknown[], hasMore: boolean) => ({
  ok: true,
  json: async () => ({
    status: 'success',
    data: {
      history_events: events,
      has_more: hasMore,
      has_more_turns: hasMore,
      earliest_sequence_no: events.length
        ? (events[0] as { sequence_no: number }).sequence_no
        : null,
      earliest_turn_sequence: events.length
        ? (events[0] as { sequence_no: number }).sequence_no
        : null,
    },
  }),
})

const setup = () => {
  const messages = ref<Array<{ id: string; role: string; content: string }>>([])
  const history = useConversationHistory({
    conversationId: ref('conv-1'),
    messages: messages as never,
  })
  return { messages, history }
}

describe('useConversationHistory 分页', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('首屏只拉一页并记录 has_more', async () => {
    apiMocks.getConversationHistory.mockResolvedValueOnce(
      historyResponse([event(5, '问题3'), event(6, '回答3')], true),
    )

    const { messages, history } = setup()
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))

    expect(apiMocks.getConversationHistory).toHaveBeenCalledWith('conv-1', {
      limitTurns: HISTORY_TURN_PAGE_SIZE,
    })
    expect(messages.value.map(item => item.content)).toEqual(['问题3', '回答3'])
    expect(history.hasMoreHistory.value).toBe(true)
  })

  it('并发翻页合流：在途请求未完成时重复触发复用同一请求，不重复打接口', async () => {
    apiMocks.getConversationHistory
      .mockResolvedValueOnce(historyResponse([event(5, '问题3'), event(6, '回答3')], true))
    const { history } = setup()
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))
    expect(history.hasMoreHistory.value).toBe(true)

    // 模拟滚顶自动加载在途（请求未返回），回放预拉此时触发：必须合流等待而非误报失败
    let release!: () => void
    const gate = new Promise<void>(resolve => {
      release = resolve
    })
    apiMocks.getConversationHistory.mockImplementationOnce(() =>
      gate.then(() => historyResponse([event(3, '问题2'), event(4, '回答2')], false)),
    )
    const first = history.loadEarlierHistory()
    const second = history.loadEarlierHistory()
    release()
    const [r1, r2] = await Promise.all([first, second])
    expect(r1).toBe('loaded')
    expect(r2).toBe('loaded')
    // 1 次首屏 + 1 次翻页：并发调用合流后第二次没有额外请求
    expect(apiMocks.getConversationHistory).toHaveBeenCalledTimes(2)
  })

  it('向上翻页用最早序号作游标，并把更早事件拼到序列前面', async () => {
    apiMocks.getConversationHistory
      .mockResolvedValueOnce(historyResponse([event(5, '问题3'), event(6, '回答3')], true))
      .mockResolvedValueOnce(historyResponse([event(3, '问题2'), event(4, '回答2')], true))

    const { messages, history } = setup()
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))

    await history.loadEarlierHistory()

    expect(apiMocks.getConversationHistory).toHaveBeenNthCalledWith(2, 'conv-1', {
      limitTurns: HISTORY_TURN_PAGE_SIZE,
      beforeTurnSequence: 5,
    })
    expect(messages.value.map(item => item.content)).toEqual(['问题2', '回答2', '问题3', '回答3'])
    expect(history.hasMoreHistory.value).toBe(true)
    expect(history.isLoadingEarlierHistory.value).toBe(false)
  })

  it('长轮次跨页加载后重建为同一个回答块', async () => {
    apiMocks.getConversationHistory
      .mockResolvedValueOnce(historyResponse([assistantEvent(6, 'turn-1', 'a-late', '后半段')], true))
      .mockResolvedValueOnce(historyResponse([
        event(3, '前半段问题'),
        assistantEvent(4, 'turn-1', 'a-early', '前半段'),
      ], false))

    const { messages, history } = setup()
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))
    await history.loadEarlierHistory()

    expect(messages.value.map(item => item.content)).toEqual(['前半段问题', '前半段\n后半段'])
  })

  it('没有更早历史时不再发请求', async () => {
    apiMocks.getConversationHistory.mockResolvedValueOnce(historyResponse([event(1, '唯一问题')], false))

    const { history } = setup()
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))

    await history.loadEarlierHistory()

    expect(apiMocks.getConversationHistory).toHaveBeenCalledTimes(1)
  })

  it('事件缺少可用序号时停止翻页，避免反复请求同一游标', async () => {
    apiMocks.getConversationHistory.mockResolvedValueOnce(historyResponse([{ event_type: 'user_message' }], true))

    const { history } = setup()
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))

    await history.loadEarlierHistory()

    expect(apiMocks.getConversationHistory).toHaveBeenCalledTimes(1)
    expect(history.hasMoreHistory.value).toBe(false)
  })

  it('向上翻页保留历史窗口之外的本会话实时消息', async () => {
    apiMocks.getConversationHistory
      .mockResolvedValueOnce(historyResponse([event(5, '问题3'), event(6, '回答3')], true))
      .mockResolvedValueOnce(historyResponse([event(3, '问题2'), event(4, '回答2')], false))

    const { messages, history } = setup()
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))

    // 模拟本会话新发消息/流式回复：这些消息不在 history_events 里
    messages.value.push({ id: 'live-1', role: 'user', content: '刚发的问题' })
    messages.value.push({ id: 'live-2', role: 'assistant', content: '正在生成的回答' })

    await history.loadEarlierHistory()

    expect(messages.value.map(item => item.content)).toEqual([
      '问题2', '回答2', '问题3', '回答3', '刚发的问题', '正在生成的回答',
    ])
    expect(history.hasMoreHistory.value).toBe(false)
  })

  it('请求期间会话已切换时丢弃旧会话响应', async () => {
    apiMocks.getConversationHistory.mockResolvedValueOnce(historyResponse([event(1, '旧会话事件')], true))

    const conversationId = ref('conv-1')
    const messages = ref<Array<{ id: string; role: string; content: string }>>([])
    const history = useConversationHistory({
      conversationId,
      messages: messages as never,
    })
    await vi.waitFor(() => expect(history.isLoadingHistory.value).toBe(false))

    let resolveLater: (value: ReturnType<typeof historyResponse>) => void
    apiMocks.getConversationHistory.mockReturnValueOnce(
      new Promise(resolve => { resolveLater = resolve }),
    )
    const pending = history.loadEarlierHistory()
    conversationId.value = 'conv-2'
    resolveLater!(historyResponse([event(1, '迟到的事件')], false))
    await pending
    await vi.waitFor(() => expect(history.isLoadingEarlierHistory.value).toBe(false))

    expect(messages.value.map(item => item.content)).not.toContain('迟到的事件')
  })
})
