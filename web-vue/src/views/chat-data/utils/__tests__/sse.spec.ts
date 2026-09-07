import { describe, expect, it } from 'vitest'

import { parseSseBlock, parseSseChunk } from '../sse'

describe('parseSseBlock', () => {
  it('parses a data-only block', () => {
    expect(parseSseBlock('data: {"a":1}')).toEqual({ data: '{"a":1}' })
  })

  it('joins multi-line data with newline per SSE spec', () => {
    expect(parseSseBlock('data: line1\ndata:line2\ndata: line3')).toEqual({
      data: 'line1\nline2\nline3',
    })
  })

  it('takes the first event line and the first non-empty id', () => {
    expect(parseSseBlock('event: a\nevent: b\nid:\nid: x\nid: y\ndata: 1')).toEqual({
      event: 'a',
      id: 'x',
      data: '1',
    })
  })

  it('keeps an event-only block with empty data', () => {
    expect(parseSseBlock('event: done')).toEqual({ event: 'done', data: '' })
  })

  it('returns null when the block has neither data nor event', () => {
    expect(parseSseBlock('')).toBeNull()
    expect(parseSseBlock(': comment-only')).toBeNull()
    expect(parseSseBlock('id: 1')).toBeNull()
  })
})

describe('parseSseChunk', () => {
  it('splits complete blocks and keeps the trailing half packet', () => {
    const { events, rest } = parseSseChunk('data: 1\n\ndata: 2\n\ndata: 3')
    expect(events).toEqual([{ data: '1' }, { data: '2' }])
    expect(rest).toBe('data: 3')
  })

  it('resumes a half packet split across network chunks', () => {
    const first = parseSseChunk('event: message')
    expect(first.events).toHaveLength(0)
    expect(first.rest).toBe('event: message')

    const second = parseSseChunk(`${first.rest}\ndata: {"a":1}\n\n`)
    expect(second.events).toEqual([{ event: 'message', data: '{"a":1}' }])
    expect(second.rest).toBe('')
  })

  it('skips blank blocks between separators', () => {
    const { events, rest } = parseSseChunk('\n\ndata: 1\n\n')
    expect(events).toEqual([{ data: '1' }])
    expect(rest).toBe('')
  })
})
