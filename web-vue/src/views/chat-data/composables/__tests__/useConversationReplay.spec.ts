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
  const conversationId = ref<string | null | undefined>('conv-1')
  const loadEarlierHistory = vi.fn(async (): Promise<'loaded' | 'failed' | 'skipped'> => {
    hasMoreHistory.value = false
    return 'loaded'
  })
  const options: HostState = { messages, chatContainerRef, isNearBottom, isGenerating, hasActiveRun, isLoadingHistory, hasMoreHistory, conversationId, loadEarlierHistory }
  return { options, messages, chatContainerRef, isNearBottom, isGenerating, hasActiveRun, isLoadingHistory, hasMoreHistory, conversationId, loadEarlierHistory }
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
    vi.mocked(ElMessage.error).mockClear()
    vi.mocked(ElMessage.warning).mockClear()
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
      return 'loaded'
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

  it('预拉单页失败：立即中止回放并提示，不空转打满护栏页数', async () => {
    const state = createOptions()
    state.hasMoreHistory.value = true
    // 失败不翻转 hasMoreHistory（与真实 loadEarlierHistory 的失败路径一致）
    state.loadEarlierHistory.mockImplementation(async () => 'failed')
    const replay = mountReplay(state.options)
    replay.requestReplay()
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(state.loadEarlierHistory).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledWith('历史消息加载失败，回放已取消，请稍后重试')
    expect(replay.replayGroup.value).toBeNull()
    expect(replay.replayPlayer.status.value).toBe('idle')
  })

  it('预拉返回 skipped（会话切换/并发跳过）：静默取消，不误报网络失败', async () => {
    const state = createOptions()
    state.hasMoreHistory.value = true
    state.loadEarlierHistory.mockImplementation(async () => 'skipped')
    const replay = mountReplay(state.options)
    replay.requestReplay()
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(ElMessage.info).not.toHaveBeenCalled()
    expect(replay.replayGroup.value).toBeNull()
    expect(replay.replayPlayer.status.value).toBe('idle')
  })

  it('预拉期间用户发送新消息：开演前取消回放，不收集流式中间态', async () => {
    const state = createOptions()
    state.hasMoreHistory.value = true
    // 模拟预拉过程中用户点了发送：loadEarlierHistory 执行时直播已在生成
    state.loadEarlierHistory.mockImplementation(async () => {
      state.isGenerating.value = true
      state.hasMoreHistory.value = false
      return 'loaded'
    })
    const replay = mountReplay(state.options)
    replay.requestReplay()
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(ElMessage.info).toHaveBeenCalledWith('有新消息正在发送，回放已取消，请稍后重试')
    expect(replay.replayGroup.value).toBeNull()
    expect(replay.replayPlayer.status.value).toBe('idle')
  })

  it('会话切换丢弃 armed 待启动（本地新建会话不重挂载时 armed 会残留）', async () => {
    const state = createOptions()
    const replay = mountReplay(state.options)
    replay.requestReplay()
    expect(replay.replayArmed.value).toBe(true)
    state.conversationId.value = 'conv-2'
    await nextTick()
    expect(replay.replayArmed.value).toBe(false)
    // 回车不再触发回放
    replay.startReplay()
    await vi.advanceTimersByTimeAsync(0)
    expect(replay.replayPlayer.status.value).toBe('idle')
    expect(replay.replayGroup.value).toBeNull()
  })
})
