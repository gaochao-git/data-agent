/**
 * 会话历史加载 composable
 * 用于加载和恢复会话历史消息（参考 React 版本）
 */
import { ref, watch, type Ref } from 'vue'
import { getConversationHistory } from '../api'
import type { ToolCallInfo, Message, MessageSegment, PendingTool, SemanticConfigSelectionInfo, SemanticConfigSelectRequiredInfo, SemanticParams, TaskStageInfo } from '../types'

const HIDDEN_TOOLS = ['load_skill_tool']

const buildAssistantHistorySegments = (message: Message): MessageSegment[] => {
  const segments: MessageSegment[] = []
  if (message.content) {
    segments.push({ type: 'text', content: message.content })
  }
  for (const toolCall of message.toolCalls || []) {
    segments.push({ type: 'toolCall', toolCallId: toolCall.tool_call_id })
  }
  return segments
}

const appendAssistantHistoryMessage = (target: Message, source: Message) => {
  if (source.content) {
    target.content = (target.content || '') + (target.content ? '\n' : '') + source.content
  }
  if (source.toolCalls?.length) {
    target.toolCalls = [...(target.toolCalls || []), ...source.toolCalls]
  }
  if (source.taskStages?.length) {
    target.taskStages = source.taskStages
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
    ...buildAssistantHistorySegments(source),
  ]
}

const applyTaskStagesToHistoryMessage = (message: Message, taskStages: TaskStageInfo[]) => {
  if (taskStages.length === 0) return
  message.taskStages = taskStages
  const segments = message.segments || []
  const beforeExecuteDetails: MessageSegment[] = []
  const afterExecuteDetails: MessageSegment[] = []
  taskStages.forEach((stage, index) => {
    const segment: MessageSegment = { type: 'taskStage', stageIndex: index }
    if (stage.stage === 'done') afterExecuteDetails.push(segment)
    else beforeExecuteDetails.push(segment)
  })
  message.segments = [
    ...beforeExecuteDetails,
    ...segments.filter(segment => segment.type !== 'taskStage'),
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
    })
  }
  return result
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
              content: msg.content,
              timestamp,
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

          const latestAssistantMessage = [...displayMessages].reverse().find(msg => msg.role === 'assistant')
          if (latestAssistantMessage) {
            applyTaskStagesToHistoryMessage(latestAssistantMessage, historyTaskStages)
          }

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
