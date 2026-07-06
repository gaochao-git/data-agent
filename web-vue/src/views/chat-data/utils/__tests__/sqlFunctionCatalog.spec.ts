import { describe, expect, it } from 'vitest'

import {
  buildSqlFunctionExpression,
  getSqlFunctionCatalog,
  getSqlFunctionOptions,
} from '../sqlFunctionCatalog'

describe('sqlFunctionCatalog', () => {
  it('keeps mysql and hive date functions dialect aware', () => {
    expect(buildSqlFunctionExpression('DATE_ADD_DAY', '`dt`', 'mysql')).toBe('DATE_ADD(`dt`, INTERVAL 1 DAY)')
    expect(buildSqlFunctionExpression('DATE_ADD_DAY', '`dt`', 'hive')).toBe('DATE_ADD(`dt`, 1)')
    expect(buildSqlFunctionExpression('CURRENT_TIMESTAMP', '`dt`', 'mysql')).toBe('NOW()')
    expect(buildSqlFunctionExpression('CURRENT_TIMESTAMP', '`dt`', 'hive')).toBe('CURRENT_TIMESTAMP()')
  })

  it('exposes shared string math and null handling functions', () => {
    const keys = getSqlFunctionCatalog('mysql').map(item => item.key)
    expect(keys).toContain('LOCATE')
    expect(keys).toContain('LPAD')
    expect(keys).toContain('ROUND')
    expect(keys).toContain('IFNULL')
  })

  it('keeps mysql-only string helpers out of hive options', () => {
    const mysqlKeys = getSqlFunctionCatalog('mysql').map(item => item.key)
    const hiveKeys = getSqlFunctionCatalog('hive').map(item => item.key)

    expect(mysqlKeys).toContain('LEFT')
    expect(mysqlKeys).toContain('RIGHT')
    expect(hiveKeys).not.toContain('LEFT')
    expect(hiveKeys).not.toContain('RIGHT')
  })

  it('builds modulo expressions with dialect specific syntax', () => {
    expect(buildSqlFunctionExpression('MOD', '`amount`', 'mysql')).toBe('MOD(`amount`, 10)')
    expect(buildSqlFunctionExpression('MOD', '`amount`', 'hive')).toBe('(`amount` % 10)')
  })

  it('can include raw column option for semantic expression editor', () => {
    expect(getSqlFunctionOptions('hive', true)[0]).toMatchObject({
      value: 'RAW',
      group: 'common',
    })
  })

  it('marks functions that do not wrap the selected expression', () => {
    expect(getSqlFunctionOptions('mysql', true).find(item => item.value === 'CURRENT_DATE')).toMatchObject({
      usesExpression: false,
    })
  })
})
