import { describe, expect, it } from 'vitest'

import { deriveAnalysisTodos } from '../analysisTodos'
import type { Message } from '../../types'

const toolMessage = (toolName: string, args: Record<string, unknown>): Message => ({
  id: `m-${Math.random().toString(36).slice(2)}`,
  role: 'assistant',
  content: '',
  toolCalls: [{ tool_call_id: 'tc-1', tool_name: toolName, args, status: 'completed' }],
})

const todosArg = (statuses: string[]) => ({
  todos: statuses.map((status, index) => ({ content: `步骤${index + 1}`, status })),
})

describe('deriveAnalysisTodos', () => {
  it('returns empty state when no write_todos exists', () => {
    expect(deriveAnalysisTodos([toolMessage('file_read', { path: 'a.txt' })])).toEqual({ items: [], finished: false })
  })

  it('derives the latest write_todos and reports finished only when all completed', () => {
    const messages = [
      toolMessage('write_todos', todosArg(['completed', 'in_progress'])),
      toolMessage('file_read', { path: 'a.txt' }),
      toolMessage('write_todos', todosArg(['completed', 'completed'])),
    ]
    const state = deriveAnalysisTodos(messages)
    expect(state.items).toEqual([
      { content: '步骤1', status: 'completed' },
      { content: '步骤2', status: 'completed' },
    ])
    expect(state.finished).toBe(true)
  })

  it('normalizes unknown status to pending', () => {
    const state = deriveAnalysisTodos([toolMessage('write_todos', todosArg(['completed', 'unknown']))])
    expect(state.items[1]).toEqual({ content: '步骤2', status: 'pending' })
    expect(state.finished).toBe(false)
  })

  it('keeps the previous list when a streaming call has incomplete args', () => {
    const state = deriveAnalysisTodos([
      toolMessage('write_todos', todosArg(['in_progress', 'pending'])),
      toolMessage('write_todos', {}),
    ])
    expect(state.items).toHaveLength(2)
    expect(state.items[0]).toEqual({ content: '步骤1', status: 'in_progress' })
  })

  it('drops malformed entries without content', () => {
    const state = deriveAnalysisTodos([toolMessage('write_todos', {
      todos: [{ content: '有效步骤', status: 'pending' }, { status: 'pending' }, 'junk'],
    })])
    expect(state.items).toEqual([{ content: '有效步骤', status: 'pending' }])
  })
})
