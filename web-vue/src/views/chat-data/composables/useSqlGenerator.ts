/**
 * SQL 生成器 Composable
 * 处理自然语言到 SQL 的转换，支持 SSE 流式响应
 */

import { ref } from 'vue'
import { streamCompletion } from '../api'
import { parseSseChunk } from '../utils/sse'
import { isAbortError } from '../utils/abort'

/**
 * Run 终态：服务端结构化 done 事件的 status 字段。
 * legacy = 旧协议纯文本 [DONE]（只有流结束信号，无业务状态，不可推断成功）。
 */
export type RunDoneStatus = 'finished' | 'failed' | 'cancelled' | 'waiting_user' | 'legacy'
const STRUCTURED_DONE_STATUSES = new Set<RunDoneStatus>([
  'finished',
  'failed',
  'cancelled',
  'waiting_user',
])

import type {
  NL2SQLResult,
  LangGraphMessage,
  SSEEventType,
  DashboardSpecPayload,
  ChatDataTokenUsage,
  ChatDataContextCompressionEvent,
  ChatDataMessageEndPayload,
  ToolLifecycleEventData,
  PendingUserQuestion,
} from '../types'

/**
 * 工具生命周期事件：顶层事件（无 parent_tool_call_id）的 tool_call_id 收进
 * lifecycleToolCallIds，用于在顶层 `tool` 事件里去重（同一工具的完整快照已由
 * 生命周期事件先行分发）。子智能体内层事件走 trail 渲染，与顶层去重无关。
 */
const TOOL_LIFECYCLE_EVENTS = new Set<SSEEventType>([
  'tool_started',
  'tool_progress',
  'tool_completed',
  'tool_failed',
  'subagent_message',
  'subagent_message_delta',
  'subagent_thinking_delta',
  'subagent_context_usage',
])

/**
 * Set.has() 不触发类型窄化，这里用 type-guard 把「集合内容恰好是
 * ToolLifecycleEventData['type'] 全集」这一不变量写进类型（块内 event
 * 随即窄化为那 8 个字面量）。
 */
const isToolLifecycleEvent = (e: SSEEventType): e is ToolLifecycleEventData['type'] =>
  TOOL_LIFECYCLE_EVENTS.has(e)

/**
 * 解析 SSE 事件流:分帧与字段级解析收敛到共享 parseSseChunk(唯一实现),
 * 这里只保留本模块的事件类型映射(SSEEventType)与"无 data 不分发"的既有约束。
 * 行为差异:多行 data 由旧实现的"最后一行覆盖"改为规范的 "\n" 连接,
 * 不再静默丢行;本接口 data 均为单行 JSON,正常链路不受影响。
 */
function parseSSEEvents(buffer: string): {
  events: Array<{ event: SSEEventType; data: string; id?: string }>
  remaining: string
} {
  const { events, rest } = parseSseChunk(buffer)
  return {
    // 未携带 event: 行时按旧实现的默认事件类型 message 处理
    events: events
      .filter(item => Boolean(item.data))
      .map(item => ({
        event: (item.event === undefined ? 'message' : item.event) as SSEEventType,
        data: item.data,
        id: item.id,
      })),
    remaining: rest,
  }
}

export function useSqlGenerator() {
  const loading = ref(false)
  const error = ref<string | null>(null)
  let abortController: AbortController | null = null

  /**
   * 停止当前生成
   */
  const stopGeneration = () => {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
  }

  /**
   * 流式生成 SQL（SSE）。
   *
   * 仅 4 个请求主体参数走位置传参；其余回调与可选项统一收进 options，
   * 避免位置参数膨胀导致插参错位。
   */
  const generateSqlStream = async (
    query: string,
    returnMetadata: boolean,
    datasourceId: string | null,
    conversationId: string | null | undefined,
    options: {
      mode?: 'generate' | 'query'
      onMessage?: (message: LangGraphMessage) => void
      onResult?: (result: NL2SQLResult) => void
      onStatus?: (status: string, message: string) => void
      onError?: (error: string) => void
      onRunningTool?: (toolInfo: { tool_name: string; tool_call_id: string; args?: Record<string, unknown> }) => void
      modelId?: string | null
      memberDatasourceIds?: string[]
      agentId?: string
      skillNames?: string[] | null
      onDashboardSpec?: (payload: DashboardSpecPayload) => void
      onMessageEnd?: (payload: ChatDataMessageEndPayload) => void
      onTokenUsage?: (payload: ChatDataTokenUsage) => void
      /** 后端滑窗口径推送的当前生成速率（generation_speed 事件）；窗口静默不推送，前端保持上次值 */
      onGenerationSpeed?: (tokensPerSecond: number) => void
      fileSelections?: string[]
      enableThinking?: boolean | null
      thinkingEffort?: string | null
      runtimeOptions?: { workspace_backend: 'local' | 'sandbox'; bash_enabled: boolean } | null
      onContextCompression?: (payload: ChatDataContextCompressionEvent) => void
      fileIds?: string[]
      queuedMessageId?: string | null
      preserveQueuedMessages?: boolean
      onToolLifecycle?: (payload: ToolLifecycleEventData) => void
      onInboxAdmitted?: (messageIds: string[]) => void
      onQuestionRequested?: (question: PendingUserQuestion) => void
      onRunStarted?: (runId: string) => void
      responseFactory?: (signal: AbortSignal) => Promise<Response>
      onRunEvent?: (eventId: string) => void
      onFirstEvent?: () => void
      onRunRestarted?: () => void | Promise<void>
      onRunCompleted?: (info: { status: RunDoneStatus; nextRunId?: string | null }) => void
    } = {},
  ): Promise<void> => {
    const {
      mode = 'query',
      onMessage,
      onResult,
      onStatus,
      onError,
      onRunningTool,
      modelId,
      memberDatasourceIds,
      agentId = 'chat_data',
      skillNames,
      onDashboardSpec,
      onMessageEnd,
      onTokenUsage,
      onGenerationSpeed,
      fileSelections,
      enableThinking,
      thinkingEffort,
      runtimeOptions,
      onContextCompression,
      fileIds,
      queuedMessageId,
      preserveQueuedMessages,
      onToolLifecycle,
      onInboxAdmitted,
      onQuestionRequested,
      onRunStarted,
      responseFactory,
      onRunEvent,
      onFirstEvent,
      onRunRestarted,
      onRunCompleted,
    } = options
    if (!conversationId) {
      onError?.('会话 ID 不能为空')
      return
    }

    loading.value = true
    error.value = null
    const currentController = new AbortController()
    abortController = currentController

    try {
      // 使用与 React 版本一致的请求结构
      const options: Record<string, unknown> = {
        datasource_id: datasourceId,
        mode,  // generate: 仅生成SQL, query: 生成并执行
      }
      // 三态：仅用户显式选择时携带；未触碰（null）交给服务端按注册表 default_on 落点
      if (enableThinking === true || enableThinking === false) {
        options.enable_thinking = enableThinking
      }
      // 档位：用户显式选择时携带，非法值由后端回退 default_effort
      if (thinkingEffort) {
        options.thinking_effort = thinkingEffort
      }

      // 仅当服务端允许用户覆盖时，runtime_options 才会改变实际运行配置。
      if (runtimeOptions && typeof runtimeOptions === 'object') {
        options.runtime_options = {
          workspace_backend: runtimeOptions.workspace_backend,
          bash_enabled: runtimeOptions.bash_enabled,
        }
      }

      // 模型选择
      if (modelId) {
        options.model_id = modelId
      }

      // @ 引用工作区文件（相对路径列表）：白名单校验与 Human 消息短句由服务端完成
      if (fileSelections?.length) {
        options.file_selections = fileSelections
      }

      if (memberDatasourceIds && memberDatasourceIds.length > 0) {
        options.member_datasource_ids = memberDatasourceIds
      }
      if (skillNames?.length) {
        options.skill_names = skillNames
      }

      const response = responseFactory ? await responseFactory(currentController.signal) : await streamCompletion(conversationId, {
        query,
        agent_id: agentId,
        options,
        file_ids: fileIds,
        queued_message_id: queuedMessageId || undefined,
        preserve_queued_messages: preserveQueuedMessages || undefined,
        // 用户点击发送的客户端时刻（epoch 毫秒）：harness 首 token/总耗时与用户
        // 气泡时间同锚「点击时刻」，服务端校验钟偏后覆盖接收章，非法回退。
        client_timestamp_ms: Date.now(),
      }, currentController.signal)

      if (!response.ok) {
        // 尝试解析错误响应以获取更详细的错误信息
        let errorMsg = `HTTP ${response.status}`
        try {
          const errorData = await response.json()
          errorMsg = errorData.message || errorData.msg || errorData.detail || errorMsg
        } catch {
          // 忽略解析错误
        }
        throw new Error(errorMsg)
      }

      const responseRunId = response.headers.get('X-ChatData-Run-Id')
      if (responseRunId) onRunStarted?.(responseRunId)

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('无法获取响应流')
      }

      const decoder = new TextDecoder()
      let buffer = ''
      let streamEnded = false
      let interrupted = false
      let firstEventHandled = false
      const lifecycleToolCallIds = new Set<string>()
      // error 事实先于终态到达（failed = error + done）：只记录不中断，
      // 继续读取直到结构化 done，避免提前退出丢失 onRunCompleted 终态通知。
      let streamErrorMsg: string | null = null
      let doneStatus: RunDoneStatus | null = null

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const { events, remaining } = parseSSEEvents(buffer)
        buffer = remaining

        for (const { event, data: dataStr, id } of events) {
          if (!firstEventHandled) {
            firstEventHandled = true
            onFirstEvent?.()
          }
          if (id) onRunEvent?.(id)
          // 处理结束事件：结构化 done 携带 Run 终态（status / next_run_id）。
          if (event === 'done' || dataStr === '[DONE]') {
            let status: RunDoneStatus = 'legacy'
            let nextRunId: string | null = null
            if (dataStr !== '[DONE]') {
              try {
                const parsed = JSON.parse(dataStr)
                if (parsed && STRUCTURED_DONE_STATUSES.has(parsed.status as RunDoneStatus)) {
                  status = parsed.status as RunDoneStatus
                }
                if (parsed && typeof parsed.next_run_id === 'string' && parsed.next_run_id) {
                  nextRunId = parsed.next_run_id
                }
              } catch {
                // 非法 JSON，按旧协议处理（只代表流结束）。
              }
            }
            if (status !== 'legacy') onRunCompleted?.({ status, nextRunId })
            doneStatus = status
            streamEnded = true
            break
          }

          // 处理错误事件：记录后继续读取（done 随后到达，见 done 分支）
          if (event === 'error') {
            let errorMsg = dataStr
            try {
              const errorData = JSON.parse(dataStr)
              errorMsg = errorData.error || errorData.message || errorMsg
            } catch {
              // 保留原始文本。
            }
            streamErrorMsg = errorMsg
            continue
          }

          if (event === 'message_end') {
            try {
              const data = JSON.parse(dataStr)
              const usage = data?.usage
              onMessageEnd?.({
                usage: usage && typeof usage === 'object' ? usage as ChatDataTokenUsage : null,
                timestamp: typeof data?.timestamp === 'string' ? data.timestamp : undefined,
                generation_stats: data?.generation_stats,
              })
            } catch (err) {
              console.error('解析 message_end 数据失败:', err, dataStr)
              onMessageEnd?.({})
            }
            continue
          }

          if (event === 'token_usage') {
            try {
              const data = JSON.parse(dataStr)
              if (data && typeof data === 'object') {
                onTokenUsage?.(data as ChatDataTokenUsage)
              }
            } catch (err) {
              console.error('解析 token_usage 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'generation_speed') {
            try {
              const data = JSON.parse(dataStr)
              if (data && typeof data.tokens_per_second === 'number' && data.tokens_per_second > 0) {
                onGenerationSpeed?.(data.tokens_per_second)
              }
            } catch (err) {
              console.error('解析 generation_speed 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'context_compression') {
            try {
              const data = JSON.parse(dataStr)
              if (data && typeof data === 'object' && typeof data.id === 'string') {
                onContextCompression?.(data as ChatDataContextCompressionEvent)
              }
            } catch (err) {
              console.error('解析 context_compression 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'inbox_admitted') {
            try {
              const data = JSON.parse(dataStr)
              const messageIds = Array.isArray(data?.message_ids)
                ? data.message_ids.filter((id: unknown): id is string => typeof id === 'string' && Boolean(id))
                : []
              if (messageIds.length) onInboxAdmitted?.(messageIds)
            } catch (err) {
              console.error('解析 inbox_admitted 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'question_requested') {
            try {
              const data = JSON.parse(dataStr)
              if (data && typeof data.question_id === 'string' && Array.isArray(data.questions)) {
                interrupted = true
                onQuestionRequested?.(data as PendingUserQuestion)
              }
            } catch (err) {
              console.error('解析 question_requested 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'run_restarted') {
            await onRunRestarted?.()
            continue
          }

          if (event === 'thinking') {
            try {
              const data = JSON.parse(dataStr)
              const content = typeof data?.content === 'string' ? data.content : ''
              if (content) {
                onMessage?.({
                  type: 'thinking',
                  content,
                  timestamp: data?.timestamp,
                  additional_kwargs: { reasoning_content: content },
                } as LangGraphMessage)
              }
            } catch (err) {
              console.error('解析 thinking 数据失败:', err, dataStr)
            }
            continue
          }

          if (isToolLifecycleEvent(event)) {
            try {
              const data = JSON.parse(dataStr) as ToolLifecycleEventData
              // 只收顶层 id：内层 id（带 parent_tool_call_id）永不出现在顶层
              // `tool` 事件里，收进集合没有意义，只会污染去重语义。
              if (data.tool_call_id && !data.parent_tool_call_id) lifecycleToolCallIds.add(data.tool_call_id)
              onToolLifecycle?.({ ...data, type: event })
            } catch (err) {
              console.error(`解析 ${event} 数据失败:`, err, dataStr)
            }
            continue
          }

          // 处理 tool 事件（根据 status 区分）
          if (event === 'tool') {
            try {
              const data = JSON.parse(dataStr)
              if (data.tool_call_id && lifecycleToolCallIds.has(data.tool_call_id)) {
                continue
              }
              // 根据 status 字段分发处理
              if (data.status === 'running') {
                // 工具开始执行
                onRunningTool?.({
                  tool_name: data.tool_name,
                  tool_call_id: data.tool_call_id || '',
                  args: data.args || {},
                })
              } else {
                // completed/success 状态仍然是工具结果，显式标记为 tool，避免进入助手正文。
                onMessage?.({ ...data, type: 'tool' } as LangGraphMessage)
              }
            } catch (err) {
              console.error('解析 tool 数据失败:', err, dataStr)
            }
            continue
          }

          // 处理 message 事件（错误事实统一走 event: error，此处不再拦截 data.error）
          if (event === 'message') {
            try {
              const data = JSON.parse(dataStr)
              onMessage?.(data as LangGraphMessage)
            } catch (err) {
              console.error('解析 SSE 数据失败:', err, dataStr)
            }
            continue
          }

          if (event === 'dashboard_spec') {
            try {
              const data = JSON.parse(dataStr)
              onDashboardSpec?.(data as DashboardSpecPayload)
            } catch (err) {
              console.error('解析 dashboard_spec 数据失败:', err, dataStr)
            }
          }
        }

        if (streamEnded) break
      }

      if (!streamEnded) {
        // 流意外中断：优先呈现已记录的业务错误，其次才是连接中断提示。
        throw new Error(streamErrorMsg || '流式连接意外中断，请重新进入会话继续接收')
      }

      // 失败终态：onRunCompleted 已先解锁停止流程，这里再把错误抛给失败回调。
      if (streamErrorMsg) {
        throw new Error(streamErrorMsg)
      }

      // 只有明确成功才触发成功回调；cancelled/failed/waiting_user/legacy 各走自己的状态处理。
      if (doneStatus === 'finished' && !interrupted) onResult?.({} as NL2SQLResult)
    } catch (err) {
      // 用户主动取消不算错误,也不应误报为成功
      if (isAbortError(err)) {
        return
      }
      const errorMsg = err instanceof Error ? err.message : '请求失败'
      error.value = errorMsg
      onError?.(errorMsg)
    } finally {
      loading.value = false
      // 只清理自己的 controller，避免覆盖新请求创建的 controller
      if (abortController === currentController) {
        abortController = null
      }
    }
  }

  return {
    loading,
    error,
    generateSqlStream,
    stopGeneration,
  }
}
