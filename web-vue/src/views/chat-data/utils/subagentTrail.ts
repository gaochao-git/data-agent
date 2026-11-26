import type { SubagentTrailItem, ToolLifecycleEventData } from '../types'

const isToolEvent = (event: ToolLifecycleEventData): boolean =>
  event.type === 'tool_started'
  || event.type === 'tool_args_delta'
  || event.type === 'tool_progress'
  || event.type === 'tool_completed'
  || event.type === 'tool_failed'

/**
 * 任务离开运行态后收口仍在转圈的内层工具。
 *
 * 取消/停止时事件流可能直接断开，最后一条 ``tool_completed``/``tool_failed``
 * 不会到达前端；保留 running/generating 会让工具卡永久转圈、计时继续走。
 * 这里只改未完成条目，已完成/失败的结果原样保留。
 */
export const finalizeSubagentTrailOnStop = (trail: SubagentTrailItem[]): SubagentTrailItem[] => {
  let changed = false
  const endedAt = Date.now()
  const next = trail.map((item) => {
    if (item.kind !== 'tool') return item
    if (item.status !== 'generating' && item.status !== 'running') return item
    changed = true
    return {
      ...item,
      status: 'cancelled' as const,
      argsStreaming: false,
      result: item.result || '任务已停止，工具未执行完成。',
      ended_at_ms: item.ended_at_ms ?? endedAt,
    }
  })
  return changed ? next : trail
}

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
    const thinking = event.thinking || ''
    const next = [...trail]
    const idx = next.findIndex(item => item.id === entryId && item.kind === 'message')
    if (idx >= 0) {
      const prev = next[idx]
      if (!prev) return trail
      // 收敛策略「更长者优先」：完整帧带完整预览，晚加入者可能因快照与游标
      // 非原子读取只收到尾部增量，需要靠这帧补齐；直播订阅者由增量拼出的
      // 全文不会短于截断预览，因此不会被误覆盖。思考同理。
      const nextContent = content && content.length >= (prev.content || '').length
        ? content
        : prev.content
      const nextThinking = thinking && thinking.length >= (prev.thinking || '').length
        ? thinking
        : prev.thinking
      next[idx] = event.streamed
        ? {
            ...prev,
            ...(nextContent !== undefined ? { content: nextContent } : {}),
            ...(nextThinking
              ? {
                  thinking: nextThinking,
                  thinkingTruncated: Boolean(event.thinking_truncated) || Boolean(prev.thinkingTruncated),
                }
              : {}),
            truncated: Boolean(event.truncated) || Boolean(prev.truncated),
          }
        : {
            ...prev,
            content: nextContent || '',
            ...(nextThinking
              ? {
                  thinking: nextThinking,
                  thinkingTruncated: Boolean(event.thinking_truncated) || Boolean(prev.thinkingTruncated),
                }
              : {}),
            truncated: Boolean(event.truncated),
          }
    } else {
      next.push({
        kind: 'message',
        id: entryId,
        content,
        truncated: Boolean(event.truncated),
        ...(thinking ? { thinking, thinkingTruncated: Boolean(event.thinking_truncated) } : {}),
      })
    }
    return next
  }

  if (!isToolEvent(event)) return trail
  const entryId = event.tool_call_id
  if (!entryId) return trail
  const progressPatch: Partial<SubagentTrailItem> = {
    ...(typeof event.content_lines === 'number' ? { contentLines: event.content_lines } : {}),
    ...(typeof event.removed_lines === 'number' ? { removedLines: event.removed_lines } : {}),
    ...(typeof event.intent === 'string' && event.intent ? { streamingIntent: event.intent } : {}),
  }
  if (event.type === 'tool_args_delta') {
    const delta = event.delta || ''
    const next = [...trail]
    const idx = next.findIndex(item => item.id === entryId)
    const prev = idx >= 0 ? next[idx] : undefined
    if (prev && (prev.status === 'completed' || prev.status === 'error')) return trail
    if (event.done) {
      if (!prev) return trail
      next[idx] = {
        ...prev,
        status: prev.status === 'generating' ? 'running' : prev.status,
        argsStreaming: false,
        argsText: undefined,
        argsTruncated: undefined,
      }
      return next
    }
    const entry: SubagentTrailItem = {
      ...(prev || {}),
      kind: 'tool',
      id: entryId,
      tool_name: event.tool_name || prev?.tool_name || 'tool_call',
      args: prev?.args || {},
      status: 'generating',
      argsText: `${prev?.argsText || ''}${delta}`,
      argsStreaming: true,
      argsTruncated: Boolean(event.truncated) || Boolean(prev?.argsTruncated),
      ...progressPatch,
      started_at_ms: prev?.started_at_ms ?? null,
      ended_at_ms: prev?.ended_at_ms ?? null,
    }
    if (idx >= 0) next[idx] = entry
    else next.push(entry)
    return next
  }
  const previous = trail.find(item => item.id === entryId)
  // 检查点快照之后重连续传时，事件流可能重放快照里已经完成的工具。
  // 终态优先：非终态事件不得把 completed/error 打回 running，否则会一直转圈。
  if (
    previous
    && (previous.status === 'completed' || previous.status === 'error')
    && (event.type === 'tool_started' || event.type === 'tool_progress')
  ) {
    return trail
  }
  const entry: SubagentTrailItem = {
    kind: 'tool',
    id: entryId,
    tool_name: event.tool_name || 'tool_call',
    args: { ...(event.args || {}) },
    result: event.content || event.result,
    status: event.type === 'tool_failed' ? 'error' : event.type === 'tool_completed' ? 'completed' : 'running',
    // 非增量生命周期事件（tool_started/完成）意味着参数生成结束：保留预览文本但停止展示
    argsText: previous?.argsText,
    argsStreaming: false,
    argsTruncated: previous?.argsTruncated,
    ...progressPatch,
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
      // tool_started 声明了 replace_args 时按权威快照整份替换；其余生命周期事件
      // （如不带 args 的 tool_completed）仍合并，保留开始时的参数。
      args: event.replace_args ? { ...(entry.args || {}) } : { ...(prev.args || {}), ...(entry.args || {}) },
      // tool_started 事件不带计时，完成事件才补齐：缺省时保留已有值
      started_at_ms: entry.started_at_ms ?? prev.started_at_ms ?? null,
      ended_at_ms: entry.ended_at_ms ?? prev.ended_at_ms ?? null,
    }
  } else {
    next.push(entry)
  }
  return next
}
