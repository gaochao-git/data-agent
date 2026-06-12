<template>
  <section class="relative flex flex-col bg-gray-50 dark:bg-gray-900 h-full">
    <!-- 消息区域 - 引导语始终显示 -->
    <div ref="chatContainerRef" class="flex-1 min-h-0 overflow-y-auto bg-gray-50/30 py-3 dark:bg-gray-800/20">
      <div class="w-[774px] max-w-[90%] mx-auto space-y-4">
        <!-- AI引导消息 -->
        <div v-if="guide" class="flex flex-col gap-2 items-start">
          <div class="ai-guide-container p-4 bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
            <h3 class="text-lg font-medium text-gray-900 dark:text-gray-100 mb-2">{{ guide.name || '智能助手' }}</h3>
            <p class="text-sm text-gray-600 dark:text-gray-400 mb-3">{{ guide.welcomeMessage }}</p>
            <div v-if="guide.uploadRequirements && guide.uploadRequirements.length > 0" class="mb-3">
              <p class="text-xs font-medium text-gray-500 dark:text-gray-500 mb-1">支持上传：</p>
              <ul class="text-xs text-gray-500 dark:text-gray-400 list-disc list-inside">
                <li v-for="req in guide.uploadRequirements" :key="req">{{ req }}</li>
              </ul>
            </div>
          </div>
        </div>
        <div v-else class="flex flex-col gap-2 items-start">
          <p class="text-base text-gray-600 dark:text-gray-400">{{ getWelcomeMessage(agentId) }}</p>
        </div>

        <!-- 消息列表 -->
        <div v-if="chatMessages.length > 0" class="space-y-4">
          <div
            v-for="message in chatMessages"
            :key="message.id"
            class="flex flex-col gap-2"
            :class="message.role === 'user' ? 'items-end' : 'items-start'"
          >
            <!-- 用户消息 -->
            <template v-if="message.role === 'user'">
              <div
                v-if="message.additional_kwargs?.task?.uploaded_files?.length > 0"
                class="flex flex-wrap gap-2"
              >
                <div
                  v-for="(file, index) in message.additional_kwargs.task.uploaded_files"
                  :key="index"
                  class="inline-flex items-center gap-2 px-3 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm"
                >
                  <div class="flex-shrink-0 w-8 h-8 bg-blue-100 dark:bg-blue-900/30 rounded flex items-center justify-center">
                    <i class="fa-solid fa-file-word text-blue-600 dark:text-blue-400 text-sm"></i>
                  </div>
                  <div class="flex flex-col min-w-0 max-w-[160px]">
                    <div class="text-xs font-medium text-gray-900 dark:text-gray-100 truncate">{{ file.file_name }}</div>
                    <div class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">{{ file.file_type?.toUpperCase() || 'DOC' }}</div>
                  </div>
                </div>
              </div>

              <div class="group flex flex-col gap-1.5 max-w-[85%]">
                <div
                  class="rounded-2xl rounded-br-sm bg-[#edf3fe] text-gray-800 dark:bg-blue-900/30 dark:text-gray-200 px-4 py-3 text-sm leading-relaxed shadow-sm"
                >
                  {{ getMessageText(message) }}
                </div>
                <div class="flex items-center justify-end gap-2 px-2 mt-1.5">
                  <div class="text-[10px] text-gray-500 dark:text-gray-400">
                    {{ formatTimestamp(message.timestamp) }}
                  </div>
                </div>
              </div>
            </template>

            <!-- AI 消息 -->
            <template v-else>
              <AnalysisMessage
                :message="message"
                :thread-id="threadId"
                :is-chat-streaming="isChatStreaming"
                :active-assistant-message-id="activeAssistantMessageId"
                @law-click="(law) => emit('law-click', law)"
                @word-document-view="(doc) => emit('word-document-view', doc)"
                @excel-document-view="(doc) => emit('excel-document-view', doc)"
                @report-click="(report) => emit('report-click', report)"
                @task-click="handleTaskClick"
                @refresh-message="handleRefreshMessage"
                @copy-message="handleCopyMessage"
              />
            </template>
          </div>
        </div>

        <!-- 正在生成指示器 -->
        <div v-if="isChatStreaming" class="flex items-start">
          <div class="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 px-2">
            <i class="fa-solid fa-spinner fa-spin text-[10px]"></i>
            <span>正在生成...</span>
          </div>
        </div>

        <!-- 建议问题 -->
        <div v-if="suggestedPrompts.length > 0 && !isChatStreaming" class="pt-2">
          <div class="mt-3 flex flex-wrap gap-2">
            <button v-for="prompt in suggestedPrompts" :key="prompt" type="button"
              @click="handlePromptClick(prompt)" :title="prompt"
              class="rounded-full border border-indigo-200/80 bg-indigo-50/70 px-3 py-2 text-xs text-indigo-600 transition-colors hover:bg-indigo-100 dark:border-indigo-800/60 dark:bg-indigo-900/30 dark:text-indigo-300 dark:hover:bg-indigo-800/40"
            >
              {{ formatPromptPreview(prompt, MAX_PROMPT_DISPLAY_LENGTH) }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 输入框固定在底部 -->
    <form @submit.prevent="handleChatSubmit"
      class="flex-shrink-0 pt-2 pb-4 bg-gray-50 dark:bg-gray-900 w-[774px] max-w-[90%] mx-auto"
    >
      <div
        class="rounded-2xl border border-gray-200 bg-white shadow-sm transition-all hover:border-gray-300 focus-within:border-indigo-400 focus-within:shadow-md dark:border-gray-700 dark:bg-gray-800/50"
      >
        <!-- 输入区域 -->
        <div class="flex items-start gap-3 px-4 pt-4 pb-2">
          <!-- 工具名称 - 有引导语时不显示，只在没有引导语时显示选中的工具 -->
          <span v-if="!guide && selectedTool && (selectedTool === 'violation-analysis' || selectedReportSubType)" class="text-base font-bold text-gray-900 dark:text-gray-100 flex-shrink-0 leading-[26px]">
            {{ selectedTool === 'violation-analysis' ? '事实认定分析' : getReportTypeName(selectedReportSubType) }}
          </span>
          <!-- 内容区域 -->
          <div class="flex-1 min-h-[54px] flex items-start">
            <!-- 检查报告生成模式：展示已上传文件列表 -->
            <div v-if="selectedTool === 'report-generation' && selectedReportSubType" class="flex flex-col gap-2 w-full">
              <div v-if="getReportConfig(selectedReportSubType).inputType === 'fact_determination'" class="flex flex-wrap gap-2">
                <!-- 已上传的文件列表 -->
                <template v-if="fileGroups.length > 0">
                  <div
                    v-for="(group, index) in fileGroups"
                    :key="group.group_id"
                    class="group flex items-center gap-1.5 px-2 py-1 bg-indigo-50 dark:bg-indigo-900/30 rounded text-sm"
                  >
                    <span class="w-4 h-4 rounded-full bg-indigo-200 dark:bg-indigo-800 text-indigo-700 dark:text-indigo-300 flex items-center justify-center text-xs font-medium flex-shrink-0">
                      {{ index + 1 }}
                    </span>
                    <i class="fa-solid fa-file-word text-xs text-indigo-600 dark:text-indigo-400"></i>
                    <span class="text-gray-700 dark:text-gray-300 max-w-[150px] truncate" :title="group.fact_determination?.filename">
                      {{ group.fact_determination?.filename }}
                    </span>
                    <button
                      type="button"
                      @click="deleteFileGroup(group.group_id)"
                      class="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-all"
                      title="删除"
                    >
                      <i class="fa-solid fa-times text-xs"></i>
                    </button>
                  </div>
                </template>
                <!-- 无文件时的提示 -->
                <span v-else class="text-base text-gray-400 dark:text-gray-500 leading-[26px]">
                  {{ uploadingGroupIds['new'] ? '上传中...' : '请点击右下角按钮上传事实认定书' }}
                </span>
              </div>
              <!-- 其他报告生成模式：显示已上传文件或提示文字 -->
              <div v-else class="flex items-center gap-2 h-[26px] leading-[26px]">
                <template v-if="inspectionReportFile">
                  <div class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <i class="fa-solid fa-file-word text-indigo-600 dark:text-indigo-400"></i>
                    <span class="max-w-[400px] truncate">{{ inspectionReportFile.file_name }}</span>
                    <button
                      type="button"
                      @click="inspectionReportFile = null"
                      class="text-gray-400 hover:text-red-500 dark:hover:text-red-400"
                      title="删除"
                    >
                      <i class="fa-solid fa-times text-xs"></i>
                    </button>
                  </div>
                </template>
                <span v-else class="text-base text-gray-400 dark:text-gray-500">
                  {{ isUploadingInspectionReport ? '上传中...' : '请上传检查报告Word文档' }}
                </span>
              </div>
            </div>
            <!-- 其他模式：显示 textarea -->
            <textarea
              v-else
              v-model="localChatInput"
              @keydown.enter.prevent="handleChatSubmit"
              :placeholder="getInputPlaceholder()"
              class="w-full resize-none border-0 bg-transparent text-base text-gray-700 placeholder-gray-400 outline-none dark:text-gray-100 dark:placeholder-gray-500 leading-[26px]"
              style="overflow-y: hidden; min-height: 54px;"
              rows="1"
              :disabled="!!selectedReportSubType"
            ></textarea>
          </div>
        </div>

        <!-- 操作区域 -->
        <div class="flex items-center justify-between gap-2 px-4 py-2">
          <!-- 左侧按钮组 -->
          <div class="flex items-center gap-2 text-xs">
            <!-- DeepSeek-R1 按钮 -->
            <button type="button" @click="deepseekR1Enabled = !deepseekR1Enabled"
              :class="[ 'px-2 py-1 rounded-md transition-colors', deepseekR1Enabled ? 'bg-cyan-100 dark:bg-cyan-900/50' : 'hover:bg-gray-100 dark:hover:bg-gray-700' ]"
              title="使用 DeepSeek-R1 模型"
            >
              <span :style="deepseekR1Enabled ? { background: 'linear-gradient(112deg, #007EA7 1%, #1F3A56 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' } : { color: '#6B7280' }"
              >DeepSeek-R1</span>
            </button>

            <!-- 深度思考按钮 -->
            <button type="button" @click="deepThinkEnabled = !deepThinkEnabled"
              :class="[ 'flex items-center gap-1 px-2 py-1 rounded-md transition-colors', deepThinkEnabled ? 'bg-cyan-100 dark:bg-cyan-900/50' : 'hover:bg-gray-100 dark:hover:bg-gray-700' ]"
              title="开启深度思考模式"
            >
              <span :style="deepThinkEnabled ? { background: 'linear-gradient(112deg, #007EA7 1%, #1F3A56 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text' } : { color: '#6B7280' }"
              >深度思考</span>
            </button>
          </div>

          <!-- 右侧按钮组：附件和发送 -->
          <div class="flex items-center gap-2">
            <!-- 上传按钮 - 检查报告生成模式（事实认定书）时直接上传文件 -->
            <template v-if="selectedTool === 'report-generation' && selectedReportSubType">
              <!-- 检查报告生成模式（事实认定书） -->
              <label v-if="getReportConfig(selectedReportSubType).inputType === 'fact_determination'" class="flex h-8 w-8 items-center justify-center rounded-full transition-all hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer">
                <i class="fa-solid fa-paperclip text-gray-500 text-sm"></i>
                <input
                  type="file"
                  accept=".docx,.wps"
                  multiple
                  class="hidden"
                  :disabled="uploadingGroupIds['new'] || fileGroups.length >= 10"
                  @change="handleFactDeterminationFileChange"
                />
              </label>
              <!-- 其他报告生成模式（检查报告）时显示 -->
              <label v-else class="flex h-8 w-8 items-center justify-center rounded-full transition-all hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer">
                <i class="fa-solid fa-paperclip text-gray-500 text-sm"></i>
                <input
                  type="file"
                  accept=".docx,.doc,.wps"
                  class="hidden"
                  :disabled="isUploadingInspectionReport"
                  @change="handleInspectionReportFileChange"
                />
              </label>
            </template>
            <!-- 默认上传按钮 - 普通对话模式 -->
            <label v-else class="flex h-8 w-8 items-center justify-center rounded-full transition-all hover:bg-gray-100 dark:hover:bg-gray-700 cursor-pointer">
              <i class="fa-solid fa-paperclip text-gray-500 text-sm"></i>
              <input type="file" accept=".pdf,.doc,.docx,.txt,.jpg,.jpeg,.png,.xls,.xlsx,.md" class="hidden" multiple @change="handleGeneralFileChange" />
            </label>

            <!-- 发送按钮 -->
            <button type="submit" :disabled="!canSend()" :title="getSendButtonTitle()"
              :class="[ 'flex h-8 w-8 items-center justify-center rounded-full transition-all', canSend() ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-gray-300 text-gray-500 cursor-not-allowed' ]"
            >
              <i class="fa-solid fa-arrow-up text-xs"></i>
            </button>
          </div>
        </div>
      </div>

      <!-- AI 免责声明 -->
      <div class="pt-1 pb-0.5 text-center text-xs text-gray-400 dark:text-gray-500">内容由AI生成，请仔细甄别</div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { formatTimestamp, formatPromptPreview } from '../utils'
import type { ChatMessage, AnalysisTask, ViolationFileGroup } from '../types'
import AnalysisMessage from './AnalysisMessage.vue'
import { SUB_FUNCTION_CONFIGS, REPORT_TYPE_MAP } from '../constants'
import { getToken } from '@/utils/auth'

// 使用 /agents 前缀，代理到 AI Agent 后端 (5001端口)
const API_BASE = import.meta.env.VITE_APP_BASE_API || ''
const MAX_PROMPT_DISPLAY_LENGTH = 25

// 获取带认证的 fetch headers
function getAuthHeaders(extraHeaders?: HeadersInit, skipContentType = false): HeadersInit {
  const headers: HeadersInit = {
    ...(extraHeaders || {}),
  }
  if (!skipContentType) {
    headers['Content-Type'] = 'application/json'
  }
  const token = getToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

// Props 定义
interface Props {
  // 核心状态
  analysisStage: string
  analysisHistory: any[]
  analysisTasks: AnalysisTask[]
  chatMessages: ChatMessage[]
  chatInput: string
  setChatInput: (value: string) => void
  isChatStreaming: boolean
  chatStreamingStatus: string
  activeAssistantMessageId: string | null

  // 表单相关
  hasFiveRequiredInputs: boolean
  missingRequiredLabels: string[]
  inspectionSummary: { inspectionContent: string; violationDescription: string }
  analysisResult: any
  register: any
  handleSubmit: any
  errors: any
  onSubmit: (data: any) => Promise<void>
  isAnalyzing: boolean
  chatContainerRef: any

  // 回调函数
  sendChatMessage: (message: string) => Promise<void>
  sendReportRequest: (reportSubType: string, uploadedFiles?: any[], onAutoPreview?: any) => Promise<void>
  stopChatStreaming: () => void
  onStartNewInspection: () => void

  // 标题和事件
  conversationTitle?: string
  onLawClick?: (law: any) => void
  onWordDocumentView?: (doc: any) => void
  onExcelDocumentView?: (doc: any) => void
  onReportClick?: (report: any) => void
  onTaskClick?: (task: any) => void

  // 其他
  panelHeightClass: string
  threadId: string
  onAddChatMessage?: (message: ChatMessage) => void
  onEnsureThread?: () => Promise<string | null>
  resetTrigger?: number
  updatedWordFilename?: string

  // agentId 和 guide
  agentId?: string
  guide?: {
    welcomeMessage: string
    description?: string
    uploadRequirements?: string[]
    name?: string
    icon?: string
    color?: string
  }
}

const props = withDefaults(defineProps<Props>(), {
  agentId: 'report_jcbg',
  guide: undefined
})

// Emits 定义
const emit = defineEmits<{
  (e: 'law-click', law: any): void
  (e: 'word-document-view', doc: any): void
  (e: 'excel-document-view', doc: any): void
  (e: 'report-click', report: any): void
  (e: 'file-modal-open'): void
  (e: 'set-chat-input', prompt: string): void
  (e: 'task-click', task: any): void
}>()

// 本地状态
const deepseekR1Enabled = ref(false)
const deepThinkEnabled = ref(false)

// 文件分组状态（用于检查报告生成）
const fileGroups = ref<ViolationFileGroup[]>([])

// 文件上传进度状态（key: group_id 或 'new' 表示新建分组）
const uploadingGroupIds = ref<Record<string, boolean>>({})

// 检查报告文件状态（用于其他报告生成）
const inspectionReportFile = ref<{ file_id: string; file_name: string; file_path: string } | null>(null)

// 检查报告上传进度
const isUploadingInspectionReport = ref(false)

// 工具状态
const selectedTool = ref<string | null>(null)
const selectedReportSubType = ref<string | null>(null)

// 计算属性：用于 v-model 绑定 chatInput
const localChatInput = computed({
  get: () => props.chatInput,
  set: (value: string) => props.setChatInput(value)
})

// 建议问题（可以从后端获取或根据 agentId 配置）
const suggestedPrompts = computed(() => {
  // 根据不同 agentId 返回不同的建议问题
  const promptsMap: Record<string, string[]> = {
    'fact_analysis': ['分析这段事实认定的违反条款', '这个行为应该如何处罚', '帮我检查事实认定是否完整'],
    'report_jcbg': ['请生成检查报告', '基于事实认定书生成报告'],
    'report_zgtzs': ['请生成整改通知书', '基于检查报告生成整改通知'],
    'report_zjml': ['请生成证据目录', '基于检查报告整理证据'],
    'report_xxjb': ['请生成信息简表', '基于检查报告生成简表']
  }
  return promptsMap[props.agentId] || ['请帮我分析', '生成报告']
})

// 获取欢迎语
const getWelcomeMessage = (agentId?: string): string => {
  if (!agentId) return '您好！我可以帮您进行执法检查相关的分析和报告撰写。'
  const config = SUB_FUNCTION_CONFIGS[agentId]
  return config?.welcomeMessage || '您好！我可以帮您进行执法检查相关的分析和报告撰写。'
}

// 获取报告类型的显示名称
const getReportTypeName = (type: string): string => {
  const names: Record<string, string> = {
    "comprehensive-report": "检查报告",
    "rectification-notice": "责令整改通知书",
    "evidence-catalog": "证据目录",
    "info-summary": "信息简表"
  }
  return type ? names[type] || "" : ""
}

// 获取报告类型的配置（输入类型和提示文本）
const getReportConfig = (type: string): { inputType: 'fact_determination' | 'inspection_report'; uploadHint: string; fileTypeHint: string } => {
  if (type === "comprehensive-report") {
    return {
      inputType: 'fact_determination',
      uploadHint: '请上传所需文件',
      fileTypeHint: '事实认定书'
    }
  } else {
    // 其他报告类型需要上传检查报告
    return {
      inputType: 'inspection_report',
      uploadHint: '请上传检查报告Word文档',
      fileTypeHint: '检查报告'
    }
  }
}

// 获取文件分组列表
const fetchFileGroups = async (customThreadId?: string) => {
  const activeThreadId = customThreadId || props.threadId
  if (!activeThreadId) return

  try {
    const response = await fetch(`${API_BASE}/api/conversations/${activeThreadId}/state`, {
      credentials: 'include',
      headers: getAuthHeaders(),
    })

    if (response.ok) {
      const data = await response.json()
      const groups = data.data?.violation_file_groups || []

      // 去重：按 group_id 去重，保留最后一个（最新的）
      const uniqueGroups = groups.reduce((acc: any[], group: any) => {
        const existingIndex = acc.findIndex(g => g.group_id === group.group_id)
        if (existingIndex >= 0) {
          acc[existingIndex] = group
        } else {
          acc.push(group)
        }
        return acc
      }, [])

      fileGroups.value = uniqueGroups
    }
  } catch (error) {
    console.error('[文件分组查询] 失败:', error)
  }
}

// 上传检查报告（用于其他报告生成）
const uploadInspectionReport = async (file: File, uploadThreadId?: string | null) => {
  const fileExt = file.name.toLowerCase().split('.').pop()
  if (fileExt !== 'docx' && fileExt !== 'wps' && fileExt !== 'doc') {
    ElMessage.error('只能上传 Word 文档（.docx、.doc 或 .wps 格式）')
    return
  }

  isUploadingInspectionReport.value = true

  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('file_type_hint', '检查报告')

    let activeThreadId = uploadThreadId || props.threadId
    if (!activeThreadId && props.onEnsureThread) {
      activeThreadId = await props.onEnsureThread()
    }

    if (!activeThreadId) {
      throw new Error('没有可用的会话 ID')
    }

    console.log('[检查报告上传] 开始上传，threadId:', activeThreadId)

    const response = await fetch(`${API_BASE}/api/conversations/${activeThreadId}/files`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: getAuthHeaders(undefined, true),  // formData 上传不需要 Content-Type，但需要认证
    })

    const result = await response.json()

    console.log('[检查报告上传] 后端响应:', result)

    if (result.status === 'error') {
      ElMessage.error(result.msg || '文件上传失败')
      return
    }

    inspectionReportFile.value = {
      file_id: result.data.file_id,
      file_name: result.data.file_name,
      file_path: result.data.file_path,
    }

    ElMessage.success('检查报告上传成功')
  } catch (error) {
    console.error('[检查报告上传] 失败:', error)
    ElMessage.error('上传失败，请稍后重试')
  } finally {
    isUploadingInspectionReport.value = false
  }
}

// 上传事实认定书（创建新分组）
const uploadFactDetermination = async (file: File, uploadThreadId?: string | null, skipLimitCheck: boolean = false): Promise<string | undefined> => {
  // 检查分组数量限制（批量上传时跳过，因为已经在Modal中已经检查过了）
  if (!skipLimitCheck && fileGroups.value.length >= 10) {
    ElMessage.error('已达到最大上传数量限制（最多10个违法行为）')
    return undefined
  }

  // 校验文件类型
  const fileExt = file.name.toLowerCase().split('.').pop()
  if (fileExt !== 'docx' && fileExt !== 'wps') {
    ElMessage.error('只能上传 Word 文档（.docx 或 .wps 格式）')
    return undefined
  }

  uploadingGroupIds.value = { ...uploadingGroupIds.value, 'new': true }

  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('file_type_hint', '事实认定书')

    let activeThreadId = uploadThreadId || props.threadId

    if (!activeThreadId && props.onEnsureThread) {
      activeThreadId = await props.onEnsureThread()
    }

    if (!activeThreadId) {
      throw new Error('没有可用的会话 ID')
    }

    const response = await fetch(`${API_BASE}/api/conversations/${activeThreadId}/files`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: getAuthHeaders(undefined, true),  // formData 上传不需要 Content-Type，但需要认证
    })

    const result = await response.json()

    console.log('[事实认定书上传] 后端响应:', result)

    if (result.status === 'error') {
      ElMessage.error(result.msg || '文件上传失败')
      return undefined
    }

    ElMessage.success('事实认定书上传成功')
    // 刷新文件分组列表（传入实际使用的 threadId）
    console.log('[事实认定书上传] 刷新文件列表，threadId:', activeThreadId)
    await fetchFileGroups(activeThreadId)

    // 返回使用的 threadId，供批量上传使用
    return activeThreadId
  } catch (error) {
    console.error('[事实认定书上传] 失败:', error)
    ElMessage.error('上传失败，请稍后重试')
    return undefined
  } finally {
    uploadingGroupIds.value = { ...uploadingGroupIds.value, 'new': false }
  }
}

// 删除整个分组
const deleteFileGroup = async (groupId: string) => {
  if (!confirm('确定要删除此违法行为及其所有文件吗？')) {
    return
  }

  try {
    const response = await fetch(
      `${API_BASE}/api/conversations/${props.threadId}/file-groups/${groupId}?delete_evidence_only=false`,
      {
        method: 'DELETE',
        credentials: 'include',
      }
    )

    const result = await response.json()

    if (result.status === 'error') {
      ElMessage.error(result.msg || '删除失败')
      return
    }

    ElMessage.success('已删除违法行为')
    // 刷新文件分组列表
    await fetchFileGroups()
  } catch (error) {
    console.error('[删除分组] 失败:', error)
    ElMessage.error('删除失败，请稍后重试')
  }
}

// 重置输入区域到初始状态的统一方法
const resetInputArea = () => {
  selectedTool.value = null
  selectedReportSubType.value = null
  uploadingGroupIds.value = {}
  fileGroups.value = []  // 清空已上传的文件
  inspectionReportFile.value = null  // 清空检查报告
  isUploadingInspectionReport.value = false
}

// 监听 resetTrigger 变化，触发重置
watch(() => props.resetTrigger, (newTrigger) => {
  if (newTrigger !== undefined && newTrigger > 0) {
    resetInputArea()
  }
})

// 当线程ID变化或选择检查报告工具时，加载文件分组
watch(() => props.threadId, async (newThreadId) => {
  if (newThreadId && selectedTool.value === "report-generation" && selectedReportSubType.value === "comprehensive-report" && props.chatMessages.length === 0) {
    await fetchFileGroups(newThreadId)
  }
})

// 监听 agentId 和 chatMessages 变化，更新工具状态
watch([() => props.agentId, () => props.chatMessages.length], ([newAgentId, messageLength]) => {
  const config = newAgentId ? AGENT_CONFIG_MAP[newAgentId] : null
  if (config && messageLength === 0) {
    selectedTool.value = config.tool
    selectedReportSubType.value = config.reportSubType
  } else if (messageLength > 0) {
    // 历史会话有消息，清空工具状态，显示普通输入框
    selectedTool.value = null
    selectedReportSubType.value = null
  }
})

// agentId 到工具状态的直接映射
const AGENT_CONFIG_MAP: Record<string, { tool: string; reportSubType: string | null }> = {
  "fact_analysis": { tool: "violation-analysis", reportSubType: null },
  "report_jcbg": { tool: "report-generation", reportSubType: "comprehensive-report" },
  "report_zgtzs": { tool: "report-generation", reportSubType: "rectification-notice" },
  "report_zjml": { tool: "report-generation", reportSubType: "evidence-catalog" },
  "report_xxjb": { tool: "report-generation", reportSubType: "info-summary" },
}

// 初始化时设置工具状态
onMounted(() => {
  const config = props.agentId ? AGENT_CONFIG_MAP[props.agentId] : null
  if (config && props.chatMessages.length === 0) {
    selectedTool.value = config.tool
    selectedReportSubType.value = config.reportSubType
  }
})

// 获取消息文本内容
const getMessageText = (message: ChatMessage): string => {
  if (typeof message.content === 'string') {
    return message.content
  }
  // 如果是内容块数组，提取文本
  if (Array.isArray(message.content)) {
    return message.content
      .filter((block: any) => block.type === 'text')
      .map((block: any) => block.text || '')
      .join('\n')
  }
  return ''
}

// 处理聊天提交
const handleChatSubmit = async () => {
  if (props.isChatStreaming) return

  // 报告生成模式
  if (selectedTool.value === 'report-generation' && selectedReportSubType.value) {
    const reportConfig = getReportConfig(selectedReportSubType.value)

    if (reportConfig.inputType === 'fact_determination') {
      // 检查报告生成：需要先上传事实认定书
      if (fileGroups.value.length === 0) {
        ElMessage.error('请先上传事实认定书')
        return
      }

      // 提取文件信息
      const uploadedFilesInfo = fileGroups.value.map(group => ({
        file_id: group.fact_determination.file_id || '',
        file_name: group.fact_determination.filename || '',
        file_type: group.fact_determination.file_type || 'docx'
      })).filter(f => f.file_id)

      // 调用报告生成 API，传入文件信息和自动预览回调
      await props.sendReportRequest(
        selectedReportSubType.value,
        uploadedFilesInfo,
        (doc) => {
          // 自动预览
          if (props.onWordDocumentView) {
            props.onWordDocumentView({
              filename: doc.filename,
              title: doc.title
            })
          }
        }
      )
    } else {
      // 其他报告生成：需要先上传检查报告
      if (!inspectionReportFile.value) {
        ElMessage.error('请先上传检查报告Word文档')
        return
      }

      // 调用报告生成 API，传入检查报告文件信息
      await props.sendReportRequest(
        selectedReportSubType.value,
        [inspectionReportFile.value],
        (doc) => {
          // 自动预览（根据文件类型判断）
          if (doc.excelData && props.onExcelDocumentView) {
            props.onExcelDocumentView({
              filename: doc.filename,
              title: doc.title,
              excelData: doc.excelData
            })
          } else if (props.onWordDocumentView) {
            props.onWordDocumentView({
              filename: doc.filename,
              title: doc.title,
              sourceData: doc.sourceData
            })
          }
        }
      )
    }

    // 清空选择和已上传文件（发送后文件信息已显示在用户消息上）
    selectedReportSubType.value = null
    selectedTool.value = null
    fileGroups.value = []
    inspectionReportFile.value = null
    return
  }

  // 违法行为分析模式：需要输入内容
  if (selectedTool.value === 'violation-analysis') {
    const trimmed = localChatInput.value.trim()
    if (!trimmed) {
      ElMessage.error('请输入事实认定内容')
      return
    }

    // 提取文件信息
    const uploadedFilesInfo = fileGroups.value.map(group => ({
      file_id: group.fact_determination?.file_id || '',
      file_name: group.fact_determination?.filename || '',
      file_type: group.fact_determination?.file_type || 'docx'
    })).filter(f => f.file_id)

    // 触发分析（onSubmit 内部会添加用户消息）
    const formData = {
      violationDescription: trimmed,
      uploadedFiles: uploadedFilesInfo.length > 0 ? uploadedFilesInfo : undefined
    }
    props.setChatInput('')
    selectedTool.value = null
    await props.onSubmit(formData)
    return
  }

  // 其他模式：普通聊天消息
  const trimmed = localChatInput.value.trim()
  if (!trimmed) return
  props.sendChatMessage(trimmed)
}

// 处理问题点击
const handlePromptClick = (prompt: string) => {
  props.setChatInput(prompt)
  // 可选：自动发送
  // nextTick(() => handleChatSubmit())
}

// 处理消息刷新
const handleRefreshMessage = (messageId: string) => {
  // 实现消息刷新逻辑
  console.log('刷新消息:', messageId)
}

// 处理消息复制
const handleCopyMessage = (content: string) => {
  navigator.clipboard.writeText(content).then(() => {
    // 可以显示提示
    console.log('已复制到剪贴板')
  })
}

// 处理任务点击 - 当用户点击待确认任务时，触发打开审核编辑器
const handleTaskClick = (task: any) => {
  if (task.status === 'awaiting_confirm' && task.reviewData) {
    if (props.onTaskClick) {
      props.onTaskClick(task)
    }
  }
}

// 获取输入框 placeholder
const getInputPlaceholder = () => {
  if (selectedTool.value === 'violation-analysis') {
    return '请输入事实认定内容'
  }
  if (selectedTool.value === 'report-generation' && selectedReportSubType.value) {
    if (getReportConfig(selectedReportSubType.value).inputType === 'fact_determination') {
      return '请上传事实认定书文件'
    }
    return '请上传检查报告Word文档'
  }
  return '请输入消息，按 Enter 发送...'
}

// 判断是否可以发送
const canSend = () => {
  if (props.isChatStreaming) return false

  // 报告生成模式
  if (selectedTool.value === 'report-generation' && selectedReportSubType.value) {
    const reportConfig = getReportConfig(selectedReportSubType.value)
    if (reportConfig.inputType === 'fact_determination') {
      return fileGroups.value.length > 0
    } else {
      return inspectionReportFile.value !== null
    }
  }

  // 违法行为分析模式
  if (selectedTool.value === 'violation-analysis') {
    return props.chatInput.trim().length > 0
  }

  // 普通对话模式
  return props.chatInput.trim().length > 0
}

// 获取发送按钮提示
const getSendButtonTitle = () => {
  if (selectedTool.value === 'violation-analysis') {
    return props.chatInput.trim().length === 0 ? '请输入事实认定内容' : '开始分析'
  }
  if (selectedTool.value === 'report-generation' && selectedReportSubType.value) {
    const reportConfig = getReportConfig(selectedReportSubType.value)
    if (reportConfig.inputType === 'fact_determination') {
      return fileGroups.value.length === 0 ? '请先上传事实认定书' : '生成检查报告'
    } else {
      return !inspectionReportFile.value ? '请先上传检查报告Word文档' : `生成${getReportTypeName(selectedReportSubType.value)}`
    }
  }
  return '发送消息 (Enter)'
}

// 处理事实认定书文件上传
const handleFactDeterminationFileChange = async (e: Event) => {
  const target = e.target as HTMLInputElement
  const files = target.files
  if (!files || files.length === 0) return

  const filesArray = Array.from(files)
  const remainingSlots = 10 - fileGroups.value.length

  if (filesArray.length > remainingSlots) {
    ElMessage.error(`最多只能上传 ${remainingSlots} 个文件（当前已有 ${fileGroups.value.length} 个）`)
    target.value = ''
    return
  }

  let sharedThreadId = props.threadId
  for (const file of filesArray) {
    try {
      const usedThreadId = await uploadFactDetermination(file, sharedThreadId, true)
      if (!sharedThreadId && usedThreadId) {
        sharedThreadId = usedThreadId
      }
    } catch (error) {
      console.error('上传文件失败:', error)
    }
  }
  target.value = ''
}

// 处理检查报告文件上传
const handleInspectionReportFileChange = async (e: Event) => {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  let currentThreadId = props.threadId
  if (!currentThreadId && props.onEnsureThread) {
    currentThreadId = await props.onEnsureThread()
    if (!currentThreadId) {
      ElMessage.error('创建会话失败')
      return
    }
  }
  await uploadInspectionReport(file, currentThreadId)
  target.value = ''
}

// 处理普通文件上传
const handleGeneralFileChange = async (e: Event) => {
  const target = e.target as HTMLInputElement
  const files = target.files
  if (!files || files.length === 0) return

  const filesArray = Array.from(files)

  let currentThreadId = props.threadId
  if (!currentThreadId && props.onEnsureThread) {
    currentThreadId = await props.onEnsureThread()
    if (!currentThreadId) {
      ElMessage.error('创建会话失败')
      target.value = ''
      return
    }
  }

  try {
    const formData = new FormData()
    filesArray.forEach(file => {
      formData.append('file', file)
    })

    const response = await fetch(`${API_BASE}/api/conversations/${currentThreadId}/files`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
      headers: getAuthHeaders(undefined, true),  // formData 上传不需要 Content-Type，但需要认证
    })

    const result = await response.json()

    if (result.status === 'error') {
      ElMessage.error(result.msg || '文件上传失败')
      return
    }

    ElMessage.success('文件上传成功')

    // 如果有文件上传成功，发送一条消息通知后端处理
    await props.sendChatMessage('我上传了文件，请帮我分析')

  } catch (error) {
    console.error('[普通文件上传] 失败:', error)
    ElMessage.error('上传失败，请稍后重试')
  } finally {
    target.value = ''
  }
}

// 监听 agentId 变化
watch(() => props.agentId, (newAgentId) => {
  console.log('Agent ID 变化:', newAgentId)
  // 可以在这里触发欢迎消息或其他初始化逻辑
}, { immediate: true })

// 监听 chatMessages 变化，自动滚动到底部
watch(() => props.chatMessages.length, async () => {
  await nextTick()
  if (props.chatContainerRef?.value) {
    const container = props.chatContainerRef.value
    container.scrollTop = container.scrollHeight
  }
})
</script>