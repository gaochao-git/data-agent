import { describe, expect, it } from 'vitest'
import { buildMemberWarningLines } from '../memberWarnings'

describe('buildMemberWarningLines', () => {
  it('只汇总 failed/cancelled 成员并带上成员名', () => {
    expect(buildMemberWarningLines([
      { status: 'succeeded' } as never,
      { status: 'failed', member_name: '库A', error: '表不存在' } as never,
      { status: 'cancelled', member_datasource_id: 'ds-2' } as never,
    ])).toEqual(['库A：表不存在', 'ds-2：已取消'])
  })

  it('无结果或全部成功时为空', () => {
    expect(buildMemberWarningLines(undefined)).toEqual([])
    expect(buildMemberWarningLines([{ status: 'succeeded' } as never])).toEqual([])
  })
})
