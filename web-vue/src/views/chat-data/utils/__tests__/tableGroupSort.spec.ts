import { describe, expect, it } from 'vitest'

import { sortTableGroupTables } from '../tableGroupSort'

const TABLES = [
  { table_name: 'orders' },
  { table_name: 'categories' },
  { table_name: 'users' },
  { table_name: 'course_enrollments' },
]

const names = (items: Array<{ table_name: string }>) => items.map(item => item.table_name)

describe('sortTableGroupTables', () => {
  it('选中优先：勾选的排在最前，组内按表名', () => {
    const selected = new Set(['users', 'categories'])
    expect(names(sortTableGroupTables(TABLES, selected, 'selected'))).toEqual([
      'categories',
      'users',
      'course_enrollments',
      'orders',
    ])
  })

  it('表名排序：忽略勾选状态，纯按表名', () => {
    const selected = new Set(['users'])
    expect(names(sortTableGroupTables(TABLES, selected, 'name'))).toEqual([
      'categories',
      'course_enrollments',
      'orders',
      'users',
    ])
  })

  it('无勾选时两种口径顺序一致（按表名）', () => {
    const empty = new Set<string>()
    expect(names(sortTableGroupTables(TABLES, empty, 'selected'))).toEqual(
      names(sortTableGroupTables(TABLES, empty, 'name')),
    )
  })

  it('不修改入参（返回新数组）', () => {
    const source = [...TABLES]
    const result = sortTableGroupTables(source, new Set(['orders']), 'selected')
    expect(names(source)).toEqual(names(TABLES))
    expect(result).not.toBe(source)
  })

  it('勾选集合里存在列表外的表名时不影响排序', () => {
    const selected = new Set(['not_exists', 'orders'])
    expect(names(sortTableGroupTables(TABLES, selected, 'selected'))).toEqual([
      'orders',
      'categories',
      'course_enrollments',
      'users',
    ])
  })
})
