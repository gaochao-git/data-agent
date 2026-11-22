import { describe, expect, it } from 'vitest'
import { extractPartialJsonString } from '../partialJson'

describe('extractPartialJsonString', () => {
  it('从完整嵌套参数里取出 description', () => {
    const raw = '{"dispatch":{"description":"盘点指标","subagent_type":"general"}}'
    expect(extractPartialJsonString(raw, 'description')).toBe('盘点指标')
  })

  it('参数未闭合时返回已经生成的部分，并解码换行与引号', () => {
    const raw = '{"description":"## 任务\\n先看工作区\\n他说：\\"开始\\"'
    expect(extractPartialJsonString(raw, 'description')).toBe('## 任务\n先看工作区\n他说："开始"')
  })

  it('字段还没出现或不是字符串时返回空串', () => {
    expect(extractPartialJsonString('{"dispatch":{', 'description')).toBe('')
    expect(extractPartialJsonString('{"description":123}', 'description')).toBe('')
  })
})
