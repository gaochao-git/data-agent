import { describe, expect, it } from 'vitest'
import { getQueryResultError, getRenderableQueryResultData } from '../semanticTool'

describe('semanticTool query result delivery', () => {
  it('exposes structured query delivery failures without rendering a result panel', () => {
    const result = JSON.stringify({
      type: 'query_result',
      version: 1,
      status: 'failed',
      data: null,
      error: '查询已执行，但结果保存失败，当前无法展示或继续分析',
      meta: { result_persisted: false },
    })

    expect(getQueryResultError(result)).toBe('查询已执行，但结果保存失败，当前无法展示或继续分析')
    expect(getRenderableQueryResultData(result)).toBe('')
  })

  it('keeps persisted query results renderable', () => {
    const result = JSON.stringify({
      type: 'query_result',
      status: 'succeeded',
      data: null,
      error: null,
      meta: { result_id: 'result-1' },
    })

    expect(getQueryResultError(result)).toBe('')
    expect(getRenderableQueryResultData(result)).toBe(result)
  })
})
