import { describe, expect, it } from 'vitest'

import {
  buildExecutionTargets,
  isSameStringArray,
  normalizeSelectedMemberDatasourceIds,
} from '../executionTargets'

describe('buildExecutionTargets', () => {
  it('把 member_sources 映射为执行对象选项，过滤缺少 member_datasource_id 的成员', () => {
    const targets = buildExecutionTargets({
      datasource: {
        id: 'ds-1',
        group_name: '华东数据源',
        source_type: 'mysql',
        member_sources: [
          { member_datasource_id: 'm-1', org_name: '机构A', org_code: ' ORGA ', host: '10.0.0.1', port: 3306, database: 'db_a' },
          { org_name: '无ID成员', host: '10.0.0.2', port: 3306, database: 'db_b' },
          { member_datasource_id: 'm-2', host: '10.0.0.3', port: 3307, database: 'db_c' },
        ],
      },
      fallbackId: 'ds-1',
    })
    expect(targets).toEqual([
      {
        member_datasource_id: 'm-1',
        org_name: '机构A',
        org_code: 'ORGA',
        source_type: 'mysql',
        host: '10.0.0.1',
        port: 3306,
        database: 'db_a',
        label: '机构A',
      },
      {
        member_datasource_id: 'm-2',
        org_name: '',
        org_code: '',
        source_type: 'mysql',
        host: '10.0.0.3',
        port: 3307,
        database: 'db_c',
        label: '10.0.0.3:3307/db_c',
      },
    ])
  })

  it('成员未声明 source_type 时回退数据源 source_type', () => {
    const targets = buildExecutionTargets({
      datasource: {
        id: 'ds-1',
        source_type: 'hive',
        member_sources: [{ member_datasource_id: 'm-1', source_type: 'mysql', database: 'db' }],
      },
      fallbackId: 'ds-1',
    })
    expect(targets[0]?.source_type).toBe('mysql')
  })

  it('无 member_sources 时优先使用 template_datasource_id，label 回退 group_name || fallbackId', () => {
    const targets = buildExecutionTargets({
      datasource: {
        id: 'ds-1',
        group_name: '组A',
        source_type: 'mysql',
        template_datasource_id: 'tpl-1',
        source_config: { host: '10.0.0.9', port: 3306, database: 'db_x', org_code: ' ORGZ ' },
      },
      fallbackId: 'ds-1',
    })
    expect(targets).toEqual([{
      member_datasource_id: 'tpl-1',
      org_code: 'ORGZ',
      source_type: 'mysql',
      host: '10.0.0.9',
      port: 3306,
      database: 'db_x',
      label: '组A',
    }])
  })

  it('无 template 时回退 fallbackId，group_name 为空时 label 用 fallbackId', () => {
    const targets = buildExecutionTargets({
      datasource: { id: 'ds-1', group_name: '' },
      fallbackId: 'ds-1',
    })
    expect(targets).toEqual([{
      member_datasource_id: 'ds-1',
      org_code: '',
      source_type: '',
      host: undefined,
      port: undefined,
      database: undefined,
      label: 'ds-1',
    }])
  })

  it('无数据源且无 fallbackId 时返回空数组', () => {
    expect(buildExecutionTargets({ datasource: null, fallbackId: '' })).toEqual([])
    expect(buildExecutionTargets({})).toEqual([])
  })

  it('无数据源但有 fallbackId 时输出单条兜底选项（看板面板口径）', () => {
    const targets = buildExecutionTargets({ datasource: null, fallbackId: 'panel-member-1' })
    expect(targets).toEqual([{
      member_datasource_id: 'panel-member-1',
      org_code: '',
      source_type: '',
      host: undefined,
      port: undefined,
      database: undefined,
      label: 'panel-member-1',
    }])
  })
})

describe('normalizeSelectedMemberDatasourceIds', () => {
  it('多选时原样返回', () => {
    expect(normalizeSelectedMemberDatasourceIds(['a', 'b'], true)).toEqual(['a', 'b'])
  })

  it('单选时只保留第一个', () => {
    expect(normalizeSelectedMemberDatasourceIds(['a', 'b'], false)).toEqual(['a'])
  })
})

describe('isSameStringArray', () => {
  it('长度不同返回 false', () => {
    expect(isSameStringArray(['a'], ['a', 'b'])).toBe(false)
  })

  it('按顺序逐项比较', () => {
    expect(isSameStringArray(['a', 'b'], ['a', 'b'])).toBe(true)
    expect(isSameStringArray(['a', 'b'], ['b', 'a'])).toBe(false)
  })

  it('空数组视为相等', () => {
    expect(isSameStringArray([], [])).toBe(true)
  })
})
