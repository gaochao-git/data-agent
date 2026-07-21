import { describe, expect, it } from 'vitest'
import { buildBuilderConfigPayload } from '../builderConfig'
import { buildSemanticSelectedTables } from '../semanticSelectedTables'

describe('QueryBuilder configuration utilities', () => {
  it('keeps a compound filter and separate HAVING filter in the generated payload', () => {
    const payload = buildBuilderConfigPayload({
      semanticConfigId: 'semantic-1',
      semanticConfigName: '订单指标集',
      selectedTables: [{ name: 'orders', alias: 'o', label: '订单' }],
      dimensions: [{ id: 'd1', table: 'orders', tableAlias: 'o', column: 'city', label: '城市', transform: 'raw' }],
      aggregations: [{ id: 'a1', table: 'orders', tableAlias: 'o', column: 'amount', label: '金额', func: 'SUM' }],
      filters: [
        { id: 'f1', table: 'orders', tableAlias: 'o', column: 'city', operator: '=', value: '上海', logic: 'AND' },
        { id: 'f2', table: 'orders', tableAlias: 'o', column: 'amount', operator: '>', value: '100', logic: 'AND', aggFunc: 'SUM', aggId: 'a1' },
      ],
      advancedFilterTree: {
        kind: 'group',
        id: 'g1',
        logic: 'AND',
        children: [{
          kind: 'leaf',
          id: 'l1',
          filter: { id: 'f1', table: 'orders', tableAlias: 'o', column: 'city', operator: '=', value: '上海', logic: 'AND' },
        }],
      },
      orderBy: [],
      pagination: { enabled: true, limit: '20', offset: '0' },
    })

    expect(payload.filters).toEqual([
      { and: [expect.objectContaining({ id: 'f1', tableAlias: 'o' })] },
      expect.objectContaining({ id: 'f2', logic: 'AND', aggFunc: 'SUM' }),
    ])
    expect(payload).toMatchObject({ semantic_config_id: 'semantic-1', limit: 20, offset: 0 })
  })

  it('builds the minimal semantic relationship chain for selected aliases', () => {
    const tables = [
      { table_name: 'orders', description: '订单' },
      { table_name: 'users', description: '用户' },
      { table_name: 'regions', description: '区域' },
    ]
    const semanticConfig = {
      id: 'semantic-1',
      name: '订单指标集',
      enabled: true,
      dimensions: [
        { name: '订单城市', table: 'orders', table_alias: 'o', column: 'city' },
        { name: '区域', table: 'regions', table_alias: 'r', column: 'name' },
      ],
      metrics: [],
      relationships: [
        { left_table: 'orders', left_alias: 'o', right_table: 'users', right_alias: 'u', left_column: 'user_id', right_column: 'id', relationship_type: 'many_to_one' as const, join_type: 'left' as const },
        { left_table: 'users', left_alias: 'u', right_table: 'regions', right_alias: 'r', left_column: 'region_id', right_column: 'id', relationship_type: 'many_to_one' as const, join_type: 'left' as const },
      ],
    }

    expect(buildSemanticSelectedTables(semanticConfig, tables, new Set(['o', 'r']))).toEqual([
      { name: 'orders', alias: 'o', label: '订单（orders）' },
      expect.objectContaining({ name: 'users', alias: 'u', joinOn: expect.objectContaining({ leftAlias: 'o' }) }),
      expect.objectContaining({ name: 'regions', alias: 'r', joinOn: expect.objectContaining({ leftAlias: 'u' }) }),
    ])
  })
})
