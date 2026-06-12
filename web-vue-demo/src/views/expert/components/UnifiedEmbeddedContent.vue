<!--
  核心聊天逻辑组件
  迁移自: web/src/pages/GeneralAssistant/components/UnifiedEmbeddedContent.tsx

  功能：
  - 管理聊天消息列表
  - 处理消息发送和流式接收
  - 文件上传管理
  - 法规引用处理
  - 工作台面板控制
-->

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useExpertAssistant } from '../composables'
import ThinkingBlock from './ThinkingBlock.vue'
import MarkdownContent from './MarkdownContent.vue'
import Composer from './Composer.vue'
import ExpertWorkbench from './ExpertWorkbench.vue'
import ToolCallProgress from './ToolCallProgress.vue'
import type { AgentOpeningMessage, ParsedCitation } from '../types'

// 导入图片资源
import uploadIcon from '@/assets/images/upload.png'

// 环境变量
const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5001'
const MAX_FILE_SIZE_MB = 10
const TRACE_PANEL_ENABLED = false // 调试面板默认关闭

// Props
interface Props {
  agentId?: string
  initialThreadId?: string
  inputActions?: Array<{
    id: string
    label: string
    icon?: 'deep-think'
    kind?: string
    defaultSelected?: boolean
  }>
  inputPlaceholder?: string
  footerTag?: string
  assistantRoleLabel?: string
  openingMessage?: AgentOpeningMessage
  workbench?: {
    enabled: boolean
    variant: 'complaint' | 'expert'
  }
  onConversationIdChange?: (conversationId: string) => void
  onTitleChange?: (title: string) => void
}

const props = withDefaults(defineProps<Props>(), {
  agentId: 'expert_agent',
  inputPlaceholder: '请输入内容...',
  footerTag: '法律检索',
  assistantRoleLabel: 'AI',
})

// Emits
const emit = defineEmits<{
  (e: 'streamDone'): void
}>()

// Refs
const layoutContainerRef = ref<HTMLDivElement | null>(null)
const scrollContainerRef = ref<HTMLElement | null>(null)

// 使用 useExpertAssistant composable
const {
  chatMessages,
  chatInput,
  setChatInput,
  isChatStreaming,
  activeAssistantMessageId,
  uploadedFiles,
  chatThreadId,
  sendChatMessage,
  stopChatStreaming,
  handleFileUpload,
  removeUploadedFile,
  clearUploadedFiles,
  handleCitationClick,
} = useExpertAssistant({ agentId: props.agentId })

// 本地状态
const selectedFiles = ref<File[]>([])
const isWorkbenchVisible = ref(false)
const activeWorkbenchPanel = ref<string | null>(null)
const activeExpertCitation = ref<ParsedCitation | null>(null)
const isResizing = ref(false)
const workbenchWidth = ref<number | null>(null)
const selectedActionIds = ref<Set<string>>(new Set())

// Refs for resizing
const resizeStartXRef = ref(0)
const resizeStartWidthRef = ref(0)
const hasManualResizeRef = ref(false)

// Computed
const showUpload = computed(() => true)
const actionButtons = computed(() => props.inputActions || [])
  const placeholderText = computed(() => props.inputPlaceholder || '请输入内容...')
const footerTagText = computed(() => props.footerTag || '法律检索')
const assistantRole = computed(() => props.assistantRoleLabel || 'AI')
const showOpening = computed(() => chatMessages.value.length === 0 && Boolean(props.openingMessage))
const showWorkbench = computed(() => Boolean(props.workbench?.enabled) && isWorkbenchVisible.value)
const workbenchVariant = computed(() => props.workbench?.variant ?? 'expert')
const tracePanelEnabled = computed(() => TRACE_PANEL_ENABLED && workbenchVariant.value === 'expert')

// 计算布局类名
const layoutClass = computed(() => {
  return showWorkbench.value
    ? 'relative mx-auto flex h-full w-full max-w-[1905px] flex-row items-stretch gap-0 px-6'
    : 'relative mx-auto flex h-full w-full max-w-[1063px] flex-col gap-6 px-6'
})

// 初始化默认选中的操作
onMounted(() => {
  const nextSelected = new Set<string>()
  actionButtons.value.forEach((action) => {
    if (action.defaultSelected) {
      nextSelected.add(action.id)
    }
  })
  // 如果没有选中模型，默认选中第一个模型
  const hasModelSelected = actionButtons.value.some(
    action => action.kind === 'model' && nextSelected.has(action.id)
  )
  if (!hasModelSelected) {
    const fallbackModel = actionButtons.value.find(action => action.kind === 'model')
    if (fallbackModel) {
      nextSelected.add(fallbackModel.id)
    }
  }
  selectedActionIds.value = nextSelected
})

// 监听初始线程 ID
watch(() => props.initialThreadId, (newThreadId) => {
  if (newThreadId && newThreadId !== chatThreadId.value) {
    // 切换会话时，更新状态
  }
})

// 工作台宽度计算
watch(showWorkbench, (newShowWorkbench) => {
  if (!newShowWorkbench) return
  const container = layoutContainerRef.value
  if (!container) return
  const MIN_LEFT_WIDTH = 550
  const MIN_RIGHT_WIDTH = 320
  const RESIZER_WIDTH = 22

  const getContentWidth = () => {
    const style = window.getComputedStyle(container)
    const paddingLeft = Number.parseFloat(style.paddingLeft || '0')
    const paddingRight = Number.parseFloat(style.paddingRight || '0')
    return Math.max(container.clientWidth - paddingLeft - paddingRight, 0)
  }

  const clampWidth = (target: number, containerWidth: number) => {
    const maxAllowed = containerWidth - MIN_LEFT_WIDTH - RESIZER_WIDTH
    const cappedMax = Math.max(maxAllowed, 0)
    const minWidth = Math.min(MIN_RIGHT_WIDTH, cappedMax)
    const upperWidth = Math.max(minWidth, cappedMax)
    return Math.min(Math.max(target, minWidth), upperWidth)
  }

  const updateWidth = () => {
    const containerWidth = getContentWidth()
    if (!containerWidth || !Number.isFinite(containerWidth)) return
    if (hasManualResizeRef.value && workbenchWidth.value) {
      workbenchWidth.value = clampWidth(workbenchWidth.value, containerWidth)
    } else {
      const ratio = workbenchVariant.value === 'complaint' ? 2 / 3 : 1 / 2
      const target = containerWidth * ratio
      workbenchWidth.value = clampWidth(target, containerWidth)
    }
  }

  updateWidth()

  const observer = new ResizeObserver(() => {
    updateWidth()
  })
  observer.observe(container)
  return () => observer.disconnect()
})

// 监听消息变化，自动滚动到底部
watch(chatMessages, () => {
  nextTick(() => {
    scrollToBottom()
  })
})

// 滚动到底部
const scrollToBottom = () => {
  const container = scrollContainerRef.value
  if (!container) return
  container.scrollTop = container.scrollHeight
}

// 处理输入
const handleSend = async () => {
  if (!chatInput.value.trim() && selectedFiles.value.length === 0) {
    ElMessage.warning('请输入内容')
    return
  }

  await sendChatMessage(chatInput.value)
  setChatInput('')
  selectedFiles.value = []
}

// 处理文件选择
const handleFileSelect = (event: Event) => {
  const target = event.target as HTMLInputElement
  if (!target?.files) return
  const fileArray = Array.from(target.files)
  selectedFiles.value = [...selectedFiles.value, ...fileArray]
  // 触发上传
  handleFileUpload(target.files)
}

// 移除选中的文件
const handleRemoveFile = (index: number) => {
  const file = selectedFiles.value[index]
  selectedFiles.value = selectedFiles.value.filter((_, idx) => idx !== index)
  if (file) {
    // 需要从 uploadedFiles 中移除对应的文件
    const fileIndex = uploadedFiles.value.findIndex(f => f.file_id === file.name)
    if (fileIndex >= 0) {
      removeUploadedFile(uploadedFiles.value[fileIndex].file_id)
    }
  }
}

// 切换操作状态
const handleToggleAction = (actionId: string) => {
  const action = actionButtons.value.find(item => item.id === actionId)
  if (!action) return
  if (action.defaultSelected) return
  const next = new Set(selectedActionIds.value)

  if (action.kind === 'model') {
    actionButtons.value
      .filter(item => item.kind === 'model')
      .forEach(item => next.delete(item.id))
    next.add(actionId)
  } else {
    if (next.has(actionId)) {
      next.delete(actionId)
    } else {
      next.add(actionId)
    }
  }

  selectedActionIds.value = next
}

// 处理引用点击
const handleReferenceClick = (ref: any) => {
  if (workbenchVariant.value === 'expert') {
    // 参考 React 版本的逻辑，正确构造 ParsedCitation
    const sourceId = ref?.sourceId || ref?.anchorId || ref?.id || ''
    const anchorId = ref?.anchorId || ref?.sourceId || ref?.id || ''
    const citation: ParsedCitation = {
      id: ref?.id || sourceId || '',
      normalizedId: ref?.id || sourceId || '',
      anchorId,
      displayLabel: ref?.displayLabel || ref?.id || '引用',
      title: ref?.title || '',
      clause: ref?.clause,
      description: ref?.description || ref?.summary,
      sourceId,
    }
    activeExpertCitation.value = citation
    activeWorkbenchPanel.value = 'reference'
    isWorkbenchVisible.value = true
  }
}

// 处理快捷提示点击
const handlePromptClick = (prompt: string) => {
  setChatInput(prompt)
}

// 处理停止
const handleStop = () => {
  stopChatStreaming()
}

// 处理输入框按键
const handleInputKeyDown = (event: KeyboardEvent) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend()
  }
}

// 处理调整大小开始
const handleResizeStart = (event: MouseEvent) => {
  isResizing.value = true
  hasManualResizeRef.value = true
  resizeStartXRef.value = event.clientX
  resizeStartWidthRef.value = workbenchWidth.value || 0
}

// 鼠标移动处理函数
const handleMouseMove = (event: MouseEvent) => {
  if (!isResizing.value) return
  const container = layoutContainerRef.value
  if (!container) return
  const MIN_LEFT_WIDTH = 550
  const MIN_RIGHT_WIDTH = 320
  const RESIZER_WIDTH = 22
  const style = window.getComputedStyle(container)
  const paddingLeft = Number.parseFloat(style.paddingLeft || '0')
  const paddingRight = Number.parseFloat(style.paddingRight || '0')
  const contentWidth = Math.max(container.clientWidth - paddingLeft - paddingRight, 0)
  const deltaX = event.clientX - resizeStartXRef.value
  const target = resizeStartWidthRef.value - deltaX

  const maxAllowed = contentWidth - MIN_LEFT_WIDTH - RESIZER_WIDTH
  const cappedMax = Math.max(maxAllowed, 0)
  const minWidth = Math.min(MIN_RIGHT_WIDTH, cappedMax)
  const upperWidth = Math.max(minWidth, cappedMax)
  const clamped = Math.min(Math.max(target, minWidth), upperWidth)
  workbenchWidth.value = clamped
}

// 鼠标抬起处理函数
const handleMouseUp = () => {
  isResizing.value = false
}

// 在组件挂载时添加全局事件监听器
onMounted(() => {
  document.addEventListener('mousemove', handleMouseMove)
  document.addEventListener('mouseup', handleMouseUp)
})

// 在组件卸载时移除全局事件监听器
onUnmounted(() => {
  document.removeEventListener('mousemove', handleMouseMove)
  document.removeEventListener('mouseup', handleMouseUp)
})

// 计算是否可以发送
const canSend = computed(() => {
  return chatInput.value.trim().length > 0 || selectedFiles.value.length > 0
})

// 格式化时间
const formatTime = (date: Date) => {
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  })
}

// 获取用户消息内容（处理引用块）
const getUserMessageContent = (message: any): string => {
  if (message.reference_blocks && message.reference_blocks.length) {
    return '（引用原文）'
  }
  return message.text || message.content || ''
}

/**
 * 格式化引用列表
 * 过滤掉无效的引用数据，确保只显示有效的法律条文
 */
const formattedReferences = (references?: any[]) => {
  if (!references || !Array.isArray(references)) return []

  return references.filter((ref) => {
    // 确保引用有基本必需字段
    return ref && (ref.id || ref.title || ref.summary)
  }).map((ref, index) => ({
    ...ref,
    // 如果没有 displayLabel，则生成一个
    displayLabel: ref.displayLabel || `引用${index + 1}`
  }))
}
</script>

<template>
  <div class="h-full w-full">
    <div class="flex h-full w-full flex-col rounded-[28px] pt-3">
      <div ref="layoutContainerRef" :class="layoutClass">
        <!-- 调试按钮 -->
        <button
          v-if="tracePanelEnabled && !isWorkbenchVisible"
          type="button"
          class="absolute right-8 top-6 z-10 inline-flex items-center gap-2 rounded-full border border-[#E5EAF0] bg-white px-4 py-2 text-xs font-semibold text-[#5B6B7C] shadow-sm transition-colors hover:border-[#D3DAE3] hover:text-[#2E3A48] disabled:cursor-not-allowed disabled:opacity-60"
          :disabled="!chatThreadId"
          @click="() => { activeWorkbenchPanel = 'trace'; isWorkbenchVisible = true }"
        >
          <i class="fa-solid fa-bug"></i>
          调试
        </button>

        <!-- 聊天区域 -->
        <div class="flex min-h-0 flex-1 min-w-[550px] flex-col">
          <!-- 消息列表 -->
          <div
            ref="scrollContainerRef"
            class="flex-1 overflow-y-auto"
          >
            <!-- 开场消息 -->
            <div v-if="showOpening && openingMessage" class="w-full rounded-[20px] border border-[#EEF2F5] bg-white px-6 py-5 shadow-[0_4px_25px_1px_rgba(0,0,0,0.06)]">
              <div class="flex items-center gap-2 text-[16px] font-[500] leading-[24px] text-[#1F3A56]">
                <i v-if="openingMessage.icon" :class="`fa-solid ${openingMessage.icon} text-[16px] text-[#1F3A56]`"></i>
                <span>{{ openingMessage.title }}</span>
              </div>
              <div class="prose prose-sm max-w-none text-gray-600 prose-p:my-2" v-html="openingMessage.content"></div>
              <!-- 快捷提示 -->
              <div v-if="openingMessage.prompts && openingMessage.prompts.length" class="mt-4 flex flex-wrap gap-2">
                <button
                  v-for="(prompt, index) in openingMessage.prompts"
                  :key="index"
                  type="button"
                  class="inline-flex items-center justify-between gap-2 rounded-[12px] bg-[#F6F8FA] px-4 py-3 text-left text-[14px] text-[#1F3A56]"
                  @click="handlePromptClick(prompt)"
                >
                  <span>{{ prompt }}</span>
                </button>
              </div>
            </div>

            <!-- 消息列表 -->
            <div v-for="(message, index) in chatMessages" :key="message.id" class="px-6 py-4">
              <!-- 用户消息 -->
              <div v-if="message.role === 'user'" class="flex justify-end">
                <div class="flex max-w-[70%] flex-col items-end gap-2">
                  <!-- 引用块 -->
                  <div v-if="message.reference_blocks && message.reference_blocks.length" class="flex w-full flex-col gap-2">
                    <div
                      v-for="block in message.reference_blocks"
                      :key="block.id"
                      class="rounded-[12px] border border-[#E5EAF0] bg-white px-4 py-3 text-[14px] leading-[22px] text-[#1F3A56]"
                    >
                      <div class="mb-1 text-[12px] text-[#8592A6]">引用原文</div>
                      <div class="whitespace-pre-wrap">{{ block.text }}</div>
                    </div>
                  </div>

                  <!-- 消息内容 -->
                  <div class="rounded-2xl bg-[rgba(0,126,167,0.1)] px-5 py-3 text-[16px] font-[350] leading-[24px] text-[#1F3A56]">
                    <div>{{ getUserMessageContent(message) }}</div>
                  </div>

                  <!-- 附件 -->
                  <div v-if="message.attachments && message.attachments.length" class="flex flex-wrap items-center gap-2 self-start">
                    <button
                      v-for="attachment in message.attachments"
                      :key="attachment.file_id"
                      type="button"
                      class="flex items-center gap-2 text-[16px] font-[350] leading-[24px] text-[#007EA7]"
                    >
                      <img :src="uploadIcon" alt="附件" class="h-4 w-4" />
                      <span>{{ attachment.original_filename || attachment.name }}</span>
                    </button>
                  </div>

                  <!-- 时间戳 -->
                  <div class="text-[16px] leading-[24px] text-[#A3AAB5] self-end">
                    我 {{ formatTime(message.timestamp) }}
                  </div>
                </div>
              </div>

              <!-- AI 消息 -->
              <div v-else class="flex max-w-[629px] flex-col gap-4">
                <!-- 时间戳和角色 -->
                <div class="text-[16px] leading-[24px] text-[#A3AAB5]">
                  {{ assistantRole }} {{ formatTime(message.timestamp) }}
                </div>

                <!-- 工具调用进度 -->
                <div v-if="message.toolCalls && message.toolCalls.length">
                  <ToolCallProgress :tool-calls="message.toolCalls" />
                </div>

                <!-- 思考步骤 -->
                <div v-if="message.steps && message.steps.length" class="mb-3">
                  <ThinkingBlock :items="message.steps" />
                </div>

                <!-- 内容 -->
                <div v-if="message.content" class="prose prose-slate max-w-none text-[16px] font-[350] leading-[30px] text-[rgba(0,0,0,0.88)] prose-headings:mb-2 prose-headings:mt-4 prose-h2:text-[16px] prose-h2:font-[350] prose-p:my-2 prose-ul:my-2 prose-li:my-1 prose-li:text-[16px] prose-li:leading-[30px]">
                  <MarkdownContent
                    :content="message.content as string"
                    :references="message.references"
                    :onCitationClick="handleReferenceClick"
                  />
                </div>

                <!-- 引用区域 -->
                <div v-if="formattedReferences(message.references) && formattedReferences(message.references).length">
                  <div class="mb-4 h-px w-full bg-[rgba(216,216,216,1)]"></div>
                  <div class="mb-3 text-[16px] font-[350] leading-[30px] text-[rgba(0,0,0,0.45)]">检索到的法律条款</div>
                  <div class="flex flex-col gap-3">
                    <button
                      v-for="(ref, index) in formattedReferences(message.references)"
                      :key="`${ref.id}-${index}`"
                      type="button"
                      class="flex w-full items-center gap-3 rounded-[12px] bg-white px-4 py-3 text-left text-[14px] text-[rgba(31,58,86,1)]"
                      @click="handleReferenceClick(ref)"
                    >
                      <div class="flex h-[30px] w-[30px] items-center justify-center rounded-full bg-[#F3F5F7] text-[12px] text-[#8592A6]">
                        {{ index + 1 }}
                      </div>
                      <div class="flex-1 text-[14px] font-[350] text-[rgba(61,61,61,1)]">
                        <div class="leading-[30px]">{{ ref.title }}</div>
                        <div class="leading-[24px]">{{ ref.summary }}</div>
                      </div>
                    </button>
                  </div>
                </div>

                <!-- 建议按钮 -->
                <div v-if="message.suggestions && message.suggestions.length" class="rounded-[16px] bg-[#F3F5F7] p-4">
                  <div class="mb-2 text-[14px] text-[#8592A6]">建议</div>
                  <div class="flex flex-col items-start gap-2 text-[14px] text-[#1F3A56]">
                    <button
                      v-for="(item, index) in message.suggestions"
                      :key="`${item.tool_id}-${index}`"
                      type="button"
                      class="inline-flex items-center justify-between gap-3 rounded-[12px] border border-[#E2E6EA] bg-white px-4 py-3 text-left transition hover:border-[#007EA7] hover:shadow-sm"
                      @click="handlePromptClick(item.name)"
                    >
                      <div class="flex items-center gap-2">
                        <span class="text-[18px] leading-none text-[#A3AAB5]">•</span>
                        <span>{{ item.name }}</span>
                      </div>
                      <i class="fa-solid fa-chevron-right text-[#C2CAD4]"></i>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 输入区域 -->
          <div class="px-6 py-4">
            <Composer
              :footer-tag-text="footerTagText"
              :input-value="chatInput"
              @update:input-value="setChatInput"
              :placeholder-text="placeholderText"
              :selected-files="selectedFiles"
              :input-actions="actionButtons"
              :selected-action-ids="selectedActionIds"
              :show-upload="showUpload"
              :is-sending="isChatStreaming"
              :can-send="canSend"
              @send="handleSend"
              @stop="handleStop"
              @file-change="handleFileSelect"
              @remove-file="handleRemoveFile"
            />
          </div>
        </div>

        <!-- 分隔线 -->
        <div v-if="showWorkbench" class="flex items-stretch px-[5px]">
          <div
            class="relative flex w-3 cursor-col-resize select-none items-stretch"
            role="separator"
            aria-orientation="vertical"
            @mousedown="handleResizeStart"
          >
            <div class="mx-auto h-full w-px bg-[#E5EAF0]"></div>
          </div>
        </div>

        <!-- 工作台（右侧） -->
        <div v-if="showWorkbench" class="flex-none min-w-[320px]" :style="{ width: workbenchWidth ?? undefined }">
          <ExpertWorkbench
            v-model:activePanelId="activeWorkbenchPanel"
            :citation="activeExpertCitation"
            :conversation-id="chatThreadId ?? ''"
            :trace-enabled="tracePanelEnabled"
            @close="() => isWorkbenchVisible = false"
          />
        </div>
      </div>
    </div>
  </div>
</template>
