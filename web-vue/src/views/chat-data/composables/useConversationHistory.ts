/**
 * 会话历史加载 composable
 * 用于加载和恢复会话历史消息（参考 React 版本）
 */
import { ref, watch, type Ref } from 'vue'
import { getConversationHistory } from '../api'
import type { ToolCallInfo, Message, MessageSegment, PendingTool, SemanticConfigSelectionInfo, SemanticConfigSelectRequiredInfo, SemanticParams, TaskStageInfo, ThinkingSegment } from '../types'

const HIDDEN_TOOLS = ['load_skill_tool']

const buildAssistantHistorySegments = (message: Message, stageOffset = 0): MessageSegment[] => {
  const segments: MessageSegment[] = []
  for (const [_index, stage] of (message.taskStages || []).entries()) {
    void stage
    segments.push({ type: 'taskStage', stageIndex: stageOffset + _index })
  }
  const visibleContent = stripThinkingContent(message.content)
  if (visibleContent) {
    segments.push({ type: 'text', content: visibleContent })
  }
  for (const toolCall of message.toolCalls || []) {
    segments.push({ type: 'toolCall', toolCallId: toolCall.tool_call_id })
  }
  return segments
}

const appendThinkingSegmentsToContent = (segments: ThinkingSegment[], visibleContent: string): string => {
  const thinkingContent = segments.map(segment => `<thinking>${segment.content}</thinking>`).join('')
  return visibleContent ? `${thinkingContent}${visibleContent}` : thinkingContent
}

const extractThinkingText = (content: string): string[] => {
  const result: string[] = []
  const regex = /<thinking>([\s\S]*?)<\/thinking>/g
  let match: RegExpExecArray | null
  while ((match = regex.exec(content || '')) !== null) {
    result.push(match[1] || '')
  }
  return result
}

const stripThinkingContent = (content: string): string => {
  return String(content || '').replace(/<thinking>[\s\S]*?<\/thinking>/g, '').trim()
}

const appendAssistantHistoryMessage = (target: Message, source: Message) => {
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
    target.content = appendThinkingSegmentsToContent(target.thinkingSegments, stripThinkingContent(target.content || ''))
  }
  if (source.toolCalls?.length) {
    target.toolCalls = [...(target.toolCalls || []), ...source.toolCalls]
  }
  if (source.taskStages?.length) {
    target.taskStages = [...(target.taskStages || []), ...source.taskStages]
  }
  if (source.semanticConfigSelection) {
    target.semanticConfigSelection = source.semanticConfigSelection
    target.semanticConfigSelectRequired = undefined
  }
  if (source.semanticConfigSelectRequired && !target.semanticConfigSelection) {
    target.semanticConfigSelectRequired = source.semanticConfigSelectRequired
  }
  target.segments = [
    ...(target.segments || []),
    ...buildAssistantHistorySegments(source, (target.taskStages?.length || 0) - (source.taskStages?.length || 0)),
  ]
}

const applyTaskStagesToHistoryMessage = (message: Message, taskStages: TaskStageInfo[]) => {
  if (taskStages.length === 0) return
  const existingStages = message.taskStages || []
  const stageOffset = existingStages.length
  message.taskStages = [...existingStages, ...taskStages]
  const segments = message.segments || []
  const beforeExecuteDetails: MessageSegment[] = []
  const afterExecuteDetails: MessageSegment[] = []
  taskStages.forEach((stage, index) => {
    const shiftedSegment: MessageSegment = { type: 'taskStage', stageIndex: stageOffset + index }
    if (stage.stage === 'done') afterExecuteDetails.push(shiftedSegment)
    else beforeExecuteDetails.push(shiftedSegment)
  })
  message.segments = [
    ...beforeExecuteDetails,
    ...segments,
    ...afterExecuteDetails,
  ]
}

const normalizeTaskStages = (value: unknown): TaskStageInfo[] => {
  if (!Array.isArray(value)) return []
  const allowedStages = new Set(['thinking', 'plan', 'execute', 'done'])
  const allowedStatuses = new Set(['running', 'completed', 'error'])
  const result: TaskStageInfo[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const data = item as Record<string, unknown>
    const stage = String(data.stage || '').trim()
    const status = String(data.status || '').trim()
    if (!allowedStages.has(stage) || !allowedStatuses.has(status)) continue
    const steps = Array.isArray(data.steps)
      ? data.steps.map(step => String(step || '').trim()).filter(Boolean)
      : undefined
    result.push({
      stage: stage as TaskStageInfo['stage'],
      status: status as TaskStageInfo['status'],
      title: typeof data.title === 'string' ? data.title : undefined,
      content: typeof data.content === 'string' ? data.content : undefined,
      steps,
      source: data.source === 'reasoning' ? 'reasoning' : 'task_stage',
    })
  }
  return result
}

const normalizeReasoningContent = (value: unknown): string => {
  return typeof value === 'string' ? value : ''
}

const normalizeReasoningSegments = (value: unknown): ThinkingSegment[] => {
  if (!Array.isArray(value)) return []
  const result: ThinkingSegment[] = []
  value.forEach((item, index) => {
    if (!item || typeof item !== 'object') return
    const data = item as Record<string, unknown>
    const content = typeof data.content === 'string' ? data.content : ''
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
  message.taskStages = [
    ...(message.taskStages || []),
    ...thinkingSegments.map((segment): TaskStageInfo => ({
      stage: 'thinking',
      status: 'completed',
      title: '思考',
      content: segment.content,
      source: 'reasoning',
    })),
  ]
  message.content = appendThinkingSegmentsToContent(thinkingSegments, stripThinkingContent(message.content || ''))
  message.segments = buildAssistantHistorySegments(message)
}

const setThinkingSegmentsOnHistoryMessage = (message: Message, thinkingSegments: ThinkingSegment[]) => {
  if (message.role !== 'assistant' || thinkingSegments.length === 0) return
  message.thinkingSegments = thinkingSegments
  const nonReasoningStages = (message.taskStages || []).filter(stage => stage.source !== 'reasoning')
  const reasoningStages = thinkingSegments.map((segment): TaskStageInfo => ({
    stage: 'thinking',
    status: 'completed',
    title: '思考',
    content: segment.content,
    source: 'reasoning',
  }))
  message.taskStages = [...reasoningStages, ...nonReasoningStages]
  message.content = appendThinkingSegmentsToContent(thinkingSegments, stripThinkingContent(message.content || ''))
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

const parseSemanticConfigToolPayload = (content: string): Record<string, unknown> | null => {
  const text = String(content || '').trim()
  if (!text.startsWith('[select_semantic_config_tool]')) return null
  const raw = text.replace('[select_semantic_config_tool]', '').trim()
  if (!raw) return null
  try {
    const payload = JSON.parse(raw)
    return payload && typeof payload === 'object' ? payload as Record<string, unknown> : null
  } catch {
    return null
  }
}

const normalizeSemanticConfigSelectionFromTool = (payload: Record<string, unknown>): SemanticConfigSelectionInfo | null => {
  if (!payload.selected) return null
  const semanticConfigId = String(payload.selected_config_id || '').trim()
  if (!semanticConfigId) return null
  return normalizeSemanticConfigSelection({
    semantic_config_id: semanticConfigId,
    semantic_config_name: payload.selected_config_name || semanticConfigId,
    confidence: payload.confidence || '',
    reason: payload.reason || payload.message || '',
    matched_metrics: payload.matched_metrics,
    matched_dimensions: payload.matched_dimensions,
    auto_selected: true,
    user_selected: false,
  })
}

const normalizeSemanticConfigSelectRequiredFromTool = (payload: Record<string, unknown>, query: string): SemanticConfigSelectRequiredInfo | null => {
  if (payload.selected) return null
  const rawCandidates = Array.isArray(payload.candidates) ? payload.candidates : []
  type Candidate = SemanticConfigSelectRequiredInfo['candidates'][number]
  const candidates = rawCandidates
    .map((item): Candidate | null => {
      if (!item || typeof item !== 'object') return null
      const data = item as Record<string, unknown>
      const id = String(data.id || '').trim()
      if (!id) return null
      return {
        id,
        name: typeof data.name === 'string' ? data.name : id,
        coverage: typeof data.coverage === 'string' ? data.coverage : '',
        confidence: typeof data.confidence === 'string' ? data.confidence : '',
        reason: typeof data.reason === 'string' ? data.reason : '',
      }
    })
    .filter((item): item is Candidate => item !== null)
  if (candidates.length === 0) return null
  const message = String(payload.message || '该问题可由多个指标集回答，请选择一个指标集后继续查询').trim()
  const reason = String(payload.reason || '').trim()
  return {
    query,
    message,
    reason,
    ambiguous: Boolean(payload.ambiguous),
    cross_config_required: Boolean(payload.cross_config_required),
    candidates,
  }
}

const applySemanticConfigSelection = (message: Message, selection: SemanticConfigSelectionInfo) => {
  if (message.semanticConfigSelection?.user_selected && !selection.user_selected) return
  message.semanticConfigSelection = selection
  message.semanticConfigSelectRequired = undefined
}

const applySemanticConfigSelectRequired = (message: Message, selectRequired: SemanticConfigSelectRequiredInfo) => {
  if (message.semanticConfigSelection) return
  message.semanticConfigSelectRequired = selectRequired
}

interface UseConversationHistoryOptions {
  conversationId: Ref<string | null | undefined>
  messages: Ref<Message[]>
  setActiveMessageId?: (id: string | null) => void
  setPendingTool?: (tool: PendingTool | null) => void
}

export function useConversationHistory({
  conversationId,
  messages,
  setActiveMessageId,
  setPendingTool,
}: UseConversationHistoryOptions) {
  const isLoadingHistory = ref(false)
  const loadedConversationId = ref<string | null>(null)
  const hasPendingTool = ref(false)
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
        const historyPendingTool = data.data?.pending_tool
        const historyTaskStages = normalizeTaskStages(data.data?.task_stages)
        const historyReasoningSegments = normalizeReasoningSegments(data.data?.reasoning_segments)

        if (historyMessages.length > 0) {
          const formattedMessages: Message[] = historyMessages.map((msg: any) => {
            // 解析时间戳
            let timestamp: Date
            if (msg.timestamp && typeof msg.timestamp === 'string' && msg.timestamp.trim()) {
              timestamp = new Date(msg.timestamp.replace(' ', 'T'))
              if (isNaN(timestamp.getTime())) {
                timestamp = new Date()
              }
            } else {
              timestamp = new Date()
            }

            const message: Message = {
              id: msg.id,
              role: msg.role,
              content: stripThinkingContent(msg.content || ''),
              timestamp,
            }

            const existingThinkingTexts = extractThinkingText(msg.content || '')
            const reasoningContent = normalizeReasoningContent(msg.reasoning_content)
            const thinkingTexts = [...existingThinkingTexts]
            if (reasoningContent) thinkingTexts.push(reasoningContent)
            if (thinkingTexts.length > 0 && msg.role === 'assistant') {
              message.thinkingSegments = thinkingTexts.map((content, index) => ({
                id: `thinking-history-${msg.id || 'message'}-${index}`,
                content,
                toolCallCount: index,
              }))
              message.taskStages = thinkingTexts.map((content): TaskStageInfo => ({
                stage: 'thinking',
                status: 'completed',
                title: '思考',
                content,
                source: 'reasoning',
              }))
              message.content = appendThinkingSegmentsToContent(message.thinkingSegments, message.content)
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
          let pendingToolSemanticConfigSelection: SemanticConfigSelectionInfo | null = null
          let pendingToolSemanticConfigSelectRequired: SemanticConfigSelectRequiredInfo | null = null
          let latestAssistantBeforeTool: Message | null = null
          let latestUserQuery = ''
          formattedMessages.forEach((msg) => {
            if (msg.role === 'user') {
              latestUserQuery = msg.content || ''
              pendingSemanticConfigSelection = msg.semanticConfigSelection || null
              msg.semanticConfigSelection = undefined
              return
            }
            if (msg.role === 'assistant' && pendingSemanticConfigSelection) {
              applySemanticConfigSelection(msg, pendingSemanticConfigSelection)
              pendingSemanticConfigSelection = null
            }
            if (msg.role === 'assistant') {
              latestAssistantBeforeTool = msg
              if (pendingToolSemanticConfigSelection) {
                applySemanticConfigSelection(msg, pendingToolSemanticConfigSelection)
                pendingToolSemanticConfigSelection = null
              }
              if (pendingToolSemanticConfigSelectRequired) {
                applySemanticConfigSelectRequired(msg, pendingToolSemanticConfigSelectRequired)
                pendingToolSemanticConfigSelectRequired = null
              }
            }
            if (msg.role === 'tool' && msg.toolCallId && msg.content) {
              toolResultMap.set(msg.toolCallId, msg.content)
              if (msg.toolName === 'select_semantic_config_tool') {
                const payload = parseSemanticConfigToolPayload(msg.content)
                if (payload) {
                  const selection = normalizeSemanticConfigSelectionFromTool(payload)
                  const selectRequired = selection ? null : normalizeSemanticConfigSelectRequiredFromTool(payload, latestUserQuery)
                  if (selection) {
                    if (latestAssistantBeforeTool) applySemanticConfigSelection(latestAssistantBeforeTool, selection)
                    else pendingToolSemanticConfigSelection = selection
                  } else if (selectRequired) {
                    if (latestAssistantBeforeTool) applySemanticConfigSelectRequired(latestAssistantBeforeTool, selectRequired)
                    else pendingToolSemanticConfigSelectRequired = selectRequired
                  }
                }
              }
            }
          })

          // 更新每个 assistant 消息中的 toolCalls
          formattedMessages.forEach((msg) => {
            if (msg.role === 'assistant' && msg.toolCalls) {
              // 先收集 dsl2sql_tool 的语义参数
              const dsl2sqlTools: Array<{ semanticParams?: SemanticParams }> = []
              msg.toolCalls.forEach((tc) => {
                if (tc.tool_name === 'dsl2sql_tool' && tc.args) {
                  dsl2sqlTools.push({
                    semanticParams: {
                      metrics: tc.args.metrics as string[] | undefined,
                      dimensions: tc.args.dimensions as SemanticParams['dimensions'],
                      filters: tc.args.filters as SemanticParams['filters'],
                      having: tc.args.having as SemanticParams['having'],
                      order_by: tc.args.order_by as SemanticParams['order_by'],
                      limit: tc.args.limit as number | undefined,
                      offset: tc.args.offset as number | undefined,
                    },
                  })
                }
              })

              // 用于追踪 execute_sql_tool 索引（与 dsl2sql_tool 对应）
              let executeSqlIndex = 0

              msg.toolCalls = msg.toolCalls.map((tc) => {
                const result = toolResultMap.get(tc.tool_call_id)
                if (!result) return tc

                let renderId: string | undefined
                let renderType: 'queryResult' | 'chart' | undefined
                let semanticParams: SemanticParams | undefined

                if (tc.tool_name === 'execute_sql_tool') {
                  const queryResultMatch = result.match(/<query_result>([\s\S]*?)<\/query_result>/)
                  if (queryResultMatch?.[1]) {
                    renderId = queryResultMatch[1].trim()
                    renderType = 'queryResult'
                  }
                  // 从对应的 dsl2sql_tool 获取语义参数
                  if (executeSqlIndex < dsl2sqlTools.length) {
                    semanticParams = dsl2sqlTools[executeSqlIndex]?.semanticParams
                  }
                  executeSqlIndex++
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

          // 合并后重新关联 dsl2sql_tool 和 execute_sql_tool 的语义参数
          // 因为它们原本在不同的 assistant 消息中，合并前无法正确关联
          displayMessages.forEach((msg) => {
            if (msg.role === 'assistant' && msg.toolCalls && msg.toolCalls.length > 0) {
              // 收集合并后消息中所有 dsl2sql_tool 的参数
              const dsl2sqlTools: Array<{ semanticParams?: SemanticParams }> = []
              msg.toolCalls.forEach((tc) => {
                if (tc.tool_name === 'dsl2sql_tool' && tc.args) {
                  dsl2sqlTools.push({
                    semanticParams: {
                      metrics: tc.args.metrics as string[] | undefined,
                      dimensions: tc.args.dimensions as SemanticParams['dimensions'],
                      filters: tc.args.filters as SemanticParams['filters'],
                      having: tc.args.having as SemanticParams['having'],
                      order_by: tc.args.order_by as SemanticParams['order_by'],
                      limit: tc.args.limit as number | undefined,
                      offset: tc.args.offset as number | undefined,
                    },
                  })
                }
              })

              // 重新关联 execute_sql_tool 的语义参数
              if (dsl2sqlTools.length > 0) {
                let executeSqlIndex = 0
                msg.toolCalls = msg.toolCalls.map((tc) => {
                  if (tc.tool_name === 'execute_sql_tool') {
                    const dslTool = dsl2sqlTools[executeSqlIndex]
                    executeSqlIndex++
                    if (dslTool) {
                      return {
                        ...tc,
                        semanticParams: dslTool.semanticParams,
                      }
                    }
                  }
                  return tc
                })
              }
            }
          })

          applyReasoningSegmentsToHistoryMessages(displayMessages, historyReasoningSegments)
          const latestAssistantMessage = [...displayMessages].reverse().find(msg => msg.role === 'assistant')
          if (latestAssistantMessage) applyTaskStagesToHistoryMessage(latestAssistantMessage, historyTaskStages)

          messages.value = displayMessages
          isFirstMessage.value = false

          // 如果有待确认的工具，恢复状态
          if (historyPendingTool && setPendingTool && setActiveMessageId) {
            const lastAssistantMsg = displayMessages[displayMessages.length - 1]
            if (lastAssistantMsg && lastAssistantMsg.role === 'assistant') {
              setActiveMessageId(lastAssistantMsg.id)
              setPendingTool({
                tool_call_id: historyPendingTool.tool_call_id || '',
                tool_name: historyPendingTool.tool_name || 'execute_sql_tool',
                args: historyPendingTool.args || {},
                message: historyPendingTool.message,
              })
              hasPendingTool.value = true
            }
          }
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
    hasPendingTool.value = false
  }

  return {
    isLoadingHistory,
    loadedConversationId,
    hasPendingTool,
    isFirstMessage,
    loadHistory,
    resetHistory,
  }
}

export default useConversationHistory
