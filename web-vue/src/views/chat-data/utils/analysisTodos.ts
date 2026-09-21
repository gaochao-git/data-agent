import type { Message } from '../types'

export interface AnalysisTodoItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
}

export interface AnalysisTodosState {
  items: AnalysisTodoItem[]
  finished: boolean
}

/**
 * 从消息流中派生最新一次 write_todos 的任务清单。
 * 实时流式与历史回放走同一份 messages，因此天然同源；
 * 单次调用参数为全量替换语义，后一次调用覆盖前一次。
 */
export function deriveAnalysisTodos(messages: Message[]): AnalysisTodosState {
  let items: AnalysisTodoItem[] = []
  for (const message of messages) {
    for (const toolCall of message.toolCalls || []) {
      if (toolCall.tool_name !== 'write_todos') continue
      const todos = Array.isArray(toolCall.args?.todos) ? toolCall.args.todos : []
      const mapped = todos
        .filter((item): item is { content: string; status?: unknown } => {
          return Boolean(item) && typeof (item as { content?: unknown }).content === 'string'
        })
        .map(item => ({
          content: String(item.content),
          status: item.status === 'completed' || item.status === 'in_progress'
            ? item.status as AnalysisTodoItem['status']
            : 'pending' as const,
        }))
      // 流式期间参数可能不完整，空清单不覆盖上一次结果，避免进度条闪烁
      if (mapped.length > 0) items = mapped
    }
  }
  const finished = items.length > 0 && items.every(item => item.status === 'completed')
  return { items, finished }
}
