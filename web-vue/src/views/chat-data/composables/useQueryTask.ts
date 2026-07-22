import { cancelQueryTask, createQueryTask, getQueryTask, type QueryTaskStatusResult } from '../api'

const DEFAULT_TIMEOUT_MS = 15 * 60 * 1000
const DEFAULT_POLL_INTERVAL_MS = 1500

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))

interface SubmitSqlTaskParams {
  sql: string[]
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
  timeoutMs?: number
  returnTerminalTask?: boolean
  onProgress?: (task: QueryTaskStatusResult) => void
}

export function useQueryTask() {
  const waitForTask = async (taskId: string, options: WaitTaskOptions = {}) => {
    const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
    const startedAt = Date.now()

    while (true) {
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
      if (Date.now() - startedAt > timeoutMs) {
        try {
          await cancelQueryTask(taskId)
        } catch {
          // 取消失败不覆盖超时提示，任务仍可从查询历史中手动停止。
        }
        throw new Error('任务执行超时，已提交停止请求')
      }

      await sleep(Math.max(500, pollIntervalMs))
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
