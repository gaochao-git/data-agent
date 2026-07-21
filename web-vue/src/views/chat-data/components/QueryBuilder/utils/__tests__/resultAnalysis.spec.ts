import { describe, expect, it } from 'vitest'

import { getPreviousCompareKey, normalizeCompareKey } from '../compareKeys'
import { applyResultSortAnalysis, applyResultTotalAnalysis, toNumericValue } from '../resultAnalysis'

describe('compareKeys', () => {
  it('normalizes month and quarter keys', () => {
    expect(normalizeCompareKey('2024-03', 'month')).toBe('2024-03')
    expect(normalizeCompareKey('2024Q2', 'quarter')).toBe('2024-Q2')
  })

  it('computes previous period keys', () => {
    expect(getPreviousCompareKey('2024-03', 'month', 'mom')).toBe('2024-02')
    expect(getPreviousCompareKey('2024-03', 'month', 'yoy')).toBe('2023-03')
    expect(getPreviousCompareKey('2024-Q1', 'quarter', 'mom')).toBe('2023-Q4')
  })
})

describe('resultAnalysis', () => {
  it('parses numeric strings for analysis', () => {
    expect(toNumericValue('12.5')).toBe(12.5)
    expect(toNumericValue('')).toBeNull()
  })

  it('sorts and totals result payloads', () => {
    const payload = {
      columns: ['name', 'amount'],
      data: [
        { name: 'b', amount: 2 },
        { name: 'a', amount: 5 },
      ],
    }

    const sorted = applyResultSortAnalysis(payload, 'amount', 'DESC')
    expect(sorted.warning).toBeNull()
    expect(sorted.payload.data?.[0]?.amount).toBe(5)

    const totaled = applyResultTotalAnalysis(payload)
    expect(totaled.warning).toBeNull()
    const lastRow = totaled.payload.data?.[totaled.payload.data.length - 1]
    expect(lastRow).toEqual({ name: '合计', amount: 7 })
  })
})
