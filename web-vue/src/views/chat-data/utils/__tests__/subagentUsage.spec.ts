import { describe, expect, it } from 'vitest'
import { buildSubagentTokenUsageText, formatTokenCount } from '../subagentUsage'

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
    })).toBe('tokens 输入 3.0k · 输出 300 · 缓存命中 33%')
    expect(buildSubagentTokenUsageText({ output_tokens: 50 })).toBe('tokens 输出 50')
    expect(buildSubagentTokenUsageText(null)).toBe('')
    expect(buildSubagentTokenUsageText({})).toBe('')
  })
})
