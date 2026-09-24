/**
 * 会话历史加载 composable
 * 用于加载和恢复会话历史消息（参考 React 版本）
 */
import { ref, watch, type Ref } from 'vue'
import { getConversationHistory } from '../api'
import type {
  ChatDataContextCompressionEvent,
  ChatDataTokenUsage,
  SubagentContextUsage,
  SubagentTrailItem,
  ToolCallInfo,
  Message,
  SemanticParams,
  ThinkingSegment,
} from '../types'
import { extractSemanticParamsFromArgs, getQueryResultError, getRenderableQueryResultData } from '../utils/semanticTool'

const parseHistoryTimestamp = (value: unknown): Date | null => {
  if (typeof value !== 'string' || !value.trim()) return null
  const parsed = new Date(value.trim().replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const normalizeReasoningContent = (value: unknown): string => {
  return typeof value === 'string' ? value.trim() : ''
}

// 用户消息 content 兼容两种形态：纯字符串（历史）与 content blocks 数组（多模态）。
// blocks 时只抽取 text 拼为正文；image 块不内联渲染，图片统一走附件卡片/工作区面板查看。
const normalizeUserMessageContent = (value: unknown): string => {
  if (typeof value === 'string') return value.trim()
  if (!Array.isArray(value)) return ''
  const texts: string[] = []
  for (const block of value) {
    if (!block || typeof block !== 'object') continue
    const data = block as Record<string, unknown>
    if (data.type === 'text' && typeof data.text === 'string' && data.text.trim()) {
      texts.push(data.text.trim())
    }
  }
  return texts.join('\n\n')
}

const normalizeReasoningSegments = (value: unknown): ThinkingSegment[] => {
  if (!Array.isArray(value)) return []
  const result: ThinkingSegment[] = []
  value.forEach((item, index) => {
    if (!item || typeof item !== 'object') return
    const data = item as Record<string, unknown>
    const content = typeof data.content === 'string' ? data.content.trim() : ''
    if (!content) return
    const rawToolCallCount = data.tool_call_count ?? data.toolCallCount ?? 0
    const toolCallCount = Number.isFinite(Number(rawToolCallCount))
      ? Math.max(0, Math.floor(Number(rawToolCallCount)))
      : 0
    result.push({
      id: String(data.id || `thinking-history-global-${index}`),
      content,
      toolCallCount,
      reasoningTurnId: typeof data.reasoning_turn_id === 'string'
        ? data.reasoning_turn_id.trim() || undefined
        : typeof data.reasoningTurnId === 'string'
          ? data.reasoningTurnId.trim() || undefined
          : undefined,
    })
  })
  return result
}

const appendHistoryTextSegment = (message: Message, content: string) => {
  const normalizedContent = String(content || '').trim()
  if (!normalizedContent) return
  const segments = [...(message.segments || [])]
  const lastSegment = segments[segments.length - 1]
  if (lastSegment?.type === 'text') {
    segments[segments.length - 1] = {
      ...lastSegment,
      content: `${lastSegment.content}${normalizedContent}`,
    }
  } else {
    segments.push({ type: 'text', content: normalizedContent })
  }
  message.content = `${message.content}${message.content ? '\n' : ''}${normalizedContent}`
  message.segments = segments
}

const appendHistoryThinkingSegment = (message: Message, segment: ThinkingSegment) => {
  if (message.thinkingSegments?.some(item => item.id === segment.id)) return
  message.thinkingSegments = [...(message.thinkingSegments || []), segment]
  message.segments = [
    ...(message.segments || []),
    { type: 'thinkingSegment', thinkingSegmentId: segment.id },
  ]
}

const appendHistoryToolCall = (message: Message, toolCall: ToolCallInfo) => {
  if (message.toolCalls?.some(item => item.tool_call_id === toolCall.tool_call_id)) return
  message.toolCalls = [...(message.toolCalls || []), toolCall]
  message.segments = [
    ...(message.segments || []),
    { type: 'toolCall', toolCallId: toolCall.tool_call_id },
  ]
}

const splitReasoningSegmentsByTurn = (segments: ThinkingSegment[]): ThinkingSegment[][] => {
  const groups: ThinkingSegment[][] = []
  for (const segment of segments) {
    const latestGroup = groups[groups.length - 1]
    const previousSegment = latestGroup?.[latestGroup.length - 1]
    if (!latestGroup || (previousSegment && segment.toolCallCount <= previousSegment.toolCallCount)) {
      groups.push([segment])
    } else {
      latestGroup.push(segment)
    }
  }
  return groups
}

interface HistoryMessagePayload {
  id?: string
  role?: Message['role']
  content?: unknown
  timestamp?: unknown
  reasoning_content?: unknown
  file_selections?: unknown
  tool_calls?: unknown
  tool_call_id?: unknown
  tool_name?: unknown
  reasoning_turn_id?: unknown
  subagent_trail?: unknown
  subagent_context_usage?: unknown
}

interface HistoryEventPayload {
  sequence_no?: unknown
  turn_id?: unknown
  event_type?: unknown
  payload?: unknown
}

const normalizeContextCompressionEvent = (value: unknown): ChatDataContextCompressionEvent | null => {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const id = String(raw.id || '').trim()
  if (!id) return null
  const toNonNegativeNumber = (item: unknown): number => {
    const parsed = Number(item)
    return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0
  }
  return {
    id,
    turn_id: typeof raw.turn_id === 'string' ? raw.turn_id : undefined,
    anchor_message_id: typeof raw.anchor_message_id === 'string' ? raw.anchor_message_id : undefined,
    timestamp: typeof raw.timestamp === 'string' ? raw.timestamp : undefined,
    estimated_tokens_before: toNonNegativeNumber(raw.estimated_tokens_before),
    estimated_tokens_after: toNonNegativeNumber(raw.estimated_tokens_after),
    estimated_cleared_tokens: toNonNegativeNumber(raw.estimated_cleared_tokens),
    compressed_messages: toNonNegativeNumber(raw.compressed_messages),
  }
}

const getHistoryToolCall = (value: unknown): ToolCallInfo | null => {
  if (!value || typeof value !== 'object') return null
  const toolCall = value as { id?: unknown; name?: unknown; args?: unknown }
  const toolCallId = String(toolCall.id || '').trim()
  const toolName = String(toolCall.name || '').trim()
  if (!toolCallId || !toolName) return null
  return {
    tool_call_id: toolCallId,
    tool_name: toolName,
    args: toolCall.args && typeof toolCall.args === 'object' ? toolCall.args as Record<string, unknown> : {},
    status: 'completed',
  }
}

// 工具执行起止时间（epoch 毫秒）：tool_result 事件 payload 顶层字段，非法值丢弃
const readExecutionTiming = (payload: unknown): Pick<ToolCallInfo, 'started_at_ms' | 'ended_at_ms'> | undefined => {
  if (!payload || typeof payload !== 'object') return undefined
  const raw = payload as Record<string, unknown>
  const normalize = (value: unknown): number | null => {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : null
  }
  const started = normalize(raw.started_at_ms)
  const ended = normalize(raw.ended_at_ms)
  if (started === null && ended === null) return undefined
  return { started_at_ms: started, ended_at_ms: ended }
}

// 子智能体过程快照：随 task 的 tool_result payload 持久化（后端盖在
// additional_kwargs.subagent_trail），历史回放时据此还原「子智能体内部逻辑」。
// 逐条目校验，坏数据丢弃条目而不是整块丢弃。
const normalizeSubagentTrail = (value: unknown): SubagentTrailItem[] | undefined => {
  if (!Array.isArray(value)) return undefined
  const trail: SubagentTrailItem[] = []
  for (const raw of value) {
    if (!raw || typeof raw !== 'object') continue
    const item = raw as Record<string, unknown>
    const id = String(item.id || '').trim()
    if (!id) continue
    if (item.kind === 'message') {
      const content = typeof item.content === 'string' ? item.content : ''
      const thinking = typeof item.thinking === 'string' ? item.thinking : ''
      if (!content && !thinking) continue
      trail.push({
        kind: 'message',
        id,
        content,
        truncated: item.truncated === true,
        thinking,
        thinkingTruncated: item.thinkingTruncated === true,
      })
    } else if (item.kind === 'tool') {
      const status = item.status === 'error' ? 'error' : item.status === 'running' ? 'running' : 'completed'
      trail.push({
        kind: 'tool',
        id,
        tool_name: typeof item.tool_name === 'string' ? item.tool_name : '',
        args: item.args && typeof item.args === 'object' ? item.args as Record<string, unknown> : {},
        result: typeof item.result === 'string' ? item.result : '',
        status,
      })
    }
  }
  return trail.length > 0 ? trail : undefined
}

// 子智能体上下文用量快照：随 task 的 tool_result payload 持久化（后端盖在
// additional_kwargs.subagent_context_usage），历史回放时 task 卡片标题行还原用量徽标。
const normalizeSubagentContextUsage = (value: unknown): SubagentContextUsage | undefined => {
  if (!value || typeof value !== 'object') return undefined
  const raw = value as Record<string, unknown>
  const toNonNegativeInt = (item: unknown): number => {
    const parsed = Number(item)
    return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : 0
  }
  const usedTokens = toNonNegativeInt(raw.used_tokens)
  if (usedTokens <= 0) return undefined
  return {
    used_tokens: usedTokens,
    threshold_tokens: toNonNegativeInt(raw.threshold_tokens),
    percent: toNonNegativeInt(raw.percent),
  }
}

const withHistoryToolResult = (
  toolCall: ToolCallInfo,
  result: string,
  subagentTrail?: SubagentTrailItem[],
  timing?: Pick<ToolCallInfo, 'started_at_ms' | 'ended_at_ms'>,
  subagentContextUsage?: SubagentContextUsage,
): ToolCallInfo => {
  let renderId: string | undefined
  let renderType: ToolCallInfo['renderType']
  let semanticParams: SemanticParams | undefined

  // 查询结果渲染：data_analyzing_tool 的 execute_query
  const isQueryResultTool = toolCall.tool_name === 'data_analyzing_tool' &&
    (toolCall.args || {})?.action === 'execute_query'

  if (isQueryResultTool) {
    const queryResultData = getRenderableQueryResultData(result)
    if (queryResultData) {
      renderId = queryResultData
      renderType = 'queryResult'
    }
    semanticParams = extractSemanticParamsFromArgs(toolCall.args)
  }

  return {
    ...toolCall,
    result,
    status: getQueryResultError(result) ? 'error' : 'completed',
    renderId,
    renderType,
    semanticParams,
    subagentTrail,
    subagentContextUsage: subagentContextUsage ?? toolCall.subagentContextUsage,
    started_at_ms: timing?.started_at_ms ?? toolCall.started_at_ms ?? null,
    ended_at_ms: timing?.ended_at_ms ?? toolCall.ended_at_ms ?? null,
  }
}

export const buildHistoryMessagesFromEvents = (historyEvents: unknown): Message[] => {
  if (!Array.isArray(historyEvents)) return []

  const events = historyEvents
    .filter((raw): raw is HistoryEventPayload => Boolean(raw && typeof raw === 'object'))
    .map((raw, index) => ({ raw, index }))
    .sort((left, right) => {
      const leftSequence = Number(left.raw.sequence_no)
      const rightSequence = Number(right.raw.sequence_no)
      const normalizedLeft = Number.isFinite(leftSequence) ? leftSequence : left.index
      const normalizedRight = Number.isFinite(rightSequence) ? rightSequence : right.index
      return normalizedLeft - normalizedRight
    })

  const displayMessages: Message[] = []
  const toolCallTargets = new Map<string, Message>()
  const persistedReasoningTurnIds = new Set<string>()
  let activeAssistant: Message | null = null

  const ensureActiveAssistant = (rawMessage: HistoryMessagePayload, turnId?: string): Message => {
    if (activeAssistant) return activeAssistant
    activeAssistant = {
      id: String(rawMessage.id || `assistant-history-${turnId || displayMessages.length}`),
      role: 'assistant',
      content: '',
      turnId: turnId || undefined,
      timestamp: parseHistoryTimestamp(rawMessage.timestamp) || undefined,
      segments: [],
    }
    displayMessages.push(activeAssistant)
    return activeAssistant
  }

  for (const { raw, index } of events) {
    const eventType = String(raw.event_type || '').trim()
    const turnId = String(raw.turn_id || '').trim()
    const payload = raw.payload
    if (!payload || typeof payload !== 'object') continue
    const messagePayload = payload as HistoryMessagePayload

    if (eventType === 'user_message') {
      activeAssistant = null
      const fileSelections = normalizeFileSelections(messagePayload.file_selections)
      const userContent = stripMentionSuffix(normalizeUserMessageContent(messagePayload.content))
      displayMessages.push({
        id: String(messagePayload.id || `user-history-${index}`),
        role: 'user',
        content: userContent,
        turnId: turnId || undefined,
        timestamp: parseHistoryTimestamp(messagePayload.timestamp) || undefined,
        fileSelections: fileSelections.length > 0 ? fileSelections : undefined,
      })
      continue
    }

    if (eventType === 'context_compression') {
      const contextCompression = normalizeContextCompressionEvent(payload)
      if (!contextCompression || displayMessages.some(message => message.id === contextCompression.id)) continue
      activeAssistant = null
      displayMessages.push({
        id: contextCompression.id,
        role: 'context_compression',
        content: '',
        turnId: turnId || undefined,
        timestamp: parseHistoryTimestamp(contextCompression.timestamp) || undefined,
        contextCompression,
      })
      continue
    }

    if (eventType === 'reasoning') {
      const content = normalizeReasoningContent(messagePayload.content)
      if (!content) continue
      const assistant = ensureActiveAssistant(messagePayload, turnId)
      appendHistoryThinkingSegment(assistant, {
        id: String(messagePayload.id || `thinking-history-${turnId || index}`),
        content,
        toolCallCount: 0,
        reasoningTurnId: turnId || undefined,
      })
      if (turnId) persistedReasoningTurnIds.add(turnId)
      continue
    }

    if (eventType === 'assistant_message') {
      const assistant = ensureActiveAssistant(messagePayload, turnId)
      assistant.timestamp = parseHistoryTimestamp(messagePayload.timestamp) || assistant.timestamp
      const fallbackReasoning = normalizeReasoningContent(messagePayload.reasoning_content)
      if (fallbackReasoning && (!turnId || !persistedReasoningTurnIds.has(turnId))) {
        appendHistoryThinkingSegment(assistant, {
          id: `thinking-history-${messagePayload.id || assistant.id}-${assistant.thinkingSegments?.length || 0}`,
          content: fallbackReasoning,
          toolCallCount: 0,
          reasoningTurnId: turnId || undefined,
        })
      }
      // LangChain AIMessage content precedes its tool_calls. Live streaming
      // naturally renders that order; replay must preserve it as well.
      appendHistoryTextSegment(assistant, String(messagePayload.content || ''))
      const rawToolCalls = Array.isArray(messagePayload.tool_calls) ? messagePayload.tool_calls : []
      for (const rawToolCall of rawToolCalls) {
        const toolCall = getHistoryToolCall(rawToolCall)
        if (!toolCall) continue
        appendHistoryToolCall(assistant, toolCall)
        toolCallTargets.set(toolCall.tool_call_id, assistant)
      }
      continue
    }

    if (eventType !== 'tool_result') continue
    const toolCallId = String(messagePayload.tool_call_id || '').trim()
    const target = toolCallTargets.get(toolCallId)
    if (!target?.toolCalls?.length) continue
    const result = String(messagePayload.content || '')
    const subagentTrail = normalizeSubagentTrail(messagePayload.subagent_trail)
    const timing = readExecutionTiming(messagePayload)
    const subagentContextUsage = normalizeSubagentContextUsage(messagePayload.subagent_context_usage)
    target.toolCalls = target.toolCalls.map(toolCall => (
      toolCall.tool_call_id === toolCallId ? withHistoryToolResult(toolCall, result, subagentTrail, timing, subagentContextUsage) : toolCall
    ))
  }
  return displayMessages
}

export const buildHistoryMessages = (
  historyMessages: unknown,
  reasoningSegments: ThinkingSegment[],
): Message[] => {
  if (!Array.isArray(historyMessages)) return []

  const displayMessages: Message[] = []
  const legacyReasoningGroups = splitReasoningSegmentsByTurn(
    reasoningSegments.filter(segment => !segment.reasoningTurnId),
  )
  const reasoningSegmentsByTurn = new Map<string, ThinkingSegment[]>()
  for (const segment of reasoningSegments) {
    if (!segment.reasoningTurnId) continue
    const current = reasoningSegmentsByTurn.get(segment.reasoningTurnId) || []
    current.push(segment)
    reasoningSegmentsByTurn.set(segment.reasoningTurnId, current)
  }
  const toolCallTargets = new Map<string, Message>()
  let activeAssistant: Message | null = null
  let legacyReasoningGroupIndex = -1
  let currentReasoningSegments: ThinkingSegment[] = []
  let nextReasoningSegmentIndex = 0
  let visibleToolCallCount = 0

  const startReasoningTurn = (reasoningTurnId?: string) => {
    if (reasoningTurnId) {
      currentReasoningSegments = reasoningSegmentsByTurn.get(reasoningTurnId) || []
    } else {
      legacyReasoningGroupIndex += 1
      currentReasoningSegments = legacyReasoningGroups[legacyReasoningGroupIndex] || []
    }
    nextReasoningSegmentIndex = 0
    visibleToolCallCount = 0
  }

  const ensureActiveAssistant = (rawMessage: HistoryMessagePayload): Message => {
    if (activeAssistant) return activeAssistant
    activeAssistant = {
      id: String(rawMessage.id || `assistant-history-${displayMessages.length}`),
      role: 'assistant',
      content: '',
      timestamp: parseHistoryTimestamp(rawMessage.timestamp) || undefined,
      segments: [],
    }
    displayMessages.push(activeAssistant)
    return activeAssistant
  }

  const appendPendingThinkingSegments = (assistant: Message) => {
    while (
      nextReasoningSegmentIndex < currentReasoningSegments.length
      && currentReasoningSegments[nextReasoningSegmentIndex]!.toolCallCount <= visibleToolCallCount
    ) {
      appendHistoryThinkingSegment(assistant, currentReasoningSegments[nextReasoningSegmentIndex]!)
      nextReasoningSegmentIndex += 1
    }
  }

  for (const raw of historyMessages) {
    if (!raw || typeof raw !== 'object') continue
    const rawMessage = raw as HistoryMessagePayload
    const role = rawMessage.role

    if (role === 'user') {
      const reasoningTurnId = typeof rawMessage.reasoning_turn_id === 'string'
        ? rawMessage.reasoning_turn_id.trim()
        : ''
      startReasoningTurn(reasoningTurnId || undefined)
      activeAssistant = null
      const fileSelections = normalizeFileSelections(rawMessage.file_selections)
      displayMessages.push({
        id: String(rawMessage.id || `user-history-${displayMessages.length}`),
        role: 'user',
        content: stripMentionSuffix(String(rawMessage.content || '').trim()),
        timestamp: parseHistoryTimestamp(rawMessage.timestamp) || undefined,
        fileSelections: fileSelections.length > 0 ? fileSelections : undefined,
      })
      continue
    }

    if (role === 'assistant') {
      if (legacyReasoningGroupIndex < 0 && currentReasoningSegments.length === 0) startReasoningTurn()
      const assistant = ensureActiveAssistant(rawMessage)
      assistant.timestamp = parseHistoryTimestamp(rawMessage.timestamp) || assistant.timestamp
      appendPendingThinkingSegments(assistant)

      const fallbackReasoning = normalizeReasoningContent(rawMessage.reasoning_content)
      if (!currentReasoningSegments.length && fallbackReasoning) {
        appendHistoryThinkingSegment(assistant, {
          id: `thinking-history-${rawMessage.id || assistant.id}-${assistant.thinkingSegments?.length || 0}`,
          content: fallbackReasoning,
          toolCallCount: visibleToolCallCount,
        })
      }

      appendHistoryTextSegment(assistant, String(rawMessage.content || ''))
      const rawToolCalls = Array.isArray(rawMessage.tool_calls) ? rawMessage.tool_calls : []
      for (const rawToolCall of rawToolCalls) {
        const toolCall = getHistoryToolCall(rawToolCall)
        if (!toolCall) continue
        appendHistoryToolCall(assistant, toolCall)
        toolCallTargets.set(toolCall.tool_call_id, assistant)
        visibleToolCallCount += 1
      }
      continue
    }

    if (role !== 'tool') continue
    const toolCallId = String(rawMessage.tool_call_id || '').trim()
    const target = toolCallTargets.get(toolCallId)
    if (!target?.toolCalls?.length) continue
    const result = String(rawMessage.content || '')
    const subagentTrail = normalizeSubagentTrail(rawMessage.subagent_trail)
    const timing = readExecutionTiming(rawMessage)
    const subagentContextUsage = normalizeSubagentContextUsage(rawMessage.subagent_context_usage)
    target.toolCalls = target.toolCalls.map(toolCall => (
      toolCall.tool_call_id === toolCallId ? withHistoryToolResult(toolCall, result, subagentTrail, timing, subagentContextUsage) : toolCall
    ))
  }

  if (activeAssistant) {
    appendPendingThinkingSegments(activeAssistant)
  }
  return displayMessages
}

// @ 引用工作区文件留痕：相对路径字符串列表，无效条目丢弃、按序去重
const normalizeFileSelections = (value: unknown): string[] => {
  const rawItems = Array.isArray(value) ? value : [value]
  const selections: string[] = []
  for (const item of rawItems) {
    const normalized = String(item || '').trim().replace(/^\/+|\/+$/g, '')
    if (!normalized || selections.includes(normalized)) continue
    selections.push(normalized)
  }
  return selections
}

// 历史用户消息正文里的服务端注入短句不重复展示，引用改由 chip 呈现：
// 现行为 [引用文件：...]；历史存量还有 [本轮指定语义模型：...]（通道已废弃，仅剥文本）
const MENTION_SUFFIX_RES = [
  /\s*\[引用文件：[^\]]*\]\s*$/,
  /\s*\[本轮指定语义模型：[^\]]*\]\s*$/,
]
const stripMentionSuffix = (content: string): string =>
  MENTION_SUFFIX_RES.reduce((text, regex) => text.replace(regex, ''), content).trimEnd()

interface UseConversationHistoryOptions {
  conversationId: Ref<string | null | undefined>
  messages: Ref<Message[]>
  tokenUsage?: Ref<ChatDataTokenUsage | null>
  onActiveRunLoaded?: (activeRun: {
    run_id: string
    status: string
    turn_id?: string | null
    is_continuation?: boolean
    history_checkpoint?: { run_id?: string; stream_id?: string } | null
  } | null) => void
}

/** 历史事件单页条数：长会话全量拉取的字节量随会话线性增长，是加载变慢的主因 */
export const HISTORY_PAGE_SIZE = 100

/** 读取窗口内最小 sequence_no，作为向上翻页的游标；无法解析时返回 null */
const readEarliestSequenceNo = (events: unknown[]): number | null => {
  let earliest: number | null = null
  for (const raw of events) {
    if (!raw || typeof raw !== 'object') continue
    const value = Number((raw as { sequence_no?: unknown }).sequence_no)
    if (!Number.isFinite(value)) continue
    if (earliest === null || value < earliest) earliest = value
  }
  return earliest
}

export function useConversationHistory({
  conversationId,
  messages,
  tokenUsage,
  onActiveRunLoaded,
}: UseConversationHistoryOptions) {
  const isLoadingHistory = ref(false)
  const loadedConversationId = ref<string | null>(null)
  const isFirstMessage = ref(true)
  // 保留原始事件而非只保留渲染结果，向上翻页时才能把更早事件拼回同一序列重建消息
  const historyEvents = ref<unknown[]>([])
  const hasMoreHistory = ref(false)
  const isLoadingEarlierHistory = ref(false)

  const applyHistoryEvents = (events: unknown[]) => {
    historyEvents.value = events
    messages.value = buildHistoryMessagesFromEvents(events)
    isFirstMessage.value = messages.value.length === 0
  }

  // 加载历史消息
  const loadHistory = async (convId: string, force = false) => {
    // 已加载过，跳过
    if (!force && loadedConversationId.value === convId) {
      return
    }

    // 如果已有消息，说明是刚创建的新会话或正在对话中，不需要加载历史
    if (!force && messages.value.length > 0) {
      loadedConversationId.value = convId
      return
    }

    const showLoading = !force || messages.value.length === 0
    if (showLoading) isLoadingHistory.value = true
    try {
      const response = await getConversationHistory(convId, { limit: HISTORY_PAGE_SIZE })

      if (response.ok) {
        const data = await response.json()
        const historyMessages = data.data?.messages || []
        const historyEventsRaw = data.data?.history_events
        const historyReasoningSegments = normalizeReasoningSegments(data.data?.reasoning_segments)
        const historyTokenUsage = data.data?.token_usage
        tokenUsage && (tokenUsage.value = historyTokenUsage && typeof historyTokenUsage === 'object' ? historyTokenUsage as ChatDataTokenUsage : null)
        onActiveRunLoaded?.(normalizeActiveRun(data.data?.active_run))

        if (Array.isArray(historyEventsRaw) && historyEventsRaw.length > 0) {
          applyHistoryEvents(historyEventsRaw)
        } else {
          historyEvents.value = []
          messages.value = buildHistoryMessages(historyMessages, historyReasoningSegments)
          isFirstMessage.value = messages.value.length === 0
        }
        hasMoreHistory.value = Boolean(data.data?.has_more)
      }
      loadedConversationId.value = convId
    } catch (error) {
      console.error('加载历史消息失败:', error)
    } finally {
      if (showLoading) isLoadingHistory.value = false
    }
  }

  // 向上翻页：拉取比当前窗口更早的一页，拼回序列后重建消息
  const loadEarlierHistory = async () => {
    const convId = conversationId.value
    if (!convId || isLoadingEarlierHistory.value || !hasMoreHistory.value) return
    const cursor = readEarliestSequenceNo(historyEvents.value)
    if (cursor === null) {
      // 拿不到游标说明历史结构异常，停止翻页避免死循环
      hasMoreHistory.value = false
      return
    }
    isLoadingEarlierHistory.value = true
    try {
      const response = await getConversationHistory(convId, {
        limit: HISTORY_PAGE_SIZE,
        beforeSequence: cursor,
      })
      // 会话可能已在请求期间切换：只应用属于当前会话的响应，避免旧会话事件污染新会话
      if (!response.ok || conversationId.value !== convId) return
      const data = await response.json()
      const olderEvents = Array.isArray(data.data?.history_events) ? data.data.history_events : []
      hasMoreHistory.value = Boolean(data.data?.has_more)
      if (olderEvents.length === 0) return
      historyEvents.value = [...olderEvents, ...historyEvents.value]
      // 前拼而不是整表重建：messages 里还有历史窗口之外的本会话实时消息
      // （已发送未落库的提问、流式进行中的回答），applyHistoryEvents 全量重建会把它们抹掉。
      // 后端保证每页是一段连续的 sequence_no 区间、且以轮次起点收尾，
      // 所以「旧页单独构建 + 前拼」与「整段重建」等价，不会漏事件也不会切断轮次。
      messages.value = [...buildHistoryMessagesFromEvents(olderEvents), ...messages.value]
      isFirstMessage.value = messages.value.length === 0
    } catch (error) {
      console.error('加载更早的历史消息失败:', error)
    } finally {
      isLoadingEarlierHistory.value = false
    }
  }

  // 监听 conversationId 变化
  // - immediate（组件挂载/重挂载）：从侧边栏选中历史会话后经 conversationKey 重挂载，正常加载历史。
  // - 非 immediate 的变化（conversationId 在不重挂载情况下变化）：只发生在"上传/发首条消息
  //   时本地新建会话"。新会话无历史，直接标记已加载，避免触发 isLoadingHistory 闪烁导致
  //   输入区"刷新"。选历史会话走父组件 conversationKey 重挂载，不会进入此分支。
  watch(
    conversationId,
    (newId, oldId) => {
      if (!newId || newId === oldId) return
      if (oldId === undefined) {
        loadHistory(newId)
        return
      }
      loadedConversationId.value = newId
    },
    { immediate: true }
  )

  // 重置状态（用于新建会话）
  const resetHistory = () => {
    loadedConversationId.value = null
    isFirstMessage.value = true
    historyEvents.value = []
    hasMoreHistory.value = false
  }

  return {
    isLoadingHistory,
    loadedConversationId,
    isFirstMessage,
    loadHistory,
    resetHistory,
    hasMoreHistory,
    isLoadingEarlierHistory,
    loadEarlierHistory,
  }
}

const normalizeActiveRun = (value: unknown): {
  run_id: string
  status: string
  turn_id?: string | null
  is_continuation?: boolean
  history_checkpoint?: { run_id?: string; stream_id?: string } | null
} | null => {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const runId = String(raw.run_id || '').trim()
  const status = String(raw.status || '').trim()
  if (!runId || !status) return null
  const rawCheckpoint = raw.history_checkpoint
  const checkpoint = rawCheckpoint && typeof rawCheckpoint === 'object'
    ? rawCheckpoint as Record<string, unknown>
    : null
  const streamId = String(checkpoint?.stream_id || '').trim()
  return {
    run_id: runId,
    status,
    turn_id: String(raw.turn_id || '').trim() || null,
    is_continuation: raw.is_continuation === true,
    history_checkpoint: streamId
      ? { run_id: String(checkpoint?.run_id || runId), stream_id: streamId }
      : null,
  }
}

export default useConversationHistory
