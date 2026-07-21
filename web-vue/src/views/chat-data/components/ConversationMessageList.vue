<script setup lang="ts">
import { ref, watch } from 'vue'
import MarkdownIt from 'markdown-it'
import { ElMessage } from 'element-plus'
import type { ExecutionTargetOption, Message, MessageSegment, ToolCallInfo } from '../types'
import { extractGeneratedSqlFromToolCall } from '../utils/semanticTool'
import { formatDateTimeValue } from '../utils'
import AssistantMessageFooter from './AssistantMessageFooter.vue'
import QueryResultPanel from './QueryResultPanel.vue'
import ToolCallList from './ToolCallList.vue'

interface Props {
  messages: Message[]
  datasourceId?: string
  executionTargets?: ExecutionTargetOption[]
  activeToolCallId?: string | null
  displayContent?: (content: string) => string
  resolveToolCall?: (message: Message, toolCallId: string) => ToolCallInfo | null
  shouldInlineRenderResult?: (toolCall: ToolCallInfo) => boolean
  onSave?: (sql: string, chartType: string, memberDatasourceId?: string, memberDatasourceIds?: string[]) => void
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
})

const emit = defineEmits<{
  selectTool: [toolCall: ToolCallInfo]
}>()

const markdown = new MarkdownIt({ breaks: true, html: false, linkify: true })
const collapsedThinkingSegmentKeys = ref<Set<string>>(new Set())

const getThinkingSegmentKey = (messageId: string, thinkingSegmentId: string) => `${messageId}:${thinkingSegmentId}`

const getThinkingSegment = (message: Message, thinkingSegmentId: string) => (
  message.thinkingSegments?.find(segment => segment.id === thinkingSegmentId) || null
)

const isThinkingSegmentCollapsed = (message: Message, thinkingSegmentId: string): boolean => (
  collapsedThinkingSegmentKeys.value.has(getThinkingSegmentKey(message.id, thinkingSegmentId))
)

const toggleThinkingSegment = (message: Message, thinkingSegmentId: string) => {
  if (!getThinkingSegment(message, thinkingSegmentId)) return
  const key = getThinkingSegmentKey(message.id, thinkingSegmentId)
  const next = new Set(collapsedThinkingSegmentKeys.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  collapsedThinkingSegmentKeys.value = next
}

const getActiveThinkingSegmentId = (message: Message): string | null => {
  if (!message.isStreaming) return null
  const segments = message.segments || []
  const lastSegment = segments[segments.length - 1]
  return lastSegment?.type === 'thinkingSegment' ? lastSegment.thinkingSegmentId : null
}

interface ThinkingMessageState {
  id: string
  activeThinkingSegmentId: string | null
  thinkingSegmentIds: string[]
}

const getThinkingMessageStates = (): ThinkingMessageState[] => (
  props.messages.map(message => ({
    id: message.id,
    activeThinkingSegmentId: getActiveThinkingSegmentId(message),
    thinkingSegmentIds: message.thinkingSegments?.map(segment => segment.id) || [],
  }))
)

watch(
  getThinkingMessageStates,
  (currentStates, previousStates = []) => {
    const nextCollapsedKeys = new Set(collapsedThinkingSegmentKeys.value)
    const previousStateByMessageId = new Map(previousStates.map(state => [state.id, state]))

    for (const currentState of currentStates) {
      const previousState = previousStateByMessageId.get(currentState.id)
      if (!previousState) {
        for (const thinkingSegmentId of currentState.thinkingSegmentIds) {
          if (thinkingSegmentId !== currentState.activeThinkingSegmentId) {
            nextCollapsedKeys.add(getThinkingSegmentKey(currentState.id, thinkingSegmentId))
          }
        }
      } else if (
        previousState.activeThinkingSegmentId
        && previousState.activeThinkingSegmentId !== currentState.activeThinkingSegmentId
      ) {
        nextCollapsedKeys.add(getThinkingSegmentKey(currentState.id, previousState.activeThinkingSegmentId))
      }

      if (currentState.activeThinkingSegmentId) {
        nextCollapsedKeys.delete(getThinkingSegmentKey(currentState.id, currentState.activeThinkingSegmentId))
      }
    }
    collapsedThinkingSegmentKeys.value = nextCollapsedKeys
  },
  { immediate: true },
)

const resolveToolCall = (message: Message, toolCallId: string): ToolCallInfo | null => (
  props.resolveToolCall(message, toolCallId)
)

const getRenderableQueryResultToolCall = (message: Message, toolCallId: string): ToolCallInfo | null => {
  const toolCall = resolveToolCall(message, toolCallId)
  return toolCall && props.shouldInlineRenderResult(toolCall) ? toolCall : null
}

const getDisplayContent = (content: string): string => props.displayContent(content)

const hasVisibleAssistantContent = (content: string): boolean => {
  const text = getDisplayContent(content)
  return Boolean(text && text !== '正在思考...')
}

const shouldShowStreamingStatus = (message: Message): boolean => (
  hasVisibleAssistantContent(message.content)
  || Boolean(message.thinkingSegments?.length)
  || Boolean(message.toolCalls?.length)
  || Boolean(message.semanticConfigSelection)
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
  <div class="chat-message-list mx-auto flex w-[90%] max-w-[774px] flex-col gap-5">
    <div v-for="message in messages" :key="message.id" class="flex" :class="message.role === 'user' ? 'justify-end' : 'justify-start'">
      <div :class="message.role === 'user' ? 'flex max-w-[80%] flex-col items-end gap-1.5' : 'w-full min-w-0'">
        <template v-if="message.role === 'user'">
          <div class="rounded-2xl rounded-br-md bg-[#1677FF]/10 px-4 py-2.5 text-sm leading-relaxed text-[#1F3A56] shadow-[0_2px_8px_rgba(59,130,246,0.12)]">
            <div class="whitespace-pre-wrap">{{ message.content }}</div>
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
              @select-tool="emit('selectTool', $event)"
            />
          </div>
          <div v-if="formatDateTimeValue(message.timestamp)" class="px-1 text-right text-[10px] text-gray-400">
            {{ formatDateTimeValue(message.timestamp) }}
          </div>
        </template>

        <template v-else>
          <div class="min-w-0 rounded-2xl rounded-bl-md border border-black/[0.06] bg-white p-4 shadow-[0_2px_8px_rgba(0,0,0,0.04)]">
            <div v-if="message.segments?.length" class="flex min-w-0 flex-col gap-3">
              <template v-for="(segment, index) in message.segments" :key="`${segment.type}-${index}`">
                <div
                  v-if="segment.type === 'text' && getDisplayContent(segment.content)"
                  class="conversation-markdown min-w-0 text-sm leading-relaxed text-slate-700"
                  v-html="markdown.render(getDisplayContent(segment.content))"
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
                    <i class="fa-solid fa-brain text-xs text-slate-400 transition-colors duration-200 group-hover:text-slate-600"></i>
                    <span>思考</span>
                    <i
                      :class="[
                        'fa-solid fa-chevron-right text-[10px] text-slate-400 transition-all duration-200 group-hover:text-slate-600',
                        !isThinkingSegmentCollapsed(message, segment.thinkingSegmentId)
                          ? 'rotate-90 opacity-100'
                          : 'opacity-0 group-hover:opacity-100',
                      ]"
                    ></i>
                  </button>
                  <div
                    v-if="!isThinkingSegmentCollapsed(message, segment.thinkingSegmentId)"
                    class="mb-1.5 max-h-[210px] overflow-y-auto whitespace-pre-wrap rounded-md bg-slate-50 px-3 py-2 text-sm leading-relaxed text-slate-400"
                  >
                    {{ getThinkingSegment(message, segment.thinkingSegmentId)!.content }}
                  </div>
                </div>

                <div v-else-if="segment.type === 'toolCall' && resolveToolCall(message, segment.toolCallId)" class="min-w-0">
                  <ToolCallList
                    :tool-calls="[resolveToolCall(message, segment.toolCallId)!]"
                    :datasource-id="datasourceId"
                    :execution-targets="executionTargets"
                    compact
                    :active-tool-call-id="activeToolCallId"
                    @select-tool="emit('selectTool', $event)"
                  />
                  <QueryResultPanel
                    v-if="getRenderableQueryResultToolCall(message, segment.toolCallId)"
                    class="mt-2 max-h-[46vh]"
                    :result-data="getRenderableQueryResultToolCall(message, segment.toolCallId)!.renderId!"
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
              v-html="markdown.render(getDisplayContent(message.content))"
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

          <div v-if="!message.isStreaming && shouldShowMessageCopyButton(message)" class="mt-1 px-0">
            <AssistantMessageFooter :timestamp="formatDateTimeValue(message.timestamp)">
              <button
                type="button"
                class="inline-flex h-[26px] min-w-[58px] shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md bg-transparent px-2 text-[11px] text-gray-500 transition-all duration-200 hover:bg-blue-500/5 hover:text-blue-500"
                @click="handleCopyAssistantMessage(message)"
              >
                <i class="fa-regular fa-copy text-[11px]"></i>
                <span>复制</span>
              </button>
            </AssistantMessageFooter>
          </div>

          <div v-if="message.isStreaming && shouldShowStreamingStatus(message)" class="mt-2 ml-3 flex items-center gap-2 text-xs text-slate-400">
            <i class="fa-solid fa-spinner fa-spin text-[11px]"></i>
            <span>{{ hasVisibleAssistantContent(message.content) ? '正在生成' : '正在思考' }}</span>
            <span class="h-1.5 w-1.5 rounded-full bg-[#1677FF] animate-pulse"></span>
            <span class="h-1.5 w-1.5 rounded-full bg-[#1677FF] animate-pulse [animation-delay:120ms]"></span>
            <span class="h-1.5 w-1.5 rounded-full bg-[#1677FF] animate-pulse [animation-delay:240ms]"></span>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.conversation-markdown {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.conversation-markdown :deep(p) {
  margin: 0 0 8px;
}

.conversation-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.conversation-markdown :deep(strong) {
  font-weight: 700;
}

.conversation-markdown :deep(:not(pre) > code) {
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(15, 23, 42, 0.06);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.conversation-markdown :deep(pre) {
  max-width: 100%;
  margin: 8px 0;
  padding: 12px;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  white-space: pre;
}

.conversation-markdown :deep(pre code) {
  display: block;
  min-width: max-content;
  padding: 0;
  background: transparent;
  white-space: pre;
  overflow-wrap: normal;
  word-break: normal;
  font-size: 12px;
  line-height: 1.6;
  color: #334155;
}

.conversation-markdown :deep(table) {
  width: 100%;
  max-width: 100%;
  margin: 10px 0;
  border-collapse: collapse;
  table-layout: fixed;
}

.conversation-markdown :deep(th),
.conversation-markdown :deep(td) {
  border: 1px solid #d8dee8;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.conversation-markdown :deep(th) {
  background: #f8fafc;
  color: #334155;
  font-weight: 700;
}

.conversation-markdown :deep(ul),
.conversation-markdown :deep(ol) {
  margin: 4px 0;
  padding-left: 18px;
}
</style>
