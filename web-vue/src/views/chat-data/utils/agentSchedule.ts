/**
 * 智能体定时任务共享工具：调度文案、执行状态文案与样式映射。
 * 用户侧管理弹窗与后台管理面板共用，保持展示口径一致。
 */
import type { AgentScheduleItem } from '../types'

export const WEEKLY_DAY_OPTIONS = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 0, label: '周日' }
]

export const SCHEDULE_RUN_STATUS_TEXT: Record<string, string> = {
  pending: '排队中',
  claimed: '已领取',
  dispatched: '执行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '已取消',
  skipped: '已跳过'
}

export const SCHEDULE_RUN_STATUS_CLASS: Record<string, string> = {
  pending: 'bg-slate-100 text-slate-600',
  claimed: 'bg-blue-50 text-blue-600',
  dispatched: 'bg-blue-50 text-blue-600',
  succeeded: 'bg-emerald-50 text-emerald-700',
  failed: 'bg-red-50 text-red-700',
  cancelled: 'bg-slate-100 text-slate-500',
  skipped: 'bg-amber-50 text-amber-700'
}

export const getScheduleRunStatusText = (status: string | null | undefined): string => (
  status ? SCHEDULE_RUN_STATUS_TEXT[status] || status : '-'
)

export const getScheduleRunStatusClass = (status: string | null | undefined): string => (
  SCHEDULE_RUN_STATUS_CLASS[status || ''] || 'bg-slate-100 text-slate-600'
)

/** 把五字段 cron 表达式转成可读文案，无法识别时原样返回 */
export const cronToText = (expr: string): string => {
  const parts = (expr || '').trim().split(/\s+/)
  if (parts.length !== 5) return expr || '未配置'
  const [minute = '', hour = '', day = '', month = '', weekday = ''] = parts
  const paddedMinute = minute.padStart(2, '0')
  const paddedHour = hour.padStart(2, '0')
  if (minute.startsWith('*/')) return `每小时第 ${minute.slice(2)} 分`
  if (day === '*' && month === '*' && weekday === '*') {
    return `每天 ${paddedHour}:${paddedMinute}`
  }
  if (day === '*' && month === '*' && weekday !== '*') {
    const dayLabel = WEEKLY_DAY_OPTIONS.find(option => String(option.value) === weekday)?.label || `周${weekday}`
    return `每${dayLabel} ${paddedHour}:${paddedMinute}`
  }
  return `自定义 · ${expr}`
}

/** 任务调度文案：单次显示执行时间，周期显示 cron 文案 */
export const scheduleText = (job: Pick<AgentScheduleItem, 'schedule_type' | 'cron_expr' | 'plan_time'>): string => {
  if (job.schedule_type === 'once') {
    return '单次执行'
  }
  return cronToText(job.cron_expr)
}

/** 触发实例执行耗时文案：派发（或领取/计划）到完成的实际间隔 */
export const scheduleRunDurationText = (run: {
  plan_time: string | null
  claimed_time: string | null
  dispatched_time: string | null
  finished_time: string | null
}): string => {
  if (!run.finished_time) return '-'
  const start = run.dispatched_time || run.claimed_time || run.plan_time
  if (!start) return '-'
  const startAt = new Date(start.replace(' ', 'T')).getTime()
  const endAt = new Date(run.finished_time.replace(' ', 'T')).getTime()
  if (Number.isNaN(startAt) || Number.isNaN(endAt) || endAt < startAt) return '-'
  const seconds = Math.round((endAt - startAt) / 1000)
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/**
 * 编辑表单的基线快照：打开弹窗时表单/任务的值。
 * 编辑提交按「与基线逐键比较」生成补丁，未改动的键不下发——既避免用表单默认值
 * 覆盖任务里其它运行配置（如 semantic_config_id / skill_names），也避免已过期的
 * 单次任务被重新提交去做「执行时间必须晚于当前时间」的校验。
 */
export interface ScheduleFormBaseline {
  options: Record<string, unknown>
  scheduleType: '' | 'cron' | 'once'
  cronExpr: string
  planTime: string
  effectiveEndTime: string
}

export const createScheduleBaseline = (): ScheduleFormBaseline => ({
  options: {},
  scheduleType: '',
  cronExpr: '',
  planTime: '',
  effectiveEndTime: ''
})

/** 推理等级 → 运行选项键：'' 清除、on/off 用开关、其余为档位 */
export const buildThinkingKeys = (effort: string): Record<string, unknown> => {
  if (!effort) return { enable_thinking: null, thinking_effort: null }
  if (effort === 'on' || effort === 'off') return { enable_thinking: effort === 'on', thinking_effort: null }
  return { enable_thinking: true, thinking_effort: effort }
}

/**
 * 运行配置补丁：只下发与基线不同的键；值为 null 表示清除该键（回退系统默认）。
 * 表单掌管数据源组/模型/推理等级三类键，其余键由后端保留原值。
 */
export const buildRuntimeOptionsPatch = (
  current: Record<string, unknown>,
  baseline: Record<string, unknown>
): Record<string, unknown> => {
  const patch: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(current)) {
    if ((value ?? null) !== (baseline[key] ?? null)) patch[key] = value
  }
  return patch
}

/** 调度字段补丁：只下发与基线不同的字段；有效期清空用 null 表达 */
export const buildSchedulePatch = (
  current: Pick<ScheduleFormBaseline, 'scheduleType' | 'cronExpr' | 'planTime' | 'effectiveEndTime'>,
  baseline: ScheduleFormBaseline
): Record<string, unknown> => {
  const patch: Record<string, unknown> = {}
  if (current.scheduleType !== baseline.scheduleType) patch.schedule_type = current.scheduleType
  if (current.scheduleType === 'once') {
    if (current.planTime !== baseline.planTime) patch.plan_time = current.planTime
  } else if (current.cronExpr !== baseline.cronExpr) {
    patch.cron_expr = current.cronExpr
  }
  if (current.effectiveEndTime !== baseline.effectiveEndTime) {
    patch.effective_end_time = current.effectiveEndTime || null
  }
  return patch
}
