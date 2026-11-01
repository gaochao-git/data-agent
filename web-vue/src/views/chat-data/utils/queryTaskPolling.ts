import { getQueryTask, type QueryTaskStatusResult } from '../api/query'
import { abortableSleep, createAbortError } from './abort'

/** 查询任务轮询的唯一实现：dashboard / useQueryTask / 表预览共用。 */
export interface WaitQueryTaskOptions {
  pollIntervalMs?: number
  onProgress?: (task: QueryTaskStatusResult) => void
  /** 取消信号：中止后抛 AbortError 并停止轮询，调用方应静默退出 */
  signal?: AbortSignal
  /** failed/cancelled 时是否返回终态任务而不是抛错 */
  returnTerminalTask?: boolean
  /** 前端兜底超时（毫秒）；不传则由后端执行层控制超时 */
  timeoutMs?: number
}

const DEFAULT_POLL_INTERVAL_MS = 1500

export const waitForQueryTask = async (
  taskId: string,
  options: WaitQueryTaskOptions = {},
): Promise<QueryTaskStatusResult> => {
  const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS
  const signal = options.signal
  const deadline = typeof options.timeoutMs === 'number' ? Date.now() + options.timeoutMs : null
  while (true) {
    if (signal?.aborted) throw createAbortError()
    if (deadline !== null && Date.now() > deadline) {
      throw new Error('任务执行超时，请稍后重试')
    }
    const response = await getQueryTask(taskId)
    const task = response.data
    if (!task) throw new Error(response.msg || '任务不存在')
    options.onProgress?.(task)
    if (task.status === 'succeeded' || task.status === 'partial_success') return task
    if (task.status === 'failed' || task.status === 'cancelled') {
      if (options.returnTerminalTask) return task
      throw new Error(task.error_message || (task.status === 'cancelled' ? '已人工取消' : '任务执行失败'))
    }
    // sleep 可被取消信号打断，卸载后不再空等一个完整轮询间隔
    await abortableSleep(Math.max(500, pollIntervalMs), signal)
  }
}
