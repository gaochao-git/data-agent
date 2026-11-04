import type { SubagentTokenUsage } from '../types'

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
