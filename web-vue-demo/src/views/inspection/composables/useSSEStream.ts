/**
 * 通用 SSE 流处理 Composable
 *
 * LangChain 官方格式：
 * - data: {"content": "...", "type": "AIMessageChunk", ...}
 * - data: [DONE]
 */

import { ref } from 'vue'
import type { ChatMessage } from '../types'
import { getToken } from '@/utils/auth'

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
  headers?: HeadersInit  // 可选的自定义 headers
}

// 获取带认证的 fetch headers
function getAuthHeaders(extraHeaders?: HeadersInit): HeadersInit {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(extraHeaders || {}),
  }
  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

/**
 * 处理 SSE 流式响应（LangChain 官方格式）
 *
 * 格式：
 *   data: {"content": "...", "type": "AIMessageChunk", ...}
 *   data: [DONE]
 */
export async function processSSEStream(options: SSEStreamOptions): Promise<void> {
  const { url, method = 'POST', body, signal, handlers, headers: extraHeaders } = options

  try {
    const response = await fetch(url, {
      method,
      headers: getAuthHeaders(extraHeaders),
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
    let currentEventType = 'message'  // 默认事件类型

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()

        // 解析 event: 行
        if (trimmed.startsWith('event:')) {
          currentEventType = trimmed.slice(6).trim()
          continue
        }

        // 解析 data: 行
        if (!trimmed.startsWith('data:')) continue

        const dataStr = trimmed.slice(5).trim()
        if (!dataStr) continue

        // 结束标记
        if (dataStr === '[DONE]') {
          handlers.onCompleted?.({})
          currentEventType = 'message'
          return
        }

        let eventData: any
        try {
          eventData = JSON.parse(dataStr)
        } catch {
          currentEventType = 'message'
          continue
        }

        // 根据事件类型处理
        if (currentEventType === 'completed') {
          handlers.onCompleted?.(eventData)
          currentEventType = 'message'
          continue
        }

        if (currentEventType === 'error') {
          handlers.onError?.(eventData.message || eventData.error || '未知错误')
          currentEventType = 'message'
          continue
        }

        if (currentEventType === 'interrupt') {
          handlers.onInterrupt?.(eventData.interrupt_data || eventData)
          currentEventType = 'message'
          return
        }

        // 错误处理（兼容旧格式）
        if (eventData.error) {
          handlers.onError?.(eventData.error)
          currentEventType = 'message'
          continue
        }

        // 中断处理（兼容旧格式）
        if (eventData.interrupt_data) {
          handlers.onInterrupt?.(eventData.interrupt_data)
          currentEventType = 'message'
          return
        }

        // 非标准块处理（NonStandardContentBlock 格式）
        if (eventData.type === 'non_standard') {
          // 任务进度
          if (eventData.value?.type === 'task_progress' && handlers.onTask) {
            handlers.onTask(eventData.value.data)
          }
          // 分析数据
          else if (eventData.value?.type === 'analysis_data') {
            console.log('[SSE] 收到 analysis_data:', eventData.value.data)
            // 分析数据通常在 onCompleted 中从消息中提取，这里不做处理
          }
          currentEventType = 'message'
          continue
        }

        // 任务进度（旧格式兼容）
        if (eventData.type === 'task_progress' && handlers.onTask) {
          handlers.onTask(eventData.data)
          currentEventType = 'message'
          continue
        }

        // AI 消息：type 为 "AIMessageChunk" 或 "ai"
        if ((eventData.type === 'AIMessageChunk' || eventData.type === 'ai') && eventData.content) {
          handlers.onMessage?.(eventData.content)
        }

        currentEventType = 'message'
      }
    }
  } finally {
    handlers.onEnd?.()
  }
}

/**
 * SSE Composable - 提供流式处理能力
 */
export function useSSEStream() {
  const abortControllerRef = ref<AbortController | null>(null)

  const startStream = async (options: Omit<SSEStreamOptions, 'signal'>) => {
    // 取消之前的流
    abortControllerRef.value?.abort()

    // 创建新的 AbortController
    const controller = new AbortController()
    abortControllerRef.value = controller

    try {
      await processSSEStream({
        ...options,
        signal: controller.signal,
      })
    } finally {
      if (abortControllerRef.value === controller) {
        abortControllerRef.value = null
      }
    }
  }

  const stopStream = () => {
    abortControllerRef.value?.abort()
    abortControllerRef.value = null
  }

  const isStreaming = () => abortControllerRef.value !== null

  return {
    startStream,
    stopStream,
    isStreaming,
  }
}

export default useSSEStream
