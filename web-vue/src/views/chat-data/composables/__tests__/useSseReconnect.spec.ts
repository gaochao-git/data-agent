import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  SSE_ACTIVITY_TIMEOUT_MS,
  SSE_RECONNECT_INTERVAL_MS,
  SSE_RECONNECT_MAX_MS,
  useSseReconnect,
} from '../useSseReconnect'

describe('useSseReconnect', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('断流后重连同一 Run，并在有活动时停止计时', async () => {
    const isGenerating = ref(false)
    const hasActiveRun = ref(true)
    const stopGeneration = vi.fn()
    const recover = vi.fn().mockResolvedValue(undefined)
    const reconnect = useSseReconnect({ isGenerating, hasActiveRun, stopGeneration, recover, onUnknown: vi.fn() })

    reconnect.start()
    await vi.advanceTimersByTimeAsync(SSE_RECONNECT_INTERVAL_MS)
    expect(stopGeneration).not.toHaveBeenCalled()
    expect(recover).toHaveBeenCalledOnce()

    isGenerating.value = true
    reconnect.markActivity()
    await vi.advanceTimersByTimeAsync(SSE_ACTIVITY_TIMEOUT_MS + SSE_RECONNECT_INTERVAL_MS)
    expect(recover).toHaveBeenCalledOnce()
    reconnect.stop()
  })

  it('重连超过 600 秒后进入 unknown，不无限重试', async () => {
    const isGenerating = ref(false)
    const hasActiveRun = ref(true)
    const recover = vi.fn().mockResolvedValue(undefined)
    const onUnknown = vi.fn()
    const reconnect = useSseReconnect({ isGenerating, hasActiveRun, stopGeneration: vi.fn(), recover, onUnknown })

    reconnect.start()
    await vi.advanceTimersByTimeAsync(SSE_RECONNECT_MAX_MS + SSE_RECONNECT_INTERVAL_MS)

    expect(onUnknown).toHaveBeenCalledOnce()
    const calls = recover.mock.calls.length
    await vi.advanceTimersByTimeAsync(SSE_RECONNECT_INTERVAL_MS)
    expect(recover).toHaveBeenCalledTimes(calls)
    reconnect.stop()
  })
})
