import { defineComponent, h, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getSubagentTaskTrail, streamSubagentTaskEvents } from '../../api'
import { useSubagentTaskStream } from '../useSubagentTaskStream'

vi.mock('../../api', () => ({
  getSubagentTaskTrail: vi.fn(),
  streamSubagentTaskEvents: vi.fn(),
}))

const emptyStream = () => Promise.resolve(new Response(''))

const mountStream = () => {
  const taskId = ref<string | null>('task-1')
  const conversationId = ref<string | null>('conv-1')
  const isActive = ref(true)
  let stream: ReturnType<typeof useSubagentTaskStream> | null = null
  const Host = defineComponent({
    setup() {
      stream = useSubagentTaskStream(
        () => taskId.value,
        () => conversationId.value,
        () => isActive.value,
      )
      return () => h('div')
    },
  })
  const wrapper = mount(Host)
  return { wrapper, stream: stream!, taskId, conversationId, isActive }
}

describe('useSubagentTaskStream 检查点快照', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(getSubagentTaskTrail).mockReset()
    vi.mocked(streamSubagentTaskEvents).mockReset().mockImplementation(emptyStream)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('首连先渲染快照，并从快照游标之后续传', async () => {
    vi.mocked(getSubagentTaskTrail).mockResolvedValue({
      available: true,
      cursor: '12-0',
      trail: [{ kind: 'message', id: 'ai-1', content: '已完成的分析过程' }],
      compactions: [],
    })

    const { wrapper, stream } = mountStream()
    await flushPromises()
    await nextTick()

    expect(getSubagentTaskTrail).toHaveBeenCalledWith('conv-1', 'task-1')
    expect(stream.trail.value).toEqual([{ kind: 'message', id: 'ai-1', content: '已完成的分析过程' }])
    expect(streamSubagentTaskEvents).toHaveBeenCalledWith('conv-1', 'task-1', '12-0', expect.any(AbortSignal))
    wrapper.unmount()
  })

  it('快照不可用时保持 0-0 全量重放兜底', async () => {
    vi.mocked(getSubagentTaskTrail).mockResolvedValue({ available: false })

    const { wrapper } = mountStream()
    await flushPromises()
    await nextTick()

    expect(streamSubagentTaskEvents).toHaveBeenCalledWith('conv-1', 'task-1', '0-0', expect.any(AbortSignal))
    wrapper.unmount()
  })

  it('快照请求失败也不阻断实时流', async () => {
    vi.mocked(getSubagentTaskTrail).mockRejectedValue(new Error('network'))

    const { wrapper } = mountStream()
    await flushPromises()
    await nextTick()

    expect(streamSubagentTaskEvents).toHaveBeenCalledWith('conv-1', 'task-1', '0-0', expect.any(AbortSignal))
    wrapper.unmount()
  })

  it('检查点领先事件流时，重放的增量不会重复追加到快照正文', async () => {
    // 快照已含完整消息，但 Redis 事件泵滞后：从游标续传会重放同一条消息的
    // token 增量与完整帧。增量必须被丢弃，否则正文翻倍且完整帧按「更长者优先」
    // 反而比拼错的内容更短、无法纠正。
    vi.mocked(getSubagentTaskTrail).mockResolvedValue({
      available: true,
      cursor: '12-0',
      trail: [{ kind: 'message', id: 'ai-1', content: '完整正文', thinking: '完整思考' }],
      compactions: [],
    })
    const frames = [
      'id: 13-0\nevent: subagent_message_delta\ndata: ' + JSON.stringify({ message_id: 'ai-1', delta: '完整正文' }) + '\n\n',
      'id: 14-0\nevent: subagent_thinking_delta\ndata: ' + JSON.stringify({ message_id: 'ai-1', delta: '完整思考' }) + '\n\n',
      'id: 15-0\nevent: subagent_message\ndata: ' + JSON.stringify({ message_id: 'ai-1', content: '完整正文', streamed: true, thinking: '完整思考' }) + '\n\n',
    ].join('')
    vi.mocked(streamSubagentTaskEvents).mockImplementation(() => Promise.resolve(new Response(frames)))

    const { wrapper, stream } = mountStream()
    await flushPromises()
    await nextTick()

    expect(stream.trail.value).toEqual([
      { kind: 'message', id: 'ai-1', content: '完整正文', thinking: '完整思考', truncated: false, thinkingTruncated: false },
    ])
    wrapper.unmount()
  })
})
