import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  SIDEBAR_TAB_STORAGE_KEY,
  defaultSidebarTab,
  readSidebarTabMemory,
  rememberSidebarTab,
  resolveSidebarTab,
} from '../sidebarTab'

const CONVERSATION_TABS = ['conversation', 'metrics']

describe('readSidebarTabMemory', () => {
  beforeEach(() => localStorage.clear())

  it('无记忆时返回空对象', () => {
    expect(readSidebarTabMemory()).toEqual({})
  })

  it('读取已写入的模式记忆', () => {
    localStorage.setItem(SIDEBAR_TAB_STORAGE_KEY, JSON.stringify({ conversation: 'metrics' }))
    expect(readSidebarTabMemory()).toEqual({ conversation: 'metrics' })
  })

  it('内容非 JSON 时降级为空对象', () => {
    localStorage.setItem(SIDEBAR_TAB_STORAGE_KEY, 'not-json{{')
    expect(readSidebarTabMemory()).toEqual({})
  })

  it('内容是数组/字符串等非对象时降级为空对象', () => {
    localStorage.setItem(SIDEBAR_TAB_STORAGE_KEY, JSON.stringify(['metrics']))
    expect(readSidebarTabMemory()).toEqual({})
    localStorage.setItem(SIDEBAR_TAB_STORAGE_KEY, JSON.stringify('metrics'))
    expect(readSidebarTabMemory()).toEqual({})
  })

  it('值为非字符串（对象/数字）的条目被剔除，字符串条目保留', () => {
    localStorage.setItem(
      SIDEBAR_TAB_STORAGE_KEY,
      JSON.stringify({ conversation: 'metrics', bad: { x: 1 }, n: 3 }),
    )
    expect(readSidebarTabMemory()).toEqual({ conversation: 'metrics' })
  })
})

describe('rememberSidebarTab', () => {
  beforeEach(() => localStorage.clear())

  it('按模式记忆，互不覆盖', () => {
    rememberSidebarTab('conversation', 'metrics')
    rememberSidebarTab('builder', 'tables')
    expect(readSidebarTabMemory()).toEqual({ conversation: 'metrics', builder: 'tables' })
  })

  it('同一模式的再次选择覆盖旧值', () => {
    rememberSidebarTab('conversation', 'metrics')
    rememberSidebarTab('conversation', 'conversation')
    expect(readSidebarTabMemory()).toEqual({ conversation: 'conversation' })
  })

  it('已有记忆损坏时不抛错，重建为新记忆', () => {
    localStorage.setItem(SIDEBAR_TAB_STORAGE_KEY, 'broken')
    rememberSidebarTab('conversation', 'metrics')
    expect(readSidebarTabMemory()).toEqual({ conversation: 'metrics' })
  })
})

describe('defaultSidebarTab', () => {
  it('对话模式默认展示对话历史，其余模式落到唯一 tab', () => {
    expect(defaultSidebarTab('conversation')).toBe('conversation')
    expect(defaultSidebarTab('dashboard')).toBe('dashboard')
    expect(defaultSidebarTab('metricConfig')).toBe('metrics')
    expect(defaultSidebarTab('builder')).toBe('tables')
    expect(defaultSidebarTab('editor')).toBe('tables')
  })
})

describe('resolveSidebarTab', () => {
  beforeEach(() => localStorage.clear())

  it('无记忆时回落该模式默认 tab', () => {
    expect(resolveSidebarTab('conversation', CONVERSATION_TABS)).toBe('conversation')
  })

  it('优先采用记忆值', () => {
    rememberSidebarTab('conversation', 'metrics')
    expect(resolveSidebarTab('conversation', CONVERSATION_TABS)).toBe('metrics')
  })

  it('各模式记忆独立：其他模式的选择不影响本模式', () => {
    rememberSidebarTab('conversation', 'metrics')
    // builder 无记忆 → 仍走默认，而非复用 conversation 的 metrics
    expect(resolveSidebarTab('builder', ['tables'])).toBe('tables')
  })

  it('记忆值已失效（tab key 改名/删除）时回落默认 tab', () => {
    rememberSidebarTab('conversation', 'legacy-tab')
    expect(resolveSidebarTab('conversation', CONVERSATION_TABS)).toBe('conversation')
  })

  it('默认 tab 也不可用时取首个可用 tab', () => {
    expect(resolveSidebarTab('conversation', ['metrics'])).toBe('metrics')
  })

  it('可用 tab 为空时回落默认值，不返回 undefined', () => {
    expect(resolveSidebarTab('conversation', [])).toBe('conversation')
    expect(resolveSidebarTab('builder', [])).toBe('tables')
  })
})

describe('localStorage 不可用', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('getItem 抛错时读取降级为空记忆，不阻断页面', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    expect(readSidebarTabMemory()).toEqual({})
  })

  it('setItem 抛错时写入静默失败，不抛出异常', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota exceeded')
    })
    expect(() => rememberSidebarTab('conversation', 'metrics')).not.toThrow()
  })
})
