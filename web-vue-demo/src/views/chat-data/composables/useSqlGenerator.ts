/**
 * SQL 生成器 Composable
 * 处理自然语言到 SQL 的转换，支持 SSE 流式响应
 */

import { ref } from 'vue'
import { streamCompletion, streamResume } from '../api'
import type {
  NL2SQLResult,
  PendingTool,
  LangGraphMessage,
  SSEEventType,
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
    onPendingTool?: (pendingTool: PendingTool) => void,
    onRunningTool?: (toolInfo: { tool_name: string; tool_call_id: string }) => void,
    semanticConfigId?: string | null,
    modelId?: string | null,
    memberDatasourceIds?: string[]
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
      }

      // 模型选择
      if (modelId) {
        options.model_id = modelId
      }

      // 传入明确的语义配置 ID 时，使用语义模式
      if (semanticConfigId !== null && semanticConfigId !== undefined) {
        options.semantic_config_id = semanticConfigId
      }

      if (memberDatasourceIds && memberDatasourceIds.length > 0) {
        options.member_datasource_ids = memberDatasourceIds
      }

      const response = await streamCompletion(conversationId, {
        query,
        agent_id: 'chat_data',
        options,
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
            try {
              const errorData = JSON.parse(dataStr)
              throw new Error(errorData.error || '未知错误')
            } catch {
              throw new Error(dataStr)
            }
          }

          // 处理 tool 事件（根据 status 区分）
          if (event === 'tool') {
            try {
              const data = JSON.parse(dataStr)
              // 根据 status 字段分发处理
              if (data.status === 'pending') {
                // 待确认的工具调用
                onPendingTool?.({
                  tool_call_id: data.tool_call_id || '',
                  tool_name: data.tool_name,
                  args: data.args || {},
                  message: data.message,
                })
              } else if (data.status === 'running') {
                // 工具开始执行
                onRunningTool?.({
                  tool_name: data.tool_name,
                  tool_call_id: data.tool_call_id || '',
                })
              } else {
                // completed/success 状态，作为普通消息处理（与 React 版本一致）
                onMessage?.(data as LangGraphMessage)
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

  /**
   * 恢复/继续执行
   */
  const onResume = async (
    conversationId: string,
    action: 'approve' | 'edit' | 'reject',
    modifiedSql?: string,
    onMessage?: (message: LangGraphMessage) => void,
    onResult?: (result: NL2SQLResult) => void,
    onError?: (error: string) => void,
    onPendingTool?: (pendingTool: PendingTool) => void,
    onRunningTool?: (toolInfo: { tool_name: string; tool_call_id: string }) => void,
    datasourceId?: string,
    semanticConfigId?: string
  ): Promise<void> => {
    loading.value = true
    error.value = null

    try {
      const response = await streamResume(conversationId, {
        action,
        modified_sql: action === 'edit' ? modifiedSql : undefined,
        datasource_id: datasourceId,
        semantic_config_id: semanticConfigId,
      })

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

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const { events, remaining } = parseSSEEvents(buffer)
        buffer = remaining

        for (const { event, data: dataStr } of events) {
          // 处理结束事件
          if (event === 'done' || dataStr === '[DONE]') {
            streamEnded = true
            break
          }

          // 处理错误事件
          if (event === 'error') {
            try {
              const errorData = JSON.parse(dataStr)
              throw new Error(errorData.error || '未知错误')
            } catch {
              throw new Error(dataStr)
            }
          }

          // 处理 tool 事件
          if (event === 'tool') {
            try {
              const data = JSON.parse(dataStr)
              if (data.status === 'pending') {
                onPendingTool?.({
                  tool_call_id: data.tool_call_id || '',
                  tool_name: data.tool_name,
                  args: data.args || {},
                  message: data.message,
                })
              } else if (data.status === 'running') {
                onRunningTool?.({
                  tool_name: data.tool_name,
                  tool_call_id: data.tool_call_id || '',
                })
              } else {
                // completed/success 状态，作为普通消息处理
                onMessage?.(data as LangGraphMessage)
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
          }
        }

        if (streamEnded) break
      }

      // 流结束
      onResult?.({} as NL2SQLResult)
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : '请求失败'
      error.value = errorMsg
      onError?.(errorMsg)
    } finally {
      loading.value = false
    }
  }

  return {
    loading,
    error,
    generateSqlStream,
    stopGeneration,
    onResume,
  }
}
