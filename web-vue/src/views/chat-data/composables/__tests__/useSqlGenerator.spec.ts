import { describe, expect, it, vi } from 'vitest'

import { useSqlGenerator } from '../useSqlGenerator'

const conversationId = 'conversation-1'

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  }), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('useSqlGenerator', () => {
  const runStream = async (
    chunks: string[],
    callbacks: {
      onResult?: () => void
      onError?: (message: string) => void
      onRunRestarted?: () => void | Promise<void>
      onRunCompleted?: (info: { status: string; nextRunId?: string | null }) => void
    } = {},
  ) => {
    const { generateSqlStream } = useSqlGenerator()
    await generateSqlStream(
      '1+1',
      true,
      null,
      conversationId,
      {
        mode: 'query',
        onResult: callbacks.onResult,
        onError: callbacks.onError,
        agentId: 'chat_data',
        responseFactory: async () => streamResponse(chunks),
        onRunRestarted: callbacks.onRunRestarted,
        onRunCompleted: callbacks.onRunCompleted,
      },
    )
  }

  it('only reports success after an explicit done event', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    await runStream(['event: message\ndata: {"content":"2"}\n\n'], { onResult, onError })

    expect(onResult).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledWith('流式连接意外中断，请重新进入会话继续接收')
  })

  it('does not treat legacy [DONE] as success', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    await runStream(['event: done\ndata: [DONE]\n\n'], { onResult, onError })

    // legacy 结束信号不可推断业务成功，成功回调不应触发。
    expect(onResult).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('reports success only for structured finished done', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    const onRunCompleted = vi.fn()
    await runStream([
      'event: message\ndata: {"content":"2"}\n\n',
      'event: done\ndata: {"status":"finished","next_run_id":"run-next"}\n\n',
    ], { onResult, onError, onRunCompleted })

    expect(onResult).toHaveBeenCalledOnce()
    expect(onError).not.toHaveBeenCalled()
    expect(onRunCompleted).toHaveBeenCalledWith({ status: 'finished', nextRunId: 'run-next' })
  })

  it('keeps reading until structured done after an error event', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    const onRunCompleted = vi.fn()
    await runStream([
      'event: message\ndata: {"content":"partial"}\n\n',
      'event: error\ndata: {"error":"智能问数执行失败"}\n\n',
      'event: done\ndata: {"status":"failed"}\n\n',
    ], { onResult, onError, onRunCompleted })

    // 终态通知先于错误回调：停止流程靠 onRunCompleted 解锁。
    expect(onRunCompleted).toHaveBeenCalledWith({ status: 'failed', nextRunId: null })
    expect(onError).toHaveBeenCalledWith('智能问数执行失败')
    expect(onResult).not.toHaveBeenCalled()
  })

  it('does not report success for cancelled done', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    const onRunCompleted = vi.fn()
    await runStream(['event: done\ndata: {"status":"cancelled"}\n\n'], { onResult, onError, onRunCompleted })

    expect(onRunCompleted).toHaveBeenCalledWith({ status: 'cancelled', nextRunId: null })
    expect(onResult).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('does not report success for waiting_user done', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    const onRunCompleted = vi.fn()
    await runStream([
      'event: question_requested\ndata: {"question_id":"q1","questions":[]}\n\n',
      'event: done\ndata: {"status":"waiting_user"}\n\n',
    ], { onResult, onError, onRunCompleted })

    expect(onRunCompleted).toHaveBeenCalledWith({ status: 'waiting_user', nextRunId: null })
    expect(onResult).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
  })

  it('surfaces the recorded business error when the stream ends without done', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    await runStream(['event: error\ndata: {"error":"数据源连接超时"}\n\n'], { onResult, onError })

    expect(onError).toHaveBeenCalledWith('数据源连接超时')
    expect(onResult).not.toHaveBeenCalled()
  })

  it('reloads durable history when the backend reports a recovered run', async () => {
    const onRunRestarted = vi.fn()
    const onResult = vi.fn()

    await runStream([
      'event: run_restarted\ndata: {"run_id":"run-1","turn_id":"turn-1"}\n\n',
      'event: done\ndata: {"status":"finished"}\n\n',
    ], { onRunRestarted, onResult })

    expect(onRunRestarted).toHaveBeenCalledOnce()
    expect(onResult).toHaveBeenCalledOnce()
  })
})

describe('useSqlGenerator tool event dedup', () => {
  it('does not let subagent inner lifecycle events suppress top-level tool events', async () => {
    const onRunningTool = vi.fn()
    const onToolLifecycle = vi.fn()
    const { generateSqlStream } = useSqlGenerator()
    await generateSqlStream('1+1', true, null, conversationId, {
      mode: 'query',
      agentId: 'chat_data',
      responseFactory: async () => streamResponse([
        // 内层工具生命周期事件（带 parent_tool_call_id）：走 trail 渲染
        'event: tool_started\ndata: {"tool_call_id":"inner-1","tool_name":"calculator_tool","parent_tool_call_id":"task-1"}\n\n',
        // 同 id 的顶层 tool 事件必须照常分发——内层 id 不得进去重集合
        'event: tool\ndata: {"tool_call_id":"inner-1","tool_name":"calculator_tool","args":{},"status":"running"}\n\n',
        // 顶层生命周期事件 → 同 id 的顶层 tool 事件被去重跳过
        'event: tool_started\ndata: {"tool_call_id":"top-1","tool_name":"file_read"}\n\n',
        'event: tool\ndata: {"tool_call_id":"top-1","tool_name":"file_read","args":{},"status":"running"}\n\n',
      ]),
      onRunningTool,
      onToolLifecycle,
    })

    expect(onToolLifecycle).toHaveBeenCalledTimes(2)
    expect(onRunningTool).toHaveBeenCalledTimes(1)
    expect(onRunningTool).toHaveBeenCalledWith(expect.objectContaining({ tool_call_id: 'inner-1' }))
  })
})
