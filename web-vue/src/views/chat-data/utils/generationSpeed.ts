import type { MessageGenerationStats } from '../types'

/**
 * 助手气泡生成统计：后端服务端口径，finish 时随 message_end 下发并随 AI 消息持久化，
 * 本轮与历史回放共用同一份数据（toGenerationStats 校验转换）。解码统计（token/速率）
 * 与总耗时是两路独立产出：usage 缺失时后端仍会下发 total_duration_ms，total 合法即
 * 保留对象、解码字段逐个省略；两路全缺才整体不展示。不用估算冒充精确值。
 * 生成中的实时速率由后端滑窗采样推送（generation_speed 事件）。
 */
export const toGenerationStats = (raw: unknown): MessageGenerationStats | null => {
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  const outputTokens = Math.floor(Number(record.output_tokens) || 0)
  const durationMs = Number(record.duration_ms)
  const tokensPerSecond = Number(record.tokens_per_second)
  const firstTokenMs = Number(record.first_token_ms)
  const totalDurationMs = Number(record.total_duration_ms)
  const modelFirstTokenMs = Number(record.model_first_token_ms)
  const inputTokens = Math.floor(Number(record.input_tokens) || 0)
  const cachedInputTokens = Math.floor(Number(record.cached_input_tokens) || 0)
  const cacheHitRate = Number(record.cache_hit_rate)
  const model = typeof record.model === 'string' ? record.model.trim() : ''
  const hasDecodeStats =
    outputTokens > 0 &&
    Number.isFinite(durationMs) && durationMs > 0 &&
    Number.isFinite(tokensPerSecond) && tokensPerSecond > 0
  const hasTotalDuration = Number.isFinite(totalDurationMs) && totalDurationMs > 0
  if (!hasDecodeStats && !hasTotalDuration) return null
  return {
    outputTokens: outputTokens > 0 ? outputTokens : undefined,
    durationMs: hasDecodeStats ? durationMs : undefined,
    tokensPerSecond: hasDecodeStats ? tokensPerSecond : undefined,
    firstTokenMs: Number.isFinite(firstTokenMs) && firstTokenMs > 0 ? firstTokenMs : undefined,
    totalDurationMs: hasTotalDuration ? totalDurationMs : undefined,
    inputTokens: inputTokens > 0 ? inputTokens : undefined,
    cachedInputTokens: cachedInputTokens > 0 ? cachedInputTokens : undefined,
    cacheHitRate: Number.isFinite(cacheHitRate) && cacheHitRate > 0 ? cacheHitRate : undefined,
    model: model || undefined,
    modelFirstTokenMs:
      Number.isFinite(modelFirstTokenMs) && modelFirstTokenMs > 0 ? modelFirstTokenMs : undefined,
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

/** 气泡旁的短文案，如「146 tok/s」；解码统计缺失（usage 缺失场景）返回空串。 */
export const formatGenerationSpeed = (stats: MessageGenerationStats | null): string => {
  if (!stats || typeof stats.tokensPerSecond !== 'number') return ''
  if (stats.tokensPerSecond >= 10_000) return `${formatCompactTokens(stats.tokensPerSecond)} tok/s`
  return `${stats.tokensPerSecond} tok/s`
}

export interface GenerationStatsRow {
  label: string
  value: string
  /** 该行前渲染淡分隔线（token 组 → 耗时组） */
  divider?: boolean
}

/**
 * 悬浮明细行（分组键值对，展示组件负责排版）：模型名单独作标题行（模板渲染），
 * 此处输出 token 组（输入/输出/速率）与耗时组（llm 首 token / harness 首 token /
 * harness 总耗）。辅助字段缺失（老历史数据）时对应行省略，核心行始终输出。
 */
export const generationStatsRows = (stats: MessageGenerationStats | null): GenerationStatsRow[] => {
  if (!stats) return []
  const rows: GenerationStatsRow[] = []
  if (typeof stats.inputTokens === 'number') {
    rows.push({ label: '输入总token', value: formatCompactTokens(stats.inputTokens) })
  }
  if (typeof stats.cachedInputTokens === 'number' || typeof stats.cacheHitRate === 'number') {
    // 命中率优先；仅输入不可报导致命中率缺失的罕见场景退显 token 数
    const value =
      typeof stats.cacheHitRate === 'number' ? `${stats.cacheHitRate}%` : formatCompactTokens(stats.cachedInputTokens ?? 0)
    rows.push({ label: '缓存命中', value })
  }
  if (typeof stats.outputTokens === 'number' && typeof stats.tokensPerSecond === 'number') {
    rows.push(
      { label: '输出总token', value: formatCompactTokens(stats.outputTokens) },
      { label: 'token速率', value: formatGenerationSpeed(stats) },
    )
  }
  const durationRows: GenerationStatsRow[] = []
  if (typeof stats.modelFirstTokenMs === 'number') {
    durationRows.push({ label: 'llm 首 token', value: formatDurationMs(stats.modelFirstTokenMs) })
  }
  if (typeof stats.firstTokenMs === 'number') {
    durationRows.push({ label: 'harness 首 token', value: formatDurationMs(stats.firstTokenMs) })
  }
  // harness 总耗优先取完整墙钟（含点击发送 → 流结束），无 total 字段的老数据回退解码时长
  const totalWallMs = stats.totalDurationMs ?? stats.durationMs
  if (typeof totalWallMs === 'number') {
    durationRows.push({ label: 'harness 总耗', value: formatDurationMs(totalWallMs) })
  }
  if (durationRows[0]) durationRows[0].divider = true
  return [...rows, ...durationRows]
}

/** 执行耗时短文案（气泡下方「用时 X」与生成中实时递增共用）：不足 1 分钟显示整秒，超出后「X 分 Y 秒」。 */
export const formatTotalDurationLabel = (durationMs: number): string => {
  if (!Number.isFinite(durationMs) || durationMs < 0) return ''
  const totalSeconds = Math.max(Math.floor(durationMs / 1000), 1)
  if (totalSeconds < 60) return `${totalSeconds} 秒`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return seconds > 0 ? `${minutes} 分 ${seconds} 秒` : `${minutes} 分钟`
}
