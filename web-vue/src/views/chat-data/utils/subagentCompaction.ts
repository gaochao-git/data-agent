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

/** 把压缩记录合进 task 调用：列表去重 + 过程 trail 插入分隔卡；无变化返回 null。 */
export const applySubagentCompactionToTool = (
  tool: ToolCallInfo,
  record: SubagentCompaction,
): ToolCallInfo | null => {
  const next = appendSubagentCompaction(tool.subagentCompactions, record)
  if (!next) return null
  const trailItem: SubagentTrailItem = {
    kind: 'compaction',
    id: record.id,
    timestamp: record.timestamp,
    estimated_tokens_before: record.estimated_tokens_before,
    estimated_tokens_after: record.estimated_tokens_after,
    estimated_cleared_tokens: record.estimated_cleared_tokens,
    compressed_messages: record.compressed_messages,
  }
  return {
    ...tool,
    subagentCompactions: next,
    subagentTrail: [...(tool.subagentTrail || []), trailItem],
  }
}
