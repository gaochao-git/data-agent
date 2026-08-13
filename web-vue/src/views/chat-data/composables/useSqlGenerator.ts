/**
 * SQL 生成器 Composable
 * 处理自然语言到 SQL 的转换，支持 SSE 流式响应
 */

import { ref } from 'vue'
import { streamCompletion } from '../api'
import type {
  NL2SQLResult,
  LangGraphMessage,
  SSEEventType,
  DashboardSpecPayload,
  ChatDataTokenUsage,
  ChatDataContextCompressionEvent,
  ChatDataMessageEndPayload,
  SemanticConfigSelectionInfo,
  ToolLifecycleEventData,
} from '../types'

/**
 * 解析 SSE 事件流
 */
function parseSSEEvents(buffer: string): {
  events: Array<{ event: SSEEventType; data: string }>
  remaining: string
} {
  const events: Array<{ event: SSEEventType; data: string }> = []
  const blocks = buffer.split('\n\n')
  const remaining = blocks.pop() || ''

  for (const block of blocks) {
    if (!block.trim()) continue

    const lines = block.split('\n')
    let eventType: SSEEventType = 'message'
    let dataStr = ''

    for (const line of lines) {
      if (line.startsWith('event:')) {
        eventType = line.substring(6).trim() as SSEEventType
      } else if (line.startsWith('data:')) {
        dataStr = line.substring(5).trim()
      }
    }

    if (dataStr) {
      events.push({ event: eventType, data: dataStr })
    }
  }

  return { events, remaining }
}

export function useSqlGenerator() {
  const loading = ref(false)
  const error = ref<string | null>(null)
  let abortController: AbortController | null = null

  /**
   * 停止当前生成
   */
  const stopGeneration = () => {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
  }

  /**
   * 流式生成 SQL（SSE）
   */
  const generateSqlStream = async (
    query: string,
    returnMetadata: boolean,
    datasourceId: string | null,
    conversationId: string | null | undefined,
    mode: 'generate' | 'query' = 'query',
    onMessage?: (message: LangGraphMessage) => void,
    onResult?: (result: NL2SQLResult) => void,
    onStatus?: (status: string, message: string) => void,
    onError?: (error: string) => void,
    onRunningTool?: (toolInfo: { tool_name: string; tool_call_id: string; args?: Record<string, unknown> }) => void,
    semanticConfigId?: string | null,
    modelId?: string | null,
    memberDatasourceIds?: string[],
    agentId: string = 'chat_data',
    skillName?: string | null,
    onDashboardSpec?: (payload: DashboardSpecPayload) => void,
    onMessageEnd?: (payload: ChatDataMessageEndPayload) => void,
    onTokenUsage?: (payload: ChatDataTokenUsage) => void,
    semanticConfigSelection?: SemanticConfigSelectionInfo | null,
    enableThinking?: boolean,
    runtimeOptions?: { workspace_backend: 'local' | 'sandbox'; bash_enabled: boolean } | null,
    onContextCompression?: (payload: ChatDataContextCompressionEvent) => void,
    fileIds?: string[],
    onToolLifecycle?: (payload: ToolLifecycleEventData) => void,
  ): Promise<void> => {
    if (!conversationId) {
      onError?.('会话 ID 不能为空')
      return
    }

    loading.value = true
    error.value = null
    const currentController = new AbortController()
    abortController = currentController

    try {
      // 使用与 React 版本一致的请求结构
      const options: Record<string, unknown> = {
        datasource_id: datasourceId,
        mode,  // generate: 仅生成SQL, query: 生成并执行
        enable_thinking: enableThinking === true,
      }

      // 仅当服务端允许用户覆盖时，runtime_options 才会改变实际运行配置。
      if (runtimeOptions && typeof runtimeOptions === 'object') {
        options.runtime_options = {
          workspace_backend: runtimeOptions.workspace_backend,
          bash_enabled: runtimeOptions.bash_enabled,
        }
      }

      // 模型选择
      if (modelId) {
        options.model_id = modelId
      }

      // 传入明确的语义配置 ID 时，使用语义模式
      if (semanticConfigId !== null && semanticConfigId !== undefined) {
        options.semantic_config_id = semanticConfigId
      }
      if (semanticConfigSelection?.semantic_config_id) {
        options.semantic_config_selection = semanticConfigSelection
      }

      if (memberDatasourceIds && memberDatasourceIds.length > 0) {
        options.member_datasource_ids = memberDatasourceIds
      }
      if (skillName) {
        options.skill_names = [skillName]
      }

      const response = await streamCompletion(conversationId, {
        query,
        agent_id: agentId,
        options,
        file_ids: fileIds,
      }, currentController.signal)

      if (!response.ok) {
        // 尝试解析错误响应以获取更详细的错误信息
        let errorMsg = `HTTP ${response.status}`
        try {
          const errorData = await response.json()
          errorMsg = errorData.message || errorData.msg || errorData.detail || errorMsg
        } catch {
          // 忽略解析错误
        }
        throw new Error(errorMsg)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('无法获取响应流')
      }

      const decoder = new TextDecoder()
      let buffer = ''
      let streamEnded = false
      const lifecycleToolCallIds = new Set<string>()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const { events, remaining } = parseSSEEvents(buffer)
        buffer = remaining

        for (const { event, data: dataStr } of events) {
          // 处理结束事件（与 React 版本一致）
          if (event === 'done' || dataStr === '[DONE]') {
            streamEnded = true
            break
          }

          // 处理错误事件
          if (event === 'error') {
            let errorMsg = dataStr
            try {
              const errorData = JSON.parse(dataStr)
              errorMsg = errorData.error || errorData.message || errorMsg
            } catch {
              // 保留原始文本。
            }
            throw new Error(errorMsg)
          }

          if (event === 'message_end') {
            try {
              const data = JSON.parse(dataStr)
              const usage = data?.usage
              onMessageEnd?.({
                usage: usage && typeof usage === 'object' ? usage as ChatDataTokenUsage : null,
                timestamp: typeof data?.timestamp === 'string' ? data.timestamp : undefined,
              })
            } catch (err) {
              console.error('解析 message_end 数据失败:', err, dataStr)
              onMessageEnd?.({})
            }
            continue
          }

          if (event === 'token_usage') {
            try {
              const data = JSON.parse(dataStr)
              if (data && typeof data === 'object') {
                onTokenUsage?.(data as ChatDataTokenUsage)
              }
            } catch (err) {
              console.error('解析 token_usage 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'context_compression') {
            try {
              const data = JSON.parse(dataStr)
              if (data && typeof data === 'object' && typeof data.id === 'string') {
                onContextCompression?.(data as ChatDataContextCompressionEvent)
              }
            } catch (err) {
              console.error('解析 context_compression 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'thinking') {
            try {
              const data = JSON.parse(dataStr)
              const content = typeof data?.content === 'string' ? data.content : ''
              if (content) {
                onMessage?.({
                  type: 'thinking',
                  content,
                  timestamp: data?.timestamp,
                  additional_kwargs: { reasoning_content: content },
                } as LangGraphMessage)
              }
            } catch (err) {
              console.error('解析 thinking 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'tool_started' || event === 'tool_progress' || event === 'tool_completed' || event === 'tool_failed') {
            try {
              const data = JSON.parse(dataStr) as ToolLifecycleEventData
              if (data.tool_call_id) lifecycleToolCallIds.add(data.tool_call_id)
              onToolLifecycle?.({ ...data, type: event })
            } catch (err) {
              console.error(`解析 ${event} 数据失败:`, err, dataStr)
            }
            continue
          }

          // 处理 tool 事件（根据 status 区分）
          if (event === 'tool') {
            try {
              const data = JSON.parse(dataStr)
              if (data.tool_call_id && lifecycleToolCallIds.has(data.tool_call_id)) {
                continue
              }
              // 根据 status 字段分发处理
              if (data.status === 'running') {
                // 工具开始执行
                onRunningTool?.({
                  tool_name: data.tool_name,
                  tool_call_id: data.tool_call_id || '',
                  args: data.args || {},
                })
              } else {
                // completed/success 状态仍然是工具结果，显式标记为 tool，避免进入助手正文。
                onMessage?.({ ...data, type: 'tool' } as LangGraphMessage)
              }
            } catch (err) {
              console.error('解析 tool 数据失败:', err, dataStr)
            }
            continue
          }

          // 处理 message 事件
          if (event === 'message') {
            try {
              const data = JSON.parse(dataStr)
              if (data.error) {
                throw new Error(data.error)
              }
              onMessage?.(data as LangGraphMessage)
            } catch (err) {
              if (err instanceof Error && err.message !== dataStr) {
                throw err
              }
              console.error('解析 SSE 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'dashboard_spec') {
            try {
              const data = JSON.parse(dataStr)
              onDashboardSpec?.(data as DashboardSpecPayload)
            } catch (err) {
              console.error('解析 dashboard_spec 数据失败:', err, dataStr)
            }
          }
        }

        if (streamEnded) break
      }

      // 流结束，调用 onResult 通知完成
      onResult?.({} as NL2SQLResult)
    } catch (err) {
      // 用户主动取消不算错误
      if (err instanceof DOMException && err.name === 'AbortError') {
        onResult?.({} as NL2SQLResult)
        return
      }
      const errorMsg = err instanceof Error ? err.message : '请求失败'
      error.value = errorMsg
      onError?.(errorMsg)
    } finally {
      loading.value = false
      // 只清理自己的 controller，避免覆盖新请求创建的 controller
      if (abortController === currentController) {
        abortController = null
      }
    }
  }

  return {
    loading,
    error,
    generateSqlStream,
    stopGeneration,
  }
}
