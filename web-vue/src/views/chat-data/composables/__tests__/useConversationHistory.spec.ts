import { describe, expect, it } from 'vitest'
import { buildHistoryMessages, buildHistoryMessagesFromEvents, stripHistoryUserContent } from '../useConversationHistory'

describe('buildHistoryMessages', () => {
  it('preserves thinking, tool calls, and final text in their original order', () => {
    const messages = buildHistoryMessages([
      { id: 'user-1', role: 'user', content: '查看所有订单明细' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'tool-select', name: 'file_grep', args: {} }],
      },
      { id: 'tool-result-1', role: 'tool', tool_call_id: 'tool-select', content: '已选择指标集' },
      {
        id: 'assistant-2',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'tool-sql', name: 'data_analyzing_tool', args: {} }],
      },
      { id: 'tool-result-2', role: 'tool', tool_call_id: 'tool-sql', content: 'SELECT * FROM order_items' },
      {
        id: 'assistant-3',
        role: 'assistant',
        content: '',
        tool_calls: [{ id: 'tool-query', name: 'data_analyzing_tool', args: {} }],
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
        tool_calls: [{ id: 'tool-query', name: 'data_analyzing_tool', args: {} }],
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
        payload: { id: 'assistant-1', content: '', tool_calls: [{ id: 'tool-1', name: 'file_grep', args: {} }] },
      },
      { sequence_no: 4, turn_id: 'turn-1', event_type: 'tool_result', payload: { id: 'tool-result-1', tool_call_id: 'tool-1', content: '已选择订单指标集' } },
      { sequence_no: 5, turn_id: 'turn-1', event_type: 'reasoning', payload: { id: 'thinking-2', content: '继续生成 SQL' } },
      {
        sequence_no: 6,
        turn_id: 'turn-1',
        event_type: 'assistant_message',
        payload: { id: 'assistant-2', content: '', tool_calls: [{ id: 'tool-2', name: 'data_analyzing_tool', args: {} }] },
      },
      { sequence_no: 7, turn_id: 'turn-1', event_type: 'tool_result', payload: { id: 'tool-result-2', tool_call_id: 'tool-2', content: 'SELECT * FROM orders' } },
      { sequence_no: 8, turn_id: 'turn-1', event_type: 'assistant_message', payload: { id: 'assistant-3', content: '查询已完成。' } },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[1]!.turnId).toBe('turn-1')
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

  it('keeps assistant text before a tool call stored in the same AI message', () => {
    const messages = buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '生成报告' } },
      {
        sequence_no: 2,
        turn_id: 'turn-1',
        event_type: 'assistant_message',
        payload: {
          id: 'assistant-1',
          content: '为了生成报告，我需要您确认以下问题：',
          tool_calls: [{ id: 'ask-1', name: 'ask_user_question', args: {} }],
        },
      },
    ])

    expect(messages[1]!.segments).toEqual([
      { type: 'text', content: '为了生成报告，我需要您确认以下问题：' },
      { type: 'toolCall', toolCallId: 'ask-1' },
    ])
  })

  it('replays context compression as a standalone timeline marker', () => {
    const messages = buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '继续分析' } },
      { sequence_no: 2, turn_id: 'turn-1', event_type: 'context_compression', payload: {
        id: 'context-compression-1',
        timestamp: '2026-07-29 10:00:00',
        estimated_tokens_before: 70000,
        estimated_tokens_after: 21000,
        estimated_cleared_tokens: 49000,
        compressed_messages: 12,
      } },
      { sequence_no: 3, turn_id: 'turn-1', event_type: 'assistant_message', payload: { id: 'assistant-1', content: '已完成' } },
    ])

    expect(messages.map(message => message.role)).toEqual(['user', 'context_compression', 'assistant'])
    expect(messages[1]!.turnId).toBe('turn-1')
    expect(messages[2]!.turnId).toBe('turn-1')
    expect(messages[1]!.contextCompression).toMatchObject({
      id: 'context-compression-1',
      estimated_cleared_tokens: 49000,
    })
  })
})

describe('subagent trail replay', () => {
  const trail = [
    { kind: 'message', id: 'm1', content: '开始计算', truncated: false, thinking: '需要精确计算', thinkingTruncated: false },
    { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 + 1' }, result: '2', status: 'completed' },
  ]

  it('restores subagent trail from history events onto the task tool call', () => {
    const messages = buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '算一下 1+1' } },
      {
        sequence_no: 2, turn_id: 'turn-1', event_type: 'assistant_message',
        payload: { id: 'assistant-1', role: 'assistant', content: '', tool_calls: [{ id: 'task-1', name: 'task', args: { description: '算 1+1', subagent_type: 'data-explore' } }] },
      },
      {
        sequence_no: 3, turn_id: 'turn-1', event_type: 'tool_result',
        payload: { id: 'tool-result-1', role: 'tool', tool_call_id: 'task-1', tool_name: 'task', content: '1 + 1 = 2', subagent_trail: trail },
      },
      {
        sequence_no: 4, turn_id: 'turn-1', event_type: 'assistant_message',
        payload: { id: 'assistant-2', role: 'assistant', content: '答案是 2。' },
      },
    ])

    const assistant = messages.find(message => message.role === 'assistant')!
    const taskCall = assistant.toolCalls?.find(tool => tool.tool_call_id === 'task-1')
    expect(taskCall?.subagentTrail).toEqual(trail)
    // 普通工具不受影响
    expect(assistant.toolCalls).toHaveLength(1)
  })

  it('restores subagent trail from the legacy messages path', () => {
    const messages = buildHistoryMessages([
      { id: 'user-1', role: 'user', content: '算一下 1+1' },
      {
        id: 'assistant-1', role: 'assistant', content: '',
        tool_calls: [{ id: 'task-1', name: 'task', args: {} }],
      },
      { id: 'tool-result-1', role: 'tool', tool_call_id: 'task-1', content: '1 + 1 = 2', subagent_trail: trail },
      { id: 'assistant-2', role: 'assistant', content: '答案是 2。' },
    ], [])

    const assistant = messages.find(message => message.role === 'assistant')!
    expect(assistant.toolCalls?.[0]?.subagentTrail).toEqual(trail)
  })

  it('restores subagent token usage from history events', () => {
    const messages = buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '盘点' } },
      {
        sequence_no: 2, turn_id: 'turn-1', event_type: 'assistant_message',
        payload: { id: 'assistant-1', role: 'assistant', content: '', tool_calls: [{ id: 'task-1', name: 'task', args: {} }] },
      },
      {
        sequence_no: 3, turn_id: 'turn-1', event_type: 'tool_result',
        payload: {
          id: 'tool-result-1', role: 'tool', tool_call_id: 'task-1', tool_name: 'task', content: '报告',
          subagent_token_usage: {
            input_tokens: 3_000,
            output_tokens: 300,
            total_tokens: 3_300,
            cache_read_input_tokens: 1_000,
          },
        },
      },
    ])
    const taskCall = messages.find(message => message.role === 'assistant')!.toolCalls?.[0]
    expect(taskCall?.subagentTokenUsage).toEqual({
      input_tokens: 3_000,
      output_tokens: 300,
      total_tokens: 3_300,
      cache_read_input_tokens: 1_000,
      cache_creation_input_tokens: undefined,
      reasoning_tokens: undefined,
    })
  })

  it('restores task tool execution timing (subagent total duration) from history events', () => {
    const messages = buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '盘点' } },
      {
        sequence_no: 2, turn_id: 'turn-1', event_type: 'assistant_message',
        payload: { id: 'assistant-1', role: 'assistant', content: '', tool_calls: [{ id: 'task-1', name: 'task', args: {} }] },
      },
      {
        sequence_no: 3, turn_id: 'turn-1', event_type: 'tool_result',
        payload: {
          id: 'tool-result-1', role: 'tool', tool_call_id: 'task-1', tool_name: 'task', content: '报告',
          started_at_ms: 10_000, ended_at_ms: 190_000,
        },
      },
    ])
    const taskCall = messages.find(message => message.role === 'assistant')!.toolCalls?.[0]
    expect(taskCall?.started_at_ms).toBe(10_000)
    expect(taskCall?.ended_at_ms).toBe(190_000)
  })

  it('restores inner tool execution timing on the trail', () => {
    const messages = buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '算一下' } },
      {
        sequence_no: 2, turn_id: 'turn-1', event_type: 'assistant_message',
        payload: { id: 'assistant-1', role: 'assistant', content: '', tool_calls: [{ id: 'task-1', name: 'task', args: {} }] },
      },
      {
        sequence_no: 3, turn_id: 'turn-1', event_type: 'tool_result',
        payload: {
          id: 'tool-result-1', role: 'tool', tool_call_id: 'task-1', tool_name: 'task', content: '结论',
          subagent_trail: [{
            kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: {},
            result: '2', status: 'completed', started_at_ms: 1_000, ended_at_ms: 1_250,
          }],
        },
      },
    ])
    const assistant = messages.find(message => message.role === 'assistant')!
    expect(assistant.toolCalls?.[0]?.subagentTrail?.[0]).toMatchObject({
      started_at_ms: 1_000,
      ended_at_ms: 1_250,
    })
  })

  it('restores subagent context usage: window percent for new snapshots, legacy percent as budget', () => {
    const buildUsage = (usage: Record<string, unknown>) => buildHistoryMessagesFromEvents([
      { sequence_no: 1, turn_id: 'turn-1', event_type: 'user_message', payload: { id: 'user-1', content: '盘点' } },
      {
        sequence_no: 2, turn_id: 'turn-1', event_type: 'assistant_message',
        payload: { id: 'assistant-1', role: 'assistant', content: '', tool_calls: [{ id: 'task-1', name: 'task', args: {} }] },
      },
      {
        sequence_no: 3, turn_id: 'turn-1', event_type: 'tool_result',
        payload: {
          id: 'tool-result-1', role: 'tool', tool_call_id: 'task-1', tool_name: 'task', content: '结论',
          subagent_context_usage: usage,
        },
      },
    ]).find(message => message.role === 'assistant')!.toolCalls?.[0]?.subagentContextUsage

    expect(buildUsage({
      used_tokens: 30_000,
      threshold_tokens: 70_000,
      context_window: 100_000,
      percent: 30,
      budget_percent: 42,
    })).toEqual({
      used_tokens: 30_000,
      threshold_tokens: 70_000,
      context_window: 100_000,
      percent: 30,
      budget_percent: 42,
    })

    // 旧快照没有窗口字段：percent 当时是预算口径 → 归到 budget_percent，不伪造成窗口占比
    const legacy = buildUsage({ used_tokens: 30_000, threshold_tokens: 70_000, percent: 42 })
    expect(legacy?.budget_percent).toBe(42)
    expect(legacy?.percent).toBeUndefined()
  })

  it('drops invalid trail entries instead of failing the whole replay', () => {
    const messages = buildHistoryMessages([
      { id: 'user-1', role: 'user', content: '分析' },
      {
        id: 'assistant-1', role: 'assistant', content: '',
        tool_calls: [{ id: 'task-1', name: 'task', args: {} }],
      },
      {
        id: 'tool-result-1', role: 'tool', tool_call_id: 'task-1', content: '结论',
        subagent_trail: [
          null,
          { kind: 'message', id: '' },
          { kind: 'message', id: 'm1', content: '', thinking: '' },
          { kind: 'unknown', id: 'x' },
          { kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: 'bad-args', status: 'weird' },
          { kind: 'message', id: 'm2', content: '有效文本', truncated: 'yes' },
        ],
      },
      { id: 'assistant-2', role: 'assistant', content: '结论。' },
    ], [])

    const assistant = messages.find(message => message.role === 'assistant')!
    const restored = assistant.toolCalls?.[0]?.subagentTrail
    expect(restored).toHaveLength(2)
    expect(restored?.[0]).toEqual({ kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: {}, result: '', status: 'completed' })
    expect(restored?.[1]).toEqual({ kind: 'message', id: 'm2', content: '有效文本', truncated: false, thinking: '', thinkingTruncated: false })
  })

  it('leaves subagentTrail undefined when the payload carries none', () => {
    const messages = buildHistoryMessages([
      { id: 'user-1', role: 'user', content: '查数据' },
      {
        id: 'assistant-1', role: 'assistant', content: '',
        tool_calls: [{ id: 'tool-query', name: 'data_analyzing_tool', args: {} }],
      },
      { id: 'tool-result-1', role: 'tool', tool_call_id: 'tool-query', content: '{"success": true}' },
      { id: 'assistant-2', role: 'assistant', content: '完成。' },
    ], [])

    const assistant = messages.find(message => message.role === 'assistant')!
    expect(assistant.toolCalls?.[0]?.subagentTrail).toBeUndefined()
  })
})

describe('stripHistoryUserContent', () => {
  it('strips quote blocks and mention suffix in regular-send order (body + mention + quote)', () => {
    const content = '查一下\n\n[引用文件：/mnt/user-data/uploads/a.pdf]\n\n[引用助手回复片段开始]\n上一轮结论原文\n[引用助手回复片段结束]'

    expect(stripHistoryUserContent(content)).toBe('查一下')
  })

  it('strips inbox-order content: body + mention + quote block + attachment placeholder', () => {
    // 排队消息采纳后的完整形态：mention 短句后面跟着 [attachment] 行，
    // 行尾锚定必须是多行的，否则 [引用文件：…] 会漏进气泡
    const content = [
      '查一下',
      '',
      '[引用文件：/mnt/user-data/uploads/a.pdf]',
      '',
      '[引用助手回复片段开始]',
      '上一轮结论原文',
      '[引用助手回复片段结束]',
      '',
      '[attachment] /mnt/user-data/uploads/a.pdf',
    ].join('\n')

    // mention 正则的 \s* 会吞掉短句后的空行（可接受，占位行由组件侧剥离/合成附件卡片）
    expect(stripHistoryUserContent(content)).toBe('查一下\n[attachment] /mnt/user-data/uploads/a.pdf')
  })

  it('removes stray orphan quote markers and collapses extra blank lines', () => {
    const content = '正文\n\n[引用助手回复片段开始]\n引用原文\n[引用助手回复片段结束]\n\n\n\n结尾'

    expect(stripHistoryUserContent(content)).toBe('正文\n\n结尾')
  })

  it('strips legacy semantic-model mention suffix', () => {
    expect(stripHistoryUserContent('查数据\n\n[本轮指定语义模型：订单模型]')).toBe('查数据')
  })
})
