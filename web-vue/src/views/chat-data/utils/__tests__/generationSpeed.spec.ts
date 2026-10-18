import { describe, expect, it } from 'vitest'

import {
  formatGenerationSpeed,
  formatTotalDurationLabel,
  generationStatsRows,
  type GenerationStatsRow,
  toGenerationStats,
} from '../generationSpeed'

describe('toGenerationStats', () => {

  it('snake_case 原始结构校验转换为 camelCase 统计', () => {
    expect(toGenerationStats({
      first_token_ms: 2100,
      duration_ms: 7000,
      output_tokens: 1000,
      tokens_per_second: 143,
    })).toEqual({ outputTokens: 1000, durationMs: 7000, tokensPerSecond: 143, firstTokenMs: 2100 })
  })

  it('first_token_ms 缺失或非法时其余字段照常产出', () => {
    const base = { duration_ms: 7000, output_tokens: 1000, tokens_per_second: 143 }
    expect(toGenerationStats({ ...base })?.firstTokenMs).toBeUndefined()
    expect(toGenerationStats({ ...base, first_token_ms: 0 })?.firstTokenMs).toBeUndefined()
    expect(toGenerationStats({ ...base, first_token_ms: Number.NaN })?.firstTokenMs).toBeUndefined()
    expect(toGenerationStats({ ...base, first_token_ms: 'x' })).toEqual({
      outputTokens: 1000, durationMs: 7000, tokensPerSecond: 143,
    })
  })

  it('total_duration_ms 合法时透出，缺失或非法时省略', () => {
    const base = { duration_ms: 7000, output_tokens: 1000, tokens_per_second: 143 }
    expect(toGenerationStats({ ...base, total_duration_ms: 9000 })?.totalDurationMs).toBe(9000)
    expect(toGenerationStats({ ...base })?.totalDurationMs).toBeUndefined()
    expect(toGenerationStats({ ...base, total_duration_ms: 0 })?.totalDurationMs).toBeUndefined()
    expect(toGenerationStats({ ...base, total_duration_ms: -5 })?.totalDurationMs).toBeUndefined()
  })

  it('model_first_token_ms / input_tokens / model 合法时透出，缺失或非法时省略', () => {
    const base = { duration_ms: 7000, output_tokens: 1000, tokens_per_second: 143 }
    const full = toGenerationStats({
      ...base,
      model_first_token_ms: 1800,
      input_tokens: 12205,
      model: 'deepseek-v4-flash',
    })
    expect(full?.modelFirstTokenMs).toBe(1800)
    expect(full?.inputTokens).toBe(12205)
    expect(full?.model).toBe('deepseek-v4-flash')
    expect(toGenerationStats({ ...base })?.modelFirstTokenMs).toBeUndefined()
    expect(toGenerationStats({ ...base })?.inputTokens).toBeUndefined()
    expect(toGenerationStats({ ...base })?.model).toBeUndefined()
    expect(toGenerationStats({ ...base, model_first_token_ms: -1 })?.modelFirstTokenMs).toBeUndefined()
    expect(toGenerationStats({ ...base, input_tokens: 0 })?.inputTokens).toBeUndefined()
    expect(toGenerationStats({ ...base, model: '  ' })?.model).toBeUndefined()
  })

  it('usage 缺失时 total_duration_ms 独立支撑统计对象，解码字段逐个省略', () => {
    // 后端契约：模型未吐可见 payload / usage 缺失时，仍下发 {"total_duration_ms": X}
    expect(toGenerationStats({ total_duration_ms: 4200 })).toEqual({ totalDurationMs: 4200 })
    expect(
      toGenerationStats({ total_duration_ms: 4200, output_tokens: 0, duration_ms: -1, tokens_per_second: 0 }),
    ).toEqual({ totalDurationMs: 4200 })
    // total 也不合法时才整体为 null
    expect(toGenerationStats({ total_duration_ms: 0 })).toBeNull()
    expect(toGenerationStats({ total_duration_ms: -5, output_tokens: 100 })).toBeNull()
  })

  it('关键数据缺失或非法时整体返回 null，不用估算冒充', () => {
    expect(toGenerationStats(null)).toBeNull()
    expect(toGenerationStats('x')).toBeNull()
    expect(toGenerationStats({})).toBeNull()
    expect(toGenerationStats({ duration_ms: 7000, tokens_per_second: 143 })).toBeNull() // 无 output_tokens
    expect(toGenerationStats({ output_tokens: 0, duration_ms: 7000, tokens_per_second: 1 })).toBeNull()
    expect(toGenerationStats({ output_tokens: 100 })).toBeNull() // 无时长
    expect(toGenerationStats({ output_tokens: 100, duration_ms: 7000 })).toBeNull() // 无速度
    expect(toGenerationStats({ output_tokens: 100, duration_ms: -1, tokens_per_second: 1 })).toBeNull()
  })
})

describe('formatGenerationSpeed', () => {

  it('常规速度直接取整展示', () => {
    expect(formatGenerationSpeed({ outputTokens: 735, durationMs: 4500, tokensPerSecond: 163 })).toBe('163 tok/s')
  })

  it('超高速度收敛为 k 展示', () => {
    expect(formatGenerationSpeed({ outputTokens: 50000, durationMs: 1000, tokensPerSecond: 50000 })).toBe('50k tok/s')
  })

  it('解码统计缺失或整体为空时返回空串', () => {
    expect(formatGenerationSpeed({ totalDurationMs: 4200 })).toBe('')
    expect(formatGenerationSpeed(null)).toBe('')
  })
})

describe('generationStatsRows', () => {

  it('辅助字段齐全时输出 token 组 + 耗时组，组间首行带分隔标记', () => {
    const rows = generationStatsRows({
      outputTokens: 602,
      inputTokens: 12205,
      model: 'deepseek-v4-flash',
      durationMs: 13000,
      tokensPerSecond: 46,
      firstTokenMs: 2100,
      totalDurationMs: 15600,
      modelFirstTokenMs: 442,
    })
    expect(rows.map((row: GenerationStatsRow) => `${row.label}=${row.value}`)).toEqual([
      '输入总token=12,205',
      '输出总token=602',
      'token速率=46 tok/s',
      'llm 首 token=442ms',
      'harness 首 token=2.1s',
      'harness 总耗=15.6s',
    ])
    expect(rows[3]?.divider).toBe(true)  // llm 首 token 行前分组分隔
    expect(rows[0]?.divider).toBeUndefined()
  })

  it('辅助字段缺失（老历史数据）时对应行省略，harness 总耗回退解码时长', () => {
    const rows = generationStatsRows({ outputTokens: 1842, durationMs: 12300, tokensPerSecond: 150 })
    expect(rows.map((row: GenerationStatsRow) => `${row.label}=${row.value}`)).toEqual([
      '输出总token=1,842',
      'token速率=150 tok/s',
      'harness 总耗=12.3s',
    ])
    expect(rows[2]?.divider).toBe(true)
    const withTotal = generationStatsRows({
      outputTokens: 1842, durationMs: 12300, tokensPerSecond: 150, firstTokenMs: 2100, totalDurationMs: 15600,
    })
    expect(withTotal.map((row: GenerationStatsRow) => `${row.label}=${row.value}`)).toEqual([
      '输出总token=1,842',
      'token速率=150 tok/s',
      'harness 首 token=2.1s',
      'harness 总耗=15.6s',
    ])
  })

  it('分秒档时长', () => {
    const base = { outputTokens: 100, tokensPerSecond: 10 }
    expect(generationStatsRows({ ...base, durationMs: 860 })[2]?.value).toBe('860ms')
    expect(generationStatsRows({ ...base, durationMs: 62000 })[2]?.value).toBe('1m02s')
  })

  it('usage 缺失（total-only）时只输出总耗时行', () => {
    const rows = generationStatsRows({ totalDurationMs: 15600 })
    expect(rows.map((row: GenerationStatsRow) => `${row.label}=${row.value}`)).toEqual([
      'harness 总耗=15.6s',
    ])
    expect(rows[0]?.divider).toBe(true)
    expect(generationStatsRows(null)).toEqual([])
  })
})

describe('formatTotalDurationLabel', () => {

  it('不足 1 分钟显示整秒（不足 1 秒按 1 秒兜底）', () => {
    expect(formatTotalDurationLabel(500)).toBe('1 秒')
    expect(formatTotalDurationLabel(42_000)).toBe('42 秒')
    expect(formatTotalDurationLabel(59_900)).toBe('59 秒')
  })

  it('超过 1 分钟显示分秒', () => {
    expect(formatTotalDurationLabel(83_000)).toBe('1 分 23 秒')
    expect(formatTotalDurationLabel(120_000)).toBe('2 分钟')
  })

  it('非法输入返回空串', () => {
    expect(formatTotalDurationLabel(Number.NaN)).toBe('')
    expect(formatTotalDurationLabel(-1)).toBe('')
  })
})
