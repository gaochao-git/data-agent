import type { MessageGenerationStats } from '../types'

/**
 * 助手气泡生成统计：后端服务端口径，finish 时随 message_end 下发并随 AI 消息持久化，
 * 本轮与历史回放共用同一份数据（toGenerationStats 校验转换）。字段缺失/非法时整体
 * 不展示，不用估算冒充精确值。生成中的实时速率仍由前端估算（computeLiveGenerationSpeed）。
 */
export const toGenerationStats = (raw: unknown): MessageGenerationStats | null => {
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  const outputTokens = Math.floor(Number(record.output_tokens) || 0)
  const durationMs = Number(record.duration_ms)
  const tokensPerSecond = Number(record.tokens_per_second)
  const firstTokenMs = Number(record.first_token_ms)
  if (outputTokens <= 0 || !Number.isFinite(durationMs) || durationMs <= 0) return null
  if (!Number.isFinite(tokensPerSecond) || tokensPerSecond <= 0) return null
  return {
    outputTokens,
    durationMs,
    tokensPerSecond,
    firstTokenMs: Number.isFinite(firstTokenMs) && firstTokenMs > 0 ? firstTokenMs : undefined,
  }
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

/** 悬浮明细，如「首 token 2.1s · 输出 1.8k tokens · 生成 12.3s」；首 token 缺失时不追加。 */
export const formatGenerationStatsDetail = (stats: MessageGenerationStats): string => {
  const parts = [`输出 ${formatCompactTokens(stats.outputTokens)} tokens`, `生成 ${formatDurationMs(stats.durationMs)}`]
  if (typeof stats.firstTokenMs === 'number') parts.unshift(`首 token ${formatDurationMs(stats.firstTokenMs)}`)
  return parts.join(' · ')
}

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
  /** 后端 stream_stats 推送的累计参数字符数（本轮全部工具调用，ASCII 按 4 字符/token 折算） */
  argsChars?: number
  /** 工具执行起止（客户端时钟，与 ToolCallInfo 的 started_at_ms/ended_at_ms 同形）；仅有 started 表示仍在执行，用 nowMs 兜底 */
  toolSpansMs?: Array<{ started_at_ms?: number | null; ended_at_ms?: number | null }>
  /** 本轮发起时刻（客户端时钟） */
  runStartedAtMs: number
  /** 当前时刻（客户端时钟） */
  nowMs: number
}

/**
 * 生成中的实时速率：token 粗估 ÷ 已用模型时间（墙钟 − 工具执行时间）。
 * 分子 = 正文/思考的字符粗估 + 后端累计参数字符数（参数流式期间后端只缓存不下发内容，
 * 该计数是参数 token 的唯一实时来源）；工具执行中分子分母同时冻结，速率自然停走；
 * 开跑首秒估算噪声大，不足 1s 不出数。
 * toolSpansMs 可能含历史轮次的工具（toolCallsMap 仅在切换会话时清空）：
 * 本轮开始前已结束的一律忽略，执行中/晚于本轮开始的把起点夹到本轮起点。
 */
export const computeLiveGenerationSpeed = (input: LiveGenerationSpeedInput): number | null => {
  const argsChars = Number(input.argsChars) || 0
  const tokens = estimateTokensFromText(`${input.streamedText || ''}${input.thinkingText || ''}`)
    + Math.floor(Math.max(argsChars, 0) / 4)
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
