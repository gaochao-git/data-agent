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
    callbacks: { onResult?: () => void; onError?: (message: string) => void; onRunRestarted?: () => void | Promise<void> } = {},
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

  it('reports success when the done event is received', async () => {
    const onResult = vi.fn()
    const onError = vi.fn()
    await runStream(['event: done\ndata: [DONE]\n\n'], { onResult, onError })

    expect(onResult).toHaveBeenCalledOnce()
    expect(onError).not.toHaveBeenCalled()
  })

  it('reloads durable history when the backend reports a recovered run', async () => {
    const onRunRestarted = vi.fn()
    const onResult = vi.fn()

    await runStream([
      'event: run_restarted\ndata: {"run_id":"run-1","turn_id":"turn-1"}\n\n',
      'event: done\ndata: [DONE]\n\n',
    ], { onRunRestarted, onResult })

    expect(onRunRestarted).toHaveBeenCalledOnce()
    expect(onResult).toHaveBeenCalledOnce()
  })
})
