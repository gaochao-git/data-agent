<script setup lang="ts">
/**
 * ChatData 会话页。
 * 左侧展示流式消息与工具调用，右侧展示当前工具结果和工作区产物预览。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, toRef, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { ChatDataContextCompressionEvent, ChatDataTokenUsage, DashboardSpecPayload, DataSource, ExecutionTargetOption, LangGraphMessage, Message, MessageAttachment, PendingUserQuestion, SemanticConfigSelectionInfo, SkillInfo, SubagentTrailItem, ThinkingSegment, ToolCallInfo, ToolLifecycleEventData, UserQuestionAnswer } from '../types'
import { useSqlGenerator, type RunDoneStatus } from '../composables/useSqlGenerator'
import { useConversationHistory } from '../composables/useConversationHistory'
import { useDeepThinking } from '../composables/useDeepThinking'
import { useModelOptions } from '../composables/useModelOptions'
import { useSaveToPanel } from '../composables/useSaveToPanel'
import { useSkillCommandMenu } from '../composables/useSkillCommandMenu'
import { answerUserQuestion, cancelConversationRun, cancelSteerMessage, cancelUserQuestion, createDashboardFromSpec, deleteConversationWorkspaceFile, getConversationWorkspaceFileContent, getConversationWorkspaceFiles, getConversationWorkspaceOfficeHtmlPreview, getConversationWorkspaceRawFile, getPendingUserQuestion, importConversationWorkspaceSkill, listSteerMessages, steerConversation, streamRunEvents, updateSteerMessage, uploadFile } from '../api'
import type { Dashboard } from '../api/dashboard'
import type { ChatDataRuntimeOptions, ConversationWorkspaceFile } from '../api'
import QuickQueries, { type FallbackQuery } from './QuickQueries.vue'
import ChartSpecRenderer from './ChartSpecRenderer.vue'
import { normalizeChartType } from '../chartTypes'
import SkillManager from './SkillManager.vue'
import ConversationMessageList from './ConversationMessageList.vue'
import ConversationSidePanel from './ConversationSidePanel.vue'
import ConversationInputBar from './ConversationInputBar.vue'
import ConversationTokenUsage from './ConversationTokenUsage.vue'
import ConversationTurnNavigator from './ConversationTurnNavigator.vue'
import SkillCommandMenu from './SkillCommandMenu.vue'
import PresentedFileArtifacts from './PresentedFileArtifacts.vue'
import MarkdownFilePreview from './MarkdownFilePreview.vue'
import HtmlPagePreview from './HtmlPagePreview.vue'
import SaveToPanelModal from './SaveToPanelModal.vue'
import { parseDateTimeValue, formatLocalDateTime, formatEpochDateTime, hideSemanticConfigSelectionPayloads } from '../utils'
import { extractSemanticParamsFromArgs, getRenderableQueryResultData } from '../utils/semanticTool'
import { admitInboxMessages, reconcileInboxSnapshot, type InboxQueueItem, type InboxQueueType } from '../utils/inboxQueue'
import ToolCallDetailPanel from './ToolCallDetailPanel.vue'
import UserQuestionCard from './UserQuestionCard.vue'
import AnalysisProgress from './AnalysisProgress.vue'
import { deriveAnalysisTodos } from '../utils/analysisTodos'
import { createTopReachTracker } from '../utils/scrollReach'
import { collapseAllDirectories, collectCollapsibleDirectoryPaths, DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES, expandAllDirectories, formatFileSize, isTreeFullyCollapsed, isWorkspaceFileDeletable, isWorkspaceFileDownloadable, syncTruncatedTitle } from '../utils/consoleFileTree'
import { evictWorkspaceFileCache } from '../utils/workspacePreviewCache'

interface Props {
  datasource?: DataSource | null
  conversationId?: string | null
  selectedDatasourceId?: string
  semanticConfigId?: string | null
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
  ensureConversationId?: () => Promise<string | null>
  ensureConversationDatasource?: () => Promise<boolean>
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  selectedModelId?: string
}

const props = withDefaults(defineProps<Props>(), {
  datasource: null,
  semanticConfigId: null,
  semanticConfigSelection: null,
  executionTargets: () => [],
  executionTargetMultiple: true,
  selectedMemberDatasourceIds: () => [],
  selectedModelId: '',
})

const emit = defineEmits<{
  firstMessageStarted: [conversationId: string]
  runStatusChanged: [conversationId: string, status: 'pending' | 'active' | 'cancelling' | 'waiting_user' | null]
  'update:selectedMemberDatasourceIds': [value: string[]]
  'update:selectedModelId': [value: string]
  dashboardCreated: [dashboard: Dashboard]
  semanticConfigSelected: [payload: SemanticConfigSelectionInfo]
  /** 控制台面板可见性广播：页面右上角的控制台开关按钮据此切换图标与提示。 */
  consoleVisibilityChanged: [visible: boolean]
}>()

const { generateSqlStream, stopGeneration } = useSqlGenerator()

const userOverridable = ref(false)
const defaultRuntimeOptions = ref<ChatDataRuntimeOptions>({ workspace_backend: 'local', bash_enabled: false })
const selectedRuntimeOptions = ref({ ...defaultRuntimeOptions.value })
// 模型列表与运行时配置：与其他入口共用同一份加载逻辑
const {
  modelOptions,
  config: chatDataConfig,
  load: loadModelOptions,
} = useModelOptions()
const selectedModel = computed({
  get: () => props.selectedModelId || '',
  set: (value: string) => emit('update:selectedModelId', value),
})
const { enableDeepThinking, thinkingEffort } = useDeepThinking()

const messages = ref<Message[]>([])
const input = ref('')
const inputFocused = ref(false)
const isGenerating = ref(false)
const isSteering = ref(false)
const admittedInboxMessageIds = ref<Set<string>>(new Set())
const editingSteerId = ref<string | null>(null)
const editingSteerDraft = ref('')
const steerActionBusy = ref(false)
const hasActiveRun = ref(false)
const activeRunStatus = ref<string | null>(null)
const activeRunTurnId = ref<string | null>(null)
const activeRunIsContinuation = ref(false)
const ownedRunStorageKey = 'chatdata:active-run-conversation'
const activeRunId = ref<string | null>(null)
const activeRunEventId = ref('0-0')
const activeRunResumeRunId = ref<string | null>(null)
let steerRestoreTimer: ReturnType<typeof setInterval> | null = null
const pendingSteerMessages = ref<InboxQueueItem[]>([])
const pendingUserQuestion = ref<PendingUserQuestion | null>(null)
const isSubmittingQuestion = ref(false)
const isStoppingRun = ref(false)
const executingQueueMessageId = ref<string | null>(null)
const tokenUsage = ref<ChatDataTokenUsage | null>(null)
const tokenUsagePanelRef = ref<InstanceType<typeof ConversationTokenUsage> | null>(null)
const hasLocalConversationStarted = ref(false)
const chatContainerRef = ref<HTMLDivElement | null>(null)

/**
 * Run 是否处于"执行中"的统一视图：本地 SSE 订阅（isGenerating）与
 * 刷新/SSE 断开后从服务端恢复的活跃 Run（hasActiveRun）必须表现一致，
 * 否则会出现消息流显示"正在生成"、输入栏却退回发送态的割裂。
 * waiting_user 是 Run 在等用户回答问题，不算执行中。
 */
const runBusy = computed(() =>
  isGenerating.value
  || (hasActiveRun.value && activeRunStatus.value !== 'waiting_user')
)
const inputBarRef = ref<InstanceType<typeof ConversationInputBar> | null>(null)
const focusInput = () => {
  inputBarRef.value?.focus()
}

const admitPendingMessages = (messageIds: string[]) => {
  if (!messageIds.length) return
  const active = activeAssistantMessageId.value
    ? messages.value.find(item => item.id === activeAssistantMessageId.value)
    : null
  if (active?.role === 'assistant') active.isStreaming = false
  activeAssistantMessageId.value = null
  const result = admitInboxMessages(pendingSteerMessages.value, messages.value, messageIds)
  pendingSteerMessages.value = result.queue
  messages.value = result.messages
  for (const messageId of messageIds) {
    admittedInboxMessageIds.value.delete(messageId)
    if (editingSteerId.value === messageId) cancelEditSteer()
  }
}

/**
 * done 已经代表后端完成了当前 Run 的收口。这里仅同步本地 UI，不能再用
 * GET /steer 重新确认同一个终态；下一轮由 done.next_run_id 直接订阅。
 */
const applyRunDoneState = (
  conversationId: string,
  status: RunDoneStatus,
  nextRunId: string | null,
  currentRunId: string | null,
) => {
  stopSteerRestorePolling()
  isStoppingRun.value = false

  if (status === 'finished') {
    finalizeUnfinishedToolCalls('completed')
    finishStreamingMessages()
    scheduleWorkspaceFilesRefresh()
  } else if (status === 'cancelled') {
    finalizeUnfinishedToolCalls('error', '已停止')
    finishStreamingMessages()
    activeAssistantMessageId.value = null
  } else if (status === 'waiting_user') {
    finishStreamingMessages()
  }

  if (status === 'finished' && nextRunId) {
    hasActiveRun.value = true
    activeRunId.value = nextRunId
    activeRunStatus.value = 'pending'
    activeRunTurnId.value = null
    activeRunIsContinuation.value = true
    activeRunEventId.value = '0-0'
    activeRunResumeRunId.value = nextRunId
    saveOwnedRun(conversationId, nextRunId)
    emit('runStatusChanged', conversationId, 'pending')
    return
  }

  if (status === 'waiting_user') {
    hasActiveRun.value = true
    activeRunStatus.value = 'waiting_user'
    emit('runStatusChanged', conversationId, 'waiting_user')
    return
  }

  // failed/cancelled 会把未确认消费的消息退回 pending；本地队列已有这些
  // 消息时直接更新状态，避免为展示状态再发一次网络请求。
  if (status === 'failed' || status === 'cancelled') {
    pendingSteerMessages.value = pendingSteerMessages.value.map(item => (
      item.status === 'claimed' ? { ...item, status: 'pending' } : item
    ))
  }
  hasActiveRun.value = false
  activeRunId.value = null
  activeRunStatus.value = null
  activeRunTurnId.value = null
  activeRunIsContinuation.value = false
  activeRunEventId.value = '0-0'
  activeRunResumeRunId.value = null
  clearOwnedRun(currentRunId || undefined)
  emit('runStatusChanged', conversationId, null)
}

const startEditSteerMessage = (item: { content: string; messageId?: string }) => {
  if (!item.messageId || editingSteerId.value === item.messageId) return
  editingSteerId.value = item.messageId
  editingSteerDraft.value = item.content
}

/** 以服务端状态和持久历史共同恢复队列，不以 pending 暂时缺失作为删除依据。 */
const restorePendingSteerQueue = async (options?: {
  restartPolling?: boolean
  preserveResumeCursor?: boolean
  applyTerminal?: boolean
}) => {
  const conversationId = localConversationId.value || props.conversationId
  if (!conversationId) return
  try {
    const response = await listSteerMessages(conversationId)
    const serverMessages = response.data?.messages ?? []
    hasActiveRun.value = Boolean(response.data?.has_active_run)
    activeRunId.value = response.data?.run_id || null
    activeRunStatus.value = response.data?.run_status ?? null
    activeRunTurnId.value = response.data?.turn_id || null
    activeRunIsContinuation.value = Boolean(response.data?.is_continuation)
    emit(
      'runStatusChanged',
      conversationId,
      hasActiveRun.value
        ? activeRunStatus.value as 'pending' | 'active' | 'cancelling' | 'waiting_user' | null
        : null,
    )
    if (activeRunId.value) {
      if (activeRunResumeRunId.value !== activeRunId.value || !options?.preserveResumeCursor) {
        activeRunEventId.value = response.data?.history_checkpoint?.stream_id || '0-0'
        activeRunResumeRunId.value = activeRunId.value
      }
      if (activeRunTurnId.value && activeRunResumeRunId.value === activeRunId.value && activeRunEventId.value === '0-0') {
        messages.value = messages.value.filter(message => (
          message.turnId !== activeRunTurnId.value || message.role === 'user'
        ))
        syncHistoryToolCalls()
      }
    } else {
      activeRunEventId.value = '0-0'
      activeRunResumeRunId.value = null
      activeRunTurnId.value = null
    }
    const persistedMessageIds = new Set(messages.value.filter(item => item.role === 'user').map(item => item.id))
    const reconciled = reconcileInboxSnapshot(
      pendingSteerMessages.value,
      serverMessages,
      persistedMessageIds,
      hasActiveRun.value,
    )
    pendingSteerMessages.value = reconciled.queue
    admitPendingMessages(reconciled.admittedMessageIds)
    reconcileOwnedRunMarker(conversationId)
    const terminalStatus = response.data?.run_status
    if (
      options?.applyTerminal
      && !hasActiveRun.value
      && terminalStatus
      && ['finished', 'failed', 'cancelled', 'waiting_user'].includes(terminalStatus)
    ) {
      applyRunDoneState(
        conversationId,
        terminalStatus as RunDoneStatus,
        response.data?.next_run_id || null,
        response.data?.run_id || null,
      )
    }
  } catch {
    // 恢复失败保持本地现状，不打断用户操作
  }
  if (hasActiveRun.value && !isGenerating.value && (options?.restartPolling ?? true)) {
    startSteerRestorePolling()
  }
}

const restorePendingUserQuestion = async () => {
  const conversationId = localConversationId.value || props.conversationId
  if (!conversationId) return
  try {
    const response = await getPendingUserQuestion(conversationId)
    pendingUserQuestion.value = response.data || null
    if (pendingUserQuestion.value && !activeAssistantMessageId.value) {
      const lastAssistant = [...messages.value].reverse().find(item => item.role === 'assistant')
      if (lastAssistant) activeAssistantMessageId.value = lastAssistant.id
    }
  } catch {
    // 恢复失败保留当前卡片，避免网络抖动导致问题消失。
  }
}

const recoverInterruptedPageRun = async () => {
  while (isLoadingHistory.value) {
    await new Promise(resolve => window.setTimeout(resolve, 50))
  }
  await restorePendingSteerQueue({ restartPolling: false, preserveResumeCursor: true })
  const conversationId = localConversationId.value || props.conversationId
  if (!conversationId || !hasActiveRun.value || !activeRunId.value) return
  if (activeRunStatus.value === 'waiting_user' || activeRunStatus.value === 'cancelling') return
  const runId = activeRunId.value
  await submitAnalysisQuery('', {
      appendUserMessage: false,
      preserveAssistant: !activeRunIsContinuation.value,
    responseFactory: signal => streamRunEvents(
      conversationId,
      runId,
      activeRunEventId.value,
      signal,
    ),
  })
}

const readOwnedRun = (): { conversationId: string; runId: string } | null => {
  const raw = window.sessionStorage.getItem(ownedRunStorageKey)
  if (!raw) return null
  try {
    const value = JSON.parse(raw)
    if (typeof value?.conversationId === 'string' && typeof value?.runId === 'string') return value
  } catch {
    // 清理旧版本只保存 conversationId 的标记，避免误取消新 Run。
  }
  window.sessionStorage.removeItem(ownedRunStorageKey)
  return null
}

const saveOwnedRun = (conversationId: string, runId: string) => {
  window.sessionStorage.setItem(ownedRunStorageKey, JSON.stringify({ conversationId, runId }))
}

const clearOwnedRun = (runId?: string) => {
  const owned = readOwnedRun()
  if (!runId || owned?.runId === runId) window.sessionStorage.removeItem(ownedRunStorageKey)
}

const reconcileOwnedRunMarker = (conversationId: string) => {
  const owned = readOwnedRun()
  if (!owned || owned.conversationId !== conversationId) return
  if (activeRunId.value !== owned.runId || !hasActiveRun.value) clearOwnedRun(owned.runId)
}

const stopSteerRestorePolling = () => {
  if (steerRestoreTimer) {
    window.clearInterval(steerRestoreTimer)
    steerRestoreTimer = null
  }
}

/** 刷新页面后模型仍在运行时，轮询同步队列消费状态。 */
const startSteerRestorePolling = () => {
  stopSteerRestorePolling()
  steerRestoreTimer = window.setInterval(() => {
    if (!hasActiveRun.value || isGenerating.value) {
      stopSteerRestorePolling()
      return
    }
    // 仅用于页面恢复或 SSE 异常断线。若 Run 已收口，快照中的
    // MySQL Run 终态必须驱动与实时 done 相同的 UI 收口。
    restorePendingSteerQueue({ restartPolling: false, applyTerminal: true })
  }, 5000)
}

const saveEditSteerMessage = async (item: { messageId?: string; content: string }) => {
  const conversationId = localConversationId.value || props.conversationId
  if (!item.messageId || !conversationId || steerActionBusy.value) return
  const draft = editingSteerDraft.value.trim()
  if (!draft) {
    ElMessage.warning('消息内容不能为空')
    return
  }
  steerActionBusy.value = true
  try {
    const response = await updateSteerMessage(conversationId, item.messageId, { content: draft })
    if (response.data?.content) item.content = response.data.content
    editingSteerId.value = null
    ElMessage.success('消息已更新')
  } catch (error) {
    // 以系统为准：消息可能已被取走，同步服务端队列状态
    restorePendingSteerQueue()
  } finally {
    steerActionBusy.value = false
  }
}

const cancelEditSteer = () => {
  editingSteerId.value = null
  editingSteerDraft.value = ''
}

const toggleSteerMessageQueueType = async (item: { messageId?: string; queueType: InboxQueueType }) => {
  const conversationId = localConversationId.value || props.conversationId
  if (!item.messageId || !conversationId || steerActionBusy.value) return
  const nextType = item.queueType === 'next-step' ? 'next-turn' : 'next-step'
  steerActionBusy.value = true
  try {
    const response = await updateSteerMessage(conversationId, item.messageId, { queueType: nextType })
    if (response.data?.queue_type) item.queueType = response.data.queue_type
    ElMessage.success(nextType === 'next-step' ? '已切换为立即插入' : '已切换为下一轮处理')
  } catch (error) {
    restorePendingSteerQueue()
  } finally {
    steerActionBusy.value = false
  }
}

const removePendingSteerMessage = async (item: { clientId: string; messageId?: string }) => {
  const conversationId = localConversationId.value || props.conversationId
  if (steerActionBusy.value || !conversationId) return
  if (!item.messageId) {
    pendingSteerMessages.value = pendingSteerMessages.value.filter(entry => entry.clientId !== item.clientId)
    return
  }
  steerActionBusy.value = true
  try {
    await cancelSteerMessage(conversationId, item.messageId)
    pendingSteerMessages.value = pendingSteerMessages.value.filter(entry => entry.clientId !== item.clientId)
    if (editingSteerId.value === item.messageId) cancelEditSteer()
  } catch (error) {
    restorePendingSteerQueue()
  } finally {
    steerActionBusy.value = false
  }
}

onMounted(() => {
  focusInput()
  document.addEventListener('fullscreenchange', syncWorkspaceFilePreviewFullscreen)
  void recoverInterruptedPageRun()
  restorePendingUserQuestion()
})

onUnmounted(() => {
  document.removeEventListener('fullscreenchange', syncWorkspaceFilePreviewFullscreen)
  stopSteerRestorePolling()
  // 卸载时中止主 SSE 流:useSqlGenerator 对 AbortError 静默返回,不会误报错误
  stopGeneration()
})
const isNearBottom = ref(true)

// ---- 文件上传 ----
const ACCEPTED_FILE_TYPES = ['.txt', '.md', '.sql', '.docx', '.xlsx', '.pptx', '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif']
const MAX_FILE_SIZE = 50 * 1024 * 1024
// 文本预览渲染上限：后端文本预览源上限为 5MB，但超大文本全量进 DOM（JSON 还要 parse+stringify）会卡死页面，
// 超限只渲染前 1MB 并提示下载查看（对齐 Office 预览「源 50MB / 渲染 12MB」的双层设计）。
const CONSOLE_TEXT_RENDER_MAX_CHARS = 1 * 1024 * 1024
// 用户输入超过该字符数时自动转为 .md 文件附件上传，避免上下文过长
const MAX_INPUT_CHARS = 4000
interface PendingFile {
  file_id: string
  filename: string
  status: 'uploading' | 'uploaded' | 'failed'
  file_type?: string
  file_size?: number
  // 上传后实际存储文件名（去重后可能与 filename 不同），用于拼装可访问路径
  storage_dir?: string
}
const pendingFiles = ref<PendingFile[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)

const hasParsingFiles = computed(() => pendingFiles.value.some(f => f.status === 'uploading'))
const hasCompletedPendingFiles = computed(() => pendingFiles.value.some(f => f.status === 'uploaded'))

// 分析任务计划：从消息流中的 write_todos 派生，实时流与历史回放同源；出现后常驻本会话
const analysisTodos = computed(() => deriveAnalysisTodos(messages.value))

const handleUploadFile = () => {
  fileInputRef.value?.click()
}

const handleFileSelected = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''

  const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
  if (!ACCEPTED_FILE_TYPES.includes(ext)) {
    ElMessage.warning(`仅支持 ${ACCEPTED_FILE_TYPES.join('、')} 格式`)
    return
  }
  if (file.size > MAX_FILE_SIZE) {
    ElMessage.warning('文件大小不能超过 50MB')
    return
  }

  try {
    let convId = props.conversationId || localConversationId.value
    if (!convId) {
      convId = (await props.ensureConversationId?.()) ?? null
      if (!convId) {
        ElMessage.error('创建会话失败，请稍后重试')
        return
      }
      localConversationId.value = convId
      // 刚创建的新会话无历史，标记已加载，避免触发历史加载导致 isLoadingHistory 闪烁
      loadedConversationId.value = convId
    }
    const res = await uploadFile(convId, file)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '上传失败')
      return
    }
    const pending: PendingFile = {
      file_id: res.data.file_id,
      filename: res.data.original_filename,
      status: 'uploaded',
      file_type: res.data.file_type,
      file_size: res.data.file_size,
      storage_dir: res.data.storage_dir,
    }
    pendingFiles.value.push(pending)
    scheduleWorkspaceFilesRefresh()
    // 上传即登记：不再预解析/链接到工作区，agent 在需要时通过 parse_document 工具按需解析
  } catch {
    ElMessage.error('文件上传失败')
  }
}

const removePendingFile = (fileId: string) => {
  pendingFiles.value = pendingFiles.value.filter(f => f.file_id !== fileId)
}

// 超长用户输入在前端转为 .md 文件，走与手动上传一致的流程
const convertOversizedInputToFile = async (text: string): Promise<boolean> => {
  const timestamp = formatLocalDateTime(new Date()).replace(/[/\\: ]/g, '-')
  const filename = `用户输入-${timestamp}.md`
  const file = new File([text], filename, { type: 'text/markdown' })

  // 立即在 pendingFiles 中显示占位卡片，让用户看到转换效果
  const placeholderId = `temp-${Date.now()}`
  const placeholder: PendingFile = {
    file_id: placeholderId,
    filename,
    status: 'uploading',
  }
  pendingFiles.value.push(placeholder)

  let convId = props.conversationId || localConversationId.value
  if (!convId && props.ensureConversationId) {
    convId = await props.ensureConversationId()
    if (convId) {
      // 新会话无历史，标记已加载，避免历史加载闪烁
      loadedConversationId.value = convId
    }
  }
  if (!convId) {
    // 移除占位卡片
    pendingFiles.value = pendingFiles.value.filter(f => f.file_id !== placeholderId)
    ElMessage.error('无法创建会话')
    return false
  }

  try {
    const res = await uploadFile(convId, file)
    if (res.status !== 'success') {
      pendingFiles.value = pendingFiles.value.filter(f => f.file_id !== placeholderId)
      ElMessage.error(res.msg || '超长输入上传失败')
      return false
    }
    // 用真实的 file_id 替换占位 id，标记已登记（不再等待预解析）
    placeholder.file_id = res.data.file_id
    placeholder.filename = res.data.original_filename
    placeholder.file_type = res.data.file_type
    placeholder.file_size = res.data.file_size
    placeholder.storage_dir = res.data.storage_dir
    placeholder.status = 'uploaded'
    scheduleWorkspaceFilesRefresh()
    return true
  } catch {
    pendingFiles.value = pendingFiles.value.filter(f => f.file_id !== placeholderId)
    ElMessage.error('超长输入上传失败')
    return false
  }
}

// 实时检测：输入超长时自动转为文件附件
const isProcessingOversizedInput = ref(false)

const checkOversizedInput = (text: string) => {
  if (text.length <= MAX_INPUT_CHARS) return
  if (isProcessingOversizedInput.value) return
  handleOversizedInputAuto(text)
}

const handleOversizedInputAuto = async (text: string) => {
  if (isProcessingOversizedInput.value) return
  if (text.length <= MAX_INPUT_CHARS) return
  isProcessingOversizedInput.value = true
  try {
    const success = await convertOversizedInputToFile(text)
    if (success) {
      // 清空输入框，内容已转为文件
      input.value = ''
    }
  } finally {
    isProcessingOversizedInput.value = false
  }
}

type AnalysisSubmitOptions = {
  semanticConfigIdOverride?: string | null
  semanticConfigSelection?: SemanticConfigSelectionInfo | null
  assistantMessageId?: string
  appendUserMessage?: boolean
  fileIds?: string[]
  queuedMessageId?: string | null
  preserveQueuedMessages?: boolean
  userMessageId?: string
  attachments?: MessageAttachment[]
  responseFactory?: (signal: AbortSignal) => Promise<Response>
  preserveAssistant?: boolean
  interruptedQuestion?: PendingUserQuestion
  autoResumeQueuedRun?: boolean
}

const resetTokenUsagePanel = (clearUsage = true) => {
  if (clearUsage) tokenUsage.value = null
  tokenUsagePanelRef.value?.closePanel()
}

const closeTokenUsagePanel = () => {
  tokenUsagePanelRef.value?.closePanel()
}

// 顶层工具调用状态注册表：只存顶层调用。子智能体内层工具不进此 map——
// 它们挂在父 task 调用的 subagentTrail 上（见 upsertSubagentTrailItem），
// 详情查找经 findToolCallById 的 trail 兜底完成。
const toolCallsMap = ref<Map<string, ToolCallInfo>>(new Map())
const activeToolCallId = ref<string | null>(null)
const sidePanelOpen = ref(false)
const sidePanelTab = ref<'console' | 'tool'>('console')
const selectedConsoleFilePath = ref<string | null>(null)
// 默认只收起 uploads / workspace，outputs 保持展开——理由见 consoleFileTree.ts 里的常量注释。
const collapsedConsoleDirectories = ref<Set<string>>(new Set(DEFAULT_COLLAPSED_CONSOLE_DIRECTORIES))
const consoleFilePreviewMode = ref<'render' | 'table' | 'source'>('render')
const workspaceFiles = ref<ConversationWorkspaceFile[]>([])
const workspaceFileContents = ref<Record<string, string>>({})
const workspaceFileBlobs = ref<Record<string, { url: string; arrayBuffer: ArrayBuffer; ext: string; mime: string; error?: boolean }>>({})
const workspaceFilePreviewRef = ref<HTMLElement | null>(null)
const isWorkspaceFilePreviewFullscreen = ref(false)
const selectedConsoleOfficeHtml = ref<string | null>(null)
const selectedConsoleOfficePreviewReason = ref<string | null>(null)
const loadingOfficePreviewKey = ref<string | null>(null)
const workspaceFilePreviewRequestSequence = ref(0)
const loadingOfficePreviewSequence = ref<number | null>(null)
const loadingWorkspaceFileSequence = ref<number | null>(null)
const isWorkspaceFilesLoading = ref(false)
const loadingWorkspaceFilePath = ref<string | null>(null)
const presentedArtifactContents = ref<Record<string, string>>({})
const loadedPresentedArtifactKeys = ref<Set<string>>(new Set())
const loadingPresentedArtifactKeys = ref<Set<string>>(new Set())
const workspaceFilesRefreshTimer = ref<number | null>(null)
const isSkillManagerOpen = ref(false)
const importedSkillIdForManager = ref<string | null>(null)
const importingWorkspaceSkillPath = ref<string | null>(null)
const importedWorkspaceSkillIds = ref<Record<string, string>>({})
const activeAssistantMessageId = ref<string | null>(null)
const localConversationId = ref<string | null>(props.conversationId || null)
const conversationIdRef = toRef(props, 'conversationId')
const creatingDashboardToolCallId = ref<string | null>(null)
const createdDashboardByToolId = ref<Record<string, Dashboard>>({})

const selectedDatasource = computed(() => props.selectedDatasourceId || props.datasource?.id || '')
// 保存到看板：共享弹窗状态与打开逻辑（chartType 为空时回退 'table'）
const {
  showSaveModal,
  saveSql,
  saveChartType,
  saveMemberDatasourceId,
  saveMemberDatasourceIds,
  handleSaveToDashboard,
} = useSaveToPanel({ getFallbackChartType: () => 'table' })
const recommendationPage = ref(0)
const recommendationPageSize = 5
const recommendationOffset = computed(() => recommendationPage.value * recommendationPageSize)
const pushFallbackQuery = (items: FallbackQuery[], seen: Set<string>, title: string, icon = 'fa-message') => {
  const normalizedTitle = String(title || '').trim()
  if (!normalizedTitle || seen.has(normalizedTitle)) return
  seen.add(normalizedTitle)
  items.push({
    id: String(items.length + 1),
    title: normalizedTitle,
    query: normalizedTitle,
    icon,
  })
}
const fallbackConversationQueries = computed<FallbackQuery[]>(() => {
  const items: FallbackQuery[] = []
  const seen = new Set<string>()
  ;(props.datasource?.recommended_questions || []).forEach(question => {
    pushFallbackQuery(items, seen, question)
  })
  return items
})
const canRefreshRecommendations = computed(() => fallbackConversationQueries.value.length > recommendationPageSize)
const refreshRecommendations = () => {
  if (!canRefreshRecommendations.value) return
  const pageCount = Math.ceil(fallbackConversationQueries.value.length / recommendationPageSize)
  recommendationPage.value = (recommendationPage.value + 1) % pageCount
}
const selectedMemberDatasourceIds = computed({
  get: () => props.selectedMemberDatasourceIds,
  set: (value: string[]) => emit('update:selectedMemberDatasourceIds', props.executionTargetMultiple ? value : value.slice(0, 1)),
})
const selectedModelValue = computed(() => modelOptions.value.find(m => m.id === selectedModel.value)?.value || selectedModel.value || null)
const buildSkillRequestText = (text: string, skillName: string) => {
  const normalizedText = String(text || '').trim()
  return skillName ? `使用「${skillName}」技能回答：${normalizedText}` : normalizedText
}
const {
  selectedSkill,
  selectedSkillTitle,
  displayedCommandSkills,
  isCommandSkillLoading,
  highlightedSkillIndex,
  showSkillCommand,
  refreshCommandSkills,
  selectCommandSkill: selectCommandSkillBase,
  clearSelectedSkill: clearSelectedSkillBase,
  handleSkillCommandKeyDown,
} = useSkillCommandMenu({
  input,
  enabled: computed(() => true),
})

const findToolCallById = (id: string | null) => {
  if (!id) return null
  const fromMap = toolCallsMap.value.get(id)
  if (fromMap) return fromMap
  for (const message of messages.value) {
    for (const toolCall of message.toolCalls || []) {
      if (toolCall.tool_call_id === id) return toolCall
      // 子智能体内层工具：挂在父 task 调用的 subagentTrail 上（与
      // ToolCallList 的 chip 构建口径一致），历史恢复后 map 中无内层条目，
      // 详情面板靠这条兜底取数。
      const inner = toolCall.subagentTrail?.find(item => item.kind === 'tool' && item.id === id)
      if (inner) {
        return {
          tool_call_id: inner.id,
          tool_name: inner.tool_name || 'tool_call',
          args: inner.args || {},
          result: inner.result,
          status: inner.status || 'running',
        }
      }
    }
  }
  return null
}

const activeToolCall = computed(() => {
  const id = activeToolCallId.value
  return findToolCallById(id)
})
const getVisibleToolCalls = (toolCalls?: ToolCallInfo[]): ToolCallInfo[] => {
  return toolCalls || []
}
const sidePanelTitle = computed(() => sidePanelTab.value === 'console' ? '工作区' : '工具详情')
// 工作区 tab 不再在头部重复显示根路径——文件树根节点已经写着同一个路径，
// 需要恢复时把下面的 '' 换回 producedFileTree.value.root
const sidePanelSubtitle = computed(() => (
  sidePanelTab.value === 'console'
    ? ''
    : '工具调用参数与结果'
))

const buildSemanticQueryToolRenderInfo = (toolInfo: ToolCallInfo): ToolCallInfo => {
  if (toolInfo.tool_name !== 'semantic_query_tool') return toolInfo
  const result = String(toolInfo.result || '')
  const queryResultData = getRenderableQueryResultData(result)
  const semanticParams = extractSemanticParamsFromArgs(toolInfo.args) || toolInfo.semanticParams
  return {
    ...toolInfo,
    renderId: queryResultData || toolInfo.renderId,
    renderType: queryResultData ? 'queryResult' : toolInfo.renderType,
    semanticParams,
  }
}

const normalizeToolCallForDisplay = (toolInfo: ToolCallInfo): ToolCallInfo => (
  toolInfo.tool_name === 'semantic_query_tool' ? buildSemanticQueryToolRenderInfo(toolInfo) : toolInfo
)

const shouldInlineRenderToolCall = (toolCall?: ToolCallInfo | null): boolean => (
  Boolean(
    toolCall?.tool_name === 'semantic_query_tool' &&
    toolCall.status === 'completed' &&
    toolCall.renderId &&
    toolCall.renderType === 'queryResult'
  )
)

interface ProducedFile {
  path: string
  fullPath: string
  relativePath: string
  displayPath: string
  source: string
  status: string
  /** 字节数，来自工作区列表接口的 st_size；行尾展示用，拿不到时为 0 且不渲染 */
  size: number
  /** 修改时间（秒级 Unix 时间戳，后端 st_mtime）；行尾展示用，拿不到时为 0 且不渲染 */
  mtime: number
}

interface ProducedFileTreeNode {
  name: string
  path: string
  label?: string
  depth: number
  file?: ProducedFile
  children: ProducedFileTreeNode[]
}

interface VisibleProducedFileTreeNode {
  node: ProducedFileTreeNode
  depth: number
  isLast: boolean
}

interface ProducedFileDirectory extends ProducedFileTreeNode {
  label: string
}

const PRODUCED_FILE_DIRECTORIES: Array<{ name: string; label: string }> = [
  { name: 'uploads', label: '用户上传文件' },
  { name: 'workspace', label: '智能体中间文件' },
  { name: 'outputs', label: '智能体输出文件' },
]

const toSandboxDisplayPath = (path: unknown): string => {
  const raw = String(path || '').trim().replace(/\\/g, '/')
  const relativePath = normalizeVisiblePath(raw)
  if (!relativePath) return '/mnt/user-data'
  return `/mnt/user-data/${relativePath}`
}

const pushProducedFile = (files: ProducedFile[], path: string, source: string, status: string, size: number, mtime: number) => {
  const relativePath = normalizeVisiblePath(path)
  const rootDirectory = relativePath.split('/')[0] || ''
  if (!['uploads', 'workspace', 'outputs'].includes(rootDirectory)) return
  const displayPath = relativePath ? toSandboxDisplayPath(relativePath) : ''
  if (!path || !relativePath) return
  files.push({ path, fullPath: displayPath, relativePath, displayPath, source, status, size, mtime })
}

const producedFiles = computed(() => {
  const files: ProducedFile[] = []
  for (const file of workspaceFiles.value) {
    // 列表接口已带 size/mtime（后端 list_workspace_files 里 stat 一次就有），无需额外请求
    pushProducedFile(
      files,
      file.relative_path || file.path,
      file.source || 'workspace',
      'completed',
      Number(file.size) || 0,
      Number(file.mtime) || 0,
    )
  }
  return files
})

const producedFileTree = computed(() => {
  const directoryMap = new Map<string, ProducedFileDirectory>()
  PRODUCED_FILE_DIRECTORIES.forEach((directory) => {
    directoryMap.set(directory.name, {
      ...directory,
      path: directory.name,
      depth: 0,
      children: [],
    })
  })

  const seenPaths = new Set<string>()
  producedFiles.value.forEach((file) => {
    const relativePath = normalizeVisiblePath(file.path)
    if (!relativePath || seenPaths.has(relativePath)) return
    seenPaths.add(relativePath)
    const [directoryName, ...restParts] = relativePath.split('/').filter(Boolean)
    if (!directoryName) return
    if (!directoryMap.has(directoryName)) {
      directoryMap.set(directoryName, {
        name: directoryName,
        label: '其他文件',
        path: directoryName,
        depth: 0,
        children: [],
      })
    }
    const rootDirectory = directoryMap.get(directoryName)!
    let current: ProducedFileTreeNode = rootDirectory
    restParts.forEach((part, index) => {
      const nodePath = [directoryName, ...restParts.slice(0, index + 1)].join('/')
      const isFile = index === restParts.length - 1
      let child = current.children.find(item => item.name === part && item.path === nodePath)
      if (!child) {
        child = {
          name: part,
          path: nodePath,
          depth: index + 1,
          children: [],
        }
        current.children.push(child)
      }
      if (isFile) {
        child.file = {
          ...file,
          path: part,
        }
      }
      current = child
    })
    if (!restParts.length) {
      rootDirectory.file = {
        ...file,
        path: relativePath,
      }
    }
  })

  const sortNodes = (nodes: ProducedFileTreeNode[]) => {
    nodes.sort((left, right) => {
      if (Boolean(left.file) !== Boolean(right.file)) return left.file ? 1 : -1
      return left.name.localeCompare(right.name)
    })
    nodes.forEach(node => sortNodes(node.children))
  }
  directoryMap.forEach(directory => sortNodes(directory.children))

  return {
    root: '/mnt/user-data',
    directories: Array.from(directoryMap.values()),
  }
})

const isConsoleDirectoryCollapsed = (name: string): boolean => collapsedConsoleDirectories.value.has(name)

const setConsoleDirectoryCollapsed = (path: string, collapsed: boolean) => {
  const normalized = normalizeVisiblePath(path)
  const next = new Set(collapsedConsoleDirectories.value)
  if (collapsed) next.add(normalized)
  else next.delete(normalized)
  collapsedConsoleDirectories.value = next
}

const getFileCount = (node: ProducedFileTreeNode): number => {
  return (node.file ? 1 : 0) + node.children.reduce((count, child) => count + getFileCount(child), 0)
}

const isFileTreeNodeCollapsed = (path: string): boolean => collapsedConsoleDirectories.value.has(path)

const toggleConsoleDirectory = (path: string) => {
  const normalized = normalizeVisiblePath(path)
  setConsoleDirectoryCollapsed(normalized, !isFileTreeNodeCollapsed(normalized))
}

// 一键折叠/展开整棵目录树。按钮文案直接由「是否已全部折叠」推导，不另存 UI 状态，
// 这样逐层点击与一键操作共用同一个 collapsedConsoleDirectories，不会出现两边说法不一致。
const consoleTreeCollapsiblePaths = computed(() =>
  collectCollapsibleDirectoryPaths(producedFileTree.value.directories),
)

const isConsoleTreeFullyCollapsed = computed(() =>
  isTreeFullyCollapsed(consoleTreeCollapsiblePaths.value, collapsedConsoleDirectories.value),
)

const toggleConsoleTreeCollapsed = () => {
  const paths = consoleTreeCollapsiblePaths.value
  collapsedConsoleDirectories.value = isConsoleTreeFullyCollapsed.value
    ? expandAllDirectories(paths, collapsedConsoleDirectories.value)
    : collapseAllDirectories(paths, collapsedConsoleDirectories.value)
}

// 两处占位文案（树为空时的「暂无文件」、未选中文件时的「点击工作区文件查看内容。」）
// 被认为冗余——文件数量已在面板头部、树本身空不空一眼可见。这里只隐藏渲染，
// 模板与样式全部保留，需要恢复时把对应常量改回 true 即可。
const SHOW_CONSOLE_EMPTY_STATE = false
const SHOW_CONSOLE_FILE_PLACEHOLDER = false

const selectConsoleFile = (path: string) => {
  const normalizedPath = normalizeVisiblePath(path)
  if (!normalizedPath) return

  const sameFileSelected = selectedConsoleFilePath.value === normalizedPath
  selectedConsoleFilePath.value = normalizedPath

  // Clicking the already selected file does not trigger the path watcher.
  // Keep the current preview visible while explicitly reloading it instead
  // of clearing the content and leaving the panel without a request.
  if (sameFileSelected) {
    void loadWorkspaceFileContent(localConversationId.value, normalizedPath, { forceReload: true })
    return
  }

  selectedConsoleOfficeHtml.value = null
  selectedConsoleOfficePreviewReason.value = null
  loadingOfficePreviewKey.value = null
  consoleFilePreviewMode.value = 'render'
}

const syncWorkspaceFilePreviewFullscreen = () => {
  isWorkspaceFilePreviewFullscreen.value = document.fullscreenElement === workspaceFilePreviewRef.value
}

const toggleWorkspaceFilePreviewFullscreen = async () => {
  const preview = workspaceFilePreviewRef.value
  if (!preview) return
  if (document.fullscreenElement === preview) {
    await document.exitFullscreen()
    return
  }
  await preview.requestFullscreen()
}

const toggleFileTreeNode = (path: string) => {
  const next = new Set(collapsedConsoleDirectories.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  collapsedConsoleDirectories.value = next
}

const flattenFileTreeNodes = (nodes: ProducedFileTreeNode[], depth = 1): VisibleProducedFileTreeNode[] => {
  const visible: VisibleProducedFileTreeNode[] = []
  nodes.forEach((node, index) => {
    visible.push({ node, depth, isLast: index === nodes.length - 1 })
    if (!node.file && node.children.length && !isFileTreeNodeCollapsed(node.path)) {
      visible.push(...flattenFileTreeNodes(node.children, depth + 1))
    }
  })
  return visible
}

// 文件树每级的左缩进。基准 6px 必须等于顶层目录行按钮的 px-1.5，之后每级固定 +18px——
// 两端用同一个基准，「顶层目录 → 它的子节点」这一步的跨度才和更深层一致。
// （此前基准写的是 10px：第一级跨度 22px、后面 18px，居中那一级看着就比上下两级偏右。）
const CONSOLE_TREE_INDENT_BASE_PX = 6
const CONSOLE_TREE_INDENT_STEP_PX = 18
const consoleTreeIndentStyle = (depth: number) => ({
  paddingLeft: `${CONSOLE_TREE_INDENT_BASE_PX + depth * CONSOLE_TREE_INDENT_STEP_PX}px`,
})

// 行首「展开箭头 / 占位」槽位宽度。文件行用 w-3 的空 span（正好 12px），目录行的箭头是 FA 图标——
// FA 7 在 `.fa-solid/.fa-regular` 上有一条 `width: var(--fa-width, 1.25em)`，优先级与 `.w-3` 相同且更靠后，
// 所以 w-3/w-4 这类宽度类在图标上是不生效的：箭头实际宽 1.25em（text-[10px] 时为 12.5px），
// 比文件行宽 0.5px，会让「目录行」的同名列比「文件行」整体右移半像素。这里显式钉成 12px 对齐。
// 箭头字形在盒内左对齐，钉窄后字形本身不动，只有其后的文件夹图标与名字左移 0.5px。
const CONSOLE_TREE_LEAD_SLOT_PX = 12
const consoleTreeLeadSlotStyle = { width: `${CONSOLE_TREE_LEAD_SLOT_PX}px` }

// 行尾「修改时间 / 大小」两列。
// 时间是 YYYY-MM-DD HH:mm:ss（19 字符定长），每行天然等宽，给 shrink-0 就够，不必钉宽度；
// 大小则长度不一（「7 KB」28.8px vs「18.7 KB」44.7px），不钉宽度会把时间列的右边缘顶得忽左忽右，
// 所以按实测最大内容给固定宽度 + 右对齐（字号改动需同步复测）。
//
// 两侧还会在面板被拖窄时整体收起（见 CONSOLE_TREE_META_VISIBILITY_CLASS）：这两列合计约 167px，
// 加上 24px 缩进、33px 占位+图标、56px 行尾操作位与内边距，面板一窄就只剩名字被压成省略号。
// 用容器查询（`@container` 落在树包裹层上）而不是 JS 监听宽度：拖拽改的是行内 width，
// 容器查询会自动响应，且不必因为几像素变化就重渲染整棵树。
const CONSOLE_TREE_META_VISIBILITY_CLASS = 'hidden @min-[400px]:block'
const CONSOLE_TREE_TIME_COLUMN_CLASS = `shrink-0 text-[10px] tabular-nums text-slate-400 ${CONSOLE_TREE_META_VISIBILITY_CLASS}`
const CONSOLE_TREE_SIZE_COLUMN_CLASS = `w-[46px] shrink-0 text-right text-[10px] tabular-nums text-slate-400 ${CONSOLE_TREE_META_VISIBILITY_CLASS}`

const parseToolResultPayload = (result?: string): Record<string, unknown> | null => {
  const text = String(result || '').trim()
  if (!text) return null
  const artifactLine = text.match(/^artifact_payload=(.+)$/m)?.[1]
  if (artifactLine) {
    try {
      const parsed = JSON.parse(artifactLine)
      return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null
    } catch {
      // fall through to normal tool JSON parsing
    }
  }
  const jsonText = text.replace(/^\[[^\]]+\]\s*/, '')
  try {
    const parsed = JSON.parse(jsonText)
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

const getPresentedArtifacts = (tool?: ToolCallInfo | null): Array<Record<string, unknown>> => {
  if (!tool || tool.tool_name !== 'present_artifacts' || tool.status !== 'completed') return []
  const payload = parseToolResultPayload(tool.result)
  const artifacts = payload?.artifacts
  if (!Array.isArray(artifacts)) return []
  return artifacts.filter((item): item is Record<string, unknown> => item !== null && typeof item === 'object' && !Array.isArray(item))
}

const getPresentedArtifactTools = (message: Message, artifactType: string): ToolCallInfo[] => {
  const tools: ToolCallInfo[] = []
  for (const tool of message.toolCalls || []) {
    if (tool.tool_name !== 'present_artifacts') continue
    getPresentedArtifacts(tool).forEach((artifact, index) => {
      if (String(artifact.artifact_type || '').trim() !== artifactType) return
      const filePath = normalizeVisiblePath(artifact.file_path)
      if (!filePath) return
      tools.push({
        ...tool,
        tool_call_id: `${tool.tool_call_id}:${artifactType}:${filePath || index}`,
        result: JSON.stringify({
          ...artifact,
          file_path: filePath,
          presentation_tool_call_id: tool.tool_call_id,
        }),
      })
    })
  }
  return tools
}

type PresentedDeliveryArtifactType = 'document' | 'spreadsheet' | 'presentation' | 'markdown' | 'html'

interface PresentedDeliveryArtifact {
  path: string
  name: string
  artifactType: PresentedDeliveryArtifactType
}

const PRESENTED_DELIVERY_ARTIFACT_TYPES = new Set<PresentedDeliveryArtifactType>([
  'document',
  'spreadsheet',
  'presentation',
  'markdown',
  'html',
])

const getPresentedDeliveryArtifacts = (message: Message): PresentedDeliveryArtifact[] => {
  const artifacts = new Map<string, PresentedDeliveryArtifact>()
  for (const tool of message.toolCalls || []) {
    for (const artifact of getPresentedArtifacts(tool)) {
      const artifactType = String(artifact.artifact_type || '').trim() as PresentedDeliveryArtifactType
      if (!PRESENTED_DELIVERY_ARTIFACT_TYPES.has(artifactType)) continue
      const path = normalizeVisiblePath(artifact.file_path)
      if (!path.startsWith('outputs/')) continue
      const name = path.split('/').pop() || ''
      if (!name) continue
      artifacts.set(path, { path, name, artifactType })
    }
  }
  return Array.from(artifacts.values())
}

const parseDashboardSpecValue = (value: unknown): Record<string, unknown> | null => {
  let parsed = value
  if (typeof parsed === 'string') {
    const text = parsed.trim()
    if (!text) return null
    try {
      parsed = JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim())
    } catch {
      const match = text.match(/\{[\s\S]*\}/)
      if (!match) return null
      try {
        parsed = JSON.parse(match[0])
      } catch {
        return null
      }
    }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const payload = parsed as Record<string, unknown>
  const spec = payload.dashboard_spec && typeof payload.dashboard_spec === 'object' && !Array.isArray(payload.dashboard_spec)
    ? payload.dashboard_spec as Record<string, unknown>
    : payload
  return Array.isArray(spec.cards) ? spec : null
}

const parseJsonLikeValue = (value: unknown): unknown => {
  if (typeof value !== 'string') return value
  const text = value.trim()
  if (!text) return null
  try {
    return JSON.parse(text.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim())
  } catch {
    return value
  }
}

const parseChartSpecValue = (value: unknown): Record<string, unknown> | null => {
  const parsed = parseJsonLikeValue(value)
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const payload = parsed as Record<string, unknown>
  const spec = payload.chart_spec && typeof payload.chart_spec === 'object' && !Array.isArray(payload.chart_spec)
    ? payload.chart_spec as Record<string, unknown>
    : payload
  const data = spec.data
  const encoding = spec.encoding
  const chartType = normalizeChartType(spec.chart_type)
  const yFields = Array.isArray((encoding as Record<string, unknown> | undefined)?.y)
    ? ((encoding as Record<string, unknown>).y as unknown[]).map(item => String(item || '').trim()).filter(Boolean)
    : []
  const graphPayload = data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : spec
  const nodes = Array.isArray(graphPayload.nodes) ? graphPayload.nodes : []
  const links = Array.isArray(graphPayload.links) ? graphPayload.links : Array.isArray(graphPayload.edges) ? graphPayload.edges : []
  if (String(spec.version || '').trim() !== '1.0') return null
  if (chartType === 'graph' && nodes.length > 0 && links.length > 0) return spec
  if (!Array.isArray(data)) return null
  if (!encoding || typeof encoding !== 'object' || Array.isArray(encoding)) return null
  if (['line', 'bar', 'horizontal_bar', 'area', 'pie', 'map', 'combo', 'kpi', 'funnel', 'graph'].includes(chartType) && yFields.length === 0) return null
  if (['line', 'bar', 'horizontal_bar', 'area', 'pie', 'map', 'combo', 'funnel', 'graph'].includes(chartType) && !String((encoding as Record<string, unknown>).x || '').trim()) return null
  return spec
}

const normalizeVisiblePath = (path: unknown): string => {
  let value = String(path || '').trim().replace(/\\/g, '/')
  value = value.replace(/^\/mnt\/user-data\/?/, '')
  value = value.replace(/^\/+/, '')
  return value
}

const replaceVisiblePathsForDisplay = (text: string): string => {
  return String(text || '')
    .replace(/(?<!\/mnt\/user-data)\/(workspace|outputs|uploads)(?=\/|["'\s,}\]]|$)/g, '/mnt/user-data/$1')
}

const isDashboardSpecArtifactTool = (tool: ToolCallInfo): boolean => {
  if (tool.tool_name !== 'present_artifacts') return false
  return getArtifactType(tool) === 'dashboard_spec'
}

const isChartSpecArtifactTool = (tool: ToolCallInfo): boolean => {
  if (tool.tool_name !== 'present_artifacts') return false
  return getArtifactType(tool) === 'chart_spec'
}

const getArtifactType = (tool?: ToolCallInfo | null): string => {
  const payload = parseToolResultPayload(tool?.result)
  return String(payload?.artifact_type || '').trim()
}

const getPresentedArtifactPath = (tool?: ToolCallInfo | null): string => {
  const payload = parseToolResultPayload(tool?.result)
  return normalizeVisiblePath(payload?.file_path)
}

const getPresentedArtifactKey = (tool?: ToolCallInfo | null): string => {
  const filePath = getPresentedArtifactPath(tool)
  const payload = parseToolResultPayload(tool?.result)
  const presentationToolCallId = String(payload?.presentation_tool_call_id || tool?.tool_call_id || '').trim()
  return presentationToolCallId && filePath ? `${presentationToolCallId}:${filePath}` : ''
}

const getPresentedArtifactContent = (tool?: ToolCallInfo | null): string | null => {
  const key = getPresentedArtifactKey(tool)
  return key ? presentedArtifactContents.value[key] ?? null : null
}

const getDashboardSpecFromTool = (tool?: ToolCallInfo | null): DashboardSpecPayload | null => {
  if (!tool) return null
  if (!isDashboardSpecArtifactTool(tool)) return null
  const resultPayload = parseToolResultPayload(tool.result)
  const content = getPresentedArtifactContent(tool)
  const spec = parseDashboardSpecValue(content)
  if (!spec) return null
  const cards = Array.isArray(spec.cards) ? spec.cards : []
  if (cards.length === 0) return null

  const warnings = Array.isArray(resultPayload?.warnings) ? resultPayload.warnings.map(item => String(item)) : []
  const validCount = typeof resultPayload?.valid_count === 'number'
    ? resultPayload.valid_count
    : cards.length
  const specSemanticConfigId = typeof spec.semantic_config_id === 'string' ? spec.semantic_config_id.trim() : ''
  const resultSemanticConfigId = typeof resultPayload?.semantic_config_id === 'string' ? resultPayload.semantic_config_id.trim() : ''
  const resultSemanticConfigIds = Array.isArray(resultPayload?.semantic_config_ids)
    ? resultPayload.semantic_config_ids.map(item => String(item || '').trim()).filter(Boolean)
    : []

  return {
    dashboard_spec: spec,
    validation_warnings: warnings,
    writable_card_count: Math.max(0, validCount),
    datasource_id: typeof resultPayload?.datasource_id === 'string' ? resultPayload.datasource_id : selectedDatasource.value || null,
    semantic_config_id: resultSemanticConfigId || specSemanticConfigId || props.semanticConfigId || null,
    semantic_config_ids: resultSemanticConfigIds,
    conversation_id: props.conversationId || localConversationId.value || null,
  }
}

const getDashboardPreviewTools = (message: Message): ToolCallInfo[] => {
  const previewTools: ToolCallInfo[] = []
  const indexByKey = new Map<string, number>()
  const candidates = getPresentedArtifactTools(message, 'dashboard_spec')
  for (const tool of candidates) {
    const payload = getDashboardSpecFromTool(tool)
    if (!payload) continue
    const title = getDashboardPreviewTitle(payload).trim()
    const cards = getDashboardPreviewCards(payload)
    const fallbackKey = cards.map((card, index) => `${getCardTitle(card, index)}:${getCardChartType(card)}`).join('|')
    const key = title || fallbackKey || tool.tool_call_id
    const existingIndex = indexByKey.get(key)
    if (existingIndex === undefined) {
      indexByKey.set(key, previewTools.length)
      previewTools.push(tool)
    } else {
      previewTools[existingIndex] = tool
    }
  }
  return previewTools
}

const getChartSpecFromTool = (tool?: ToolCallInfo | null): Record<string, unknown> | null => {
  if (!tool || !isChartSpecArtifactTool(tool)) return null
  return parseChartSpecValue(getPresentedArtifactContent(tool))
}

const getChartPreviewTools = (message: Message): ToolCallInfo[] => {
  const previewTools: ToolCallInfo[] = []
  for (const tool of getPresentedArtifactTools(message, 'chart_spec')) {
    const spec = getChartSpecFromTool(tool)
    if (!spec) continue
    previewTools.push(tool)
  }
  return previewTools
}

const getDashboardPreviewCards = (payload: DashboardSpecPayload | null): Array<Record<string, unknown>> => {
  const cards = payload?.dashboard_spec?.cards
  return Array.isArray(cards) ? cards.filter(item => item && typeof item === 'object') as Array<Record<string, unknown>> : []
}

const getDashboardPreviewTitle = (payload: DashboardSpecPayload | null): string => {
  return String(payload?.dashboard_spec?.dashboard_name || payload?.dashboard_spec?.title || '看板预览')
}

const getDashboardPreviewWritableCount = (payload: DashboardSpecPayload | null): number => {
  if (!payload) return 0
  if (typeof payload.writable_card_count === 'number') return payload.writable_card_count
  return getDashboardPreviewCards(payload).length
}

const getDashboardPreviewWarningCount = (payload: DashboardSpecPayload | null): number => {
  return payload?.validation_warnings?.length || 0
}

const getCardTitle = (card: Record<string, unknown>, index: number): string => {
  return String(card.title || card.name || `卡片 ${index + 1}`)
}

const getCardChartType = (card: Record<string, unknown>): string => {
  const labels: Record<string, string> = {
    kpi: '指标卡',
    line: '折线图',
    bar: '柱状图',
    horizontal_bar: '横向柱状图',
    pie: '饼图',
    table: '明细表',
    map: '地图',
    combo: '柱线组合图',
    area: '面积图',
    funnel: '漏斗图',
    graph: '关系图',
  }
  const type = normalizeChartType(card.chart_type || card.type)
  return labels[type] || type
}

const getCardLayoutText = (card: Record<string, unknown>): string => {
  const layout = card.layout
  if (!layout || typeof layout !== 'object' || Array.isArray(layout)) return ''
  const item = layout as Record<string, unknown>
  const x = Number(item.x ?? 0)
  const y = Number(item.y ?? 0)
  const w = Number(item.w ?? 0)
  const h = Number(item.h ?? 0)
  if ([x, y, w, h].some(value => Number.isNaN(value))) return ''
  return `${x},${y} / ${w}x${h}`
}

const selectedConsoleFile = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return null
  return producedFiles.value.find(file => file.relativePath === selectedPath) || null
})

const selectedWorkspaceFile = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath) return null
  return workspaceFiles.value.find(file => normalizeVisiblePath(file.relative_path || file.path) === selectedPath) || null
})

const handleImportWorkspaceSkill = async (path: string) => {
  const conversationId = localConversationId.value
  const skillDirPath = normalizeVisiblePath(path)
  if (!conversationId || !skillDirPath) return
  importingWorkspaceSkillPath.value = skillDirPath
  try {
    const response = await importConversationWorkspaceSkill(conversationId, { skill_dir_path: skillDirPath })
    const skill = response.data
    const skillId = skill?.skill_id || skill?.name || ''
    importedWorkspaceSkillIds.value = {
      ...importedWorkspaceSkillIds.value,
      [skillDirPath]: skillId,
    }
    importedSkillIdForManager.value = skillId || null
    await refreshCommandSkills()
    isSkillManagerOpen.value = true
    ElMessage.success('技能已导入，可在技能管理中继续编辑')
  } catch (err: any) {
    ElMessage.error(err?.response?.data?.msg || '导入技能失败')
  } finally {
    importingWorkspaceSkillPath.value = null
  }
}

const loadWorkspaceFiles = async (conversationId: string | null | undefined) => {
  if (!conversationId) {
    workspaceFiles.value = []
    Object.values(workspaceFileBlobs.value).forEach(b => { if (b.url) URL.revokeObjectURL(b.url) })
    workspaceFileBlobs.value = {}
    workspaceFileContents.value = {}
    selectedConsoleOfficeHtml.value = null
    selectedConsoleOfficePreviewReason.value = null
    loadingOfficePreviewKey.value = null
    return
  }
  isWorkspaceFilesLoading.value = true
  try {
    const response = await getConversationWorkspaceFiles(conversationId)
    if (conversationId !== localConversationId.value) return
    workspaceFiles.value = response.data?.files || []
  } catch (err) {
    if (conversationId !== localConversationId.value) return
    workspaceFiles.value = []
    console.error('加载智能分析工作区文件失败:', err)
  } finally {
    if (conversationId === localConversationId.value) {
      isWorkspaceFilesLoading.value = false
    }
  }
}

const scheduleWorkspaceFilesRefresh = () => {
  if (!localConversationId.value) return
  if (workspaceFilesRefreshTimer.value !== null) {
    window.clearTimeout(workspaceFilesRefreshTimer.value)
  }
  workspaceFilesRefreshTimer.value = window.setTimeout(() => {
    workspaceFilesRefreshTimer.value = null
    loadWorkspaceFiles(localConversationId.value)
  }, 500)
}

// 手动刷新文件列表（预览区头部按钮）。tool_result 与 run 结束都会自动刷新，
// 这里只是给用户一个兜底入口。传 localConversationId 与自动刷新保持一致：
// loadWorkspaceFiles 在 id 不匹配时会丢弃结果且不复位 loading 态。
const refreshWorkspaceFiles = () => {
  if (!localConversationId.value) return
  void loadWorkspaceFiles(localConversationId.value)
}

const OFFICE_HTML_PREVIEW_EXTENSIONS = new Set(['docx', 'xlsx', 'pptx'])
const IMAGE_PREVIEW_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'])
const IMAGE_PREVIEW_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  svg: 'image/svg+xml',
}
const BINARY_PREVIEW_EXTENSIONS = new Set(['pdf', ...OFFICE_HTML_PREVIEW_EXTENSIONS, ...IMAGE_PREVIEW_EXTENSIONS])
const isBinaryPreviewPath = (path: string) => {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  return BINARY_PREVIEW_EXTENSIONS.has(ext)
}

type WorkspaceFileLoadOptions = {
  forceReload?: boolean
}

// 超过渲染上限的文本只保留前 CONSOLE_TEXT_RENDER_MAX_CHARS 字符并附提示（下载路径不受影响）。
const capWorkspaceTextPreview = (content: string) => {
  if (content.length <= CONSOLE_TEXT_RENDER_MAX_CHARS) return content
  return `${content.slice(0, CONSOLE_TEXT_RENDER_MAX_CHARS)}\n\n...（文件过大，仅显示前 1MB，完整内容请下载查看）...`
}

const loadWorkspaceFileContent = async (
  conversationId: string | null | undefined,
  path: string | null,
  options: WorkspaceFileLoadOptions = {},
) => {
  if (!conversationId || !path) return
  const ext = path.split('.').pop()?.toLowerCase() || ''
  const officeRequestKey = `${conversationId}:${path}`
  if (
    !options.forceReload
    && OFFICE_HTML_PREVIEW_EXTENSIONS.has(ext)
    && loadingOfficePreviewKey.value === officeRequestKey
    && loadingOfficePreviewSequence.value === workspaceFilePreviewRequestSequence.value
  ) return
  const requestSequence = ++workspaceFilePreviewRequestSequence.value
  const refreshToken = options.forceReload ? `${Date.now()}-${requestSequence}` : undefined
  if (isBinaryPreviewPath(path)) {
    if (OFFICE_HTML_PREVIEW_EXTENSIONS.has(ext)) {
      await loadWorkspaceOfficeHtmlPreview(
        conversationId,
        path,
        requestSequence,
        options.forceReload === true,
      )
    } else {
      await loadWorkspaceBinaryFile(
        conversationId,
        path,
        requestSequence,
        options.forceReload === true,
        refreshToken,
      )
    }
    return
  }
  if (!options.forceReload && workspaceFileContents.value[path] !== undefined) return
  loadingWorkspaceFilePath.value = path
  loadingWorkspaceFileSequence.value = requestSequence
  try {
    const response = await getConversationWorkspaceFileContent(conversationId, path, refreshToken)
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    workspaceFileContents.value = {
      ...workspaceFileContents.value,
      [path]: capWorkspaceTextPreview(response.data?.content || ''),
    }
  } catch (err) {
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    if (!options.forceReload || workspaceFileContents.value[path] === undefined) {
      workspaceFileContents.value = {
        ...workspaceFileContents.value,
        [path]: '文件内容加载失败。',
      }
    }
    console.error('加载智能分析工作区文件内容失败:', err)
  } finally {
    if (loadingWorkspaceFileSequence.value === requestSequence) {
      loadingWorkspaceFilePath.value = null
      loadingWorkspaceFileSequence.value = null
    }
  }
}

const isCurrentWorkspaceFilePreviewRequest = (
  conversationId: string,
  path: string,
  requestSequence: number,
) => (
  requestSequence === workspaceFilePreviewRequestSequence.value
  && conversationId === localConversationId.value
  && path === selectedConsoleFilePath.value
)

const loadWorkspaceOfficeHtmlPreview = async (
  conversationId: string,
  path: string,
  requestSequence: number,
  forceReload = false,
) => {
  const requestKey = `${conversationId}:${path}`
  const refreshToken = forceReload ? `${Date.now()}-${requestSequence}` : undefined
  loadingOfficePreviewKey.value = requestKey
  loadingOfficePreviewSequence.value = requestSequence
  try {
    const response = await getConversationWorkspaceOfficeHtmlPreview(conversationId, path, refreshToken)
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    const preview = response.data
    selectedConsoleOfficeHtml.value = preview?.available && !preview.truncated && preview.html
      ? preview.html
      : null
    selectedConsoleOfficePreviewReason.value = selectedConsoleOfficeHtml.value
      ? null
      : (preview?.reason || 'preview_unavailable')
  } catch (err) {
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    if (!forceReload || !selectedConsoleOfficeHtml.value) {
      selectedConsoleOfficeHtml.value = null
      selectedConsoleOfficePreviewReason.value = 'preview_failed'
    }
    console.warn('OfficeCLI 文件预览不可用:', err)
  } finally {
    if (loadingOfficePreviewSequence.value === requestSequence) {
      loadingOfficePreviewKey.value = null
      loadingOfficePreviewSequence.value = null
    }
  }
}

const loadWorkspaceBinaryFile = async (
  conversationId: string,
  path: string,
  requestSequence: number,
  forceReload = false,
  refreshToken?: string,
) => {
  if (!forceReload && workspaceFileBlobs.value[path] !== undefined) return
  loadingWorkspaceFilePath.value = path
  loadingWorkspaceFileSequence.value = requestSequence
  try {
    const blob = await getConversationWorkspaceRawFile(conversationId, path, refreshToken)
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    const arrayBuffer = await blob.arrayBuffer()
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    const ext = path.split('.').pop()?.toLowerCase() || ''
    const mime = IMAGE_PREVIEW_MIME[ext] || blob.type || 'application/octet-stream'
    const url = URL.createObjectURL(new Blob([arrayBuffer], { type: mime }))
    const previousUrl = workspaceFileBlobs.value[path]?.url
    if (previousUrl) URL.revokeObjectURL(previousUrl)
    workspaceFileBlobs.value = {
      ...workspaceFileBlobs.value,
      [path]: { url, arrayBuffer, ext, mime },
    }
  } catch (err) {
    if (!isCurrentWorkspaceFilePreviewRequest(conversationId, path, requestSequence)) return
    if (!forceReload || workspaceFileBlobs.value[path] === undefined) {
      workspaceFileBlobs.value = {
        ...workspaceFileBlobs.value,
        [path]: { url: '', arrayBuffer: new ArrayBuffer(0), ext: '', mime: '', error: true },
      }
    }
    console.error('加载工作区二进制文件失败:', err)
  } finally {
    if (loadingWorkspaceFileSequence.value === requestSequence) {
      loadingWorkspaceFilePath.value = null
      loadingWorkspaceFileSequence.value = null
    }
  }
}

const loadPresentedArtifactContent = async (conversationId: string | null | undefined, tool: ToolCallInfo) => {
  const key = getPresentedArtifactKey(tool)
  const path = getPresentedArtifactPath(tool)
  if (
    !conversationId
    || !key
    || !path
    || loadedPresentedArtifactKeys.value.has(key)
    || loadingPresentedArtifactKeys.value.has(key)
  ) return
  loadingPresentedArtifactKeys.value.add(key)
  let loaded = false
  try {
    const response = await getConversationWorkspaceFileContent(conversationId, path)
    if (conversationId !== localConversationId.value) return
    presentedArtifactContents.value = {
      ...presentedArtifactContents.value,
      [key]: response.data?.content || '',
    }
    loaded = true
  } catch (err) {
    if (conversationId !== localConversationId.value) return
    console.error('加载呈现产物内容失败:', err)
  } finally {
    if (conversationId !== localConversationId.value) return
    if (loaded) loadedPresentedArtifactKeys.value.add(key)
    loadingPresentedArtifactKeys.value.delete(key)
  }
}

const loadPresentedArtifacts = (conversationId: string | null | undefined) => {
  if (!conversationId) return
  for (const message of messages.value) {
    if (message.role !== 'assistant') continue
    for (const tool of message.toolCalls || []) {
      if (tool.status !== 'completed' || tool.tool_name !== 'present_artifacts') continue
      getPresentedArtifacts(tool).forEach((artifact, index) => {
        const artifactType = String(artifact.artifact_type || '').trim()
        if (!['dashboard_spec', 'chart_spec'].includes(artifactType)) return
        const filePath = normalizeVisiblePath(artifact.file_path)
        if (!filePath) return
        const artifactTool: ToolCallInfo = {
          ...tool,
          tool_call_id: `${tool.tool_call_id}:${artifactType}:${filePath || index}`,
          result: JSON.stringify({
            ...artifact,
            file_path: filePath,
            presentation_tool_call_id: tool.tool_call_id,
          }),
        }
        void loadPresentedArtifactContent(conversationId, artifactTool)
      })
    }
  }
}

const selectedConsoleFileExt = computed(() => {
  const p = selectedConsoleFilePath.value || ''
  return p.split('.').pop()?.toLowerCase() || ''
})
const selectedConsoleIsPdf = computed(() => selectedConsoleFileExt.value === 'pdf')
const selectedConsoleIsOfficeFile = computed(() => OFFICE_HTML_PREVIEW_EXTENSIONS.has(selectedConsoleFileExt.value))
const selectedConsoleIsImage = computed(() => IMAGE_PREVIEW_EXTENSIONS.has(selectedConsoleFileExt.value))
const selectedConsoleIsMarkdown = computed(() => selectedConsoleFileExt.value === 'md')
const selectedConsoleIsHtml = computed(() => selectedConsoleFileExt.value === 'html' || selectedConsoleFileExt.value === 'htm')
// HTML 渲染必须用原始文本：replaceVisiblePathsForDisplay 会改写数据里的模型可见路径，
// 破坏内联 JSON 数据与代码。仅源码视图走替换后的展示文案。
const selectedConsoleHtmlDocument = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath || !selectedConsoleIsHtml.value) return ''
  return workspaceFileContents.value[selectedPath] ?? ''
})
const workspaceFilePreviewHeightClass = computed(() => (
  isWorkspaceFilePreviewFullscreen.value ? 'h-[calc(100vh-48px)]' : 'h-[46vh]'
))
const selectedConsoleBinaryBlob = computed(() => {
  const p = selectedConsoleFilePath.value
  if (!p) return null
  return workspaceFileBlobs.value[p] || null
})

const selectedConsoleFileText = computed(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath || !selectedWorkspaceFile.value) return ''
  if (loadingWorkspaceFilePath.value === selectedPath) return '文件内容加载中...'
  const content = workspaceFileContents.value[selectedPath] ?? ''
  if (!selectedPath.toLowerCase().endsWith('.json')) return replaceVisiblePathsForDisplay(content)
  try {
    return replaceVisiblePathsForDisplay(JSON.stringify(JSON.parse(content), null, 2))
  } catch {
    return replaceVisiblePathsForDisplay(content)
  }
})

const selectedConsoleChartSpec = computed<Record<string, unknown> | null>(() => {
  const selectedPath = selectedConsoleFilePath.value
  if (!selectedPath || !selectedWorkspaceFile.value || !selectedPath.toLowerCase().endsWith('.json')) return null
  return parseChartSpecValue(selectedConsoleFileText.value)
})

const selectedConsoleJsonValue = computed<unknown>(() => {
  const text = selectedConsoleFileText.value.trim()
  if (!text || loadingWorkspaceFilePath.value === selectedConsoleFilePath.value) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
})

const selectedConsoleTableRows = computed<Record<string, unknown>[]>(() => {
  const value = selectedConsoleJsonValue.value
  const rows = Array.isArray(value)
    ? value
    : (
        value && typeof value === 'object' && !Array.isArray(value)
          ? ((value as Record<string, unknown>).data || (value as Record<string, unknown>).rows)
          : null
      )
  if (!Array.isArray(rows)) return []
  return rows.filter((row): row is Record<string, unknown> => row !== null && typeof row === 'object' && !Array.isArray(row))
})

const selectedConsoleTableColumns = computed<string[]>(() => {
  const value = selectedConsoleJsonValue.value
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const payloadColumns = (value as Record<string, unknown>).columns
    if (Array.isArray(payloadColumns)) {
      const columns = payloadColumns.map(col => String(col || '').trim()).filter(Boolean)
      if (columns.length) return columns
    }
  }
  const columns = new Set<string>()
  selectedConsoleTableRows.value.slice(0, 20).forEach((row) => {
    Object.keys(row).forEach(key => columns.add(key))
  })
  return Array.from(columns)
})

const selectedConsolePreviewRows = computed(() => selectedConsoleTableRows.value.slice(0, 50))
const hasSelectedConsoleTable = computed(() => selectedConsoleTableColumns.value.length > 0 && selectedConsoleTableRows.value.length > 0)

const isDashboardPreviewWritable = (tool: ToolCallInfo, payload: DashboardSpecPayload | null): boolean => {
  return tool.status === 'completed' && getDashboardPreviewWritableCount(payload) > 0 && !createdDashboardByToolId.value[tool.tool_call_id]
}

const resolveDashboardPreviewSemanticConfigId = (message: Message, payload: DashboardSpecPayload): string | null => {
  const specId = String(payload.dashboard_spec?.semantic_config_id || '').trim()
  if (specId) return specId
  const payloadId = String(payload.semantic_config_id || '').trim()
  if (payloadId) return payloadId
  const selectedId = String(message.semanticConfigSelection?.semantic_config_id || '').trim()
  if (selectedId) return selectedId
  return props.semanticConfigId || null
}

const handleCreateDashboardFromPreview = async (message: Message, tool: ToolCallInfo) => {
  const payload = getDashboardSpecFromTool(tool)
  if (!payload?.dashboard_spec) return
  if (!isDashboardPreviewWritable(tool, payload)) return
  creatingDashboardToolCallId.value = tool.tool_call_id
  try {
    const res = await createDashboardFromSpec({
      dashboard_spec: payload.dashboard_spec,
      datasource_id: payload.datasource_id || selectedDatasource.value,
      semantic_config_id: resolveDashboardPreviewSemanticConfigId(message, payload),
      conversation_id: payload.conversation_id || props.conversationId || localConversationId.value || null,
    })
    if (res.status !== 'success') {
      const warnings = res.data?.warnings || []
      throw new Error(warnings.length ? `${res.msg || '创建看板失败'}：${warnings[0]}` : (res.msg || '创建看板失败'))
    }
    const dashboard = res.data?.dashboard
    if (dashboard?.id) {
      createdDashboardByToolId.value = {
        ...createdDashboardByToolId.value,
        [tool.tool_call_id]: dashboard,
      }
      emit('dashboardCreated', dashboard)
    }
    const warnings = res.data?.warnings || []
    ElMessage.success(warnings.length ? `看板已创建，${warnings.length} 个卡片需调整` : '看板已创建，已切换到数据看板')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建看板失败')
  } finally {
    creatingDashboardToolCallId.value = null
  }
}

const formatToolTableCell = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

const selectToolCall = (toolCall: ToolCallInfo) => {
  if (activeToolCallId.value === toolCall.tool_call_id && sidePanelOpen.value && sidePanelTab.value === 'tool') {
    sidePanelOpen.value = false
    return
  }
  activeToolCallId.value = toolCall.tool_call_id
  sidePanelTab.value = 'tool'
  sidePanelOpen.value = true
}

const openConsolePanel = () => {
  sidePanelTab.value = 'console'
  sidePanelOpen.value = true
}

/**
 * 控制台面板开关（页面右上角按钮用）。
 *
 * 能改可见性的路径不止这一处——点击工具卡（工具详情）、切换/清空会话等都会动
 * sidePanelOpen，所以状态只认它：按钮只管调用本方法，图标/提示由
 * consoleVisibilityChanged 广播回来的值决定，避免两边各存一份状态而说法不一致
 * （面板在别处被关掉、按钮还停在「收起」态）。
 */
const toggleConsolePanel = () => {
  if (sidePanelOpen.value) {
    sidePanelOpen.value = false
    return
  }
  openConsolePanel()
}

watch(sidePanelOpen, (visible) => emit('consoleVisibilityChanged', visible), { immediate: true })

const handleOpenAttachment = async (attachment: MessageAttachment) => {
  const conversationId = localConversationId.value
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法查看文件')
    return
  }
  const rawPath = attachment.parsed_path || attachment.original_path || ''
  const relativePath = normalizeVisiblePath(rawPath)
  if (!relativePath) {
    ElMessage.warning('文件路径无效')
    return
  }
  // 如果工作区文件列表中还没有该文件，先刷新一次
  if (!workspaceFiles.value.some(f => normalizeVisiblePath(f.relative_path || f.path) === relativePath)) {
    await loadWorkspaceFiles(conversationId)
  }
  selectConsoleFile(relativePath)
  sidePanelTab.value = 'console'
  sidePanelOpen.value = true
  // 直接加载文件内容（不依赖 watch 中的 selectedWorkspaceFile 判断）
  await loadWorkspaceFileContent(conversationId, relativePath)
}

const handleOpenPresentedArtifact = async (artifact: { path: string; name: string }) => {
  await handleOpenAttachment({
    file_id: `presented:${artifact.path}`,
    filename: artifact.name,
    file_type: artifact.name.split('.').pop()?.toLowerCase(),
    original_path: artifact.path,
  })
}

const handleDownloadPresentedArtifact = async (artifact: { path: string; name: string }) => {
  const conversationId = localConversationId.value
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法下载文件')
    return
  }
  try {
    const blob = await getConversationWorkspaceRawFile(conversationId, artifact.path)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = artifact.name
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    console.error('下载交付文件失败:', err)
    ElMessage.error('文件下载失败，请稍后重试')
  }
}

// 工作区里的文件（uploads / workspace / outputs）都可直接下载，不依赖智能体是否调用过展示工具。
// 后端 raw 接口对三个目录一视同仁，此前的 outputs 限制只是前端产品决策，已放开。
// SQL 查询结果与工具结果卸载缓存除外——见 utils/consoleFileTree.ts 的下载白名单说明。
// file 由文件树每行的下载按钮传入；不传时回退到当前选中文件（兼容旧调用点）。
const handleDownloadConsoleFile = async (file: ProducedFile | null = selectedConsoleFile.value) => {
  const conversationId = props.conversationId || localConversationId.value
  if (!file) return
  if (!isWorkspaceFileDownloadable(file.relativePath)) {
    ElMessage.warning('该文件不允许下载')
    return
  }
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法下载文件')
    return
  }
  try {
    const blob = await getConversationWorkspaceRawFile(conversationId, file.relativePath)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.relativePath.split('/').pop() || file.relativePath
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (err) {
    console.error('下载工作区文件失败:', err)
    ElMessage.error('文件下载失败，请稍后重试')
  }
}

// 删除工作区文件：uploads / workspace / outputs 一视同仁，唯独 SQL 查询结果与工具结果缓存例外
// ——它们是审计留存，后端删除接口同样拒绝（前端这层是纵深防御，见 utils/consoleFileTree.ts）。
// 物理删除不可恢复，因此必须走二次确认；uploads 里的文件还会由后端连带清理 uploaded_files 记录，
// 否则历史消息里的附件卡片会指向一个已不存在的文件。
// 删除被设计成「单文件」：目录递归删需要独立的确认交互，不在这一版里提供。
// 删除成功后必须连同预览缓存一起作废，否则「删除 → 重传同名 → 点预览」会命中旧缓存：
// 文本预览显示上一份文件的内容、二进制预览复用一个指向已删文件的 blob URL，而且缓存命中
// 就不再发请求，这个状态不会自愈。这不是理论风险——uploads 的重名策略是「已存在才加 -1 后缀」
// （后端 uploaded_file_service.py::_unique_storage_name），文件删掉后重传同名拿回的就是
// 删除前的原路径，缓存必然命中。
// 失效规则本身在 utils/workspacePreviewCache.ts（纯函数，有单测），这里只做反射式赋值与 revoke。
const forgetWorkspaceFileCache = (path: string) => {
  const { contents, blobs, revokedUrl } = evictWorkspaceFileCache(
    path,
    workspaceFileContents.value,
    workspaceFileBlobs.value,
  )
  workspaceFileContents.value = contents
  workspaceFileBlobs.value = blobs
  if (revokedUrl) URL.revokeObjectURL(revokedUrl)
}

const deletingConsoleFilePath = ref<string | null>(null)

const handleDeleteConsoleFile = async (file: ProducedFile | null = null) => {
  const conversationId = props.conversationId || localConversationId.value
  if (!file) return
  const fileName = file.relativePath.split('/').pop() || file.relativePath
  if (!isWorkspaceFileDeletable(file.relativePath)) {
    ElMessage.warning('查询结果需要留存审计，不支持删除')
    return
  }
  if (!conversationId) {
    ElMessage.warning('会话未就绪，无法删除文件')
    return
  }
  // Run 执行中删工作区文件，会让沙箱回写被判成「应用侧已变更」而使该轮落盘失败。
  // 前端按 runBusy 拦截，后端对 pending/active/cancelling 同样返回 409，双保险。
  if (runBusy.value) {
    ElMessage.warning('智能体执行中，暂不能删除文件')
    return
  }
  try {
    await ElMessageBox.confirm(`确定删除「${fileName}」？删除后不可恢复。`, '删除文件', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }

  deletingConsoleFilePath.value = file.relativePath
  try {
    const response = await deleteConversationWorkspaceFile(conversationId, file.relativePath)
    if (response.status !== 'success') {
      ElMessage.error(response.msg || '删除失败')
      return
    }
    ElMessage.success(`已删除 ${fileName}`)
    // 删掉的正是当前预览的文件时清空选中，否则预览区会继续渲染一份已不存在的文件。
    // key 取归一化路径：预览缓存的 key 就是 selectedConsoleFilePath 的坐标系（见 selectConsoleFile）。
    const previewKey = normalizeVisiblePath(file.relativePath)
    if (selectedConsoleFilePath.value === previewKey) {
      selectedConsoleFilePath.value = null
    }
    // 缓存也要一并作废，否则重传同名文件后点预览会读回旧内容（理由见 forgetWorkspaceFileCache）
    forgetWorkspaceFileCache(previewKey)
    await loadWorkspaceFiles(conversationId)
  } catch {
    ElMessage.error('文件删除失败，请稍后重试')
  } finally {
    deletingConsoleFilePath.value = null
  }
}

// 面板内补充文件：只把文件落进 uploads（会话工作区），**不**进入 pendingFiles，
// 也就是不挂到下一轮消息的附件上——智能体需要时通过 parse_document/file_list 自行读取。
// 这条链路是现成的：app 侧 uploads 为权威副本，沙箱在执行前按 manifest 增量镜像，
// 而 agent 的文件工具对 uploads 只读，用户是 uploads 唯一合法写入者。
const workspaceUploadInputRef = ref<HTMLInputElement | null>(null)
const isWorkspaceFileUploading = ref(false)

const openWorkspaceUploadPicker = () => {
  if (isWorkspaceFileUploading.value) return
  workspaceUploadInputRef.value?.click()
}

const handleWorkspaceUploadSelected = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return

  const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
  if (!ACCEPTED_FILE_TYPES.includes(ext)) {
    ElMessage.warning(`仅支持 ${ACCEPTED_FILE_TYPES.join('、')} 格式`)
    return
  }
  if (file.size > MAX_FILE_SIZE) {
    ElMessage.warning('文件大小不能超过 50MB')
    return
  }

  let convId = props.conversationId || localConversationId.value
  if (!convId) {
    convId = (await props.ensureConversationId?.()) ?? null
    if (!convId) {
      ElMessage.error('创建会话失败，请稍后重试')
      return
    }
    localConversationId.value = convId
    // 刚创建的新会话无历史，标记已加载，避免触发历史加载导致 isLoadingHistory 闪烁
    loadedConversationId.value = convId
  }

  isWorkspaceFileUploading.value = true
  try {
    const res = await uploadFile(convId, file)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '上传失败')
      return
    }
    ElMessage.success(`已上传 ${res.data.original_filename}`)
    scheduleWorkspaceFilesRefresh()
  } catch {
    ElMessage.error('文件上传失败')
  } finally {
    isWorkspaceFileUploading.value = false
  }
}

const fillSemanticFieldQuestion = (payload: { fieldName: string; fieldType: 'dimension' | 'metric' }) => {
  const fieldName = String(payload?.fieldName || '').trim()
  if (!fieldName) return
  selectedSkill.value = null
  const currentInput = input.value.trim()
  const prefix = payload.fieldType === 'dimension' ? '按' : '查询'
  input.value = currentInput ? `${currentInput}、${fieldName}` : `${prefix}${fieldName}`
  inputBarRef.value?.focus()
}

defineExpose({
  openConsolePanel,
  toggleConsolePanel,
  fillSemanticFieldQuestion,
  openSkillManager: () => {
    isSkillManagerOpen.value = true
  },
})

const syncHistoryToolCalls = () => {
  const nextMap = new Map<string, ToolCallInfo>()
  for (const message of messages.value) {
    if (message.role !== 'assistant' || !message.toolCalls?.length) continue
    for (const toolCall of message.toolCalls) {
      const displayToolCall = normalizeToolCallForDisplay(toolCall)
      nextMap.set(displayToolCall.tool_call_id, displayToolCall)
    }
  }
  toolCallsMap.value = nextMap
}

const {
  isLoadingHistory,
  loadedConversationId,
  loadHistory,
  resetHistory,
  hasMoreHistory,
  isLoadingEarlierHistory,
  loadEarlierHistory,
} = useConversationHistory({
  conversationId: conversationIdRef,
  messages,
  tokenUsage,
  onActiveRunLoaded: (activeRun) => {
    activeRunId.value = activeRun?.run_id || null
    activeRunStatus.value = activeRun?.status || null
    activeRunTurnId.value = activeRun?.turn_id || null
    activeRunIsContinuation.value = Boolean(activeRun?.is_continuation)
    activeRunResumeRunId.value = activeRun?.run_id || null
    activeRunEventId.value = activeRun?.history_checkpoint?.stream_id || '0-0'
  },
})

watch(
  () => messages.value.filter(item => item.role === 'user').map(item => item.id).join('\u0000'),
  () => {
    const persistedIds = new Set(messages.value.filter(item => item.role === 'user').map(item => item.id))
    const retiredIds = pendingSteerMessages.value
      .filter(item => item.messageId && persistedIds.has(item.messageId))
      .map(item => item.messageId!)
    if (!retiredIds.length) return
    const retiredSet = new Set(retiredIds)
    pendingSteerMessages.value = pendingSteerMessages.value.filter(
      item => !item.messageId || !retiredSet.has(item.messageId),
    )
    if (editingSteerId.value && retiredSet.has(editingSteerId.value)) cancelEditSteer()
  },
)

const getConversationDisplayContent = (content: string) => hideSemanticConfigSelectionPayloads(content).trim()

// 距底部 100px 内视为「贴底」：内容增长时自动跟随，也容忍用户的小幅回滚
const NEAR_BOTTOM_THRESHOLD_PX = 100

const isScrolledToBottom = (container: HTMLElement) =>
  container.scrollHeight - container.scrollTop - container.clientHeight < NEAR_BOTTOM_THRESHOLD_PX

// 滚到顶自动续页：只在「从阈值之外向上越过阈值」时放行一次（见 utils/scrollReach）
const topReach = createTopReachTracker()

// 滚到顶自动加载更早一页。顶部按钮保留为手动兜底：自动翻页被跳过的场景
// （运行中、内容不足以滚动）用户仍可自行点击；失败后也不会自动重试，避免请求风暴。
const maybeAutoLoadEarlier = () => {
  const container = chatContainerRef.value
  if (!container) return
  // 内容不足以滚动时 scrollTop 恒为 0，若放行会在每个 scroll 事件上连环触发
  if (container.scrollHeight <= container.clientHeight) return
  if (!hasMoreHistory.value || isLoadingEarlierHistory.value || isLoadingHistory.value) return
  // 运行中不自动翻页：流式内容会在用户阅读历史时继续增长，翻不翻页交给用户自己决定
  if (runBusy.value) return
  void handleLoadEarlier()
}

const handleScroll = () => {
  const container = chatContainerRef.value
  if (!container) return
  isNearBottom.value = isScrolledToBottom(container)
  if (topReach.observe(container.scrollTop)) maybeAutoLoadEarlier()
}

// 用户上翻浏览历史后（isNearBottom=false）由「回到最新」按钮触发回到底部。
// 平滑滚动途中每次 @scroll 都会按当前距离重算，会让按钮在滚到底前又冒出来，
// 因此滚动期间用 rAF 维持跟随态，停稳（连续两帧 scrollTop 不变）后按真实位置校正。
const scrollToLatest = () => {
  const container = chatContainerRef.value
  if (!container) return
  isNearBottom.value = true
  if (typeof container.scrollTo !== 'function') {
    container.scrollTop = container.scrollHeight
    return
  }
  container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
  let lastTop = container.scrollTop
  // 负初值给出平滑滚动启动宽限，避免起手几帧就被判定为「已停稳」
  let stableFrames = -6
  const settle = () => {
    // 组件已卸载或切了会话，锚点失效，放弃校正
    if (container !== chatContainerRef.value) return
    if (container.scrollTop !== lastTop) {
      lastTop = container.scrollTop
      stableFrames = 0
      isNearBottom.value = true
      requestAnimationFrame(settle)
      return
    }
    if (++stableFrames < 2) {
      requestAnimationFrame(settle)
      return
    }
    isNearBottom.value = isScrolledToBottom(container)
  }
  requestAnimationFrame(settle)
}

// 向上翻页时把视口锚定在原消息上：补入更早内容后现有内容整体下移，
// 若不补偿 scrollTop，视图会跳到别处，用户会失去阅读位置。
// 首选按「首条已渲染消息」的布局位移补偿：流式新增内容发生在列表下方，不会污染这个位移；
// 拿不到锚点（列表为空、节点被重建）时退回 scrollHeight 差值。
const handleLoadEarlier = async () => {
  const container = chatContainerRef.value
  const anchor = container?.querySelector<HTMLElement>('.chat-message-list > div') ?? null
  const previousHeight = container?.scrollHeight ?? 0
  const previousTop = container?.scrollTop ?? 0
  const previousAnchorTop = anchor?.offsetTop ?? null
  await loadEarlierHistory()
  await nextTick()
  if (!container) return
  // 加载期间用户可能已经自己滚动（典型：等不及直接点了「回到最新」）。
  // 此时视口已经离开原位，再按锚点补偿会把人拽回刚才的位置，所以直接放弃补偿。
  if (container.scrollTop !== previousTop) return
  if (anchor && previousAnchorTop !== null && anchor.isConnected) {
    const shifted = anchor.offsetTop - previousAnchorTop
    if (shifted > 0) {
      container.scrollTop = previousTop + shifted
      return
    }
  }
  const grew = container.scrollHeight - previousHeight
  if (grew > 0) container.scrollTop = previousTop + grew
}

// 工具失败现在由后端 ToolMessage.status="error" 显式标记，此处仅为旧数据兜底：
// 旧后端失败文本格式为「<工具名> 执行失败: …」/「<工具名> 工具错误: …」，
// 只认首行前缀，避免成功结果正文恰好包含"执行失败"字样被误标为失败。
const isToolErrorResult = (content?: string) => {
  const text = String(content || '').trim()
  return /^[^\n:：]{0,64}?(?:执行失败|工具错误)[:：]/.test(text) || text.toLowerCase().startsWith('error:')
}

type PresentedSkillArtifact = {
  path: string
  name: string
  title: string
  description: string
}

const getPresentedSkillArtifacts = (message: Message): PresentedSkillArtifact[] => {
  const artifacts = new Map<string, PresentedSkillArtifact>()
  for (const tool of getPresentedArtifactTools(message, 'skill')) {
    const payload = parseToolResultPayload(tool.result)
    const filePath = normalizeVisiblePath(payload?.file_path)
    if (!/^outputs\/[^/]+\/SKILL\.md$/i.test(filePath)) continue
    const name = String(payload?.name || filePath.split('/')[1] || '').trim()
    if (!name) continue
    artifacts.set(filePath, {
      path: filePath.slice(0, -'/SKILL.md'.length),
      name,
      title: String(payload?.skill_title || name).trim() || name,
      description: String(payload?.description || '').trim(),
    })
  }
  return Array.from(artifacts.values())
}

const openImportedSkill = (path: string) => {
  importedSkillIdForManager.value = importedWorkspaceSkillIds.value[path] || null
  isSkillManagerOpen.value = true
}

const getMessageToolCall = (message: Message, toolCallId: string) => {
  return message.toolCalls?.find(item => item.tool_call_id === toolCallId) || findToolCallById(toolCallId)
}

const getRenderableMessageToolCall = (message: Message, toolCallId: string) => {
  const toolCall = getMessageToolCall(message, toolCallId)
  return toolCall ? normalizeToolCallForDisplay(toolCall) : null
}

const handleSkillSelect = (skill: SkillInfo) => {
  selectedSkill.value = skill
  inputBarRef.value?.focus()
}

watch(chatDataConfig, (data) => {
  if (!data) return
  const cfg = data.runtime_options
  defaultRuntimeOptions.value = {
    workspace_backend: cfg?.workspace_backend === 'sandbox' ? 'sandbox' : 'local',
    bash_enabled: !!cfg?.bash_enabled,
  }
  userOverridable.value = !!data.user_overridable
  selectedRuntimeOptions.value = { ...defaultRuntimeOptions.value }
})

// 当前选中模型不在注册表中时回填默认模型（如配置变更后）
watch(modelOptions, (models) => {
  const defaultModel = models.find(item => item.is_default)?.id || models[0]?.id || ''
  if (defaultModel && !models.some(item => item.id === selectedModel.value)) {
    selectedModel.value = defaultModel
  }
})

onMounted(() => {
  loadModelOptions()
})

const selectCommandSkill = (skill: SkillInfo) => {
  selectCommandSkillBase(skill)
  inputBarRef.value?.focus()
}

const onRuntimeOptionsChange = (options: { workspace_backend: 'local' | 'sandbox'; bash_enabled: boolean }) => {
  selectedRuntimeOptions.value = options
}

const clearSelectedSkill = () => {
  clearSelectedSkillBase()
  inputBarRef.value?.focus()
}

watch(input, (value) => {
  nextTick(() => inputBarRef.value?.resizeTextarea())
  checkOversizedInput(value)
})

watch(
  () => [
    selectedDatasource.value,
    fallbackConversationQueries.value.map(item => item.title).join('|'),
  ],
  () => {
    recommendationPage.value = 0
  }
)

watch(
  () => props.conversationId,
  (nextId, prevId) => {
    if (nextId === prevId) return
    if (nextId && nextId === localConversationId.value) return
    // 首次创建会话（null -> id，上传第一个附件或发送首条消息时触发）或“新建会话”清空
    // （id -> null）时，不重置已上传文件/输入等状态；仅在切换到另一个已有会话时才重置。
    // 注意：会话实际切换由父组件 conversationKey 强制重挂载处理，此处只兜底创建/清空场景。
    if (!prevId || !nextId) {
      localConversationId.value = nextId || null
      return
    }
    if (!isGenerating.value) {
      localConversationId.value = nextId
      hasLocalConversationStarted.value = false
      messages.value = []
      toolCallsMap.value.clear()
      workspaceFiles.value = []
      Object.values(workspaceFileBlobs.value).forEach(b => { if (b.url) URL.revokeObjectURL(b.url) })
      workspaceFileBlobs.value = {}
      workspaceFileContents.value = {}
      selectedConsoleOfficeHtml.value = null
      selectedConsoleOfficePreviewReason.value = null
      loadingOfficePreviewKey.value = null
      presentedArtifactContents.value = {}
      loadedPresentedArtifactKeys.value = new Set()
      loadingPresentedArtifactKeys.value = new Set()
      activeToolCallId.value = null
      sidePanelOpen.value = false
      sidePanelTab.value = 'console'
      activeAssistantMessageId.value = null
      resetTokenUsagePanel()
      resetHistory()
      // 清空会话后视口回到顶部即底部，重置跟随状态，避免「回到最新」按钮残留；
      // 自动翻页的越界观测值一并复位，避免带着上一个会话的位置误判「已越过顶部」
      isNearBottom.value = true
      topReach.reset()
      focusInput()
    }
  }
)

watch(
  () => localConversationId.value,
  (conversationId) => {
    loadWorkspaceFiles(conversationId)
  },
  { immediate: true }
)

watch(selectedConsoleFilePath, (path) => {
  if (!path || !selectedWorkspaceFile.value) return
  loadWorkspaceFileContent(localConversationId.value, path)
})

watch(
  () => [
    selectedConsoleFilePath.value,
    Boolean(selectedConsoleChartSpec.value),
    Boolean(hasSelectedConsoleTable.value),
    selectedConsoleIsMarkdown.value,
    selectedConsoleIsHtml.value,
  ] as const,
  () => {
    if (selectedConsoleChartSpec.value) {
      consoleFilePreviewMode.value = 'render'
      return
    }
    if (hasSelectedConsoleTable.value) {
      consoleFilePreviewMode.value = 'table'
      return
    }
    if (selectedConsoleIsMarkdown.value || selectedConsoleIsHtml.value) {
      consoleFilePreviewMode.value = 'render'
      return
    }
    consoleFilePreviewMode.value = 'source'
  }
)

watch(messages, () => {
  if (isGenerating.value) return
  syncHistoryToolCalls()
  loadPresentedArtifacts(localConversationId.value)
  if (messages.value.length > 0) {
    hasLocalConversationStarted.value = true
  }
}, { deep: true })

watch(messages, () => {
  if (!isNearBottom.value) return
  nextTick(() => {
    if (!chatContainerRef.value) return
    chatContainerRef.value.scrollTop = chatContainerRef.value.scrollHeight
  })
}, { deep: true })

const ensureAssistantMessage = () => {
  const activeId = activeAssistantMessageId.value
  const active = activeId ? messages.value.find(item => item.id === activeId) : null
  if (active && active.role === 'assistant') return active
  const last = messages.value[messages.value.length - 1]
  if (last?.role === 'assistant') {
    last.isStreaming = true
    activeAssistantMessageId.value = last.id
    return last
  }
  const assistant: Message = {
    id: `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: 'assistant',
    content: '',
    timestamp: new Date(),
    isStreaming: true,
  }
  messages.value.push(assistant)
  activeAssistantMessageId.value = assistant.id
  return assistant
}

const updateAssistantFinishedAt = (messageId: string, timestamp?: string) => {
  const finishedAt = parseDateTimeValue(timestamp)
  if (!finishedAt) return
  const activeMessage = activeAssistantMessageId.value
    ? messages.value.find(item => item.id === activeAssistantMessageId.value)
    : null
  const message = activeMessage?.role === 'assistant'
    ? activeMessage
    : messages.value.find(item => item.id === messageId)
  if (message?.role === 'assistant') {
    message.timestamp = finishedAt
  }
}

const removeEmptyActiveAssistant = () => {
  const activeId = activeAssistantMessageId.value
  if (!activeId) return
  const active = messages.value.find(item => item.id === activeId)
  const hasSegments = Boolean(active?.segments?.length)
  if (active && active.role === 'assistant' && !active.content && !active.error && !active.toolCalls?.length && !hasSegments) {
    messages.value = messages.value.filter(item => item.id !== activeId)
  }
}

const removeCompressionPlaceholders = () => {
  const hasPlaceholder = messages.value.some(
    message => message.role === 'context_compression' && message.contextCompression?.compressing,
  )
  if (hasPlaceholder) {
    messages.value = messages.value.filter(
      message => !(message.role === 'context_compression' && message.contextCompression?.compressing),
    )
  }
}

const appendContextCompressionEvent = (event: ChatDataContextCompressionEvent) => {
  if (!event.id) return
  if (event.phase === 'started') {
    removeEmptyActiveAssistant()
    messages.value.push({
      id: event.id,
      role: 'context_compression',
      content: '',
      timestamp: parseDateTimeValue(event.timestamp) || new Date(),
      contextCompression: { ...event, compressing: true },
    })
    return
  }
  // 完成事件到达后移除"压缩中"占位，再走原有去重与插入逻辑
  removeCompressionPlaceholders()
  if (messages.value.some(message => message.id === event.id)) return
  removeEmptyActiveAssistant()
  const active = activeAssistantMessageId.value
    ? messages.value.find(message => message.id === activeAssistantMessageId.value)
    : null
  if (active?.role === 'assistant') active.isStreaming = false
  activeAssistantMessageId.value = null
  messages.value.push({
    id: event.id,
    role: 'context_compression',
    content: '',
    timestamp: parseDateTimeValue(event.timestamp) || new Date(),
    contextCompression: event,
  })
}

const upsertToolMessage = (toolInfo: ToolCallInfo) => {
  if (!toolInfo.tool_call_id) return
  const displayToolInfo = normalizeToolCallForDisplay(toolInfo)
  removeEmptyActiveAssistant()
  toolCallsMap.value.set(displayToolInfo.tool_call_id, displayToolInfo)
  const assistant = ensureAssistantMessage()
  const toolCalls = assistant.toolCalls || []
  const existingIndex = toolCalls.findIndex(item => item.tool_call_id === displayToolInfo.tool_call_id)
  if (existingIndex >= 0) {
    toolCalls[existingIndex] = displayToolInfo
  } else {
    toolCalls.push(displayToolInfo)
  }
  assistant.toolCalls = [...toolCalls]
  const segments = assistant.segments || []
  if (!segments.some(segment => segment.type === 'toolCall' && segment.toolCallId === displayToolInfo.tool_call_id)) {
    segments.push({ type: 'toolCall', toolCallId: displayToolInfo.tool_call_id })
  }
  assistant.segments = [...segments]
  activeAssistantMessageId.value = assistant.id
  if (displayToolInfo.status === 'completed') {
    scheduleWorkspaceFilesRefresh()
    loadPresentedArtifacts(localConversationId.value)
  }
}

const upsertSubagentTrailItem = (event: ToolLifecycleEventData) => {
  const parentId = event.parent_tool_call_id
  if (!parentId) return

  // 提交挂到父 task 调用下的 trail 变更：写 map 与消息引用（内层工具不进
  // 全局 map，详情查找走 findToolCallById 的 trail 兜底）
  const commitTrail = (transform: (trail: SubagentTrailItem[]) => SubagentTrailItem[]) => {
    const mergeParent = (tool: ToolCallInfo): ToolCallInfo => (
      { ...tool, subagentTrail: transform([...(tool.subagentTrail || [])]) }
    )
    const parentFromMap = toolCallsMap.value.get(parentId)
    const merged = mergeParent(parentFromMap || {
      tool_call_id: parentId,
      tool_name: 'task',
      args: {},
      status: 'running',
    })
    toolCallsMap.value.set(parentId, merged)
    for (const message of messages.value) {
      if (!message.toolCalls?.some(item => item.tool_call_id === parentId)) continue
      message.toolCalls = message.toolCalls.map(item => item.tool_call_id === parentId ? merged : item)
    }
  }

  // 流式增量：按 message_id 追加正文/思考
  if (event.type === 'subagent_message_delta' || event.type === 'subagent_thinking_delta') {
    const entryId = event.message_id
    const delta = event.delta || ''
    if (!entryId || !delta) return
    const isText = event.type === 'subagent_message_delta'
    commitTrail((trail) => {
      const idx = trail.findIndex(item => item.id === entryId && item.kind === 'message')
      if (idx >= 0) {
        const prev = trail[idx]
        if (!prev) return trail
        trail[idx] = isText
          ? { ...prev, content: (prev.content || '') + delta, truncated: Boolean(event.truncated) || Boolean(prev.truncated) }
          : { ...prev, thinking: (prev.thinking || '') + delta, thinkingTruncated: Boolean(event.truncated) || Boolean(prev.thinkingTruncated) }
      } else {
        trail.push(isText
          ? { kind: 'message', id: entryId, content: delta, truncated: Boolean(event.truncated) }
          : { kind: 'message', id: entryId, thinking: delta, thinkingTruncated: Boolean(event.truncated) })
      }
      return trail
    })
    return
  }

  if (event.type === 'subagent_message') {
    const entryId = event.message_id
    if (!entryId) return
    const content = event.content || ''
    commitTrail((trail) => {
      const idx = trail.findIndex(item => item.id === entryId && item.kind === 'message')
      if (idx >= 0) {
        const prev = trail[idx]
        if (!prev) return trail
        // 收敛策略「只补标记不覆盖」：已增量流过的消息保留前端拼出的全文，
        // 完整事件只补截断标记；未流过（如后端降级路径）才写入完整内容
        trail[idx] = event.streamed
          ? { ...prev, truncated: Boolean(event.truncated) || Boolean(prev.truncated) }
          : { ...prev, content: content || prev.content || '', truncated: Boolean(event.truncated) }
      } else {
        trail.push({ kind: 'message', id: entryId, content, truncated: Boolean(event.truncated) })
      }
      return trail
    })
    return
  }

  // 工具生命周期事件：tool_started / tool_completed / tool_failed
  const entryId = event.tool_call_id
  if (!entryId) return
  const entry: SubagentTrailItem = {
    kind: 'tool',
    id: entryId,
    tool_name: event.tool_name || 'tool_call',
    args: { ...(event.args || {}) },
    result: event.content || event.result,
    status: event.type === 'tool_failed' ? 'error' : event.type === 'tool_completed' ? 'completed' : 'running',
  }
  commitTrail((trail) => {
    const idx = trail.findIndex(item => item.id === entryId)
    if (idx >= 0) {
      const prev = trail[idx]
      if (!prev) return trail
      trail[idx] = { ...prev, ...entry, args: { ...(prev.args || {}), ...(entry.args || {}) } }
    } else {
      trail.push(entry)
    }
    return trail
  })

  // 子智能体内部工具落盘（如 OfficeCLI 写 docx）后同步刷新控制台文件树，
  // 与主智能体工具完成后的刷新行为保持一致；schedule 内部有 500ms 防抖
  if (event.type === 'tool_completed') {
    scheduleWorkspaceFilesRefresh()
  }
}

const isUnfinishedToolStatus = (status?: ToolCallInfo['status']) => status === 'generating' || status === 'running'

const finalizeUnfinishedToolCalls = (status: 'completed' | 'error', result?: string) => {
  const nextMap = new Map(toolCallsMap.value)
  for (const [id, tool] of nextMap) {
    if (!isUnfinishedToolStatus(tool.status)) continue
    nextMap.set(id, normalizeToolCallForDisplay({
      ...tool,
      status,
      result: result || tool.result,
    }))
  }
  toolCallsMap.value = nextMap

  for (const message of messages.value) {
    if (!message.toolCalls?.length) continue
    message.toolCalls = message.toolCalls.map((tool) => {
      if (!isUnfinishedToolStatus(tool.status)) return tool
      return normalizeToolCallForDisplay({
        ...tool,
        status,
        result: result || tool.result,
      })
    })
  }
}

const appendAssistantContent = (content: string) => {
  if (!content) return
  const assistant = ensureAssistantMessage()
  assistant.content += content
  const segments = assistant.segments || []
  const last = segments[segments.length - 1]
  if (last?.type === 'text') {
    last.content += content
  } else {
    segments.push({ type: 'text', content })
  }
  assistant.segments = [...segments]
}

const finishStreamingMessages = () => {
  for (const item of messages.value) {
    if (item.role === 'assistant') item.isStreaming = false
  }
}

const setActiveAssistantError = (error: string) => {
  const assistant = ensureAssistantMessage()
  assistant.error = error
  assistant.isStreaming = false
}

const appendThinkingSegment = (content: string) => {
  if (!content) return
  const assistant = ensureAssistantMessage()
  const thinkingSegments = [...(assistant.thinkingSegments || [])]
  const segments = [...(assistant.segments || [])]
  const lastSegment = segments[segments.length - 1]
  if (lastSegment?.type === 'thinkingSegment') {
    const segmentIndex = thinkingSegments.findIndex(item => item.id === lastSegment.thinkingSegmentId)
    if (segmentIndex >= 0) {
      thinkingSegments[segmentIndex] = {
        ...thinkingSegments[segmentIndex]!,
        content: `${thinkingSegments[segmentIndex]!.content}${content}`,
      }
      assistant.thinkingSegments = thinkingSegments
      assistant.segments = segments
      activeAssistantMessageId.value = assistant.id
      return
    }
  }

  const thinkingSegment: ThinkingSegment = {
    id: `thinking-${Date.now()}-${thinkingSegments.length}`,
    content,
    toolCallCount: (assistant.toolCalls || []).length,
  }
  thinkingSegments.push(thinkingSegment)
  segments.push({ type: 'thinkingSegment', thinkingSegmentId: thinkingSegment.id })
  assistant.thinkingSegments = thinkingSegments
  assistant.segments = segments
  activeAssistantMessageId.value = assistant.id
}

const handleLangGraphMessage = (msg: LangGraphMessage) => {
  if (msg.type === 'thinking') {
    const reasoningContent = typeof msg.content === 'string'
      ? msg.content
      : typeof msg.additional_kwargs?.reasoning_content === 'string'
        ? msg.additional_kwargs.reasoning_content
        : ''
    if (reasoningContent) {
      appendThinkingSegment(reasoningContent)
    }
    return
  }

  if (msg.tool_calls?.length) {
    for (const tc of msg.tool_calls) {
      if (!tc.id || !tc.name) continue
      const existing = toolCallsMap.value.get(tc.id)
      const toolInfo: ToolCallInfo = {
        tool_call_id: tc.id,
        tool_name: tc.name,
        args: { ...(existing?.args || {}), ...(tc.args || {}) },
        result: existing?.result,
        status: existing?.status || 'generating',
        started_at_ms: existing?.started_at_ms ?? null,
        ended_at_ms: existing?.ended_at_ms ?? null,
      }
      upsertToolMessage(toolInfo)
    }
  }

  if (msg.type === 'tool' && msg.tool_call_id) {
    const existing = toolCallsMap.value.get(msg.tool_call_id)
    const result = msg.content || ''
    const toolInfo: ToolCallInfo = {
      tool_call_id: msg.tool_call_id,
      tool_name: msg.tool_name || existing?.tool_name || 'tool_call',
      args: { ...(existing?.args || {}), ...(msg.accumulated_args || {}) },
      result,
      status: msg.status === 'error' || isToolErrorResult(result) ? 'error' : 'completed',
      started_at_ms: msg.started_at_ms ?? existing?.started_at_ms ?? null,
      ended_at_ms: msg.ended_at_ms ?? existing?.ended_at_ms ?? null,
    }
    upsertToolMessage(toolInfo)
    return
  }

  appendAssistantContent(msg.content || '')
}

const getExistingAssistantMessage = (messageId?: string) => {
  if (!messageId) return null
  const message = messages.value.find(item => item.id === messageId)
  return message?.role === 'assistant' ? message : null
}

const submitAnalysisQuery = async (
  requestText: string,
  options: AnalysisSubmitOptions & { skillName?: string | null; messageSkill?: Message['skill']; restoreInputText?: string } = {}
) => {
  if ((!requestText.trim() && !options.fileIds?.length && !options.responseFactory && !options.queuedMessageId) || isGenerating.value) return
  const isFirstMessage = options.appendUserMessage !== false
    && !messages.value.some(item => item.role === 'user')
  const effectiveSemanticConfigSelection = options.semanticConfigSelection !== undefined
    ? options.semanticConfigSelection
    : (props.semanticConfigSelection || null)
  const userMessage: Message = {
    id: options.userMessageId || `user-${Date.now()}`,
    role: 'user',
    content: requestText,
    timestamp: new Date(),
    skill: options.messageSkill,
    attachments: options.attachments,
  }
  if (options.appendUserMessage !== false) {
    messages.value.push(userMessage)
  }
  hasLocalConversationStarted.value = true
  let assistantMessage = getExistingAssistantMessage(options.assistantMessageId)
  if (assistantMessage) {
    activeAssistantMessageId.value = assistantMessage.id
    if (!options.preserveAssistant) {
      assistantMessage.content = ''
      assistantMessage.error = undefined
      assistantMessage.toolCalls = []
      assistantMessage.thinkingSegments = []
      assistantMessage.segments = []
    }
    assistantMessage.semanticConfigSelection = effectiveSemanticConfigSelection || undefined
    assistantMessage.isStreaming = true
  } else {
    activeAssistantMessageId.value = null
    assistantMessage = ensureAssistantMessage()
    assistantMessage.semanticConfigSelection = effectiveSemanticConfigSelection || undefined
  }
  const assistantMessageId = assistantMessage.id
  if (options.appendUserMessage !== false) {
    input.value = ''
    selectedSkill.value = null
  }
  isGenerating.value = true
  activeToolCallId.value = null
  let startedRunId: string | null = null
  let completedNextRunId: string | null = null
  let completedRunStatus: RunDoneStatus | null = null
  let convId: string | null = props.conversationId || null
  // Keep the previous request visible until the first usage event of this turn arrives.
  resetTokenUsagePanel(false)

  try {
    if (!convId && props.ensureConversationId) {
      convId = await props.ensureConversationId()
      if (convId) {
        // 新会话无历史，标记已加载，避免历史加载闪烁
        loadedConversationId.value = convId
      }
    }
    if (!convId) {
      ElMessage.error('无法创建会话')
      if (options.appendUserMessage !== false) {
        messages.value = messages.value.filter(item => item.id !== userMessage.id && item.id !== assistantMessage.id)
      }
      activeAssistantMessageId.value = null
      if (options.restoreInputText) input.value = options.restoreInputText
      if (options.messageSkill) selectedSkill.value = options.messageSkill
      return
    }
    const resolvedConversationId = convId
    if (props.ensureConversationDatasource && !await props.ensureConversationDatasource()) {
      if (options.appendUserMessage !== false) {
        messages.value = messages.value.filter(item => item.id !== userMessage.id && item.id !== assistantMessage.id)
      }
      activeAssistantMessageId.value = null
      if (options.restoreInputText) input.value = options.restoreInputText
      if (options.messageSkill) selectedSkill.value = options.messageSkill
      return
    }
    localConversationId.value = resolvedConversationId
    await generateSqlStream(
      requestText,
      true,
      selectedDatasource.value,
      resolvedConversationId,
      {
        mode: 'query',
        onMessage: (msg) => handleLangGraphMessage(msg),
        onResult: () => {},
        onStatus: () => {},
        onError: (error) => {
          if (options.interruptedQuestion) {
            pendingUserQuestion.value = options.interruptedQuestion
            const active = activeAssistantMessageId.value
              ? messages.value.find(item => item.id === activeAssistantMessageId.value)
              : null
            if (active?.role === 'assistant') active.isStreaming = false
            ElMessage.error(error || '提交回答失败')
            return
          }
          finalizeUnfinishedToolCalls('error', error)
          setActiveAssistantError(error)
        },
        onRunningTool: (toolInfo) => {
          if (!toolInfo.tool_call_id) return
          const existing = toolCallsMap.value.get(toolInfo.tool_call_id)
          const updated: ToolCallInfo = {
            tool_call_id: toolInfo.tool_call_id,
            tool_name: toolInfo.tool_name || existing?.tool_name || 'tool_call',
            args: { ...(existing?.args || {}), ...(toolInfo.args || {}) },
            result: existing?.result,
            status: 'running',
            started_at_ms: existing?.started_at_ms ?? null,
            ended_at_ms: existing?.ended_at_ms ?? null,
          }
          upsertToolMessage(updated)
        },
        semanticConfigId: options.semanticConfigIdOverride !== undefined ? options.semanticConfigIdOverride : (props.semanticConfigId || null),
        modelId: selectedModelValue.value,
        memberDatasourceIds: selectedMemberDatasourceIds.value,
        agentId: 'chat_data',
        skillName: options.skillName || null,
        onMessageEnd: (payload) => {
          if (payload.usage) tokenUsage.value = payload.usage
          updateAssistantFinishedAt(assistantMessageId, payload.timestamp)
        },
        onTokenUsage: (usage) => {
          tokenUsage.value = usage
        },
        semanticConfigSelection: effectiveSemanticConfigSelection,
        enableThinking: enableDeepThinking.value,
        thinkingEffort: thinkingEffort.value,
        runtimeOptions: selectedRuntimeOptions.value,
        onContextCompression: (event) => appendContextCompressionEvent(event),
        fileIds: options.fileIds,
        queuedMessageId: options.queuedMessageId,
        preserveQueuedMessages: options.preserveQueuedMessages,
        onToolLifecycle: (event) => {
          // 子智能体内部过程事件：挂到所属 task 调用下，不建顶层片段
          // 必须在 tool_call_id 判空之前分流——文本条目本身没有 tool_call_id
          if (event.parent_tool_call_id) {
            upsertSubagentTrailItem(event)
            return
          }
          if (!event.tool_call_id) return
          const existing = toolCallsMap.value.get(event.tool_call_id)
          const isFailed = event.type === 'tool_failed'
          const isCompleted = event.type === 'tool_completed'
          const toolInfo: ToolCallInfo = {
            tool_call_id: event.tool_call_id,
            tool_name: event.tool_name || existing?.tool_name || 'tool_call',
            args: { ...(existing?.args || {}), ...(event.args || {}) },
            result: event.content || event.result || existing?.result,
            progressMessage: event.message || existing?.progressMessage,
            progress: typeof event.progress === 'number' ? event.progress : existing?.progress,
            status: isFailed ? 'error' : isCompleted ? 'completed' : 'running',
            subagentTrail: existing?.subagentTrail,
            started_at_ms: event.started_at_ms ?? existing?.started_at_ms ?? null,
            ended_at_ms: event.ended_at_ms ?? existing?.ended_at_ms ?? null,
          }
          upsertToolMessage(toolInfo)
        },
        onInboxAdmitted: (messageIds) => {
          admittedInboxMessageIds.value = new Set([...admittedInboxMessageIds.value, ...messageIds])
          admitPendingMessages(messageIds)
        },
        onQuestionRequested: (question) => {
          pendingUserQuestion.value = question
          hasActiveRun.value = true
          emit('runStatusChanged', resolvedConversationId, 'waiting_user')
          const active = activeAssistantMessageId.value
            ? messages.value.find(item => item.id === activeAssistantMessageId.value)
            : null
          if (active?.role === 'assistant') active.isStreaming = false
        },
        onRunStarted: (runId) => {
          startedRunId = runId
          hasActiveRun.value = true
          if (activeRunId.value !== runId) {
            activeRunEventId.value = '0-0'
            activeRunResumeRunId.value = runId
          }
          activeRunId.value = runId
          saveOwnedRun(resolvedConversationId, runId)
          emit('runStatusChanged', resolvedConversationId, 'active')
        },
        responseFactory: options.responseFactory,
        onRunEvent: (eventId) => {
          // 事件游标只由服务端数据库 checkpoint 提供，避免浏览器内存状态
          // 在断线恢复时跳过尚未随历史一起读取的事件。
          void eventId
        },
        onFirstEvent: () => {
          if (isFirstMessage) emit('firstMessageStarted', resolvedConversationId)
        },
        onRunRestarted: async () => {
          activeAssistantMessageId.value = null
          await loadHistory(resolvedConversationId, true)
          syncHistoryToolCalls()
        },
        onRunCompleted: (info) => {
          completedRunStatus = info.status
          completedNextRunId = info.nextRunId || null
          applyRunDoneState(
            resolvedConversationId,
            info.status,
            info.status === 'finished' ? info.nextRunId || null : null,
            startedRunId,
          )
        },
      },
    )
  } catch (err) {
    const errorMessage = err instanceof Error ? err.message : '发送失败'
    setActiveAssistantError(errorMessage)
  } finally {
    isGenerating.value = false
    // 流结束（含异常中断）后清掉未完成的"上下文压缩中"占位
    removeCompressionPlaceholders()

    // 结构化 done 已包含终态和下一轮 Run，不再查询队列。只有旧协议或
    // SSE 意外中断时没有足够信息，才用一次服务端快照恢复状态。
    if (startedRunId && (completedRunStatus === null || completedRunStatus === 'legacy')) {
      await restorePendingSteerQueue({ restartPolling: true, applyTerminal: true })
    }
    if (options.autoResumeQueuedRun !== false) {
      const nextRunId = completedNextRunId
      const conversationIdForNextRun = convId
      if (conversationIdForNextRun && nextRunId && completedRunStatus === 'finished' && !isStoppingRun.value) {
        // 服务端在 done 中直接给出了下一轮 Run ID（事务性创建已完成），
        // 跳过轮询，立刻接续订阅。新 Run 从头回放，游标从 '0-0' 开始。
        // 注意必须保持 autoResumeQueuedRun 默认开启：多条队列时每轮
        // continuation 的 done 都会携带下一个 next_run_id，链式订阅要
        // 逐轮传递下去；队列清空后 complete_run 返回 next_run_id=None，
        // 链条自然终止，不存在无限循环。
        activeAssistantMessageId.value = null
        await submitAnalysisQuery('', {
          appendUserMessage: false,
          preserveAssistant: false,
          responseFactory: signal => streamRunEvents(
            conversationIdForNextRun,
            nextRunId,
            '0-0',
            signal,
          ),
        })
      }
    }
  }
}

const resumePendingQuestion = async (answers?: UserQuestionAnswer[], cancelled = false) => {
  const question = pendingUserQuestion.value
  const conversationId = localConversationId.value || props.conversationId
  if (!question || !conversationId || isSubmittingQuestion.value || isGenerating.value) return
  isSubmittingQuestion.value = true
  pendingUserQuestion.value = null
  const resumeAssistant = activeAssistantMessageId.value
    ? messages.value.find(item => item.id === activeAssistantMessageId.value && item.role === 'assistant')
    : [...messages.value].reverse().find(item => item.role === 'assistant')
  try {
    await submitAnalysisQuery('', {
      appendUserMessage: false,
      assistantMessageId: resumeAssistant?.id,
      preserveAssistant: true,
      interruptedQuestion: question,
      responseFactory: signal => cancelled
        ? cancelUserQuestion(conversationId, question.question_id, signal)
        : answerUserQuestion(conversationId, question.question_id, answers || [], signal),
    })
  } finally {
    isSubmittingQuestion.value = false
    restorePendingUserQuestion()
  }
}

// options.queuedMessageId：单条队列消息"执行"入口透传的消息 ID（普通输入框发送不传）
const handleSubmit = async (options: { queuedMessageId?: string } = {}) => {
  if (isStoppingRun.value) return
  if (activeRunStatus.value === 'cancelling') {
    ElMessage.info('当前任务正在停止，请稍后再发送')
    return
  }
  if (activeRunStatus.value === 'waiting_user') {
    ElMessage.info('请先回答智能体问题，回答后再继续处理任务')
    return
  }
  if (options.queuedMessageId) {
    const queuedMessage = pendingSteerMessages.value.find(
      item => item.messageId === options.queuedMessageId,
    )
    const conversationId = localConversationId.value || props.conversationId
    if (
      !queuedMessage
      || queuedMessage.status !== 'pending'
      || !conversationId
      || hasActiveRun.value
      || isGenerating.value
    ) return
    if (!selectedDatasource.value) {
      ElMessage.warning('请先选择数据源')
      return
    }
    executingQueueMessageId.value = options.queuedMessageId
    try {
      await submitAnalysisQuery(queuedMessage.content, {
        userMessageId: queuedMessage.messageId,
        queuedMessageId: queuedMessage.messageId,
        preserveQueuedMessages: true,
      })
    } finally {
      executingQueueMessageId.value = null
    }
    return
  }
  // 本地正在流式生成，或刷新后服务端仍有运行中的任务：输入都进入队列
  if (isGenerating.value || hasActiveRun.value) {
    const query = input.value.trim()
    const conversationId = localConversationId.value || props.conversationId
    if (!query || !conversationId || isSteering.value) return
    const temporaryId = `steer-${Date.now()}`
    pendingSteerMessages.value.push({
      clientId: temporaryId,
      content: query,
      queueType: 'next-turn',
      status: 'submitting',
      timestamp: new Date(),
    })
    input.value = ''
    isSteering.value = true
    try {
      const response = await steerConversation(conversationId, query, 'next-turn')
      const queuedMessage = pendingSteerMessages.value.find(item => item.clientId === temporaryId)
      if (queuedMessage && response.data?.message_id) {
        queuedMessage.messageId = response.data.message_id
        queuedMessage.status = 'pending'
        if (admittedInboxMessageIds.value.has(queuedMessage.messageId)) {
          admitPendingMessages([queuedMessage.messageId])
        }
      }
    } catch (error) {
      pendingSteerMessages.value = pendingSteerMessages.value.filter(item => item.clientId !== temporaryId)
      input.value = query
      await restorePendingSteerQueue({ restartPolling: false })
      if (!hasActiveRun.value) {
        if (isStoppingRun.value) return // 停止竞态下不得重新发起任务
        void submitAnalysisQuery(query)
        return
      }
      ElMessage.error(error instanceof Error ? error.message : '追加消息失败')
    } finally {
      isSteering.value = false
      focusInput()
    }
    return
  }
  const hasUploadedFiles = pendingFiles.value.some(
    f => f.status === 'uploaded' && f.file_id && !f.file_id.startsWith('temp-'),
  )
  if (!input.value.trim() && !hasUploadedFiles) return
  if (!selectedDatasource.value) {
    ElMessage.warning('请先选择数据源')
    return
  }
  if (props.executionTargets.length > 0 && selectedMemberDatasourceIds.value.length === 0) {
    ElMessage.warning('请选择执行对象')
    return
  }
  if (hasParsingFiles.value) {
    ElMessage.warning('请等待文件上传完成')
    return
  }

  const queryText = input.value.trim()
  const skillName = selectedSkill.value?.name || null
  const manualSemanticConfigId = props.semanticConfigSelection?.user_selected
    ? props.semanticConfigSelection.semantic_config_id
    : null
  const messageSkill = selectedSkill.value
    ? {
        name: selectedSkill.value.name,
        title: selectedSkill.value.title,
        description: selectedSkill.value.description,
      }
    : undefined

  // 超长输入兜底：转文件（带真实 file_id 进入 pendingFiles），成功后继续发送
  let effectiveQueryText = queryText
  if (queryText.length > MAX_INPUT_CHARS && !isProcessingOversizedInput.value) {
    const success = await convertOversizedInputToFile(queryText)
    if (success) {
      input.value = ''
      ElMessage.info('已转为文件附件并发送')
      effectiveQueryText = '请查看附件内容并进行分析'
    } else {
      return
    }
  }

  // 收集本轮消息关联的已上传文件（手动上传 + 超长转文件）；不再要求预解析/链接完成
  const completedFileIds: string[] = []
  const attachments: MessageAttachment[] = []
  for (const f of pendingFiles.value) {
    if (f.file_id && !f.file_id.startsWith('temp-') && f.status === 'uploaded') {
      completedFileIds.push(f.file_id)
      attachments.push({
        file_id: f.file_id,
        filename: f.filename,
        file_type: f.file_type,
        file_size: f.file_size,
        original_path: `/mnt/user-data/uploads/${f.storage_dir || f.filename}`,
      })
    }
  }

  // 如果只有文件没有文本，使用占位提示
  if (!effectiveQueryText.trim() && completedFileIds.length > 0) {
    effectiveQueryText = '请查看附件内容并进行分析'
  }

  const requestText = buildSkillRequestText(effectiveQueryText, skillName || '')
  // 附件已捕获进 completedFileIds/attachments，发送瞬间即清空输入框附件卡片，
  // 并避免后续消息重复附带历史上传文件
  pendingFiles.value = []
  await submitAnalysisQuery(requestText, {
    skillName,
    messageSkill,
    restoreInputText: queryText,
    semanticConfigIdOverride: manualSemanticConfigId,
    fileIds: completedFileIds.length > 0 ? completedFileIds : undefined,
    preserveQueuedMessages: pendingSteerMessages.value.length > 0,
    attachments: attachments.length > 0 ? attachments : undefined,
  })
}

const handleStop = async () => {
  if (isStoppingRun.value) return
  const conversationId = localConversationId.value || props.conversationId
  const runId = activeRunId.value
  if (!conversationId || !runId) return
  isStoppingRun.value = true
  try {
    const response = await cancelConversationRun(conversationId, runId)
    if (!response.data?.cancelled) {
      await restorePendingSteerQueue({ restartPolling: false, applyTerminal: true })
      isStoppingRun.value = false
      return
    }
    // 接口响应只代表后端已接受取消。若 done 尚未先到达，则显示停止中；
    // 气泡、工具和历史终态全部由结构化 done 的统一回调收口。
    if (hasActiveRun.value && activeRunId.value === runId) {
      activeRunStatus.value = 'cancelling'
      emit('runStatusChanged', conversationId, 'cancelling')
    }
  } catch (error) {
    isStoppingRun.value = false
    ElMessage.error(error instanceof Error ? error.message : '停止运行失败')
  }
}

const handleKeyDown = (event: KeyboardEvent) => {
  if (handleSkillCommandKeyDown(event, selectCommandSkill)) return
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSubmit()
  }
}
</script>

<template>
  <div class="relative h-full flex min-h-0 bg-[#F7F8FA]">
    <div class="relative flex-1 min-w-0 flex flex-col">
      <div
        ref="chatContainerRef"
        :class="[
          'flex-1 overflow-y-auto p-4',
          !hasLocalConversationStarted && messages.length === 0 && 'flex flex-col'
        ]"
        @scroll="handleScroll"
      >
        <div v-if="hasMoreHistory && !isLoadingHistory" class="mb-3 flex justify-center">
          <!-- 滚到顶会自动翻页，这里主要负责反馈加载中状态；
               自动翻页被跳过的场景（运行中、内容不足以滚动）仍可点按钮手动加载 -->
          <div
            v-if="isLoadingEarlierHistory"
            class="inline-flex h-8 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 text-[12px] text-slate-400 shadow-sm"
          >
            <i class="fa-solid fa-spinner fa-spin text-[10px]"></i>
            <span>正在加载更早的消息...</span>
          </div>
          <button
            v-else
            type="button"
            class="inline-flex h-8 items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 text-[12px] text-slate-500 shadow-sm transition-colors hover:bg-slate-50 hover:text-blue-600"
            @click="handleLoadEarlier"
          >
            <i class="fa-solid fa-arrow-up text-[10px]"></i>
            <span>加载更早的消息</span>
          </button>
        </div>
        <div v-if="isLoadingHistory" class="h-full flex items-center justify-center text-sm text-slate-400">
          <i class="fa-solid fa-spinner fa-spin mr-2 text-xs"></i>
          <span>加载历史消息...</span>
        </div>
        <div v-else-if="!hasLocalConversationStarted && messages.length === 0" class="flex-1 flex justify-center pt-8 md:pt-10">
          <div class="w-[774px] max-w-[90%]">
            <div class="mb-5 rounded-2xl bg-gradient-to-r from-blue-50/80 via-white to-indigo-50/80 px-5 py-4 text-left shadow-[0_8px_24px_rgba(15,23,42,0.04)]">
              <div class="text-[17px] font-semibold leading-7 text-slate-800">你好，我是数据智能体</div>
            </div>

            <div v-if="selectedDatasource" class="max-w-[560px]">
              <div class="mb-2 flex items-center justify-between gap-3">
                <div class="flex items-center gap-1.5 text-[13px] font-medium text-slate-400">
                  <i class="fa-regular fa-lightbulb text-[12px] text-amber-400"></i>
                  <span>你可以这样问：</span>
                </div>
                <button
                  v-if="canRefreshRecommendations"
                  type="button"
                  class="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg border border-transparent bg-transparent px-2 text-[12px] text-slate-400 transition-colors hover:bg-slate-100 hover:text-blue-600"
                  title="换一批推荐问题"
                  @click="refreshRecommendations"
                >
                  <i class="fa-solid fa-rotate-right text-[10px]"></i>
                  <span>换一批</span>
                </button>
              </div>
              <QuickQueries
                key="conversation-recommendations"
                :datasource-id="selectedDatasource"
                mode="conversation"
                content-type="queries"
                :fallback-queries="fallbackConversationQueries"
                :max-items="5"
                :page-offset="recommendationOffset"
                prefer-fallback
                :load-favorites="false"
                compact
                @select-query="(query: string) => { input = query; focusInput() }"
                @select-skill="handleSkillSelect"
              />
            </div>
          </div>
        </div>

        <ConversationMessageList
          v-else
          :messages="messages"
          :datasource-id="selectedDatasource"
          :execution-targets="props.executionTargets"
          :active-tool-call-id="activeToolCallId"
          :conversation-id="localConversationId"
          :on-save="handleSaveToDashboard"
          :display-content="getConversationDisplayContent"
          :resolve-tool-call="getRenderableMessageToolCall"
          :should-inline-render-result="shouldInlineRenderToolCall"
          :run-active="isGenerating || hasActiveRun"
          @select-tool="selectToolCall"
          @open-attachment="handleOpenAttachment"
        >
          <template #assistant-extra="{ message: msg }">
            <PresentedFileArtifacts
              v-if="getPresentedDeliveryArtifacts(msg).length"
              :artifacts="getPresentedDeliveryArtifacts(msg)"
              @preview="handleOpenPresentedArtifact"
              @download="handleDownloadPresentedArtifact"
            />
            <div v-if="getPresentedSkillArtifacts(msg).length" class="mt-3 space-y-2">
              <div
                v-for="skill in getPresentedSkillArtifacts(msg)"
                :key="skill.path"
                class="flex items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50/70 px-3.5 py-3"
              >
                <div class="flex min-w-0 items-center gap-2.5">
                  <span class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-emerald-600 text-white">
                    <i class="fa-solid fa-wand-magic-sparkles text-xs"></i>
                  </span>
                  <div class="min-w-0">
                    <div class="truncate text-sm font-semibold text-slate-800">{{ skill.title }}</div>
                    <div class="mt-0.5 truncate text-[11px] text-slate-500">{{ skill.description || `技能标识：${skill.name}` }}</div>
                  </div>
                </div>
                <button
                  v-if="!importedWorkspaceSkillIds[skill.path]"
                  type="button"
                  :disabled="importingWorkspaceSkillPath === skill.path"
                  class="shrink-0 h-8 rounded-md border border-emerald-200 bg-white px-2.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400"
                  @click="handleImportWorkspaceSkill(skill.path)"
                >
                  <i v-if="importingWorkspaceSkillPath === skill.path" class="fa-solid fa-spinner fa-spin mr-1"></i>
                  {{ importingWorkspaceSkillPath === skill.path ? '导入中' : '导入技能' }}
                </button>
                <button
                  v-else
                  type="button"
                  class="shrink-0 h-8 rounded-md border border-emerald-200 bg-white px-2.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
                  @click="openImportedSkill(skill.path)"
                >
                  <i class="fa-solid fa-pen-to-square mr-1"></i>
                  编辑草稿
                </button>
              </div>
            </div>
            <div v-if="getDashboardPreviewTools(msg).length" class="mt-3 space-y-3">
                  <div
                    v-for="tool in getDashboardPreviewTools(msg)"
                    :key="tool.tool_call_id"
                    class="rounded-xl border border-blue-100 bg-blue-50/60 px-3.5 py-3"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <div class="flex items-center gap-2">
                          <span class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white">
                            <i class="fa-solid fa-chart-pie text-xs"></i>
                          </span>
                          <div class="min-w-0">
                            <div class="truncate text-sm font-semibold text-slate-800">{{ getDashboardPreviewTitle(getDashboardSpecFromTool(tool)) }}</div>
                            <div class="mt-0.5 text-[11px] text-slate-500">
                              {{ getDashboardPreviewCards(getDashboardSpecFromTool(tool)).length }} 个卡片，{{ getDashboardPreviewWritableCount(getDashboardSpecFromTool(tool)) }} 个可写入
                            </div>
                          </div>
                        </div>
                        <div
                          v-if="getDashboardPreviewWarningCount(getDashboardSpecFromTool(tool)) > 0"
                          class="mt-2 text-[11px] text-amber-600"
                        >
                          <i class="fa-solid fa-triangle-exclamation mr-1"></i>
                          {{ getDashboardPreviewWarningCount(getDashboardSpecFromTool(tool)) }} 条校验提示，写入时会跳过不可生成 SQL 的卡片。
                        </div>
                      </div>
                      <button
                        v-if="!createdDashboardByToolId[tool.tool_call_id]"
                        type="button"
                        class="shrink-0 h-8 rounded-lg border border-blue-200 bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                        :disabled="creatingDashboardToolCallId === tool.tool_call_id || !isDashboardPreviewWritable(tool, getDashboardSpecFromTool(tool))"
                        @click="handleCreateDashboardFromPreview(msg, tool)"
                      >
                        <i v-if="creatingDashboardToolCallId === tool.tool_call_id" class="fa-solid fa-spinner fa-spin mr-1"></i>
                        <i v-else class="fa-solid fa-check mr-1"></i>
                        写入看板
                      </button>
                      <div v-else class="shrink-0 rounded-lg bg-white px-3 py-1.5 text-xs text-blue-700">
                        已写入
                      </div>
                    </div>
                    <div class="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                      <div
                        v-for="(card, cardIndex) in getDashboardPreviewCards(getDashboardSpecFromTool(tool))"
                        :key="`${tool.tool_call_id}-${cardIndex}`"
                        class="rounded-lg border border-white/80 bg-white px-3 py-2"
                      >
                        <div class="flex items-center justify-between gap-2">
                          <div class="min-w-0 truncate text-xs font-medium text-slate-700">{{ getCardTitle(card, cardIndex) }}</div>
                          <div class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{{ getCardChartType(card) }}</div>
                        </div>
                        <div v-if="getCardLayoutText(card)" class="mt-1 font-mono text-[10px] text-slate-400">
                          {{ getCardLayoutText(card) }}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
            <div v-if="getChartPreviewTools(msg).length" class="mt-3 space-y-3">
                  <div
                    v-for="tool in getChartPreviewTools(msg)"
                    :key="tool.tool_call_id"
                    class="rounded-xl border border-slate-200 bg-slate-50/70 p-3"
                  >
                    <ChartSpecRenderer
                      v-if="getChartSpecFromTool(tool)"
                      :spec="getChartSpecFromTool(tool)!"
                    />
                  </div>
            </div>
          </template>
        </ConversationMessageList>
      </div>

      <!-- 「回到最新」悬浮按钮：h-0 锚点贴在滚动区下边界，不占布局高度；
           放在滚动容器之外，按钮不随内容滚动、也不会压住正文。
           水平居中于输入栏上方（业内惯例位置），也自然避开右侧轮次导航刻度带。 -->
      <div class="relative z-20 h-0">
        <button
          v-if="!isNearBottom"
          type="button"
          class="absolute -top-14 left-1/2 inline-flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-[0_6px_16px_rgba(15,23,42,0.14)] transition-colors hover:border-blue-200 hover:text-[#1677FF]"
          title="回到最新"
          aria-label="回到最新"
          @click="scrollToLatest"
        >
          <i class="fa-solid fa-chevron-down text-xs"></i>
        </button>
      </div>

      <div v-show="!isLoadingHistory || messages.length > 0" class="p-4 bg-gray-50 border-t border-slate-100">
        <div class="max-w-[774px] mx-auto w-[90%]">
          <AnalysisProgress
            v-if="analysisTodos.items.length"
            class="mb-3"
            :todos="analysisTodos"
          />
          <div
            v-if="pendingSteerMessages.length"
            class="mb-2 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
          >
            <div class="flex items-center gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              <i class="fa-solid fa-chevron-down text-[9px]"></i>
              <span>{{ pendingSteerMessages.length }} 个队列任务</span>
              <span class="ml-auto text-[10px] text-slate-400">
                {{ activeRunStatus === 'waiting_user' ? '请先回答智能体问题，恢复后继续处理队列' : '发送默认下一轮处理，可手动立即插入' }}
              </span>
            </div>
            <div class="max-h-[120px] overflow-y-auto">
              <div
                v-for="item in pendingSteerMessages"
                :key="item.clientId"
                class="group flex items-center gap-3 border-b border-slate-100 px-3 py-2.5 text-sm text-slate-700 last:border-b-0"
              >
                <span
                  class="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                  :class="item.queueType === 'next-step' ? 'bg-amber-50 text-amber-600' : 'bg-slate-100 text-slate-500'"
                >{{ item.queueType === 'next-step' ? '立即' : '下轮' }}</span>
                <input
                  v-if="editingSteerId === item.messageId"
                  v-model="editingSteerDraft"
                  type="text"
                  autofocus
                  class="min-w-0 flex-1 rounded border border-[#1677FF]/50 bg-white px-2 py-1 text-sm text-slate-700 outline-none focus:border-[#1677FF]"
                  placeholder="修改消息内容"
                  @keydown.enter.prevent="saveEditSteerMessage(item)"
                  @keydown.esc.prevent="cancelEditSteer()"
                />
                <span v-else class="min-w-0 flex-1 truncate">{{ item.content }}</span>
                <span v-if="editingSteerId !== item.messageId" class="flex shrink-0 items-center gap-1.5 text-[10px] text-slate-400">
                  <i v-if="!item.messageId" class="fa-solid fa-circle-notch fa-spin text-[9px]"></i>
                  <span v-if="item.status === 'submitting'">入队中</span>
                  <span v-else-if="item.status === 'claimed' || item.status === 'consumed'">处理中</span>
                  <span v-else>等待处理</span>
                  <button
                    v-if="item.messageId && item.status === 'pending' && !hasActiveRun && !isGenerating"
                    type="button"
                    class="flex shrink-0 items-center gap-1 rounded bg-[#1677FF] px-1.5 py-0.5 text-[10px] text-white transition-colors hover:bg-[#1677FF]/85 disabled:cursor-not-allowed disabled:opacity-50"
                    :disabled="Boolean(executingQueueMessageId) || steerActionBusy"
                    title="执行这条消息"
                    @click="handleSubmit({ queuedMessageId: item.messageId })"
                  >
                    <i
                      :class="executingQueueMessageId === item.messageId ? 'fa-solid fa-spinner fa-spin text-[9px]' : 'fa-solid fa-play text-[9px]'"
                    ></i>
                    执行
                  </button>
                  <button
                    v-if="item.messageId && hasActiveRun && activeRunStatus !== 'waiting_user'"
                    type="button"
                    class="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] transition-colors"
                    :class="item.queueType === 'next-step' ? 'bg-slate-100 text-slate-500 hover:bg-slate-200' : 'bg-blue-50 text-[#1677FF] hover:bg-blue-100'"
                    :title="item.queueType === 'next-step' ? '改为下一轮处理' : '立即插入当前对话'"
                    @click="toggleSteerMessageQueueType(item)"
                  >
                    <i :class="item.queueType === 'next-step' ? 'fa-solid fa-arrow-right-arrow-left text-[9px]' : 'fa-solid fa-bolt text-[9px]'"></i>
                    {{ item.queueType === 'next-step' ? '转下轮' : '立即插入' }}
                  </button>
                  <button
                    v-if="item.messageId"
                    type="button"
                    class="flex h-5 w-5 items-center justify-center rounded text-slate-400 transition-colors hover:bg-blue-50 hover:text-[#1677FF]"
                    title="编辑"
                    @click="startEditSteerMessage(item)"
                  >
                    <i class="fa-solid fa-pen text-[9px]"></i>
                  </button>
                  <button
                    type="button"
                    class="flex h-5 w-5 items-center justify-center rounded text-slate-400 transition-colors hover:bg-red-50 hover:text-red-500"
                    title="移除"
                    @click="removePendingSteerMessage(item)"
                  >
                    <i class="fa-solid fa-xmark text-[10px]"></i>
                  </button>
                </span>
                <span v-else class="flex shrink-0 items-center gap-1.5 text-[10px]">
                  <button
                    type="button"
                    class="rounded bg-[#1677FF] px-1.5 py-0.5 text-white transition-colors hover:bg-[#1677FF]/85"
                    title="保存修改"
                    @click="saveEditSteerMessage(item)"
                  >
                    <i class="fa-solid fa-check text-[9px]"></i>
                    保存
                  </button>
                  <button
                    type="button"
                    class="rounded bg-slate-100 px-1.5 py-0.5 text-slate-500 transition-colors hover:bg-slate-200"
                    title="取消编辑"
                    @click="cancelEditSteer()"
                  >
                    取消
                  </button>
                </span>
              </div>
            </div>
          </div>
          <UserQuestionCard
            v-if="pendingUserQuestion"
            :question="pendingUserQuestion"
            :submitting="isSubmittingQuestion"
            @submit="resumePendingQuestion($event)"
            @cancel="resumePendingQuestion(undefined, true)"
          />
          <div v-if="pendingFiles.length" class="mb-1.5 flex max-h-[100px] flex-wrap gap-1.5 overflow-y-auto">
            <div
              v-for="f in pendingFiles"
              :key="f.file_id"
              class="group flex items-center gap-1.5 rounded-full border border-[#1677FF]/30 bg-white px-2.5 py-1 text-[11px] text-[#1F3A56] shadow-sm transition-colors hover:border-[#1677FF]/50"
            >
              <i class="fa-solid fa-spinner fa-spin text-[9px] text-amber-500" v-if="f.status === 'uploading'"></i>
              <i class="fa-regular fa-file-lines text-[11px] text-[#1677FF]" v-else></i>
              <span class="max-w-[160px] truncate">{{ f.filename }}</span>
              <span v-if="f.status === 'uploading'" class="text-amber-500">上传中</span>
              <span v-else-if="f.status === 'failed'" class="text-red-400">失败</span>
              <button
                type="button"
                class="ml-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full text-gray-300 transition-colors hover:bg-red-50 hover:text-red-500"
                @click="removePendingFile(f.file_id)"
                title="移除"
              >
                <i class="fa-solid fa-xmark text-[8px]"></i>
              </button>
            </div>
          </div>
          <ConversationInputBar
            ref="inputBarRef"
            v-model:input="input"
            v-model:selected-model="selectedModel"
            v-model:enable-deep-thinking="enableDeepThinking"
            v-model:thinking-effort="thinkingEffort"
            :placeholder="hasLocalConversationStarted || messages.length > 0 ? '继续输入任务，输入 / 可选择技能' : '请输入任务，输入 / 可选择技能'"
            :selected-skill-title="selectedSkillTitle"
            :runtime-options="selectedRuntimeOptions"
            :user-overridable="userOverridable"
            :model-options="modelOptions"
            :is-generating="runBusy"
            :can-submit="runBusy ? Boolean(input.trim() && !isSteering) : Boolean((input.trim() || hasCompletedPendingFiles) && selectedDatasource)"
            :input-focused="inputFocused"
            :show-skill-button="!runBusy"
            :submit-title="!selectedDatasource ? '请先选择数据源' : '发送 (Enter)'"
            @submit="handleSubmit"
            @stop="handleStop"
            @clear-skill="clearSelectedSkill"
            @upload-file="handleUploadFile"
            @open-skill-manager="isSkillManagerOpen = true"
            @update:runtime-options="onRuntimeOptionsChange"
            @keydown="handleKeyDown"
            @focus="inputFocused = true"
            @blur="inputFocused = false"
          >
            <template #model-prefix>
              <ConversationTokenUsage
                ref="tokenUsagePanelRef"
                :usage="tokenUsage"
                :messages="messages"
                :is-generating="isGenerating"
                compact
              />
            </template>

            <template #command-menu>
              <SkillCommandMenu
                :open="showSkillCommand"
                :loading="isCommandSkillLoading"
                :skills="displayedCommandSkills"
                :highlighted-index="highlightedSkillIndex"
                @select="selectCommandSkill"
              />
            </template>
          </ConversationInputBar>
        </div>
      </div>
      <input
        ref="fileInputRef"
        type="file"
        class="hidden"
        :accept="ACCEPTED_FILE_TYPES.join(',')"
        @change="handleFileSelected"
      />
      <!-- 工作区面板的补充文件入口：与输入栏上传复用同一接口，但结果只落 uploads、不挂到消息附件 -->
      <input
        ref="workspaceUploadInputRef"
        type="file"
        class="hidden"
        :accept="ACCEPTED_FILE_TYPES.join(',')"
        data-workspace-upload-input="true"
        @change="handleWorkspaceUploadSelected"
      />
      <ConversationTurnNavigator :messages="messages" :container="chatContainerRef" />
    </div>

    <ConversationSidePanel
      :open="sidePanelOpen"
      :title="sidePanelTitle"
      :subtitle="sidePanelSubtitle"
      :show-trigger="false"
      :show-close="false"
      @open="openConsolePanel"
      @close="sidePanelOpen = false"
    >
        <!-- 头部这一行从左到右：标题「工作区」→ 工作区根路径 →「折叠/展开全部」按钮 →（行尾）文件数量。
             根路径原先占着文件树的第一行（/mnt/user-data 文件夹行），上移到标题行后树里不再有
             这一级、同一件事不重复表达，树也少一行高度；一键折叠/展开按钮跟着根路径走——它折叠的
             就是这棵树里的全部目录，与各目录行左侧的箭头属同一族操作，紧挨根路径放语义最直白。
             上传是 uploads 目录专属的写入入口，已下沉到 uploads 目录行（就近绑定）。
             数量徽标贴行尾：pr-[13px] 让它与下方文件树里的数量徽标落在同一条竖线上——头部容器有 px-4(16px)，
             文件树里的数量徽标右边缘距面板右沿 29px（内容区 p-4 16 + 树容器 px-1 4 + 目录区域边框 1 + 滚动容器 px-1 4 + 行 pr-1 4），
             这里补 13px 补齐差值。改文件树的内边距或边框时要同步这一处。 -->
        <template v-if="sidePanelTab === 'console'" #header-inline>
          <span class="min-w-0 truncate font-mono text-xs font-normal text-slate-400">{{ producedFileTree.root }}</span>
          <button
            v-if="consoleTreeCollapsiblePaths.length"
            type="button"
            class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-slate-200 bg-white text-[11px] leading-none text-slate-500 shadow-sm transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 active:bg-slate-100"
            :title="isConsoleTreeFullyCollapsed ? '展开全部目录' : '折叠全部目录'"
            :aria-label="isConsoleTreeFullyCollapsed ? '展开全部目录' : '折叠全部目录'"
            data-console-tree-toggle="true"
            @click="toggleConsoleTreeCollapsed"
          >
            <i class="fa-solid" :class="isConsoleTreeFullyCollapsed ? 'fa-angles-down' : 'fa-angles-up'"></i>
          </button>
        </template>

        <template v-if="sidePanelTab === 'console'" #header-extra>
          <div class="flex shrink-0 items-center pr-[13px]">
            <div class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">
              {{ isWorkspaceFilesLoading ? '加载中' : `${producedFiles.length} 个文件` }}
            </div>
          </div>
        </template>

        <template v-if="sidePanelTab === 'console'">
          <!-- 树的左缩进体系：顶层目录行用 px-1.5（6px）作基准，子层用 consoleTreeIndentStyle(depth)。
               改这里的 px-1.5 时必须同步 CONSOLE_TREE_INDENT_BASE_PX，否则第一级跨度会与后续级不等。
               根路径（/mnt/user-data）那一行已上移到面板头部标题行，这里直接从顶层目录开始。
               目录区域套一圈细边框，把「树」从面板的空白底上框出来：目录行是 7px 高、缩进逐级变化的
               文字行，没有边界时视线容易散到面板空白处；框住之后区域范围一眼可见（也顺带标出
               滚动区的范围）。空树时整块不渲染——留一个空框比不画更难解释。
               树容器仍带 @container：「时间 / 大小」两列的显隐按它的宽度算，与边框无关。 -->
          <div v-if="producedFileTree.directories.length" class="mb-3 px-1 py-1 text-xs @container">
            <div class="max-h-[300px] space-y-0.5 overflow-auto rounded-md border border-slate-200 px-1 py-1">
              <div
                v-for="directory in producedFileTree.directories"
                :key="directory.name"
              >
                <div class="flex h-7 min-w-0 items-center gap-1 rounded pr-1 transition-colors hover:bg-slate-50">
                  <button
                    type="button"
                    class="flex h-7 min-w-0 flex-1 items-center gap-1.5 border-none bg-transparent px-1.5 text-left text-slate-700"
                    @click="toggleConsoleDirectory(directory.path)"
                  >
                    <i class="w-3 shrink-0 text-[10px] text-slate-400" :style="consoleTreeLeadSlotStyle" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                    <i class="fa-regular w-4 shrink-0 text-center text-amber-500" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-folder' : 'fa-folder-open'"></i>
                    <span class="truncate font-mono font-semibold" @mouseenter="syncTruncatedTitle">{{ directory.name }}</span>
                    <span class="shrink-0 text-slate-400">{{ directory.label }}</span>
                  </button>
                  <!-- 上传入口挂在 uploads 目录行：它是 uploads 的唯一写入方，
                       与「往这个目录补文件」在语义上就近绑定，比塞在面板头部更好找。
                       该目录行由 PRODUCED_FILE_DIRECTORIES 常驻渲染，空会话也有入口。
                       文件只落 uploads（不进 pendingFiles、不挂到下一轮消息附件），
                       智能体需要时经 parse_document / file_list 自行读取。 -->
                  <button
                    v-if="directory.name === 'uploads'"
                    type="button"
                    class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-none bg-transparent text-[11px] leading-none text-slate-400 transition-colors hover:bg-slate-200/70 hover:text-slate-700 disabled:opacity-50"
                    title="上传文件到工作区"
                    aria-label="上传文件到工作区"
                    :disabled="isWorkspaceFileUploading"
                    data-console-upload="true"
                    @click="openWorkspaceUploadPicker"
                  >
                    <i class="fa-solid" :class="isWorkspaceFileUploading ? 'fa-spinner fa-spin' : 'fa-upload'"></i>
                  </button>
                  <!-- 数量徽标统一放在每行最右端（按钮在其左侧），与面板头部同序：
                       所有数量落在同一条竖线上，扫一眼就能纵向比对。 -->
                  <span v-if="getFileCount(directory)" class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(directory) }}</span>
                </div>
                <div v-if="directory.children.length && !isConsoleDirectoryCollapsed(directory.name)" class="space-y-0.5">
                  <div
                    v-for="item in flattenFileTreeNodes(directory.children)"
                    :key="item.node.path"
                    class="min-w-0"
                  >
                    <!-- 文件行：单行承载「选中预览 + 行尾操作」。预览/下载按钮默认透明，
                         hover 或该文件已选中时显现——面板窄、文件名长，常显会把文件名压没。
                         预览按钮与点文件名等价（选中并在下方面板渲染），只是把动作显式化。 -->
                    <div
                      v-if="item.node.file"
                      :class="[
                        'group flex h-7 min-w-0 items-center gap-0.5 rounded pr-1 transition-colors hover:bg-blue-50',
                        selectedConsoleFilePath === item.node.file.relativePath ? 'bg-blue-50' : ''
                      ]"
                    >
                      <button
                        type="button"
                        :class="[
                          'flex h-7 min-w-0 flex-1 items-center gap-1.5 border-none bg-transparent px-1.5 text-left transition-colors',
                          selectedConsoleFilePath === item.node.file.relativePath ? 'text-blue-700' : 'text-slate-600 hover:text-blue-700'
                        ]"
                        :style="consoleTreeIndentStyle(item.depth)"
                        @click="selectConsoleFile(item.node.file.relativePath)"
                      >
                        <!-- 占位与图标必须 shrink-0：文件名的 flex-basis 是 auto，取的是 max-content
                             （长文件名可达 460px+）；只靠 truncate 的话，行内放不下时收缩量会按基础宽度
                             比例摊到所有子项，这个 12px 占位和 16px 图标会被一起挤窄，
                             导致「长文件名那一行」的图标与文字比上下行左移 6px/12px。 -->
                        <span class="w-3 shrink-0"></span>
                        <i class="fa-regular fa-file-lines w-4 shrink-0 text-center text-slate-400"></i>
                        <!-- 名字被截断时悬浮显示完整文件名：面板窄、长文件名必然省略号截断 -->
                        <span class="min-w-0 flex-1 truncate font-mono text-[13px]" @mouseenter="syncTruncatedTitle">{{ item.node.name }}</span>
                        <!-- 修改时间 + 文件大小：行尾两个扫读列（行内样式常量见 CONSOLE_TREE_TIME/SIZE_COLUMN_CLASS），
                             名字让出空间（flex-1 + truncate），长名靠悬浮提示看全。
                             时间用绝对格式 YYYY-MM-DD HH:mm:ss——文件多是版本对照，需要能直接比出具体时刻。 -->
                        <span :class="CONSOLE_TREE_TIME_COLUMN_CLASS">{{ formatEpochDateTime(item.node.file.mtime) }}</span>
                        <span :class="CONSOLE_TREE_SIZE_COLUMN_CLASS">{{ formatFileSize(item.node.file.size) }}</span>
                      </button>
                      <div
                        class="flex shrink-0 items-center gap-0.5 transition-opacity"
                        :class="selectedConsoleFilePath === item.node.file.relativePath ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'"
                      >
                        <button
                          type="button"
                          class="flex h-6 w-6 items-center justify-center rounded-md border-none bg-transparent text-[11px] text-slate-400 transition-colors hover:bg-blue-100 hover:text-blue-600"
                          title="预览文件"
                          data-console-file-preview="true"
                          @click="selectConsoleFile(item.node.file.relativePath)"
                        >
                          <i class="fa-solid fa-eye"></i>
                        </button>
                        <!-- SQL 查询结果（outputs/query_result、outputs/.tool-offload）不提供下载，
                             防数据泄漏；后端两侧已拦截，这里只是前端侧的纵深防御。 -->
                        <button
                          v-if="isWorkspaceFileDownloadable(item.node.file.relativePath)"
                          type="button"
                          class="flex h-6 w-6 items-center justify-center rounded-md border-none bg-transparent text-[11px] text-slate-400 transition-colors hover:bg-blue-100 hover:text-blue-600"
                          title="下载文件"
                          data-console-file-download="true"
                          @click="handleDownloadConsoleFile(item.node.file)"
                        >
                          <i class="fa-solid fa-download"></i>
                        </button>
                        <!-- 删除：与预览/下载同列（行尾操作组），危险动作用红色区分。
                             查询结果（outputs/query_result、outputs/.tool-offload）不提供删除，
                             那是审计留存；run 执行中禁用，避免打断沙箱回写。 -->
                        <button
                          v-if="isWorkspaceFileDeletable(item.node.file.relativePath)"
                          type="button"
                          class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border-none bg-transparent text-[11px] text-slate-400 transition-colors hover:bg-red-100 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                          title="删除文件"
                          :disabled="runBusy || deletingConsoleFilePath === item.node.file.relativePath"
                          data-console-file-delete="true"
                          @click="handleDeleteConsoleFile(item.node.file)"
                        >
                          <i class="fa-solid" :class="deletingConsoleFilePath === item.node.file.relativePath ? 'fa-spinner fa-spin' : 'fa-trash-can'"></i>
                        </button>
                      </div>
                    </div>
                    <div
                      v-else
                      class="flex h-7 min-w-0 items-center gap-1 rounded pr-1 transition-colors hover:bg-slate-50"
                    >
                      <div
                        role="button"
                        tabindex="0"
                        class="flex h-7 min-w-0 flex-1 items-center gap-1.5 border-none bg-transparent px-1.5 text-left text-slate-600"
                        :style="consoleTreeIndentStyle(item.depth)"
                        @click="toggleConsoleDirectory(item.node.path)"
                        @keydown.enter.prevent="toggleConsoleDirectory(item.node.path)"
                        @keydown.space.prevent="toggleConsoleDirectory(item.node.path)"
                      >
                        <i class="w-3 shrink-0 text-[10px] text-slate-400" :style="consoleTreeLeadSlotStyle" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                        <i class="fa-regular w-4 shrink-0 text-center text-amber-500" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-folder' : 'fa-folder-open'"></i>
                        <span class="truncate font-mono text-[13px] font-semibold" @mouseenter="syncTruncatedTitle">{{ item.node.name }}</span>
                      </div>
                      <!-- 数量徽标与顶层目录行同构：放在可点击区域之外的行尾，
                           所有数量徽标的右边缘落在同一条竖线上（放在按钮内时会被 px-1.5 推走 2px）。 -->
                      <span v-if="getFileCount(item.node)" class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(item.node) }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-if="SHOW_CONSOLE_EMPTY_STATE && !producedFiles.length" class="mt-2 rounded bg-slate-50 px-2 py-2 text-xs text-slate-400">
              暂无文件
            </div>
          </div>

          <div v-if="selectedConsoleFile" class="rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div class="mb-2 flex items-center justify-between gap-2">
              <div class="min-w-0 truncate font-mono text-xs font-medium text-slate-500">
                {{ selectedConsoleFile?.displayPath }}
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <div v-if="selectedConsoleFile && (selectedConsoleChartSpec || hasSelectedConsoleTable || selectedConsoleIsMarkdown || selectedConsoleIsHtml)" class="flex rounded-md border border-slate-200 bg-white p-0.5">
                  <button
                    v-if="selectedConsoleChartSpec"
                    type="button"
                    :class="[
                      'h-6 rounded px-2 text-[10px] transition-colors',
                      consoleFilePreviewMode === 'render' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                    ]"
                    @click="consoleFilePreviewMode = 'render'"
                  >
                    图表
                  </button>
                  <button
                    v-if="hasSelectedConsoleTable"
                    type="button"
                    :class="[
                      'h-6 rounded px-2 text-[10px] transition-colors',
                      consoleFilePreviewMode === 'table' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                    ]"
                    @click="consoleFilePreviewMode = 'table'"
                  >
                    表格
                  </button>
                  <button
                    v-if="selectedConsoleIsMarkdown || selectedConsoleIsHtml"
                    type="button"
                    :class="[
                      'h-6 rounded px-2 text-[10px] transition-colors',
                      consoleFilePreviewMode === 'render' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                    ]"
                    @click="consoleFilePreviewMode = 'render'"
                  >
                    预览
                  </button>
                  <button
                    type="button"
                    :class="[
                      'h-6 rounded px-2 text-[10px] transition-colors',
                      consoleFilePreviewMode === 'source' ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-50'
                    ]"
                    @click="consoleFilePreviewMode = 'source'"
                  >
                    源码
                  </button>
                </div>
                <!-- 下载已下沉到文件树每行右端（和文件名同处一行，不必先选中再来这里找）。
                     这里改为刷新：tool_result / run 结束本就会自动刷新，此按钮是手动兜底。 -->
                <button
                  v-if="selectedConsoleFile"
                  type="button"
                  class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700 disabled:opacity-50"
                  :title="isWorkspaceFilesLoading ? '正在刷新文件列表' : '刷新文件列表'"
                  :disabled="isWorkspaceFilesLoading"
                  data-console-files-refresh="true"
                  @click="refreshWorkspaceFiles"
                >
                  <i class="fa-solid fa-rotate-right" :class="isWorkspaceFilesLoading ? 'fa-spin' : ''"></i>
                </button>
                <button
                  v-if="selectedConsoleFile"
                  type="button"
                  class="flex h-6 w-6 items-center justify-center rounded border border-slate-200 bg-white text-[11px] text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700"
                  title="放大预览"
                  @click="toggleWorkspaceFilePreviewFullscreen"
                >
                  <i class="fa-solid fa-expand"></i>
                </button>
              </div>
            </div>
            <div ref="workspaceFilePreviewRef" class="workspace-file-preview relative bg-white" :class="isWorkspaceFilePreviewFullscreen ? 'h-screen w-screen overflow-auto p-6' : ''">
              <button
                v-if="isWorkspaceFilePreviewFullscreen"
                type="button"
                class="absolute right-3 top-3 z-20 flex h-8 w-8 items-center justify-center rounded border border-slate-200 bg-white text-xs text-slate-600 shadow-sm hover:bg-slate-50"
                title="退出放大预览"
                @click="toggleWorkspaceFilePreviewFullscreen"
              >
                <i class="fa-solid fa-compress"></i>
              </button>
              <ChartSpecRenderer
                v-if="selectedConsoleChartSpec && consoleFilePreviewMode === 'render'"
                :spec="selectedConsoleChartSpec"
              />
              <MarkdownFilePreview
                v-else-if="selectedConsoleIsMarkdown && consoleFilePreviewMode === 'render'"
                :content="selectedConsoleFileText"
                :class="workspaceFilePreviewHeightClass"
              />
              <HtmlPagePreview
                v-else-if="selectedConsoleIsHtml && consoleFilePreviewMode === 'render'"
                :html="selectedConsoleHtmlDocument"
                :class="workspaceFilePreviewHeightClass"
              />
              <div v-else-if="hasSelectedConsoleTable && consoleFilePreviewMode === 'table'" :class="isWorkspaceFilePreviewFullscreen ? 'h-[calc(100vh-48px)] overflow-auto rounded-md border border-slate-100 bg-white' : 'max-h-[46vh] overflow-auto rounded-md border border-slate-100 bg-white'">
                <table class="min-w-full border-separate border-spacing-0 text-left text-xs">
                <thead class="sticky top-0 z-10 bg-slate-50">
                  <tr>
                    <th
                      v-for="column in selectedConsoleTableColumns"
                      :key="column"
                      class="border-b border-slate-100 px-3 py-2 font-medium text-slate-500"
                    >
                      {{ column }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, rowIndex) in selectedConsolePreviewRows" :key="rowIndex" class="hover:bg-slate-50">
                    <td
                      v-for="column in selectedConsoleTableColumns"
                      :key="column"
                      class="max-w-[220px] truncate border-b border-slate-50 px-3 py-2 text-slate-700"
                      :title="formatToolTableCell(row[column])"
                    >
                      {{ formatToolTableCell(row[column]) }}
                    </td>
                  </tr>
                </tbody>
              </table>
              </div>
              <iframe
                v-else-if="selectedConsoleIsPdf && selectedConsoleBinaryBlob?.url"
                :src="selectedConsoleBinaryBlob.url"
                :class="isWorkspaceFilePreviewFullscreen ? 'm-0 h-[calc(100vh-48px)] min-w-0 w-full rounded-md border border-slate-100 bg-white' : 'm-0 h-[46vh] min-w-0 w-full rounded-md border border-slate-100 bg-white'"
              ></iframe>
              <div
                v-else-if="selectedConsoleIsPdf && loadingWorkspaceFilePath === selectedConsoleFilePath"
                :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex h-[46vh] items-center justify-center text-xs text-slate-400'"
              >PDF 加载中...</div>
              <div
                v-else-if="selectedConsoleIsOfficeFile && selectedConsoleOfficeHtml"
                :class="isWorkspaceFilePreviewFullscreen ? 'h-[calc(100vh-48px)] min-w-0 overflow-auto rounded-md border border-slate-100 bg-white' : 'h-[46vh] min-w-0 overflow-auto rounded-md border border-slate-100 bg-white'"
              >
                <iframe
                  :srcdoc="selectedConsoleOfficeHtml"
                  sandbox="allow-scripts"
                  class="h-full min-w-0 w-full border-0 bg-white"
                  title="Office 文件预览"
                ></iframe>
              </div>
              <div
                v-else-if="selectedConsoleIsOfficeFile && loadingOfficePreviewKey === `${localConversationId}:${selectedConsoleFilePath}`"
                :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex h-[46vh] items-center justify-center text-xs text-slate-400'"
              >Office 文件加载中...</div>
              <div
                v-else-if="selectedConsoleIsOfficeFile && selectedConsoleOfficePreviewReason"
                :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] flex-col items-center justify-center gap-2 text-xs text-slate-400' : 'flex h-[46vh] flex-col items-center justify-center gap-2 text-xs text-slate-400'"
              >
                <span v-if="selectedConsoleOfficePreviewReason === 'preview_too_large'">文件过大，暂不支持预览，请下载查看。</span>
                <span v-else>当前环境不能预览 Office 文件，请下载查看。</span>
              </div>
              <div
                v-else-if="selectedConsoleIsImage && selectedConsoleBinaryBlob?.url"
                :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center overflow-auto rounded-md border border-slate-100 bg-white p-2' : 'flex h-[46vh] items-center justify-center overflow-auto rounded-md border border-slate-100 bg-white p-2'"
              >
                <img :src="selectedConsoleBinaryBlob.url" :alt="selectedConsoleFilePath || ''" class="max-h-full max-w-full object-contain" />
              </div>
              <div
                v-else-if="selectedConsoleIsImage && loadingWorkspaceFilePath === selectedConsoleFilePath"
                :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-slate-400' : 'flex h-[46vh] items-center justify-center text-xs text-slate-400'"
              >图片加载中...</div>
              <div v-else-if="selectedConsoleBinaryBlob?.error" :class="isWorkspaceFilePreviewFullscreen ? 'flex h-[calc(100vh-48px)] items-center justify-center text-xs text-red-400' : 'flex h-[46vh] items-center justify-center text-xs text-red-400'">文件加载失败，请重试。</div>
              <pre v-else :class="isWorkspaceFilePreviewFullscreen ? 'm-0 h-[calc(100vh-48px)] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700' : 'm-0 max-h-[46vh] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700'">{{ selectedConsoleFileText || '暂无可预览内容' }}</pre>
            </div>
          </div>
          <div v-else-if="SHOW_CONSOLE_FILE_PLACEHOLDER" class="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs text-slate-400">
            点击工作区文件查看内容。
          </div>
        </template>

        <template v-else>
          <ToolCallDetailPanel
            :tool-call="activeToolCall"
            :format-text="replaceVisiblePathsForDisplay"
          />
        </template>
    </ConversationSidePanel>

    <el-dialog v-model="isSkillManagerOpen" width="min(1280px, 92vw)" title="技能管理" append-to-body>
      <div class="h-[640px] overflow-hidden">
        <SkillManager :initial-skill-id="importedSkillIdForManager" />
      </div>
    </el-dialog>

    <SaveToPanelModal
      v-model:visible="showSaveModal"
      :sql="saveSql"
      :chart-type="saveChartType"
      :source-thread-id="localConversationId || undefined"
      :datasource-id="selectedDatasource"
      :member-datasource-id="saveMemberDatasourceId"
      :member-datasource-ids="saveMemberDatasourceIds"
      :execution-targets="props.executionTargets"
    />
  </div>
</template>
