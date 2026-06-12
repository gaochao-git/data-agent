/**
 * Expert 模块核心状态管理 Composable
 * 迁移自: web/src/pages/GeneralAssistant/hooks/useGeneralAssistant.ts
 *
 * 负责管理对话状态、历史记录和 API 调用
 */

import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useSSEStream } from './useSSEStream'
import { ensureThreadId } from '../utils/threadHelpers'
import * as expertApi from '../api/expert'
import type {
  ChatMessage,
  ConversationHistoryItem,
  UploadedFile,
  AssistantReference,
} from '../types'

// 使用代理路径，不需要完整的 URL
const API_BASE = ''

// Agent ID 常量
const AGENT_ID = 'expert_agent'

export interface UseExpertAssistantOptions {
  /** 后端 agent_id */
  agentId?: string
}

export function useExpertAssistant(options: UseExpertAssistantOptions = {}) {
  const { agentId = AGENT_ID } = options

  const route = useRoute()
  const router = useRouter()
  const { startStream, stopStream: stopSSEStream, isStreaming: sseIsStreaming } = useSSEStream()

  // ===== 对话状态 =====
  const chatMessages = ref<ChatMessage[]>([])
  const chatInput = ref('')
  const isChatStreaming = ref(false)
  const activeAssistantMessageId = ref<string | null>(null)

  // ===== 历史对话状态 =====
  const conversationHistory = ref<ConversationHistoryItem[]>([])
  const activeHistoryId = ref<string | null>(null)
  const isHistoryCollapsed = ref(true)
  const isHistoryLoading = ref(true) // 初始为 true，首次加载时显示
  const authError = ref<string | null>(null)

  // ===== 文件上传状态 =====
  const uploadedFiles = ref<UploadedFile[]>([])

  // ===== 当前线程 ID - 初始化时从 URL 读取 =====
  const chatThreadId = ref<string | null>(
    route.params.conversationId as string || null
  )

  // ===== 历史刷新触发器 =====
  const historyRefreshTrigger = ref(0)

  // ===== Refs =====
  const chatContainerRef = ref<HTMLDivElement | null>(null)

  // ===== 跟踪已生成标题的线程 ID，确保每个线程只生成一次标题 =====
  const generatedTitlesRef = ref<Set<string>>(new Set())

  // ===== 标记是否已完成首次加载 =====
  const isFirstLoadRef = ref(true)

  // ===== 监听 URL 参数变化，更新 threadId 并加载消息 =====
  watch(
    () => route.params.conversationId as string | undefined,
    (conversationIdFromUrl) => {
      if (conversationIdFromUrl && conversationIdFromUrl !== chatThreadId.value) {
        chatThreadId.value = conversationIdFromUrl
        activeHistoryId.value = conversationIdFromUrl
      } else if (!conversationIdFromUrl && chatThreadId.value) {
        // URL 中没有 threadId 时，清除当前 threadId
        chatThreadId.value = null
        activeHistoryId.value = null
      }
    }
  )

  /**
   * 根据 file_ids 批量获取文件信息
   */
  const fetchFilesByIds = async (fileIds: string[]): Promise<UploadedFile[]> => {
    const files: UploadedFile[] = []
    for (const fid of fileIds) {
      try {
        const fileInfo = await expertApi.getFileInfo(fid)
        if (fileInfo) {
          files.push(fileInfo)
        }
      } catch (e) {
        console.warn(`获取文件信息失败: ${fid}`, e)
      }
    }
    return files
  }

  /**
   * 页面加载时，如果 URL 中有 threadId 且消息为空，则加载历史消息
   */
  const loadMessagesFromUrl = async () => {
    const conversationIdFromUrl = route.params.conversationId as string | undefined
    if (conversationIdFromUrl && chatMessages.value.length === 0) {
      try {
        const response = await expertApi.getConversation(conversationIdFromUrl)
        const messages = response?.data?.messages || []

        // 格式化消息，根据 file_ids 获取文件详情
        const formattedMessages: ChatMessage[] = await Promise.all(
          messages.map(async (msg: any, index: number) => {
            const message: ChatMessage = {
              id: `history-${conversationIdFromUrl}-${index}`,
              role: msg.role,
              content: msg.content,
              timestamp: new Date(),
            }

            // 如果有 file_ids，获取文件详情
            if (msg.file_ids && msg.file_ids.length > 0) {
              message.attachments = await fetchFilesByIds(msg.file_ids)
            }

            return message
          })
        )

        chatMessages.value = formattedMessages
      } catch (error) {
        console.error('加载历史消息失败:', error)
      }
    }
  }

  /**
   * 生成并更新会话标题
   * 注意：specialist_app 可能不支持标题生成，所以这里只刷新历史记录
   */
  const generateAndUpdateTitle = async (threadId: string) => {
    // 直接触发历史记录刷新
    historyRefreshTrigger.value++
  }

  /**
   * 创建新的对话线程
   * 注意：specialist_app 没有创建会话接口，会话由后端自动管理
   * 返回一个临时 ID，实际会话 ID 由后端在首次对话时返回
   */
  const createNewThread = async (): Promise<string | null> => {
    // 返回一个临时 ID，实际会话 ID 由后端返回
    return `temp-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  }

  /**
   * 处理文件上传
   */
  const handleFileUpload = async (files: FileList) => {
    // 先把 FileList 转成数组，避免后续 router 导致 FileList 失效
    const fileArray = Array.from(files)
    console.log('[文件上传] 开始, 文件数量:', fileArray.length, 'chatThreadId:', chatThreadId.value)

    // 确保有线程 ID
    let currentThreadId = chatThreadId.value
    if (!currentThreadId) {
      console.log('[文件上传] 没有 threadId，创建新会话...')
      currentThreadId = await createNewThread()
      console.log('[文件上传] 创建结果:', currentThreadId)
      if (!currentThreadId) {
        ElMessage.error('无法创建会话')
        return
      }
    }

    console.log('[文件上传] 使用 threadId:', currentThreadId)

    for (const file of fileArray) {
      console.log('[文件上传] 处理文件:', file.name)
      // 先添加到本地状态，显示"上传中"
      const tempId = `temp-${Date.now()}-${Math.random()}`
      const tempFile: UploadedFile = {
        file_id: tempId,
        original_filename: file.name,
        file_type: file.name.split('.').pop()?.toLowerCase() || '',
        file_size: file.size,
        status: 'uploading',
      }
      uploadedFiles.value = [...uploadedFiles.value, tempFile]

      // 上传文件
      console.log('[文件上传] 调用 uploadFile:', file.name, currentThreadId)
      const result = await expertApi.uploadFile(file, currentThreadId)
      console.log('[文件上传] uploadFile 结果:', result)
      if (result) {
        // 更新为真实的文件信息，确保状态是 "parsing"
        const fileWithParsingStatus = { ...result, status: 'parsing' }
        uploadedFiles.value = uploadedFiles.value.map((f) =>
          f.file_id === tempId ? fileWithParsingStatus : f
        )

        // 延迟 500ms 后开始轮询，让用户能看到 "解析中" 状态
        setTimeout(() => {
          expertApi.pollFileStatus(result.file_id, 30, 1000, (status) => {
            uploadedFiles.value = uploadedFiles.value.map((f) =>
              f.file_id === result.file_id ? { ...f, status } : f
            )
          }).then((finalStatus) => {
            uploadedFiles.value = uploadedFiles.value.map((f) =>
              f.file_id === result.file_id ? { ...f, status: finalStatus } : f
            )
          })
        }, 500)
      } else {
        // 上传失败，移除临时文件
        uploadedFiles.value = uploadedFiles.value.filter((f) => f.file_id !== tempId)
      }
    }
  }

  /**
   * 移除已上传的文件
   */
  const removeUploadedFile = async (fileId: string) => {
    try {
      await expertApi.deleteFile(fileId)
    } catch (error) {
      console.error('删除文件失败:', error)
    }

    uploadedFiles.value = uploadedFiles.value.filter((f) => f.file_id !== fileId)
  }

  /**
   * 清空已上传的文件
   */
  const clearUploadedFiles = () => {
    uploadedFiles.value = []
  }

  /**
   * 发送聊天消息
   */
  const sendChatMessage = async (message: string) => {
    if (!message.trim()) return

    // 确保有线程 ID（使用锁机制防止并发创建）
    const currentThreadId = await ensureThreadId(
      chatThreadId.value,
      createNewThread,
      '法律助手对话'
    )
    if (!currentThreadId) {
      ElMessage.error('无法创建会话')
      return
    }

    // 更新本地状态
    if (chatThreadId.value !== currentThreadId) {
      chatThreadId.value = currentThreadId
    }

    // 添加用户消息
    const userMessageId = `user-${Date.now()}`
    const userMessage: ChatMessage = {
      id: userMessageId,
      role: 'user',
      content: message,
      timestamp: new Date(),
      attachments: uploadedFiles.value.length > 0 ? [...uploadedFiles.value] : undefined,
    }
    chatMessages.value = [...chatMessages.value, userMessage]

    // 发送后清空文件列表
    uploadedFiles.value = []

    // 创建 AI 消息占位符
    const assistantMessageId = `assistant-${Date.now()}`
    const assistantMessage: ChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
    }
    chatMessages.value = [...chatMessages.value, assistantMessage]
    activeAssistantMessageId.value = assistantMessageId

    // 开始流式输出
    isChatStreaming.value = true

    try {
      await startStream({
        url: `${API_BASE}/api/ai_chat`,
        method: 'POST',
        body: {
          question: message,
          conversation_id: currentThreadId,
        },
        handlers: {
          onStart: () => {
            console.log('[法律助手] 流开始')
          },
          onMessage: (content: string) => {
            // 累积文本内容
            chatMessages.value = chatMessages.value.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: (msg.content as string) + content }
                : msg
            )
          },
          onStepStart: (stepId: string, step: any) => {
            // 更新步骤状态
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg

              const updateSteps = (steps: any[]): any[] => {
                return steps.map((s: any) => {
                  if (s.id === stepId) {
                    // 如果有 detailLines，需要合并而不是覆盖
                    if (step.detailLines) {
                      const existingDetailLines = s.detailLines || []
                      const newDetailLines = step.detailLines
                      // 合并 detailLines
                      return {
                        ...s,
                        ...step,
                        detailLines: [...existingDetailLines, ...newDetailLines],
                      }
                    } else {
                      // 否则直接合并
                      return { ...s, ...step }
                    }
                  }
                  if (s.children && s.children.length > 0) {
                    return { ...s, children: updateSteps(s.children) }
                  }
                  return s
                })
              }

              const existingSteps = msg.steps || []
              const stepExists = (steps: any[], id: string): boolean => {
                return steps.some((s) => {
                  if (s.id === id) return true
                  if (s.children && s.children.length > 0) {
                    return stepExists(s.children, id)
                  }
                  return false
                })
              }

              if (!stepExists(existingSteps, stepId)) {
                return { ...msg, steps: [...existingSteps, step] }
              } else {
                return { ...msg, steps: updateSteps(existingSteps) }
              }
            })
          },
          onStepEnd: (stepId: string) => {
            // 更新步骤状态为完成
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg

              const updateSteps = (steps: any[]): any[] => {
                {
                  return steps.map((s: any) => {
                    if (s.id === stepId) {
                      return { ...s, status: 'done' }
                    }
                    if (s.children && s.children.length > 0) {
                      return { ...s, children: updateSteps(s.children) }
                    }
                    return s
                  })
                }
              }

              return { ...msg, steps: updateSteps(msg.steps || []) }
            })
          },
          onToolCallStart: (toolCall) => {
            // 添加工具调用记录
            chatMessages.value = chatMessages.value.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, toolCalls: [...(msg.toolCalls || []), toolCall] }
                : msg
            )
          },
          onToolCallDelta: ({ toolCallId, delta }) => {
            // 更新工具调用结果预览
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg

              const updatedToolCalls = (msg.toolCalls || []).map((tc: any) =>
                tc.id === toolCallId
                  ? {
                      ...tc,
                      resultPreview: (tc.resultPreview + delta).slice(0, 20),
                    }
                  : tc
              )
              return { ...msg, toolCalls: updatedToolCalls }
            })
          },
          onToolCallEnd: (toolCallId) => {
            // 标记工具调用完成
            chatMessages.value = chatMessages.value.map((msg) => {
              if (msg.id !== assistantMessageId) return msg

              const updatedToolCalls = (msg.toolCalls || []).map((tc: any) =>
                tc.id === toolCallId ? { ...tc, status: 'completed' } : tc
              )
              return { ...msg, toolCalls: updatedToolCalls }
            })
          },
          onReferences: (references: any[]) => {
            // 更新引用
            chatMessages.value = chatMessages.value.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, references }
                : msg
            )
          },
          onSuggestions: (suggestions: any[]) => {
            // 更新建议
            chatMessages.value = chatMessages.value.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, suggestions }
                : msg
            )
          },
          onConversationIdChange: (conversationId: string) => {
            // 更新会话ID
            if (chatThreadId.value !== conversationId) {
              chatThreadId.value = conversationId
            }
          },
          onCompleted: (data) => {
            console.log('[法律助手] 流完成', data)
            // 先刷新历史（显示新会话）
            historyRefreshTrigger.value++
            // 异步生成标题（完成后会再次触发刷新）
            void generateAndUpdateTitle(currentThreadId)
          },
          onError: (error) => {
            console.error('[法律助手] 流错误:', error)
            ElMessage.error(`发送失败: ${error}`)
          },
          onEnd: () => {
            isChatStreaming.value = false
            activeAssistantMessageId.value = null
          },
        },
      })
    } catch (error) {
      console.error('[法律助手] 发送消息失败:', error)
      ElMessage.error('发送失败，请稍后重试')
      isChatStreaming.value = false
      activeAssistantMessageId.value = null
    }
  }

  /**
   * 停止流式输出
   */
  const stopChatStreaming = async () => {
    stopSSEStream()
    isChatStreaming.value = false
    activeAssistantMessageId.value = null
  }

  /**
   * 加载历史对话列表（所有智能体的会话）
   * 首次加载显示 loading，后续刷新静默更新（避免页面抖动）
   */
  const loadConversationHistory = async () => {
    // 只有首次加载时显示 loading
    if (isFirstLoadRef.value) {
      isHistoryLoading.value = true
    }
    try {
      // 不传 agent_id，获取所有会话
      const response = await expertApi.getConversationHistory()

      // 检查认证错误
      if (response?.code === 40101) {
        authError.value = '请先登录后再使用'
        conversationHistory.value = []
        return
      }

      // 清除之前的错误
      authError.value = null

      // 后端返回的数据结构：{user_id, conversations}
      const conversationsData = response?.data?.conversations || response?.data || []
      const conversations = Array.isArray(conversationsData) ? conversationsData : []

      const formattedHistory: ConversationHistoryItem[] = conversations.map((conv: any) => {
        // 确保 timestamp 是有效的 Date 对象
        const updateTime = conv.last_used_at ? new Date(conv.last_used_at) : null
        const createTime = conv.created_at ? new Date(conv.created_at) : null
        const timestamp = updateTime || createTime || new Date()

        return {
          id: conv.conversation_id,
          title: conv.title || '新会话',
          timestamp,
          preview: conv.latest_message_preview || '',
          is_favorited: false,
          agent_id: 'expert_agent',
          create_time: createTime || undefined,
          update_time: updateTime || undefined,
          message_count: conv.message_count || 0,
        }
      })

      // 按创建时间降序排序（最新创建的在前面）
      formattedHistory.sort((a, b) => {
        const timeA = a.create_time || a.timestamp
        const timeB = b.create_time || b.timestamp
        return timeB.getTime() - timeA.getTime()
      })

      conversationHistory.value = formattedHistory
    } catch (error) {
      console.error('加载历史对话失败:', error)
    } finally {
      // 首次加载完成后，标记为已完成并关闭 loading
      if (isFirstLoadRef.value) {
        isFirstLoadRef.value = false
        isHistoryLoading.value = false
      }
    }
  }

  /**
   * 切换到历史对话
   */
  const switchToThread = async (threadId: string) => {
    try {
      chatThreadId.value = threadId
      activeHistoryId.value = threadId

      const response = await expertApi.getConversation(threadId)
      const messages = response?.data?.messages || []

      // 格式化消息，根据 file_ids 获取文件详情
      const formattedMessages: ChatMessage[] = await Promise.all(
        messages.map(async (msg: any, index: number) => {
          const message: ChatMessage = {
            id: `history-${threadId}-${index}`,
            role: msg.role,
            content: msg.content,
            timestamp: new Date(),
          }

          // 如果有 file_ids，获取文件详情
          if (msg.file_ids && msg.file_ids.length > 0) {
            message.attachments = await fetchFilesByIds(msg.file_ids)
          }

          return message
        })
      )

      chatMessages.value = formattedMessages

      // 更新 URL
      router.replace(`/expert/${threadId}`)
    } catch (error) {
      console.error('加载历史对话失败:', error)
      ElMessage.error('加载对话失败')
    }
  }

  /**
   * 清空聊天状态（不导航）
   */
  const clearChatState = () => {
    chatMessages.value = []
    chatInput.value = ''
    chatThreadId.value = null
    activeHistoryId.value = null
    isChatStreaming.value = false
    activeAssistantMessageId.value = null
    uploadedFiles.value = []
  }

  /**
   * 重置对话（新建会话）- 清空状态并导航到 /expert
   */
  const resetChatHistory = () => {
    clearChatState()
    router.replace('/expert')
  }

  /**
   * 处理引用点击
   * 设置 activeReferences 供工作台显示
   */
  const handleCitationClick = (reference: AssistantReference) => {
    console.log('[引用点击]', reference)
    // TODO: 在后续版本中实现引用显示逻辑
  }

  // ===== 组件挂载时加载历史消息 =====
  onMounted(() => {
    loadMessagesFromUrl()
  })

  // ===== 组件卸载时清理 =====
  onUnmounted(() => {
    stopSSEStream()
  })

  return {
    // ===== 状态 =====
    chatMessages,
    chatInput,
    setChatInput: (value: string) => { chatInput.value = value },
    isChatStreaming,
    activeAssistantMessageId,
    conversationHistory,
    activeHistoryId,
    setActiveHistoryId: (value: string | null) => { activeHistoryId.value = value },
    isHistoryCollapsed,
    setIsHistoryCollapsed: (value: boolean) => { isHistoryCollapsed.value = value },
    isHistoryLoading,
    chatThreadId,
    historyRefreshTrigger,
    uploadedFiles,
    authError,

    // ===== Refs =====
    chatContainerRef,

    // ===== 函数 =====
    sendChatMessage,
    stopChatStreaming,
    loadConversationHistory,
    switchToThread,
    resetChatHistory,
    clearChatState,
    createNewThread,
    handleFileUpload,
    removeUploadedFile,
    clearUploadedFiles,
    handleCitationClick,
  }
}

export default useExpertAssistant
