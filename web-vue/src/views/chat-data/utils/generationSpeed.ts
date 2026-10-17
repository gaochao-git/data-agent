import type { MessageGenerationStats } from '../types'

/**
 * 助手气泡生成统计：后端服务端口径，finish 时随 message_end 下发并随 AI 消息持久化，
 * 本轮与历史回放共用同一份数据（toGenerationStats 校验转换）。字段缺失/非法时整体
 * 不展示，不用估算冒充精确值。生成中的实时速率由后端滑窗采样推送（generation_speed 事件）。
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
