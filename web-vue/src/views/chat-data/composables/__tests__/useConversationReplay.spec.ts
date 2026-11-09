import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { defineComponent, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { ElMessage } from 'element-plus'
import { useConversationReplay, type UseConversationReplayOptions } from '../useConversationReplay'
import type { Message } from '../../types'

vi.mock('element-plus', () => ({
  ElMessage: Object.assign(vi.fn(), { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}))

const userMessage = (id = 'u1', content = '第一条提问'): Message => ({ id, role: 'user', content } as Message)
const assistantMessage = (id = 'a1', content = '回答正文'): Message =>
  ({ id, role: 'assistant', content, segments: [{ type: 'text', content }], isStreaming: false } as Message)

type HostState = UseConversationReplayOptions

const createOptions = () => {
  const messages = ref<Message[]>([userMessage(), assistantMessage()])
  const chatContainerRef = ref<HTMLDivElement | null>(null)
  const isNearBottom = ref(true)
  const isGenerating = ref(false)
  const hasActiveRun = ref(false)
  const isLoadingHistory = ref(false)
  const hasMoreHistory = ref(false)
  const loadEarlierHistory = vi.fn(async () => {
    hasMoreHistory.value = false
  })
  const options: HostState = { messages, chatContainerRef, isNearBottom, isGenerating, hasActiveRun, isLoadingHistory, hasMoreHistory, loadEarlierHistory }
  return { options, messages, chatContainerRef, isNearBottom, isGenerating, hasActiveRun, isLoadingHistory, hasMoreHistory, loadEarlierHistory }
}

const mountReplay = (options: HostState) => {
  const captured: Record<string, unknown> = {}
  const Host = defineComponent({
    setup() {
      Object.assign(captured, useConversationReplay(options))
      return () => null
    },
  })
  mount(Host)
  return captured as ReturnType<typeof useConversationReplay>
}

describe('useConversationReplay', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.mocked(ElMessage.info).mockClear()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('运行中触发回放：提示且不进入待启动（不再静默）', () => {
    const state = createOptions()
    state.isGenerating.value = true
    const replay = mountReplay(state.options)
    replay.requestReplay()
    expect(ElMessage.info).toHaveBeenCalledTimes(1)
    expect(replay.replayArmed.value).toBe(false)
    expect(replay.replayGroup.value).toBeNull()
    expect(replay.replayPlayer.status.value).toBe('idle')
  })

  it('requestReplay 进入待启动（armed）而非直接开演；回车 startReplay 才开演', async () => {
    const state = createOptions()
    const replay = mountReplay(state.options)
    // 未 armed 时 startReplay 不生效
    replay.startReplay()
    expect(replay.replayPlayer.status.value).toBe('idle')
    replay.requestReplay()
    expect(replay.replayArmed.value).toBe(true)
    expect(replay.replayPlayer.status.value).toBe('idle')
    // 种子文本 = 第一条用户提问（宿主预填输入框用）
    expect(replay.replaySeedText.value).toBe('第一条提问')
    // armed 后回车启动
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(replay.replayArmed.value).toBe(false)
    expect(replay.replayGroup.value?.length).toBe(2)
    expect(replay.replayPlayer.status.value).toBe('playing')
  })

  it('hasMoreHistory 时预拉全量后再开演（长会话不只演最近一页）', async () => {
    const state = createOptions()
    state.messages.value = [userMessage(), assistantMessage()]
    let pages = 0
    state.loadEarlierHistory.mockImplementation(async () => {
      pages += 1
      if (pages >= 3) state.hasMoreHistory.value = false
    })
    state.options.hasMoreHistory.value = true
    const replay = mountReplay(state.options)
    replay.requestReplay()
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(state.loadEarlierHistory).toHaveBeenCalledTimes(3)
    expect(replay.replayGroup.value?.length).toBe(2)
    expect(replay.replayPlayer.status.value).toBe('playing')
  })

  it('空会话：提示且不进入待启动（不再静默）', () => {
    const state = createOptions()
    state.messages.value = []
    const replay = mountReplay(state.options)
    replay.requestReplay()
    expect(ElMessage.info).toHaveBeenCalledWith('暂无可回放的历史消息')
    expect(replay.replayArmed.value).toBe(false)
    expect(replay.replayGroup.value).toBeNull()
    expect(replay.replayPlayer.status.value).toBe('idle')
  })

  it('pending 消费不泄漏：空会话加载完成后标记被清，不会突然进入待启动', async () => {
    const state = createOptions()
    state.messages.value = []
    state.isLoadingHistory.value = true
    const replay = mountReplay(state.options)
    replay.requestReplay()
    expect(ElMessage.info).not.toHaveBeenCalled()
    // 历史加载完成但会话为空：pending 必须被消费掉
    state.isLoadingHistory.value = false
    await nextTick()
    expect(ElMessage.info).toHaveBeenCalledWith('暂无可回放的历史消息')
    expect(replay.replayArmed.value).toBe(false)
    expect(replay.replayGroup.value).toBeNull()
    // 之后消息到位、再翻转一次加载状态：不再自动进入待启动（标记已消费）
    state.messages.value = [userMessage(), assistantMessage()]
    state.isLoadingHistory.value = true
    state.isLoadingHistory.value = false
    await nextTick()
    expect(replay.replayArmed.value).toBe(false)
    expect(replay.replayPlayer.status.value).toBe('idle')
  })

  it('pending 正常路径：历史加载完成后进入待启动，回车才开演', async () => {
    const state = createOptions()
    state.isLoadingHistory.value = true
    const replay = mountReplay(state.options)
    replay.requestReplay()
    expect(replay.replayPlayer.status.value).toBe('idle')
    state.isLoadingHistory.value = false
    await nextTick()
    expect(replay.replayArmed.value).toBe(true)
    expect(replay.replayPlayer.status.value).toBe('idle')
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(replay.replayPlayer.status.value).toBe('playing')
  })

  it('柔性跟随：仅当用户本就在底部才拽底，上翻即停', async () => {
    const state = createOptions()
    const fakeEl = { scrollTop: 0, scrollHeight: 5000 } as HTMLDivElement
    state.chatContainerRef.value = fakeEl
    const replay = mountReplay(state.options)
    replay.requestReplay()
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTime(600)
    expect(fakeEl.scrollTop).toBeGreaterThan(0) // 底部时跟随
    // 用户上翻：停止跟随
    state.isNearBottom.value = false
    fakeEl.scrollTop = 100
    await vi.advanceTimersByTime(2000)
    expect(fakeEl.scrollTop).toBe(100)
  })

  it('Esc 取消待启动：armed 被清、不开演', async () => {
    const state = createOptions()
    const replay = mountReplay(state.options)
    replay.requestReplay()
    expect(replay.replayArmed.value).toBe(true)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(replay.replayArmed.value).toBe(false)
    expect(replay.replayPlayer.status.value).toBe('idle')
  })

  it('Esc 退出回放', async () => {
    const state = createOptions()
    const replay = mountReplay(state.options)
    replay.requestReplay()
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(replay.replayGroup.value).not.toBeNull()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(replay.replayGroup.value).toBeNull()
    expect(replay.replayPlayer.status.value).toBe('idle')
  })
})
