import { describe, expect, it } from 'vitest'
import { buildHistoryMessages, buildHistoryMessagesFromEvents } from '../useConversationHistory'

describe('buildHistoryMessages', () => {
  it('preserves thinking, tool calls, and final text in their original order', () => {
    const messages = buildHistoryMessages([
      { id: 'user-1', role: 'user', content: '查看所有订单明细' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'tool-select', name: 'select_semantic_config_tool', args: {} }],
      },
      { id: 'tool-result-1', role: 'tool', tool_call_id: 'tool-select', content: '已选择指标集' },
      {
        id: 'assistant-2',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'tool-sql', name: 'dsl2sql_tool', args: {} }],
      },
      { id: 'tool-result-2', role: 'tool', tool_call_id: 'tool-sql', content: 'SELECT * FROM order_items' },
      {
        id: 'assistant-3',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'tool-query', name: 'semantic_query_tool', args: {} }],
      },
      { id: 'tool-result-3', role: 'tool', tool_call_id: 'tool-query', content: '{"success": true}' },
      { id: 'assistant-4', role: 'assistant', content: '已查询到 100 条订单明细。' },
    ], [
      { id: 'thinking-1', content: '先确定使用的指标集。', toolCallCount: 0 },
      { id: 'thinking-2', content: '接着生成查询 SQL。', toolCallCount: 1 },
      { id: 'thinking-3', content: '最后执行明细查询。', toolCallCount: 2 },
    ])

    expect(messages).toHaveLength(2)
    const assistant = messages[1]!

    expect(assistant.segments).toEqual([
      { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
      { type: 'toolCall', toolCallId: 'tool-select' },
      { type: 'thinkingSegment', thinkingSegmentId: 'thinking-2' },
      { type: 'toolCall', toolCallId: 'tool-sql' },
      { type: 'thinkingSegment', thinkingSegmentId: 'thinking-3' },
      { type: 'toolCall', toolCallId: 'tool-query' },
      { type: 'text', content: '已查询到 100 条订单明细。' },
    ])
    expect(assistant.toolCalls?.map(tool => tool.result)).toEqual([
      '已选择指标集',
      'SELECT * FROM order_items',
      '{"success": true}',
    ])
  })

  it('associates persisted thinking with its original user turn', () => {
    const messages = buildHistoryMessages([
      { id: 'user-1', role: 'user', content: '先回答第一个问题', reasoning_turn_id: 'turn-1' },
      { id: 'assistant-1', role: 'assistant', content: '第一个回答。' },
      { id: 'user-2', role: 'user', content: '再查询第二个问题', reasoning_turn_id: 'turn-2' },
      {
        id: 'assistant-2',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'tool-query', name: 'semantic_query_tool', args: {} }],
      },
      { id: 'tool-result-2', role: 'tool', tool_call_id: 'tool-query', content: '{"success": true}' },
      { id: 'assistant-3', role: 'assistant', content: '第二个回答。' },
    ], [
      {
        id: 'thinking-2',
        content: '先执行第二个问题的查询。',
        toolCallCount: 0,
        reasoningTurnId: 'turn-2',
      },
    ])

    expect(messages).toHaveLength(4)
    expect(messages[1]!.segments).toEqual([
      { type: 'text', content: '第一个回答。' },
    ])
    expect(messages[3]!.segments).toEqual([
      { type: 'thinkingSegment', thinkingSegmentId: 'thinking-2' },
      { type: 'toolCall', toolCallId: 'tool-query' },
      { type: 'text', content: '第二个回答。' },
    ])
  })

  it('replays persisted history events by sequence instead of guessing thought positions', () => {
    const messages = buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '查询订单' } },
      { sequence_no: 2, turn_id: 'turn-1', event_type: 'reasoning', payload: { id: 'thinking-1', content: '先选择指标集' } },
      {
        sequence_no: 3,
        turn_id: 'turn-1',
        event_type: 'assistant_message',
        payload: { id: 'assistant-1', content: '', tool_calls: [{ id: 'tool-1', name: 'select_semantic_config_tool', args: {} }] },
      },
      { sequence_no: 4, turn_id: 'turn-1', event_type: 'tool_result', payload: { id: 'tool-result-1', tool_call_id: 'tool-1', content: '已选择订单指标集' } },
      { sequence_no: 5, turn_id: 'turn-1', event_type: 'reasoning', payload: { id: 'thinking-2', content: '继续生成 SQL' } },
      {
        sequence_no: 6,
        turn_id: 'turn-1',
        event_type: 'assistant_message',
        payload: { id: 'assistant-2', content: '', tool_calls: [{ id: 'tool-2', name: 'dsl2sql_tool', args: {} }] },
      },
      { sequence_no: 7, turn_id: 'turn-1', event_type: 'tool_result', payload: { id: 'tool-result-2', tool_call_id: 'tool-2', content: 'SELECT * FROM orders' } },
      { sequence_no: 8, turn_id: 'turn-1', event_type: 'assistant_message', payload: { id: 'assistant-3', content: '查询已完成。' } },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[1]!.segments).toEqual([
      { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
      { type: 'toolCall', toolCallId: 'tool-1' },
      { type: 'thinkingSegment', thinkingSegmentId: 'thinking-2' },
      { type: 'toolCall', toolCallId: 'tool-2' },
      { type: 'text', content: '查询已完成。' },
    ])
    expect(messages[1]!.toolCalls?.map(tool => tool.result)).toEqual([
      '已选择订单指标集',
      'SELECT * FROM orders',
    ])
  })
})
