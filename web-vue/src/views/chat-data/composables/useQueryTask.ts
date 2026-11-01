import { createQueryTask, type QueryTaskStatusResult } from '../api'
import { waitForQueryTask } from '../utils/queryTaskPolling'

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
    // 查询超时由后端 executor 层控制；前端只负责轮询到终态（表预览/看板共享同一实现）
    return waitForQueryTask(taskId, {
      pollIntervalMs: options.pollIntervalMs,
      returnTerminalTask: options.returnTerminalTask,
      onProgress: options.onProgress,
      signal: options.signal,
    })
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
