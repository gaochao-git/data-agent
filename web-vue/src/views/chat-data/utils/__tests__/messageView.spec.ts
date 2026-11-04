import { describe, expect, it } from 'vitest'
import type { Message, ToolCallInfo } from '../../types'
import {
  buildMessageBlocks,
  buildSubagentMessages,
  buildSubagentTrailBlocks,
  formatFileSize,
  getActiveThinkingSegmentId,
  getAssistantCopyText,
  getEffectiveAttachments,
  findFinalAnswerSegmentIndex,
  getThinkingPreviewText,
  hasWorkProcess,
  summarizeWorkProcess,
  getUserMentionedFiles,
  getUserQuotedSnippets,
  hasSubagentTrail,
  hasVisibleAssistantContent,
  isLongUserMessage,
  parseAttachmentPlaceholders,
  resolveActiveRunStartIndex,
  shouldShowAssistantFooter,
  shouldShowStreamingStatus,
  stripAttachmentPlaceholders,
} from '../messageView'

const assistant = (patch: Partial<Message> = {}): Message => ({
  id: 'assistant-1',
  role: 'assistant',
  content: '',
  ...patch,
})

const toolCall = (patch: Partial<ToolCallInfo> = {}): ToolCallInfo => ({
  tool_call_id: 'tool-1',
  tool_name: 'calculator_tool',
  args: { expression: '1 + 1' },
  status: 'completed',
  result: '2',
  ...patch,
})

describe('getThinkingPreviewText', () => {
  it('压成单行后只保留尾部 80 字符并补省略号', () => {
    const content = `${'前文 '.repeat(30)}最后结论`
    const preview = getThinkingPreviewText(content)
    expect(preview.startsWith('…')).toBe(true)
    expect(preview).toHaveLength(81)
    expect(preview.endsWith('最后结论')).toBe(true)
  })

  it('短内容不加省略号，空白内容返回空串', () => {
    expect(getThinkingPreviewText('  一段  思考 ')).toBe('一段 思考')
    expect(getThinkingPreviewText('   ')).toBe('')
  })
})

describe('getActiveThinkingSegmentId', () => {
  it('只有流式中且最后一段是思考分段时才返回 id', () => {
    const message = assistant({
      isStreaming: true,
      segments: [
        { type: 'text', content: '开头' },
        { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
      ],
    })
    expect(getActiveThinkingSegmentId(message)).toBe('thinking-1')
    expect(getActiveThinkingSegmentId({ ...message, isStreaming: false })).toBeNull()
    expect(getActiveThinkingSegmentId(assistant({
      isStreaming: true,
      segments: [{ type: 'text', content: '只有文本' }],
    }))).toBeNull()
  })
})

describe('buildMessageBlocks', () => {
  const message = assistant({
    segments: [
      { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
      { type: 'text', content: '中间叙述' },
      { type: 'toolCall', toolCallId: 'tool-1' },
      { type: 'toolCall', toolCallId: 'missing-tool' },
      { type: 'thinkingSegment', thinkingSegmentId: 'missing-thinking' },
      { type: 'text', content: '   ' },
    ],
    thinkingSegments: [{ id: 'thinking-1', content: '先看语义模型，再查订单表。', toolCallCount: 0 }],
    toolCalls: [toolCall()],
    isStreaming: false,
  })

  it('按 segments 顺序产出块，缺失引用与空白文本跳过', () => {
    const blocks = buildMessageBlocks(message, {
      shouldInlineRenderResult: call => call.tool_call_id === 'tool-1',
    })
    expect(blocks.map(block => block.type)).toEqual(['thinking', 'text', 'tool'])
    expect(blocks[0]).toMatchObject({ type: 'thinking', segmentId: 'thinking-1', content: '先看语义模型，再查订单表。' })
    expect(blocks[1]).toMatchObject({ type: 'text', content: '中间叙述' })
    expect(blocks[2]).toMatchObject({
      type: 'tool',
      queryResultToolCall: expect.objectContaining({ tool_call_id: 'tool-1' }),
    })
  })

  it('无 segments 时退化为 content 单文本块（视为最终回答）', () => {
    expect(buildMessageBlocks(assistant({ content: '  只有正文  ' }))).toEqual([
      { type: 'text', key: 'content', content: '只有正文', finalAnswer: true },
    ])
    expect(buildMessageBlocks(assistant({ content: '   ' }))).toEqual([])
  })

  it('最后一段有可见文本的分段标记为最终回答，中间叙述进工作过程', () => {
    const blocks = buildMessageBlocks(assistant({
      segments: [
        { type: 'text', content: '我先看一下数据分布。' },
        { type: 'text', content: '' },
        { type: 'text', content: '结论：数据已就绪。' },
      ],
    }))
    expect(blocks).toMatchObject([
      { type: 'text', content: '我先看一下数据分布。', finalAnswer: false },
      { type: 'text', content: '结论：数据已就绪。', finalAnswer: true },
    ])
    expect(findFinalAnswerSegmentIndex(assistant({
      segments: [
        { type: 'text', content: '我先看一下数据分布。' },
        { type: 'text', content: '结论：数据已就绪。' },
      ],
    }))).toBe(1)
    expect(hasWorkProcess(blocks)).toBe(true)
    expect(summarizeWorkProcess(blocks)).toBe('')
  })

  it('工作过程摘要统计思考与工具块数量；纯最终回答没有工作过程', () => {
    const blocks = buildMessageBlocks(assistant({
      segments: [
        { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
        { type: 'toolCall', toolCallId: 'tool-1' },
        { type: 'text', content: '最终回答' },
      ],
      thinkingSegments: [{ id: 'thinking-1', content: '思考', toolCallCount: 0 }],
      toolCalls: [toolCall()],
    }))
    expect(hasWorkProcess(blocks)).toBe(true)
    expect(summarizeWorkProcess(blocks)).toBe('思考 1 · 工具 1')
    expect(hasWorkProcess(buildMessageBlocks(assistant({ content: '最终回答' })))).toBe(false)
  })

  it('流式中最后一段思考标记 active 并带尾部预览，其余分段不带预览', () => {
    const blocks = buildMessageBlocks(assistant({
      isStreaming: true,
      segments: [
        { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
        { type: 'thinkingSegment', thinkingSegmentId: 'thinking-2' },
      ],
      thinkingSegments: [
        { id: 'thinking-1', content: '第一段思考', toolCallCount: 0 },
        { id: 'thinking-2', content: '第二段思考', toolCallCount: 1 },
      ],
    }))
    expect(blocks[0]).toMatchObject({ type: 'thinking', active: false, preview: '' })
    expect(blocks[1]).toMatchObject({ type: 'thinking', active: true, preview: '第二段思考' })
  })
})

describe('getAssistantCopyText / shouldShowStreamingStatus', () => {
  it('只复制最终回答，隐藏的中间叙述不泄露', () => {
    const message = assistant({
      content: '整段原文',
      segments: [
        { type: 'text', content: '第一段中间叙述' },
        { type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' },
        { type: 'text', content: '第二段最终回答' },
      ],
      thinkingSegments: [{ id: 'thinking-1', content: '思考', toolCallCount: 0 }],
    })
    expect(getAssistantCopyText(message)).toBe('第二段最终回答')
    expect(getAssistantCopyText(assistant({ content: '整段原文' }))).toBe('整段原文')
    expect(getAssistantCopyText({ ...message, role: 'user' })).toBe('')
    // 有 segments 但没有最终回答：返回错误文案（不复制隐藏叙述）
    expect(getAssistantCopyText(assistant({
      segments: [{ type: 'thinkingSegment', thinkingSegmentId: 'thinking-1' }],
      thinkingSegments: [{ id: 'thinking-1', content: '思考', toolCallCount: 0 }],
      error: '模型调用失败',
    }))).toBe('模型调用失败')
  })

  it('有正文/思考/工具任一即展示流式状态行', () => {
    expect(shouldShowStreamingStatus(assistant({ content: '正文' }))).toBe(true)
    expect(shouldShowStreamingStatus(assistant({ content: '正在思考...' }))).toBe(false)
    expect(shouldShowStreamingStatus(assistant({
      thinkingSegments: [{ id: 't', content: '思考', toolCallCount: 0 }],
    }))).toBe(true)
    expect(shouldShowStreamingStatus(assistant({ toolCalls: [toolCall()] }))).toBe(true)
    expect(hasVisibleAssistantContent('正文')).toBe(true)
    expect(hasVisibleAssistantContent('正在思考...')).toBe(false)
  })
})

describe('footer 口径', () => {
  const messages: Message[] = [
    { id: 'user-1', role: 'user', content: '第一轮' },
    { id: 'assistant-old', role: 'assistant', content: '第一轮回答' },
    { id: 'user-2', role: 'user', content: '第二轮' },
    { id: 'assistant-new', role: 'assistant', content: '第二轮回答' },
  ]

  it('run 活跃时只抑制最后一条 user 之后的消息 footer', () => {
    const start = resolveActiveRunStartIndex(messages, true)
    expect(start).toBe(2)
    expect(shouldShowAssistantFooter(messages[1]!, 1, { runActive: true, activeRunStartIndex: start })).toBe(true)
    expect(shouldShowAssistantFooter(messages[3]!, 3, { runActive: true, activeRunStartIndex: start })).toBe(false)
    expect(shouldShowAssistantFooter(messages[3]!, 3, { runActive: false, activeRunStartIndex: -1 })).toBe(true)
  })

  it('流式中一律不展示 footer；无内容时不展示', () => {
    expect(shouldShowAssistantFooter(assistant({ isStreaming: true }), 0, { runActive: false, activeRunStartIndex: -1 })).toBe(false)
    expect(shouldShowAssistantFooter(assistant({ content: '  ' }), 0, { runActive: false, activeRunStartIndex: -1 })).toBe(false)
  })
})

describe('用户消息投影', () => {
  it('附件占位：解析路径、类型与文件名，并从正文中剥离', () => {
    const content = '看看这个\n[attachment] /mnt/workspace/订单.xlsx\n[attachment] /mnt/workspace/data.csv'
    expect(parseAttachmentPlaceholders(content)).toEqual([
      { file_id: '/mnt/workspace/订单.xlsx', filename: '订单.xlsx', file_type: 'xlsx', original_path: '/mnt/workspace/订单.xlsx' },
      { file_id: '/mnt/workspace/data.csv', filename: 'data.csv', file_type: 'csv', original_path: '/mnt/workspace/data.csv' },
    ])
    expect(stripAttachmentPlaceholders(content)).toBe('看看这个')
    const message: Message = { id: 'u', role: 'user', content }
    expect(getEffectiveAttachments(message)).toHaveLength(2)
  })

  it('结构化 attachments 优先于正文占位', () => {
    const message: Message = {
      id: 'u',
      role: 'user',
      content: '[attachment] /mnt/a.xlsx',
      attachments: [{ file_id: 'f1', filename: '结构化.xlsx', file_type: 'xlsx' }],
    }
    expect(getEffectiveAttachments(message)).toEqual([{ file_id: 'f1', filename: '结构化.xlsx', file_type: 'xlsx' }])
  })

  it('@ 文件与划选引用只对用户消息生效', () => {
    const user: Message = {
      id: 'u',
      role: 'user',
      content: '',
      fileSelections: ['', '/mnt/workspace/a.sql', '  '],
      quotedSnippets: [{ id: 'q1', text: ' 引用片段 ' }, { id: 'q2', text: '   ' }],
    }
    expect(getUserMentionedFiles(user)).toEqual([{ path: '/mnt/workspace/a.sql', name: 'a.sql' }])
    expect(getUserQuotedSnippets(user)).toEqual([{ id: 'q1', text: '引用片段' }])
    expect(getUserMentionedFiles(assistant())).toEqual([])
    expect(getUserQuotedSnippets(assistant())).toEqual([])
  })

  it('长消息按行数判定，文件大小按档位格式化', () => {
    expect(isLongUserMessage(Array.from({ length: 11 }, () => '行').join('\n'))).toBe(true)
    expect(isLongUserMessage('一行')).toBe(false)
    expect(formatFileSize()).toBe('')
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(2048)).toBe('2.0 KB')
    expect(formatFileSize(3 * 1024 * 1024)).toBe('3.0 MB')
  })
})

describe('子智能体过程 → 主对话消息投影', () => {
  const taskCall = (patch: Partial<ToolCallInfo> = {}): ToolCallInfo => toolCall({
    tool_call_id: 'call-task',
    tool_name: 'task',
    args: { description: '盘点语义模型中的指标', subagent_type: 'explore' },
    result: '报告',
    status: 'running',
    ...patch,
  })

  it('任务书投影成 user 消息，整段过程合并成一条 assistant 消息（思考/叙述/工具入 segments）', () => {
    const messages = buildSubagentMessages(taskCall({
      subagentTrail: [
        { kind: 'message', id: 'msg-1', content: '先看目录', thinking: '思考一' },
        { kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: { path: '/mnt/a.sql' }, status: 'completed', result: 'select 1' },
        { kind: 'tool', id: 'inner-2', tool_name: 'calculator_tool', args: {}, status: 'completed', result: '2' },
        { kind: 'message', id: 'msg-2', content: '结论：已盘点' },
      ],
    }))

    expect(messages.map(message => message.role)).toEqual(['user', 'assistant'])
    expect(messages[0]).toMatchObject({ role: 'user', content: '盘点语义模型中的指标' })
    expect(messages[1]!.segments!.map(segment => segment.type))
      .toEqual(['thinkingSegment', 'text', 'toolCall', 'toolCall', 'text'])
    expect(messages[1]!.toolCalls).toHaveLength(2)
    expect(messages[1]!.isStreaming).toBe(true)
  })

  it('父 task 结束后单条过程消息不再标记流式', () => {
    const settled = buildSubagentMessages(taskCall({
      status: 'completed',
      subagentTrail: [{ kind: 'message', id: 'msg-1', content: '报告', thinking: '思考' }],
    }))
    expect(settled[1]!.isStreaming).toBe(false)
  })

  it('工具先于消息出现时仍合并进同一条助手消息；无有效过程时只有任务书', () => {
    const toolFirst = buildSubagentMessages(taskCall({
      subagentTrail: [
        { kind: 'tool', id: 'inner-1', tool_name: 'file_read', args: {}, status: 'running' },
        { kind: 'tool', id: 'inner-2', tool_name: 'calculator_tool', args: {}, status: 'running' },
      ],
    }))
    expect(toolFirst.map(message => message.role)).toEqual(['user', 'assistant'])
    expect(toolFirst[1]!.segments).toHaveLength(2)

    const promptOnly = buildSubagentMessages(taskCall({ subagentTrail: [], status: 'completed' }))
    expect(promptOnly.map(message => message.role)).toEqual(['user'])
    // 运行中即使没有正文/工具段也保留助手占位，状态行（工作中动画）可见
    const runningEmpty = buildSubagentMessages(taskCall({ subagentTrail: [], status: 'running' }))
    expect(runningEmpty.map(message => message.role)).toEqual(['user', 'assistant'])
  })
})

describe('子智能体过程投影', () => {
  const taskCall = (patch: Partial<ToolCallInfo> = {}): ToolCallInfo => toolCall({
    tool_call_id: 'call-task',
    tool_name: 'task',
    args: { description: '任务书' },
    result: '结论',
    status: 'running',
    ...patch,
  })

  it('按发生顺序混排消息与工具，工具补齐展示名与运行态', () => {
    const call = taskCall({
      subagentTrail: [
        { kind: 'message', id: 'msg-1', content: '先算一下' },
        { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: { expression: '1 + 1' }, status: 'completed', result: '2' },
        { kind: 'tool', id: 'inner-2', tool_name: 'data_analyzing_tool', args: { action: 'execute_query' }, status: 'running' },
        { kind: 'message', id: 'msg-2', content: '结论是 2。', truncated: true },
      ],
    })
    const blocks = buildSubagentTrailBlocks(call)
    expect(blocks.map(block => block.type)).toEqual(['message', 'tool', 'tool', 'message'])
    expect(blocks[1]).toMatchObject({
      type: 'tool',
      toolCall: expect.objectContaining({ tool_call_id: 'inner-1', status: 'completed' }),
      isRunning: false,
      isError: false,
    })
    expect((blocks[1] as { displayName: string }).displayName).not.toBe('')
    expect(blocks[2]).toMatchObject({ type: 'tool', isRunning: true })
    expect(blocks[3]).toMatchObject({ type: 'message', truncated: true })
  })

  it('只有最后一条消息的思考在父 task 运行时算 active，并带尾部预览', () => {
    const longThinking = `${'核对字段口径。'.repeat(20)}最终结论`
    const call = taskCall({
      subagentTrail: [
        { kind: 'message', id: 'msg-1', content: '过程一', thinking: longThinking },
        { kind: 'message', id: 'msg-2', content: '过程二', thinking: '第二段思考' },
      ],
    })
    const blocks = buildSubagentTrailBlocks(call) as Array<{
      type: string
      thinking: { active: boolean; preview: string; content: string } | null
    }>
    expect(blocks[0]?.thinking?.active).toBe(false)
    expect(blocks[0]?.thinking?.preview).toBe('')
    expect(blocks[1]?.thinking).toMatchObject({ active: true, content: '第二段思考' })
    expect(blocks[1]?.thinking?.preview).toBe('第二段思考')

    // 父 task 结束后不再有 active 思考
    const settled = buildSubagentTrailBlocks(taskCall({
      status: 'completed',
      subagentTrail: [{ kind: 'message', id: 'msg-1', content: '过程', thinking: '思考' }],
    })) as Array<{ thinking: { active: boolean } | null }>
    expect(settled[0]?.thinking?.active).toBe(false)
  })

  it('内层工具耗时随 trail 进入工具卡（与主图 ToolTimingMiddleware 同口径）', () => {
    const blocks = buildSubagentTrailBlocks(taskCall({
      subagentTrail: [{
        kind: 'tool',
        id: 'inner-1',
        tool_name: 'calculator_tool',
        args: {},
        status: 'completed',
        result: '2',
        started_at_ms: 1_000,
        ended_at_ms: 1_250,
      }],
    }))
    const first = blocks[0]
    expect(first?.type).toBe('tool')
    expect((first as { toolCall: ToolCallInfo }).toolCall).toMatchObject({
      started_at_ms: 1_000,
      ended_at_ms: 1_250,
    })

    const messages = buildSubagentMessages(taskCall({
      subagentTrail: [{
        kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: {},
        status: 'completed', result: '2', started_at_ms: 1_000, ended_at_ms: 1_250,
      }],
    }))
    expect(messages[1]!.toolCalls?.[0]).toMatchObject({ started_at_ms: 1_000, ended_at_ms: 1_250 })
  })

  it('内层查询工具派生可渲染结果面板（renderId/语义参数），普通工具不派生', () => {
    const envelope = JSON.stringify({ type: 'query_result', status: 'succeeded', data: null, meta: { result_id: 'q1' } })
    const blocks = buildSubagentTrailBlocks(taskCall({
      subagentTrail: [
        {
          kind: 'tool',
          id: 'inner-query',
          tool_name: 'data_analyzing_tool',
          args: { action: 'execute_query' },
          status: 'completed',
          result: envelope,
        },
        { kind: 'tool', id: 'inner-calc', tool_name: 'calculator_tool', args: {}, status: 'completed', result: '2' },
      ],
    }))
    const [queryBlock, calcBlock] = blocks as Array<{ type: string; queryResultToolCall: ToolCallInfo | null }>
    expect(queryBlock?.queryResultToolCall).toMatchObject({ renderType: 'queryResult' })
    expect(queryBlock?.queryResultToolCall?.renderId).toBeTruthy()
    expect(calcBlock?.queryResultToolCall).toBeNull()
  })

  it('非 task / 无 trail 不算过程区', () => {
    const call = taskCall({
      subagentTrail: [
        { kind: 'tool', id: 'inner-1', tool_name: 'calculator_tool', args: {}, status: 'running' },
      ],
    })
    expect(hasSubagentTrail(call)).toBe(true)
    expect(hasSubagentTrail({ ...call, tool_name: 'calculator_tool' })).toBe(false)
    expect(hasSubagentTrail({ ...call, subagentTrail: [] })).toBe(false)
  })
})
