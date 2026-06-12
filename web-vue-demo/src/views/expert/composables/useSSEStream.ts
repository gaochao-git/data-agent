/**
 * Expert 模块 SSE 流处理 Composable
 *
 * ChatMessage 格式：
 * - event: chat_message
 * - data: {event: "...", ...}
 */

import { ref } from 'vue'
import type { ChatMessage, ChatMockMessage, ThinkingItem, ToolCallInfo } from '../types'

export interface SSEEventHandlers {
  onStart?: () => void
  onMessage?: (content: string) => void
  onTask?: (tasks: any[]) => void
  onCompleted?: (data?: any) => void
  onError?: (error: string) => void
  onInterrupt?: (data: {
    review_type: string
    message: string
    current_data: any
  }) => void
  onEnd?: () => void
  // Expert 特有事件
  onStepStart?: (stepId: string, step: ThinkingItem) => void
  onStepEnd?: (stepId: string) => void
  onReferences?: (references: any[]) => void
  onSuggestions?: (suggestions: any[]) => void
  onConversationIdChange?: (conversationId: string) => void
  // 工具调用事件
  onToolCallStart?: (toolCall: ToolCallInfo) => void
  onToolCallDelta?: (data: { toolCallId: string; delta: string }) => void
  onToolCallEnd?: (toolCallId: string) => void
  // 文件相关事件
  onFile?: (data: { file_id: string; mime_type: string; url: string }) => void
  onFileTitle?: (data: { title: string }) => void
  onExcelData?: (data: any) => void
  onReportSourceData?: (data: any) => void
}

export interface SSEStreamOptions {
  url: string
  method?: string
  body: any
  signal?: AbortSignal
  handlers: SSEEventHandlers
}

/**
 * 处理 SSE 流式响应（specialist_app 格式）
 *
 * specialist_app 发送格式：
 * data: {"event": "content_delta", "delta": "...", ...}
 * data: {"event": "tool_call_start", ...}
 * data: {"event": "done", ...}
 */
export async function processSSEStream(options: SSEStreamOptions): Promise<void> {
  const { url, method = 'POST', body, signal, handlers } = options

  try {
    const response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      credentials: 'include',
      signal,
    })

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(errorText || `HTTP ${response.status}`)
    }

    if (!response.body) {
      throw new Error('响应体为空')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()

        // 跳过空行
        if (!trimmed) continue

        // 解析 data: 行（specialist_app 格式）
        if (trimmed.startsWith('data:')) {
          const dataStr = trimmed.slice(5).trim()
          if (!dataStr) continue

          try {
            const data = JSON.parse(dataStr)
            handleSpecialistEvent(data, handlers)
          } catch (error) {
            console.error('解析 SSE 数据失败:', error)
          }
        }
      }
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      // 用户主动中止，不触发错误
    } else {
      console.error('SSE 流错误:', error)
      handlers.onError?.(error instanceof Error ? error.message : '未知错误')
    }
  } finally {
    handlers.onEnd?.()
  }
}

/**
 * 处理 specialist_app 事件
 */
function handleSpecialistEvent(
  data: any,
  handlers: SSEEventHandlers
) {
  const eventType = data.event
  if (!eventType) return

  console.log('[expert SSE] event:', eventType, data)

  // message_start: 开始新消息
  if (eventType === 'message_start') {
    handlers.onStart?.()
    if (typeof data.conversation_id === 'string' && data.conversation_id) {
      handlers.onConversationIdChange?.(data.conversation_id)
    }
    return
  }

  // content_delta: 内容增量
  if (eventType === 'content_delta') {
    const delta = typeof data.delta === 'string' ? data.delta : ''
    if (delta) {
      handlers.onMessage?.(delta)
    }
    return
  }

  // node_start: 思考节点开始
  if (eventType === 'node_start') {
    const nodeId = data.node_id || `node-${Date.now()}`
    const nodeType = data.type || 'thinking'
    const title = data.title || '思考中'
    handlers.onStepStart?.(nodeId, {
      id: nodeId,
      label: title,
      status: 'thinking',
      toolName: title,
      label_hidden: data.label_hidden,
      flatten_children: data.flatten_children,
    })
    return
  }

  // node_delta: 思考节点增量
  if (eventType === 'node_delta') {
    const nodeId = data.node_id
    if (nodeId && data.delta) {
      handlers.onStepStart?.(nodeId, {
        id: nodeId,
        label: '',  // delta 不更新标签
        status: 'thinking',
        detailLines: [{
          lineId: data.line_id,
          content: data.delta,
        }],
      })
    }
    return
  }

  // node_end: 思考节点结束
  if (eventType === 'node_end') {
    const nodeId = data.node_id
    if (nodeId) {
      handlers.onStepEnd?.(nodeId)
    }
    return
  }

  // tool_call_start: 工具调用开始（兼容旧版本）
  if (eventType === 'tool_call_start') {
    const stepId = data.tool_call_id || `step-${Date.now()}`
    const toolName = data.name || '调用工具'
    handlers.onStepStart?.(stepId, {
      id: stepId,
      label: data.desc || toolName,
      status: 'thinking',
      toolName: toolName,
    })
    // 触发工具调用进度显示
    handlers.onToolCallStart?.({
      id: stepId,
      name: toolName.slice(0, 10),
      status: 'running',
      resultPreview: '',
      timestamp: Date.now(),
    })
    return
  }

  // tool_call_delta: 工具调用增量（更新描述）
  if (eventType === 'tool_call_delta') {
    const stepId = data.tool_call_id
    if (stepId && data.desc) {
      handlers.onStepStart?.(stepId, {
        id: stepId,
        label: data.desc,
        status: 'thinking',
      })
    }
    return
  }

  // tool_result_delta: 工具结果流式输出
  if (eventType === 'tool_result_delta' && data.delta) {
    const toolCallId = data.tool_call_id
    if (toolCallId) {
      handlers.onToolCallDelta?.({
        toolCallId,
        delta: data.delta,
      })
    }
    return
  }

  // tool_call_end: 工具调用结束
  if (eventType === 'tool_call_end') {
    const stepId = data.tool_call_id
    if (stepId) {
      handlers.onStepEnd?.(stepId)
      handlers.onToolCallEnd?.(stepId)
    }
    return
  }

  // message_end: 消息结束
  if (eventType === 'message_end') {
    handlers.onCompleted?.(data)
    return
  }

  // title_generation: 标题生成
  if (eventType === 'title_generation' || eventType === 'title_generation_done') {
    // 触发历史刷新以显示新标题
    handlers.onCompleted?.({ title: data.title })
    return
  }

  // done: 完成
  if (eventType === 'done') {
    handlers.onCompleted?.({})
    return
  }

  // references: 引用更新
  if (eventType === 'references' && data.references) {
    handlers.onReferences?.(data.references)
    return
  }
}

/**
 * SSE Composable - 提供流式处理能力
 */
export function useSSEStream() {
  const abortControllerRef = ref<AbortController | null>(null)
  const isStreaming = ref(false)

  const startStream = async (options: Omit<SSEStreamOptions, 'signal'>) => {
    // 取消之前的流
    abortControllerRef.value?.abort()

    // 创建新的 AbortController
    const controller = new AbortController()
    abortControllerRef.value = controller
    isStreaming.value = true

    try {
      await processSSEStream({
        ...options,
        signal: controller.signal,
      })
    } finally {
      if (abortControllerRef.value === controller) {
        abortControllerRef.value = null
      }
      isStreaming.value = false
    }
  }

  const stopStream = () => {
    abortControllerRef.value?.abort()
    abortControllerRef.value = null
    isStreaming.value = false
  }

  return {
    startStream,
    stopStream,
    isStreaming,
  }
}

export default useSSEStream
