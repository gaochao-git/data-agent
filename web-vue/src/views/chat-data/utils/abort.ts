/**
 * AbortSignal 取消语义的公共工具。
 *
 * 轮询(useQueryTask.waitForTask / waitForDashboardTask)和 SSE 主流被取消时,
 * 统一抛出标准 AbortError;调用方用 isAbortError 识别后静默退出,
 * 避免"用户离开页面/主动停止"被误报成业务错误弹给前端。
 */

/** 创建与 fetch 请求中止行为一致的标准 AbortError */
export function createAbortError(): DOMException {
  return new DOMException('Aborted', 'AbortError')
}

/** 判断异常是否为取消导致的 AbortError(用户取消应静默退出,不算业务失败) */
export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/**
 * 可被 AbortSignal 中断的 sleep。
 * 取消时立即 reject,不再等满整个轮询间隔,保证组件卸载/停止后轮询尽快退出。
 */
export function abortableSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(createAbortError())
      return
    }
    let timer: number | undefined
    const onAbort = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      reject(createAbortError())
    }
    timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}
