/**
 * 消息渲染投影层（唯一事实源）。
 *
 * 主对话与子智能体过程都从这里拿「块」，组件只负责把块渲染成 DOM：
 * - 主消息：`Message`（role + segments + toolCalls/thinkingSegments 引用）→ `MessageBlock[]`
 * - 子智能体过程：`ToolCallInfo.subagentTrail` → `SubagentTrailBlock[]`
 *
 * 纯函数、无 DOM 依赖，便于单测；新增消息/分段类型时改这里，而不是改模板分支。
 */
import type {
  Message,
  MessageAttachment,
  SubagentTrailItem,
  ThinkingSegment,
  ToolCallInfo,
} from '../types'
import { getToolCompactDescription, getToolDisplayName, getToolTechnicalDescription } from './toolDisplay'
import { normalizeToolCallForDisplay, shouldInlineRenderQueryResult } from './semanticTool'

/** 思考收起态尾部预览长度（主对话与子智能体共用口径）。 */
export const THINKING_PREVIEW_TAIL_LENGTH = 80
/** 用户长消息折叠展开展示的最大行数。 */
export const USER_MESSAGE_SCROLL_LINE_THRESHOLD = 10
/** 用户长消息展开后的最大高度。 */
export const USER_MESSAGE_SCROLL_MAX_HEIGHT = '240px'

export type TextNormalizer = (content: string) => string

const defaultNormalizeText: TextNormalizer = content => String(content || '').trim()

/** 文本块：markdown 正文（html 由 MarkdownBlock 渲染）。 */
export interface MessageTextBlock {
  type: 'text'
  key: string
  content: string
  /** 是否为「最终回答」：折叠工作过程时唯一留在折叠区外的文本块 */
  finalAnswer: boolean
}

/** 思考块：思考分段正文 + 收起态尾部预览 + 是否正在流式追加。 */
export interface MessageThinkingBlock {
  type: 'thinking'
  key: string
  segmentId: string
  content: string
  active: boolean
  preview: string
}

/** 工具块：工具调用 + 需要内联渲染时附带的查询结果。 */
export interface MessageToolBlock {
  type: 'tool'
  key: string
  toolCall: ToolCallInfo
  queryResultToolCall: ToolCallInfo | null
}

export type MessageBlock = MessageTextBlock | MessageThinkingBlock | MessageToolBlock

export interface MessageBlockDeps {
  /** 强制整段内容都算工作过程（中间段助手消息不露最终回答） */
  forceWorkProcess?: boolean;
  resolveToolCall?: (message: Message, toolCallId: string) => ToolCallInfo | null
  shouldInlineRenderResult?: (toolCall: ToolCallInfo) => boolean
  normalizeText?: TextNormalizer
}

export const resolveMessageToolCall = (message: Message, toolCallId: string): ToolCallInfo | null => (
  message.toolCalls?.find(toolCall => toolCall.tool_call_id === toolCallId) || null
)

export const getThinkingSegment = (message: Message, thinkingSegmentId: string): ThinkingSegment | null => (
  message.thinkingSegments?.find(segment => segment.id === thinkingSegmentId) || null
)

/** 收起态尾部预览：压成单行后只保留最后 N 个字符，前面补省略号。 */
export const getThinkingPreviewText = (
  content: string,
  maxLength: number = THINKING_PREVIEW_TAIL_LENGTH,
): string => {
  const singleLine = String(content || '').replace(/\s+/g, ' ').trim()
  if (!singleLine) return ''
  return singleLine.length > maxLength ? `…${singleLine.slice(-maxLength)}` : singleLine
}

/** 正在流式输出的思考分段：只有最后一段且形如 thinkingSegment 时才算。 */
export const getActiveThinkingSegmentId = (message: Message): string | null => {
  if (!message.isStreaming) return null
  const segments = message.segments || []
  const lastSegment = segments[segments.length - 1]
  return lastSegment?.type === 'thinkingSegment' ? lastSegment.thinkingSegmentId : null
}

/**
 * 最终回答分段：最后一段有可见文本的 text 分段（ZCode 同款切分口径）。
 * 无可见文本时返回 -1：说明没有可留在折叠区外的最终回答。
 */
export const findFinalAnswerSegmentIndex = (
  message: Message,
  normalizeText: TextNormalizer = defaultNormalizeText,
): number => {
  const segments = message.segments ?? []
  for (let index = segments.length - 1; index >= 0; index--) {
    const segment = segments[index]
    if (segment?.type === 'text' && normalizeText(segment.content)) return index
  }
  return -1
}

const buildSegmentBlocks = (message: Message, deps: MessageBlockDeps): MessageBlock[] => {
  const normalizeText = deps.normalizeText || defaultNormalizeText
  const resolveToolCall = deps.resolveToolCall || resolveMessageToolCall
  const shouldInline = deps.shouldInlineRenderResult || (() => false)
  const activeThinkingId = getActiveThinkingSegmentId(message)
  const finalAnswerIndex = deps.forceWorkProcess ? -1 : findFinalAnswerSegmentIndex(message, normalizeText)
  const blocks: MessageBlock[] = []

  for (const [index, segment] of (message.segments || []).entries()) {
    if (segment.type === 'text') {
      const content = normalizeText(segment.content)
      if (!content) continue
      blocks.push({ type: 'text', key: `text-${index}`, content, finalAnswer: index === finalAnswerIndex })
      continue
    }
    if (segment.type === 'thinkingSegment') {
      const thinking = getThinkingSegment(message, segment.thinkingSegmentId)
      if (!thinking) continue
      const active = activeThinkingId === segment.thinkingSegmentId
      blocks.push({
        type: 'thinking',
        key: `thinking-${segment.thinkingSegmentId}`,
        segmentId: segment.thinkingSegmentId,
        content: thinking.content,
        active,
        preview: active ? getThinkingPreviewText(thinking.content) : '',
      })
      continue
    }
    const toolCall = resolveToolCall(message, segment.toolCallId)
    if (!toolCall) continue
    blocks.push({
      type: 'tool',
      key: `tool-${segment.toolCallId}`,
      toolCall,
      queryResultToolCall: shouldInline(toolCall) ? toolCall : null,
    })
  }
  return blocks
}

/**
 * 主消息 → 渲染块。
 * 有 segments 时按分段顺序出块（文本/思考/工具）；没有 segments 时退化成 content 单文本块。
 */
export const buildMessageBlocks = (message: Message, deps: MessageBlockDeps = {}): MessageBlock[] => {
  const segments = message.segments || []
  if (segments.length > 0) return buildSegmentBlocks(message, deps)
  const normalizeText = deps.normalizeText || defaultNormalizeText
  const content = normalizeText(message.content)
  return content ? [{ type: 'text', key: 'content', content, finalAnswer: true }] : []
}

/** 助手正文是否已有可见内容（排除「正在思考...」占位）。 */
export const hasVisibleAssistantContent = (
  content: string,
  normalizeText: TextNormalizer = defaultNormalizeText,
): boolean => {
  const text = normalizeText(content)
  return Boolean(text && text !== '正在思考...')
}

/** 流式状态行是否展示：有正文 / 思考 / 工具调用任一。 */
export const shouldShowStreamingStatus = (
  message: Message,
  normalizeText: TextNormalizer = defaultNormalizeText,
): boolean => (
  hasVisibleAssistantContent(message.content, normalizeText)
  || Boolean(message.thinkingSegments?.length)
  || Boolean(message.toolCalls?.length)
)

/**
 * 助手可复制内容：与折叠口径一致，只复制折叠区外的最终回答。
 * 有 segments 但没有最终回答时返回空（隐藏的中间叙述不能通过复制泄露）。
 */
export const getAssistantCopyText = (
  message: Message,
  normalizeText: TextNormalizer = defaultNormalizeText,
): string => {
  if (message.role !== 'assistant') return ''
  const segments = message.segments || []
  const finalIndex = findFinalAnswerSegmentIndex(message, normalizeText)
  if (finalIndex >= 0) {
    const finalSegment = segments[finalIndex]
    if (finalSegment?.type === 'text') return normalizeText(finalSegment.content)
  }
  if (segments.length > 0) return normalizeText(message.error || '')
  return normalizeText(message.content) || normalizeText(message.error || '')
}

/** 工作过程按钮是否出现：折叠区里有可渲染的思考/工具/中间叙述块。 */
export const hasWorkProcess = (blocks: MessageBlock[]): boolean => (
  blocks.some(block => block.type !== 'text' || !block.finalAnswer)
)

/** 工作过程摘要文案：思考 N · 工具 M（未渲染出块的分段不计入）。 */
export const summarizeWorkProcess = (blocks: MessageBlock[]): string => {
  const thinkingCount = blocks.filter(block => block.type === 'thinking').length
  const toolCount = blocks.filter(block => block.type === 'tool').length
  const parts: string[] = []
  if (thinkingCount) parts.push(`思考 ${thinkingCount}`)
  if (toolCount) parts.push(`工具 ${toolCount}`)
  return parts.join(' · ')
}

/**
 * run 活跃时，只有「当前 run 产生」的消息（最后一条 user 消息之后）才抑制 footer；
 * 历史轮次的 footer 保持稳定，不随新 run 启停闪烁。
 */
export const resolveActiveRunStartIndex = (messages: Message[], runActive: boolean): number => {
  if (!runActive) return -1
  for (let index = messages.length - 1; index >= 0; index--) {
    if (messages[index]?.role === 'user') return index
  }
  return -1
}

export const shouldShowAssistantFooter = (
  message: Message,
  index: number,
  options: { runActive: boolean; activeRunStartIndex: number; copyText?: string },
): boolean => {
  if (message.isStreaming) return false
  if (options.runActive && index > options.activeRunStartIndex) return false
  return Boolean(options.copyText ?? getAssistantCopyText(message))
}

// ---- 用户消息：引用片段 / @ 文件 / 附件 / 长消息 ----

/** 隐藏用户消息中的附件路径占位（[attachment] /mnt/...），附件由附件卡片渲染。 */
export const stripAttachmentPlaceholders = (content: string): string =>
  String(content || '').replace(/^\[attachment\].*$/gm, '').replace(/\n{3,}/g, '\n\n').trimEnd()

/** 从用户消息内容解析 [attachment] /path 占位，历史会话无结构化 attachments 时兜底。 */
export const parseAttachmentPlaceholders = (content: string): MessageAttachment[] => {
  if (!content) return []
  const attachments: MessageAttachment[] = []
  for (const match of content.matchAll(/^\[attachment\]\s+(.+)$/gm)) {
    const path = match[1]!.trim()
    const filename = path.split('/').pop() || path
    const ext = filename.includes('.') ? filename.split('.').pop()!.toLowerCase() : ''
    attachments.push({ file_id: path, filename, file_type: ext, original_path: path })
  }
  return attachments
}

/** 优先结构化 attachments（含完整元数据）；历史会话无 attachments 时回退到内容占位解析。 */
export const getEffectiveAttachments = (message: Message): MessageAttachment[] => (
  message.attachments && message.attachments.length > 0
    ? message.attachments
    : parseAttachmentPlaceholders(message.content)
)

/** 用户消息上展示的 @ 引用工作区文件（本地发送与历史回放统一走 fileSelections）。 */
export const getUserMentionedFiles = (message: Message): Array<{ path: string; name: string }> => {
  if (message.role !== 'user') return []
  return (message.fileSelections || [])
    .map(path => String(path || '').trim())
    .filter(Boolean)
    .map(path => ({ path, name: path.split('/').pop() || path }))
}

/** 用户消息上展示的划选引用片段（本地发送与历史回放统一走 quotedSnippets）。 */
export const getUserQuotedSnippets = (message: Message): Array<{ id: string; text: string }> => {
  if (message.role !== 'user') return []
  return (message.quotedSnippets || [])
    .map(item => ({ id: String(item?.id || ''), text: String(item?.text || '').trim() }))
    .filter(item => item.text)
}

/** 用户长消息：超过阈值行数时展开区限高滚动。 */
export const isLongUserMessage = (content: string): boolean => {
  const text = String(content || '')
  if (!text) return false
  return text.split('\n').length > USER_MESSAGE_SCROLL_LINE_THRESHOLD
}

export const formatFileSize = (bytes?: number): string => {
  if (!bytes || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** 压缩事件的 token 数展示（千分位）。 */
export const formatCompressionTokens = (value?: number): string => (
  new Intl.NumberFormat('zh-CN').format(Math.max(0, Number(value) || 0))
)

// ---- 子智能体过程：trail → 渲染块 ----

export interface SubagentTrailThinking {
  content: string
  truncated: boolean
  /** 父 task 仍在跑且这是最后一条条目：思考可能还在增量。 */
  active: boolean
  preview: string
}

export interface SubagentTrailMessageBlock {
  type: 'message'
  key: string
  content: string
  truncated: boolean
  thinking: SubagentTrailThinking | null
}

export interface SubagentTrailToolBlock {
  type: 'tool'
  key: string
  toolCall: ToolCallInfo
  displayName: string
  compactDescription: string
  technicalDescription: string
  isRunning: boolean
  isError: boolean
  /** 内层查询工具的结果面板（仅完整过程视图渲染） */
  queryResultToolCall: ToolCallInfo | null
}

export interface SubagentTrailCompactionBlock {
  type: 'compaction'
  key: string
  compaction: {
    id: string
    timestamp?: string
    estimated_tokens_before?: number
    estimated_tokens_after?: number
    estimated_cleared_tokens?: number
    compressed_messages?: number
  }
}

export type SubagentTrailBlock = SubagentTrailMessageBlock | SubagentTrailToolBlock | SubagentTrailCompactionBlock

const isSubagentParentRunning = (toolCall: ToolCallInfo): boolean => (
  toolCall.status === 'running' || toolCall.status === 'generating'
)

/** 内层工具调用：trail 条目 → 与主对话同形状的 ToolCallInfo（供 chip / 详情面板复用）。 */
const buildTrailToolCall = (item: SubagentTrailItem): ToolCallInfo => ({
  tool_call_id: item.id,
  tool_name: item.tool_name || 'tool_call',
  args: item.args || {},
  result: item.result,
  status: item.status || 'running',
  started_at_ms: item.started_at_ms ?? null,
  ended_at_ms: item.ended_at_ms ?? null,
})

/** 子智能体 trail → 渲染块（消息与工具按发生顺序混排）。 */
export const buildSubagentTrailBlocks = (toolCall: ToolCallInfo): SubagentTrailBlock[] => {
  const trail = toolCall.subagentTrail || []
  const parentRunning = isSubagentParentRunning(toolCall)
  return trail.map((item, index) => {
    if (item.kind === 'compaction') {
      return {
        type: 'compaction',
        key: item.id,
        compaction: {
          id: item.id,
          timestamp: item.timestamp,
          estimated_tokens_before: item.estimated_tokens_before,
          estimated_tokens_after: item.estimated_tokens_after,
          estimated_cleared_tokens: item.estimated_cleared_tokens,
          compressed_messages: item.compressed_messages,
        },
      }
    }
    if (item.kind === 'tool') {
      const toolCallItem = normalizeToolCallForDisplay(buildTrailToolCall(item))
      return {
        type: 'tool',
        key: item.id,
        toolCall: toolCallItem,
        displayName: getToolDisplayName(toolCallItem.tool_name, toolCallItem.args),
        compactDescription: getToolCompactDescription(toolCallItem.tool_name, toolCallItem.args || {}),
        technicalDescription: getToolTechnicalDescription(toolCallItem.tool_name || '', toolCallItem.args),
        isRunning: item.status === 'running',
        isError: item.status === 'error',
        queryResultToolCall: shouldInlineRenderQueryResult(toolCallItem) ? toolCallItem : null,
      }
    }
    const thinkingContent = item.thinking || ''
    const active = parentRunning && index === trail.length - 1
    return {
      type: 'message',
      key: item.id,
      content: item.content || '',
      truncated: Boolean(item.truncated),
      thinking: thinkingContent
        ? {
            content: thinkingContent,
            truncated: Boolean(item.thinkingTruncated),
            active,
            preview: active ? getThinkingPreviewText(thinkingContent) : '',
          }
        : null,
    }
  })
}

/**
 * 子智能体 trail → 主对话消息模型（聊天式过程视图复用消息组件）。
 *
 * - 任务书（task.description）→ 一条 user 消息；
 * - 整段子智能体过程 → **一条** assistant 消息：思考/中间叙述/内层工具都进
 *   segments（与主对话「一条助手消息里过程 + 最终回答」结构一致），最后一段
 *   文本即最终报告，由消息组件按工作过程折叠口径渲染。
 *
 * 合并成单条消息而不是每条 trail 记录一个气泡：子智能体一轮任务会有多个内层
 * 模型轮次，拆成多个气泡会出现一屏「工作过程」重复标题，反而不像主对话。
 */
export const buildSubagentMessages = (toolCall: ToolCallInfo): Message[] => {
  const blocks = buildSubagentTrailBlocks(toolCall)
  const parentRunning = isSubagentParentRunning(toolCall)
  const messages: Message[] = []
  const prompt = String(toolCall.args?.description || '').trim()
  if (prompt) {
    messages.push({ id: `${toolCall.tool_call_id}-task`, role: 'user', content: prompt })
  }

  const assistant: Message = {
    id: `${toolCall.tool_call_id}-process`,
    role: 'assistant',
    content: '',
    segments: [],
    thinkingSegments: [],
    toolCalls: [],
    isStreaming: parentRunning,
  }

  for (const block of blocks) {
    if (block.type === 'compaction') {
      // 压缩分隔卡：按发生位置断开助手气泡，复用主对话的 CompactionMessage
      if (assistant.segments?.length) {
        messages.push({
          ...assistant,
          segments: [...assistant.segments],
          thinkingSegments: [...(assistant.thinkingSegments || [])],
          isStreaming: false,
          forceWorkProcess: true,
        })
      }
      messages.push({
        id: block.compaction.id,
        role: 'context_compression',
        content: '',
        contextCompression: {
          id: block.compaction.id,
          compressing: false,
          estimated_tokens_before: block.compaction.estimated_tokens_before,
          estimated_tokens_after: block.compaction.estimated_tokens_after,
          estimated_cleared_tokens: block.compaction.estimated_cleared_tokens,
          compressed_messages: block.compaction.compressed_messages,
          timestamp: block.compaction.timestamp,
        },
      })
      assistant.segments = []
      assistant.thinkingSegments = []
      assistant.toolCalls = []
      continue
    }
    if (block.type === 'tool') {
      assistant.toolCalls = [...(assistant.toolCalls || []), block.toolCall]
      assistant.segments = [...(assistant.segments || []), { type: 'toolCall', toolCallId: block.toolCall.tool_call_id }]
      continue
    }
    if (block.thinking) {
      const thinkingSegmentId = `${block.key}-thinking`
      assistant.thinkingSegments = [
        ...(assistant.thinkingSegments || []),
        { id: thinkingSegmentId, content: block.thinking.content, toolCallCount: 0 },
      ]
      assistant.segments = [...(assistant.segments || []), { type: 'thinkingSegment', thinkingSegmentId }]
    }
    if (block.content) {
      assistant.segments = [...(assistant.segments || []), { type: 'text', content: block.content }]
    }
  }

  // 运行中即使还没有正文/工具段也要保留助手占位：状态行（工作中动画/已运行时长）要可见
  if (assistant.segments?.length || parentRunning) messages.push(assistant)
  return messages
}

/** 子智能体过程区是否有可渲染内容。 */
export const hasSubagentTrail = (toolCall: ToolCallInfo): boolean => (
  toolCall.tool_name === 'task' && Boolean(toolCall.subagentTrail?.length)
)
