import { describe, expect, it } from 'vitest'

import { getSafeNumericColumns, isNumericValue, isSafeNumericValue, toSafeNumber } from '../numeric'

describe('numeric', () => {
  it('accepts decimal strings that can safely be used for chart calculations', () => {
    expect(toSafeNumber('12.50')).toBe(12.5)
    expect(isSafeNumericValue('10130.089787')).toBe(true)
    expect(toSafeNumber('0.0000001')).toBe(0.0000001)
  })

  it('rejects integers outside the JavaScript safe-number range', () => {
    expect(toSafeNumber('9007199254740992')).toBeNull()
    expect(toSafeNumber('12345678901234567890.01')).toBeNull()
    expect(isSafeNumericValue(9007199254740992)).toBe(false)
    expect(isNumericValue('9007199254740992')).toBe(true)
    expect(isNumericValue('not-a-number')).toBe(false)
  })

  it('rejects decimal strings that lose precision during JavaScript conversion', () => {
    expect(toSafeNumber('9007199254740991.1')).toBeNull()
    expect(toSafeNumber('0.1234567890123456789012345')).toBeNull()
    expect(toSafeNumber('12.50')).toBe(12.5)
  })

  it('requires every populated row in a chart column to be safe', () => {
    const rows = [
      { label: 'A', amount: '12.5' },
      { label: 'B', amount: '9007199254740992' },
      { label: 'C', amount: null },
    ]

    expect(getSafeNumericColumns(rows, ['label', 'amount'])).toEqual([])
  })
})
