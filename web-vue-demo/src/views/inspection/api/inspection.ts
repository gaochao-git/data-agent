/**
 * Inspection 模块 API 封装
 * 统一封装执法检查模块相关的所有请求
 */

import request from '@/utils/request'
import requestStream from '@/utils/requestStream'
import { getToken } from '@/utils/auth'

// API 基础路径 - 使用 /agents 前缀代理到 AI Agent 后端 (5001端口)
const BASE_PREFIX = import.meta.env.VITE_APP_BASE_API || ''

/**
 * 创建会话
 */
export async function createConversation(agentId: string) {
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
 */
export async function getConversationMessages(threadId: string) {
  return request({
    url: `/api/violation/conversations/${threadId}`,
    method: 'get',
  })
}

/**
 * 获取会话状态
 */
export async function getConversationState(threadId: string) {
  return request({
    url: `/api/conversations/${threadId}/state`,
    method: 'get',
  })
}

/**
 * 获取会话文件列表
 */
export async function getConversationFiles(threadId: string) {
  return request({
    url: `/api/conversations/${threadId}/files`,
    method: 'get',
  })
}

/**
 * 上传文件
 */
export async function uploadFile(file: File, threadId?: string) {
  const formData = new FormData()
  formData.append('file', file)

  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) {
    headers['Authorization'] = 'Bearer ' + token
  }

  const url = threadId
    ? `${BASE_PREFIX}/api/conversations/${threadId}/files`
    : `${BASE_PREFIX}/api/files/upload`

  return fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers,
    body: formData,
  }).then(res => res.json())
}

/**
 * 删除文件组
 */
export async function deleteFileGroup(threadId: string, groupId: string) {
  return request({
    url: `/api/conversations/${threadId}/files/groups/${groupId}`,
    method: 'delete',
  })
}

/**
 * 删除单个文件
 */
export async function deleteFile(threadId: string, fileId: string) {
  return request({
    url: `/api/conversations/${threadId}/files/${fileId}`,
    method: 'delete',
  })
}

/**
 * 会话补全请求（SSE 流式请求）
 * 用于处理 SSE 流式响应
 */
export async function conversationCompletion(threadId: string, params: {
  query?: string
  agent_id?: string
  options?: any
}, signal?: AbortSignal) {
  return requestStream({
    url: `/api/conversations/${threadId}/completion`,
    method: 'POST',
    data: params,
    signal,
  })
}

/**
 * 会话恢复请求（SSE 流式请求）
 * 用于恢复被中断的工作流
 */
export async function conversationResume(threadId: string, params: {
  action: string
  data?: any
}, signal?: AbortSignal) {
  return requestStream({
    url: `/api/conversations/${threadId}/resume`,
    method: 'POST',
    data: params,
    signal,
  })
}

/**
 * 生成会话标题
 */
export async function generateConversationTitle(threadId: string) {
  return request({
    url: `/api/conversations/${threadId}/title`,
    method: 'post',
  })
}

/**
 * 获取文件下载地址
 */
export function getFileDownloadUrl(fileId: string) {
  return `${BASE_PREFIX}/api/files/${fileId}`
}

/**
 * 获取会话列表
 */
export async function getConversations() {
  return request({
    url: '/api/conversations',
    method: 'get',
  })
}

export default {
  createConversation,
  getConversationMessages,
  getConversationState,
  getConversationFiles,
  uploadFile,
  deleteFileGroup,
  deleteFile,
  conversationCompletion,
  conversationResume,
  generateConversationTitle,
  getFileDownloadUrl,
  getConversations,
}
