/**
 * 后台子智能体任务的会话级状态源。
 *
 * 任务真值在后端账本；本 composable 只做三件事：
 * - 按会话加载列表，刷新页面后恢复任务面板；
 * - 合并父 Run SSE 的 `subagent_task_updated` 快照与轮询结果（task_id 去重）；
 * - 有活跃任务时低频轮询，空闲 Run 也能看到进度，不依赖父 Run 一直在线。
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import { cancelSubagentTask, getSubagentTask, listSubagentTasks } from '../api'
import type { SubagentTaskRecord, SubagentTaskStatus } from '../types'

export const ACTIVE_SUBAGENT_TASK_STATUSES: ReadonlySet<SubagentTaskStatus> = new Set([
  'pending',
  'running',
  'cancelling',
  'paused',
  'waiting_approval',
])
export const SUBAGENT_TASK_POLL_MS = 3500

export const isSubagentTaskActive = (task: Pick<SubagentTaskRecord, 'status'>): boolean =>
  ACTIVE_SUBAGENT_TASK_STATUSES.has(task.status)

const taskTime = (task: SubagentTaskRecord): number => {
  const value = Date.parse(task.updated_at || task.finished_at || task.created_at || '')
  return Number.isFinite(value) ? value : 0
}

const sortTasks = (tasks: SubagentTaskRecord[]): SubagentTaskRecord[] => [...tasks].sort((a, b) => {
  const activeDelta = Number(isSubagentTaskActive(b)) - Number(isSubagentTaskActive(a))
  if (activeDelta !== 0) return activeDelta
  return taskTime(b) - taskTime(a)
})

export interface SubagentTaskSession {
  sessionId: string
  tasks: SubagentTaskRecord[]
  rootTask: SubagentTaskRecord
  latestTask: SubagentTaskRecord
  label: string
  turnCount: number
}

const taskTurnIndex = (task: SubagentTaskRecord): number => Math.max(Number(task.turn_index) || 1, 1)
export const subagentTaskSessionId = (task: SubagentTaskRecord): string =>
  task.agent_session_id || task.agent_id || task.root_task_id || task.task_id

/** 任务账本按轮次落记录；管理页按子会话聚合，避免追问被展示成新的智能体。 */
export const groupSubagentTaskSessions = (tasks: SubagentTaskRecord[]): SubagentTaskSession[] => {
  const grouped = new Map<string, SubagentTaskRecord[]>()
  for (const task of tasks) {
    const sessionId = subagentTaskSessionId(task)
    const bucket = grouped.get(sessionId)
    if (bucket) bucket.push(task)
    else grouped.set(sessionId, [task])
  }

  const sessions = [...grouped.entries()].map(([sessionId, sessionTasks]) => {
    const ordered = [...sessionTasks].sort((a, b) => taskTurnIndex(a) - taskTurnIndex(b) || taskTime(a) - taskTime(b))
    const rootTask = ordered[0]!
    const latestTask = ordered[ordered.length - 1]!
    return {
      sessionId,
      tasks: ordered,
      rootTask,
      latestTask,
      label: rootTask.label?.trim() || rootTask.description?.trim() || latestTask.label?.trim() || '子智能体任务',
      turnCount: Math.max(ordered.length, ...ordered.map(taskTurnIndex)),
    }
  })

  return sessions.sort((a, b) => {
    const activeDelta = Number(b.tasks.some(isSubagentTaskActive)) - Number(a.tasks.some(isSubagentTaskActive))
    if (activeDelta !== 0) return activeDelta
    return taskTime(b.latestTask) - taskTime(a.latestTask)
  })
}

/** 合并任务快照：SSE 列表帧不带 report，保留已加载详情，按 task_id 避免重复。 */
export const mergeSubagentTasks = (
  current: SubagentTaskRecord[],
  incoming: SubagentTaskRecord[],
): SubagentTaskRecord[] => {
  const merged = new Map(current.map(task => [task.task_id, task]))
  for (const task of incoming) {
    const existing = merged.get(task.task_id)
    merged.set(task.task_id, {
      ...existing,
      ...task,
      report: task.report ?? existing?.report,
      error: task.error ?? existing?.error,
      result_meta: task.result_meta ?? existing?.result_meta,
    })
  }
  return sortTasks([...merged.values()])
}

export const useSubagentTasks = () => {
  const tasks = ref<SubagentTaskRecord[]>([])
  const details = ref<Record<string, SubagentTaskRecord>>({})
  const loading = ref(false)
  const error = ref<string | null>(null)
  let conversationId: string | null = null
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let loadSequence = 0

  const hasActiveTasks = computed(() => tasks.value.some(isSubagentTaskActive))
  const activeCount = computed(() => tasks.value.filter(isSubagentTaskActive).length)

  const stopPolling = () => {
    if (pollTimer === null) return
    clearInterval(pollTimer)
    pollTimer = null
  }

  const startPolling = () => {
    if (pollTimer !== null || !conversationId) return
    pollTimer = setInterval(() => { void loadTasks(false) }, SUBAGENT_TASK_POLL_MS)
  }

  const syncPolling = (nextTasks: SubagentTaskRecord[]) => {
    if (nextTasks.some(isSubagentTaskActive)) startPolling()
    else stopPolling()
  }

  const loadTasks = async (showLoading = true) => {
    const currentConversationId = conversationId
    if (!currentConversationId) {
      tasks.value = []
      details.value = {}
      stopPolling()
      return
    }
    const sequence = ++loadSequence
    if (showLoading) loading.value = true
    try {
      const nextTasks = await listSubagentTasks(currentConversationId, { limit: 50 })
      if (sequence !== loadSequence || currentConversationId !== conversationId) return
      tasks.value = mergeSubagentTasks(tasks.value, nextTasks)
      syncOpenTaskDetails(nextTasks)
      error.value = null
      syncPolling(tasks.value)
    } catch (err) {
      if (sequence !== loadSequence) return
      error.value = err instanceof Error ? err.message : '读取子智能体任务失败'
    } finally {
      if (sequence === loadSequence && showLoading) loading.value = false
    }
  }

  /** 已打开的详情页跟随列表快照更新状态/进度；任务刚收口时再拉一次完整报告与过程。 */
  const syncOpenTaskDetails = (listedTasks: SubagentTaskRecord[]) => {
    const listedById = new Map(listedTasks.map(task => [task.task_id, task]))
    const nextDetails = { ...details.value }
    let changed = false
    for (const [taskId, detail] of Object.entries(nextDetails)) {
      const listed = listedById.get(taskId)
      if (!listed) continue
      nextDetails[taskId] = {
        ...detail,
        ...listed,
        report: listed.report ?? detail.report,
        error: listed.error ?? detail.error,
        result_meta: listed.result_meta ?? detail.result_meta,
      }
      changed = true
      if (isSubagentTaskActive(detail) && !isSubagentTaskActive(listed)) {
        void loadTaskDetail(taskId, true)
      }
    }
    if (changed) details.value = nextDetails
  }

  const applyTaskUpdate = (task: SubagentTaskRecord) => {
    if (!task?.task_id || task.conversation_id !== conversationId) return
    tasks.value = mergeSubagentTasks(tasks.value, [task])
    const detail = details.value[task.task_id]
    if (detail) {
      details.value = {
        ...details.value,
        [task.task_id]: { ...detail, ...task, report: task.report ?? detail.report, result_meta: task.result_meta ?? detail.result_meta },
      }
      if (!isSubagentTaskActive(task)) void loadTaskDetail(task.task_id, true)
    }
    syncPolling(tasks.value)
  }

  const loadTaskDetail = async (taskId: string, refresh = false): Promise<SubagentTaskRecord | null> => {
    const currentConversationId = conversationId
    if (!currentConversationId || (!refresh && details.value[taskId])) return details.value[taskId] || null
    try {
      const detail = await getSubagentTask(currentConversationId, taskId)
      if (currentConversationId !== conversationId) return null
      details.value = { ...details.value, [taskId]: detail }
      tasks.value = mergeSubagentTasks(tasks.value, [detail])
      return detail
    } catch (err) {
      error.value = err instanceof Error ? err.message : '读取任务详情失败'
      return details.value[taskId] || null
    }
  }

  const cancelTask = async (taskId: string): Promise<SubagentTaskRecord | null> => {
    const currentConversationId = conversationId
    if (!currentConversationId) return null
    try {
      const task = await cancelSubagentTask(currentConversationId, taskId)
      if (currentConversationId === conversationId) applyTaskUpdate(task)
      return task
    } catch (err) {
      error.value = err instanceof Error ? err.message : '取消任务失败'
      return null
    }
  }

  const setConversation = async (nextConversationId: string | null) => {
    const normalized = nextConversationId || null
    if (normalized === conversationId) {
      await loadTasks(false)
      return
    }
    stopPolling()
    loadSequence += 1
    conversationId = normalized
    tasks.value = []
    details.value = {}
    error.value = null
    if (normalized) await loadTasks(true)
  }

  onBeforeUnmount(stopPolling)

  return {
    tasks,
    details,
    loading,
    error,
    hasActiveTasks,
    activeCount,
    setConversation,
    loadTasks,
    applyTaskUpdate,
    loadTaskDetail,
    cancelTask,
    stopPolling,
  }
}
