import { describe, expect, it } from 'vitest'

import { admitInboxMessages, reconcileInboxSnapshot, type InboxQueueItem } from '../inboxQueue'

const queued = (messageId: string, status: InboxQueueItem['status'] = 'pending'): InboxQueueItem => ({
  clientId: `client-${messageId}`,
  messageId,
  content: `内容-${messageId}`,
  queueType: 'next-turn',
  status,
  timestamp: new Date('2026-08-14T10:00:00'),
})

describe('inboxQueue', () => {
  it('does not remove a local item merely because a snapshot temporarily omits it', () => {
    const result = reconcileInboxSnapshot([queued('m1')], [], new Set())
    expect(result.queue.map(item => item.messageId)).toEqual(['m1'])
    expect(result.admittedMessageIds).toEqual([])
  })

  it('removes a missing local item after the run is terminal', () => {
    const result = reconcileInboxSnapshot([queued('m1')], [], new Set(), false)
    expect(result.queue).toEqual([])
  })

  it('keeps claimed items visible and admits consumed items', () => {
    const result = reconcileInboxSnapshot([queued('m1'), queued('m2')], [
      { message_id: 'm1', content: '处理中', queue_type: 'next-step', status: 'claimed', create_time: '2026-08-14T10:00:00' },
      { message_id: 'm2', content: '已接纳', queue_type: 'next-turn', status: 'consumed', create_time: '2026-08-14T10:01:00' },
    ], new Set())
    expect(result.queue.map(item => [item.messageId, item.status])).toEqual([
      ['m1', 'claimed'],
      ['m2', 'consumed'],
    ])
    expect(result.admittedMessageIds).toEqual(['m2'])
  })

  it('recovers from a lost SSE admission event using the next server snapshot', () => {
    const claimed = reconcileInboxSnapshot([queued('m1')], [
      { message_id: 'm1', content: '处理中', queue_type: 'next-step', status: 'claimed', create_time: '2026-08-14T10:00:00' },
    ], new Set())
    expect(claimed.queue.map(item => item.status)).toEqual(['claimed'])

    const consumed = reconcileInboxSnapshot(claimed.queue, [
      { message_id: 'm1', content: '处理中', queue_type: 'next-step', status: 'consumed', create_time: '2026-08-14T10:00:00' },
    ], new Set())
    expect(consumed.admittedMessageIds).toEqual(['m1'])
    const admitted = admitInboxMessages(consumed.queue, [], consumed.admittedMessageIds)
    expect(admitted.queue).toEqual([])
    expect(admitted.messages.map(message => message.id)).toEqual(['m1'])
  })

  it('retires queue items only after durable history or an admitted event', () => {
    const historyResult = reconcileInboxSnapshot([queued('m1')], [], new Set(['m1']))
    expect(historyResult.queue).toEqual([])

    const admitted = admitInboxMessages([queued('m2')], [], ['m2'])
    expect(admitted.queue).toEqual([])
    expect(admitted.messages.map(message => [message.id, message.role])).toEqual([['m2', 'user']])
  })

  it('does not duplicate an admitted user message after reconnect', () => {
    const existing = [{ id: 'm1', role: 'user' as const, content: '已存在' }]
    const result = admitInboxMessages([queued('m1')], existing, ['m1'])
    expect(result.queue).toEqual([])
    expect(result.messages).toEqual(existing)
  })
})
