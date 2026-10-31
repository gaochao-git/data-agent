import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { formatToolDuration } from '../utils/toolDisplay'

/** 运行中耗时的刷新间隔：按秒即可，工具耗时展示本身不要求毫秒级精度。 */
const TICK_MS = 1000

/**
 * 工具/子智能体耗时展示（主对话工具卡 / 工具详情 / 子智能体面板共用）：
 * - 已完成：后端 ToolTimingMiddleware 盖章的起止时间差（唯一权威口径）；
 * - 进行中：后端 started_at_ms 到当前时间的已运行时长，每秒刷新；结束后立即切到精确总耗时。
 *
 * 起点与终点都来自后端，前端只做「按秒刷新显示」，不自行发明开始时间；
 * 浏览器时钟落后于服务端（now < started）时按 0 处理，避免出现负值/离谱数字。
 */
export const useToolTiming = (
  getStartedAtMs: () => number | null | undefined,
  getEndedAtMs: () => number | null | undefined,
) => {
  const nowMs = ref(Date.now())
  let timer: ReturnType<typeof setInterval> | null = null

  const stop = () => {
    if (timer === null) return
    clearInterval(timer)
    timer = null
  }

  const syncTicker = () => {
    const started = getStartedAtMs()
    const ended = getEndedAtMs()
    if (!started || ended) {
      stop()
      nowMs.value = Date.now()
      return
    }
    if (timer === null) {
      nowMs.value = Date.now()
      timer = setInterval(() => { nowMs.value = Date.now() }, TICK_MS)
    }
  }

  watch(() => [getStartedAtMs(), getEndedAtMs()], syncTicker, { immediate: true })
  onBeforeUnmount(stop)

  const isRunning = computed(() => Boolean(getStartedAtMs()) && !getEndedAtMs())

  /** 毫秒口径（给主对话同款「用时 X」状态行/footer 用） */
  const elapsedMs = computed<number | null>(() => {
    const started = getStartedAtMs()
    const ended = getEndedAtMs()
    if (!started) return null
    if (ended && ended >= started) return ended - started
    return Math.max(nowMs.value - started, 0)
  })

  const durationText = computed(() => {
    const started = getStartedAtMs()
    const ended = getEndedAtMs()
    if (!started) return ''
    if (ended && ended >= started) return formatToolDuration(started, ended)
    const end = Math.max(nowMs.value, started)
    return formatToolDuration(started, end)
  })

  return { durationText, elapsedMs, isRunning }
}
