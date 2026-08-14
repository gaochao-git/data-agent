import type { Message } from '../types'

export type InboxQueueType = 'next-step' | 'next-turn'
export type InboxMessageStatus = 'pending' | 'claimed' | 'consumed' | 'cancelled'

export interface InboxQueueItem {
  clientId: string
  messageId?: string
  content: string
  queueType: InboxQueueType
  status: 'submitting' | InboxMessageStatus
  timestamp: Date
}

export interface InboxServerMessage {
  message_id: string
  content: string
  queue_type: InboxQueueType
  status: InboxMessageStatus
  create_time: string
}

export const admitInboxMessages = (
  queue: InboxQueueItem[],
  messages: Message[],
  messageIds: string[],
): { queue: InboxQueueItem[]; messages: Message[] } => {
  const admittedIds = new Set(messageIds)
  const admitted = queue.filter(item => item.messageId && admittedIds.has(item.messageId))
  if (!admitted.length) return { queue, messages }

  const existingIds = new Set(messages.map(message => message.id))
  const admittedMessages = admitted
    .filter(item => item.messageId && !existingIds.has(item.messageId))
    .map(item => ({
      id: item.messageId!,
      role: 'user' as const,
      content: item.content,
      timestamp: item.timestamp,
    }))
  return {
    queue: queue.filter(item => !item.messageId || !admittedIds.has(item.messageId)),
    messages: [...messages, ...admittedMessages],
  }
}

export const reconcileInboxSnapshot = (
  localItems: InboxQueueItem[],
  serverItems: InboxServerMessage[],
  persistedMessageIds: Set<string>,
): { queue: InboxQueueItem[]; admittedMessageIds: string[] } => {
  const serverById = new Map(serverItems.map(item => [item.message_id, item]))
  const queue: InboxQueueItem[] = []
  const admittedMessageIds: string[] = []

  for (const item of localItems) {
    if (!item.messageId) {
      queue.push(item)
      continue
    }
    if (persistedMessageIds.has(item.messageId)) continue
    const serverItem = serverById.get(item.messageId)
    if (!serverItem) {
      queue.push(item)
      continue
    }
    serverById.delete(item.messageId)
    if (serverItem.status === 'consumed') {
      admittedMessageIds.push(item.messageId)
      queue.push({
        ...item,
        content: serverItem.content,
        queueType: serverItem.queue_type,
        status: serverItem.status,
      })
      continue
    }
    if (serverItem.status === 'cancelled') continue
    queue.push({
      ...item,
      content: serverItem.content,
      queueType: serverItem.queue_type,
      status: serverItem.status,
    })
  }

  for (const serverItem of serverById.values()) {
    if (persistedMessageIds.has(serverItem.message_id) || serverItem.status === 'cancelled') continue
    const item: InboxQueueItem = {
      clientId: `server-${serverItem.message_id}`,
      messageId: serverItem.message_id,
      content: serverItem.content,
      queueType: serverItem.queue_type,
      status: serverItem.status,
      timestamp: new Date(serverItem.create_time),
    }
    queue.push(item)
    if (serverItem.status === 'consumed') admittedMessageIds.push(serverItem.message_id)
  }

  return { queue, admittedMessageIds }
}
