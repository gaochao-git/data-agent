/**
 * 会话历史加载 composable
 * 用于加载和恢复会话历史消息（参考 React 版本）
 */
import { ref, watch, type Ref } from 'vue'
import { getConversationHistory } from '../api'
import type { ChatDataTokenUsage, ToolCallInfo, Message, SemanticConfigSelectionInfo, SemanticParams, ThinkingSegment } from '../types'
import { extractSemanticParamsFromArgs, getRenderableQueryResultData } from '../utils/semanticTool'

const HIDDEN_TOOLS = ['load_skill_tool']

const parseHistoryTimestamp = (value: unknown): Date | null => {
  if (typeof value !== 'string' || !value.trim()) return null
  const parsed = new Date(value.trim().replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const normalizeReasoningContent = (value: unknown): string => {
  return typeof value === 'string' ? value.trim() : ''
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
  if (HIDDEN_TOOLS.includes(toolCall.tool_name)) return
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
  semantic_config_selection?: unknown
  tool_calls?: unknown
  tool_call_id?: unknown
  tool_name?: unknown
  reasoning_turn_id?: unknown
}

interface HistoryEventPayload {
  sequence_no?: unknown
  turn_id?: unknown
  event_type?: unknown
  payload?: unknown
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

const withHistoryToolResult = (toolCall: ToolCallInfo, result: string): ToolCallInfo => {
  let renderId: string | undefined
  let renderType: ToolCallInfo['renderType']
  let semanticParams: SemanticParams | undefined

  if (toolCall.tool_name === 'semantic_query_tool') {
    const queryResultData = getRenderableQueryResultData(result)
    if (queryResultData) {
      renderId = queryResultData
      renderType = 'queryResult'
    }
    semanticParams = extractSemanticParamsFromArgs(toolCall.args)
  } else if (toolCall.tool_name === 'generate_chart_tool') {
    const chartMatch = result.match(/<chart_config>([^<]+)<\/chart_config>/)
    const chartId = chartMatch?.[1]?.trim()
    if (chartId && /^[a-f0-9-]{36}$/.test(chartId)) {
      renderId = chartId
      renderType = 'chart'
    }
  }

  return {
    ...toolCall,
    result,
    status: 'completed',
    renderId,
    renderType,
    semanticParams,
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
  let pendingSemanticConfigSelection: SemanticConfigSelectionInfo | null = null

  const ensureActiveAssistant = (rawMessage: HistoryMessagePayload, turnId?: string): Message => {
    if (activeAssistant) return activeAssistant
    activeAssistant = {
      id: String(rawMessage.id || `assistant-history-${turnId || displayMessages.length}`),
      role: 'assistant',
      content: '',
      timestamp: parseHistoryTimestamp(rawMessage.timestamp) || undefined,
      segments: [],
    }
    if (pendingSemanticConfigSelection) {
      applySemanticConfigSelection(activeAssistant, pendingSemanticConfigSelection)
      pendingSemanticConfigSelection = null
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
      pendingSemanticConfigSelection = normalizeSemanticConfigSelection(messagePayload.semantic_config_selection)
      displayMessages.push({
        id: String(messagePayload.id || `user-history-${index}`),
        role: 'user',
        content: String(messagePayload.content || '').trim(),
        timestamp: parseHistoryTimestamp(messagePayload.timestamp) || undefined,
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
      const rawToolCalls = Array.isArray(messagePayload.tool_calls) ? messagePayload.tool_calls : []
      for (const rawToolCall of rawToolCalls) {
        const toolCall = getHistoryToolCall(rawToolCall)
        if (!toolCall) continue
        appendHistoryToolCall(assistant, toolCall)
        toolCallTargets.set(toolCall.tool_call_id, assistant)
      }
      appendHistoryTextSegment(assistant, String(messagePayload.content || ''))
      continue
    }

    if (eventType !== 'tool_result') continue
    const toolCallId = String(messagePayload.tool_call_id || '').trim()
    const target = toolCallTargets.get(toolCallId)
    if (!target?.toolCalls?.length) continue
    const result = String(messagePayload.content || '')
    target.toolCalls = target.toolCalls.map(toolCall => (
      toolCall.tool_call_id === toolCallId ? withHistoryToolResult(toolCall, result) : toolCall
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
  let pendingSemanticConfigSelection: SemanticConfigSelectionInfo | null = null
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
    if (pendingSemanticConfigSelection) {
      applySemanticConfigSelection(activeAssistant, pendingSemanticConfigSelection)
      pendingSemanticConfigSelection = null
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
      pendingSemanticConfigSelection = normalizeSemanticConfigSelection(rawMessage.semantic_config_selection)
      displayMessages.push({
        id: String(rawMessage.id || `user-history-${displayMessages.length}`),
        role: 'user',
        content: String(rawMessage.content || '').trim(),
        timestamp: parseHistoryTimestamp(rawMessage.timestamp) || undefined,
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

      const rawToolCalls = Array.isArray(rawMessage.tool_calls) ? rawMessage.tool_calls : []
      for (const rawToolCall of rawToolCalls) {
        const toolCall = getHistoryToolCall(rawToolCall)
        if (!toolCall) continue
        appendHistoryToolCall(assistant, toolCall)
        toolCallTargets.set(toolCall.tool_call_id, assistant)
        if (!HIDDEN_TOOLS.includes(toolCall.tool_name)) visibleToolCallCount += 1
      }
      appendHistoryTextSegment(assistant, String(rawMessage.content || ''))
      continue
    }

    if (role !== 'tool') continue
    const toolCallId = String(rawMessage.tool_call_id || '').trim()
    const target = toolCallTargets.get(toolCallId)
    if (!target?.toolCalls?.length) continue
    const result = String(rawMessage.content || '')
    target.toolCalls = target.toolCalls.map(toolCall => (
      toolCall.tool_call_id === toolCallId ? withHistoryToolResult(toolCall, result) : toolCall
    ))
  }

  if (activeAssistant) {
    appendPendingThinkingSegments(activeAssistant)
  }
  return displayMessages
}

const normalizeSemanticConfigSelection = (value: unknown): SemanticConfigSelectionInfo | null => {
  if (!value || typeof value !== 'object') return null
  const data = value as Record<string, unknown>
  const semanticConfigId = String(data.semantic_config_id || '').trim()
  if (!semanticConfigId) return null
  const matchedMetrics = Array.isArray(data.matched_metrics)
    ? data.matched_metrics.map(item => String(item || '').trim()).filter(Boolean)
    : []
  const matchedDimensions = Array.isArray(data.matched_dimensions)
    ? data.matched_dimensions.map(item => String(item || '').trim()).filter(Boolean)
    : []
  return {
    semantic_config_id: semanticConfigId,
    semantic_config_name: typeof data.semantic_config_name === 'string' ? data.semantic_config_name : undefined,
    confidence: typeof data.confidence === 'string' ? data.confidence : undefined,
    reason: typeof data.reason === 'string' ? data.reason : undefined,
    matched_metrics: matchedMetrics,
    matched_dimensions: matchedDimensions,
    auto_selected: Boolean(data.auto_selected),
    user_selected: Boolean(data.user_selected),
  }
}

const applySemanticConfigSelection = (message: Message, selection: SemanticConfigSelectionInfo) => {
  if (message.semanticConfigSelection?.user_selected && !selection.user_selected) return
  message.semanticConfigSelection = selection
}

interface UseConversationHistoryOptions {
  conversationId: Ref<string | null | undefined>
  messages: Ref<Message[]>
  tokenUsage?: Ref<ChatDataTokenUsage | null>
}

export function useConversationHistory({
  conversationId,
  messages,
  tokenUsage,
}: UseConversationHistoryOptions) {
  const isLoadingHistory = ref(false)
  const loadedConversationId = ref<string | null>(null)
  const isFirstMessage = ref(true)

  // 加载历史消息
  const loadHistory = async (convId: string) => {
    // 已加载过，跳过
    if (loadedConversationId.value === convId) {
      return
    }

    // 如果已有消息，说明是刚创建的新会话或正在对话中，不需要加载历史
    if (messages.value.length > 0) {
      loadedConversationId.value = convId
      return
    }

    isLoadingHistory.value = true
    try {
      const response = await getConversationHistory(convId)

      if (response.ok) {
        const data = await response.json()
        const historyMessages = data.data?.messages || []
        const historyEvents = data.data?.history_events
        const historyReasoningSegments = normalizeReasoningSegments(data.data?.reasoning_segments)
        const historyTokenUsage = data.data?.token_usage
        tokenUsage && (tokenUsage.value = historyTokenUsage && typeof historyTokenUsage === 'object' ? historyTokenUsage as ChatDataTokenUsage : null)

        messages.value = Array.isArray(historyEvents) && historyEvents.length > 0
          ? buildHistoryMessagesFromEvents(historyEvents)
          : buildHistoryMessages(historyMessages, historyReasoningSegments)
        isFirstMessage.value = messages.value.length === 0
      }
      loadedConversationId.value = convId
    } catch (error) {
      console.error('加载历史消息失败:', error)
    } finally {
      isLoadingHistory.value = false
    }
  }

  // 监听 conversationId 变化
  watch(
    conversationId,
    (newId, oldId) => {
      if (newId && newId !== oldId) {
        loadHistory(newId)
      }
    },
    { immediate: true }
  )

  // 重置状态（用于新建会话）
  const resetHistory = () => {
    loadedConversationId.value = null
    isFirstMessage.value = true
  }

  return {
    isLoadingHistory,
    loadedConversationId,
    isFirstMessage,
    loadHistory,
    resetHistory,
  }
}

export default useConversationHistory
