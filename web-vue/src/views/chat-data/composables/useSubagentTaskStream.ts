/**
 * 后台子智能体详情页的实时过程流。
 *
 * 任务账本仍是最终真值；这里只订阅任务自己的 Redis SSE，实时归并正文增量、
 * 思考、工具生命周期、上下文用量与压缩事件。父 Run 是否已经结束不影响订阅。
 */
import { onBeforeUnmount, ref, watch } from 'vue'
import { getSubagentTaskTrail, streamSubagentTaskEvents } from '../api'
import type {
  ChatDataTokenUsage,
  SubagentCompaction,
  SubagentContextUsage,
  SubagentTrailItem,
  ToolLifecycleEventData,
} from '../types'
import { parseSseChunk } from '../utils/sse'
import { buildSubagentCompactionRecord, buildSubagentCompactionStartedTrail, buildSubagentCompactionTrailItem, clearSubagentCompactionPlaceholders, replaceSubagentCompactionPlaceholder } from '../utils/subagentCompaction'
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
  let snapshotLoaded = false
  // 检查点快照里的 message 是已完结的完整消息。事件流由后台泵异步写 Redis，
  // 可能落后于检查点：从快照游标续传时会重放同一条消息的 token 增量，若照常
  // 拼接就会把正文/思考重复追加一遍，且「更长者优先」的完整帧反而更短、纠正不了。
  // 这些 id 的增量一律丢弃，内容以快照为准。
  const closedMessageIds = new Set<string>()

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
    snapshotLoaded = false
    closedMessageIds.clear()
  }

  const applyEvent = (raw: Record<string, unknown>, eventName?: string) => {
    const type = eventName || String(raw.event_type || raw.type || '')
    if (!type || type === 'heartbeat') return
    // 检查点快照可能领先于事件流：从游标续传会重放已完结消息的增量，
    // 无条件拼接会把正文/思考重复追加一遍（完整帧按「更长者优先」纠正不了）。
    if (
      (type === 'subagent_message_delta' || type === 'subagent_thinking_delta')
      && closedMessageIds.has(String(raw.message_id || ''))
    ) return
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
      // 「压缩中」只是实时占位，不进 compactions（压缩次数只数完成的压缩）；
      // 完成帧到达时由 replaceSubagentCompactionPlaceholder 顶掉占位。
      if (event.phase === 'started') {
        const placeholder = buildSubagentCompactionStartedTrail(event)
        if (placeholder) trail.value = replaceSubagentCompactionPlaceholder(trail.value, placeholder)
        return
      }
      const record = buildSubagentCompactionRecord(event)
      if (!record) return
      if (compactions.value.some(item => item.id === record.id)) {
        // 重连回放的重复完成帧：不重复加卡，但仍清掉可能残留的「压缩中」占位。
        trail.value = clearSubagentCompactionPlaceholders(trail.value)
        return
      }
      compactions.value = [...compactions.value, record]
      trail.value = replaceSubagentCompactionPlaceholder(trail.value, buildSubagentCompactionTrailItem(record))
      return
    }
    trail.value = applySubagentTrailEvent(trail.value, event)
  }

  /**
   * 首连先取检查点快照：运行中任务不再从 0-0 重放整段 token 流。
   * 快照不可用（无检查点/无事件）时保持 0-0，沿用原来的全量重放兜底。
   */
  const hydrateSnapshot = async (task: string, conversation: string, runGeneration: number) => {
    snapshotLoaded = true
    try {
      const snapshot = await getSubagentTaskTrail(conversation, task)
      if (runGeneration !== generation) return
      if (!snapshot?.available || !snapshot.cursor) return
      if (Array.isArray(snapshot.trail) && snapshot.trail.length) {
        trail.value = snapshot.trail
        for (const item of snapshot.trail) {
          if (item.kind === 'message') closedMessageIds.add(item.id)
        }
      }
      if (Array.isArray(snapshot.compactions)) compactions.value = snapshot.compactions
      lastEventId = snapshot.cursor
    } catch {
      // 快照读取失败不阻断实时流，按原逻辑从 0-0 全量重放。
    }
  }

  const connect = async (runGeneration: number, task: string, conversation: string) => {
    while (runGeneration === generation && isActive()) {
      if (!snapshotLoaded && lastEventId === '0-0') {
        await hydrateSnapshot(task, conversation, runGeneration)
        if (runGeneration !== generation || !isActive()) return
      }
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
