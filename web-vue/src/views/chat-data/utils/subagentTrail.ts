import type { SubagentTrailItem, ToolLifecycleEventData } from '../types'

const isToolEvent = (event: ToolLifecycleEventData): boolean =>
  event.type === 'tool_started'
  || event.type === 'tool_progress'
  || event.type === 'tool_completed'
  || event.type === 'tool_failed'

/** 把一条子智能体过程事件归并进 trail；返回新数组，未消费的事件原样返回。 */
export const applySubagentTrailEvent = (
  trail: SubagentTrailItem[],
  event: ToolLifecycleEventData,
): SubagentTrailItem[] => {
  if (event.type === 'subagent_message_delta' || event.type === 'subagent_thinking_delta') {
    const entryId = event.message_id
    const delta = event.delta || ''
    if (!entryId || !delta) return trail
    const isText = event.type === 'subagent_message_delta'
    const next = [...trail]
    const idx = next.findIndex(item => item.id === entryId && item.kind === 'message')
    if (idx >= 0) {
      const prev = next[idx]
      if (!prev) return trail
      next[idx] = isText
        ? { ...prev, content: (prev.content || '') + delta, truncated: Boolean(event.truncated) || Boolean(prev.truncated) }
        : { ...prev, thinking: (prev.thinking || '') + delta, thinkingTruncated: Boolean(event.truncated) || Boolean(prev.thinkingTruncated) }
    } else {
      next.push(isText
        ? { kind: 'message', id: entryId, content: delta, truncated: Boolean(event.truncated) }
        : { kind: 'message', id: entryId, thinking: delta, thinkingTruncated: Boolean(event.truncated) })
    }
    return next
  }

  if (event.type === 'subagent_message') {
    const entryId = event.message_id
    if (!entryId) return trail
    const content = event.content || ''
    const next = [...trail]
    const idx = next.findIndex(item => item.id === entryId && item.kind === 'message')
    if (idx >= 0) {
      const prev = next[idx]
      if (!prev) return trail
      // 收敛策略「只补标记不覆盖」：增量已拼出全文时，完整消息只补截断标记；
      // 未走增量（如后端降级路径）才写入完整内容。
      next[idx] = event.streamed
        ? { ...prev, truncated: Boolean(event.truncated) || Boolean(prev.truncated) }
        : { ...prev, content: content || prev.content || '', truncated: Boolean(event.truncated) }
    } else {
      next.push({ kind: 'message', id: entryId, content, truncated: Boolean(event.truncated) })
    }
    return next
  }

  if (!isToolEvent(event)) return trail
  const entryId = event.tool_call_id
  if (!entryId) return trail
  const entry: SubagentTrailItem = {
    kind: 'tool',
    id: entryId,
    tool_name: event.tool_name || 'tool_call',
    args: { ...(event.args || {}) },
    result: event.content || event.result,
    status: event.type === 'tool_failed' ? 'error' : event.type === 'tool_completed' ? 'completed' : 'running',
    started_at_ms: event.started_at_ms ?? null,
    ended_at_ms: event.ended_at_ms ?? null,
  }
  const next = [...trail]
  const idx = next.findIndex(item => item.id === entryId)
  if (idx >= 0) {
    const prev = next[idx]
    if (!prev) return trail
    next[idx] = {
      ...prev,
      ...entry,
      args: { ...(prev.args || {}), ...(entry.args || {}) },
      // tool_started 事件不带计时，完成事件才补齐：缺省时保留已有值
      started_at_ms: entry.started_at_ms ?? prev.started_at_ms ?? null,
      ended_at_ms: entry.ended_at_ms ?? prev.ended_at_ms ?? null,
    }
  } else {
    next.push(entry)
  }
  return next
}
