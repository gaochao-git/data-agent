import { describe, expect, it } from 'vitest'
import { buildSubagentTokenUsageRows, buildSubagentTokenUsageText, formatTokenCount, resolveSubagentContextUsage } from '../subagentUsage'

describe('subagentUsage', () => {
  it('token 数紧凑展示', () => {
    expect(formatTokenCount(0)).toBe('0')
    expect(formatTokenCount(999)).toBe('999')
    expect(formatTokenCount(1_234)).toBe('1.2k')
    expect(formatTokenCount(1_234_567)).toBe('1.2m')
    expect(formatTokenCount(Number.NaN)).toBe('0')
  })

  it('拼装过程面板用量摘要（输入/输出/缓存命中率）', () => {
    expect(buildSubagentTokenUsageText({
      input_tokens: 3_000,
      output_tokens: 300,
      cache_read_input_tokens: 1_000,
    })).toBe('tokens 输入 3k · 输出 300 · 缓存命中 33.3%')
    expect(buildSubagentTokenUsageText({ output_tokens: 50 })).toBe('tokens 输出 50')
    expect(buildSubagentTokenUsageText(null)).toBe('')
    expect(buildSubagentTokenUsageText({})).toBe('')
  })

  it('用量浮层按主对话口径生成模型与分组明细', () => {
    expect(buildSubagentTokenUsageRows({
      model: 'SenseNova-Loop-9B-MLX-6bit',
      input_tokens: 19_600,
      output_tokens: 450,
      cache_read_input_tokens: 18_600,
      tokens_per_second: 7,
    })).toEqual([
      { label: '输入总token', value: '19.6k' },
      { label: '缓存命中', value: '94.9%' },
      { label: '输出总token', value: '450' },
      { label: 'token速率', value: '7 tok/s' },
    ])
    expect(buildSubagentTokenUsageRows(null)).toEqual([])
  })

  it('上下文快照优先于计量明细，明细只补充 breakdown', () => {
    const usage = resolveSubagentContextUsage(
      { used_tokens: 20_000, threshold_tokens: 64_000, context_window: 128_000, percent: 16 },
      {
        used: 50_000,
        total: 128_000,
        percentage: 39,
        current_input_tokens: 50_000,
        compression_trigger_tokens: 64_000,
        context_window: 128_000,
        breakdown: [{ key: 'system', label: '系统提示词', tokens: 10_000 }],
      },
    )

    expect(usage).toMatchObject({
      used: 20_000,
      total: 128_000,
      percentage: 16,
      current_input_tokens: 20_000,
      compression_trigger_tokens: 64_000,
      context_window: 128_000,
      estimated: true,
    })
    expect(usage?.breakdown).toEqual([{ key: 'system', label: '系统提示词', tokens: 10_000 }])
  })

  it('没有上下文快照时回退到计量明细', () => {
    expect(resolveSubagentContextUsage(null, {
      used: 12_800,
      total: 128_000,
      current_input_tokens: 12_800,
    })).toMatchObject({
      used: 12_800,
      total: 128_000,
      percentage: 10,
      current_input_tokens: 12_800,
    })
    expect(resolveSubagentContextUsage(null, null)).toBeNull()
  })
})
