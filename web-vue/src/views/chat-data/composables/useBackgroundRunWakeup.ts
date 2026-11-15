/**
 * 后台子智能体收口后的主 Run 自动续订。
 *
 * 后台任务完成/失败后，服务端会在会话空闲时新建一个隐藏通知 Run，把结论注入
 * 主智能体继续工作。这个 Run 不是用户点击触发的，前端没有天然的订阅入口；
 * 只在页面挂载/断线时恢复会出现「必须重新进入会话才能看到后续工作」。
 *
 * 这里监听任务账本的 active -> terminal 迁移，在通知 Run 可能被创建的窗口内
 * 做少量重试探测；是否真的有活跃 Run、是否该订阅仍由恢复入口按服务端快照判断。
 */
import { onBeforeUnmount, watch, type Ref } from 'vue'
import type { SubagentTaskRecord } from '../types'
import { isSubagentTaskActive } from './useSubagentTasks'

export const BACKGROUND_RUN_WAKE_DELAYS_MS = [1_000, 2_500, 5_000, 9_000] as const

export type BackgroundRunWakeupOptions = {
  conversationId: Ref<string | null>
  tasks: Ref<SubagentTaskRecord[]>
  isGenerating: Ref<boolean>
  hasActiveRun: Ref<boolean>
  recover: () => Promise<void>
}

export function useBackgroundRunWakeup(options: BackgroundRunWakeupOptions) {
  const lastStatuses = new Map<string, SubagentTaskRecord['status']>()
  let timers: number[] = []
  let checking = false

  const stop = () => {
    timers.forEach(timer => window.clearTimeout(timer))
    timers = []
  }

  const check = async () => {
    if (checking || options.isGenerating.value || options.hasActiveRun.value) return
    if (!options.conversationId.value) return
    checking = true
    try {
      await options.recover()
    } finally {
      checking = false
    }
  }

  const schedule = () => {
    stop()
    for (const delay of BACKGROUND_RUN_WAKE_DELAYS_MS) {
      const handle = window.setTimeout(() => {
        timers = timers.filter(timer => timer !== handle)
        void check()
      }, delay)
      timers.push(handle)
    }
  }

  watch(options.tasks, (tasks) => {
    let becameTerminal = false
    for (const task of tasks) {
      const previous = lastStatuses.get(task.task_id)
      if (
        task.execution_mode !== 'sync'
        && previous
        && isSubagentTaskActive({ status: previous })
        && !isSubagentTaskActive(task)
      ) {
        becameTerminal = true
      }
      lastStatuses.set(task.task_id, task.status)
    }
    if (becameTerminal) schedule()
  }, { deep: true, immediate: true })

  watch(options.conversationId, () => {
    lastStatuses.clear()
    stop()
  })

  onBeforeUnmount(stop)

  return { stop }
}
