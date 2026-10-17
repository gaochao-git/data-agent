import { describe, expect, it } from 'vitest'

import {
  formatGenerationSpeed,
  formatGenerationStatsDetail,
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
})

describe('formatGenerationStatsDetail', () => {

  it('毫秒 / 秒 / 分秒三档时长', () => {
    const base = { outputTokens: 100, tokensPerSecond: 10 }
    expect(formatGenerationStatsDetail({ ...base, durationMs: 860 })).toContain('生成 860ms')
    expect(formatGenerationStatsDetail({ ...base, durationMs: 12300 })).toContain('生成 12.3s')
    expect(formatGenerationStatsDetail({ ...base, durationMs: 62000 })).toContain('生成 1m02s')
  })

  it('tokens 紧凑展示', () => {
    expect(formatGenerationStatsDetail({ outputTokens: 1842, durationMs: 12300, tokensPerSecond: 150 }))
      .toBe('输出 1.8k tokens · 生成 12.3s')
  })

  it('含首 token 耗时时置于明细开头', () => {
    expect(formatGenerationStatsDetail({ outputTokens: 1842, durationMs: 12300, tokensPerSecond: 150, firstTokenMs: 2100 }))
      .toBe('首 token 2.1s · 输出 1.8k tokens · 生成 12.3s')
    expect(formatGenerationStatsDetail({ outputTokens: 100, durationMs: 860, tokensPerSecond: 10, firstTokenMs: 860 }))
      .toContain('首 token 860ms')
    expect(formatGenerationStatsDetail({ outputTokens: 100, durationMs: 860, tokensPerSecond: 10 }))
      .not.toContain('首 token')
  })
})
