import type { SubagentCompaction, SubagentTrailItem, ToolCallInfo } from '../types'

/** 从 SSE/历史 payload 归一一条压缩记录；缺 id 丢弃。 */
export const buildSubagentCompactionRecord = (raw: {
  id?: string
  timestamp?: string
  estimated_tokens_before?: number
  estimated_tokens_after?: number
  estimated_cleared_tokens?: number
  compressed_messages?: number
}): SubagentCompaction | null => {
  const id = String(raw.id || '').trim()
  if (!id) return null
  return {
    id,
    timestamp: raw.timestamp,
    estimated_tokens_before: raw.estimated_tokens_before,
    estimated_tokens_after: raw.estimated_tokens_after,
    estimated_cleared_tokens: raw.estimated_cleared_tokens,
    compressed_messages: raw.compressed_messages,
  }
}

/** 追加压缩记录（按 id 去重）；已存在返回 null，便于调用方跳过写回。 */
export const appendSubagentCompaction = (
  current: SubagentCompaction[] | undefined,
  record: SubagentCompaction,
): SubagentCompaction[] | null => {
  const list = current || []
  if (list.some(item => item.id === record.id)) return null
  return [...list, record]
}

/** 清掉所有「压缩中」占位（任务收口 / 重复完成帧兜底）；占位只属于实时流。 */
export const clearSubagentCompactionPlaceholders = (
  trail: SubagentTrailItem[],
): SubagentTrailItem[] => trail.filter(item => !(item.kind === 'compaction' && item.compressing))

/** 用一条压缩条目替换掉此前所有「压缩中」占位（占位只属于实时流，不落历史）。 */
export const replaceSubagentCompactionPlaceholder = (
  trail: SubagentTrailItem[],
  item: SubagentTrailItem,
): SubagentTrailItem[] => [...clearSubagentCompactionPlaceholders(trail), item]

/** 从「压缩中」SSE 帧构造占位条目；缺 id 丢弃。 */
export const buildSubagentCompactionStartedTrail = (raw: {
  id?: string
  timestamp?: string
}): SubagentTrailItem | null => {
  const id = String(raw.id || '').trim()
  if (!id) return null
  return { kind: 'compaction', id, compressing: true, timestamp: raw.timestamp }
}

/** 把压缩记录映射成过程 trail 的分隔卡。 */
export const buildSubagentCompactionTrailItem = (record: SubagentCompaction): SubagentTrailItem => ({
  kind: 'compaction',
  id: record.id,
  timestamp: record.timestamp,
  estimated_tokens_before: record.estimated_tokens_before,
  estimated_tokens_after: record.estimated_tokens_after,
  estimated_cleared_tokens: record.estimated_cleared_tokens,
  compressed_messages: record.compressed_messages,
})

/** 把压缩记录合进 subagent 调用：列表去重 + 过程 trail 插入完成分隔卡；无变化返回 null。 */
export const applySubagentCompactionToTool = (
  tool: ToolCallInfo,
  record: SubagentCompaction,
): ToolCallInfo | null => {
  const cleaned = clearSubagentCompactionPlaceholders(tool.subagentTrail || [])
  const next = appendSubagentCompaction(tool.subagentCompactions, record)
  if (!next) {
    // 重连回放的重复完成帧：不重复计数，但仍要清掉可能残留的「压缩中」占位。
    if (cleaned.length === (tool.subagentTrail || []).length) return null
    return { ...tool, subagentTrail: cleaned }
  }
  return {
    ...tool,
    subagentCompactions: next,
    subagentTrail: [...cleaned, buildSubagentCompactionTrailItem(record)],
  }
}

/** 实时流收到「压缩中」：替换占位；不写 subagentCompactions（只记完成的压缩）。 */
export const applySubagentCompactionStartedToTool = (
  tool: ToolCallInfo,
  event: { id?: string; timestamp?: string },
): ToolCallInfo | null => {
  const item = buildSubagentCompactionStartedTrail(event)
  if (!item) return null
  return {
    ...tool,
    subagentTrail: replaceSubagentCompactionPlaceholder(tool.subagentTrail || [], item),
  }
}
