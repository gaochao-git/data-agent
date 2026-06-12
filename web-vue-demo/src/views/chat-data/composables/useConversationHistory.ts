/**
 * 会话历史加载 composable
 * 用于加载和恢复会话历史消息（参考 React 版本）
 */
import { ref, watch, type Ref } from 'vue'
import { getConversationHistory } from '../api'
import type { ToolCallInfo, Message, PendingTool, SemanticParams, VisualizationConfig } from '../types'

// 隐藏的工具
const HIDDEN_TOOLS = ['load_skill_tool']

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
          formattedMessages.forEach((msg) => {
            if (msg.role === 'tool' && msg.toolCallId && msg.content) {
              toolResultMap.set(msg.toolCallId, msg.content)
            }
          })

          // 更新每个 assistant 消息中的 toolCalls
          formattedMessages.forEach((msg) => {
            if (msg.role === 'assistant' && msg.toolCalls) {
              // 先收集 dsl2sql_tool 的语义参数和 visualization
              const dsl2sqlTools: Array<{ semanticParams?: SemanticParams; visualization?: VisualizationConfig }> = []
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
                    },
                    visualization: tc.args.visualization as VisualizationConfig | undefined,
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
                let visualization: VisualizationConfig | undefined

                if (tc.tool_name === 'execute_sql_tool') {
                  const queryResultMatch = result.match(/<query_result>([\s\S]*?)<\/query_result>/)
                  if (queryResultMatch) {
                    renderId = queryResultMatch[1].trim()
                    renderType = 'queryResult'
                  }
                  // 从对应的 dsl2sql_tool 获取语义参数和 visualization
                  if (executeSqlIndex < dsl2sqlTools.length) {
                    semanticParams = dsl2sqlTools[executeSqlIndex]?.semanticParams
                    visualization = dsl2sqlTools[executeSqlIndex]?.visualization
                  }
                  executeSqlIndex++
                } else if (tc.tool_name === 'generate_chart_tool') {
                  const chartMatch = result.match(/<chart_config>([^<]+)<\/chart_config>/)
                  if (chartMatch) {
                    const id = chartMatch[1].trim()
                    if (/^[a-f0-9-]{36}$/.test(id)) {
                      renderId = id
                      renderType = 'chart'
                    }
                  }
                } else if (tc.tool_name === 'dsl2sql_tool') {
                  // dsl2sql_tool 也保存自己的 visualization
                  visualization = tc.args?.visualization as VisualizationConfig | undefined
                }

                return {
                  ...tc,
                  result,
                  status: 'completed' as const,
                  renderId,
                  renderType,
                  semanticParams,
                  visualization,
                }
              })
            }
          })

          // 过滤掉 tool 类型的消息（它们的结果已经关联到 assistant 的 toolCalls 中了）
          const nonToolMessages = formattedMessages.filter((msg) => msg.role !== 'tool')

          // 合并连续的 assistant 消息（同一轮对话中的多个 assistant 消息合并为一个）
          // 这样历史渲染和实时流式渲染保持一致
          const displayMessages: Message[] = []
          for (const msg of nonToolMessages) {
            if (msg.role === 'assistant') {
              // 查找前一个消息
              const lastMsg = displayMessages[displayMessages.length - 1]
              if (lastMsg && lastMsg.role === 'assistant') {
                // 合并到前一个 assistant 消息
                // 合并内容（如果有）
                if (msg.content) {
                  lastMsg.content = (lastMsg.content || '') + (lastMsg.content ? '\n' : '') + msg.content
                }
                // 合并工具调用
                if (msg.toolCalls && msg.toolCalls.length > 0) {
                  lastMsg.toolCalls = [...(lastMsg.toolCalls || []), ...msg.toolCalls]
                }
              } else {
                // 新的 assistant 消息
                displayMessages.push(msg)
              }
            } else {
              // user 消息直接添加
              displayMessages.push(msg)
            }
          }

          // 合并后重新关联 dsl2sql_tool 和 execute_sql_tool 的 visualization
          // 因为它们原本在不同的 assistant 消息中，合并前无法正确关联
          displayMessages.forEach((msg) => {
            if (msg.role === 'assistant' && msg.toolCalls && msg.toolCalls.length > 0) {
              // 收集合并后消息中所有 dsl2sql_tool 的参数
              const dsl2sqlTools: Array<{ semanticParams?: SemanticParams; visualization?: VisualizationConfig }> = []
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
                    },
                    visualization: tc.args.visualization as VisualizationConfig | undefined,
                  })
                }
              })

              // 重新关联 execute_sql_tool 的 visualization
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
                        visualization: dslTool.visualization,
                      }
                    }
                  }
                  return tc
                })
              }
            }
          })

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
