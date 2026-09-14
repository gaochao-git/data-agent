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

/** 周几值 → 已选 tag 短文案（「一」「二」…），让多选 tag 紧凑不换行；未知值回退完整标签 */
export const weeklyDayShortLabel = (value: number | string): string => {
  const label = WEEKLY_DAY_OPTIONS.find(option => option.value === Number(value))?.label
  return label ? label.slice(1) : String(value)
}

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

/** 周几字段（支持 `1,3,5` 逗号列表）→ 中文标签串，无法识别时原样回退 */
const weeklyDaysLabel = (weekday: string): string => {
  const labels = weekday.split(',').map(token =>
    WEEKLY_DAY_OPTIONS.find(option => String(option.value) === token)?.label || ''
  )
  if (labels.some(label => !label)) return `周${weekday}`
  return labels.join('、')
}

/** 把五字段 cron 表达式转成可读文案，无法识别时原样返回 */
export const cronToText = (expr: string): string => {
  const parts = (expr || '').trim().split(/\s+/)
  if (parts.length !== 5) return expr || '未配置'
  const [minute = '', hour = '', day = '', month = '', weekday = ''] = parts
  if (hour === '*' && day === '*' && month === '*' && weekday === '*' && /^\d{1,2}$/.test(minute)) {
    return `每小时的第 ${minute.padStart(2, '0')} 分`
  }
  if (minute.startsWith('*/')) return `每小时第 ${minute.slice(2)} 分`
  const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
  if (day !== '*' || month !== '*') {
    if (day !== '*' && month === '*' && weekday === '*' && /^\d{1,2}$/.test(day)) {
      return `每月 ${Number(day)} 号 ${time}`
    }
    return `自定义 · ${expr}`
  }
  if (weekday === '*') return `每天 ${time}`
  return `每${weeklyDaysLabel(weekday)} ${time}`
}

/** 任务调度文案：单次显示执行时间，周期显示 cron 文案 */
export const scheduleText = (job: Pick<AgentScheduleItem, 'schedule_type' | 'cron_expr' | 'plan_time'>): string => {
  if (job.schedule_type === 'once') {
    return '单次执行'
  }
  return cronToText(job.cron_expr)
}

/** 调度表单的档位：单次执行并入档位下拉，不再用单独的周期/单次切换 */
export type ScheduleCronPreset = 'hourly' | 'daily' | 'weekly' | 'monthly' | 'once' | 'custom'

/** cron 反解析出的结构化表单值（各档位共用一套字段，未命中档位的字段取默认值） */
export interface ParsedCronForm {
  cronPreset: Exclude<ScheduleCronPreset, 'once' | 'custom'>
  hourlyMinute: number
  dailyTime: string
  weeklyDays: number[]
  monthlyDay: number
}

const pad2 = (num: number): string => String(num).padStart(2, '0')

/**
 * 把 cron 表达式反解析回结构化档位（编辑回显用）。
 * 只接受各预设档位能原样生成回去的形态（与 buildCronExpr 一一对应）；
 * 区间（1-5）、步进（*\/10）等手工写法不属于任何档位，返回 null 回落自定义。
 */
export const parseCronToPreset = (expr: string): ParsedCronForm | null => {
  const parts = (expr || '').trim().split(/\s+/)
  if (parts.length !== 5) return null
  const [minute = '', hour = '', day = '', month = '', weekday = ''] = parts
  const minuteNum = Number(minute)
  const hourNum = Number(hour)
  const minuteOk = /^\d{1,2}$/.test(minute) && minuteNum <= 59
  const hourOk = /^\d{1,2}$/.test(hour) && hourNum <= 23
  if (month !== '*' || !minuteOk) return null
  if (hour === '*' && day === '*' && weekday === '*') {
    return { cronPreset: 'hourly', hourlyMinute: minuteNum, dailyTime: '09:00', weeklyDays: [1], monthlyDay: 1 }
  }
  // 非法组合只有「几号与周几同时指定」（无档位能表达）；其余星号组合各归各档
  const dayIsStar = day === '*'
  const weekdayIsStar = weekday === '*'
  if (!hourOk || (!dayIsStar && !weekdayIsStar)) return null
  const time = `${pad2(hourNum)}:${pad2(minuteNum)}`
  if (dayIsStar) {
    if (weekdayIsStar) {
      return { cronPreset: 'daily', hourlyMinute: 0, dailyTime: time, weeklyDays: [1], monthlyDay: 1 }
    }
    const days = weekday.split(',').map(Number)
    const unique = [...new Set(days)]
    if (unique.every(d => Number.isInteger(d) && d >= 0 && d <= 6)) {
      return { cronPreset: 'weekly', hourlyMinute: 0, dailyTime: time, weeklyDays: unique.sort((a, b) => a - b), monthlyDay: 1 }
    }
    return null
  }
  const dayNum = Number(day)
  if (weekday === '*' && /^\d{1,2}$/.test(day) && dayNum >= 1 && dayNum <= 31) {
    return { cronPreset: 'monthly', hourlyMinute: 0, dailyTime: time, weeklyDays: [1], monthlyDay: dayNum }
  }
  return null
}

/** 调度表单当前选择的人类可读预览（跟随表单实时变化，供用户确认语义） */
export const schedulePresetPreview = (form: {
  cronPreset: ScheduleCronPreset
  hourlyMinute: number
  dailyTime: string
  weeklyDays: number[]
  monthlyDay: number
  cronExpr: string
  planTime: string | Date
}): string => {
  switch (form.cronPreset) {
    case 'hourly':
      return `每小时的第 ${String(form.hourlyMinute).padStart(2, '0')} 分`
    case 'daily':
      return `每天 ${form.dailyTime}`
    case 'weekly': {
      // 周日(0)排末尾，按「周一→周日」习惯顺序展示
      const labels = [...form.weeklyDays].sort((a, b) => (a === 0 ? 7 : a) - (b === 0 ? 7 : b)).map(day =>
        WEEKLY_DAY_OPTIONS.find(option => option.value === day)?.label || `周${day}`
      )
      return labels.length ? `每${labels.join('、')} ${form.dailyTime}` : '请选择执行日'
    }
    case 'monthly':
      return `每月 ${form.monthlyDay} 号 ${form.dailyTime}`
    case 'once':
      if (!form.planTime) return '单次执行'
      if (form.planTime instanceof Date) {
        return `单次执行 · ${form.planTime.getFullYear()}-${pad2(form.planTime.getMonth() + 1)}-${pad2(form.planTime.getDate())} ${pad2(form.planTime.getHours())}:${pad2(form.planTime.getMinutes())}`
      }
      return `单次执行 · ${form.planTime}`
    default:
      return cronToText(form.cronExpr)
  }
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
