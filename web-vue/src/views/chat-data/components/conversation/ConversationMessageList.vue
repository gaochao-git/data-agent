<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElTooltip } from 'element-plus'
import type { ExecutionTargetOption, Message, MessageAttachment, MessageSegment, ToolCallInfo } from '../../types'
import { useMermaidRenderer } from '../../composables/useMermaidRenderer'
import { extractGeneratedSqlFromToolCall } from '../../utils/semanticTool'
import { fileIconNameByExtension } from '../../utils/fileTypeIcon'
import { formatDateTimeValue, formatGenerationSpeed, formatGenerationStatsDetail, renderMarkdown } from '../../utils'
import AssistantMessageFooter from '../AssistantMessageFooter.vue'
import QueryResultPanel from '../QueryResultPanel.vue'
import ToolCallList from '../ToolCallList.vue'
import ThinkingSegmentBody from './ThinkingSegmentBody.vue'

interface Props {
  messages: Message[]
  datasourceId?: string
  executionTargets?: ExecutionTargetOption[]
  activeToolCallId?: string | null
  displayContent?: (content: string) => string
  resolveToolCall?: (message: Message, toolCallId: string) => ToolCallInfo | null
  shouldInlineRenderResult?: (toolCall: ToolCallInfo) => boolean
  onSave?: (sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => void
  conversationId?: string | null
  /** 本轮 Run 仍活跃（流式进行中 / 链式接续 / 等待用户 / 恢复订阅）时抑制底部 footer */
  runActive?: boolean
  /** 生成中的实时速率（tokens/s，前端估算）；null 表示暂不展示 */
  liveGenerationSpeed?: number | null
}

const props = withDefaults(defineProps<Props>(), {
  datasourceId: '',
  executionTargets: () => [],
  activeToolCallId: null,
  displayContent: (content: string) => content.trim(),
  resolveToolCall: (message: Message, toolCallId: string) => (
    message.toolCalls?.find(toolCall => toolCall.tool_call_id === toolCallId) || null
  ),
  shouldInlineRenderResult: (toolCall: ToolCallInfo) => (
    toolCall.renderType === 'queryResult' && Boolean(toolCall.renderId)
  ),
  runActive: false,
  liveGenerationSpeed: null,
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
  openAttachment: [attachment: MessageAttachment]
}>()

// 用户手动展开的思考分段（跨流式过程保持；未点开的分段一律默认收起）
const expandedThinkingSegmentKeys = ref<Set<string>>(new Set())

// 消息里的 ```mermaid 代码块渲染为图表：流式增量期间防抖，静默后跑一轮
const messageListRef = ref<HTMLElement | null>(null)
const { schedule: scheduleMermaid } = useMermaidRenderer(() => messageListRef.value)
watch(() => props.messages, scheduleMermaid, { deep: true, immediate: true })

const getThinkingSegmentKey = (messageId: string, thinkingSegmentId: string) => `${messageId}:${thinkingSegmentId}`

const getThinkingSegment = (message: Message, thinkingSegmentId: string) => (
  message.thinkingSegments?.find(segment => segment.id === thinkingSegmentId) || null
)

const isThinkingSegmentCollapsed = (message: Message, thinkingSegmentId: string): boolean => (
  // 用户点开的分段记在 Set 里；不在 Set 里 = 默认收起（含流式中的分段）
  !expandedThinkingSegmentKeys.value.has(getThinkingSegmentKey(message.id, thinkingSegmentId))
)

const toggleThinkingSegment = (message: Message, thinkingSegmentId: string) => {
  if (!getThinkingSegment(message, thinkingSegmentId)) return
  const key = getThinkingSegmentKey(message.id, thinkingSegmentId)
  const next = new Set(expandedThinkingSegmentKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedThinkingSegmentKeys.value = next
}

const getActiveThinkingSegmentId = (message: Message): string | null => {
  if (!message.isStreaming) return null
  const segments = message.segments || []
  const lastSegment = segments[segments.length - 1]
  return lastSegment?.type === 'thinkingSegment' ? lastSegment.thinkingSegmentId : null
}

// 收起态下，流式中的思考分段在同一行露尾部文字（走马灯式：只显示最新思考到的内容）
const THINKING_PREVIEW_TAIL_LENGTH = 80

const getThinkingPreviewText = (message: Message, thinkingSegmentId: string): string => {
  const segment = getThinkingSegment(message, thinkingSegmentId)
  if (!segment) return ''
  const singleLine = segment.content.replace(/\s+/g, ' ').trim()
  if (!singleLine) return ''
  return singleLine.length > THINKING_PREVIEW_TAIL_LENGTH
    ? `…${singleLine.slice(-THINKING_PREVIEW_TAIL_LENGTH)}`
    : singleLine
}

const resolveToolCall = (message: Message, toolCallId: string): ToolCallInfo | null => (
  props.resolveToolCall(message, toolCallId)
)

const getRenderableQueryResultToolCall = (message: Message, toolCallId: string): ToolCallInfo | null => {
  const toolCall = resolveToolCall(message, toolCallId)
  return toolCall && props.shouldInlineRenderResult(toolCall) ? toolCall : null
}

const getDisplayContent = (content: string): string => props.displayContent(content)

// 隐藏用户消息中的附件路径占位（[attachment] /mnt/...），附件由 message.attachments 卡片渲染
const ATTACHMENT_PLACEHOLDER_RE = /^\[attachment\].*$/gm
const stripAttachmentPlaceholders = (content: string): string =>
  content.replace(ATTACHMENT_PLACEHOLDER_RE, '').replace(/\n{3,}/g, '\n\n').trimEnd()

// 从用户消息内容解析 [attachment] /path 占位，合成附件卡片（历史会话无 message.attachments 时兜底）
const ATTACHMENT_PATH_RE = /^\[attachment\]\s+(.+)$/gm
const parseAttachmentPlaceholders = (content: string): MessageAttachment[] => {
  if (!content) return []
  const attachments: MessageAttachment[] = []
  let match: RegExpExecArray | null
  while ((match = ATTACHMENT_PATH_RE.exec(content)) !== null) {
    const path = match[1]!.trim()
    const filename = path.split('/').pop() || path
    const ext = filename.includes('.') ? filename.split('.').pop()!.toLowerCase() : ''
    attachments.push({ file_id: path, filename, file_type: ext, original_path: path })
  }
  return attachments
}

// 当前消息优先用结构化 message.attachments（含完整元数据）；历史会话无 attachments 时回退到内容占位解析
const getEffectiveAttachments = (message: Message): MessageAttachment[] =>
  message.attachments && message.attachments.length > 0
    ? message.attachments
    : parseAttachmentPlaceholders(message.content)

// 用户消息上展示的 @ 引用工作区文件列表（本地发送与历史回放统一走 fileSelections）
const getUserMentionedFiles = (message: Message): Array<{ path: string; name: string }> => {
  if (message.role !== 'user') return []
  return (message.fileSelections || [])
    .map(path => String(path || '').trim())
    .filter(Boolean)
    .map(path => ({ path, name: path.split('/').pop() || path }))
}

const formatCompressionTokens = (value?: number): string => new Intl.NumberFormat('zh-CN').format(Math.max(0, Number(value) || 0))

const hasVisibleAssistantContent = (content: string): boolean => {
  const text = getDisplayContent(content)
  return Boolean(text && text !== '正在思考...')
}

const shouldShowStreamingStatus = (message: Message): boolean => (
  hasVisibleAssistantContent(message.content)
  || Boolean(message.thinkingSegments?.length)
  || Boolean(message.toolCalls?.length)
)

const getCopyableAssistantContent = (message: Message): string => {
  if (message.role !== 'assistant') return ''
  const segmentText = (message.segments || [])
    .filter((segment): segment is Extract<MessageSegment, { type: 'text' }> => segment.type === 'text')
    .map(segment => getDisplayContent(segment.content))
    .filter(Boolean)
    .join('\n\n')
    .trim()
  return segmentText || getDisplayContent(message.content) || getDisplayContent(message.error || '')
}

const shouldShowMessageCopyButton = (message: Message): boolean => Boolean(getCopyableAssistantContent(message))

/**
 * run 活跃时，只有「当前 run 产生」的消息（最后一条 user 消息之后的）才抑制 footer；
 * 历史轮次的 footer 保持稳定，不随新 run 启停闪烁。
 */
const activeRunStartIndex = computed(() => {
  if (!props.runActive) return -1
  for (let i = props.messages.length - 1; i >= 0; i--) {
    if (props.messages[i]?.role === 'user') return i
  }
  return -1
})

const shouldShowAssistantFooter = (message: Message, index: number): boolean => {
  if (message.isStreaming) return false
  if (props.runActive && index > activeRunStartIndex.value) return false
  return shouldShowMessageCopyButton(message)
}

const USER_MESSAGE_SCROLL_LINE_THRESHOLD = 10
const USER_MESSAGE_SCROLL_MAX_HEIGHT = '240px'

const isLongUserMessage = (content: string): boolean => {
  const text = String(content || '')
  if (!text) return false
  const lineCount = text.split('\n').length
  return lineCount > USER_MESSAGE_SCROLL_LINE_THRESHOLD
}

const formatFileSize = (bytes?: number): string => {
  if (!bytes || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

const getFileIconClass = (fileType?: string): string => fileIconNameByExtension(fileType)

const handleAttachmentClick = (attachment: MessageAttachment) => {
  emit('openAttachment', attachment)
}

// @ 引用 chip 点击：复用附件打开链路（presented artifact 同款），在会话工作区面板
// 定位并预览该文件。file_id 用 mention: 前缀避免与真实附件 id 混淆。
const handleMentionFileClick = (file: { path: string; name: string }) => {
  emit('openAttachment', {
    file_id: `mention:${file.path}`,
    filename: file.name,
    file_type: file.name.includes('.') ? file.name.split('.').pop()!.toLowerCase() : undefined,
    original_path: file.path,
  })
}

const handleCopyAssistantMessage = async (message: Message) => {
  const text = getCopyableAssistantContent(message)
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch (error) {
    console.error('复制回复失败:', error)
    ElMessage.error('复制失败，请稍后重试')
  }
}

</script>

<template>
  <div ref="messageListRef" class="chat-message-list mx-auto flex w-[90%] max-w-[774px] flex-col gap-5">
    <div
      v-for="(message, messageIndex) in messages"
      :key="message.id"
      class="flex"
      :class="message.role === 'user' ? 'justify-end' : message.role === 'context_compression' ? 'justify-center' : 'justify-start'"
      :data-conversation-turn-id="message.role === 'user' ? message.id : undefined"
    >
      <div :class="message.role === 'user' ? 'flex max-w-[80%] flex-col items-end gap-1.5' : 'w-full min-w-0'">
        <template v-if="message.role === 'user'">
          <div
            v-if="getUserMentionedFiles(message).length"
            class="flex w-full flex-row-reverse flex-wrap gap-1.5"
            title="本轮引用的工作区文件"
          >
            <button
              v-for="file in getUserMentionedFiles(message)"
              :key="file.path"
              type="button"
              class="flex items-center gap-1.5 rounded-full border border-[#1677FF]/20 bg-white/80 px-2.5 py-1 text-xs text-[#1F3A56] transition-colors duration-150 hover:border-[#1677FF]/40 hover:bg-white"
              :title="file.path"
              @click="handleMentionFileClick(file)"
            >
              <i class="fa-solid fa-at text-[11px] text-[#1677FF]"></i>
              <span class="max-w-[200px] truncate">{{ file.name }}</span>
            </button>
          </div>
          <div v-if="getEffectiveAttachments(message).length" class="flex w-full flex-col gap-1.5">
            <button
              v-for="attachment in getEffectiveAttachments(message)"
              :key="attachment.file_id"
              type="button"
              class="flex items-center gap-2 rounded-lg border border-[#1677FF]/20 bg-white/80 px-2.5 py-1.5 text-left text-xs text-[#1F3A56] transition-colors duration-150 hover:bg-white hover:border-[#1677FF]/40"
              @click="handleAttachmentClick(attachment)"
            >
              <i :class="['text-[13px] text-[#1677FF]', getFileIconClass(attachment.file_type)]"></i>
              <span class="min-w-0 flex-1 truncate">{{ attachment.filename }}</span>
              <span v-if="formatFileSize(attachment.file_size)" class="shrink-0 text-[10px] text-gray-400">{{ formatFileSize(attachment.file_size) }}</span>
              <i class="fa-solid fa-chevron-right text-[9px] text-gray-400"></i>
            </button>
          </div>
          <div
            class="rounded-2xl rounded-br-md bg-[#1677FF]/10 px-4 py-2.5 text-sm leading-relaxed text-[#1F3A56] shadow-[0_2px_8px_rgba(59,130,246,0.12)]"
            :style="isLongUserMessage(stripAttachmentPlaceholders(message.content)) ? { maxHeight: USER_MESSAGE_SCROLL_MAX_HEIGHT, overflowY: 'auto' } : undefined"
          >
            <div class="whitespace-pre-wrap">{{ stripAttachmentPlaceholders(message.content) }}</div>
          </div>
          <div class="mr-1 text-[10px] text-gray-400">{{ formatDateTimeValue(message.timestamp) }}</div>
        </template>

        <template v-else-if="message.role === 'tool'">
          <div class="rounded-xl border border-slate-100 bg-white px-4 py-3 shadow-sm">
            <ToolCallList
              v-if="message.toolCalls?.length"
              :tool-calls="message.toolCalls"
              :datasource-id="datasourceId"
              :execution-targets="executionTargets"
              compact
              :active-tool-call-id="activeToolCallId"
              :conversation-id="conversationId"
              @select-tool="emit('selectTool', $event)"
            />
          </div>
          <div v-if="formatDateTimeValue(message.timestamp)" class="px-1 text-right text-[10px] text-gray-400">
            {{ formatDateTimeValue(message.timestamp) }}
          </div>
        </template>

        <template v-else-if="message.role === 'context_compression'">
          <el-tooltip v-if="message.contextCompression?.compressing" placement="top" effect="light">
            <template #content>
              <div class="text-xs leading-5">正在压缩历史上下文，稍候继续执行</div>
            </template>
            <div class="flex cursor-default items-center gap-3 py-1 text-xs text-slate-400">
              <span class="h-px flex-1 bg-slate-200"></span>
              <span class="flex items-center gap-1.5 whitespace-nowrap">
                <i class="fa-solid fa-circle-notch fa-spin"></i>
                上下文压缩中
              </span>
              <span class="h-px flex-1 bg-slate-200"></span>
            </div>
          </el-tooltip>
          <el-tooltip v-else placement="top" effect="light">
            <template #content>
              <div class="space-y-1 text-xs leading-5">
                <div>压缩前 {{ formatCompressionTokens(message.contextCompression?.estimated_tokens_before) }} tokens</div>
                <div>压缩后 {{ formatCompressionTokens(message.contextCompression?.estimated_tokens_after) }} tokens</div>
                <div>释放 {{ formatCompressionTokens(message.contextCompression?.estimated_cleared_tokens) }} tokens，压缩 {{ formatCompressionTokens(message.contextCompression?.compressed_messages) }} 条消息</div>
              </div>
            </template>
            <div class="flex cursor-default items-center gap-3 py-1 text-xs text-slate-400">
              <span class="h-px flex-1 bg-slate-200"></span>
              <span class="whitespace-nowrap">上下文已压缩</span>
              <span class="h-px flex-1 bg-slate-200"></span>
            </div>
          </el-tooltip>
        </template>

        <template v-else>
          <div class="min-w-0 rounded-2xl rounded-bl-md border border-black/[0.06] bg-white p-4 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
            <div v-if="message.segments?.length" class="flex min-w-0 flex-col gap-3">
              <template v-for="(segment, index) in message.segments" :key="`${segment.type}-${index}`">
                <div
                  v-if="segment.type === 'text' && getDisplayContent(segment.content)"
                  class="conversation-markdown min-w-0 text-sm leading-relaxed text-slate-700"
                  v-html="renderMarkdown(getDisplayContent(segment.content))"
                ></div>

                <div
                  v-else-if="segment.type === 'thinkingSegment' && getThinkingSegment(message, segment.thinkingSegmentId)"
                  class="min-w-0"
                >
                  <button
                    type="button"
                    class="group inline-flex max-w-full items-center gap-2 rounded-md border-none bg-transparent px-2.5 py-1.5 text-left text-xs font-medium text-slate-400 transition-colors duration-200 hover:text-slate-600"
                    @click="toggleThinkingSegment(message, segment.thinkingSegmentId)"
                  >
                    <i
                      :class="[
                        'fa-solid fa-brain text-xs text-slate-400 transition-colors duration-200 group-hover:text-slate-600',
                        getActiveThinkingSegmentId(message) === segment.thinkingSegmentId ? 'fa-fade' : '',
                      ]"
                    ></i>
                    <span>{{ getActiveThinkingSegmentId(message) === segment.thinkingSegmentId ? '思考中' : '思考' }}</span>
                    <i
                      :class="[
                        'fa-solid fa-chevron-right text-[10px] text-slate-400 transition-all duration-200 group-hover:text-slate-600',
                        !isThinkingSegmentCollapsed(message, segment.thinkingSegmentId)
                          ? 'rotate-90 opacity-100'
                          : 'opacity-0 group-hover:opacity-100',
                      ]"
                    ></i>
                    <span
                      v-if="getActiveThinkingSegmentId(message) === segment.thinkingSegmentId
                        && isThinkingSegmentCollapsed(message, segment.thinkingSegmentId)
                        && getThinkingPreviewText(message, segment.thinkingSegmentId)"
                      class="min-w-0 flex-1 truncate text-xs font-normal text-slate-400/80"
                    >{{ getThinkingPreviewText(message, segment.thinkingSegmentId) }}</span>
                  </button>
                  <ThinkingSegmentBody
                    v-if="!isThinkingSegmentCollapsed(message, segment.thinkingSegmentId)"
                    :content="getThinkingSegment(message, segment.thinkingSegmentId)!.content"
                    :active="getActiveThinkingSegmentId(message) === segment.thinkingSegmentId"
                  />
                </div>

                <div v-else-if="segment.type === 'toolCall' && resolveToolCall(message, segment.toolCallId)" class="min-w-0">
                  <ToolCallList
                    :tool-calls="[resolveToolCall(message, segment.toolCallId)!]"
                    :datasource-id="datasourceId"
                    :execution-targets="executionTargets"
                    compact
                    :active-tool-call-id="activeToolCallId"
                    :conversation-id="conversationId"
                    @select-tool="emit('selectTool', $event)"
                  />
                  <QueryResultPanel
                    v-if="getRenderableQueryResultToolCall(message, segment.toolCallId)"
                    class="mt-2 max-h-[46vh]"
                    :result-data="getRenderableQueryResultToolCall(message, segment.toolCallId)!.renderId!"
                    :conversation-id="conversationId"
                    :sql="extractGeneratedSqlFromToolCall(getRenderableQueryResultToolCall(message, segment.toolCallId)!)"
                    :semantic-params="getRenderableQueryResultToolCall(message, segment.toolCallId)!.semanticParams"
                    :on-save="onSave"
                  />
                </div>
              </template>
            </div>

            <div
              v-else-if="getDisplayContent(message.content)"
              class="conversation-markdown min-w-0 text-sm leading-relaxed text-slate-700"
              v-html="renderMarkdown(getDisplayContent(message.content))"
            ></div>
            <div v-else-if="message.isStreaming" class="flex items-center gap-2 text-sm text-slate-500">
              <i class="fa-solid fa-spinner fa-spin text-xs"></i>
              <span>正在处理...</span>
            </div>

            <slot name="assistant-extra" :message="message" />

            <div v-if="message.error" class="mt-3 flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-600">
              <i class="fa-solid fa-circle-exclamation"></i>
              <span>{{ message.error }}</span>
            </div>
          </div>

          <div v-if="shouldShowAssistantFooter(message, messageIndex)" class="mt-1 px-0">
            <AssistantMessageFooter :timestamp="formatDateTimeValue(message.timestamp)">
              <button
                type="button"
                class="inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-transparent px-2 text-[11px] text-gray-500 transition-all duration-200 hover:bg-blue-500/5 hover:text-blue-500"
                @click="handleCopyAssistantMessage(message)"
              >
                <i class="fa-regular fa-copy text-[11px]"></i>
                <span>复制</span>
              </button>
              <el-tooltip
                v-if="message.generationStats"
                :content="formatGenerationStatsDetail(message.generationStats)"
                placement="top"
                effect="light"
              >
                <span class="inline-flex items-center gap-1 whitespace-nowrap text-[11px] text-gray-400">
                  <i class="fa-solid fa-bolt text-[10px]"></i>
                  {{ formatGenerationSpeed(message.generationStats) }}
                </span>
              </el-tooltip>
            </AssistantMessageFooter>
          </div>

          <div v-if="message.isStreaming && shouldShowStreamingStatus(message)" class="mt-2 ml-3 flex items-center gap-2 text-xs text-slate-400">
            <span>{{ hasVisibleAssistantContent(message.content) ? '正在生成' : '正在思考' }}</span>
            <span class="h-1.5 w-1.5 rounded-full bg-[#1677FF] animate-pulse"></span>
            <span class="h-1.5 w-1.5 rounded-full bg-[#1677FF] animate-pulse [animation-delay:120ms]"></span>
            <span class="h-1.5 w-1.5 rounded-full bg-[#1677FF] animate-pulse [animation-delay:240ms]"></span>
            <span v-if="liveGenerationSpeed" class="whitespace-nowrap text-[11px] text-slate-400">~{{ liveGenerationSpeed }} tok/s</span>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
