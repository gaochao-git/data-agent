import { describe, expect, it } from 'vitest'

import type { ChatDataTokenUsage } from '../../types'
import {
  computeGenerationStats,
  computeLiveGenerationSpeed,
  estimateTokensFromText,
  formatGenerationSpeed,
  formatGenerationStatsDetail,
} from '../generationSpeed'

const RUN_START = 10_000
const RUN_END = 20_000

const buildUsage = (overrides: {
  output_tokens?: number
  toolRounds?: Array<[number, number] | null>
} = {}): ChatDataTokenUsage => ({
  used: 0,
  total: 0,
  output_tokens: overrides.output_tokens,
  breakdown: (overrides.toolRounds || []).map(round => (
    round
      ? { key: `tool-${round[0]}`, label: '工具', tokens: 10, role: 'tool', started_at_ms: round[0], ended_at_ms: round[1] }
      : { key: 'ai-1', label: '模型调用', tokens: 10, role: 'ai' }
  )),
} as ChatDataTokenUsage)

describe('computeGenerationStats', () => {

  it('无工具轮次：分母 = 整轮墙钟时间', () => {
    const stats = computeGenerationStats(buildUsage({ output_tokens: 1000 }), {
      runStartedAtMs: RUN_START,
      runEndedAtMs: RUN_END,
    })
    expect(stats).toEqual({ outputTokens: 1000, durationMs: 10_000, tokensPerSecond: 100 })
  })

  it('本轮窗口内的工具时间从分母扣除', () => {
    const stats = computeGenerationStats(buildUsage({ output_tokens: 1000, toolRounds: [[11_000, 13_000], [14_000, 15_000]] }), {
      runStartedAtMs: RUN_START,
      runEndedAtMs: RUN_END,
    })
    expect(stats).toEqual({ outputTokens: 1000, durationMs: 7000, tokensPerSecond: 143 })
  })

  it('历史轮次的工具行落在窗口外，不参与扣减', () => {
    const stats = computeGenerationStats(buildUsage({ output_tokens: 1000, toolRounds: [[1_000, 5_000]] }), {
      runStartedAtMs: RUN_START,
      runEndedAtMs: RUN_END,
    })
    expect(stats).toEqual({ outputTokens: 1000, durationMs: 10_000, tokensPerSecond: 100 })
  })

  it('tool 行缺时间戳或时间倒挂时跳过', () => {
    const stats = computeGenerationStats(buildUsage({ output_tokens: 1000, toolRounds: [null, [15_000, 14_000], [12_000, 12_500]] }), {
      runStartedAtMs: RUN_START,
      runEndedAtMs: RUN_END,
    })
    expect(stats).toEqual({ outputTokens: 1000, durationMs: 9500, tokensPerSecond: 105 })
  })

  it('usage 缺失或 output_tokens 非正数时返回 null', () => {
    expect(computeGenerationStats(null, { runStartedAtMs: RUN_START, runEndedAtMs: RUN_END })).toBeNull()
    expect(computeGenerationStats(buildUsage({}), { runStartedAtMs: RUN_START, runEndedAtMs: RUN_END })).toBeNull()
  })

  it('起止时刻非法或扣完工具后无剩余生成时间时返回 null', () => {
    expect(computeGenerationStats(buildUsage({ output_tokens: 100 }), { runStartedAtMs: RUN_END, runEndedAtMs: RUN_START })).toBeNull()
    expect(computeGenerationStats(buildUsage({ output_tokens: 100, toolRounds: [[RUN_START, RUN_END]] }), {
      runStartedAtMs: RUN_START,
      runEndedAtMs: RUN_END,
    })).toBeNull()
  })

  it('首 token 耗时 = 首个流事件时刻 − 发起时刻', () => {
    const stats = computeGenerationStats(buildUsage({ output_tokens: 1000 }), {
      runStartedAtMs: RUN_START,
      runEndedAtMs: RUN_END,
      firstChunkAtMs: RUN_START + 2100,
    })
    expect(stats?.firstTokenMs).toBe(2100)
  })

  it('首 token 时刻缺失、非法或早于发起时不产出', () => {
    const timing = { runStartedAtMs: RUN_START, runEndedAtMs: RUN_END }
    expect(computeGenerationStats(buildUsage({ output_tokens: 1000 }), timing)?.firstTokenMs).toBeUndefined()
    expect(computeGenerationStats(buildUsage({ output_tokens: 1000 }), { ...timing, firstChunkAtMs: null })?.firstTokenMs).toBeUndefined()
    expect(computeGenerationStats(buildUsage({ output_tokens: 1000 }), { ...timing, firstChunkAtMs: Number.NaN })?.firstTokenMs).toBeUndefined()
    expect(computeGenerationStats(buildUsage({ output_tokens: 1000 }), { ...timing, firstChunkAtMs: RUN_START - 1 })?.firstTokenMs).toBeUndefined()
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
