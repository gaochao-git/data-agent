import type { ChatDataTokenUsage, SubagentContextUsage, SubagentTokenUsage } from '../types'

const toNonNegativeNumber = (value: unknown): number | null => {
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : null
}

const toPositiveNumber = (value: unknown): number | null => {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : null
}

const percentageOf = (used: number, total: number): number | undefined => (
  total > 0 ? Math.round((used / total) * 1000) / 10 : undefined
)

/**
 * 统一子智能体上下文用量的读取口径。
 *
 * ``subagent_context_usage`` 是上下文快照（节点更新后实时推送、心跳落库），
 * 百分比和当前输入以它为准；``subagent_token_usage_detail`` 只补充明细字段。
 * 旧数据没有快照时才回退到 token detail，避免卡片和详情页各用一套优先级。
 */
export const resolveSubagentContextUsage = (
  snapshot?: SubagentContextUsage | null,
  detail?: ChatDataTokenUsage | null,
): ChatDataTokenUsage | null => {
  const snapshotUsed = toNonNegativeNumber(snapshot?.used_tokens)
  if (snapshotUsed !== null) {
    const detailWindow = toPositiveNumber(detail?.context_window)
    const threshold = toPositiveNumber(snapshot?.threshold_tokens)
      ?? toPositiveNumber(detail?.compression_trigger_tokens)
    const contextWindow = toPositiveNumber(snapshot?.context_window) ?? detailWindow
    const total = contextWindow ?? threshold ?? 0
    const percentage = toNonNegativeNumber(snapshot?.percent)
      ?? percentageOf(snapshotUsed, total)
      ?? toNonNegativeNumber(detail?.percentage)
      ?? undefined
    return {
      ...(detail || {}),
      used: snapshotUsed,
      total,
      percentage,
      current_input_tokens: snapshotUsed,
      ...(threshold !== null ? { compression_trigger_tokens: threshold } : {}),
      ...(contextWindow !== null ? { context_window: contextWindow } : {}),
      estimated: true,
    }
  }

  if (!detail) return null
  const used = toNonNegativeNumber(detail.used)
    ?? toNonNegativeNumber(detail.current_input_tokens)
  if (used === null) return null
  const total = toPositiveNumber(detail.total)
    ?? toPositiveNumber(detail.context_window)
    ?? toPositiveNumber(detail.compression_trigger_tokens)
  if (total === null) return null
  return {
    ...detail,
    used,
    total,
    percentage: toNonNegativeNumber(detail.percentage) ?? percentageOf(used, total),
    current_input_tokens: toNonNegativeNumber(detail.current_input_tokens) ?? used,
  }
}

/** token 数紧凑展示：1234 → 1.2k，1234567 → 1.2m（面板窄，不展示完整数字）。 */
export const formatTokenCount = (value?: number): string => {
  const count = Math.max(0, Math.floor(Number(value) || 0))
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}m`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`
  return String(count)
}

/**
 * 子智能体 token 用量摘要（billing 口径，主对话统计不含这部分）：
 * `tokens 输入 3.0k · 输出 300 · 缓存命中 33%`。
 */
export const buildSubagentTokenUsageText = (usage?: SubagentTokenUsage | null): string => {
  if (!usage) return ''
  const input = Math.max(0, Number(usage.input_tokens) || 0)
  const output = Math.max(0, Number(usage.output_tokens) || 0)
  const cacheRead = Math.max(0, Number(usage.cache_read_input_tokens) || 0)
  const parts: string[] = []
  if (input > 0) parts.push(`输入 ${formatTokenCount(input)}`)
  if (output > 0) parts.push(`输出 ${formatTokenCount(output)}`)
  if (cacheRead > 0 && input > 0) parts.push(`缓存命中 ${Math.round((cacheRead / input) * 100)}%`)
  return parts.length > 0 ? `tokens ${parts.join(' · ')}` : ''
}
