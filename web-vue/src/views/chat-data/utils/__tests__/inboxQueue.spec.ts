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

  it('本地项保留引用快照，服务端重建项不带快照（fileSelections）', () => {
    const withSnapshot: InboxQueueItem = { ...queued('m1'), fileSelections: ['uploads/a.pdf'] }
    const result = reconcileInboxSnapshot([withSnapshot], [
      { message_id: 'm1', content: '内容-m1', queue_type: 'next-turn', status: 'claimed', create_time: '2026-08-14T10:00:00' },
      { message_id: 'm2', content: '服务端消息', queue_type: 'next-turn', status: 'claimed', create_time: '2026-08-14T10:02:00' },
    ], new Set())
    // 本地项 spread 保留，快照原样透传
    const local = result.queue.find(item => item.messageId === 'm1')
    expect(local?.fileSelections).toEqual(['uploads/a.pdf'])
    // 服务端重建项无快照：执行时显式不带引用，绝不兜底取当前 chip
    const fromServer = result.queue.find(item => item.messageId === 'm2')
    expect(fromServer?.fileSelections).toBeUndefined()
  })

  it('本地项缺快照时回填服务端 meta，服务端重建项携带 meta 快照', () => {
    // 本地项只有 fileSelections、缺 fileIds：合并时从服务端 meta 补齐
    const localPartial: InboxQueueItem = { ...queued('m1'), fileSelections: ['uploads/a.pdf'] }
    const result = reconcileInboxSnapshot([localPartial], [
      {
        message_id: 'm1',
        content: '内容-m1',
        queue_type: 'next-turn',
        status: 'claimed',
        meta: { file_selections: ['workspace/semantic/a__1.yaml'], file_ids: ['file-9'] },
        create_time: '2026-08-14T10:00:00',
      },
    ], new Set())
    const merged = result.queue.find(item => item.messageId === 'm1')
    expect(merged?.fileSelections).toEqual(['uploads/a.pdf']) // 本地快照优先
    expect(merged?.fileIds).toEqual(['file-9']) // 缺失的从服务端回填

    // 服务端重建项：meta 快照完整还原（刷新恢复后执行仍带引用/附件）
    const rebuilt = reconcileInboxSnapshot([], [
      {
        message_id: 'm2',
        content: '服务端消息',
        queue_type: 'next-turn',
        status: 'pending',
        meta: { file_selections: ['uploads/b.md'], file_ids: ['file-8'] },
        create_time: '2026-08-14T10:02:00',
      },
    ], new Set())
    const item = rebuilt.queue.find(entry => entry.messageId === 'm2')
    expect(item?.fileSelections).toEqual(['uploads/b.md'])
    expect(item?.fileIds).toEqual(['file-8'])
  })

  it('does not duplicate an admitted user message after reconnect', () => {
    const existing = [{ id: 'm1', role: 'user' as const, content: '已存在' }]
    const result = admitInboxMessages([queued('m1')], existing, ['m1'])
    expect(result.queue).toEqual([])
    expect(result.messages).toEqual(existing)
  })

  it('admitted user message carries the @ mention snapshot (live chip parity with history)', () => {
    // 生成中入队的消息被服务端消费后，气泡必须带上入队时的引用快照；
    // 否则实时会话无 chip、刷新后历史回放才有（file_selections 来自事件 payload）
    const withMention: InboxQueueItem = {
      ...queued('m1', 'consumed'),
      fileSelections: ['workspace/semantic/订单模型__444139.yaml'],
    }
    const result = admitInboxMessages([withMention], [], ['m1'])
    expect(result.messages).toHaveLength(1)
    expect(result.messages[0]!.fileSelections).toEqual(['workspace/semantic/订单模型__444139.yaml'])

    // 无快照的消息不挂空数组字段（保持与纯文本消息同形）
    const plain = admitInboxMessages([queued('m2', 'consumed')], [], ['m2'])
    expect(plain.messages[0]!.fileSelections).toBeUndefined()
  })
})
