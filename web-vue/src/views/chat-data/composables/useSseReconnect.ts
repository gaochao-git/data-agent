import { ref, type Ref } from 'vue'

export const SSE_ACTIVITY_TIMEOUT_MS = 15_000
export const SSE_RECONNECT_INTERVAL_MS = 5_000
export const SSE_RECONNECT_MAX_MS = 600_000

export type SseReconnectOptions = {
  isGenerating: Ref<boolean>
  hasActiveRun: Ref<boolean>
  hasRunId?: Ref<string | null>
  stopGeneration: () => void
  recover: () => Promise<void>
  onUnknown: () => void
}

export type SseReconnectPhase = 'idle' | 'reconnecting' | 'unknown'
export type SseReconnectState = {
  phase: SseReconnectPhase
  attempt: number
  retryDelayMs: number
  reason: string | null
}

/** 网络/代理断流后的有界重连：只重新订阅同一 Run，不创建新任务。 */
export function useSseReconnect(options: SseReconnectOptions) {
  const state = ref<SseReconnectState>({ phase: 'idle', attempt: 0, retryDelayMs: 0, reason: null })
  let timer: number | null = null
  let lastActivityAt = 0
  let reconnectStartedAt = 0

  const stop = () => {
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
    state.value = { phase: 'idle', attempt: 0, retryDelayMs: 0, reason: null }
  }

  const markActivity = () => {
    lastActivityAt = Date.now()
    if (options.isGenerating.value && state.value.phase === 'reconnecting') {
      state.value = { ...state.value, phase: 'idle' }
    }
  }

  const signalDisconnect = () => {
    if (!options.hasActiveRun.value && !options.hasRunId?.value) return
    if (state.value.phase !== 'reconnecting') {
      reconnectStartedAt = Date.now()
      state.value = { phase: 'reconnecting', attempt: 0, retryDelayMs: SSE_RECONNECT_INTERVAL_MS, reason: '连接中断' }
    }
    lastActivityAt = 0
  }

  const start = () => {
    if (timer !== null) return
    reconnectStartedAt = 0
    lastActivityAt = Date.now()
    state.value = { phase: 'idle', attempt: 0, retryDelayMs: 0, reason: null }
    timer = window.setInterval(() => {
      if (!options.hasActiveRun.value && !options.hasRunId?.value) {
        stop()
        return
      }
      if (options.isGenerating.value && Date.now() - lastActivityAt < SSE_ACTIVITY_TIMEOUT_MS) return
      if (state.value.phase === 'idle') {
        reconnectStartedAt = Date.now()
        state.value = { phase: 'reconnecting', attempt: 0, retryDelayMs: SSE_RECONNECT_INTERVAL_MS, reason: '连接中断' }
      }
      if (Date.now() - reconnectStartedAt >= SSE_RECONNECT_MAX_MS) {
        stop()
        state.value = { ...state.value, phase: 'unknown' }
        options.onUnknown()
        return
      }
      state.value = {
        ...state.value,
        attempt: state.value.attempt + 1,
        retryDelayMs: SSE_RECONNECT_INTERVAL_MS,
        reason: '连接中断',
      }
      if (options.isGenerating.value) options.stopGeneration()
      void options.recover()
    }, SSE_RECONNECT_INTERVAL_MS)
  }

  return { start, stop, markActivity, signalDisconnect, state }
}
