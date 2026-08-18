<script setup lang="ts">
/**
 * ChatData 会话页。
 * 左侧展示流式消息与工具调用，右侧展示当前工具结果和工作区产物预览。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, toRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { ChatDataContextCompressionEvent, ChatDataModelOption, ChatDataTokenUsage, DashboardSpecPayload, DataSource, ExecutionTargetOption, LangGraphMessage, Message, MessageAttachment, PendingUserQuestion, SemanticConfigSelectionInfo, SkillInfo, ThinkingSegment, ToolCallInfo, UserQuestionAnswer } from '../types'
import { useSqlGenerator } from '../composables/useSqlGenerator'
import { useConversationHistory } from '../composables/useConversationHistory'
import { useDeepThinking } from '../composables/useDeepThinking'
import { useSkillCommandMenu } from '../composables/useSkillCommandMenu'
import { answerUserQuestion, cancelConversationRun, cancelSteerMessage, cancelUserQuestion, createDashboardFromSpec, getChatDataConfig, getConversationWorkspaceFileContent, getConversationWorkspaceFiles, getConversationWorkspaceOfficeHtmlPreview, getConversationWorkspaceRawFile, getPendingUserQuestion, importConversationWorkspaceSkill, listSteerMessages, steerConversation, streamRunEvents, updateSteerMessage, uploadFile } from '../api'
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
import SaveToPanelModal from './SaveToPanelModal.vue'
import { parseDateTimeValue, formatLocalDateTime, hideSemanticConfigSelectionPayloads } from '../utils'
import { extractSemanticParamsFromArgs, getRenderableQueryResultData } from '../utils/semanticTool'
import { admitInboxMessages, reconcileInboxSnapshot, type InboxQueueItem, type InboxQueueType } from '../utils/inboxQueue'
import ToolCallDetailPanel from './ToolCallDetailPanel.vue'
import UserQuestionCard from './UserQuestionCard.vue'

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
}>()

const { generateSqlStream, stopGeneration } = useSqlGenerator()

const userOverridable = ref(false)
const defaultRuntimeOptions = ref<ChatDataRuntimeOptions>({ workspace_backend: 'local', bash_enabled: false })
const selectedRuntimeOptions = ref({ ...defaultRuntimeOptions.value })
const modelOptions = ref<ChatDataModelOption[]>([])
const selectedModel = computed({
  get: () => props.selectedModelId || '',
  set: (value: string) => emit('update:selectedModelId', value),
})
const { enableDeepThinking } = useDeepThinking()

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
const ownedRunStorageKey = 'chatdata-active-run-conversation'
const activeRunId = ref<string | null>(null)
const activeRunEventId = ref('0-0')
const runEventIdStorageKey = (conversationId: string, runId: string) => `chatdata-run-event-id:${conversationId}:${runId}`
let steerRestoreTimer: ReturnType<typeof setInterval> | null = null
const pendingSteerMessages = ref<InboxQueueItem[]>([])
const pendingUserQuestion = ref<PendingUserQuestion | null>(null)
const isSubmittingQuestion = ref(false)
const isStoppingRun = ref(false)
const tokenUsage = ref<ChatDataTokenUsage | null>(null)
const tokenUsagePanelRef = ref<InstanceType<typeof ConversationTokenUsage> | null>(null)
const hasLocalConversationStarted = ref(false)
const chatContainerRef = ref<HTMLDivElement | null>(null)
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

const startEditSteerMessage = (item: { content: string; messageId?: string }) => {
  if (!item.messageId || editingSteerId.value === item.messageId) return
  editingSteerId.value = item.messageId
  editingSteerDraft.value = item.content
}

/** 以服务端状态和持久历史共同恢复队列，不以 pending 暂时缺失作为删除依据。 */
const restorePendingSteerQueue = async (options?: { restartPolling?: boolean }) => {
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
      const storedEventId = window.sessionStorage.getItem(runEventIdStorageKey(conversationId, activeRunId.value))
      activeRunEventId.value = storedEventId || '0-0'
      if (!storedEventId && activeRunTurnId.value) {
        messages.value = messages.value.filter(message => (
          message.turnId !== activeRunTurnId.value || message.role === 'user'
        ))
        syncHistoryToolCalls()
      }
    } else {
      activeRunEventId.value = '0-0'
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
  await restorePendingSteerQueue({ restartPolling: false })
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
    restorePendingSteerQueue({ restartPolling: false })
  }, 5000)
}

watch(isGenerating, (generating) => {
  if (!generating) {
    // 本轮结束：队列消息可能已被系统取走或新增，同步一次
    restorePendingSteerQueue()
  }
})

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
    ElMessage.error(error instanceof Error ? error.message : '修改队列消息失败')
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
    ElMessage.error(error instanceof Error ? error.message : '切换队列类型失败')
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
    ElMessage.error(error instanceof Error ? error.message : '移除队列消息失败')
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
})
const isNearBottom = ref(true)

// ---- 文件上传 ----
const ACCEPTED_FILE_TYPES = ['.txt', '.md', '.sql', '.docx', '.xlsx', '.pptx', '.pdf']
const MAX_FILE_SIZE = 50 * 1024 * 1024
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

const toolCallsMap = ref<Map<string, ToolCallInfo>>(new Map())
const activeToolCallId = ref<string | null>(null)
const sidePanelOpen = ref(false)
const sidePanelTab = ref<'console' | 'tool'>('console')
const selectedConsoleFilePath = ref<string | null>(null)
const collapsedConsoleDirectories = ref<Set<string>>(new Set(['uploads']))
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
const showSaveModal = ref(false)
const saveSql = ref('')
const saveChartType = ref('table')
const saveMemberDatasourceId = ref('')
const saveMemberDatasourceIds = ref<string[]>([])
const handleSaveToDashboard = (
  sql: string,
  chartType: string,
  memberDatasourceId?: string,
  memberDatasourceIds?: string[],
) => {
  saveSql.value = sql
  saveChartType.value = chartType || 'table'
  saveMemberDatasourceId.value = String(memberDatasourceId || '').trim()
  const executedMemberIds = (memberDatasourceIds || [])
    .map(item => String(item || '').trim())
    .filter(Boolean)
  saveMemberDatasourceIds.value = executedMemberIds.length > 1
    ? executedMemberIds
    : (saveMemberDatasourceId.value ? [saveMemberDatasourceId.value] : [])
  showSaveModal.value = true
}
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
    const matched = message.toolCalls?.find(item => item.tool_call_id === id)
    if (matched) return matched
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
const sidePanelTitle = computed(() => sidePanelTab.value === 'console' ? '控制台' : '工具详情')
const sidePanelSubtitle = computed(() => (
  sidePanelTab.value === 'console'
    ? producedFileTree.value.root
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

const pushProducedFile = (files: ProducedFile[], path: string, source: string, status: string) => {
  const relativePath = normalizeVisiblePath(path)
  const rootDirectory = relativePath.split('/')[0] || ''
  if (!['uploads', 'workspace', 'outputs'].includes(rootDirectory)) return
  const displayPath = relativePath ? toSandboxDisplayPath(relativePath) : ''
  if (!path || !relativePath) return
  files.push({ path, fullPath: displayPath, relativePath, displayPath, source, status })
}

const producedFiles = computed(() => {
  const files: ProducedFile[] = []
  for (const file of workspaceFiles.value) {
    pushProducedFile(files, file.relative_path || file.path, file.source || 'workspace', 'completed')
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

type PresentedDeliveryArtifactType = 'document' | 'spreadsheet' | 'presentation' | 'markdown'

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
      [path]: response.data?.content || '',
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

const handleOpenPresentedArtifact = async (artifact: PresentedDeliveryArtifact) => {
  await handleOpenAttachment({
    file_id: `presented:${artifact.path}`,
    filename: artifact.name,
    file_type: artifact.name.split('.').pop()?.toLowerCase(),
    original_path: artifact.path,
  })
}

const handleDownloadPresentedArtifact = async (artifact: PresentedDeliveryArtifact) => {
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
  fillSemanticFieldQuestion,
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

const { isLoadingHistory, loadedConversationId, loadHistory, resetHistory } = useConversationHistory({
  conversationId: conversationIdRef,
  messages,
  tokenUsage,
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

const handleScroll = () => {
  const container = chatContainerRef.value
  if (!container) return
  const { scrollTop, scrollHeight, clientHeight } = container
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 100
}

const isToolErrorResult = (content?: string) => {
  const text = String(content || '').trim()
  return text.includes('执行失败') || text.includes('工具错误') || text.toLowerCase().startsWith('error:')
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

const loadModelOptions = async () => {
  try {
    const response = await getChatDataConfig()
    const models = response.data?.models || []
    const cfg = response.data?.runtime_options
    defaultRuntimeOptions.value = {
      workspace_backend: cfg?.workspace_backend === 'sandbox' ? 'sandbox' : 'local',
      bash_enabled: !!cfg?.bash_enabled,
    }
    userOverridable.value = !!response.data?.user_overridable
    selectedRuntimeOptions.value = { ...defaultRuntimeOptions.value }
    modelOptions.value = models
    const defaultModel = models.find(item => item.is_default)?.id || models[0]?.id || ''
    if (defaultModel && !models.some(item => item.id === selectedModel.value)) {
      selectedModel.value = defaultModel
    }
  } catch (err) {
    modelOptions.value = []
    console.error('加载模型列表失败:', err)
  }
}

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
    if (selectedConsoleIsMarkdown.value) {
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

const appendContextCompressionEvent = (event: ChatDataContextCompressionEvent) => {
  if (!event.id || messages.value.some(message => message.id === event.id)) return
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
  if ((!requestText.trim() && !options.fileIds?.length && !options.responseFactory) || isGenerating.value) return
  const isFirstMessage = options.appendUserMessage !== false
    && !messages.value.some(item => item.role === 'user')
  const effectiveSemanticConfigSelection = options.semanticConfigSelection !== undefined
    ? options.semanticConfigSelection
    : (props.semanticConfigSelection || null)
  const userMessage: Message = {
    id: `user-${Date.now()}`,
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
      'query',
      (msg) => handleLangGraphMessage(msg),
      () => {
        finalizeUnfinishedToolCalls('completed')
        finishStreamingMessages()
        scheduleWorkspaceFilesRefresh()
      },
      () => {},
      (error) => {
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
      (toolInfo) => {
        if (!toolInfo.tool_call_id) return
        const existing = toolCallsMap.value.get(toolInfo.tool_call_id)
        const updated: ToolCallInfo = {
          tool_call_id: toolInfo.tool_call_id,
          tool_name: toolInfo.tool_name || existing?.tool_name || 'tool_call',
          args: { ...(existing?.args || {}), ...(toolInfo.args || {}) },
          result: existing?.result,
          status: 'running',
        }
        upsertToolMessage(updated)
      },
      options.semanticConfigIdOverride !== undefined ? options.semanticConfigIdOverride : (props.semanticConfigId || null),
      selectedModelValue.value,
      selectedMemberDatasourceIds.value,
      'chat_data',
      options.skillName || null,
      undefined,
      (payload) => {
        if (payload.usage) tokenUsage.value = payload.usage
        updateAssistantFinishedAt(assistantMessageId, payload.timestamp)
      },
      (usage) => {
        tokenUsage.value = usage
      },
      effectiveSemanticConfigSelection,
      enableDeepThinking.value,
      selectedRuntimeOptions.value,
      (event) => appendContextCompressionEvent(event),
      options.fileIds,
      (event) => {
        if (!event.tool_call_id) return
        const existing = toolCallsMap.value.get(event.tool_call_id)
        const isFailed = event.type === 'tool_failed'
        const isCompleted = event.type === 'tool_completed'
        const toolInfo: ToolCallInfo = {
          tool_call_id: event.tool_call_id,
          tool_name: event.tool_name || existing?.tool_name || 'tool_call',
          args: { ...(existing?.args || {}), ...(event.args || {}), ...(event.accumulated_args || {}) },
          result: event.content || event.result || existing?.result,
          progressMessage: event.message || existing?.progressMessage,
          progress: typeof event.progress === 'number' ? event.progress : existing?.progress,
          status: isFailed ? 'error' : isCompleted ? 'completed' : 'running',
        }
        upsertToolMessage(toolInfo)
      },
      (messageIds) => {
        admittedInboxMessageIds.value = new Set([...admittedInboxMessageIds.value, ...messageIds])
        admitPendingMessages(messageIds)
      },
      (question) => {
        pendingUserQuestion.value = question
        hasActiveRun.value = true
        emit('runStatusChanged', resolvedConversationId, 'waiting_user')
        const active = activeAssistantMessageId.value
          ? messages.value.find(item => item.id === activeAssistantMessageId.value)
          : null
        if (active?.role === 'assistant') active.isStreaming = false
      },
      (runId) => {
        startedRunId = runId
        if (activeRunId.value !== runId) activeRunEventId.value = '0-0'
        activeRunId.value = runId
        saveOwnedRun(resolvedConversationId, runId)
        emit('runStatusChanged', resolvedConversationId, 'active')
      },
      options.responseFactory,
      (eventId) => {
        activeRunEventId.value = eventId
        window.sessionStorage.setItem(runEventIdStorageKey(resolvedConversationId, activeRunId.value || ''), eventId)
      },
      () => {
        if (isFirstMessage) emit('firstMessageStarted', resolvedConversationId)
      },
      async () => {
        activeAssistantMessageId.value = null
        await loadHistory(resolvedConversationId, true)
        syncHistoryToolCalls()
      },
    )
  } catch (err) {
    const errorMessage = err instanceof Error ? err.message : '发送失败'
    setActiveAssistantError(errorMessage)
  } finally {
    isGenerating.value = false
    await restorePendingSteerQueue({ restartPolling: false })
    if (options.autoResumeQueuedRun !== false) {
      await resumeQueuedRunAfterCompletion(convId, startedRunId || activeRunId.value)
    }
  }
}

/**
 * Successful Run completion creates the next Run transactionally when its
 * inbox still has messages. Subscribe to that Run separately so one SSE
 * stream never contains multiple assistant turns.
 */
const resumeQueuedRunAfterCompletion = async (conversationId: string | null, completedRunId: string | null) => {
  if (!conversationId || !completedRunId || isStoppingRun.value) return
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise(resolve => window.setTimeout(resolve, 100))
    await restorePendingSteerQueue({ restartPolling: false })
    if (!hasActiveRun.value || !activeRunId.value || activeRunId.value === completedRunId) continue
    if (activeRunStatus.value === 'waiting_user' || activeRunStatus.value === 'cancelling') return
    const nextRunId = activeRunId.value
    // The previous assistant bubble is complete. The next Run gets its own
    // bubble; its queued user messages arrive through inbox_admitted.
    activeAssistantMessageId.value = null
    await submitAnalysisQuery('', {
      appendUserMessage: false,
      preserveAssistant: false,
      autoResumeQueuedRun: false,
      responseFactory: signal => streamRunEvents(
        conversationId,
        nextRunId,
        activeRunEventId.value,
        signal,
      ),
    })
    return
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

const handleSubmit = async () => {
  if (isStoppingRun.value) return
  if (activeRunStatus.value === 'cancelling') {
    ElMessage.info('当前任务正在停止，请稍后再发送')
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
    attachments: attachments.length > 0 ? attachments : undefined,
  })
}

const handleStop = async () => {
  if (isStoppingRun.value) return
  const conversationId = localConversationId.value || props.conversationId
  isStoppingRun.value = true
  stopGeneration()
  finalizeUnfinishedToolCalls('error', '已停止')
  finishStreamingMessages()
  activeAssistantMessageId.value = null
  try {
    if (conversationId) {
      if (activeRunId.value) await cancelConversationRun(conversationId, activeRunId.value)
      for (let attempt = 0; attempt < 24; attempt += 1) {
        await new Promise(resolve => window.setTimeout(resolve, 250))
        await restorePendingSteerQueue({ restartPolling: false })
        if (!hasActiveRun.value) break
      }
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '停止运行失败')
  } finally {
    isGenerating.value = false
    isStoppingRun.value = false
    await restorePendingSteerQueue({ restartPolling: false })
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
                @select-query="(query: string) => input = query"
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

      <div v-show="!isLoadingHistory || messages.length > 0" class="p-4 bg-gray-50 border-t border-slate-100">
        <div class="max-w-[774px] mx-auto w-[90%]">
          <div
            v-if="pendingSteerMessages.length"
            class="mb-2 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm"
          >
            <div class="flex items-center gap-2 border-b border-slate-100 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              <i class="fa-solid fa-chevron-down text-[9px]"></i>
              <span>{{ pendingSteerMessages.length }} 个队列任务</span>
              <span class="ml-auto text-[10px] text-slate-400">发送默认下一轮处理，可手动立即插入</span>
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
                    v-if="item.messageId"
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
            :placeholder="hasLocalConversationStarted || messages.length > 0 ? '继续输入任务，输入 / 可选择技能' : '请输入任务，输入 / 可选择技能'"
            :selected-skill-title="selectedSkillTitle"
            :runtime-options="selectedRuntimeOptions"
            :user-overridable="userOverridable"
            :model-options="modelOptions"
            :is-generating="isGenerating"
            :can-submit="isGenerating ? Boolean(input.trim() && !isSteering) : Boolean((input.trim() || hasCompletedPendingFiles) && selectedDatasource)"
            :input-focused="inputFocused"
            :show-skill-button="!isGenerating"
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
      <ConversationTurnNavigator :messages="messages" :container="chatContainerRef" />
    </div>

    <ConversationSidePanel
      :open="sidePanelOpen"
      :title="sidePanelTitle"
      :subtitle="sidePanelSubtitle"
      :show-trigger="false"
      @open="openConsolePanel"
      @close="sidePanelOpen = false"
    >
        <template v-if="sidePanelTab === 'console'">
          <div class="mb-3 rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div class="mb-2 flex items-center justify-between">
              <div class="text-xs font-medium text-slate-500">工作区文件</div>
              <div class="text-[10px] text-slate-400">{{ isWorkspaceFilesLoading ? '加载中' : `${producedFiles.length} 个文件` }}</div>
            </div>
            <div class="rounded-md bg-white px-2 py-2 text-xs">
              <div class="flex h-7 items-center gap-2 rounded px-1.5 font-mono text-slate-700">
                <i class="fa-regular fa-folder-open w-4 text-center text-amber-500"></i>
                <span class="font-medium">{{ producedFileTree.root }}</span>
              </div>
              <div class="mt-1 max-h-[300px] space-y-0.5 overflow-auto pr-1">
                <div
                  v-for="directory in producedFileTree.directories"
                  :key="directory.name"
                >
                  <button
                    type="button"
                    class="flex h-7 w-full items-center gap-1.5 rounded border-none bg-transparent px-1.5 text-left text-slate-700 hover:bg-slate-50"
                    @click="toggleConsoleDirectory(directory.path)"
                  >
                    <i class="w-3 text-[10px] text-slate-400" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                    <i class="fa-regular w-4 text-center text-amber-500" :class="isConsoleDirectoryCollapsed(directory.name) ? 'fa-folder' : 'fa-folder-open'"></i>
                    <span class="truncate font-mono font-semibold">{{ directory.name }}</span>
                    <span class="shrink-0 text-slate-400">{{ directory.label }}</span>
                    <span v-if="getFileCount(directory)" class="ml-auto shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(directory) }}</span>
                  </button>
                  <div v-if="directory.children.length && !isConsoleDirectoryCollapsed(directory.name)" class="space-y-0.5">
                    <div
                      v-for="item in flattenFileTreeNodes(directory.children)"
                      :key="item.node.path"
                      class="flex min-w-0 items-center text-slate-600"
                    >
                      <button
                        type="button"
                        v-if="item.node.file"
                        :class="[
                          'flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded border-none bg-transparent px-1.5 text-left text-slate-600 hover:bg-blue-50 hover:text-blue-700',
                          selectedConsoleFilePath === item.node.file.relativePath ? 'bg-blue-50 text-blue-700' : ''
                        ]"
                        :style="{ paddingLeft: `${item.depth * 18 + 10}px` }"
                        @click="selectConsoleFile(item.node.file.relativePath)"
                      >
                        <span class="w-3"></span>
                        <i class="fa-regular fa-file-lines w-4 text-center text-slate-400"></i>
                        <span class="truncate font-mono text-[13px]">{{ item.node.name }}</span>
                      </button>
                      <div
                        v-else
                        role="button"
                        tabindex="0"
                        class="flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded border-none bg-transparent px-1.5 text-left text-slate-600 hover:bg-slate-50"
                        :style="{ paddingLeft: `${item.depth * 18 + 10}px` }"
                        @click="toggleConsoleDirectory(item.node.path)"
                        @keydown.enter.prevent="toggleConsoleDirectory(item.node.path)"
                        @keydown.space.prevent="toggleConsoleDirectory(item.node.path)"
                      >
                        <i class="w-3 text-[10px] text-slate-400" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-solid fa-chevron-right' : 'fa-solid fa-chevron-down'"></i>
                        <i class="fa-regular w-4 text-center text-amber-500" :class="isFileTreeNodeCollapsed(item.node.path) ? 'fa-folder' : 'fa-folder-open'"></i>
                        <span class="truncate font-mono text-[13px] font-semibold">{{ item.node.name }}</span>
                        <span v-if="getFileCount(item.node)" class="ml-auto shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] leading-none text-slate-500">{{ getFileCount(item.node) }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div v-if="!producedFiles.length" class="mt-2 rounded bg-slate-50 px-2 py-2 text-xs text-slate-400">
                暂无文件
              </div>
            </div>
          </div>

          <div v-if="selectedConsoleFile" class="rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div class="mb-2 flex items-center justify-between gap-2">
              <div class="min-w-0 truncate font-mono text-xs font-medium text-slate-500">
                {{ selectedConsoleFile?.displayPath }}
              </div>
              <div class="flex shrink-0 items-center gap-2">
                <div v-if="selectedConsoleFile && (selectedConsoleChartSpec || hasSelectedConsoleTable || selectedConsoleIsMarkdown)" class="flex rounded-md border border-slate-200 bg-white p-0.5">
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
                    v-if="selectedConsoleIsMarkdown"
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
                <div v-if="selectedConsoleFile" class="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{{ selectedConsoleFile.source }}</div>
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
          <div v-else class="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs text-slate-400">
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
