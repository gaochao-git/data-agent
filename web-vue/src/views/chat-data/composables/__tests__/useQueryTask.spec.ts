import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createAbortError } from '../../utils/abort'

// 只 mock 用到的查询任务接口,避免拉起完整 api 模块(axios 等副作用)
vi.mock('../../api', () => ({
  createQueryTask: vi.fn(),
}))
vi.mock('../../api/query', () => ({
  getQueryTask: vi.fn(),
}))

import { getQueryTask } from '../../api/query'
import { useQueryTask } from '../useQueryTask'

const mockedGetQueryTask = vi.mocked(getQueryTask)

describe('useQueryTask waitForTask abort', () => {
  beforeEach(() => {
    mockedGetQueryTask.mockReset()
  })

  it('resolves when the task reaches a terminal success status', async () => {
    mockedGetQueryTask.mockResolvedValue({
      data: { task_id: 't1', status: 'succeeded' },
    } as never)

    const { waitForTask } = useQueryTask()
    const task = await waitForTask('t1')

    expect(task.status).toBe('succeeded')
    expect(mockedGetQueryTask).toHaveBeenCalledTimes(1)
  })

  it('throws a recognizable AbortError without querying when the signal is already aborted', async () => {
    const controller = new AbortController()
    controller.abort()

    const { waitForTask } = useQueryTask()
    await expect(
      waitForTask('t1', { signal: controller.signal }),
    ).rejects.toMatchObject({ name: 'AbortError' })
    expect(mockedGetQueryTask).not.toHaveBeenCalled()
  })

  it('stops polling immediately when the signal aborts mid-wait', async () => {
    const controller = new AbortController()
    mockedGetQueryTask.mockResolvedValue({
      data: { task_id: 't1', status: 'running' },
    } as never)

    const { waitForTask } = useQueryTask()
    const promise = waitForTask('t1', { pollIntervalMs: 5000, signal: controller.signal })

    // 等待轮询进入 sleep 阶段后再取消,验证 sleep 被中断且不再发起下一次查询
    await vi.waitFor(() => expect(mockedGetQueryTask).toHaveBeenCalledTimes(1))
    controller.abort()

    await expect(promise).rejects.toMatchObject({ name: 'AbortError' })
    expect(mockedGetQueryTask).toHaveBeenCalledTimes(1)
  })

  it('rejects with the task error message on failed status', async () => {
    mockedGetQueryTask.mockResolvedValue({
      data: { task_id: 't1', status: 'failed', error_message: 'SQL执行失败' },
    } as never)

    const { waitForTask } = useQueryTask()
    await expect(waitForTask('t1')).rejects.toThrow('SQL执行失败')
  })

  it('createAbortError produces a standard AbortError detectable via instanceof', () => {
    const err = createAbortError()
    expect(err).toBeInstanceOf(DOMException)
    expect(err.name).toBe('AbortError')
  })
})
