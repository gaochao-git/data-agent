/**
 * 会话历史加载 composable
 * 用于加载和恢复会话历史消息（参考 React 版本）
 */
import { ref, watch, type Ref } from 'vue'
import { getConversationHistory } from '../api'
import type { ChatDataTokenUsage, ToolCallInfo, Message, MessageSegment, SemanticConfigSelectionInfo, SemanticParams, ThinkingSegment } from '../types'
import { extractSemanticParamsFromArgs, getRenderableQueryResultData } from '../utils/semanticTool'

const HIDDEN_TOOLS = ['load_skill_tool']

const parseHistoryTimestamp = (value: unknown): Date | null => {
  if (typeof value !== 'string' || !value.trim()) return null
  const parsed = new Date(value.trim().replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

const buildAssistantHistorySegments = (message: Message): MessageSegment[] => {
  const segments: MessageSegment[] = []
  const thinkingSegments = message.thinkingSegments || []
  const visibleToolCalls = (message.toolCalls || []).filter(toolCall => !HIDDEN_TOOLS.includes(toolCall.tool_name))
  const emittedThinkingSegmentIds = new Set<string>()

  for (let toolIndex = 0; toolIndex < visibleToolCalls.length; toolIndex += 1) {
    for (const thinkingSegment of thinkingSegments) {
      if (thinkingSegment.toolCallCount === toolIndex) {
        segments.push({ type: 'thinkingSegment', thinkingSegmentId: thinkingSegment.id })
        emittedThinkingSegmentIds.add(thinkingSegment.id)
      }
    }
    const toolCall = visibleToolCalls[toolIndex]
    if (!toolCall) continue
    segments.push({ type: 'toolCall', toolCallId: toolCall.tool_call_id })
  }

  for (const thinkingSegment of thinkingSegments) {
    if (!emittedThinkingSegmentIds.has(thinkingSegment.id)) {
      segments.push({ type: 'thinkingSegment', thinkingSegmentId: thinkingSegment.id })
    }
  }

  const visibleContent = String(message.content || '').trim()
  if (visibleContent) {
    segments.push({ type: 'text', content: visibleContent })
  }
  return segments
}

const appendAssistantHistoryMessage = (target: Message, source: Message) => {
  if (source.timestamp) {
    target.timestamp = source.timestamp
  }
  if (source.content) {
    target.content = (target.content || '') + (target.content ? '\n' : '') + source.content
  }
  if (source.thinkingSegments?.length) {
    const currentToolCallCount = (target.toolCalls || []).filter(tool => !HIDDEN_TOOLS.includes(tool.tool_name)).length
    const normalizedThinkingSegments = source.thinkingSegments.map(segment => ({
      ...segment,
      toolCallCount: currentToolCallCount + segment.toolCallCount,
    }))
    target.thinkingSegments = [...(target.thinkingSegments || []), ...normalizedThinkingSegments]
    target.content = String(target.content || '').trim()
  }
  if (source.toolCalls?.length) {
    target.toolCalls = [...(target.toolCalls || []), ...source.toolCalls]
  }
  if (source.semanticConfigSelection) {
    target.semanticConfigSelection = source.semanticConfigSelection
  }
  target.segments = buildAssistantHistorySegments(target)
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
    })
  })
  return result
}

const applyThinkingSegmentsToHistoryMessage = (message: Message, thinkingSegments: ThinkingSegment[]) => {
  if (message.role !== 'assistant' || thinkingSegments.length === 0) return
  if (message.thinkingSegments?.length) return
  message.thinkingSegments = thinkingSegments
  message.content = String(message.content || '').trim()
  message.segments = buildAssistantHistorySegments(message)
}

const setThinkingSegmentsOnHistoryMessage = (message: Message, thinkingSegments: ThinkingSegment[]) => {
  if (message.role !== 'assistant' || thinkingSegments.length === 0) return
  message.thinkingSegments = thinkingSegments
  message.content = String(message.content || '').trim()
  message.segments = buildAssistantHistorySegments(message)
}

const applyReasoningSegmentsToHistoryMessages = (messages: Message[], reasoningSegments: ThinkingSegment[]) => {
  if (reasoningSegments.length === 0) return
  const groups: ThinkingSegment[][] = []
  for (const segment of reasoningSegments) {
    const latestGroup = groups[groups.length - 1]
    const previousSegment = latestGroup?.[latestGroup.length - 1]
    if (!latestGroup || (previousSegment && segment.toolCallCount < previousSegment.toolCallCount)) {
      groups.push([segment])
    } else {
      latestGroup.push(segment)
    }
  }
  const assistantMessages = messages.filter(message => message.role === 'assistant' && !message.thinkingSegments?.length)
  if (assistantMessages.length === 0) return
  if (groups.length === 0) return
  const startIndex = Math.max(0, assistantMessages.length - groups.length)
  groups.forEach((group, groupIndex) => {
    const targetIndex = Math.min(startIndex + groupIndex, assistantMessages.length - 1)
    const targetMessage = assistantMessages[targetIndex]
    if (!targetMessage) return
    setThinkingSegmentsOnHistoryMessage(targetMessage, [...(targetMessage.thinkingSegments || []), ...group])
  })
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
        const historyReasoningSegments = normalizeReasoningSegments(data.data?.reasoning_segments)
        const historyTokenUsage = data.data?.token_usage
        tokenUsage && (tokenUsage.value = historyTokenUsage && typeof historyTokenUsage === 'object' ? historyTokenUsage as ChatDataTokenUsage : null)

        if (historyMessages.length > 0) {
          const formattedMessages: Message[] = historyMessages.map((msg: any) => {
            const parsedTimestamp = parseHistoryTimestamp(msg.timestamp)

            const message: Message = {
              id: msg.id,
              role: msg.role,
              content: String(msg.content || '').trim(),
              timestamp: parsedTimestamp || undefined,
            }

            const reasoningContent = normalizeReasoningContent(msg.reasoning_content)
            const thinkingTexts = reasoningContent ? [reasoningContent] : []
            if (thinkingTexts.length > 0 && msg.role === 'assistant') {
              message.thinkingSegments = thinkingTexts.map((content, index) => ({
                id: `thinking-history-${msg.id || 'message'}-${index}`,
                content,
                toolCallCount: index,
              }))
            }

            const semanticConfigSelection = normalizeSemanticConfigSelection(msg.semantic_config_selection)
            if (semanticConfigSelection) {
              message.semanticConfigSelection = semanticConfigSelection
            }

            // tool 消息：保留 tool_call_id 和 tool_name
            if (msg.role === 'tool') {
              message.toolCallId = msg.tool_call_id
              message.toolName = msg.tool_name
            }

            // 从 assistant 消息中提取 tool_calls
            if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
              const toolCalls: ToolCallInfo[] = []
              for (const tc of msg.tool_calls) {
                if (tc.name && HIDDEN_TOOLS.includes(tc.name)) {
                  continue
                }
                if (tc.id && tc.name) {
                  toolCalls.push({
                    tool_call_id: tc.id,
                    tool_name: tc.name,
                    args: tc.args || {},
                    status: 'completed',
                  })
                }
              }
              if (toolCalls.length > 0) {
                message.toolCalls = toolCalls
              }
            }
            return message
          })

          // 后处理：将 tool 消息的结果关联到对应的 tool_call
          const toolResultMap = new Map<string, string>()
          let pendingSemanticConfigSelection: SemanticConfigSelectionInfo | null = null
          formattedMessages.forEach((msg) => {
            if (msg.role === 'user') {
              pendingSemanticConfigSelection = msg.semanticConfigSelection || null
              msg.semanticConfigSelection = undefined
              return
            }
            if (msg.role === 'assistant' && pendingSemanticConfigSelection) {
              applySemanticConfigSelection(msg, pendingSemanticConfigSelection)
              pendingSemanticConfigSelection = null
            }
            if (msg.role === 'assistant') {
            }
            if (msg.role === 'tool' && msg.toolCallId && msg.content) {
              toolResultMap.set(msg.toolCallId, msg.content)
            }
          })

          // 更新每个 assistant 消息中的 toolCalls
          formattedMessages.forEach((msg) => {
            if (msg.role === 'assistant' && msg.toolCalls) {
              msg.toolCalls = msg.toolCalls.map((tc) => {
                const result = toolResultMap.get(tc.tool_call_id)
                if (!result) return tc

                let renderId: string | undefined
                let renderType: 'queryResult' | 'chart' | undefined
                let semanticParams: SemanticParams | undefined

                if (tc.tool_name === 'semantic_query_tool') {
                  const queryResultData = getRenderableQueryResultData(result)
                  if (queryResultData) {
                    renderId = queryResultData
                    renderType = 'queryResult'
                  }
                  semanticParams = extractSemanticParamsFromArgs(tc.args)
                } else if (tc.tool_name === 'generate_chart_tool') {
                  const chartMatch = result.match(/<chart_config>([^<]+)<\/chart_config>/)
                  if (chartMatch?.[1]) {
                    const id = chartMatch[1].trim()
                    if (/^[a-f0-9-]{36}$/.test(id)) {
                      renderId = id
                      renderType = 'chart'
                    }
                  }
                }

                return {
                  ...tc,
                  result,
                  status: 'completed' as const,
                  renderId,
                  renderType,
                  semanticParams,
                }
              })
            }
          })

          // 过滤掉 tool 类型的消息（它们的结果已经关联到 assistant 的 toolCalls 中了）
          const nonToolMessages = formattedMessages.filter((msg) => msg.role !== 'tool')

          // 合并同一轮对话中的 assistant 消息，并按历史消息顺序重建 segments。
          // 不能只合并 content/toolCalls，否则工具会全部堆到气泡末尾。
          const displayMessages: Message[] = []
          for (const msg of nonToolMessages) {
            if (msg.role === 'assistant') {
              const lastMsg = displayMessages[displayMessages.length - 1]
              if (lastMsg && lastMsg.role === 'assistant') {
                appendAssistantHistoryMessage(lastMsg, msg)
              } else {
                displayMessages.push({
                  ...msg,
                  segments: buildAssistantHistorySegments(msg),
                })
              }
            } else {
              displayMessages.push(msg)
            }
          }

          // 合并后只使用 semantic_query_tool 自身 DSL 参数重建展示信息。
          displayMessages.forEach((msg) => {
            if (msg.role === 'assistant' && msg.toolCalls && msg.toolCalls.length > 0) {
              msg.toolCalls = msg.toolCalls.map((tc) => {
                if (tc.tool_name === 'semantic_query_tool') {
                  const semanticParams = extractSemanticParamsFromArgs(tc.args)
                  if (semanticParams) {
                    return {
                      ...tc,
                      semanticParams,
                    }
                  }
                }
                return tc
              })
            }
          })

          applyReasoningSegmentsToHistoryMessages(displayMessages, historyReasoningSegments)

          messages.value = displayMessages
          isFirstMessage.value = false
        }
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
