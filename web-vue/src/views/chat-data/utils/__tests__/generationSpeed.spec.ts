import { describe, expect, it } from 'vitest'

import {
  computeLiveGenerationSpeed,
  estimateTokensFromText,
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

describe('estimateTokensFromText', () => {

  it('CJK 按 1 字/token,拉丁按 4 字符/token,空白不计', () => {
    expect(estimateTokensFromText('你好世界')).toBe(4)
    expect(estimateTokensFromText('abcdefgh')).toBe(2)
    expect(estimateTokensFromText('你好 world')).toBe(4)
    expect(estimateTokensFromText('   \n\t ')).toBe(0)
  })
})

describe('computeLiveGenerationSpeed', () => {

  it('无工具时段:速率 = 粗估 tokens ÷ 墙钟', () => {
    const speed = computeLiveGenerationSpeed({
      streamedText: '你'.repeat(2000),
      runStartedAtMs: 10_000,
      nowMs: 20_000,
    })
    expect(speed).toBe(200)
  })

  it('执行中的工具按已耗时扣除,分母只含模型时间', () => {
    const speed = computeLiveGenerationSpeed({
      streamedText: '你'.repeat(1000),
      argsChars: 800,
      toolSpansMs: [{ started_at_ms: 12_000, ended_at_ms: null }],
      runStartedAtMs: 10_000,
      nowMs: 20_000,
    })
    // tokens≈1000+200(参数 800 字符/4),模型时间=10s−8s(工具已跑 8s)=2s → 600 tok/s
    expect(speed).toBe(600)
  })

  it('已完成的工具按起止差扣除', () => {
    const speed = computeLiveGenerationSpeed({
      streamedText: '你'.repeat(1000),
      toolSpansMs: [{ started_at_ms: 11_000, ended_at_ms: 16_000 }],
      runStartedAtMs: 10_000,
      nowMs: 20_000,
    })
    expect(speed).toBe(200)
  })

  it('无内容或模型时间不足 1 秒时不出数', () => {
    expect(computeLiveGenerationSpeed({ streamedText: '', runStartedAtMs: 10_000, nowMs: 20_000 })).toBeNull()
    expect(computeLiveGenerationSpeed({ streamedText: '你好', runStartedAtMs: 10_000, nowMs: 10_500 })).toBeNull()
  })

  it('历史轮次的工具(本轮开始前已结束)不参与扣减', () => {
    const speed = computeLiveGenerationSpeed({
      streamedText: '你'.repeat(1000),
      toolSpansMs: [{ started_at_ms: 0, ended_at_ms: 9_000 }],
      runStartedAtMs: 10_000,
      nowMs: 20_000,
    })
    expect(speed).toBe(100)
  })

  it('本轮开始后才启动的工具,起点夹到本轮起点', () => {
    const speed = computeLiveGenerationSpeed({
      streamedText: '你'.repeat(1000),
      toolSpansMs: [{ started_at_ms: 0, ended_at_ms: 99_000 }],
      runStartedAtMs: 10_000,
      nowMs: 20_000,
    })
    // 工具夹为 [10000, 20000],模型时间 0 → 不出数
    expect(speed).toBeNull()
  })

  it('纯参数流式阶段(无正文)凭服务端 argsChars 也能出数', () => {
    const speed = computeLiveGenerationSpeed({
      streamedText: '',
      argsChars: 2000,
      runStartedAtMs: 10_000,
      nowMs: 20_000,
    })
    // 2000/4=500 tokens ÷ 10s = 50 tok/s
    expect(speed).toBe(50)
  })

  it('工具时间异常吞掉全部墙钟时不出数', () => {
    expect(computeLiveGenerationSpeed({
      streamedText: '你'.repeat(1000),
      toolSpansMs: [{ started_at_ms: 10_000, ended_at_ms: 20_000 }],
      runStartedAtMs: 10_000,
      nowMs: 20_000,
    })).toBeNull()
  })
})
