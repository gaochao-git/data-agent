/**
 * 后台子智能体详情页的实时过程流。
 *
 * 任务账本仍是最终真值；这里只订阅任务自己的 Redis SSE，实时归并正文增量、
 * 思考、工具生命周期、上下文用量与压缩事件。父 Run 是否已经结束不影响订阅。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { streamSubagentTaskEvents } from '../api'
import type {
  ChatDataTokenUsage,
  SubagentCompaction,
  SubagentContextUsage,
  SubagentTaskRecord,
  SubagentTrailItem,
  ToolLifecycleEventData,
} from '../types'
import { parseSseChunk } from '../utils/sse'
import { buildSubagentCompactionRecord } from '../utils/subagentCompaction'
import { applySubagentTrailEvent } from '../utils/subagentTrail'

const RECONNECT_DELAY_MS = 800

export const useSubagentTaskStream = (
  taskId: () => string | null,
  conversationId: () => string | null,
  isActive: () => boolean,
) => {
  const trail = ref<SubagentTrailItem[]>([])
  const contextUsage = ref<SubagentContextUsage | null>(null)
  const compactions = ref<SubagentCompaction[]>([])
  const tokenUsageDetail = ref<ChatDataTokenUsage | undefined>()
  const connected = ref(false)

  let currentTaskId: string | null = null
  let controller: AbortController | null = null
  let generation = 0
  let lastEventId = '0-0'

  const stop = () => {
    generation += 1
    controller?.abort()
    controller = null
    connected.value = false
  }

  const reset = () => {
    trail.value = []
    contextUsage.value = null
    compactions.value = []
    tokenUsageDetail.value = undefined
    lastEventId = '0-0'
  }

  const applyEvent = (raw: Record<string, unknown>, eventName?: string) => {
    const type = eventName || String(raw.event_type || raw.type || '')
    if (!type || type === 'heartbeat') return
    const event = { ...raw, type } as ToolLifecycleEventData
    if (type === 'subagent_context_usage') {
      if (typeof event.used_tokens !== 'number') return
      contextUsage.value = {
        used_tokens: event.used_tokens,
        threshold_tokens: event.threshold_tokens ?? 0,
        context_window: event.context_window,
        percent: event.percent,
        budget_percent: event.budget_percent,
      }
      if (event.token_usage_detail) tokenUsageDetail.value = event.token_usage_detail
      return
    }
    if (type === 'subagent_context_compression') {
      const record = buildSubagentCompactionRecord(event)
      if (!record || compactions.value.some(item => item.id === record.id)) return
      compactions.value = [...compactions.value, record]
      trail.value = [...trail.value, {
        kind: 'compaction',
        id: record.id,
        timestamp: record.timestamp,
        estimated_tokens_before: record.estimated_tokens_before,
        estimated_tokens_after: record.estimated_tokens_after,
        estimated_cleared_tokens: record.estimated_cleared_tokens,
        compressed_messages: record.compressed_messages,
      }]
      return
    }
    trail.value = applySubagentTrailEvent(trail.value, event)
  }

  const connect = async (runGeneration: number, task: string, conversation: string) => {
    while (runGeneration === generation && isActive()) {
      controller = new AbortController()
      try {
        const response = await streamSubagentTaskEvents(conversation, task, lastEventId, controller.signal)
        if (!response.ok) throw new Error(`订阅子智能体事件失败: ${response.status}`)
        const reader = response.body?.getReader()
        if (!reader) throw new Error('子智能体事件流不可读')
        connected.value = true
        const decoder = new TextDecoder()
        let buffer = ''
        while (runGeneration === generation) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const parsed = parseSseChunk(buffer)
          buffer = parsed.rest
          for (const block of parsed.events) {
            if (block.id) lastEventId = block.id
            if (!block.data || block.event === 'heartbeat') continue
            try {
              applyEvent(JSON.parse(block.data) as Record<string, unknown>, block.event)
            } catch {
              // 单帧坏数据不打断整条过程流。
            }
          }
        }
      } catch {
        if (controller?.signal.aborted || runGeneration !== generation) return
      } finally {
        connected.value = false
        controller = null
      }
      if (runGeneration !== generation || !isActive()) return
      // 网络中断或服务端短连接都由原游标续传；终态最多等下一次任务轮询收口。
      await new Promise<void>(resolve => setTimeout(resolve, RECONNECT_DELAY_MS))
    }
  }

  const start = () => {
    const nextTaskId = taskId()
    if (nextTaskId !== currentTaskId) {
      currentTaskId = nextTaskId
      reset()
    }
    stop()
    const conversation = conversationId()
    if (!nextTaskId || !conversation || !isActive()) return
    const runGeneration = generation
    void connect(runGeneration, nextTaskId, conversation)
  }

  watch([taskId, conversationId, isActive], start, { immediate: true })
  onBeforeUnmount(stop)

  return {
    trail,
    contextUsage,
    compactions,
    tokenUsageDetail,
    connected,
  }
}
