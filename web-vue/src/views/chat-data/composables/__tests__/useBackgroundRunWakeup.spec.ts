import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  BACKGROUND_RUN_WAKE_DELAYS_MS,
  useBackgroundRunWakeup,
} from '../useBackgroundRunWakeup'
import type { SubagentTaskRecord } from '../../types'

const LAST_WAKE_DELAY = BACKGROUND_RUN_WAKE_DELAYS_MS[BACKGROUND_RUN_WAKE_DELAYS_MS.length - 1] ?? 9_000

const task = (status: SubagentTaskRecord['status']): SubagentTaskRecord => ({
  task_id: 'task-1',
  conversation_id: 'conv-1',
  parent_run_id: 'run-parent',
  parent_tool_call_id: 'call-1',
  subagent_type: 'explore',
  label: '入金趋势',
  description: '分析近 30 天入金趋势',
  status,
  status_label: status,
  progress: {},
  created_at: '2026-11-15T10:00:00',
  updated_at: '2026-11-15T10:00:00',
  started_at: '2026-11-15T10:00:01',
  finished_at: null,
  notified_at: null,
  timeout_seconds: 60,
})

const mountWakeup = () => {
  const conversationId = ref<string | null>('conv-1')
  const tasks = ref<SubagentTaskRecord[]>([task('running')])
  const isGenerating = ref(false)
  const hasActiveRun = ref(false)
  const recover = vi.fn().mockResolvedValue(undefined)
  const Host = defineComponent({
    setup() {
      useBackgroundRunWakeup({ conversationId, tasks, isGenerating, hasActiveRun, recover })
      return () => h('div')
    },
  })
  const wrapper = mount(Host)
  return { wrapper, conversationId, tasks, isGenerating, hasActiveRun, recover }
}

describe('useBackgroundRunWakeup', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('任务从执行中收口后，在通知 Run 创建窗口内自动探测恢复', async () => {
    const { wrapper, tasks, recover } = mountWakeup()

    tasks.value = [task('completed')]
    await nextTick()
    expect(recover).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(BACKGROUND_RUN_WAKE_DELAYS_MS[0])
    expect(recover).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('接上活跃 Run 后停止后续重试，不再重复订阅', async () => {
    const { wrapper, tasks, hasActiveRun, recover } = mountWakeup()
    recover.mockImplementation(async () => { hasActiveRun.value = true })

    tasks.value = [task('completed')]
    await nextTick()
    await vi.advanceTimersByTimeAsync(LAST_WAKE_DELAY + 1)

    expect(recover).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('主对话正在生成时不抢占当前订阅', async () => {
    const { wrapper, tasks, isGenerating, recover } = mountWakeup()
    isGenerating.value = true

    tasks.value = [task('completed')]
    await nextTick()
    await vi.advanceTimersByTimeAsync(LAST_WAKE_DELAY + 1)

    expect(recover).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('切换会话时清掉上一会话的待探测任务', async () => {
    const { wrapper, conversationId, tasks, recover } = mountWakeup()

    tasks.value = [task('completed')]
    await nextTick()
    conversationId.value = 'conv-2'
    await nextTick()
    await vi.advanceTimersByTimeAsync(LAST_WAKE_DELAY + 1)

    expect(recover).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
