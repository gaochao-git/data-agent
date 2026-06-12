/**
 * Expert 模块 API 封装
 * 迁移自: web/src/pages/GeneralAssistant/hooks/useGeneralAssistant.ts (API部分)
 * 参考: web-vue/src/views/inspection/api/inspection.ts
 */

import request from '@/utils/agentRequest'

// 使用代理路径，不需要完整的 URL
const BASE_PREFIX = '/api'

import type { UploadedFile, ConversationHistoryItem, PolicyResponse } from '../types/index'

/**
 * API 响应基础类型
 */
export interface ApiResponse<T = any> {
  code?: number
  msg?: string
  data?: T
  status?: string
}

// ============ 会话相关 API ============

/**
 * 创建创建会话
 * @param agentId - 智能体 ID
 * @returns 会话信息
 */
export async function createConversation(agentId: string): Promise<ApiResponse<{ id: string }>> {
  return request({
    url: '/api/conversations',
    method: 'post',
    data: {
      agent_id: agentId,
    },
  })
}

/**
 * 获取会话消息
 * @param conversationId - 会话 ID
 * @returns 会话消息列表
 */
export async function getConversation(conversationId: string): Promise<ApiResponse<{ messages: any[] }>> {
  return request({
    url: `/api/ai_chat/conversations/${conversationId}`,
    method: 'get',
  })
}

/**
 * 获取历史会话列表
 * @returns 会话列表
 */
export async function getConversationHistory(): Promise<ApiResponse<ConversationHistoryItem[]>> {
  return request({
    url: '/api/ai_chat/conversations',
    method: 'get',
  })
}

/**
 * 生成会话标题
 * @param conversationId - 会话 ID
 * @returns 标题信息
 */
export async function generateTitle(conversationId: string): Promise<ApiResponse<{ title: string }>> {
  return request({
    url: `/api/conversations/${conversationId}/title`,
    method: 'post',
  })
}

// ============ 文件相关 API ============

/**
 * 上传文件
 * @param file - 文件对象
 * @param conversationId - 会话 ID
 * @returns 文件信息
 */
export async function uploadFile(
  file: File,
  conversationId: string
): Promise<UploadedFile | null> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('thread_id', conversationId)

  try {
    const response = await fetch(`/api/uploaded-files/upload`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const data = await response.json()
    return {
      file_id: data.file_id,
      original_filename: data.original_filename,
      file_type: data.file_type,
      file_size: data.file_size,
      status: data.status,
    }
  } catch (error) {
    console.error('文件上传失败:', error)
    return null
  }
}

/**
 * 获取文件信息
 * @param fileId - 文件 ID
 * @returns 文件信息
 */
export async function getFileInfo(fileId: string): Promise<UploadedFile | null> {
  try {
    const response = await fetch(`/api/uploaded-files/${fileId}`, {
      method: 'GET',
      credentials: 'include',
    })

    if (!response.ok) {
      return null
    }

    const data = await response.json()
    return {
      file_id: data.file_id,
      original_filename: data.original_filename,
      file_type: data.file_type,
      file_size: data.file_size,
      status: data.status,
    }
  } catch (error) {
    console.error(`获取文件信息失败: ${fileId}`, error)
    return null
  }
}

/**
 * 轮询文件处理状态
 * @param fileId - 文件 ID
 * @param maxAttempts - 最大轮询次数
 * @param interval - 轮询间隔（毫秒）
 * @param onUpdate - 状态更新回调
 * @returns 最终状态
 */
export async function pollFileStatus(
  fileId: string,
  maxAttempts: number = 30,
  interval: number = 1000,
  onUpdate?: (status: string) => void
): Promise<string> {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const response = await fetch(`/api/uploaded-files/${fileId}`, {
        credentials: 'include',
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const data = await response.json()
      const status = data.status

      if (onUpdate) {
        onUpdate(status)
      }

      if (status === 'completed' || status === 'failed') {
        return status
      }

      // 等待后继续轮询
      await new Promise((resolve) => setTimeout(resolve, interval))
    } catch (error) {
      console.error('轮询文件状态失败:', error)
      return 'failed'
    }
  }

  return 'timeout'
}

/**
 * 删除文件
 * @param fileId - 文件 ID
 * @returns 删除结果
 */
export async function deleteFile(fileId: string): Promise<ApiResponse> {
  return request({
    url: `/api/uploaded-files/${fileId}`,
    method: 'delete',
  })
}

// ============ 法规相关 API ============

/**
 * 获取法规内容
 * @param policyKey - 法规 key
 * @returns 法规内容
 */
export async function fetchPolicy(policyKey: string): Promise<PolicyResponse | null> {
  try {
    const response = await fetch(`/api/policies/${encodeURIComponent(policyKey)}`, {
      credentials: 'include',
    })

    if (!response.ok) {
      return null
    }

    const data = await response.json()
    return data
  } catch (error) {
    console.error('获取法规内容失败:', error)
    return null
  }
}

// ============ SSE 相关 API ============

/**
 * 会话补全请求（SSE 流式请求）
 * specialist_app 使用专属的 /api/ai_chat 接口
 * @param threadId - 会话 ID
 * @param params - 请求参数
 * @param signal - AbortSignal 用于取消请求
 * @returns Response 对象，用于读取 SSE 流
 */
export async function conversationCompletion(
  threadId: string,
  params: {
    query: string
    agent_id: string
    file_ids?: string[]
    modelConfig?: any
  },
  signal?: AbortSignal
): Promise<Response> {
  // specialist_app 使用 /api/ai_chat 接口，请求体格式为 {question, conversation_id}
  const url = `/api/ai_chat`

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      question: params.query,
      conversation_id: threadId,
    }),
    credentials: 'include',
    signal,
  })

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(errorText || `HTTP ${response.status}`)
  }

  return response
}

export default {
  createConversation,
  getConversation,
  getConversationHistory,
  generateTitle,
  uploadFile,
  getFileInfo,
  pollFileStatus,
  deleteFile,
  fetchPolicy,
  conversationCompletion,
}
