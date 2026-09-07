import { createQueryTask, getQueryTask, type QueryTaskStatusResult } from '../api'
import { abortableSleep, createAbortError } from '../utils/abort'

const DEFAULT_POLL_INTERVAL_MS = 1500

interface SubmitSqlTaskParams {
  sql?: string[]
  datasource_id: string
  member_datasource_ids?: string[]
  limit?: number
  timeout?: number
  query_type?: string
  builder_config?: Record<string, unknown>
  semantic_builder_config?: Record<string, unknown>
  semantic_config_id?: string
  query_text?: string
  skip_query_cache?: boolean
}

interface WaitTaskOptions {
  pollIntervalMs?: number
  returnTerminalTask?: boolean
  onProgress?: (task: QueryTaskStatusResult) => void
  /** 取消信号:中止后抛出 AbortError 并停止轮询,调用方应静默退出(如组件卸载) */
  signal?: AbortSignal
}

export function useQueryTask() {
  const waitForTask = async (taskId: string, options: WaitTaskOptions = {}) => {
    const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS
    const signal = options.signal

    // 查询超时由后端 executor 层控制（MySQL/Hive 各有默认超时，可配置）。
    // 前端只负责轮询到终态，不主动停止任务；长任务可在查询历史中手动停止。
    while (true) {
      // 每轮发起查询前先响应取消:调用方已中止就不再发起下一次状态查询
      if (signal?.aborted) {
        throw createAbortError()
      }
      const response = await getQueryTask(taskId)
      const task = response.data
      if (!task) {
        throw new Error('任务不存在')
      }
      options.onProgress?.(task)

      if (task.status === 'succeeded' || task.status === 'partial_success') {
        return task
      }
      if (task.status === 'cancelled') {
        if (options.returnTerminalTask) return task
        throw new Error(task.error_message || '已人工取消')
      }
      if (task.status === 'failed') {
        if (options.returnTerminalTask) return task
        throw new Error(task.error_message || '任务执行失败')
      }

      // sleep 可被取消信号打断,卸载后不再空等一个完整轮询间隔
      await abortableSleep(Math.max(500, pollIntervalMs), signal)
    }
  }

  const submitSqlTask = async (params: SubmitSqlTaskParams, options: WaitTaskOptions = {}) => {
    const createResp = await createQueryTask(params)
    const taskInfo = createResp.data
    if (!taskInfo?.task_id) {
      throw new Error(createResp.msg || '任务创建失败')
    }
    return waitForTask(taskInfo.task_id, {
      ...options,
      pollIntervalMs: options.pollIntervalMs ?? taskInfo.poll_interval_ms ?? DEFAULT_POLL_INTERVAL_MS,
    })
  }

  return {
    waitForTask,
    submitSqlTask,
  }
}
