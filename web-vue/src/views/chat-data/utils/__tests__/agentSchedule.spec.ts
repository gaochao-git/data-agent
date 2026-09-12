import { describe, expect, it } from 'vitest'
import {
  buildRuntimeOptionsPatch,
  buildSchedulePatch,
  buildThinkingKeys,
  createScheduleBaseline
} from '../agentSchedule'

describe('buildThinkingKeys', () => {
  it('推理等级留空时用 null 表达清除', () => {
    expect(buildThinkingKeys('')).toEqual({ enable_thinking: null, thinking_effort: null })
  })

  it('开关档位写成布尔值', () => {
    expect(buildThinkingKeys('on')).toEqual({ enable_thinking: true, thinking_effort: null })
    expect(buildThinkingKeys('off')).toEqual({ enable_thinking: false, thinking_effort: null })
  })

  it('具体档位写入 thinking_effort 并开启思考', () => {
    expect(buildThinkingKeys('high')).toEqual({ enable_thinking: true, thinking_effort: 'high' })
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
