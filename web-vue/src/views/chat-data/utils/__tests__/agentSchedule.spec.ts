import { describe, expect, it } from 'vitest'
import {
  buildRuntimeOptionsPatch,
  buildSchedulePatch,
  buildThinkingKeys,
  createScheduleBaseline,
  cronToText,
  parseCronToPreset,
  schedulePresetPreview,
  weeklyDayShortLabel
} from '../agentSchedule'

describe('buildThinkingKeys', () => {
  it('开关/档位均未触碰时用 null 表达清除（跟随模型默认）', () => {
    expect(buildThinkingKeys(null, null)).toEqual({ enable_thinking: null, thinking_effort: null })
  })

  it('开关关闭时档位不生效', () => {
    expect(buildThinkingKeys(false, 'low')).toEqual({ enable_thinking: false, thinking_effort: null })
  })

  it('开关开启时写入档位，未选档位则为 null', () => {
    expect(buildThinkingKeys(true, 'low')).toEqual({ enable_thinking: true, thinking_effort: 'low' })
    expect(buildThinkingKeys(true, null)).toEqual({ enable_thinking: true, thinking_effort: null })
  })

  it('开关未触碰但选了档位：保持跟随默认开关语义', () => {
    expect(buildThinkingKeys(null, 'high')).toEqual({ enable_thinking: null, thinking_effort: 'high' })
  })
})

describe('buildRuntimeOptionsPatch', () => {
  const baseline = {
    datasource_id: 'ds-1',
    model_id: null,
    enable_thinking: true,
    thinking_effort: null
  }

  it('未改动的键不下发：编辑标题不会清空任务里其它运行配置', () => {
    expect(buildRuntimeOptionsPatch({ ...baseline }, baseline)).toEqual({})
  })

  it('清空数据源用 null 表达清除', () => {
    expect(buildRuntimeOptionsPatch({ ...baseline, datasource_id: null }, baseline))
      .toEqual({ datasource_id: null })
  })

  it('只下发改动过的键', () => {
    expect(buildRuntimeOptionsPatch({ ...baseline, model_id: 'm2' }, baseline))
      .toEqual({ model_id: 'm2' })
  })

  it('新建（空基线）只下发有值的键，不写空键', () => {
    expect(buildRuntimeOptionsPatch(
      { datasource_id: 'ds-1', model_id: null, enable_thinking: false, thinking_effort: null },
      {}
    )).toEqual({ datasource_id: 'ds-1', enable_thinking: false })
  })
})

describe('buildSchedulePatch', () => {
  const baseline = {
    ...createScheduleBaseline(),
    scheduleType: 'cron' as const,
    cronExpr: '0 9 * * *'
  }

  it('未改动的调度字段不下发（已过期的单次任务不会被重新校验）', () => {
    expect(buildSchedulePatch({
      scheduleType: 'cron',
      cronExpr: '0 9 * * *',
      planTime: '',
      effectiveEndTime: ''
    }, baseline)).toEqual({})
  })

  it('周期改单次时只下发执行时间，不夹带旧 cron', () => {
    expect(buildSchedulePatch({
      scheduleType: 'once',
      cronExpr: '0 9 * * *',
      planTime: '2026-09-13 09:00:00',
      effectiveEndTime: ''
    }, baseline)).toEqual({
      schedule_type: 'once',
      plan_time: '2026-09-13 09:00:00'
    })
  })

  it('清空有效期用 null 表达', () => {
    expect(buildSchedulePatch({
      scheduleType: 'cron',
      cronExpr: '0 9 * * *',
      planTime: '',
      effectiveEndTime: ''
    }, { ...baseline, effectiveEndTime: '2026-10-01 00:00:00' })).toEqual({
      effective_end_time: null
    })
  })
})

describe('cronToText', () => {
  it('每小时档（分 * * * *）不再误显示为每天，分钟补齐两位', () => {
    expect(cronToText('30 * * * *')).toBe('每小时的第 30 分')
    expect(cronToText('5 * * * *')).toBe('每小时的第 05 分')
  })

  it('每天与每月文案', () => {
    expect(cronToText('0 9 * * *')).toBe('每天 09:00')
    expect(cronToText('0 9 1 * *')).toBe('每月 1 号 09:00')
  })

  it('周几支持逗号多选列表', () => {
    expect(cronToText('0 9 * * 1,3,5')).toBe('每周一、周三、周五 09:00')
    expect(cronToText('0 9 * * 0')).toBe('每周日 09:00')
  })

  it('区间/步进等手工写法原样展示', () => {
    expect(cronToText('*/10 * * * *')).toBe('每小时第 10 分')
    expect(cronToText('0 9 * * 1-5')).toBe('每周1-5 09:00')
  })
})

describe('parseCronToPreset', () => {
  it('预设档位生成的 cron 能完整反解析回去（与 buildCronExpr 一一对应）', () => {
    expect(parseCronToPreset('30 * * * *')).toEqual({
      cronPreset: 'hourly', hourlyMinute: 30, dailyTime: '09:00', weeklyDays: [1], monthlyDay: 1
    })
    expect(parseCronToPreset('0 9 * * *')).toEqual({
      cronPreset: 'daily', hourlyMinute: 0, dailyTime: '09:00', weeklyDays: [1], monthlyDay: 1
    })
    expect(parseCronToPreset('5 9 * * 1,3,5')).toEqual({
      cronPreset: 'weekly', hourlyMinute: 0, dailyTime: '09:05', weeklyDays: [1, 3, 5], monthlyDay: 1
    })
    expect(parseCronToPreset('0 9 15 * *')).toEqual({
      cronPreset: 'monthly', hourlyMinute: 0, dailyTime: '09:00', weeklyDays: [1], monthlyDay: 15
    })
  })

  it('反解析往返无损：解析再重建得到同一 cron（编辑不改调度不产生补丁的前提）', () => {
    for (const expr of ['30 * * * *', '0 9 * * *', '5 9 * * 1,3,5', '0 9 15 * *']) {
      const parsed = parseCronToPreset(expr)
      expect(parsed).not.toBeNull()
      const { cronPreset, hourlyMinute, dailyTime, weeklyDays, monthlyDay } = parsed!
      const [hour, minute] = dailyTime.split(':').map(Number)
      const rebuilt = cronPreset === 'hourly'
        ? `${hourlyMinute % 60} * * * *`
        : cronPreset === 'daily'
          ? `${minute} ${hour} * * *`
          : cronPreset === 'weekly'
            ? `${minute} ${hour} * * ${[...weeklyDays].sort((a, b) => a - b).join(',')}`
            : `${minute} ${hour} ${monthlyDay} * *`
      expect(rebuilt).toBe(expr)
    }
  })

  it('区间/步进/越界等手工写法返回 null（回落自定义档位）', () => {
    expect(parseCronToPreset('*/10 * * * *')).toBeNull()
    expect(parseCronToPreset('0 9 * * 1-5')).toBeNull()
    expect(parseCronToPreset('0 9 1 * 1')).toBeNull()
    expect(parseCronToPreset('60 9 * * *')).toBeNull()
    expect(parseCronToPreset('0 9 *')).toBeNull()
    expect(parseCronToPreset('')).toBeNull()
  })
})

describe('schedulePresetPreview', () => {
  const base = {
    hourlyMinute: 0,
    dailyTime: '09:00',
    weeklyDays: [1] as number[],
    monthlyDay: 1,
    cronExpr: '',
    planTime: '' as string | Date
  }

  it('各档位输出自然语言预览', () => {
    expect(schedulePresetPreview({ ...base, cronPreset: 'hourly', hourlyMinute: 30 })).toBe('每小时的第 30 分')
    expect(schedulePresetPreview({ ...base, cronPreset: 'daily' })).toBe('每天 09:00')
    expect(schedulePresetPreview({ ...base, cronPreset: 'weekly', weeklyDays: [5, 1, 3] })).toBe('每周一、周三、周五 09:00')
    expect(schedulePresetPreview({ ...base, cronPreset: 'monthly', monthlyDay: 15 })).toBe('每月 15 号 09:00')
    expect(schedulePresetPreview({ ...base, cronPreset: 'once', planTime: '2026-09-20 14:30' })).toBe('单次执行 · 2026-09-20 14:30')
  })

  it('每周未选执行日给出提示语', () => {
    expect(schedulePresetPreview({ ...base, cronPreset: 'weekly', weeklyDays: [] })).toBe('请选择执行日')
  })

  it('自定义档位复用 cronToText；单次的 Date 值按本地时间格式化', () => {
    expect(schedulePresetPreview({ ...base, cronPreset: 'custom', cronExpr: '0 9 * * 1-5' })).toBe('每周1-5 09:00')
    expect(schedulePresetPreview({ ...base, cronPreset: 'once', planTime: new Date(2026, 8, 20, 9, 5) }))
      .toBe('单次执行 · 2026-09-20 09:05')
  })
})

describe('weeklyDayShortLabel', () => {
  it('周几值映射为单字短标签，未知值原样回退', () => {
    expect(weeklyDayShortLabel(1)).toBe('一')
    expect(weeklyDayShortLabel(6)).toBe('六')
    expect(weeklyDayShortLabel(0)).toBe('日')
    expect(weeklyDayShortLabel('3')).toBe('三')
    expect(weeklyDayShortLabel(9)).toBe('9')
  })
})
