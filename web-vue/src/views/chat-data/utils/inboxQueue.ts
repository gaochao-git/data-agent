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
  /** 入队瞬间捕获的 @ 引用快照（相对路径列表）。undefined = 恢复自服务端且服务端无快照，执行时显式不带引用。 */
  fileSelections?: string[]
  /** 入队瞬间捕获的上传附件快照（file_id 列表，附件/截图共用）。随消息走，绝不兜底取当前输入框状态。 */
  fileIds?: string[]
}

export interface InboxServerMessage {
  message_id: string
  content: string
  queue_type: InboxQueueType
  status: InboxMessageStatus
  /** 服务端随行元数据（file_selections/file_ids）；旧数据或纯文本消息为 null。 */
  meta?: { file_selections?: string[]; file_ids?: string[] } | null
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
      // 随行快照一并落气泡：否则实时会话里被接纳的排队消息丢 @ 引用 chip，
      // 只有刷新后的历史回放（从事件 payload 重建）才显示
      ...(item.fileSelections?.length ? { fileSelections: item.fileSelections } : {}),
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
  retainMissingItems = true,
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
      if (retainMissingItems) queue.push(item)
      continue
    }
    serverById.delete(item.messageId)
    // 快照归属：本地项自带快照优先，缺失时回填服务端 meta（跨端/旧数据兜底）
    const mergedMeta = {
      fileSelections: item.fileSelections ?? serverItem.meta?.file_selections ?? undefined,
      fileIds: item.fileIds ?? serverItem.meta?.file_ids ?? undefined,
    }
    if (serverItem.status === 'consumed') {
      admittedMessageIds.push(item.messageId)
      queue.push({
        ...item,
        content: serverItem.content,
        queueType: serverItem.queue_type,
        status: serverItem.status,
        ...mergedMeta,
      })
      continue
    }
    if (serverItem.status === 'cancelled') continue
    queue.push({
      ...item,
      content: serverItem.content,
      queueType: serverItem.queue_type,
      status: serverItem.status,
      ...mergedMeta,
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
      // 服务端随行快照：刷新恢复后执行时仍能带上当时的引用/附件
      fileSelections: serverItem.meta?.file_selections ?? undefined,
      fileIds: serverItem.meta?.file_ids ?? undefined,
      timestamp: new Date(serverItem.create_time),
    }
    queue.push(item)
    if (serverItem.status === 'consumed') admittedMessageIds.push(serverItem.message_id)
  }

  return { queue, admittedMessageIds }
}
