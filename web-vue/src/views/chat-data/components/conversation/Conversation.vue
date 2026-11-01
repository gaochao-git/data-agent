<script setup lang="ts">
/**
 * ChatData 会话页。
 * 左侧展示流式消息与工具调用，右侧展示当前工具结果和工作区产物预览。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, toRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { ChatDataContextCompressionEvent, ChatDataTokenUsage, DashboardSpecPayload, DataSource, ExecutionTargetOption, LangGraphMessage, Message, MessageAttachment, PendingUserQuestion, SemanticYamlFile, SkillInfo, SubagentCompaction, SubagentContextUsage, SubagentTrailItem, ThinkingSegment, ToolCallInfo, ToolLifecycleEventData, UserQuestionAnswer } from '../../types'
import { useSqlGenerator, type RunDoneStatus } from '../../composables/useSqlGenerator'
import { useConversationHistory } from '../../composables/useConversationHistory'
import { useModelOptions } from '../../composables/useModelOptions'
import { useSaveToPanel } from '../../composables/useSaveToPanel'
import { useSkillCommandMenu } from '../../composables/useSkillCommandMenu'
import { useWorkspaceFileMention, type WorkspaceFilePick } from '../../composables/useWorkspaceFileMention'
import { useQuotedSnippets } from '../../composables/useQuotedSnippets'
import { answerUserQuestion, cancelConversationRun, cancelSteerMessage, cancelUserQuestion, createDashboardFromSpec, getConversationWorkspaceFileContent, getConversationWorkspaceFiles, getConversationWorkspaceRawFile, getPendingUserQuestion, importConversationWorkspaceSkill, listSteerMessages, steerConversation, streamRunEvents, updateSteerMessage, uploadFile } from '../../api'
import type { Dashboard } from '../../api/dashboard'
import QuickQueries, { type FallbackQuery } from '../QuickQueries.vue'
import ChartSpecRenderer from '../ChartSpecRenderer.vue'
import SkillManager from './SkillManager.vue'
import ConversationMessageList from './ConversationMessageList.vue'
import ConversationWorkspacePanel from './ConversationWorkspacePanel.vue'
import ConversationInputBar from './ConversationInputBar.vue'
import ConversationTokenUsage from './ConversationTokenUsage.vue'
import ConversationTurnNavigator from './ConversationTurnNavigator.vue'
import SkillCommandMenu from './SkillCommandMenu.vue'
import WorkspaceFileMentionMenu from './WorkspaceFileMentionMenu.vue'
import PresentedFileArtifacts from './PresentedFileArtifacts.vue'
import SaveToPanelModal from '../SaveToPanelModal.vue'
import { parseDateTimeValue, formatLocalDateTime, toGenerationStats } from '../../utils'
import { normalizeToolCallForDisplay, shouldInlineRenderQueryResult } from '../../utils/semanticTool'
import { admitInboxMessages, reconcileInboxSnapshot, type InboxQueueItem, type InboxQueueType } from '../../utils/inboxQueue'
import UserQuestionCard from './UserQuestionCard.vue'
import AnalysisProgress from './AnalysisProgress.vue'
import { deriveAnalysisTodos } from '../../utils/analysisTodos'
import { createTopReachTracker } from '../../utils/scrollReach'
import { appendSubagentCompaction, buildSubagentCompactionRecord } from '../../utils/subagentCompaction'
import { isScrolledToBottom } from '../../utils/scrollFollow'
import { normalizeVisiblePath } from '../../utils/consoleFileTree'
import {
  getCardChartType,
  getCardLayoutText,
  getCardTitle,
  getChartPreviewTools as getChartPreviewToolsBase,
  getChartSpecFromTool as getChartSpecFromToolBase,
  getDashboardPreviewCards,
  getDashboardPreviewTitle,
  getDashboardPreviewWarningCount,
  getDashboardPreviewWritableCount,
  getDashboardPreviewTools as getDashboardPreviewToolsBase,
  getDashboardSpecFromTool as getDashboardSpecFromToolBase,
  getPresentedArtifacts,
  getPresentedArtifactKey,
  getPresentedArtifactPath,
  getPresentedArtifactTools,
  getPresentedDeliveryArtifacts,
  parseToolResultPayload,
  replaceVisiblePathsForDisplay,
} from '../../utils/messageArtifacts'
import { ACCEPTED_FILE_TYPES, MAX_FILE_SIZE } from '../../utils/uploadLimits'

interface Props {
  datasource?: DataSource | null
  conversationId?: string | null
  selectedDatasourceId?: string
  ensureConversationId?: () => Promise<string | null>
  ensureConversationDatasource?: () => Promise<boolean>
  executionTargets?: ExecutionTargetOption[]
  executionTargetMultiple?: boolean
  selectedMemberDatasourceIds?: string[]
  selectedModelId?: string
  /** 工作区 yaml「导入为语义模型」行操作的可见性（管理员权限，与配置入口同源） */
  canImportSemanticModel?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  datasource: null,
  executionTargets: () => [],
  executionTargetMultiple: true,
  selectedMemberDatasourceIds: () => [],
  selectedModelId: '',
  canImportSemanticModel: false,
})

const emit = defineEmits<{
  firstMessageStarted: [conversationId: string]
  runStatusChanged: [conversationId: string, status: 'pending' | 'active' | 'cancelling' | 'waiting_user' | null]
  'update:selectedMemberDatasourceIds': [value: string[]]
  'update:selectedModelId': [value: string]
  dashboardCreated: [dashboard: Dashboard]
  /** 控制台面板可见性广播：页面右上角的控制台开关按钮据此切换图标与提示。 */
  consoleVisibilityChanged: [visible: boolean]
  /** 工作区 yaml 一键导入：由页面层直接打开 Ossie 导入弹窗并预置文件 */
  importSemanticYaml: [files: SemanticYamlFile[]]
}>()

const { generateSqlStream, stopGeneration } = useSqlGenerator()

// 模型列表与运行时配置：与其他入口共用同一份加载逻辑；审批策略不提供前端切换。
const { modelOptions, load: loadModelOptions } = useModelOptions()
const selectedModel = computed({
  get: () => props.selectedModelId || '',
  set: (value: string) => emit('update:selectedModelId', value),
})
// 思考开关/档位为组件内临时状态（null=未触碰：请求不传字段，由后端按注册表
// default_on/default_effort 落点）。不持久化、不跨模型/跨会话携带——切换即重置，
// 默认永远以语义模型管理面板表里的配置为准。
const enableDeepThinking = ref<boolean | null>(null)
const thinkingEffort = ref<string | null>(null)
watch(selectedModel, () => {
  enableDeepThinking.value = null
  thinkingEffort.value = null
})

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
/** Run 活跃状态的合法取值：与 runStatusChanged emit 的契约保持一致。 */
type RunActiveStatus = 'pending' | 'active' | 'cancelling' | 'waiting_user'
const activeRunStatus = ref<RunActiveStatus | null>(null)
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

  if (status !== 'waiting_user') {
    // Run 已收口（finished/failed/cancelled）时，旧审批卡一律失效，
    // 避免用户面对一个再也提交不成功的历史问题卡。
    pendingUserQuestion.value = null
  }

  if (status === 'finished') {
    finalizeUnfinishedToolCalls('completed')
    finishStreamingMessages()
    scheduleWorkspacePanelRefresh()
  } else if (status === 'cancelled') {
    finalizeUnfinishedToolCalls('error', '已停止')
    finishStreamingMessages()
    activeAssistantMessageId.value = null
  } else if (status === 'waiting_user') {
    finishStreamingMessages()
  } else if (status === 'failed') {
    // 模型挂起等失败终态经 fallback done 到达时没有 error 事件兜底，
    // 必须在这里收尾占位消息，否则「正在处理...」会一直转下去。
    finalizeUnfinishedToolCalls('error')
    finishStreamingMessages()
    setActiveAssistantError('本轮执行失败，请重试')
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
    // 兜底 done（如 Redis 劣化时 question 事件未被读到）也可能走这里：
    // 事件缺失时卡片未填充，主动拉一次待答问题，避免用户面对「待确认」
    // 状态却没有可回答的卡片
    if (!pendingUserQuestion.value) void restorePendingUserQuestion()
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
    activeRunStatus.value = (response.data?.run_status ?? null) as RunActiveStatus | null
    activeRunTurnId.value = response.data?.turn_id || null
    activeRunIsContinuation.value = Boolean(response.data?.is_continuation)
    emit(
      'runStatusChanged',
      conversationId,
      hasActiveRun.value ? activeRunStatus.value : null,
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
  // 「用时」起点种子：正在生成的 run 在历史消息里有服务端盖章的用户消息时间戳
  // （= 客户端点击时刻），恢复订阅时从它起算，避免 ticker 从 0 重计。
  const elapsedSeedMs = [...messages.value].reverse().find(item => item.role === 'user')?.timestamp?.getTime()
  await submitAnalysisQuery('', {
      appendUserMessage: false,
      preserveAssistant: !activeRunIsContinuation.value,
      elapsedSeedMs,
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
  void recoverInterruptedPageRun()
  restorePendingUserQuestion()
})

onUnmounted(() => {
  stopSteerRestorePolling()
  stopLiveGenerationSpeed()
  // 卸载时中止主 SSE 流:useSqlGenerator 对 AbortError 静默返回,不会误报错误
  stopGeneration()
})
const isNearBottom = ref(true)

// ---- 文件上传 ----
// 上传格式/大小上限统一在 utils/uploadLimits.ts（与工作区面板上传共用），文本渲染上限随面板拆到子组件。
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

/** 剪贴板图片可能没有扩展名（部分平台给 image/png），按 MIME 补一个名字。 */
const normalizeClipboardFilename = (file: File): File => {
  const hasExt = /\.[a-z0-9]+$/i.test(file.name)
  if (hasExt) return file
  const mimeExt: Record<string, string> = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
    'image/gif': 'gif',
  }
  const ext = mimeExt[file.type]
  if (!ext) return file
  const timestamp = Date.now()
  return new File([file], `clipboard-${timestamp}.${ext}`, { type: file.type })
}

/** 校验（格式/大小）并上传单个文件，成功后进入 pendingFiles。 */
const uploadValidatedFile = async (file: File): Promise<boolean> => {
  const ext = '.' + (file.name.split('.').pop() || '').toLowerCase()
  if (!ACCEPTED_FILE_TYPES.includes(ext)) {
    ElMessage.warning(`仅支持 ${ACCEPTED_FILE_TYPES.join('、')} 格式`)
    return false
  }
  if (file.size > MAX_FILE_SIZE) {
    ElMessage.warning('文件大小不能超过 50MB')
    return false
  }

  try {
    let convId = props.conversationId || localConversationId.value
    if (!convId) {
      convId = (await props.ensureConversationId?.()) ?? null
      if (!convId) {
        ElMessage.error('创建会话失败，请稍后重试')
        return false
      }
      localConversationId.value = convId
      // 刚创建的新会话无历史，标记已加载，避免触发历史加载导致 isLoadingHistory 闪烁
      loadedConversationId.value = convId
    }
    const res = await uploadFile(convId, file)
    if (res.status !== 'success') {
      ElMessage.error(res.msg || '上传失败')
      return false
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
    scheduleWorkspacePanelRefresh()
    // 上传即登记：不再预解析/链接到工作区，agent 在需要时通过 parse_document 工具按需解析
    return true
  } catch {
    ElMessage.error('文件上传失败')
    return false
  }
}

const handleFileSelected = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''
  await uploadValidatedFile(file)
}

/** 输入框粘贴截图/图片：走与手动上传相同的校验与上传链路。 */
const handlePasteFiles = async (files: File[]) => {
  for (const raw of files) {
    await uploadValidatedFile(normalizeClipboardFilename(raw))
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
    scheduleWorkspacePanelRefresh()
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
  /** @ 引用文件快照（相对路径列表）；未传时按当前 chip 状态构建。 */
  fileSelections?: string[] | null
  /** 划选引用片段快照（[{text}]）；未传时按当前 chip 状态构建，随请求透传服务端。 */
  quotedSnippets?: Array<{ text: string }> | null
  assistantMessageId?: string
  appendUserMessage?: boolean
  fileIds?: string[]
  queuedMessageId?: string | null
  preserveQueuedMessages?: boolean
  userMessageId?: string
  attachments?: MessageAttachment[]
  /** 恢复场景的「用时」起点种子（服务端盖章的 run 点击时刻，毫秒）；未传时从本地当下起算。 */
  elapsedSeedMs?: number
  responseFactory?: (signal: AbortSignal) => Promise<Response>
  preserveAssistant?: boolean
  interruptedQuestion?: PendingUserQuestion
  autoResumeQueuedRun?: boolean
}

const resetTokenUsagePanel = (clearUsage = true) => {
  if (clearUsage) tokenUsage.value = null
  tokenUsagePanelRef.value?.closePanel()
}

// 顶层工具调用状态注册表：只存顶层调用。子智能体内层工具不进此 map——
// 它们挂在父 task 调用的 subagentTrail 上（见 upsertSubagentTrailItem），
// 详情查找经 findToolCallById 的 trail 兜底完成。
const toolCallsMap = ref<Map<string, ToolCallInfo>>(new Map())
const activeToolCallId = ref<string | null>(null)
const sidePanelOpen = ref(false)
// 工作区文件面板整体拆到 ConversationWorkspacePanel.vue：文件列表/树/预览/上传/下载/删除
// 与刷新调度都在子组件内闭环，父级只通过 ref 调用刷新与打开入口。
const workspacePanelRef = ref<InstanceType<typeof ConversationWorkspacePanel> | null>(null)
const presentedArtifactContents = ref<Record<string, string>>({})
const loadedPresentedArtifactKeys = ref<Set<string>>(new Set())
const loadingPresentedArtifactKeys = ref<Set<string>>(new Set())
const isSkillManagerOpen = ref(false)
const importedSkillIdForManager = ref<string | null>(null)
const importingWorkspaceSkillPath = ref<string | null>(null)
const importedWorkspaceSkillIds = ref<Record<string, string>>({})
const activeAssistantMessageId = ref<string | null>(null)
const localConversationId = ref<string | null>(props.conversationId || null)
const conversationIdRef = toRef(props, 'conversationId')
// 切换会话同样重置思考意图：显式选择不跨会话记忆（默认永远以注册表配置为准）
watch(() => props.conversationId || localConversationId.value, () => {
  enableDeepThinking.value = null
  thinkingEffort.value = null
})
const creatingDashboardToolCallId = ref<string | null>(null)
const createdDashboardByToolId = ref<Record<string, Dashboard>>({})

const selectedDatasource = computed(() => props.selectedDatasourceId || props.datasource?.id || '')
// 产物内容缓存与会话上下文在这里绑定，模板保持单参调用（纯函数在 utils/messageArtifacts.ts）。
const dashboardSpecContext = computed(() => ({
  datasourceId: selectedDatasource.value,
  // 会话级单选已下线（@ 引用是唯一显式指定通道），看板语义模型以工具
  // 结果 payload 自带的 semantic_config_id 为准，这里恒为 null。
  semanticConfigId: null,
  conversationId: props.conversationId || localConversationId.value || null,
}))
const getDashboardSpecFromTool = (tool?: ToolCallInfo | null) =>
  getDashboardSpecFromToolBase(tool, presentedArtifactContents.value, dashboardSpecContext.value)
const getChartSpecFromTool = (tool?: ToolCallInfo | null) =>
  getChartSpecFromToolBase(tool, presentedArtifactContents.value)
const getDashboardPreviewTools = (message: Message) =>
  getDashboardPreviewToolsBase(message, presentedArtifactContents.value, dashboardSpecContext.value)
const getChartPreviewTools = (message: Message) =>
  getChartPreviewToolsBase(message, presentedArtifactContents.value)
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
const buildSkillRequestText = (text: string, skillNames: string[]) => {
  const normalizedText = String(text || '').trim()
  if (!skillNames.length) return normalizedText
  const prefix = skillNames.map(name => `「${name}」`).join('')
  return `使用${prefix}技能回答：${normalizedText}`
}
const {
  selectedSkills,
  selectedSkillTitles,
  commandSkills,
  displayedCommandSkills,
  isCommandSkillLoading,
  highlightedSkillIndex,
  showSkillCommand,
  loadCommandSkills,
  refreshCommandSkills,
  selectCommandSkill: selectCommandSkillBase,
  replaceSelectedSkill,
  removeSelectedSkill,
  clearSelectedSkills,
  handleSkillCommandKeyDown,
} = useSkillCommandMenu({
  input,
  enabled: computed(() => true),
})

// ---- 输入框 @ 引用工作区文件（多选）----
// @ 菜单数据源是当前会话工作区（uploads/outputs/workspace）可见文件，平铺展示（文件名 + 目录路径）；
// 「工作区有什么就能引用什么」：新建会话工作区为空时菜单给空态说明，物化/产出后即可引用。
// 选中信息随消息走 file_selections（相对路径列表），服务端校验白名单后并入正文短句。
const mentionWorkspaceFiles = ref<WorkspaceFilePick[]>([])

const resolveMentionConversationId = () => props.conversationId || localConversationId.value || ''

const loadMentionWorkspaceFiles = async () => {
  const conversationId = resolveMentionConversationId()
  if (!conversationId) {
    mentionWorkspaceFiles.value = []
    return
  }
  try {
    const res = await getConversationWorkspaceFiles(conversationId)
    if (res.status === 'success') {
      mentionWorkspaceFiles.value = (res.data?.files || []).map(file => ({
        relativePath: file.relative_path,
        name: file.name,
      }))
    }
  } catch (err) {
    console.error('加载工作区文件列表失败:', err)
  }
}

const {
  mentionedFiles,
  displayedMentionFiles,
  showMentionMenu,
  highlightedFileIndex,
  selectMentionFile,
  removeMentionFile,
  clearMentionFiles,
  handleMentionMenuKeyDown,
} = useWorkspaceFileMention({
  input,
  files: mentionWorkspaceFiles,
})

// 菜单打开（输入 @）时刷新文件清单，保证引用的是工作区最新内容。
// 不在此触发建会话/物化：@ 只引用当前工作区已有文件，没有就走空态提示；
// 会话落库与物化统一由发送首条消息的兜底链路完成。
watch(showMentionMenu, (open) => {
  if (open) void loadMentionWorkspaceFiles()
})

watch(() => resolveMentionConversationId(), () => {
  mentionWorkspaceFiles.value = []
})

/** @ 引用选中：进 chip 列表（可重复 @ 追加多个）。 */
const handleMentionFileSelect = (file: WorkspaceFilePick) => {
  selectMentionFile(file)
}

/** 移除单个 @ 引用 chip。 */
const handleMentionFileClear = (relativePath: string) => {
  removeMentionFile(relativePath)
}

/** 发送链路用的引用快照：只含输入框 @ 引用 chip 的相对路径。 */
const buildMentionFileSelections = (): string[] => {
  return mentionedFiles.value.map(file => file.relativePath)
}

// ---- 划选引用：助手气泡划选 → 浮层「添加到对话」→ chip；发送时随请求透传服务端 ----
const {
  quotedSnippets,
  addQuotedSnippet,
  removeQuotedSnippet,
  clearQuotedSnippets,
} = useQuotedSnippets()

/** 浮层「添加到对话」回调：片段进 chip 列表并聚焦输入框；超限/重复时提示。 */
const handleAddQuoteSnippet = (text: string) => {
  if (!addQuotedSnippet(text)) {
    const isDuplicate = quotedSnippets.value.some(item => item.text === text.trim())
    ElMessage.warning(isDuplicate ? '该片段已在引用列表中' : '引用片段已达上限（5 条），请先发送或移除')
    return
  }
  focusInput()
}

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
          started_at_ms: inner.started_at_ms ?? null,
          ended_at_ms: inner.ended_at_ms ?? null,
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

// 查询结果渲染口径统一在 utils/semanticTool（主对话 / 子智能体过程共用）
const shouldInlineRenderToolCall = (toolCall?: ToolCallInfo | null): boolean => shouldInlineRenderQueryResult(toolCall)

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

// 工作区文件面板的刷新入口：调度（防抖）与手动兜底都转发给子组件，
// 工具完成后的自动刷新（tool_completed / 上传 / 预览失效）沿用原调用点。
const scheduleWorkspacePanelRefresh = () => {
  workspacePanelRef.value?.scheduleRefresh()
}

// 工具写文件后的预览强制重载：仅当写的是当前预览文件时子组件才会真正发请求。
const reloadWorkspacePreviewIfSelected = (toolInfo?: ToolCallInfo | null) => {
  workspacePanelRef.value?.reloadPreviewIfSelected(toolInfo)
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

const isDashboardPreviewWritable = (tool: ToolCallInfo, payload: DashboardSpecPayload | null): boolean => {
  return tool.status === 'completed' && getDashboardPreviewWritableCount(payload) > 0 && !createdDashboardByToolId.value[tool.tool_call_id]
}

const resolveDashboardPreviewSemanticConfigId = (payload: DashboardSpecPayload): string | null => {
  const specId = String(payload.dashboard_spec?.semantic_config_id || '').trim()
  if (specId) return specId
  return String(payload.semantic_config_id || '').trim() || null
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
      semantic_config_id: resolveDashboardPreviewSemanticConfigId(payload),
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

const selectToolCall = (toolCall: ToolCallInfo) => {
  // 右侧面板是 tab 容器：点工具只负责打开/激活对应 tab，不再抢占其他 tab 的内容。
  activeToolCallId.value = toolCall.tool_call_id
  sidePanelOpen.value = true
  workspacePanelRef.value?.openTool(toolCall)
}

const openConsolePanel = () => {
  // 只负责展开：保持上次停留的 tab（关闭只卸载 aside，tab 状态还在），不强制回到「文件管理」
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
  // 选中、缺文件先刷列表、直载内容与面板打开都在子组件内完成（openFile 内部 emit 更新 open/tab）
  await workspacePanelRef.value?.openFile(relativePath)
}

// 面板内上传触发的新建会话：与附件上传同一口径，同步本地会话 ID 与历史加载标记，
// 避免子组件刷新文件列表时又触发一次历史加载（isLoadingHistory 闪烁）。
const handlePanelConversationCreated = (conversationId: string) => {
  localConversationId.value = conversationId
  loadedConversationId.value = conversationId
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
const fillSemanticFieldQuestion = (payload: { fieldName: string; fieldType: 'dimension' | 'metric' }) => {
  const fieldName = String(payload?.fieldName || '').trim()
  if (!fieldName) return
  clearSelectedSkills()
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
    activeRunStatus.value = (activeRun?.status || null) as RunActiveStatus | null
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

// 距底部阈值内视为「贴底」：与右侧子智能体过程共用 utils/scrollFollow 的同一口径


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
  replaceSelectedSkill(skill)
  inputBarRef.value?.focus()
}

// 技能管理「新建」→ 对话式创建：关闭弹窗，挂上 skill-creator 技能并预填引导语，
// 用户补全「」中的目标后发送，由技能生成技能引导完成创建（对齐 WorkBuddy 体验）。
const SKILL_CREATOR_NAME = 'skill-creator'
const SKILL_CREATE_PROMPT = '请帮我创建一个可以实现「……」的技能'
const startSkillCreation = async () => {
  isSkillManagerOpen.value = false
  if (!commandSkills.value.length) await loadCommandSkills()
  const creator = commandSkills.value.find(skill => skill.name === SKILL_CREATOR_NAME) || null
  if (creator) replaceSelectedSkill(creator)
  input.value = SKILL_CREATE_PROMPT
  inputBarRef.value?.focus()
}

// 当前选中模型不在注册表中时回填默认模型（如配置变更后）
watch(modelOptions, (models) => {
  const defaultModel = models.find(item => item.is_default)?.id || models[0]?.id || ''
  if (defaultModel && !models.some(item => item.id === selectedModel.value)) {
    selectedModel.value = defaultModel
  }
}, { immediate: true })

onMounted(() => {
  loadModelOptions()
})

const selectCommandSkill = (skill: SkillInfo) => {
  selectCommandSkillBase(skill)
  inputBarRef.value?.focus()
}

const clearSelectedSkill = (name: string) => {
  removeSelectedSkill(name)
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
      presentedArtifactContents.value = {}
      loadedPresentedArtifactKeys.value = new Set()
      loadingPresentedArtifactKeys.value = new Set()
      activeToolCallId.value = null
      sidePanelOpen.value = false
          activeAssistantMessageId.value = null
      resetTokenUsagePanel()
      resetHistory()
      clearQuotedSnippets()
      // 清空会话后视口回到顶部即底部，重置跟随状态，避免「回到最新」按钮残留；
      // 自动翻页的越界观测值一并复位，避免带着上一个会话的位置误判「已越过顶部」
      isNearBottom.value = true
      topReach.reset()
      focusInput()
    }
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

// 生成统计（首 token / 输出 tokens / 时长 / tok/s）：后端服务端口径，message_end 随
// payload 下发（历史回放走 useConversationHistory 从持久化消息读同一份），校验转换后挂载。
const attachGenerationStats = (messageId: string, rawStats: unknown) => {
  const message = messages.value.find(item => item.id === messageId && item.role === 'assistant')
    ?? (activeAssistantMessageId.value
      ? messages.value.find(item => item.id === activeAssistantMessageId.value && item.role === 'assistant')
      : null)
  if (message?.role === 'assistant') {
    message.generationStats = toGenerationStats(rawStats) ?? undefined
  }
}

// ---- 生成中的实时速率与执行耗时 ----
// 实时速率由后端滑窗口径推送（generation_speed SSE 事件，最近 2s 的模型输出粗估）；
// 执行耗时是前端本地 ticker：从本轮发起起算的墙钟实时递增，message_end 收口后由
// generation_stats.totalDurationMs（服务端总墙钟，随消息持久化）接管，二者是两个来源。
const liveGenerationSpeed = ref<number | null>(null)
const liveElapsedMs = ref<number | null>(null)
let elapsedTickerId: ReturnType<typeof setInterval> | null = null

const stopLiveGenerationSpeed = () => {
  liveGenerationSpeed.value = null
  if (elapsedTickerId !== null) {
    clearInterval(elapsedTickerId)
    elapsedTickerId = null
  }
  liveElapsedMs.value = null
}

const startLiveElapsedTicker = (startedAtMs?: number) => {
  stopLiveGenerationSpeed()
  // 起点：恢复场景用种子（服务端盖章的点击时刻，与本页时钟同源校准过），
  // 否则从本地当下起算。种子来自未来（时钟漂移）时退回本地当下。
  const now = Date.now()
  const runStartedAtMs =
    typeof startedAtMs === 'number' && Number.isFinite(startedAtMs) && startedAtMs > 0 && startedAtMs <= now
      ? startedAtMs
      : now
  liveElapsedMs.value = Math.max(now - runStartedAtMs, 0)
  elapsedTickerId = setInterval(() => {
    liveElapsedMs.value = Date.now() - runStartedAtMs
  }, 500)
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
    scheduleWorkspacePanelRefresh()
    loadPresentedArtifacts(localConversationId.value)
    // 改的就是正在预览的文件时，预览内容立刻跟着刷新，不等用户手动兜底
    reloadWorkspacePreviewIfSelected(displayToolInfo)
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

  /** 只更新父 task 调用上的字段（不碰 trail），同一事件流内收敛到最新值。 */
  const applySubagentToolPatch = (parentId: string, patch: (tool: ToolCallInfo) => ToolCallInfo | null) => {
    const base = toolCallsMap.value.get(parentId) || {
      tool_call_id: parentId,
      tool_name: 'task',
      args: {},
      status: 'running',
    }
    const merged = patch(base)
    if (!merged) return
    toolCallsMap.value.set(parentId, merged)
    for (const message of messages.value) {
      if (!message.toolCalls?.some(item => item.tool_call_id === parentId)) continue
      message.toolCalls = message.toolCalls.map(item => item.tool_call_id === parentId ? merged : item)
    }
  }

  // 子智能体上下文用量：只更新父 task 调用上的快照字段（标题行徽标）
  const upsertSubagentContextUsage = (event: ToolLifecycleEventData) => {
    const parentId = event.parent_tool_call_id
    if (!parentId || typeof event.used_tokens !== 'number') return
    const usage: SubagentContextUsage = {
      used_tokens: event.used_tokens,
      threshold_tokens: event.threshold_tokens ?? 0,
      context_window: event.context_window,
      percent: event.percent,
      budget_percent: event.budget_percent,
    }
    applySubagentToolPatch(parentId, tool => ({ ...tool, subagentContextUsage: usage }))
  }

  // 子图压缩记录：按 id 去重追加到父 task 调用（过程面板展示「已压缩 N 次」）
  const upsertSubagentCompaction = (event: ToolLifecycleEventData) => {
    const parentId = event.parent_tool_call_id
    const record = buildSubagentCompactionRecord(event)
    if (!parentId || !record) return
    applySubagentToolPatch(parentId, (tool) => {
      const next = appendSubagentCompaction(tool.subagentCompactions, record)
      return next ? { ...tool, subagentCompactions: next } : null
    })
  }

  if (event.type === 'subagent_context_usage') {
    upsertSubagentContextUsage(event)
    return
  }

  if (event.type === 'subagent_context_compression') {
    upsertSubagentCompaction(event)
    return
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
    started_at_ms: event.started_at_ms ?? null,
    ended_at_ms: event.ended_at_ms ?? null,
  }
  commitTrail((trail) => {
    const idx = trail.findIndex(item => item.id === entryId)
    if (idx >= 0) {
      const prev = trail[idx]
      if (!prev) return trail
      trail[idx] = {
        ...prev,
        ...entry,
        args: { ...(prev.args || {}), ...(entry.args || {}) },
        // tool_started 事件不带计时，完成事件才补齐：缺省时保留已有值
        started_at_ms: entry.started_at_ms ?? prev.started_at_ms ?? null,
        ended_at_ms: entry.ended_at_ms ?? prev.ended_at_ms ?? null,
      }
    } else {
      trail.push(entry)
    }
    return trail
  })

  // 子智能体内部工具落盘（如 OfficeCLI 写 docx）后同步刷新控制台文件树，
  // 与主智能体工具完成后的刷新行为保持一致；schedule 内部有 500ms 防抖
  if (event.type === 'tool_completed') {
    scheduleWorkspacePanelRefresh()
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
        display_name: tc.display_name || existing?.display_name,
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
      display_name: msg.display_name || existing?.display_name,
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
  options: AnalysisSubmitOptions & { skillNames?: string[]; messageSkills?: Message['skills']; restoreInputText?: string } = {}) => {
  if ((!requestText.trim() && !options.fileIds?.length && !options.responseFactory && !options.queuedMessageId) || isGenerating.value) return
  const isFirstMessage = options.appendUserMessage !== false
    && !messages.value.some(item => item.role === 'user')
  // @ 引用文件快照：options.fileSelections 是调用方（重试/排队消息）传入的快照
  const mentionFileSelections = options.fileSelections !== undefined
    ? (options.fileSelections || [])
    : buildMentionFileSelections()
  // 划选引用快照：随消息透传服务端（存引用、模型可见标记块由后端统一追加）
  const quotedSnippetsSnapshot = options.quotedSnippets !== undefined
    ? (options.quotedSnippets || [])
    : quotedSnippets.value.map(item => ({ text: item.text }))
  const userMessage: Message = {
    id: options.userMessageId || `user-${Date.now()}`,
    role: 'user',
    content: requestText,
    timestamp: new Date(),
    skills: options.messageSkills,
    attachments: options.attachments,
  }
  // 本轮 @ 引用了工作区文件时，随用户消息留痕供气泡 chip 展示
  if (mentionFileSelections.length > 0) {
    userMessage.fileSelections = mentionFileSelections
  }
  // 本轮划选引用了助手回复片段时，随用户消息留痕供气泡 chip 展示（历史回放同构）
  if (quotedSnippetsSnapshot.length > 0) {
    userMessage.quotedSnippets = quotedSnippetsSnapshot.map((item, index) => ({
      id: `quote-${index}`,
      text: item.text,
    }))
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
    assistantMessage.isStreaming = true
  } else {
    activeAssistantMessageId.value = null
    assistantMessage = ensureAssistantMessage()
  }
  const assistantMessageId = assistantMessage.id
  if (options.appendUserMessage !== false) {
    input.value = ''
    clearSelectedSkills()
    clearMentionFiles()
    clearQuotedSnippets()
  }
  isGenerating.value = true
  startLiveElapsedTicker(options.elapsedSeedMs)
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
      if (options.messageSkills?.length) selectedSkills.value = options.messageSkills
      return
    }
    const resolvedConversationId = convId
    if (props.ensureConversationDatasource && !await props.ensureConversationDatasource()) {
      if (options.appendUserMessage !== false) {
        messages.value = messages.value.filter(item => item.id !== userMessage.id && item.id !== assistantMessage.id)
      }
      activeAssistantMessageId.value = null
      if (options.restoreInputText) input.value = options.restoreInputText
      if (options.messageSkills?.length) selectedSkills.value = options.messageSkills
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
        onError: (error, meta) => {
          if (options.interruptedQuestion) {
            const questionGone = /待回答问题不存在|问题已回答|运行已恢复/.test(error || '')
            if (questionGone) {
              // 审批已经被消费（或 Run 已失败/结束），重放旧 question_id 不会再成功。
              pendingUserQuestion.value = null
            } else {
              // 网络抖动等暂时性失败才恢复卡片，允许用户重试。
              pendingUserQuestion.value = options.interruptedQuestion
            }
            const active = activeAssistantMessageId.value
              ? messages.value.find(item => item.id === activeAssistantMessageId.value)
              : null
            if (active?.role === 'assistant') active.isStreaming = false
            if (questionGone) ElMessage.warning('该审批已处理或任务已结束')
            else ElMessage.error(error || '提交回答失败')
            return
          }
          // 只有明确的业务错误响应（HTTP 非 2xx）才说明服务端没有建 Run；
          // 网络中断/abort 时 Run 可能已在跑，保留气泡，避免刷新后孤儿消息 + 重发双份。
          if (!startedRunId && meta?.isBusinessResponseError && options.appendUserMessage !== false) {
            messages.value = messages.value.filter(item => item.id !== userMessage.id && item.id !== assistantMessage.id)
            activeAssistantMessageId.value = null
            if (options.restoreInputText) input.value = options.restoreInputText
            if (options.messageSkills?.length) selectedSkills.value = options.messageSkills
            ElMessage.error('消息未发送成功，已恢复到输入框，请重试')
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
        // 引用文件的白名单校验与 Human 消息短句由服务端完成，前端只传相对路径列表。
        modelId: selectedModelValue.value,
        memberDatasourceIds: selectedMemberDatasourceIds.value,
        agentId: 'chat_data',
        skillNames: options.skillNames?.length ? options.skillNames : null,
        onMessageEnd: (payload) => {
          if (payload.usage) tokenUsage.value = payload.usage
          attachGenerationStats(assistantMessageId, payload.generation_stats)
          updateAssistantFinishedAt(assistantMessageId, payload.timestamp)
        },
        onTokenUsage: (usage) => {
          tokenUsage.value = usage
        },
        onGenerationSpeed: (tokensPerSecond) => {
          liveGenerationSpeed.value = tokensPerSecond
        },
        fileSelections: mentionFileSelections,
        quotedSnippets: quotedSnippetsSnapshot,
        enableThinking: enableDeepThinking.value,
        thinkingEffort: thinkingEffort.value,
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
            display_name: event.display_name || existing?.display_name,
            args: { ...(existing?.args || {}), ...(event.args || {}) },
            result: event.content || event.result || existing?.result,
            progressMessage: event.message || existing?.progressMessage,
            progress: typeof event.progress === 'number' ? event.progress : existing?.progress,
            contentLines: typeof event.content_lines === 'number' ? event.content_lines : existing?.contentLines,
            removedLines: typeof event.removed_lines === 'number' ? event.removed_lines : existing?.removedLines,
            streamingIntent: typeof event.intent === 'string' && event.intent ? event.intent : existing?.streamingIntent,
            status: isFailed ? 'error' : isCompleted ? 'completed' : 'running',
            subagentTrail: existing?.subagentTrail,
            subagentTokenUsage: event.subagent_token_usage || existing?.subagentTokenUsage,
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
    stopLiveGenerationSpeed()
    // 流结束（含异常中断）后清掉未完成的"上下文压缩中"占位
    removeCompressionPlaceholders()

    // 结构化 done 已包含终态和下一轮 Run，不再查询队列。只有旧协议或
    // SSE 意外中断时没有足够信息，才用一次服务端快照恢复状态。
    if (startedRunId && (completedRunStatus === null || completedRunStatus === 'legacy') && !isStoppingRun.value) {
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
        // 队列消息的随行快照：入队时捕获；恢复自服务端且无 meta 的项显式传空，
        // 绝不兜底取当前输入框状态（执行时点的 chip/附件属于之后的新消息）
        fileSelections: queuedMessage.fileSelections ?? [],
        quotedSnippets: queuedMessage.quotedSnippets ?? [],
        fileIds: queuedMessage.fileIds ?? [],
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
    // 入队瞬间捕获随行快照：执行时间点与入队时间点之间输入框状态可能已变化，
    // 引用/附件必须随消息走（服务端存引用，续跑执行时统一校验解析）。
    const mentionFileSelectionsSnapshot = buildMentionFileSelections()
    // 划选引用走结构化通道：随队列消息透传（服务端存引用，采纳时统一解析）
    const queuedQuotedSnippets = quotedSnippets.value.map(item => ({ text: item.text }))
    const queuedFileIds = pendingFiles.value
      .filter(f => f.status === 'uploaded' && f.file_id && !f.file_id.startsWith('temp-'))
      .map(f => f.file_id!)
    // 技能沿用普通发送口径：入队时拼进正文（技能激活靠文本，无独立通道）
    const queuedSkillNames = selectedSkills.value.map(skill => skill.name)
    const bakedQuery = buildSkillRequestText(query, queuedSkillNames)
    pendingSteerMessages.value.push({
      clientId: temporaryId,
      content: bakedQuery,
      queueType: 'next-turn',
      status: 'submitting',
      timestamp: new Date(),
      fileSelections: mentionFileSelectionsSnapshot,
      quotedSnippets: queuedQuotedSnippets.length > 0 ? queuedQuotedSnippets : undefined,
      fileIds: queuedFileIds.length > 0 ? queuedFileIds : undefined,
    })
    input.value = ''
    isSteering.value = true
    try {
      const response = await steerConversation(conversationId, bakedQuery, 'next-turn', {
        file_selections: mentionFileSelectionsSnapshot,
        quoted_snippets: queuedQuotedSnippets.length > 0 ? queuedQuotedSnippets : undefined,
        file_ids: queuedFileIds,
      })
      const queuedMessage = pendingSteerMessages.value.find(item => item.clientId === temporaryId)
      if (queuedMessage && response.data?.message_id) {
        queuedMessage.messageId = response.data.message_id
        queuedMessage.status = 'pending'
        if (admittedInboxMessageIds.value.has(queuedMessage.messageId)) {
          admitPendingMessages([queuedMessage.messageId])
        }
      }
      // 随行内容已入队：只释放已随消息入队的那批引用/附件/技能（快照精确释放）；
      // API 往返期间新加的保留给下一条消息（失败分支全部保留供重试）
      mentionFileSelectionsSnapshot.forEach(path => removeMentionFile(path))
      if (queuedQuotedSnippets.length > 0) {
        const queuedTexts = new Set(queuedQuotedSnippets.map(snippet => snippet.text))
        quotedSnippets.value
          .filter(snippet => queuedTexts.has(snippet.text))
          .forEach(snippet => removeQuotedSnippet(snippet.id))
      }
      if (queuedFileIds.length > 0) {
        const queuedFileIdSet = new Set(queuedFileIds)
        pendingFiles.value = pendingFiles.value.filter(f => !queuedFileIdSet.has(f.file_id))
      }
      queuedSkillNames.forEach(name => removeSelectedSkill(name))
    } catch (error) {
      pendingSteerMessages.value = pendingSteerMessages.value.filter(item => item.clientId !== temporaryId)
      input.value = query
      await restorePendingSteerQueue({ restartPolling: false })
      if (!hasActiveRun.value) {
        if (isStoppingRun.value) return // 停止竞态下不得重新发起任务
        // 降级为直接发送：submit 内会清空 chip 与技能，这里同步释放已发送的附件
        if (queuedFileIds.length > 0) {
          const queuedFileIdSet = new Set(queuedFileIds)
          pendingFiles.value = pendingFiles.value.filter(f => !queuedFileIdSet.has(f.file_id))
        }
        void submitAnalysisQuery(bakedQuery, {
          fileSelections: mentionFileSelectionsSnapshot,
          quotedSnippets: queuedQuotedSnippets,
          fileIds: queuedFileIds.length > 0 ? queuedFileIds : undefined,
        })
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
  if (!input.value.trim() && !hasUploadedFiles && quotedSnippets.value.length === 0) return
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
  const skillNames = selectedSkills.value.map(skill => skill.name)
  // @ 引用文件快照（发送瞬间捕获，发送后 chip 会被清空）；留痕在 submit 内按快照决策
  const mentionFileSelectionsSnapshot = buildMentionFileSelections()
  // 划选引用快照：发送瞬间捕获，随请求透传服务端（模型可见标记块由后端统一追加），发送后 chip 清空
  const quotedSnippetsSnapshot = quotedSnippets.value.map(item => ({ text: item.text }))
  const messageSkills: Message['skills'] = selectedSkills.value.map(skill => ({
    name: skill.name,
    title: skill.title,
    description: skill.description,
  }))

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
  // 只有划选引用没有文本时，同样给出占位问题
  if (!effectiveQueryText.trim() && quotedSnippetsSnapshot.length > 0) {
    effectiveQueryText = '请结合引用的内容继续分析'
  }

  const requestText = buildSkillRequestText(effectiveQueryText, skillNames)
  // 附件已捕获进 completedFileIds/attachments，发送瞬间即清空输入框附件卡片，
  // 并避免后续消息重复附带历史上传文件
  pendingFiles.value = []
  await submitAnalysisQuery(requestText, {
    skillNames,
    messageSkills,
    restoreInputText: queryText,
    fileSelections: mentionFileSelectionsSnapshot,
    quotedSnippets: quotedSnippetsSnapshot.length > 0 ? quotedSnippetsSnapshot : [],
    fileIds: completedFileIds.length > 0 ? completedFileIds : undefined,
    preserveQueuedMessages: pendingSteerMessages.value.length > 0,
    attachments: attachments.length > 0 ? attachments : undefined,
  })
}

const handleStop = async () => {
  if (isStoppingRun.value) return
  const conversationId = localConversationId.value || props.conversationId
  const runId = activeRunId.value
  if (!conversationId) {
    // 会话创建请求还没返回时也可能显示「停止」；此时没有 Run 可取消，
    // 直接中断正在创建的本地请求，不能因为 conversationId 为空就无响应。
    isStoppingRun.value = true
    stopGeneration()
    isGenerating.value = false
    finishStreamingMessages()
    finalizeUnfinishedToolCalls('error', '已停止')
    isStoppingRun.value = false
    return
  }
  isStoppingRun.value = true
  // 乐观反馈：点击立即显示「停止中」。取消链路含多次远程存储操作，链路劣化时
  // HTTP 响应可能秒级，等待期间无反馈像「点了没反应」。done 先到的竞态由下方
  // activeRunId 守卫 + done 回调清空 activeRunId 双重防护，不会覆盖已收口状态。
  const previousRunStatus = activeRunStatus.value
  if (runId && hasActiveRun.value && activeRunId.value === runId) {
    activeRunStatus.value = 'cancelling'
    emit('runStatusChanged', conversationId, 'cancelling')
  }
  // 立刻断开本地 SSE：后端取消可能散落在另一个 worker，还要等心跳/工具边界，
  // 若继续等 done 事件，用户会看到 token 继续滚动，误以为停止无效。
  // 后端状态仍以 DELETE 响应 + 服务端快照为准，本地只是先冻结画面。
  stopGeneration()
  // 主动 abort 后 useSqlGenerator 的 finally 会再置一次；这里同步收掉，
  // 避免「run header 未到 + abort 异常路径」时停止按钮一直停留。
  isGenerating.value = false
  finishStreamingMessages()
  finalizeUnfinishedToolCalls('error', '已停止')
  // 首包之前 run header 还没到：没有 runId 可取消，直接收回本地请求即可。
  // 否则「点了停止但 activeRunId 为空 → 直接 return」会让按钮完全没反应。
  if (!runId) {
    applyRunDoneState(conversationId, 'cancelled', null, null)
    isStoppingRun.value = false
    return
  }
  try {
    const response = await cancelConversationRun(conversationId, runId)
    if (!response.data?.cancelled) {
      // 后端明确说没有运行中任务：以服务端快照恢复真实状态（同时回滚乐观置位）
      await restorePendingSteerQueue({ restartPolling: false, applyTerminal: true })
      isStoppingRun.value = false
      return
    }
    // 取消已受理：直接按 cancelled 收口本地 UI，不等异步 done（SSE 已断开）。
    if (activeRunId.value === runId) {
      applyRunDoneState(conversationId, 'cancelled', null, runId)
    }
    isStoppingRun.value = false
  } catch (error) {
    // 回滚乐观状态：仅当 done 尚未收口（activeRunId 未变）时恢复原状态，
    // 避免「实际已受理取消但响应丢失」时把 UI 改回运行中造成误导
    if (hasActiveRun.value && activeRunId.value === runId) {
      activeRunStatus.value = previousRunStatus
      emit('runStatusChanged', conversationId, previousRunStatus)
    }
    isStoppingRun.value = false
    ElMessage.error(error instanceof Error ? error.message : '停止运行失败')
    // 本地 SSE 已被主动断开；若取消请求失败而 Run 仍在执行，重新接上，
    // 避免页面停在「无流、无停止按钮」的半截状态。
    void recoverInterruptedPageRun()
  }
}

const handleKeyDown = (event: KeyboardEvent) => {
  if (handleMentionMenuKeyDown(event)) return
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
        <div v-else-if="!hasLocalConversationStarted && messages.length === 0" class="flex-1 flex items-center justify-center">
          <div class="w-[774px] max-w-[90%] flex flex-col items-center pb-4">
            <div class="flex flex-col items-center text-center">
              <div class="bg-gradient-to-r from-blue-600 to-indigo-500 bg-clip-text text-[32px] font-bold leading-tight text-transparent">嗨，我是 DataBuddy</div>
              <div class="mt-2 text-[14px] text-slate-500">你的专属数据伙伴，让洞察触手可及</div>
            </div>

            <div v-if="selectedDatasource" class="mt-8 w-full max-w-[560px]">
              <div v-if="canRefreshRecommendations" class="mb-2.5 flex items-center justify-end">
                <button
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
          :resolve-tool-call="getRenderableMessageToolCall"
          :should-inline-render-result="shouldInlineRenderToolCall"
          :run-active="isGenerating || hasActiveRun"
          :live-generation-speed="liveGenerationSpeed"
          :live-elapsed-ms="liveElapsedMs"
          @select-tool="selectToolCall"
          @open-attachment="handleOpenAttachment"
          @add-to-conversation="handleAddQuoteSnippet"
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
          <div v-if="!pendingUserQuestion && pendingFiles.length" class="mb-1.5 flex max-h-[100px] flex-wrap gap-1.5 overflow-y-auto">
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
            v-if="!pendingUserQuestion"
            ref="inputBarRef"
            v-model:input="input"
            v-model:selected-model="selectedModel"
            v-model:enable-deep-thinking="enableDeepThinking"
            v-model:thinking-effort="thinkingEffort"
            :placeholder="hasLocalConversationStarted || messages.length > 0 ? '继续输入任务，输入 / 可选择技能，@ 可引用工作区文件' : '请输入任务，输入 / 可选择技能，@ 可引用工作区文件'"
            :selected-skills="selectedSkills"
            :selected-mention-files="mentionedFiles"
            :quoted-snippets="quotedSnippets"
            :model-options="modelOptions"
            :is-generating="runBusy"
            :is-stopping="isStoppingRun"
            :can-submit="runBusy ? Boolean(input.trim() && !isSteering) : Boolean((input.trim() || hasCompletedPendingFiles || quotedSnippets.length > 0) && selectedDatasource)"
            :input-focused="inputFocused"
            :submit-title="!selectedDatasource ? '请先选择数据源' : '发送 (Enter)'"
            @submit="handleSubmit"
            @stop="handleStop"
            @clear-skill="clearSelectedSkill"
            @clear-mention-file="handleMentionFileClear"
            @remove-quote-snippet="removeQuotedSnippet"
            @upload-file="handleUploadFile"
            @paste-files="handlePasteFiles"
            @open-skill-manager="isSkillManagerOpen = true"
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
              <WorkspaceFileMentionMenu
                :open="showMentionMenu"
                :files="displayedMentionFiles"
                :highlighted-index="highlightedFileIndex"
                :empty-hint="resolveMentionConversationId() ? '工作区还没有可引用的文件，发送消息后即可引用' : '发送消息后即可引用工作区文件'"
                @select="handleMentionFileSelect"
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

    <ConversationWorkspacePanel
      ref="workspacePanelRef"
      v-model:open="sidePanelOpen"
      :active-tool-call="activeToolCall"
      :resolve-tool-call="findToolCallById"
      :conversation-id="localConversationId"
      :run-busy="runBusy"
      :can-import-semantic-model="props.canImportSemanticModel"
      :format-text="replaceVisiblePathsForDisplay"
      :ensure-conversation-id="ensureConversationId"
      @conversation-created="handlePanelConversationCreated"
      @import-semantic-yaml="emit('importSemanticYaml', $event)"
      @select-tool="selectToolCall"
    />

    <el-dialog v-model="isSkillManagerOpen" width="min(1280px, 92vw)" title="技能管理" append-to-body>
      <div class="h-[640px] overflow-hidden">
        <SkillManager :initial-skill-id="importedSkillIdForManager" @create-with-assistant="startSkillCreation" />
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
