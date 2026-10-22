import { describe, expect, it } from 'vitest'

import { resolveVisibleModes } from '../viewModes'
import type { ViewModeItem } from '../viewModes'

const ALL_MODES: ViewModeItem[] = [
  { key: 'conversation', label: '数据智能体' },
  { key: 'builder', label: '可视化查询' },
  { key: 'editor', label: 'SQL查询' },
  { key: 'dashboard', label: '数据看板' },
  { key: 'metricConfig', label: '语义模型配置' },
]

const keysOf = (items: ViewModeItem[]) => items.map(item => item.key)

describe('resolveVisibleModes', () => {
  it('语义模型开关关闭时整页签隐藏（含配置未加载的默认态）', () => {
    expect(keysOf(resolveVisibleModes(ALL_MODES, [], false))).toEqual([
      'conversation',
      'builder',
      'editor',
      'dashboard',
    ])
    // 不传第三个参数也按关闭处理（fail-closed）
    expect(keysOf(resolveVisibleModes(ALL_MODES))).not.toContain('metricConfig')
  })

  it('语义模型开关打开时五个页签齐全', () => {
    expect(keysOf(resolveVisibleModes(ALL_MODES, [], true))).toEqual([
      'conversation',
      'builder',
      'editor',
      'dashboard',
      'metricConfig',
    ])
  })

  it('hideTabs 支持模式 key 与展示名两种写法', () => {
    expect(keysOf(resolveVisibleModes(ALL_MODES, ['dashboard'], true))).not.toContain('dashboard')
    expect(keysOf(resolveVisibleModes(ALL_MODES, ['数据看板'], true))).not.toContain('dashboard')
  })

  it('展示名随 modeLabels 联动，未知值忽略', () => {
    const renamed: ViewModeItem[] = ALL_MODES.map(item =>
      item.key === 'dashboard' ? { key: 'dashboard', label: '数据看板2' } : item,
    )
    expect(keysOf(resolveVisibleModes(renamed, ['数据看板2'], true))).not.toContain('dashboard')
    expect(keysOf(resolveVisibleModes(renamed, ['不存在'], true))).toContain('dashboard')
  })

  it('全部被隐藏时回退，避免空页签栏；但开关关闭的页签不因回退被放回来', () => {
    const allKeys = ALL_MODES.map(item => item.key)
    // 开关关闭 + 全隐藏：回退只放出四个常规页签
    expect(keysOf(resolveVisibleModes(ALL_MODES, allKeys, false))).toEqual([
      'conversation',
      'builder',
      'editor',
      'dashboard',
    ])
    // 开关打开 + 全隐藏：回退为全量
    expect(resolveVisibleModes(ALL_MODES, allKeys, true)).toHaveLength(ALL_MODES.length)
  })
})
