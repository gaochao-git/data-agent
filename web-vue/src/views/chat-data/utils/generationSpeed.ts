import type { ChatDataTokenUsage, MessageGenerationStats } from '../types'

/**
 * 助手气泡生成速度统计（纯前端，message_end 收口时计算一次）。
 *
 * 口径：
 * - 分子 = usage.output_tokens：后端按每次模型调用返回的 usage_metadata 累加，run 级精确值；
 * - 分母 = 本轮墙钟时间 − 工具执行时间 ≈ 纯模型生成时间。工具执行时长取 breakdown 中
 *   role === 'tool' 行的 started/ended（tool_timing 中间件盖戳、随消息持久化），并按本轮
 *   时间窗过滤，避免把历史轮次的工具时间算进来；
 * - 已知偏差：模型调用之外的开销（checkpoint 写入、事件下发，通常几十毫秒级）计入分母，
 *   以及含 ask_user 等待的轮次会把等待时间计入分母——速度会被低估，不做美化；
 * - 任一关键数据缺失（模型未回 usage / 无结束时间）返回 null，前端不展示，不用估算冒充精确值。
 */
export interface GenerationStatsInput {
  /** 本轮发起时间（客户端时钟，Date.now()） */
  runStartedAtMs: number
  /** 本轮收口时间（客户端时钟，onMessageEnd 时刻） */
  runEndedAtMs: number
}

const SKEW_GUARD_MS = 2000

export const computeGenerationStats = (
  usage: ChatDataTokenUsage | null | undefined,
  timing: GenerationStatsInput,
): MessageGenerationStats | null => {
  const outputTokens = Math.floor(Number(usage?.output_tokens) || 0)
  if (outputTokens <= 0) return null

  const { runStartedAtMs, runEndedAtMs } = timing
  if (
    !Number.isFinite(runStartedAtMs) || !Number.isFinite(runEndedAtMs)
    || runEndedAtMs <= runStartedAtMs
  ) return null

  // 工具执行时间：breakdown 里 tool 行的精确起止，只统计落在本轮窗口内的行
  let toolDurationMs = 0
  for (const item of usage?.breakdown || []) {
    if (item.role !== 'tool') continue
    const { started_at_ms, ended_at_ms } = item
    if (
      typeof started_at_ms !== 'number' || !Number.isFinite(started_at_ms)
      || typeof ended_at_ms !== 'number' || !Number.isFinite(ended_at_ms)
      || ended_at_ms <= started_at_ms
    ) continue
    if (started_at_ms < runStartedAtMs - SKEW_GUARD_MS || ended_at_ms > runEndedAtMs + SKEW_GUARD_MS) continue
    toolDurationMs += ended_at_ms - started_at_ms
  }

  const durationMs = (runEndedAtMs - runStartedAtMs) - toolDurationMs
  if (durationMs <= 0) return null

  const tokensPerSecond = Math.round(outputTokens / (durationMs / 1000))
  if (!Number.isFinite(tokensPerSecond) || tokensPerSecond <= 0) return null

  return { outputTokens, durationMs, tokensPerSecond }
}

const formatCompactTokens = (value: number): string => {
  if (value >= 1_000_000) {
    const scaled = value / 1_000_000
    return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}m`
  }
  if (value >= 1000) {
    const scaled = value / 1000
    return `${scaled >= 100 ? scaled.toFixed(0) : scaled.toFixed(1).replace(/\.0$/, '')}k`
  }
  return String(value)
}

const formatDurationMs = (durationMs: number): string => {
  if (durationMs < 1000) return `${Math.round(durationMs)}ms`
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1).replace(/\.0$/, '')}s`
  const minutes = Math.floor(durationMs / 60_000)
  const seconds = Math.round((durationMs % 60_000) / 1000)
  return `${minutes}m${String(seconds).padStart(2, '0')}s`
}

/** 气泡旁的短文案，如「146 tok/s」。 */
export const formatGenerationSpeed = (stats: MessageGenerationStats): string => {
  if (stats.tokensPerSecond >= 10_000) return `${formatCompactTokens(stats.tokensPerSecond)} tok/s`
  return `${stats.tokensPerSecond} tok/s`
}

/** 悬浮明细，如「输出 1.8k tokens · 生成 12.3s」。 */
export const formatGenerationStatsDetail = (stats: MessageGenerationStats): string => (
  `输出 ${formatCompactTokens(stats.outputTokens)} tokens · 生成 ${formatDurationMs(stats.durationMs)}`
)

const CJK_CHAR_REGEX = /[\u3400-\u9FFF\uF900-\uFAFF\u3000-\u303F\uFF01-\uFF5E]/

/**
 * 流式文本的 token 粗估：CJK 字符按 1 token/字，其余非空白按 4 字符/token。
 * 仅用于生成中的实时展示（前端拿不到逐 token 计数），收口后被精确 usage 覆盖。
 */
export const estimateTokensFromText = (text: string): number => {
  let cjkCount = 0
  let otherCount = 0
  for (const char of text) {
    if (char.trim() === '') continue
    if (CJK_CHAR_REGEX.test(char)) cjkCount += 1
    else otherCount += 1
  }
  return cjkCount + Math.ceil(otherCount / 4)
}

export interface LiveGenerationSpeedInput {
  /** 本轮已流出的正文 */
  streamedText: string
  /** 本轮已流出的思考内容（同样是模型输出） */
  thinkingText?: string
  /** 工具执行起止（客户端时钟，与 ToolCallInfo 的 started_at_ms/ended_at_ms 同形）；仅有 started 表示仍在执行，用 nowMs 兜底 */
  toolSpansMs?: Array<{ started_at_ms?: number | null; ended_at_ms?: number | null }>
  /** 本轮发起时刻（客户端时钟） */
  runStartedAtMs: number
  /** 当前时刻（客户端时钟） */
  nowMs: number
}

/**
 * 生成中的实时速率：token 粗估 ÷ 已用模型时间（墙钟 − 工具执行时间）。
 * 工具执行中分子分母同时冻结，速率自然停走；开跑首秒估算噪声大，不足 1s 不出数。
 * toolSpansMs 可能含历史轮次的工具（toolCallsMap 仅在切换会话时清空）：
 * 本轮开始前已结束的一律忽略，执行中/晚于本轮开始的把起点夹到本轮起点。
 */
export const computeLiveGenerationSpeed = (input: LiveGenerationSpeedInput): number | null => {
  const tokens = estimateTokensFromText(`${input.streamedText || ''}${input.thinkingText || ''}`)
  if (tokens <= 0) return null

  const { runStartedAtMs, nowMs } = input
  if (!Number.isFinite(runStartedAtMs) || !Number.isFinite(nowMs) || nowMs <= runStartedAtMs) return null

  let toolMs = 0
  for (const span of input.toolSpansMs || []) {
    const rawStartedAtMs = span?.started_at_ms
    if (typeof rawStartedAtMs !== 'number' || !Number.isFinite(rawStartedAtMs)) continue
    const endedAtMs = typeof span.ended_at_ms === 'number' && Number.isFinite(span.ended_at_ms)
      ? span.ended_at_ms
      : nowMs
    if (endedAtMs <= runStartedAtMs) continue
    const startedAtMs = Math.max(rawStartedAtMs, runStartedAtMs)
    if (endedAtMs <= startedAtMs) continue
    toolMs += Math.min(endedAtMs, nowMs) - startedAtMs
  }

  const modelMs = (nowMs - runStartedAtMs) - toolMs
  if (modelMs < 1000) return null

  const tokensPerSecond = Math.round(tokens / (modelMs / 1000))
  if (!Number.isFinite(tokensPerSecond) || tokensPerSecond <= 0) return null
  return tokensPerSecond
}
